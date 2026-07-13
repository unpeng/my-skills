# -*- coding: utf-8 -*-
"""
Monitor variables module - 计算588170盯盘所需的全部动态变量。

将 SKILL.md 中原本内嵌的历史数据获取 + 技术指标计算逻辑整理到此处，
供 main.py 的 `monitor` 子命令调用。
"""

import sys
import os
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.fetcher import get_current_quote, is_etf_code
from data.kline_cache import get_kline_cached
from data.processor import detect_and_truncate_split
from model.technical import compute_all_indicators

# 技术指标各自所需的最小数据长度，数据不足时应标记为不可靠而非给出误导性数值
MIN_BARS_FOR = {
    "ma60": 60,
    "ma120": 120,
    "boll": 20,
    "kdj": 9,
    "macd": 26,
    "rsi": 14,
    "atr": 14,
    "60日最高低": 60,
    "20日均量": 20,
}

# 做T仓位波动率调整（借鉴 abu 的 ATR 仓位管理）：以"日波动率=ATR/当前价"衡量，
# 波动率不超过基准时用满资金上限，超过基准时按反比收缩，且不低于最低比例、
# 不超过原始上限（只会因高波动而降低，不会放大）。
_TDAY_BASE_VOL_RATIO = 0.02   # 基准日波动率 2%（ATR/当前价），低于此不缩减
_TDAY_MIN_CASH_PCT = 20.0     # 波动率再高，做T资金比例的下限（避免缩到 0）


class MonitorInputError(ValueError):
    """用户输入参数不合法（如持仓/成本非正数）。"""


def resolve_stop_loss(cost: float, position: float,
                      max_loss_pct: float = None,
                      max_loss_amount: float = None,
                      stop_loss_price: float = None,
                      atr_stop_n: float = None,
                      atr: float = None) -> float:
    """
    根据用户提供的四种止损设定方式之一，计算止损位。

    优先级：stop_loss_price（直接指定价格） > max_loss_amount（最大亏损金额）
    > max_loss_pct（最大亏损比例） > atr_stop_n（ATR 倍数）。UI 侧强制四选一，
    正常只有一种非空；此处保留优先级仅作多值传入时的防御性兜底。

    ATR 止损（借鉴 abu 的 N 倍 ATR 止损思路）：止损位 = 加权成本 − N×ATR。
    波动越大止损越宽、不易被日内震荡扫出；波动越小止损越紧。需要外部传入当日
    ATR 值；若指定了 atr_stop_n 但 atr 不可用（数据不足/为 None），返回 None，
    由调用方据此如实说明"ATR 数据不足、止损位暂不可用"。

    Args:
        cost: 加权成本价
        position: 持仓数量
        max_loss_pct: 最大可承受亏损比例（如 10 表示 10%）
        max_loss_amount: 最大可承受亏损金额
        stop_loss_price: 用户直接指定的止损价格
        atr_stop_n: ATR 止损倍数 N（正数）
        atr: 当日 ATR 值（由 compute_monitor_variables 从 K 线指标传入）

    Returns:
        止损位价格，若均未提供（或 ATR 方式下 atr 不可用）则返回 None。
    """
    if stop_loss_price is not None:
        return stop_loss_price
    if max_loss_amount is not None:
        if not position or position <= 0:
            raise MonitorInputError("持仓数量必须为正数才能按最大亏损金额计算止损位")
        return cost - max_loss_amount / position
    if max_loss_pct is not None:
        return cost * (1 - max_loss_pct / 100)
    if atr_stop_n is not None:
        # ATR 数据不足时不臆造止损位，返回 None 交由上层如实说明。
        if atr is None:
            return None
        return cost - atr_stop_n * atr
    return None


def _validate_inputs(code: str, position: float, cost: float, cash: float):
    if not code or not str(code).strip():
        raise MonitorInputError("股票/ETF代码不能为空")
    if position is None or position <= 0:
        raise MonitorInputError(f"持仓数量必须为正数，当前传入: {position}")
    if cost is None or cost <= 0:
        raise MonitorInputError(f"加权成本价必须为正数，当前传入: {cost}")
    if cash is not None and cash < 0:
        raise MonitorInputError(f"可用资金不能为负数，当前传入: {cash}")


def _safe_round(value, ndigits=4):
    """安全四舍五入：None 或 NaN 时返回 None，避免抛异常或输出 'nan'。"""
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f):
            return None
        return round(f, ndigits)
    except (TypeError, ValueError):
        return None


