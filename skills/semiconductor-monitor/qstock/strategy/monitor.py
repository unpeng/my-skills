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
from config import (
    ATR_PERIOD, ATR_STOP_MULTIPLIER, ATR_T_MULTIPLIER,
    ATR_TRAIL_MULTIPLIER, ATR_TRAIL_WINDOW,
)

# 技术指标各自所需的最小数据长度，数据不足时应标记为不可靠而非给出误导性数值
MIN_BARS_FOR = {
    "ma60": 60,
    "ma120": 120,
    "boll": 20,
    "kdj": 9,
    "macd": 26,
    "rsi": 14,
    "atr": ATR_PERIOD,
    "移动止盈": ATR_TRAIL_WINDOW,
    "60日最高低": 60,
    "20日均量": 20,
    "综合评分": 60,  # multi_factor_score 要求至少60条数据
}


class MonitorInputError(ValueError):
    """用户输入参数不合法（如持仓/成本非正数）。"""


def resolve_stop_loss(cost: float, position: float,
                      max_loss_pct: float = None,
                      max_loss_amount: float = None,
                      stop_loss_price: float = None) -> float:
    """
    根据用户提供的三种止损设定方式之一，计算止损位。

    优先级：stop_loss_price（直接指定价格） > max_loss_amount（最大亏损金额）
    > max_loss_pct（最大亏损比例）。

    Args:
        cost: 加权成本价
        position: 持仓数量
        max_loss_pct: 最大可承受亏损比例（如 10 表示 10%）
        max_loss_amount: 最大可承受亏损金额
        stop_loss_price: 用户直接指定的止损价格

    Returns:
        止损位价格，若均未提供则返回 None。
    """
    if stop_loss_price is not None:
        return stop_loss_price
    if max_loss_amount is not None:
        if not position or position <= 0:
            raise MonitorInputError("持仓数量必须为正数才能按最大亏损金额计算止损位")
        return cost - max_loss_amount / position
    if max_loss_pct is not None:
        return cost * (1 - max_loss_pct / 100)
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
                              start: str = "20200101",
                              t_cash_cap_pct: float = 80.0,
                              atr_stop_mult: float = ATR_STOP_MULTIPLIER,
                              atr_t_mult: float = ATR_T_MULTIPLIER,
                              atr_trail_mult: float = ATR_TRAIL_MULTIPLIER) -> dict:
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
        start: 历史数据起始日期
        t_cash_cap_pct: 做T单次最多使用可用资金的比例（默认80%，避免
                       满仓做T导致无应急资金）
        atr_stop_mult: ATR止损倍数 n，ATR止损位 = 成本 - n×ATR（借鉴abu波动率自适应止损）
        atr_t_mult: 做T价差ATR倍数 k，做T买/卖位 = 现价 ∓ k×ATR
        atr_trail_mult: 移动止盈ATR倍数 m，移动止盈位 = 区间最高 - m×ATR

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

    # ATR 波动率（借鉴 abu）：数据不足时为 None，做T/止损自动回退到固定百分比
    atr_val = _if_enough("atr", float(latest.get("atr")) if pd_notna(latest.get("atr")) else None)

    stop_loss = resolve_stop_loss(
        cost=cost, position=position,
        max_loss_pct=max_loss_pct,
        max_loss_amount=max_loss_amount,
        stop_loss_price=stop_loss_price,
    )

    # 做T价位：优先用 ATR 自适应（现价 ∓ k×ATR），ATR不可用时回退固定±2%
    if atr_val is not None and atr_val > 0:
        t_buy_price = _safe_round(current_price - atr_t_mult * atr_val)
        t_sell_price = _safe_round(current_price + atr_t_mult * atr_val)
        t_price_source = f"ATR自适应(现价∓{atr_t_mult}×ATR)"
    else:
        t_buy_price = _safe_round(current_price * 0.98)
        t_sell_price = _safe_round(current_price * 1.02)
        t_price_source = "固定±2%(ATR数据不足回退)"

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
        "ATR": _safe_round(atr_val),
        "ATR占比%": _safe_round(atr_val / current_price * 100, 2) if (atr_val and current_price) else None,
        "做T买入位": t_buy_price,
        "做T卖出位": t_sell_price,
        "做T价差来源": t_price_source,
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
    }

    if stop_loss is not None:
        result["止损亏损"] = _safe_round((cost - stop_loss) * position, 2)

    # 改进1（借abu）：ATR波动率自适应止损位 = 成本 - n×ATR
    # 与用户设定的固定止损位并列输出，作为波动率参考，不覆盖用户设定
    atr_stop = None
    if atr_val is not None and atr_val > 0:
        atr_stop = cost - atr_stop_mult * atr_val
        result["ATR止损位"] = _safe_round(atr_stop)
        result["ATR止损说明"] = f"成本 - {atr_stop_mult}×ATR，波动越大止损越宽"
    else:
        result["ATR止损位"] = None

    # 改进2（借abu）：移动止盈位 = 区间最高 - m×ATR，盈利后跟随上移锁利
    is_profit = current_price > cost
    trail_stop = None
    trail_high = None
    if atr_val is not None and atr_val > 0 and usable_bars >= ATR_TRAIL_WINDOW:
        trail_high = float(df["high"].tail(ATR_TRAIL_WINDOW).max())
        trail_stop = trail_high - atr_trail_mult * atr_val
        result[f"近{ATR_TRAIL_WINDOW}日最高"] = _safe_round(trail_high)
        result["移动止盈位"] = _safe_round(trail_stop)
        result["移动止盈生效"] = bool(is_profit)
        result["移动止盈说明"] = (
            f"区间最高 - {atr_trail_mult}×ATR。仅在盈利(当前价>成本)时用于"
            f"上移止损锁利；未盈利时以常规止损位为准"
        )
    else:
        result["移动止盈位"] = None
        result["移动止盈生效"] = None

    # 建议动态止损位：综合用户止损/ATR止损/移动止盈给出单一可执行价位
    # - 盈利时：取 max(常规止损, 移动止盈)，让止损随盈利抬升
    # - 未盈利时：取常规止损（用户设定优先，无则用ATR止损）
    base_stop = stop_loss if stop_loss is not None else atr_stop
    suggested_stop = base_stop
    if is_profit and trail_stop is not None:
        candidates = [s for s in (base_stop, trail_stop) if s is not None]
        if candidates:
            suggested_stop = max(candidates)
    result["建议动态止损位"] = _safe_round(suggested_stop)

    # C8: 做T资金设上限保护，不将可用资金全部打满，避免判断错误后
    # 没有应急资金
    if cash and t_buy_price:
        t_cash_available = cash * max(0.0, min(t_cash_cap_pct, 100.0)) / 100.0
        result["做T可用资金上限"] = _safe_round(t_cash_available, 2)
        result["做T可买份数"] = int(t_cash_available // t_buy_price)

    # 技术指标：数据不足以支撑对应窗口时返回 None，而不是给出失真数值
    result["RSI"] = _if_enough("rsi", _safe_round(latest.get("rsi"), 2))
    result["RSI超卖"] = bool(latest.get("rsi_oversold")) if result["RSI"] is not None else None
    result["RSI超买"] = bool(latest.get("rsi_overbought")) if result["RSI"] is not None else None
    result["MACD_DIF"] = _if_enough("macd", _safe_round(latest.get("macd_dif")))
    result["MACD_DEA"] = _if_enough("macd", _safe_round(latest.get("macd_dea")))
    result["MACD_HIST"] = _if_enough("macd", _safe_round(latest.get("macd_hist")))
    result["MACD金叉"] = bool(latest.get("macd_golden")) if result["MACD_HIST"] is not None else None
    result["MACD死叉"] = bool(latest.get("macd_death")) if result["MACD_HIST"] is not None else None
    result["KDJ_K"] = _if_enough("kdj", _safe_round(latest.get("kdj_k"), 2))
    result["KDJ_D"] = _if_enough("kdj", _safe_round(latest.get("kdj_d"), 2))
    result["KDJ_J"] = _if_enough("kdj", _safe_round(latest.get("kdj_j"), 2))
    result["布林上轨"] = _if_enough("boll", _safe_round(latest.get("boll_upper")))
    result["布林中轨"] = _if_enough("boll", _safe_round(latest.get("boll_mid")))
    result["布林下轨"] = _if_enough("boll", _safe_round(latest.get("boll_lower")))

    # 改进3（借tqsdk目标持仓思想）：把已有的多因子综合评分接入盯盘输出，
    # 给出量化的方向判断和仓位倾向，让建议更一致、可复现。数据不足(<60条)
    # 或计算失败时对应字段置 None，不影响其余变量。
    if usable_bars >= MIN_BARS_FOR["综合评分"]:
        try:
            from strategy.scoring import multi_factor_score
            score = multi_factor_score(df)
            if "error" not in score:
                composite = score["composite_score"]
                result["综合评分"] = composite
                result["评分_技术面"] = score["technical_score"]
                result["评分_动量"] = score["momentum_score"]
                result["评分_量能"] = score["volume_score"]
                if composite >= 3:
                    direction, tilt = "偏多", "偏多，可逢低加仓/持有"
                elif composite <= -3:
                    direction, tilt = "偏空", "偏空，反弹减仓/控制仓位"
                else:
                    direction, tilt = "中性", "震荡，维持现有仓位，做T为主"
                result["方向判断"] = direction
                result["仓位倾向"] = tilt
            else:
                result["综合评分"] = None
                result["方向判断"] = None
                result["_评分说明"] = f"综合评分不可用：{score['error']}"
        except Exception as e:
            result["综合评分"] = None
            result["方向判断"] = None
            result["_评分说明"] = f"综合评分计算异常：{e}"
    else:
        result["综合评分"] = None
        result["方向判断"] = None
        result["_评分说明"] = f"数据不足{MIN_BARS_FOR['综合评分']}条，综合评分不可用"

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
