# -*- coding: utf-8 -*-
"""
ATR 波动率自适应风控 + 综合评分接入的单元测试。

覆盖：
1. compute_atr：True Range / Wilder 平滑 / atr_pct 计算正确
2. compute_all_indicators 已包含 atr 列
3. monitor 集成（注入假数据源，不联网）：
   - 做T买入/卖出位 = 现价 ∓ k×ATR
   - ATR止损位 = 成本 - n×ATR
   - 移动止盈位 = 区间最高 - m×ATR，且盈利时并入建议动态止损位
   - 综合评分/方向判断 已接入
4. ATR 数据不足时：做T价位回退固定 ±2%，ATR相关字段为 None
"""

import os
import sys
import math
import unittest
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model.technical import compute_atr, compute_all_indicators
import strategy.monitor as monitor
from config import (
    ATR_PERIOD, ATR_STOP_MULTIPLIER, ATR_T_MULTIPLIER,
    ATR_TRAIL_MULTIPLIER, ATR_TRAIL_WINDOW,
)


def _make_df(n, base=1.0, code="588170"):
    """构造 n 条与 get_kline 结构一致、含真实高低波动的日K线。"""
    today = datetime.now()
    rows = []
    for i in range(n):
        # 制造温和上行+波动，保证 TR / ATR 非零
        c = base + i * 0.01 + (0.02 if i % 2 == 0 else -0.015)
        d = (today - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d")
        rows.append({
            "name": "测试ETF", "code": code, "date": d,
            "open": c - 0.005, "high": c + 0.03, "low": c - 0.03,
            "close": c, "volume": 1_000_000 + i * 1000, "turnover": c * 1_000_000,
            "turnover_rate": 0.5, "amplitude": 1.0,
            "pct_change": 0.1, "change": 0.001,
        })
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["date"])
    return df


class ComputeAtrTest(unittest.TestCase):
    def test_atr_basic_true_range(self):
        # 构造一段简单数据，手算首个可用 TR
        df = _make_df(30)
        out = compute_atr(df, period=ATR_PERIOD)
        self.assertIn("atr", out.columns)
        self.assertIn("atr_pct", out.columns)
        latest = out.iloc[-1]
        self.assertTrue(latest["atr"] > 0)
        # atr_pct 应等于 atr/close*100
        self.assertAlmostEqual(
            latest["atr_pct"], latest["atr"] / latest["close"] * 100, places=6
        )

    def test_atr_insufficient_data_is_nan(self):
        # 数据条数 < period 时，ATR 因 min_periods 应为 NaN
        df = _make_df(ATR_PERIOD - 2)
        out = compute_atr(df, period=ATR_PERIOD)
        self.assertTrue(math.isnan(float(out.iloc[-1]["atr"])))

    def test_compute_all_indicators_includes_atr(self):
        df = _make_df(80)
        out = compute_all_indicators(df)
        self.assertIn("atr", out.columns)
        self.assertTrue(out.iloc[-1]["atr"] > 0)


class MonitorAtrIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._orig_kline = monitor.get_kline_cached
        self._orig_quote = monitor.get_current_quote

    def tearDown(self):
        monitor.get_kline_cached = self._orig_kline
        monitor.get_current_quote = self._orig_quote

    def _patch(self, df, price, prev_close):
        monitor.get_kline_cached = lambda code, start="20200101": df
        monitor.get_current_quote = lambda code: {
            "price": price, "prev_close": prev_close, "source": "realtime",
        }

    def test_atr_based_t_prices_and_stop(self):
        df = _make_df(80)
        current = float(df.iloc[-1]["close"])
        self._patch(df, price=current, prev_close=float(df.iloc[-2]["close"]))

        res = monitor.compute_monitor_variables(
            code="588170", position=10000, cost=current * 1.05, cash=100000,
            max_loss_pct=10,
        )
        self.assertNotIn("error", res)
        atr = res["ATR"]
        self.assertIsNotNone(atr)
        self.assertEqual(res["做T价差来源"][:3], "ATR")
        # 做T买/卖位 = 现价 ∓ k×ATR
        self.assertAlmostEqual(res["做T买入位"], round(current - ATR_T_MULTIPLIER * atr, 4), places=3)
        self.assertAlmostEqual(res["做T卖出位"], round(current + ATR_T_MULTIPLIER * atr, 4), places=3)
        # ATR止损位 = 成本 - n×ATR
        self.assertAlmostEqual(res["ATR止损位"], round(current * 1.05 - ATR_STOP_MULTIPLIER * atr, 4), places=3)
        # 综合评分已接入
        self.assertIsNotNone(res["综合评分"])
        self.assertIn(res["方向判断"], ("偏多", "偏空", "中性"))

    def test_trailing_stop_when_profit(self):
        df = _make_df(80)
        current = float(df.iloc[-1]["close"])
        # 成本远低于现价 → 盈利，移动止盈应并入建议动态止损位
        cost = current * 0.7
        self._patch(df, price=current, prev_close=float(df.iloc[-2]["close"]))

        res = monitor.compute_monitor_variables(
            code="588170", position=10000, cost=cost, cash=100000, max_loss_pct=10,
        )
        atr = res["ATR"]
        trail_high = float(df["high"].tail(ATR_TRAIL_WINDOW).max())
        expected_trail = round(trail_high - ATR_TRAIL_MULTIPLIER * atr, 4)
        self.assertAlmostEqual(res["移动止盈位"], expected_trail, places=3)
        self.assertTrue(res["移动止盈生效"])
        # 盈利时建议动态止损位 = max(常规止损, 移动止盈)
        regular_stop = cost * 0.9
        self.assertAlmostEqual(
            res["建议动态止损位"], round(max(regular_stop, expected_trail), 4), places=3
        )

    def test_no_trailing_when_loss(self):
        df = _make_df(80)
        current = float(df.iloc[-1]["close"])
        cost = current * 1.2  # 亏损
        self._patch(df, price=current, prev_close=float(df.iloc[-2]["close"]))

        res = monitor.compute_monitor_variables(
            code="588170", position=10000, cost=cost, cash=100000, max_loss_pct=10,
        )
        self.assertFalse(res["移动止盈生效"])
        # 未盈利时建议动态止损位应等于常规止损位，不被移动止盈抬升
        self.assertAlmostEqual(res["建议动态止损位"], round(cost * 0.9, 4), places=3)

    def test_atr_fallback_when_insufficient_bars(self):
        # 只有 10 条数据 < ATR_PERIOD → ATR 不可用，做T回退固定±2%
        df = _make_df(10)
        current = float(df.iloc[-1]["close"])
        self._patch(df, price=current, prev_close=float(df.iloc[-2]["close"]))

        res = monitor.compute_monitor_variables(
            code="588170", position=10000, cost=current, cash=100000, max_loss_pct=10,
        )
        self.assertIsNone(res["ATR"])
        self.assertIn("固定±2%", res["做T价差来源"])
        self.assertAlmostEqual(res["做T买入位"], round(current * 0.98, 4), places=4)
        self.assertAlmostEqual(res["做T卖出位"], round(current * 1.02, 4), places=4)
        self.assertIsNone(res["ATR止损位"])
        self.assertIsNone(res["移动止盈位"])
        # 数据不足60条，综合评分应为 None
        self.assertIsNone(res["综合评分"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
