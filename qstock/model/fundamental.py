# -*- coding: utf-8 -*-
"""
Fundamental scoring module - evaluates stocks based on financial metrics.
Adapted from tkfy920/qstock stock/stock_pool.py scoring system.
"""

import pandas as pd
from data.fetcher import get_financial


def _score_roe(roe: float) -> float:
    """Score ROE (净资产收益率). Higher is better."""
    try:
        if roe >= 15:
            return 10
        elif roe >= 10:
            return 8
        elif roe >= 5:
            return 5
        elif roe >= 0:
            return 2
        else:
            return 0
    except (TypeError, ValueError):
        return 0


def _score_pe(pe: float) -> float:
    """Score PE ratio (市盈率). Lower is better, but negative is bad."""
    try:
        if pe < 0:
            return 0
        elif pe < 10:
            return 10
        elif pe < 20:
            return 8
        elif pe < 40:
            return 5
        elif pe < 60:
            return 3
        else:
            return 1
    except (TypeError, ValueError):
        return 0


def _score_pb(pb: float) -> float:
    """Score PB ratio (市净率). Lower is better, but negative is bad."""
    try:
        if pb < 0:
            return 0
        elif pb < 1:
            return 10
        elif pb < 2:
            return 8
        elif pb < 3:
            return 5
        elif pb < 5:
            return 3
        else:
            return 1
    except (TypeError, ValueError):
        return 0


def _score_margin(margin: float) -> float:
    """Score gross margin (毛利率). Higher is better."""
    try:
        if margin >= 60:
            return 10
        elif margin >= 40:
            return 8
        elif margin >= 20:
            return 5
        elif margin >= 0:
            return 2
        else:
            return 0
    except (TypeError, ValueError):
        return 0


def _score_net_margin(margin: float) -> float:
    """Score net profit margin (净利率). Higher is better."""
    try:
        if margin >= 30:
            return 10
        elif margin >= 20:
            return 8
        elif margin >= 10:
            return 5
        elif margin >= 0:
            return 2
        else:
            return 0
    except (TypeError, ValueError):
        return 0


def compute_fundamental_score(financial_data: dict) -> dict:
    """
    Compute a composite fundamental score from financial metrics.

    Args:
        financial_data: Dict from get_financial() with keys like
                       '市盈率(动)', '市净率', 'ROE', '毛利率', '净利率'

    Returns:
        Dict with individual scores and total_score (0-50).
    """
    scores = {}

    pe = financial_data.get("市盈率(动)")
    pb = financial_data.get("市净率")
    roe = financial_data.get("ROE")
    gross_margin = financial_data.get("毛利率")
    net_margin = financial_data.get("净利率")

    scores["pe_score"] = _score_pe(pe)
    scores["pb_score"] = _score_pb(pb)
    scores["roe_score"] = _score_roe(roe)
    scores["margin_score"] = _score_margin(gross_margin)
    scores["net_margin_score"] = _score_net_margin(net_margin)

    scores["total_score"] = sum(scores.values())

    return scores


def score_stock(code: str) -> dict:
    """
    Fetch financials and compute fundamental score for a single stock.

    Args:
        code: Stock code

    Returns:
        Dict with financial metrics and scores.
    """
    financial = get_financial(code)
    if not financial:
        return {"code": code, "total_score": 0, "error": "无法获取财务数据"}

    scores = compute_fundamental_score(financial)
    return {
        "code": code,
        "name": financial.get("名称", ""),
        "industry": financial.get("所处行业", ""),
        "pe": financial.get("市盈率(动)"),
        "pb": financial.get("市净率"),
        "roe": financial.get("ROE"),
        "gross_margin": financial.get("毛利率"),
        "net_margin": financial.get("净利率"),
        **scores,
    }
