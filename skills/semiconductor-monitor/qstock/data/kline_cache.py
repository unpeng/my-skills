# -*- coding: utf-8 -*-
"""
日K线本地增量缓存层（方案1）。

历史日K线中除"最新交易日"外的部分是稳定不变的，每次盯盘都从
2020 年至今全量拉取（1000+ 条）是主要的重复网络开销。本模块提供
`get_kline_cached`，在本地缓存历史K线，之后每次只增量拉取"缓存最后
日期之后"的新数据，大幅减少网络请求量。

缓存位置：<本技能根目录>/.local/kline_<code>_<fqt>.json
（纯本地文件，不联网、不提交版本库，用户可随时删除，删除后自动重建）

健壮性设计：
- 复权漂移检测：前复权数据在除权日会整体调整，导致历史K线数值变化。
  增量拉取时会回溯若干条与缓存重叠，比对历史重叠日的收盘价，若偏差
  超过阈值则判定发生复权调整，自动全量刷新。
- 过期兜底：缓存最后日期距今超过 MAX_STALE_DAYS 自然日时强制全量刷新，
  避免长期增量累积未被发现的漂移。
- 拉取失败兜底：网络增量拉取失败时退回使用旧缓存，保证有数据可用而
  非直接失败。
- 当日数据更新：缓存最后一条（当日/最新交易日）盘中会变化，增量时会
  用新拉取的数据覆盖同日期行。
"""

import json
import os
from datetime import datetime
import pandas as pd

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.fetcher import get_kline as _default_fetch_kline

# 缓存目录与 position_store 一致，位于技能根目录下的 .local/
# 本文件路径 <技能根目录>/qstock/data/kline_cache.py，向上三级得技能根目录
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CACHE_DIR = os.path.join(_SKILL_ROOT, ".local")

# 历史重叠日收盘价相对偏差超过该阈值(1%)判定为复权漂移，触发全量刷新
DRIFT_REL_THRESHOLD = 0.01
# 缓存最后日期距今超过该自然日数则强制全量刷新（防止长期增量累积漂移）
MAX_STALE_DAYS = 20
# 增量拉取时向前回溯的条数，用于提供历史重叠日做漂移校验、并覆盖当日数据
OVERLAP_BARS = 5

# 需要转成数值类型的列（其余如 date/name/code 保持字符串）
_NUMERIC_COLS = ["open", "high", "low", "close", "volume", "turnover",
                 "turnover_rate", "amplitude", "pct_change", "change"]


def _fmt_date(s: str) -> str:
    """把 'YYYYMMDD' 或 'YYYY-MM-DD' 统一成 'YYYY-MM-DD'，便于字符串比较。"""
    s = str(s).strip().replace("-", "")
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _cache_path(code: str, fqt: int) -> str:
    return os.path.join(_CACHE_DIR, f"kline_{code}_{fqt}.json")


def _ensure_dir():
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _rebuild_df(records: list) -> pd.DataFrame:
    """把缓存中的 records 还原成与 get_kline 一致结构的 DataFrame。"""
    if not records:
        return pd.DataFrame()
    df = pd.DataFrame(records)
    if "date" not in df.columns or df.empty:
        return pd.DataFrame()
    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    df.index = pd.to_datetime(df["date"])
    return df


def _load_cache(path: str) -> dict:
    """读取缓存文件，返回 {'df':DataFrame,'last_full_refresh':str} 或 None。"""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    df = _rebuild_df(data.get("records", []))
    if df.empty:
        return None
    return {
        "df": df,
        "last_full_refresh": data.get("last_full_refresh"),
        # 上次全量拉取时请求的起始日期（'YYYY-MM-DD'）。用于判断是否需要
        # 再次全量：只有本次请求 start 比它更早才需要，避免因数据源实际
        # 最早日期晚于请求 start（如新上市ETF）导致缓存永久失效
        "fetch_start": data.get("fetch_start"),
    }


def _save_cache(path: str, df: pd.DataFrame, last_full_refresh: str,
                fetch_start: str) -> None:
    """把 DataFrame 序列化保存到缓存文件（NaN 转 null，兼容 numpy 类型）。"""
    _ensure_dir()
    # 用 to_json 处理 NaN->null 与 numpy 类型序列化，再解析回 records
    records = json.loads(df.to_json(orient="records", force_ascii=False))
    payload = {
        "code": str(df["code"].iloc[0]) if "code" in df.columns and not df.empty else "",
        "cached_at": datetime.now().isoformat(timespec="seconds"),
        "last_full_refresh": last_full_refresh,
        "fetch_start": fetch_start,
        "records": records,
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)  # 原子替换，避免写一半损坏缓存


