# -*- coding: utf-8 -*-
"""
ATR 指标与 ATR 止损的单元测试（纯计算，不联网）。

覆盖：
1. compute_atr 的 True Range / ATR 计算正确性与数据不足时的 NaN 行为
2. compute_atr 挂载到 compute_all_indicators 后产出 atr/atr14/atr21 列
3. resolve_stop_loss 的 ATR 止损分支：止损位 = 成本 − N×ATR
4. resolve_stop_loss 在 ATR 方式下 atr 缺失时返回 None（不臆造止损位）
5. resolve_stop_loss 的优先级：显式止损价 / 金额 / 比例 高于 ATR 倍数
"""

import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model.technical import compute_atr, compute_all_indicators
from strategy.monitor import resolve_stop_loss


def _make_ohlc(n, high, low, close):
    """构造等长 OHLC DataFrame（open 取 close 近似，仅用于指标计算）。"""
    return pd.DataFrame(
        {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": [1000] * n,
        }
    )


class TestComputeAtr(unittest.TestCase):
    def test_true_range_and_atr_value(self):
        # 构造已知序列，手算 TR 后与实现比对。
        # 每天 high-low = 2；相邻收盘无跳空影响时 TR 恒为 2，ATR 应恒为 2。
        n = 20
        close = [10.0 + i for i in range(n)]
        high = [c + 1.0 for c in close]
        low = [c - 1.0 for c in close]
        df = _make_ohlc(n, high, low, close)

        out = compute_atr(df, periods=[14])
        # 前 13 行不足 14 日窗口 → NaN；第 14 行起有值。
        self.assertTrue(np.isnan(out["atr14"].iloc[12]))
        self.assertFalse(np.isnan(out["atr14"].iloc[13]))
        # 由于每日真实波幅稳定（收盘递增1、日内±1，TR=max(2, |high-prevclose|, |low-prevclose|)）。
        # high-prevclose = (c+1)-(c-1)=2；low-prevclose = (c-1)-(c-1)=0；high-low=2 → TR=2。
        self.assertAlmostEqual(out["atr14"].iloc[-1], 2.0, places=6)

    def test_default_atr_column_and_periods(self):
        n = 30
        close = [20.0 + (i % 5) for i in range(n)]  # 有波动
        high = [c + 0.5 for c in close]
        low = [c - 0.5 for c in close]
        df = _make_ohlc(n, high, low, close)

        out = compute_atr(df, periods=[14, 21])
        self.assertIn("atr14", out.columns)
        self.assertIn("atr21", out.columns)
        self.assertIn("atr", out.columns)
        # 默认 atr 应等于 atr14。
        last14 = out["atr14"].iloc[-1]
        self.assertAlmostEqual(out["atr"].iloc[-1], last14, places=9)

    def test_compute_all_indicators_includes_atr(self):
        n = 40
        close = [15.0 + np.sin(i) for i in range(n)]
        high = [c + 0.3 for c in close]
        low = [c - 0.3 for c in close]
        df = _make_ohlc(n, high, low, close)

        out = compute_all_indicators(df)
        self.assertIn("atr", out.columns)
        self.assertFalse(np.isnan(out["atr"].iloc[-1]))

    def test_insufficient_data_is_nan(self):
        n = 5  # < 14
        close = [10.0 + i for i in range(n)]
        high = [c + 1 for c in close]
        low = [c - 1 for c in close]
        df = _make_ohlc(n, high, low, close)

        out = compute_atr(df, periods=[14])
        self.assertTrue(out["atr14"].isna().all())


class TestResolveStopLossAtr(unittest.TestCase):
    def test_atr_stop_loss_value(self):
        # 止损位 = 成本 − N×ATR。
        stop = resolve_stop_loss(cost=1.30, position=1000, atr_stop_n=2.0, atr=0.03)
        self.assertAlmostEqual(stop, 1.30 - 2.0 * 0.03, places=9)

    def test_atr_stop_loss_none_when_atr_missing(self):
        # 选择了 ATR 方式但 atr 不可用 → 返回 None，不臆造。
        stop = resolve_stop_loss(cost=1.30, position=1000, atr_stop_n=2.0, atr=None)
        self.assertIsNone(stop)

    def test_priority_over_atr(self):
        # 直接指定止损价优先于 ATR 倍数。
        stop = resolve_stop_loss(
            cost=1.30, position=1000, stop_loss_price=1.25, atr_stop_n=2.0, atr=0.03
        )
        self.assertEqual(stop, 1.25)
        # 最大亏损比例优先于 ATR 倍数。
        stop2 = resolve_stop_loss(
            cost=1.30, position=1000, max_loss_pct=10, atr_stop_n=2.0, atr=0.03
        )
        self.assertAlmostEqual(stop2, 1.30 * 0.9, places=9)

    def test_none_when_no_mode(self):
        self.assertIsNone(resolve_stop_loss(cost=1.30, position=1000))


if __name__ == "__main__":
    unittest.main()
