# -*- coding: utf-8 -*-
"""Property 2：集合竞价情景互斥且穷尽（Validates: Requirements 4.1）。

对任意竞价价格与关键价位组合（含相等、临界、乱序大小关系），``RuleEngine`` 在集合竞价
时段应将当前状态匹配到情景 1–7 中且仅一个情景（匹配计数恒为 1）。

本测试不复用被测实现的级联逻辑，而是**独立地**依据决策树的降序阈值语义重建情景归属，
再与 ``RuleEngine.session_advice`` 的输出比对：
  - 独立计算 7 个情景各自的命中布尔值，断言命中计数恒为 1（互斥且穷尽，是发现区间
    重叠或空档的关键手段）。
  - 断言引擎返回的 scenario 非空、落在情景 1–7 集合内，且与独立计算出的唯一情景一致。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.models import TradingSession
from app.rule_engine import RuleEngine


# 集合竞价决策树的降序阈值键（成本+2% > 加权成本 > 昨收+2% > 昨收-2% > 昨收-2.5% > 昨收-4%），
# 与情景标签一一对应；越过第 i 个阈值即归入 SCENARIOS[i]，全部未越过则归入兜底情景7。
_THRESHOLD_KEYS = ["成本+2%", "加权成本", "昨收+2%", "昨收-2%", "昨收-2.5%", "昨收-4%"]
_SCENARIOS = ["情景1", "情景2", "情景3", "情景4", "情景5", "情景6", "情景7"]


def _independent_matches(price: float, thresholds: list) -> list:
    """独立重建"降序阈值级联"情景归属，返回 7 个情景的命中布尔列表。

    情景 i（0..5）命中 <=> 价格越过第 i 个阈值（price > thresholds[i]）且未越过任何更靠前
    的阈值（对所有 j < i 有 price <= thresholds[j]）；情景7（兜底）命中 <=> 未越过任何阈值。
    该定义是一个良构划分：任一价格恰好落入一个情景，与阈值之间的大小关系（含乱序、相等）无关。
    """
    matches = []
    # 情景1–6：首个被越过的阈值决定归属。
    for i in range(len(thresholds)):
        higher_all_not_crossed = all(price <= thresholds[j] for j in range(i))
        matches.append(higher_all_not_crossed and price > thresholds[i])
    # 情景7：所有阈值均未越过（price <= 每个阈值）。
    matches.append(all(price <= t for t in thresholds))
    return matches


@st.composite
def _price_and_thresholds(draw):
    """生成竞价价与 6 个关键阈值。

    刻意从较小的离散网格取值以提高"相等 / 临界"命中概率，并允许阈值之间任意乱序，
    覆盖关键价位大小关系不满足降序假设的边界情况。
    """
    # 离散网格：步长 0.5，跨越负值到正值，制造大量相等与临界比较。
    grid = st.sampled_from([round(x * 0.5, 1) for x in range(-8, 21)])
    thresholds = [draw(grid) for _ in _THRESHOLD_KEYS]
    # 价格：多数时候直接取自同一网格（易与阈值相等/临界），偶尔精确等于某个阈值。
    price = draw(st.one_of(grid, st.sampled_from(thresholds)))
    return price, thresholds


@settings(max_examples=100)
@given(_price_and_thresholds())
def test_prop_02_call_auction_scenarios_partition(data):
    # Feature: realtime-monitor-app, Property 2: 集合竞价情景互斥且穷尽
    price, thresholds = data

    vars = {"当前价": price}
    for key, value in zip(_THRESHOLD_KEYS, thresholds):
        vars[key] = value

    engine = RuleEngine()
    advice = engine.session_advice(TradingSession.CALL_AUCTION, vars)

    # 独立计算的情景命中：互斥且穷尽 => 命中计数恒为 1。
    matches = _independent_matches(price, thresholds)
    assert sum(matches) == 1, (
        f"情景命中计数应恒为 1，实际为 {sum(matches)}；"
        f"price={price}, thresholds={thresholds}, matches={matches}"
    )
    expected_scenario = _SCENARIOS[matches.index(True)]

    # 引擎必须产出情景建议：非空、唯一、落在情景 1–7 集合内。
    assert advice.data_available is True
    assert advice.scenario is not None
    assert advice.scenario in _SCENARIOS

    # 引擎归属应与独立重建的唯一情景一致（既不重叠也无空档）。
    assert advice.scenario == expected_scenario, (
        f"引擎归属 {advice.scenario} 与独立计算 {expected_scenario} 不一致；"
        f"price={price}, thresholds={thresholds}"
    )
