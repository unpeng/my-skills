# -*- coding: utf-8 -*-
"""Position_Manager 的 ATR 止损（第四种止损方式）校验单元测试。

覆盖：
1. 仅填 ATR止损倍数 → 校验通过，value.atr_stop_n 被正确解析，其余止损字段为 None
2. ATR止损倍数越界（0 / 负数 / >20 / 非数值）→ 校验失败并给出 atr_stop_n 错误
3. ATR止损倍数与其它止损方式同时填写 → 违反"恰好选一"→ 校验失败
4. 四种止损方式一个都不填 → 校验失败
"""

from __future__ import annotations

import unittest

from app.models import PositionForm
from app.position_manager import PositionManager


def _base_form(**stop_kwargs) -> PositionForm:
    """构造基本信息合法、止损字段由参数指定的表单。"""
    return PositionForm(
        position="10000",
        cost="1.30",
        cash="5000",
        max_loss_pct=stop_kwargs.get("max_loss_pct", ""),
        max_loss_amount=stop_kwargs.get("max_loss_amount", ""),
        stop_loss_price=stop_kwargs.get("stop_loss_price", ""),
        atr_stop_n=stop_kwargs.get("atr_stop_n", ""),
    )


class TestAtrStopValidation(unittest.TestCase):
    def setUp(self):
        self.pm = PositionManager()

    def test_valid_atr_stop_only(self):
        res = self.pm.validate(_base_form(atr_stop_n="2"))
        self.assertTrue(res.valid, msg=str(res.errors))
        self.assertIsNotNone(res.value)
        self.assertEqual(res.value.atr_stop_n, 2.0)
        # 其余止损字段应为 None（恰好选一）。
        self.assertIsNone(res.value.max_loss_pct)
        self.assertIsNone(res.value.max_loss_amount)
        self.assertIsNone(res.value.stop_loss_price)

    def test_atr_stop_zero_rejected(self):
        res = self.pm.validate(_base_form(atr_stop_n="0"))
        self.assertFalse(res.valid)
        self.assertIn("atr_stop_n", res.errors)

    def test_atr_stop_negative_rejected(self):
        res = self.pm.validate(_base_form(atr_stop_n="-1"))
        self.assertFalse(res.valid)
        self.assertIn("atr_stop_n", res.errors)

    def test_atr_stop_too_large_rejected(self):
        res = self.pm.validate(_base_form(atr_stop_n="25"))
        self.assertFalse(res.valid)
        self.assertIn("atr_stop_n", res.errors)

    def test_atr_stop_non_numeric_rejected(self):
        res = self.pm.validate(_base_form(atr_stop_n="abc"))
        self.assertFalse(res.valid)
        self.assertIn("atr_stop_n", res.errors)

    def test_atr_stop_conflict_with_other_mode_rejected(self):
        # 同时填 ATR倍数 与 最大亏损比例 → 违反"恰好选一"。
        res = self.pm.validate(_base_form(atr_stop_n="2", max_loss_pct="10"))
        self.assertFalse(res.valid)
        self.assertIn("stop_loss", res.errors)

    def test_no_stop_mode_rejected(self):
        res = self.pm.validate(_base_form())
        self.assertFalse(res.valid)
        self.assertIn("stop_loss", res.errors)


if __name__ == "__main__":
    unittest.main()
