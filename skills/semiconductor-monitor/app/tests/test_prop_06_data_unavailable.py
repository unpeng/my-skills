# -*- coding: utf-8 -*-
"""Property 6：数据不足时降级不产出情景建议（Validates: Requirements 4.7）。

对任意"当前时段所需关键价位中至少有一个为 None"的输入，``RuleEngine.session_advice``
应返回 ``data_available=False`` 且不产出该时段的具体情景/分支建议（``scenario`` 为 None）。

本测试覆盖集合竞价/开盘/尾盘/盘中四个时段：为每个时段先构造一份完整的关键价位输入，
再随机选择其中某个必需关键价位置为 None，验证降级行为。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

# 从技能根运行 pytest，`app` 可作为顶层包被导入。
from app.models import TradingSession
from app.rule_engine import RuleEngine

# 各时段所需的必需关键价位键列表，严格对齐 rule_engine.py 各分支的 ``required`` 定义。
_REQUIRED_KEYS = {
    TradingSession.CALL_AUCTION: [
        "当前价", "成本+2%", "加权成本", "昨收+2%", "昨收-2%", "昨收-2.5%", "昨收-4%",
    ],
    TradingSession.OPENING: [
        "当前价", "加权成本", "昨收+2%", "昨收-2%", "昨收-2.5%", "昨收-4%",
    ],
    TradingSession.CLOSING: [
        "当前价", "加权成本", "止损位",
    ],
    TradingSession.INTRADAY: [
        "当前价", "做T买入位", "做T卖出位", "昨收+2%", "昨收-2%",
    ],
}


def _full_vars(session: TradingSession, base: float) -> dict:
    """基于一个基准价构造该时段所需关键价位齐备（均非 None）的变量字典。

    具体数值不影响本属性——本属性只关心"某个必需价位为 None 时是否降级"，
    因此这里给每个必需键都填入非 None 的合法数值即可。
    """
    return {key: base + i for i, key in enumerate(_REQUIRED_KEYS[session])}


@settings(max_examples=100)
@given(
    session=st.sampled_from(list(_REQUIRED_KEYS.keys())),
    base=st.floats(min_value=0.1, max_value=1000.0),
    missing_index=st.integers(min_value=0, max_value=6),
    use_pop=st.booleans(),
)
def test_property_06_data_unavailable_degrades(session, base, missing_index, use_pop):
    # Feature: realtime-monitor-app, Property 6: 数据不足时降级不产出情景建议
    required = _REQUIRED_KEYS[session]
    vars_dict = _full_vars(session, base)

    # 随机选择一个必需关键价位使其"为 None"：既覆盖显式置 None，也覆盖键缺失两种情况。
    target_key = required[missing_index % len(required)]
    if use_pop:
        vars_dict.pop(target_key)      # 键缺失（vars.get 返回 None）
    else:
        vars_dict[target_key] = None   # 显式置 None

    advice = RuleEngine().session_advice(session, vars_dict)

    # 数据不足时应降级：标记数据不可用，且不产出该时段的具体情景/分支建议。
    assert advice.data_available is False
    assert advice.scenario is None
    # 时段回填正确，便于上层展示"数据不可用"提示。
    assert advice.session is session
