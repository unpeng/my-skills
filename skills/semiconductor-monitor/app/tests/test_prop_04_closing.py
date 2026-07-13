# -*- coding: utf-8 -*-
"""Property 4 属性测试：尾盘决策树分支互斥且穷尽（Validates: Requirements 4.3）。

对任意当前价、加权成本与止损位组合，``RuleEngine`` 在尾盘时段（14:00:00–14:59:59）应把
当前状态匹配到尾盘决策树的五个分支中且仅一个（匹配计数恒为 1，``scenario`` 唯一非空）。

尾盘决策树内部还用到 成本×0.98、成本×0.96 作为中间阈值。本测试用 Hypothesis 生成随机的
当前价、加权成本与止损位（含相等、临界、跨越边界，且允许三者任意相对大小关系），独立按
"互斥且穷尽的降序区间"语义计算应命中的分支，再断言引擎返回的分支恰好等于该唯一分支。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models import TradingSession
from app.rule_engine import RuleEngine

# 尾盘决策树的分支标签，按降序阈值顺序排列（与实现 _closing_advice 的级联顺序一致）：
# 加权成本 > 成本×0.98 > 成本×0.96 > 止损位 > 兜底(大亏)。
_CLOSING_SCENARIOS = [
    "尾盘-盈利",
    "尾盘-小赚",
    "尾盘-持平",
    "尾盘-小亏",
    "尾盘-大亏",
]

# 价格/关键价位生成策略：有限、非 NaN、正数；四舍五入到 2 位小数，
# 以提高命中"相等/临界边界"的概率，增强对区间端点的覆盖。
_price = st.floats(
    min_value=0.01,
    max_value=100.0,
    allow_nan=False,
    allow_infinity=False,
).map(lambda x: round(x, 2))


def _matched_scenarios(price, cost, stop):
    """独立于实现，按尾盘决策树的互斥区间定义计算所有命中的分支。

    级联阈值顺序（严格按实现的列表顺序，而非数值排序）：
    加权成本 > 成本×0.98 > 成本×0.96 > 止损位 > 兜底。命中规则为首个满足
    ``price > 阈值`` 的分支；兜底分支无条件命中。由该级联结构保证互斥且穷尽——
    对任意 ``price`` 与任意 ``cost``/``stop`` 组合，恰好命中一个分支。返回命中分支列表
    （正确时长度恒为 1），用于差分校验引擎输出。
    """
    matched = []
    # 尾盘-盈利：当前价高于加权成本。
    if price > cost:
        matched.append("尾盘-盈利")
    # 尾盘-小赚：不高于加权成本，但高于成本×0.98。
    if price <= cost and price > cost * 0.98:
        matched.append("尾盘-小赚")
    # 尾盘-持平：不高于成本×0.98，但高于成本×0.96。
    if price <= cost and price <= cost * 0.98 and price > cost * 0.96:
        matched.append("尾盘-持平")
    # 尾盘-小亏：不高于成本×0.96，但高于止损位。
    if (
        price <= cost
        and price <= cost * 0.98
        and price <= cost * 0.96
        and price > stop
    ):
        matched.append("尾盘-小亏")
    # 尾盘-大亏：不高于止损位（兜底）。
    if (
        price <= cost
        and price <= cost * 0.98
        and price <= cost * 0.96
        and price <= stop
    ):
        matched.append("尾盘-大亏")
    return matched


@settings(max_examples=100)
@given(
    price=_price,
    cost=_price,
    stop=_price,
)
def test_closing_branches_mutually_exclusive_and_exhaustive(price, cost, stop):
    # Feature: realtime-monitor-app, Property 4: 尾盘决策树分支互斥且穷尽
    variables = {
        "当前价": price,
        "加权成本": cost,
        "止损位": stop,
    }

    advice = RuleEngine().session_advice(TradingSession.CLOSING, variables)

    # 关键价位齐全时应产出尾盘时段的决策建议。
    assert advice.session is TradingSession.CLOSING
    assert advice.data_available is True

    # 独立按互斥且穷尽的区间定义计算命中分支——应恒为恰好一个（匹配计数为 1）。
    matched = _matched_scenarios(price, cost, stop)
    assert len(matched) == 1, f"尾盘决策树分支匹配计数应为 1，实际命中 {matched}"

    # 引擎返回的 scenario 必须唯一非空、属于五个尾盘分支，且等于独立计算出的那个分支。
    assert advice.scenario is not None
    assert advice.scenario in _CLOSING_SCENARIOS
    assert advice.scenario == matched[0]
