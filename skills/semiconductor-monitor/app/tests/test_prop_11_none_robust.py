# -*- coding: utf-8 -*-
"""Property 11：缺失值稳健性（None 不影响其余判断）（Validates: Requirements 5.5, 5.6）。

需求 5.5/5.6 要求：任何比较在取值前先判空；某条件依赖的任一取值为 None 时，该条件既不
计入「成立数」也不计入「可参与数」——即从该类信号的「可参与集合」中被恰好移除，且不得
污染其余条件的判断。

本测试以「差分不变性」表达该稳健性：先构造一份各字段均非 None 的完整变量字典，调用
``evaluate_signals`` 取基准结果；再随机选一个字段置为 None，重新调用并与基准对比：

  1. 不依赖被置 None 字段的信号种类，其生成与否、触发价、关联价、成立理由必须逐一保持一致
     （置 None 不污染无关判断）。
  2. 置 None 后不会凭空产生新的信号种类，也不会产生新的成立理由——差分只可能「移除」依赖
     该字段的条件（成立理由集合只会收缩为子集）。

设计依据：``rule_engine`` 中每条成立理由（reason）字符串只嵌入其自身依赖字段的取值，因此
将某字段置 None 时，仅依赖该字段的条件被移除，其余条件的判断结果与理由文本逐字不变，从而
上述不变性对任意字段、任意取值组合恒成立。
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

# 从技能根运行 pytest，`app` 可作为顶层包被导入。
from app.rule_engine import RuleEngine

# evaluate_signals 会读取的全部输入字段（均可能影响某类信号的某个条件或触发/关联价）。
_FIELDS = [
    "当前价",
    "RSI",
    "KDJ_J",
    "布林下轨",
    "布林上轨",
    "加权成本",
    "止损位",
    "今日成交量",
    "20日均量",
    "昨收价",
    "macd_hist_prev",
    "macd_hist_curr",
]

# 字段 -> 会被该字段影响的信号种类集合（严格对齐 rule_engine 各 _eval_* 的依赖关系）。
# 依赖既包含「参与某条件判断」，也包含「作为触发价/关联价被写入信号」。
#   - 当前价：买入(布林下轨条件+触发价)、卖出(布林上轨/回本距离+触发价)、止损、放量下跌止损 全部依赖
#   - RSI / KDJ_J / MACD 前后柱值：买入、卖出 两类条件集合
#   - 布林下轨：仅买入；布林上轨：仅卖出；加权成本：仅卖出(回本距离)
#   - 止损位：止损(触发条件) 与 放量下跌止损(关联价 related_price)
#   - 今日成交量 / 20日均量 / 昨收价：仅放量下跌止损
_AFFECTED = {
    "当前价": {"买入", "卖出", "止损", "放量下跌止损"},
    "RSI": {"买入", "卖出"},
    "KDJ_J": {"买入", "卖出"},
    "布林下轨": {"买入"},
    "布林上轨": {"卖出"},
    "加权成本": {"卖出"},
    "止损位": {"止损", "放量下跌止损"},
    "今日成交量": {"放量下跌止损"},
    "20日均量": {"放量下跌止损"},
    "昨收价": {"放量下跌止损"},
    "macd_hist_prev": {"买入", "卖出"},
    "macd_hist_curr": {"买入", "卖出"},
}

# 有限浮点：排除 NaN/Inf，使各字段都是可参与数值比较的合法取值（完整、无缺失）。
_finite_float = st.floats(
    min_value=-300.0, max_value=300.0, allow_nan=False, allow_infinity=False
)


def _signal_map(signals):
    """把信号列表归约为 {信号种类: (触发价, 关联价, frozenset(成立理由))} 便于逐项对比。"""
    return {
        s.kind: (s.trigger_price, s.related_price, frozenset(s.reasons))
        for s in signals
    }


@settings(max_examples=100)
@given(
    当前价=_finite_float,
    RSI=_finite_float,
    KDJ_J=_finite_float,
    布林下轨=_finite_float,
    布林上轨=_finite_float,
    加权成本=_finite_float,
    止损位=_finite_float,
    今日成交量=_finite_float,
    日均量20=_finite_float,
    昨收价=_finite_float,
    macd_hist_prev=_finite_float,
    macd_hist_curr=_finite_float,
    field_to_null=st.sampled_from(_FIELDS),
)
def test_property_11_none_robustness(
    当前价,
    RSI,
    KDJ_J,
    布林下轨,
    布林上轨,
    加权成本,
    止损位,
    今日成交量,
    日均量20,
    昨收价,
    macd_hist_prev,
    macd_hist_curr,
    field_to_null,
):
    # Feature: realtime-monitor-app, Property 11: 缺失值稳健性（None 不影响其余判断）
    # 构造一份各字段均非 None 的完整变量字典（基准输入）。
    full_vars = {
        "当前价": 当前价,
        "RSI": RSI,
        "KDJ_J": KDJ_J,
        "布林下轨": 布林下轨,
        "布林上轨": 布林上轨,
        "加权成本": 加权成本,
        "止损位": 止损位,
        "今日成交量": 今日成交量,
        "20日均量": 日均量20,
        "昨收价": 昨收价,
        "macd_hist_prev": macd_hist_prev,
        "macd_hist_curr": macd_hist_curr,
    }

    engine = RuleEngine()
    full_map = _signal_map(engine.evaluate_signals(full_vars))

    # 仅把选中的一个字段置为 None，其余保持不变。
    mutated_vars = dict(full_vars)
    mutated_vars[field_to_null] = None
    mut_map = _signal_map(engine.evaluate_signals(mutated_vars))

    affected = _AFFECTED[field_to_null]

    # 断言 1：不依赖被置 None 字段的信号种类，其生成与否及全部内容逐项保持一致。
    all_kinds = {"买入", "卖出", "止损", "放量下跌止损"}
    for kind in all_kinds - affected:
        assert full_map.get(kind) == mut_map.get(kind), (
            f"将「{field_to_null}」置 None 后，不相关信号「{kind}」发生了变化："
            f"before={full_map.get(kind)} after={mut_map.get(kind)}"
        )

    # 断言 2：置 None 只会移除条件，不会凭空产生新的信号种类。
    assert set(mut_map.keys()) <= set(full_map.keys()), (
        f"将「{field_to_null}」置 None 后凭空出现了新信号种类："
        f"{set(mut_map.keys()) - set(full_map.keys())}"
    )

    # 断言 3：对每一种信号，置 None 后的成立理由集合只会收缩为基准的子集
    # （被移除的恰是依赖该字段的条件；其余条件的判断结果与理由文本逐字不变）。
    for kind, (_, _, mut_reasons) in mut_map.items():
        full_reasons = full_map.get(kind, (None, None, frozenset()))[2]
        assert mut_reasons <= full_reasons, (
            f"将「{field_to_null}」置 None 后，信号「{kind}」出现了非预期的新成立理由："
            f"{mut_reasons - full_reasons}"
        )
