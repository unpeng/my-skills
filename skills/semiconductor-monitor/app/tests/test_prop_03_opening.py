# -*- coding: utf-8 -*-
"""Property 3 属性测试：开盘情景互斥且穷尽（Validates: Requirements 4.2）。

对任意开盘价与关键价位组合，``RuleEngine`` 在开盘时段（09:30:00–09:59:59）应把
当前状态匹配到开盘情景 A–F 中且仅一个（匹配计数恒为 1，``scenario`` 唯一非空）。

本测试用 Hypothesis 生成随机的开盘价与各关键价位（含相等、临界、跨越边界，且允许各
关键价位之间任意相对大小关系），独立按"互斥且穷尽的降序区间"语义计算应命中的情景，
再断言引擎返回的情景恰好等于该唯一情景。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models import TradingSession
from app.rule_engine import RuleEngine

# 开盘决策树的情景标签，按降序阈值顺序排列：
# 加权成本 > 昨收+2% > 昨收-2% > 昨收-2.5% > 昨收-4% > 兜底(F)。
_OPENING_SCENARIOS = [
    "开盘情景A",
    "开盘情景B",
    "开盘情景C",
    "开盘情景D",
    "开盘情景E",
    "开盘情景F",
]

# 价格/关键价位生成策略：有限、非 NaN；四舍五入到 2 位小数，
# 以提高命中"相等/临界边界"的概率，增强对区间端点的覆盖。
_price = st.floats(
    min_value=0.01,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
).map(lambda x: round(x, 2))


def _matched_scenarios(price, thresholds):
    """独立于实现，按开盘决策树的互斥区间定义计算所有命中的情景。

    降序阈值顺序：加权成本 > 昨收+2% > 昨收-2% > 昨收-2.5% > 昨收-4% > 兜底。
    每个情景定义为"高于当前阈值且不高于全部更高阈值"的半开区间，由构造保证互斥且
    穷尽——对任意 ``price`` 与任意阈值组合，恰好命中一个情景。返回命中情景列表
    （正确时长度恒为 1），用于差分校验引擎输出。
    """
    cost, up2, dn2, dn25, dn4 = thresholds
    matched = []
    # A：开盘价高于加权成本。
    if price > cost:
        matched.append("开盘情景A")
    # B：不高于加权成本，但高于昨收+2%。
    if price <= cost and price > up2:
        matched.append("开盘情景B")
    # C：不高于昨收+2%，但高于昨收-2%。
    if price <= cost and price <= up2 and price > dn2:
        matched.append("开盘情景C")
    # D：不高于昨收-2%，但高于昨收-2.5%。
    if price <= cost and price <= up2 and price <= dn2 and price > dn25:
        matched.append("开盘情景D")
    # E：不高于昨收-2.5%，但高于昨收-4%。
    if (
        price <= cost
        and price <= up2
        and price <= dn2
        and price <= dn25
        and price > dn4
    ):
        matched.append("开盘情景E")
    # F：不高于昨收-4%（兜底）。
    if (
        price <= cost
        and price <= up2
        and price <= dn2
        and price <= dn25
        and price <= dn4
    ):
        matched.append("开盘情景F")
    return matched


@settings(max_examples=100)
@given(
    price=_price,
    cost=_price,
    up2=_price,
    dn2=_price,
    dn25=_price,
    dn4=_price,
)
def test_opening_scenarios_mutually_exclusive_and_exhaustive(
    price, cost, up2, dn2, dn25, dn4
):
    # Feature: realtime-monitor-app, Property 3: 开盘情景互斥且穷尽
    variables = {
        "当前价": price,
        "加权成本": cost,
        "昨收+2%": up2,
        "昨收-2%": dn2,
        "昨收-2.5%": dn25,
        "昨收-4%": dn4,
    }

    advice = RuleEngine().session_advice(TradingSession.OPENING, variables)

    # 关键价位齐全时应产出开盘时段的情景建议。
    assert advice.session is TradingSession.OPENING
    assert advice.data_available is True

    # 独立按互斥且穷尽的区间定义计算命中情景——应恒为恰好一个（匹配计数为 1）。
    matched = _matched_scenarios(price, (cost, up2, dn2, dn25, dn4))
    assert len(matched) == 1, f"开盘情景匹配计数应为 1，实际命中 {matched}"

    # 引擎返回的 scenario 必须唯一非空、属于 A–F，且等于独立计算出的那个情景。
    assert advice.scenario is not None
    assert advice.scenario in _OPENING_SCENARIOS
    assert advice.scenario == matched[0]
