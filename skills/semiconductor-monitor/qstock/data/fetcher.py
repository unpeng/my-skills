# -*- coding: utf-8 -*-
"""
Data acquisition module - fetches stock data from East Money (东方财富) APIs.
Adapted from tkfy920/qstock data/trade.py.
"""

import time
import requests
import pandas as pd
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    KLINE_URL, REALTIME_URL, SINGLE_REALTIME_URL, SINGLE_REALTIME_URL_FALLBACK,
    STOCK_INFO_URL, REQUEST_HEADER, MARKET_FILTER, MARKET_NUM_DICT,
    TRADE_DETAIL_DICT, KLINE_FIELD, STOCK_INFO_DICT, FREQ_MAP,
)

# Reusable session for connection pooling
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})


def _request_with_retry(url: str, params: dict, max_retries: int = 5,
                        timeout: int = 20,
                        backoff_base: float = 5.0) -> requests.Response:
    """Make HTTP request with retry logic for rate limiting.

    Args:
        backoff_base: 重试等待时间的基数（秒），实际等待为
            (attempt+1)*backoff_base + 随机抖动。调用方可根据场景调低，
            例如实时行情轮询场景应尽快失败并回退，而不是像批量拉取
            全市场行情那样容忍较长等待。
    """
    import random
    for attempt in range(max_retries):
        try:
            # Add small random delay to avoid synchronized requests
            time.sleep(random.uniform(0.5, 1.5))
            resp = _session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * backoff_base + random.uniform(1, 3)
                time.sleep(wait)
            else:
                raise


def _get_code_id(code: str) -> str:
    """Convert stock code to East Money secid format (market.code)."""
    code = str(code).strip()
    if "." in code:
        return code
    if code.startswith("6") or code.startswith("9") or code.startswith("5"):
        return f"1.{code}"
    elif code.startswith("0") or code.startswith("3") or code.startswith("2"):
        return f"0.{code}"
    elif code.startswith("4") or code.startswith("8"):
        return f"0.{code}"
    elif code.startswith("1"):  # 债券/ETF
        return f"1.{code}"
    else:
        return f"0.{code}"


def _trans_num(df: pd.DataFrame, ignore_cols: list) -> pd.DataFrame:
    """Convert columns to numeric types, ignoring specified columns."""
    for col in df.columns:
        if col not in ignore_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


ETF_PREFIXES = ("51", "56", "58", "15", "16", "50")


def is_etf_code(code: str) -> bool:
    """
    判断代码是否为 ETF（而非个股）。

    ETF 没有 PE/PB/ROE 等财务指标，不能用个股基本面评分体系打分，
    调用方应据此改用 get_etf_quote 等 ETF 专属逻辑。

    Args:
        code: 证券代码

    Returns:
        True 表示是 ETF 代码（按常见前缀判断，非绝对准确，但覆盖
        沪市/深市主流 ETF 代码段，如 588xxx 科创板ETF、510xxx/159xxx 等）。
    """
    code = str(code).strip()
    return code.startswith(ETF_PREFIXES)


def get_current_quote(code: str) -> dict:
    """
    获取当前价格，带兜底回退：实时接口失败时用最新K线收盘价代替。

    Args:
        code: 证券代码

    Returns:
        Dict，至少包含 'price'（当前价）、'source'（数据来源:
        'realtime' 或 'kline_fallback'）。若两种方式都失败，返回
        {'error': ...}。
    """
    # 优先尝试实时接口（含涨跌幅、成交量等更多字段）
    try:
        df = get_realtime(code)
        if not df.empty:
            row = df.iloc[0]
            price = row.get("最新价")
            if price is not None and pd.notna(price) and float(price) > 0:
                return {
                    "price": float(price),
                    "change_pct": float(row.get("涨跌幅", 0) or 0),
                    "open": float(row.get("今开", 0) or 0),
                    "prev_close": float(row.get("昨收", 0) or 0),
                    "high": float(row.get("最高", 0) or 0),
                    "low": float(row.get("最低", 0) or 0),
                    "volume": float(row.get("成交量", 0) or 0),
                    "turnover_rate": float(row.get("换手率", 0) or 0),
                    "source": "realtime",
                }
    except Exception:
        pass

    # 回退：用最近一个交易日的K线收盘价兜底（非实时，但保证有值）
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        df = get_kline(code, start=start, end=end)
        if not df.empty:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else latest
            return {
                "price": float(latest["close"]),
                "change_pct": round(
                    (float(latest["close"]) / float(prev["close"]) - 1) * 100, 2
                ) if float(prev["close"]) else 0.0,
                "open": float(latest["open"]),
                "prev_close": float(prev["close"]),
                "high": float(latest["high"]),
                "low": float(latest["low"]),
                "volume": float(latest["volume"]),
                "turnover_rate": 0.0,
                "source": "kline_fallback",
                "as_of": str(latest.get("date", "")),
            }
    except Exception:
        pass

    return {"error": f"无法获取 {code} 的当前价格（实时接口与K线兜底均失败）"}


