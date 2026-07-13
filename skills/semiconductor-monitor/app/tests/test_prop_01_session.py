# -*- coding: utf-8 -*-
"""Property 1：交易时段判定互斥且穷尽（Validates: Requirements 4.5）。

对任意一天中的时间点与交易日标志，``classify_session`` 应把它归入五类交易时段
（集合竞价/开盘/盘中/尾盘/非交易时段）中且仅一类；且当归入非交易时段时，
``RuleEngine.session_advice`` 不产出任何盘中决策树建议（scenario 为 None）。
"""

from __future__ import annotations

from datetime import date, datetime, time

from hypothesis import given, settings
from hypothesis import strategies as st

# 从技能根运行 pytest，`app` 可作为顶层包被导入。
from app.models import TradingSession
from app.rule_engine import RuleEngine, classify_session


def _expected_trading_sessions(t: time):
    """独立于被测实现，按设计的半开区间 [start, end) 推导 ``t`` 命中的交易时段集合。

    这里仅列出四类"交易中"时段；非交易时段是它们的补集。返回命中的时段列表，
    用于校验互斥（列表长度 ≤ 1）与穷尽（据此推导期望结果并与实现比对）。
    """
    matched = []
    # 集合竞价 09:15:00–09:25:59  ->  [09:15:00, 09:26:00)
    if time(9, 15, 0) <= t < time(9, 26, 0):
        matched.append(TradingSession.CALL_AUCTION)
    # 开盘 09:30:00–09:59:59  ->  [09:30:00, 10:00:00)
    if time(9, 30, 0) <= t < time(10, 0, 0):
        matched.append(TradingSession.OPENING)
    # 盘中 10:00:00–11:29:59 或 13:00:00–13:59:59
    if (time(10, 0, 0) <= t < time(11, 30, 0)) or (time(13, 0, 0) <= t < time(14, 0, 0)):
        matched.append(TradingSession.INTRADAY)
    # 尾盘 14:00:00–14:59:59  ->  [14:00:00, 15:00:00)
    if time(14, 0, 0) <= t < time(15, 0, 0):
        matched.append(TradingSession.CLOSING)
    return matched


@settings(max_examples=100)
@given(
    now=st.datetimes(
        min_value=datetime(2024, 1, 1, 0, 0, 0),
        max_value=datetime(2024, 1, 1, 23, 59, 59),
    ),
    is_trading_day=st.booleans(),
)
def test_property_01_session_mutually_exclusive_and_exhaustive(now, is_trading_day):
    # Feature: realtime-monitor-app, Property 1: 交易时段判定互斥且穷尽
    result = classify_session(now, is_trading_day)

    # 穷尽：结果始终是五类合法枚举之一。
    assert result in set(TradingSession)

    if not is_trading_day:
        # 非交易日全天归非交易时段（需求 4.5）。
        assert result is TradingSession.NON_TRADING
    else:
        matched = _expected_trading_sessions(now.time())
        # 互斥：任一时刻至多命中一个"交易中"时段（不重叠）。
        assert len(matched) <= 1
        # 穷尽 + 一致：命中则归该时段，否则归非交易时段（无空档）。
        expected = matched[0] if matched else TradingSession.NON_TRADING
        assert result is expected

    # 归入非交易时段时不产出盘中决策树建议（scenario 为 None）。
    if result is TradingSession.NON_TRADING:
        advice = RuleEngine().session_advice(result, {})
        assert advice.scenario is None