def pd_notna(value) -> bool:
    """安全判断值是否非空/非NaN，避免对 None 直接做 float() 转换报错。"""
    if value is None:
        return False
    try:
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def compute_monitor_variables(code: str, position: float, cost: float,
                              cash: float = 0.0,
                              max_loss_pct: float = None,
                              max_loss_amount: float = None,
                              stop_loss_price: float = None,
                              atr_stop_n: float = None,
                              start: str = "20200101",
                              t_cash_cap_pct: float = 80.0) -> dict:
    """
    获取历史K线并计算 SKILL.md 中定义的全部盯盘变量。

    Args:
        code: 标的代码（如 588170）
        position: 持仓数量（必须为正数）
        cost: 加权平均成本价（必须为正数）
        cash: 可用资金
        max_loss_pct: 最大可承受亏损比例
        max_loss_amount: 最大可承受亏损金额
        stop_loss_price: 用户直接指定的止损价
        atr_stop_n: ATR 止损倍数（止损位 = 成本 − N×ATR，借鉴 abu 的波动率止损）
        start: 历史数据起始日期
        t_cash_cap_pct: 做T单次最多使用可用资金的比例（默认80%，避免
                       满仓做T导致无应急资金）

    Returns:
        变量名 -> 数值 的字典；若参数不合法抛出 MonitorInputError，
        若获取数据失败则返回 {"error": ...}
    """
    _validate_inputs(code, position, cost, cash)

    # 使用本地增量缓存获取历史K线，避免每次全量拉取（方案1）
    df = get_kline_cached(code, start=start)
    if df.empty or len(df) < 2:
        return {"error": f"无法获取 {code} 的历史数据"}

    # A2: 检测未复权拆分/合并导致的异常跳空，若检测到则截断到跳空后的
    # 干净数据窗口，避免跨越拆分日的滚动指标失真
    df, split_detected, split_date = detect_and_truncate_split(df)
    usable_bars = len(df)

    df = compute_all_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else latest

    # A3: 当前价优先用实时行情（带K线兜底），比单纯读取K线最新收盘价更
    # 能反映盘中实时状态；quote 获取失败时回退到K线收盘价
    quote = get_current_quote(code)
    if "error" not in quote:
        current_price = float(quote["price"])
        prev_close = float(quote.get("prev_close") or prev["close"])
        price_source = quote.get("source", "realtime")
    else:
        current_price = float(latest["close"])
        prev_close = float(prev["close"])
        price_source = "kline_only"

    def _if_enough(indicator_key, value):
        """数据长度不足以支撑该指标时返回 None，避免给出失真数值。"""
        if usable_bars < MIN_BARS_FOR.get(indicator_key, 0):
            return None
        return value

    high_60 = _if_enough("60日最高低", float(df["high"].tail(60).max()) if usable_bars >= 1 else None)
    low_60 = _if_enough("60日最高低", float(df["low"].tail(60).min()) if usable_bars >= 1 else None)
    vol_ma20 = _if_enough("20日均量", float(df["volume"].tail(20).mean()) if usable_bars >= 1 else None)

    # ATR（当日平均真实波幅）：数据不足对应窗口时为 None（NaN 经 _if_enough+pd_notna 归一）。
    atr_raw = latest.get("atr")
    atr = _if_enough("atr", float(atr_raw)) if pd_notna(atr_raw) else None

    stop_loss = resolve_stop_loss(
        cost=cost, position=position,
        max_loss_pct=max_loss_pct,
        max_loss_amount=max_loss_amount,
        stop_loss_price=stop_loss_price,
        atr_stop_n=atr_stop_n,
        atr=atr,
    )

    t_buy_price = _safe_round(current_price * 0.98)
    t_sell_price = _safe_round(current_price * 1.02)

    result = {
        "标的类型": "ETF" if is_etf_code(code) else "个股",
        "价格来源": price_source,
        "昨收价": _safe_round(prev_close),
        "当前价": _safe_round(current_price),
        "60日最高": _safe_round(high_60),
        "60日最低": _safe_round(low_60),
        "20日均量": _safe_round(vol_ma20, 0),
        "今日成交量": _safe_round(float(latest["volume"]), 0),
        "加权成本": cost,
        "回本价": cost,
        "止损位": _safe_round(stop_loss),
        "做T买入位": t_buy_price,
        "做T卖出位": t_sell_price,
        "昨收+2%": _safe_round(prev_close * 1.02),
        "昨收-2%": _safe_round(prev_close * 0.98),
        "昨收-2.5%": _safe_round(prev_close * 0.975),
        "昨收-4%": _safe_round(prev_close * 0.96),
        "成本+2%": _safe_round(cost * 1.02),
        "成本-2%": _safe_round(cost * 0.98),
        "成本-4%": _safe_round(cost * 0.96),
        "持仓市值": _safe_round(current_price * position, 2),
        "浮动盈亏": _safe_round((current_price - cost) * position, 2),
        "盈亏比例": _safe_round((current_price / cost - 1) * 100, 2),
        "距回本": _safe_round((cost / current_price - 1) * 100, 2) if current_price else None,
        # ATR 及其相对当前价的百分比（供放量止损阈值自适应等使用）；数据不足为 None。
        "ATR": _safe_round(atr),
        "ATR百分比": _safe_round(atr / current_price * 100, 2)
        if (atr is not None and current_price) else None,
    }
    if atr_stop_n is not None:
        result["ATR止损倍数"] = atr_stop_n

    if stop_loss is not None:
        result["止损亏损"] = _safe_round((cost - stop_loss) * position, 2)
    elif atr_stop_n is not None and atr is None:
        # 用户选择了 ATR 止损但 ATR 数据不足：如实说明，不臆造止损位。
        result["_止损_说明"] = "已选择 ATR 止损方式，但 ATR 数据不足（历史K线条数不够），止损位暂不可用。"

    # C8: 做T资金设上限保护，不将可用资金全部打满，避免判断错误后没有应急资金。
    # 增强（借鉴 abu 的 ATR 仓位管理）：在 80% 上限基础上按波动率进一步收缩——
    # 日波动率（ATR/当前价）超过基准时按反比降低做T资金比例，波动越大投入越少；
    # 只会因高波动下调、不会上调超过原始上限。ATR 不可用时维持原始上限（向后兼容）。
    if cash and t_buy_price:
        base_pct = max(0.0, min(t_cash_cap_pct, 100.0))
        effective_pct = base_pct
        vol_adjusted = False
        if atr is not None and current_price:
            vol_ratio = atr / current_price
            if vol_ratio > _TDAY_BASE_VOL_RATIO:
                # 反比收缩：波动率是基准的 k 倍则比例降到 1/k，再夹在 [下限, 上限]。
                effective_pct = base_pct * (_TDAY_BASE_VOL_RATIO / vol_ratio)
                effective_pct = max(_TDAY_MIN_CASH_PCT, min(effective_pct, base_pct))
                vol_adjusted = True
        t_cash_available = cash * effective_pct / 100.0
        result["做T可用资金上限"] = _safe_round(t_cash_available, 2)
        result["做T可买份数"] = int(t_cash_available // t_buy_price)
        result["做T资金比例"] = _safe_round(effective_pct, 2)
        if vol_adjusted:
            result["_做T波动调整说明"] = (
                f"检测到日波动率 ATR/当前价={_safe_round(atr / current_price * 100, 2)}% "
                f"高于基准 {_TDAY_BASE_VOL_RATIO * 100:.0f}%，做T资金比例已由 {base_pct:.0f}% "
                f"自适应下调至 {_safe_round(effective_pct, 2)}%，以降低高波动下的单次风险。"
            )

    # 技术指标：数据不足以支撑对应窗口时返回 None，而不是给出失真数值
    result["RSI"] = _if_enough("rsi", _safe_round(latest.get("rsi"), 2))
    result["RSI超卖"] = bool(latest.get("rsi_oversold")) if result["RSI"] is not None else None
    result["RSI超买"] = bool(latest.get("rsi_overbought")) if result["RSI"] is not None else None
    result["MACD_DIF"] = _if_enough("macd", _safe_round(latest.get("macd_dif")))
    result["MACD_DEA"] = _if_enough("macd", _safe_round(latest.get("macd_dea")))
    result["MACD_HIST"] = _if_enough("macd", _safe_round(latest.get("macd_hist")))
    # 上一周期 MACD 柱值：df 在本函数内已算好整条 macd_hist 序列，直接取倒数第二根，
    # 供上层（Rule_Engine 的跨周期金叉/死叉判断）使用，避免上层为取这一个值
    # 重新拉取K线、重新计算指标（重复网络请求会拖慢单轮耗时甚至导致超时）。
    # 门控：prev 是倒数第二根，需 usable_bars-1 也满足 MACD 最小窗口才可信。
    result["MACD_HIST_PREV"] = (
        _safe_round(prev.get("macd_hist"))
        if usable_bars - 1 >= MIN_BARS_FOR.get("macd", 0) and len(df) >= 2
        else None
    )
    result["MACD金叉"] = bool(latest.get("macd_golden")) if result["MACD_HIST"] is not None else None
    result["MACD死叉"] = bool(latest.get("macd_death")) if result["MACD_HIST"] is not None else None
    result["KDJ_K"] = _if_enough("kdj", _safe_round(latest.get("kdj_k"), 2))
    result["KDJ_D"] = _if_enough("kdj", _safe_round(latest.get("kdj_d"), 2))
    result["KDJ_J"] = _if_enough("kdj", _safe_round(latest.get("kdj_j"), 2))
    result["布林上轨"] = _if_enough("boll", _safe_round(latest.get("boll_upper")))
    result["布林中轨"] = _if_enough("boll", _safe_round(latest.get("boll_mid")))
    result["布林下轨"] = _if_enough("boll", _safe_round(latest.get("boll_lower")))

    # A2: 数据质量提示，供执行者判断是否需要向用户说明
    result["_数据质量_检测到拆分跳空"] = split_detected
    result["_数据质量_拆分日期"] = split_date
    result["_数据质量_可用K线条数"] = usable_bars
    if split_detected:
        result["_数据质量_说明"] = (
            f"检测到 {split_date} 附近收盘价异常跳空（疑似份额拆分/合并且"
            f"未复权），已自动截断为拆分后的 {usable_bars} 条数据计算。"
            f"若 {usable_bars} 条不足以支撑某些指标（如MA60/60日高低），"
            f"对应字段已返回 None，请如实告知用户该情况，不要用截断前的"
            f"历史数据估算。"
        )

    # C6: 风险提示，任何输出都应附带，不构成投资建议
    result["_风险提示"] = "以上数据仅供参考，不构成投资建议，市场有风险，操作需自行判断"

    return result
