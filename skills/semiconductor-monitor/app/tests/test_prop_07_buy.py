# -*- coding: utf-8 -*-
"""Property 7：买入信号生成当且仅当成立条件数达标（Validates: Requirements 5.1, 5.7）。

买入信号为"条件集合"型信号，其四个条件为：
  1. RSI < 30（超卖）；
  2. KDJ_J < 0；
  3. 当前价 ≤ 布林下轨；
  4. macd_hist_prev < 0 且 macd_hist_curr ≥ 0（MACD 柱由负转正）。

某条件依赖的任一取值为 None 时该条件"不可参与"（既不计成立也不计不成立）。
生成规则（需求 5.1/5.7）：可参与条件数 < 2 不生成；可参与条件中成立数 ≥ 2 才生成。

本测试独立重算"可参与买入条件数"与"成立数"，断言：
买入信号被生成 当且仅当 可参与条件数 ≥ 2 且 成立数 ≥ 2。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

# 从技能根运行 pytest，`app` 可作为顶层包被导入。
from app.models import Signal
from app.rule_engine import RuleEngine

# 生成"可能为 None、否则为普通有限浮点数"的取值。
# 排除 NaN/Inf，使被测实现的 _num 归一等价于恒等，从而独立重算与实现口径一致。
_maybe_float = st.one_of(
    st.none(),
    st.floats(min_value=-200.0, max_value=200.0, allow_nan=False, allow_infinity=False),
)


def _expected_participating_and_established(cur, rsi, kdj_j, boll_lower, macd_prev, macd_curr):
    """独立于被测实现，重算买入四条件的"可参与数"与"成立数"。"""
    participating = 0
    established = 0

    # 条件1：RSI < 30，依赖 RSI。
    if rsi is not None:
        participating += 1
        if rsi < 30:
            established += 1

    # 条件2：KDJ_J < 0，依赖 KDJ_J。
    if kdj_j is not None:
        participating += 1
        if kdj_j < 0:
            established += 1

    # 条件3：当前价 ≤ 布林下轨，依赖 当前价 与 布林下轨。
    if cur is not None and boll_lower is not None:
        participating += 1
        if cur <= boll_lower:
            established += 1

    # 条件4：MACD 柱由负转正，依赖 macd_hist_prev 与 macd_hist_curr。
    if macd_prev is not None and macd_curr is not None:
        participating += 1
        if macd_prev < 0 and macd_curr >= 0:
            established += 1

    return participating, established


@settings(max_examples=100)
@given(
    cur=_maybe_float,
    rsi=_maybe_float,
    kdj_j=_maybe_float,
    boll_lower=_maybe_float,
    macd_prev=_maybe_float,
    macd_curr=_maybe_float,
)
def test_property_07_buy_signal_iff_thresholds_met(
    cur, rsi, kdj_j, boll_lower, macd_prev, macd_curr
):
    # Feature: realtime-monitor-app, Property 7: 买入信号生成当且仅当成立条件数达标
    vars = {
        "当前价": cur,
        "RSI": rsi,
        "KDJ_J": kdj_j,
        "布林下轨": boll_lower,
        # evaluate_signals 优先读取这两个派生键作为跨周期 MACD 柱值。
        "macd_hist_prev": macd_prev,
        "macd_hist_curr": macd_curr,
    }

    signals = RuleEngine().evaluate_signals(vars)
    buy_signals = [s for s in signals if isinstance(s, Signal) and s.kind == "买入"]
    buy_generated = len(buy_signals) > 0

    participating, established = _expected_participating_and_established(
        cur, rsi, kdj_j, boll_lower, macd_prev, macd_curr
    )
    expected = participating >= 2 and established >= 2

    # 当且仅当：可参与条件数 ≥ 2 且 成立数 ≥ 2 时生成买入信号。
    assert buy_generated == expected, (
        f"可参与数={participating}, 成立数={established}, 期望生成={expected}, "
        f"实际生成={buy_generated}"
    )

    # 生成时至多一个买入信号，且成立理由数应与独立重算的成立数一致。
    if buy_generated:
        assert len(buy_signals) == 1
        assert len(buy_signals[0].reasons) == established
