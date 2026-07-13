# -*- coding: utf-8 -*-
"""
单标的规则回测模块（改进5，借鉴 abu 的策略回测 + tqsdk 的回测→验证思路）。

与 strategy/backtest.py（横截面选股回测）不同，本模块针对**单只标的**
（如 588170）的 ATR 网格做T + ATR 止损规则，在历史日K线上做事件驱动回测，
用于验证改进1/4 的参数（k/n/档数）在历史上是否有效，避免"拍脑袋定阈值"。

回测假设（简化、保守）：
- 以"前一日收盘价"为当日网格中枢，band = k × 前一日ATR
- 盘中若最低价触及买入触发价（中枢-band）→ 以触发价买入一手
- 盘中若最高价触及卖出触发价（中枢+band）→ 以触发价卖出一手（做T止盈）
- 若收盘价跌破 持仓均价 - n×ATR → 收盘价清仓止损
- 每手固定资金，最多持有 max_lots 手，避免无限加仓
- 不含手续费/滑点（ETF费率极低；如需更严谨可自行加）

纯计算 + 依赖历史K线 DataFrame，不联网。
"""

import sys
import os
import math

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model.technical import compute_all_indicators
from strategy.backtest import _compute_metrics


def grid_triggers(ref_price: float, atr: float, k: float) -> tuple:
    """
    根据中枢价与ATR计算买入/卖出触发价（纯函数，便于单测）。

    Returns: (buy_trigger, sell_trigger)，atr无效时返回 (None, None)。
    """
    if ref_price is None or atr is None or atr <= 0:
        return None, None
    band = k * atr
    return ref_price - band, ref_price + band


def backtest_atr_grid(df: pd.DataFrame,
                      init_cash: float = 100000.0,
                      k_atr: float = 1.0,
                      n_stop: float = 2.5,
                      lot_cash_pct: float = 20.0,
                      max_lots: int = 4) -> dict:
    """
    对单标的历史K线运行 ATR 网格做T + ATR止损回测。

    Args:
        df: 历史K线 DataFrame（含 open/high/low/close/volume），按日期升序
        init_cash: 初始资金
        k_atr: 网格 band = k_atr × ATR
        n_stop: 止损 = 持仓均价 - n_stop × ATR
        lot_cash_pct: 每手使用初始资金的比例（默认20%）
        max_lots: 最多持有手数（限制加仓次数）

    Returns:
        dict：含 metrics（复用 backtest._compute_metrics）、trades（成交明细）、
        equity_curve、benchmark_curve（买入持有基准）等。
    """
    if df is None or df.empty or len(df) < 30:
        return {"error": "数据不足，无法回测（需至少30条K线）"}

    df = compute_all_indicators(df).reset_index(drop=True)

    cash = float(init_cash)
    shares = 0
    avg_cost = 0.0
    lot_cash = init_cash * lot_cash_pct / 100.0

    trades = []
    equity_list = []
    dates = []

    for i in range(1, len(df)):
        prev = df.iloc[i - 1]
        bar = df.iloc[i]
        atr = prev.get("atr")
        ref = prev.get("close")

        if atr is not None and not (isinstance(atr, float) and math.isnan(atr)) and atr > 0:
            buy_trigger, sell_trigger = grid_triggers(float(ref), float(atr), k_atr)
            n_stop_price = (avg_cost - n_stop * float(atr)) if shares > 0 else None

            # 1) 止损优先：收盘跌破止损价则清仓
            if shares > 0 and n_stop_price is not None and float(bar["close"]) < n_stop_price:
                cash += shares * float(bar["close"])
                trades.append({
                    "date": bar.get("date"), "action": "止损清仓",
                    "price": round(float(bar["close"]), 4), "shares": shares,
                })
                shares = 0
                avg_cost = 0.0
            else:
                # 2) 买入：盘中触及买入触发价且未超最大手数、资金充足
                lots_held = (shares * buy_trigger) / lot_cash if lot_cash > 0 else max_lots
                if (float(bar["low"]) <= buy_trigger and cash >= buy_trigger
                        and lots_held < max_lots):
                    qty = int(lot_cash // buy_trigger)
                    if qty > 0 and cash >= qty * buy_trigger:
                        cost_add = qty * buy_trigger
                        avg_cost = ((avg_cost * shares + cost_add) / (shares + qty)
                                    if (shares + qty) > 0 else buy_trigger)
                        shares += qty
                        cash -= cost_add
                        trades.append({
                            "date": bar.get("date"), "action": "做T买入",
                            "price": round(buy_trigger, 4), "shares": qty,
                        })

                # 3) 卖出止盈：盘中触及卖出触发价且有持仓
                if float(bar["high"]) >= sell_trigger and shares > 0:
                    qty = int(lot_cash // sell_trigger) if sell_trigger > 0 else shares
                    qty = min(qty, shares) if qty > 0 else shares
                    if qty > 0:
                        cash += qty * sell_trigger
                        shares -= qty
                        if shares == 0:
                            avg_cost = 0.0
                        trades.append({
                            "date": bar.get("date"), "action": "做T卖出",
                            "price": round(sell_trigger, 4), "shares": qty,
                        })

        equity_list.append(cash + shares * float(bar["close"]))
        dates.append(bar.get("date"))

    equity = pd.Series(equity_list, index=pd.to_datetime(dates))
    daily_ret = equity.pct_change().dropna()

    # 基准：同期买入持有
    bench_close = df["close"].iloc[1:].reset_index(drop=True)
    bench_ret = bench_close.pct_change().dropna()
    bench_ret.index = daily_ret.index[:len(bench_ret)] if len(daily_ret) else None

    metrics = _compute_metrics(daily_ret, bench_ret)
    metrics["成交笔数"] = len(trades)
    metrics["期末持仓"] = shares
    metrics["期末现金"] = round(cash, 2)
    metrics["期末权益"] = round(float(equity.iloc[-1]) if len(equity) else init_cash, 2)

    return {
        "params": {
            "init_cash": init_cash, "k_atr": k_atr, "n_stop": n_stop,
            "lot_cash_pct": lot_cash_pct, "max_lots": max_lots,
        },
        "metrics": metrics,
        "trades": trades,
        "equity_curve": equity,
        "benchmark_curve": (1 + bench_ret).cumprod() if len(bench_ret) else None,
    }
