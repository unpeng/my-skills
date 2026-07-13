# -*- coding: utf-8 -*-
"""Property 12：放量下跌止损的跌幅阈值随 ATR 自适应（Validates: Requirements 5.4 增强）。

借鉴 abu 的波动率自适应思路：当 ``vars`` 提供 "ATR百分比"（当日 ATR 相对当前价的
百分比）且为正时，放量下跌止损的跌幅触发阈值改为 ``max(3%, 1.5×ATR%)``；否则退回
固定 3%（向后兼容）。

本测试验证：对任意（今日成交量、20日均量、昨收价、当前价、ATR百分比），
``RuleEngine.evaluate_signals`` 产出 "放量下跌止损" 当且仅当
``今日成交量 > 20日均量×1.5`` 且 ``当日跌幅 > max(3%, 1.5×ATR%)``。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.rule_engine import RuleEngine

_BASE = 3.0
_N = 1.5


@settings(max_examples=200)
@given(
    volume=st.integers(min_value=0, max_value=10_000_000),
    vol_ma20=st.integers(min_value=1, max_value=10_000_000),
    prev_close=st.integers(min_value=1, max_value=1000),
    cur=st.integers(min_value=0, max_value=1000),
    # ATR 百分比覆盖 0（含缺失语义边界）到较大波动；用一位小数规避浮点临界抖动。
    atr_pct_tenths=st.integers(min_value=0, max_value=120),  # 0.0% ~ 12.0%
)
def test_property_12_atr_adaptive_volume_threshold(
    volume, vol_ma20, prev_close, cur, atr_pct_tenths
):
    # Feature: realtime-monitor-app, Property 12: 放量下跌止损阈值随 ATR 自适应
    atr_pct = atr_pct_tenths / 10.0
    vars = {
        "今日成交量": volume,
        "20日均量": vol_ma20,
        "昨收价": prev_close,
        "当前价": cur,
        "ATR百分比": atr_pct,
    }

    signals = RuleEngine().evaluate_signals(vars)
    produced = any(s.kind == "放量下跌止损" for s in signals)

    volume_surge = volume > vol_ma20 * 1.5
    drop_pct = (prev_close - cur) / prev_close * 100.0
    # 独立重算阈值：ATR% 为正取 max(3%, 1.5×ATR%)，否则退回 3%。
    if atr_pct > 0:
        threshold = max(_BASE, _N * atr_pct)
    else:
        threshold = _BASE
    expected = volume_surge and drop_pct > threshold

    assert produced == expected


@settings(max_examples=50)
@given(
    volume=st.integers(min_value=0, max_value=10_000_000),
    vol_ma20=st.integers(min_value=1, max_value=10_000_000),
    prev_close=st.integers(min_value=1, max_value=1000),
    cur=st.integers(min_value=0, max_value=1000),
)
def test_property_12_absent_atr_falls_back_to_3pct(volume, vol_ma20, prev_close, cur):
    # ATR百分比缺失时必须与旧行为完全一致（固定 3% 阈值），保证向后兼容。
    vars = {
        "今日成交量": volume,
        "20日均量": vol_ma20,
        "昨收价": prev_close,
        "当前价": cur,
    }
    signals = RuleEngine().evaluate_signals(vars)
    produced = any(s.kind == "放量下跌止损" for s in signals)

    volume_surge = volume > vol_ma20 * 1.5
    drop_pct = (prev_close - cur) / prev_close * 100.0
    expected = volume_surge and drop_pct > _BASE

    assert produced == expected
