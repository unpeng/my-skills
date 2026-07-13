# -*- coding: utf-8 -*-
"""单标的规则回测模块（改进5）单元测试。"""

import os
import sys
import math
import unittest
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategy.rule_backtest import grid_triggers, backtest_atr_grid


def _oscillating_df(n=80, center=1.0, amp=0.08):
    """构造上下震荡的K线，保证网格买卖触发。"""
    today = datetime.now()
    rows = []
    for i in range(n):
        # 用正弦震荡
        c = center + amp * math.sin(i / 3.0)
        d = (today - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d")
        rows.append({
            "name": "测试ETF", "code": "588170", "date": d,
            "open": c, "high": c + 0.03, "low": c - 0.03,
            "close": c, "volume": 1_000_000, "turnover": c * 1_000_000,
        })
    return pd.DataFrame(rows)


class GridTriggersTest(unittest.TestCase):
    def test_triggers(self):
        buy, sell = grid_triggers(1.0, 0.02, 1.0)
        self.assertAlmostEqual(buy, 0.98, places=6)
        self.assertAlmostEqual(sell, 1.02, places=6)

    def test_triggers_no_atr(self):
        self.assertEqual(grid_triggers(1.0, None, 1.0), (None, None))
        self.assertEqual(grid_triggers(1.0, 0, 1.0), (None, None))


class BacktestTest(unittest.TestCase):
    def test_insufficient_data(self):
        df = _oscillating_df(n=20)
        res = backtest_atr_grid(df)
        self.assertIn("error", res)

    def test_backtest_runs_and_trades(self):
        df = _oscillating_df(n=80, amp=0.10)
        res = backtest_atr_grid(df, init_cash=100000, k_atr=1.0, n_stop=2.5)
        self.assertNotIn("error", res)
        # 震荡行情应至少触发若干笔成交
        self.assertGreaterEqual(res["metrics"]["成交笔数"], 1)
        # 关键绩效指标齐全
        for key in ("总收益率", "最大回撤", "夏普比率", "期末权益"):
            self.assertIn(key, res["metrics"])
        # 权益曲线长度合理
        self.assertGreater(len(res["equity_curve"]), 0)

    def test_params_recorded(self):
        df = _oscillating_df(n=60)
        res = backtest_atr_grid(df, init_cash=50000, k_atr=1.5, n_stop=3.0)
        self.assertEqual(res["params"]["init_cash"], 50000)
        self.assertEqual(res["params"]["k_atr"], 1.5)
        self.assertEqual(res["params"]["n_stop"], 3.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
