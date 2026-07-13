# -*- coding: utf-8 -*-
"""Property 10：放量下跌止损信号生成条件（Validates: Requirements 5.4）。

对任意的（今日成交量、20日均量、昨收价、当前价，均非 None 且昨收>0），
``RuleEngine.evaluate_signals`` 应当且仅当满足
``今日成交量 > 20日均量×1.5`` 且 ``当日跌幅 > 3%``
（当日跌幅=(昨收价−当前价)/昨收价×100%）时，产出一个 kind=="放量下跌止损" 的信号。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

# 从技能根运行 pytest，`app` 可作为顶层包被导入。
from app.rule_engine import RuleEngine


@settings(max_examples=100)
@given(
    # 使用整数生成，规避浮点临界（如恰等于阈值）导致的抖动；独立重算沿用相同算式。
    volume=st.integers(min_value=0, max_value=10_000_000),
    vol_ma20=st.integers(min_value=1, max_value=10_000_000),
    prev_close=st.integers(min_value=1, max_value=1000),  # 昨收 > 0
    cur=st.integers(min_value=0, max_value=1000),
)
def test_property_10_volume_stop_loss_condition(volume, vol_ma20, prev_close, cur):
    # Feature: realtime-monitor-app, Property 10: 放量下跌止损信号生成条件
    vars = {
        "今日成交量": volume,
        "20日均量": vol_ma20,
        "昨收价": prev_close,
        "当前价": cur,
    }

    signals = RuleEngine().evaluate_signals(vars)
    produced = any(s.kind == "放量下跌止损" for s in signals)

    # 独立按需求 5.4 的定义重算触发条件（沿用与实现一致的算式）。
    volume_surge = volume > vol_ma20 * 1.5
    drop_pct = (prev_close - cur) / prev_close * 100.0
    expected = volume_surge and drop_pct > 3.0

    # 当且仅当同时满足放量与跌幅条件时产出该信号。
    assert produced == expected
