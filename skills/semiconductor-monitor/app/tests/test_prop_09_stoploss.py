# -*- coding: utf-8 -*-
"""Property 9：止损信号生成当且仅当当前价低于止损位（Validates: Requirements 5.3）。

对任意当前价与非 None 的止损位，``RuleEngine.evaluate_signals`` 的结果中存在
``kind == "止损"`` 的信号，当且仅当 当前价 < 止损位。

说明：``vars`` 仅提供"当前价"与"止损位"两个键，其余买入/卖出条件（RSI、KDJ、
布林轨、MACD 柱、加权成本等）与放量下跌止损条件（成交量、昨收价等）均缺失，
因此不会产出买入/卖出/放量下跌止损信号，不影响本属性对"止损"信号的判定。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

# 从技能根运行 pytest，`app` 可作为顶层包被导入。
from app.rule_engine import RuleEngine

# 生成有限、非 NaN 的价格；止损位限定为非 None（题目要求）。
_prices = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)


@settings(max_examples=100)
@given(current_price=_prices, stop_loss=_prices)
def test_property_09_stop_loss_iff_below_stop(current_price, stop_loss):
    # Feature: realtime-monitor-app, Property 9: 止损信号生成当且仅当当前价低于止损位
    vars = {"当前价": current_price, "止损位": stop_loss}

    signals = RuleEngine().evaluate_signals(vars)
    has_stop_loss = any(sig.kind == "止损" for sig in signals)

    # 当且仅当：存在止损信号 ⇔ 当前价 < 止损位。
    assert has_stop_loss == (current_price < stop_loss)
