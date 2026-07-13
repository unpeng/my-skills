# -*- coding: utf-8 -*-
"""决策日志复盘模块（改进5）单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy.review as review


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self._orig = review.read_decision_log

    def tearDown(self):
        review.read_decision_log = self._orig

    def _patch(self, entries):
        review.read_decision_log = lambda code=None, limit=1000: list(entries)

    def test_empty_log(self):
        self._patch([])
        res = review.review_decisions("588170")
        self.assertIn("error", res)

    def test_full_pair_profit(self):
        self._patch([
            {"time": "t1", "code": "588170", "action": "做T买入", "price": 1.0, "shares": 100},
            {"time": "t2", "code": "588170", "action": "做T卖出", "price": 1.1, "shares": 100},
        ])
        res = review.review_decisions("588170")
        self.assertEqual(res["已配对次数"], 1)
        self.assertEqual(res["盈利次数"], 1)
        self.assertAlmostEqual(res["总已实现盈亏"], 10.0, places=2)
        self.assertEqual(res["胜率"], 100.0)
        self.assertEqual(res["未平仓买入笔数"], 0)

    def test_partial_close(self):
        self._patch([
            {"time": "t1", "code": "588170", "action": "做T买入", "price": 1.0, "shares": 100},
            {"time": "t2", "code": "588170", "action": "做T卖出", "price": 1.1, "shares": 50},
        ])
        res = review.review_decisions("588170")
        self.assertEqual(res["已配对次数"], 1)
        self.assertAlmostEqual(res["总已实现盈亏"], 5.0, places=2)  # (1.1-1.0)*50
        self.assertEqual(res["未平仓买入笔数"], 1)  # 剩50份未平

    def test_stop_loss_counts_as_sell(self):
        self._patch([
            {"time": "t1", "code": "588170", "action": "做T买入", "price": 1.2, "shares": 100},
            {"time": "t2", "code": "588170", "action": "止损清仓", "price": 1.1, "shares": 100},
        ])
        res = review.review_decisions("588170")
        self.assertEqual(res["亏损次数"], 1)
        self.assertAlmostEqual(res["总已实现盈亏"], -10.0, places=2)
        self.assertEqual(res["胜率"], 0.0)

    def test_sell_without_shares_matches_buy_qty(self):
        self._patch([
            {"time": "t1", "code": "588170", "action": "做T买入", "price": 1.0, "shares": 100},
            {"time": "t2", "code": "588170", "action": "做T卖出", "price": 0.9, "shares": None},
        ])
        res = review.review_decisions("588170")
        self.assertEqual(res["已配对次数"], 1)
        self.assertAlmostEqual(res["总已实现盈亏"], -10.0, places=2)  # (0.9-1.0)*100


if __name__ == "__main__":
    unittest.main(verbosity=2)