def _filter_range(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """按日期区间过滤（闭区间），start/end 为 'YYYY-MM-DD'。"""
    if df.empty:
        return df
    mask = (df["date"] >= start) & (df["date"] <= end)
    return df[mask].copy()


def _detect_drift(df_cache: pd.DataFrame, df_new: pd.DataFrame) -> bool:
    """
    比对缓存与新拉取数据在历史重叠日的收盘价，判断是否发生复权漂移。

    排除 df_new 的最新一条（当日/最新交易日盘中会变，属正常波动）。
    若存在历史重叠日且收盘价相对偏差超过阈值，返回 True。
    """
    if df_cache.empty or df_new.empty:
        return False
    latest_new = df_new["date"].max()
    cache_close = dict(zip(df_cache["date"], df_cache["close"]))
    for _, row in df_new.iterrows():
        d = row["date"]
        if d == latest_new:  # 排除最新一条（盘中可变）
            continue
        old = cache_close.get(d)
        if old is None or old == 0:
            continue
        try:
            if abs(float(row["close"]) - float(old)) / abs(float(old)) > DRIFT_REL_THRESHOLD:
                return True
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return False


def get_kline_cached(code: str, start: str = "19000101", end: str = None,
                     fqt: int = 1, fetch_func=None) -> pd.DataFrame:
    """
    带本地增量缓存的日K线获取，接口与 fetcher.get_kline 兼容。

    Args:
        code: 证券代码
        start: 起始日期 'YYYYMMDD' 或 'YYYY-MM-DD'
        end: 结束日期，默认今天
        fqt: 复权类型（1前复权/2后复权/0不复权），不同 fqt 分别缓存
        fetch_func: 底层拉取函数（默认 fetcher.get_kline），仅测试时注入

    Returns:
        与 get_kline 相同结构的 DataFrame（按 start/end 过滤后的区间）。
    """
    fetch = fetch_func or _default_fetch_kline
    today = datetime.now().strftime("%Y-%m-%d")
    start_fmt = _fmt_date(start)
    end_fmt = _fmt_date(end) if end else today

    path = _cache_path(code, fqt)
    cache = _load_cache(path)

    # ---- 判断是否需要全量拉取 ----
    need_full = False
    if cache is None:
        need_full = True
    else:
        df_cache = cache["df"]
        cache_last = df_cache["date"].max()
        # 与"上次全量请求的 start"比较，而非缓存实际最早日期：数据源能
        # 提供的最早日期可能晚于请求 start（如新上市ETF），此时缓存最早
        # 日期恒晚于请求 start，不应据此反复全量刷新
        prev_fetch_start = cache.get("fetch_start") or df_cache["date"].min()
        if start_fmt < prev_fetch_start:
            need_full = True  # 本次请求比上次全量更早，可能取到更早数据
        else:
            try:
                stale_days = (datetime.strptime(today, "%Y-%m-%d")
                              - datetime.strptime(cache_last, "%Y-%m-%d")).days
                if stale_days > MAX_STALE_DAYS:
                    need_full = True
            except ValueError:
                need_full = True

    # ---- 全量拉取分支 ----
    if need_full:
        df = fetch(code, start=start, end=end_fmt.replace("-", ""), fqt=fqt)
        if df is None or df.empty:
            # 拉取失败：有旧缓存则退回旧缓存，否则返回空
            if cache is not None:
                return _filter_range(cache["df"], start_fmt, end_fmt)
            return pd.DataFrame() if df is None else df
        _save_cache(path, df, last_full_refresh=today, fetch_start=start_fmt)
        return _filter_range(_rebuild_df(json.loads(df.to_json(orient="records"))),
                             start_fmt, end_fmt)

    # ---- 增量拉取分支 ----
    df_cache = cache["df"]
    n = len(df_cache)
    overlap_idx = max(0, n - OVERLAP_BARS)
    overlap_start = df_cache["date"].iloc[overlap_idx]
    df_new = fetch(code, start=overlap_start.replace("-", ""),
                   end=end_fmt.replace("-", ""), fqt=fqt)

    if df_new is None or df_new.empty:
        # 增量拉取失败，退回旧缓存
        return _filter_range(df_cache, start_fmt, end_fmt)

    # 复权漂移检测：若历史重叠日收盘价变化，说明发生除权调整，全量刷新
    if _detect_drift(df_cache, df_new):
        df_full = fetch(code, start=start, end=end_fmt.replace("-", ""), fqt=fqt)
        if df_full is not None and not df_full.empty:
            _save_cache(path, df_full, last_full_refresh=today,
                        fetch_start=cache.get("fetch_start") or start_fmt)
            return _filter_range(
                _rebuild_df(json.loads(df_full.to_json(orient="records"))),
                start_fmt, end_fmt)
        # 全量刷新失败，退回旧缓存
        return _filter_range(df_cache, start_fmt, end_fmt)

    # 合并：缓存中早于重叠起点的部分 + 新数据（新数据覆盖重叠区间和当日）
    old_part = df_cache[df_cache["date"] < overlap_start]
    merged = pd.concat([old_part, df_new], ignore_index=True)
    merged = merged.drop_duplicates(subset="date", keep="last")
    merged = merged.sort_values("date").reset_index(drop=True)
    merged.index = pd.to_datetime(merged["date"])

    _save_cache(path, merged, last_full_refresh=cache.get("last_full_refresh") or today,
                fetch_start=cache.get("fetch_start") or start_fmt)
    return _filter_range(merged, start_fmt, end_fmt)
