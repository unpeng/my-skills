# -*- coding: utf-8 -*-
"""Property 5：做 T 可买份数计算正确（Validates: Requirements 4.6）。

做 T 可买份数的口径为::

    做T可用资金上限 = 可用资金 × 0.80
    做T可买份数     = 向下取整(做T可用资金上限 ÷ 做T买入位)

对应 ``qstock/strategy/monitor.py`` 中 ``compute_monitor_variables`` 的实现::

    t_cash_available = cash * 80 / 100
    result["做T可用资金上限"] = round(t_cash_available, 2)
    result["做T可买份数"]     = int(t_cash_available // t_buy_price)

Rule_Engine 盘中建议不重新计算份数，而是**直接复用** ``vars['做T可买份数']``（需求 4.6）。
本测试不触网，直接对上述纯公式与"盘中建议复用 vars 份数"这一契约做属性验证。
"""

from __future__ import annotations

import math

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# 从技能根运行 pytest，`app` 可作为顶层包被导入。
from app.models import TradingSession
from app.rule_engine import RuleEngine

# 做 T 单次最多使用可用资金的比例（默认 80%），与 monitor.py 的 t_cash_cap_pct 默认值一致。
_T_CASH_CAP_PCT = 80.0


def _t_cash_available(cash: float) -> float:
    """复刻实现口径：做T可用资金上限 = 可用资金 × 0.80。"""
    return cash * _T_CASH_CAP_PCT / 100.0


def _t_shares(cash: float, t_buy: float) -> int:
    """复刻实现口径：做T可买份数 = int(做T可用资金上限 // 做T买入位) = 向下取整。"""
    return int(_t_cash_available(cash) // t_buy)


@settings(max_examples=100)
@given(
    cash=st.floats(min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    price=st.floats(min_value=0.5, max_value=1000.0, allow_nan=False, allow_infinity=False),
    prev_close=st.floats(
        min_value=0.5, max_value=1000.0, allow_nan=False, allow_infinity=False
    ),
)
def test_property_05_intraday_reuses_vars_t_shares(cash, price, prev_close):
    # Feature: realtime-monitor-app, Property 5: 做 T 可买份数计算正确
    # 契约：盘中建议展示的可买份数与 vars['做T可买份数'] 完全一致（需求 4.6），
    # 即 Rule_Engine 不自行重算，而是复用 compute_monitor_variables 已算好的字段。
    t_buy = round(price * 0.98, 3)
    assume(t_buy > 0)
    t_sell = round(price * 1.02, 3)

    upper = _t_cash_available(cash)
    shares = _t_shares(cash, t_buy)

    variables = {
        "当前价": price,
        "做T买入位": t_buy,
        "做T卖出位": t_sell,
        "昨收+2%": round(prev_close * 1.02, 3),
        "昨收-2%": round(prev_close * 0.98, 3),
        "做T可用资金上限": round(upper, 2),
        "做T可买份数": shares,
    }

    advice = RuleEngine().session_advice(TradingSession.INTRADAY, variables)

    # 关键价位齐备，应正常产出盘中建议（非情景分类，scenario 为 None）。
    assert advice.data_available is True
    assert advice.scenario is None
    # 展示的份数必须等于 vars 中的份数（原样复用，不重算）。
    assert f"约 {shares} 份" in advice.advice_text


@settings(max_examples=100)
@given(
    cash=st.floats(min_value=0.0, max_value=1e7, allow_nan=False, allow_infinity=False),
    t_buy=st.floats(min_value=0.01, max_value=1e4, allow_nan=False, allow_infinity=False),
)
def test_property_05_t_shares_is_floor_of_formula(cash, t_buy):
    # Feature: realtime-monitor-app, Property 5: 做 T 可买份数计算正确
    # 纯公式验证：做T可买份数 == 向下取整(可用资金 × 0.80 / 做T买入位)。
    upper = _t_cash_available(cash)          # 做T可用资金上限 = 可用资金 × 0.80
    shares = _t_shares(cash, t_buy)          # 做T可买份数 = 向下取整(上限 / 做T买入位)

    # 份数恒为非负整数（可用资金非负、买入位为正）。
    assert isinstance(shares, int)
    assert shares >= 0

    # floor 语义（等价于 shares == floor(upper / t_buy)）：
    # 份数 × 买入位不超过可用资金上限；再加一份即会超出上限。
    # 用与量级成比例的极小容差吸收浮点表示误差，保证判定稳定。
    eps = 1e-6 * max(1.0, upper)
    assert shares * t_buy <= upper + eps
    assert (shares + 1) * t_buy > upper - eps
