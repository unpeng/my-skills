# -*- coding: utf-8 -*-
"""
Monitor variables module - 计算588170盯盘所需的全部动态变量。

将 SKILL.md 中原本内嵌的历史数据获取 + 技术指标计算逻辑整理到此处，
供 main.py 的 `monitor` 子命令调用。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.fetcher import get_kline
from model.technical import compute_all_indicators


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
    if max_loss_amount is not None and position:
        return cost - max_loss_amount / position
    if max_loss_pct is not None:
        return cost * (1 - max_loss_pct / 100)
    return None


def compute_monitor_variables(code: str, position: float, cost: float,
                              cash: float = 0.0,
                              max_loss_pct: float = None,
                              max_loss_amount: float = None,
                              stop_loss_price: float = None,
                              start: str = "20200101") -> dict:
    """
    获取历史K线并计算 SKILL.md 中定义的全部盯盘变量。

    Args:
        code: 标的代码（如 588170）
        position: 持仓数量
        cost: 加权平均成本价
        cash: 可用资金
        max_loss_pct: 最大可承受亏损比例
        max_loss_amount: 最大可承受亏损金额
        stop_loss_price: 用户直接指定的止损价
        start: 历史数据起始日期

    Returns:
        变量名 -> 数值 的字典；若获取数据失败则返回 {"error": ...}
    """
    df = get_kline(code, start=start)
    if df.empty or len(df) < 2:
        return {"error": f"无法获取 {code} 的历史数据"}

    df = compute_all_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    prev_close = float(prev["close"])
    current_price = float(latest["close"])
    high_60 = float(df["high"].tail(60).max())
    low_60 = float(df["low"].tail(60).min())
    vol_ma20 = float(df["volume"].tail(20).mean())

    stop_loss = resolve_stop_loss(
        cost=cost, position=position,
        max_loss_pct=max_loss_pct,
        max_loss_amount=max_loss_amount,
        stop_loss_price=stop_loss_price,
    )

    result = {
        "昨收价": prev_close,
        "当前价": current_price,
        "60日最高": high_60,
        "60日最低": low_60,
        "20日均量": vol_ma20,
        "今日成交量": float(latest["volume"]),
        "加权成本": cost,
        "回本价": cost,
        "止损位": stop_loss,
        "做T买入位": round(current_price * 0.98, 4),
        "做T卖出位": round(current_price * 1.02, 4),
        "昨收+2%": round(prev_close * 1.02, 4),
        "昨收-2%": round(prev_close * 0.98, 4),
        "昨收-2.5%": round(prev_close * 0.975, 4),
        "昨收-4%": round(prev_close * 0.96, 4),
        "成本+2%": round(cost * 1.02, 4),
        "成本-2%": round(cost * 0.98, 4),
        "成本-4%": round(cost * 0.96, 4),
        "持仓市值": round(current_price * position, 2),
        "浮动盈亏": round((current_price - cost) * position, 2),
        "盈亏比例": round((current_price / cost - 1) * 100, 2),
        "距回本": round((cost / current_price - 1) * 100, 2),
    }

    if stop_loss is not None:
        result["止损亏损"] = round((cost - stop_loss) * position, 2)

    if cash and result["做T买入位"]:
        result["做T可买份数"] = int(cash // result["做T买入位"])

    # 技术指标（注意：若历史数据跨越份额拆分/除权除息日且未复权，
    # 跨越该日期的滚动指标如 MA/RSI/MACD/KDJ/BOLL 会失真，使用前需核实）
    result["RSI"] = round(float(latest["rsi"]), 2) if pd_notna(latest.get("rsi")) else None
    result["RSI超卖"] = bool(latest.get("rsi_oversold"))
    result["RSI超买"] = bool(latest.get("rsi_overbought"))
    result["MACD_DIF"] = round(float(latest["macd_dif"]), 4) if pd_notna(latest.get("macd_dif")) else None
    result["MACD_DEA"] = round(float(latest["macd_dea"]), 4) if pd_notna(latest.get("macd_dea")) else None
    result["MACD_HIST"] = round(float(latest["macd_hist"]), 4) if pd_notna(latest.get("macd_hist")) else None
    result["MACD金叉"] = bool(latest.get("macd_golden"))
    result["MACD死叉"] = bool(latest.get("macd_death"))
    result["KDJ_K"] = round(float(latest["kdj_k"]), 2) if pd_notna(latest.get("kdj_k")) else None
    result["KDJ_D"] = round(float(latest["kdj_d"]), 2) if pd_notna(latest.get("kdj_d")) else None
    result["KDJ_J"] = round(float(latest["kdj_j"]), 2) if pd_notna(latest.get("kdj_j")) else None
    result["布林上轨"] = round(float(latest["boll_upper"]), 4) if pd_notna(latest.get("boll_upper")) else None
    result["布林中轨"] = round(float(latest["boll_mid"]), 4) if pd_notna(latest.get("boll_mid")) else None
    result["布林下轨"] = round(float(latest["boll_lower"]), 4) if pd_notna(latest.get("boll_lower")) else None

    return result


def pd_notna(value) -> bool:
    """安全判断值是否非空/非NaN，避免对 None 直接做 float() 转换报错。"""
    if value is None:
        return False
    try:
        import math
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False
