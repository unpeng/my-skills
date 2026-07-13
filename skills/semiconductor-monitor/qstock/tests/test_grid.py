# -*- coding: utf-8 -*-
"""网格做T模块（改进4）单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategy.grid import compute_grid


class GridTest(unittest.TestCase):
    def test_atr_based_grid(self):
        res = compute_grid(current_price=1.0, atr=0.02, cash=10000,
                           levels=3, step_atr_mult=1.0, cash_cap_pct=80.0)
        self.assertNotIn("error", res)
        self.assertAlmostEqual(res["档间距"], 0.02, places=6)
        self.assertIn("ATR自适应", res["档间距来源"])
        self.assertEqual(res["档数"], 3)
        # cap = 10000*80% = 8000, 每档 8000/3
        self.assertAlmostEqual(res["做T可用资金上限"], 8000.0, places=2)
        # 买入档价位 = 现价 - i*step
        prices = [g["买入价"] for g in res["买入网格"]]
        self.assertEqual(prices, [0.98, 0.96, 0.94])
        # 卖出档与同档买入档份数对称
        for b, s in zip(res["买入网格"], res["卖出网格"]):
            self.assertEqual(b["份数"], s["份数"])
        # 卖出价 = 现价 + i*step
        self.assertEqual([g["卖出价"] for g in res["卖出网格"]], [1.02, 1.04, 1.06])

    def test_fallback_when_no_atr(self):
        res = compute_grid(current_price=2.0, atr=None, cash=10000,
                           levels=2, step_pct_fallback=2.0)
        self.assertAlmostEqual(res["档间距"], 0.04, places=6)  # 2.0*2%
        self.assertIn("固定", res["档间距来源"])

    def test_below_stop_flag(self):
        res = compute_grid(current_price=1.0, atr=0.02, cash=10000,
                           levels=3, stop_loss=0.97)
        # 0.98 不低于止损，0.96/0.94 低于止损
        flags = [g["低于止损"] for g in res["买入网格"]]
        self.assertEqual(flags, [False, True, True])

    def test_invalid_params(self):
        self.assertIn("error", compute_grid(current_price=0, atr=0.02))
        self.assertIn("error", compute_grid(current_price=1.0, atr=0.02, levels=0))

    def test_zero_cash_gives_zero_shares(self):
        res = compute_grid(current_price=1.0, atr=0.02, cash=0, levels=3)
        self.assertTrue(all(g["份数"] == 0 for g in res["买入网格"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