def get_stock_list(market: str = "沪深A") -> list:
    """
    Get all stock codes for a given market.

    Args:
        market: Market name (沪深A, 上证A, 深证A, 创业板, 科创板, etc.)

    Returns:
        List of stock codes.
    """
    fs = MARKET_FILTER.get(market, MARKET_FILTER["沪深A"])
    fields = ",".join(TRADE_DETAIL_DICT.keys())
    codes = []
    page = 1

    while True:
        params = {
            "pn": str(page),
            "pz": "5000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": fields,
        }
        try:
            resp = _request_with_retry(REALTIME_URL, params=params)
            data = resp.json()
            diff = data.get("data", {}).get("diff", [])
            if not diff:
                break
            for item in diff:
                code = item.get("f12", "")
                if code:
                    codes.append(code)
            page += 1
            time.sleep(0.3)
        except Exception:
            break

    return codes


def _get_kline_sina(code: str, start: str = "20200101",
                    end: str = None) -> pd.DataFrame:
    """
    Fetch K-line data from Sina Finance API (fallback when East Money is blocked).

    Args:
        code: Stock code
        start: Start date YYYYMMDD
        end: End date YYYYMMDD

    Returns:
        DataFrame with OHLCV data.
    """
    # Determine market prefix
    code = str(code).strip()
    if code.startswith("6") or code.startswith("9") or code.startswith("5"):
        sina_code = f"sh{code}"
    else:
        sina_code = f"sz{code}"

    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {
        "symbol": sina_code,
        "scale": "240",  # daily
        "ma": "no",
        "datalen": "5000",
    }

    try:
        resp = requests.get(url, params=params, headers=REQUEST_HEADER, timeout=15)
        data = resp.json()

        if not data:
            return pd.DataFrame()

        rows = []
        for item in data:
            rows.append({
                "date": item["day"],
                "open": float(item["open"]),
                "close": float(item["close"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "volume": float(item["volume"]),
            })

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        # Filter by date range
        start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        df = df[df["date"] >= start_fmt]

        df["name"] = ""
        df["code"] = code
        df["turnover"] = 0.0
        df["turnover_rate"] = 0.0
        df.index = pd.to_datetime(df["date"])

        return df

    except Exception:
        return pd.DataFrame()


def get_kline(code: str, start: str = "19000101", end: str = None,
              freq: str = "d", fqt: int = 1) -> pd.DataFrame:
    """
    Fetch historical K-line (OHLCV) data for a stock.
    Tries East Money API first, falls back to Tencent API.

    Args:
        code: Stock code (e.g., '000001', '600519')
        start: Start date in 'YYYYMMDD' or 'YYYY-MM-DD' format
        end: End date (defaults to latest trading day)
        freq: Frequency - 'd'(daily), 'w'(weekly), 'm'(monthly)
        fqt: Adjustment type - 1(qfq), 2(hfq), 0(no adjust)

    Returns:
        DataFrame with columns: date, name, code, open, high, low, close,
        volume, turnover, turnover_rate
    """
    if end is None:
        end = datetime.now().strftime("%Y%m%d")
    start = start.replace("-", "")
    end = end.replace("-", "")

    # Try East Money API first
    try:
        klt = FREQ_MAP.get(freq, 101)
        code_id = _get_code_id(code)
        fields2 = ",".join(KLINE_FIELD.keys())

        params = {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": fields2,
            "beg": start,
            "end": end,
            "rtntype": "6",
            "secid": code_id,
            "klt": str(klt),
            "fqt": str(fqt),
        }

        resp = _request_with_retry(KLINE_URL, params=params, max_retries=2)
        json_data = resp.json()

        klines = json_data.get("data", {}).get("klines", [])
        if klines:
            name = json_data["data"].get("name", "")
            stock_code = code_id.split(".")[-1]

            rows = [k.split(",") for k in klines]
            columns = list(KLINE_FIELD.values())
            df = pd.DataFrame(rows, columns=columns)

            # Rename to English
            col_map = {
                "日期": "date", "名称": "name", "代码": "code",
                "开盘": "open", "最高": "high", "最低": "low",
                "收盘": "close", "成交量": "volume", "成交额": "turnover",
                "换手率": "turnover_rate", "振幅": "amplitude",
                "涨跌幅": "pct_change", "涨跌额": "change",
            }
            df.insert(0, "code", stock_code)
            df.insert(0, "name", name)
            df = df.rename(columns=col_map)
            df.index = pd.to_datetime(df["date"])

            ignore = ["name", "code", "date"]
            df = _trans_num(df, ignore)
            return df
    except Exception:
        pass

    # Fallback to Sina Finance API
    return _get_kline_sina(code, start, end)


def get_realtime(code_list) -> pd.DataFrame:
    """
    Fetch real-time quote data for one or more stocks.

    Args:
        code_list: Single code string or list of codes

    Returns:
        DataFrame with real-time market data.
    """
    if isinstance(code_list, str):
        code_list = [code_list]

    secids = [_get_code_id(c) for c in code_list]
    fields = ",".join(TRADE_DETAIL_DICT.keys())

    params = {
        "fields": fields,
        "fltt": "2",
        "secids": ",".join(secids),
    }

    # 该接口在盯盘场景下被高频轮询调用，且已有 get_current_quote 的
    # K线收盘价兜底逻辑，因此用较少重试次数与较短超时/回退等待，
    # 避免行情主机不可达时长时间阻塞轮询——尽快失败、尽快回退更重要。
    #
    # 主域名 push2 在部分网络环境下会对该接口稳定拒绝连接（域名级问题，
    # 而非偶发超时/限流），同域名重试没有意义，因此只试1次就立刻切换到
    # push2delay 域名的同一接口（该域名重试2次），而不是在同一个坏域名
    # 上耗尽重试次数后才回退到精度更低的K线兜底价。
    try:
        resp = _request_with_retry(SINGLE_REALTIME_URL, params=params,
                                   max_retries=1, timeout=5)
    except (requests.ConnectionError, requests.Timeout):
        resp = _request_with_retry(SINGLE_REALTIME_URL_FALLBACK, params=params,
                                   max_retries=2, timeout=5, backoff_base=1.0)
    data = resp.json()
    diff = data.get("data", {}).get("diff", [])

    if not diff:
        return pd.DataFrame(columns=TRADE_DETAIL_DICT.values())

    df = pd.DataFrame(diff)
    # Keep only mapped columns
    available = [k for k in TRADE_DETAIL_DICT.keys() if k in df.columns]
    df = df[available].rename(columns=TRADE_DETAIL_DICT)

    ignore = ["名称", "代码", "所处行业"]
    df = _trans_num(df, ignore)
    return df


def get_financial(code: str) -> dict:
    """
    Fetch basic financial indicators for a single stock.

    Args:
        code: Stock code

    Returns:
        Dict with financial metrics (PE, PB, ROE, etc.)
    """
    code_id = _get_code_id(code)
    fields = ",".join(STOCK_INFO_DICT.keys())

    params = {
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "invt": "2",
        "fltt": "2",
        "fields": fields,
        "secid": code_id,
    }

    resp = _request_with_retry(STOCK_INFO_URL, params=params)
    data = resp.json().get("data", {})

    if not data:
        return {}

    result = {}
    for api_key, cn_name in STOCK_INFO_DICT.items():
        result[cn_name] = data.get(api_key, None)

    return result


def get_etf_info(code: str) -> dict:
    """
    获取 ETF 专属信息：最新净值溢价/折价、20日均成交额（流动性）等。
    ETF 没有 PE/PB/ROE，不应套用个股基本面评分体系。

    Args:
        code: ETF代码（如 588170）

    Returns:
        Dict，包含 'price'（最新价/二级市场价）、'change_pct'（涨跌幅）、
        'avg_turnover_20d'（20日日均成交额，流动性参考）、
        'turnover_rate'（换手率）。若净值数据无法获取，'premium_pct'
        （溢价率）字段为 None，不代表数据获取失败。
    """
    quote = get_current_quote(code)
    if "error" in quote:
        return quote

    result = {
        "code": code,
        "price": quote["price"],
        "change_pct": quote.get("change_pct"),
        "turnover_rate": quote.get("turnover_rate"),
        "source": quote.get("source"),
    }

    # 20日日均成交额 —— 用于判断ETF流动性是否充足（流动性差的ETF
    # 买卖价差大，实际成交价可能偏离盘口价）
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
        df = get_kline(code, start=start, end=end)
        if not df.empty:
            result["avg_turnover_20d"] = float(df["turnover"].tail(20).mean())
            result["avg_volume_20d"] = float(df["volume"].tail(20).mean())
    except Exception:
        result["avg_turnover_20d"] = None
        result["avg_volume_20d"] = None

    # 净值溢价率暂无稳定免费数据源，标记为 None，调用方应如实告知用户
    # 该字段缺失，而不是编造数值
    result["premium_pct"] = None

    return result


def get_market_realtime(market: str = "沪深A") -> pd.DataFrame:
    """
    Fetch real-time quotes for all stocks in a market.

    Args:
        market: Market name

    Returns:
        DataFrame with all stocks' real-time data.
    """
    fs = MARKET_FILTER.get(market, MARKET_FILTER["沪深A"])
    fields = ",".join(TRADE_DETAIL_DICT.keys())
    df_total = pd.DataFrame()
    page = 1

    while True:
        params = {
            "pn": str(page),
            "pz": "5000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": fields,
        }
        try:
            time.sleep(0.3)
            resp = _request_with_retry(REALTIME_URL, params=params)
            data = resp.json()
            diff = data.get("data", {}).get("diff", [])
            if not diff:
                break
            df_page = pd.DataFrame(diff)
            df_total = pd.concat([df_total, df_page], ignore_index=True)
            page += 1
        except Exception:
            break

    if df_total.empty:
        return df_total

    available = [k for k in TRADE_DETAIL_DICT.keys() if k in df_total.columns]
    df_total = df_total[available].rename(columns=TRADE_DETAIL_DICT)

    ignore = ["代码", "名称", "所处行业"]
    df_total = _trans_num(df_total, ignore)
    return df_total
