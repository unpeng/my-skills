# -*- coding: utf-8 -*-
"""Property 8：卖出信号生成当且仅当成立条件数达标（Validates: Requirements 5.2, 5.7）。

对任意 RSI、KDJ_J、当前价、布林上轨、加权成本、前后周期 MACD 柱值（均可能为 None）组合，
``RuleEngine.evaluate_signals`` 产出的卖出信号被生成，当且仅当"可参与卖出条件数 ≥ 2 且
其中成立条件数 ≥ 2"。

本测试独立于被测实现重算"可参与数"与"成立数"，再与实现是否真的产出卖出信号比对，
从而对判定阈值与 None（不可参与）处理做穷尽式随机验证。
"""

from __future__ import annotations

from typing import Optional

from hypothesis import given, settings
from hypothesis import strategies as st

# 从技能根运行 pytest，`app` 可作为顶层包被导入。
from app.rule_engine import RuleEngine

# 生成有限浮点或 None：None 用于验证"依赖值缺失则该条件不可参与"（需求 5.5/5.6）。
_maybe_float = st.one_of(
    st.none(),
    st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False),
)


def _count_sell_conditions(
    rsi: Optional[float],
    kdj_j: Optional[float],
    cur: Optional[float],
    boll_upper: Optional[float],
    cost: Optional[float],
    macd_prev: Optional[float],
    macd_curr: Optional[float],
):
    """独立重算卖出条件的（可参与数, 成立数）。

    严格对齐设计与 ``_eval_sell`` 的卖出条件集合：
      - RSI > 70（RSI 非 None 才可参与）
      - KDJ_J > 100（KDJ_J 非 None 才可参与）
      - 当前价 ≥ 布林上轨（当前价与布林上轨均非 None 才可参与）
      - macd_hist_prev > 0 且 macd_hist_curr ≤ 0（两者均非 None 才可参与）
      - 回本距离绝对值 < 2%（当前价与成本均非 None 且成本 ≠ 0 才可参与）
    """
    participating = 0
    established = 0

    if rsi is not None:
        participating += 1
        if rsi > 70:
            established += 1

    if kdj_j is not None:
        participating += 1
        if kdj_j > 100:
            established += 1

    if cur is not None and boll_upper is not None:
        participating += 1
        if cur >= boll_upper:
            established += 1

    if macd_prev is not None and macd_curr is not None:
        participating += 1
        if macd_prev > 0 and macd_curr <= 0:
            established += 1

    if cur is not None and cost is not None and cost != 0:
        participating += 1
        dist = abs((cur - cost) / cost) * 100.0
        if dist < 2.0:
            established += 1

    return participating, established


@settings(max_examples=100)
@given(
    rsi=_maybe_float,
    kdj_j=_maybe_float,
    cur=_maybe_float,
    boll_upper=_maybe_float,
    cost=_maybe_float,
    macd_prev=_maybe_float,
    macd_curr=_maybe_float,
)
def test_property_08_sell_signal_iff_thresholds(
    rsi, kdj_j, cur, boll_upper, cost, macd_prev, macd_curr
):
    # Feature: realtime-monitor-app, Property 8: 卖出信号生成当且仅当成立条件数达标
    vars = {
        "RSI": rsi,
        "KDJ_J": kdj_j,
        "当前价": cur,
        "布林上轨": boll_upper,
        "加权成本": cost,
        # 跨周期 MACD 柱值以派生键放入 vars（见 rule_engine._macd_hist_pair）。
        "macd_hist_prev": macd_prev,
        "macd_hist_curr": macd_curr,
    }

    signals = RuleEngine().evaluate_signals(vars)
    sell_generated = any(s.kind == "卖出" for s in signals)

    participating, established = _count_sell_conditions(
        rsi, kdj_j, cur, boll_upper, cost, macd_prev, macd_curr
    )
    expected = participating >= 2 and established >= 2

    assert sell_generated is expected, (
        f"可参与数={participating} 成立数={established} "
        f"期望卖出={expected} 实际卖出={sell_generated}"
    )
