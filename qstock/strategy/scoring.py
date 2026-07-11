# -*- coding: utf-8 -*-
"""
Multi-factor scoring system - combines technical, fundamental, and ML signals.
"""

import pandas as pd
from model.technical import compute_all_indicators
from data.processor import clean_kline, compute_returns, compute_volatility, \
    compute_volume_features


def _score_technical(row: pd.Series) -> float:
    """
    Compute technical signal score from indicator values.

    Scoring rules:
    - RSI oversold (<30): +2, overbought (>70): -2
    - MACD golden cross: +2, death cross: -2
    - KDJ golden cross: +1, death cross: -1
    - Price above MA20: +1, below: -1
    - Price above MA60: +1, below: -1
    - Bollinger band position: closer to lower band = higher score (mean reversion)

    Returns: score in range [-10, 10]
    """
    score = 0.0

    # RSI
    rsi = row.get("rsi", 50)
    if rsi < 30:
        score += 2
    elif rsi > 70:
        score -= 2
    elif rsi < 40:
        score += 1
    elif rsi > 60:
        score -= 1

    # MACD
    if row.get("macd_golden", 0):
        score += 2
    elif row.get("macd_death", 0):
        score -= 2
    elif row.get("macd_hist", 0) > 0:
        score += 0.5
    else:
        score -= 0.5

    # KDJ
    if row.get("kdj_golden", 0):
        score += 1
    elif row.get("kdj_death", 0):
        score -= 1

    # MA trend
    close = row.get("close", 0)
    ma20 = row.get("ma20", close)
    ma60 = row.get("ma60", close)

    if close > ma20:
        score += 1
    else:
        score -= 1

    if close > ma60:
        score += 1
    else:
        score -= 1

    # Bollinger band position (mean reversion)
    boll_pct = row.get("boll_pct", 0.5)
    if boll_pct < 0.2:
        score += 1  # Near lower band, potential bounce
    elif boll_pct > 0.8:
        score -= 1  # Near upper band, potential pullback

    return max(min(score, 10), -10)


def multi_factor_score(kline_df: pd.DataFrame, fundamental_score: float = 0) -> dict:
    """
    Compute a composite multi-factor score for a stock.

    Args:
        kline_df: Raw K-line DataFrame
        fundamental_score: Fundamental score (0-50)

    Returns:
        Dict with individual factor scores and composite score.
    """
    df = clean_kline(kline_df)
    if df.empty or len(df) < 60:
        return {"error": "数据不足"}

    df = compute_returns(df)
    df = compute_volatility(df)
    df = compute_volume_features(df)
    df = compute_all_indicators(df)

    # Only drop rows where key indicator columns are NaN
    key_cols = ["close", "rsi", "macd_hist", "ma20", "boll_pct"]
    available = [c for c in key_cols if c in df.columns]
    df = df.dropna(subset=available)

    if df.empty:
        return {"error": "计算指标后数据为空"}

    latest = df.iloc[-1]

    # Technical score
    tech_score = _score_technical(latest)

    # Momentum score (based on recent returns)
    ret_5d = latest.get("ret_5d", 0)
    ret_20d = latest.get("ret_20d", 0)

    momentum_score = 0
    if ret_5d > 3:
        momentum_score += 2
    elif ret_5d > 0:
        momentum_score += 1
    elif ret_5d < -3:
        momentum_score -= 2
    else:
        momentum_score -= 1

    if ret_20d > 10:
        momentum_score += 2
    elif ret_20d > 0:
        momentum_score += 1
    elif ret_20d < -10:
        momentum_score -= 2
    else:
        momentum_score -= 1

    # Volume score
    vol_ratio = latest.get("vol_ratio", 1)
    vol_score = 0
    if vol_ratio > 2:
        vol_score += 1  # Active trading
    elif vol_ratio < 0.5:
        vol_score -= 1  # Low activity

    # Normalize fundamental score to [-10, 10] range
    fund_normalized = (fundamental_score - 25) / 2.5  # 0-50 → -10 to 10

    # Composite score (weighted)
    composite = (
        tech_score * 0.35 +
        momentum_score * 0.25 +
        vol_score * 0.10 +
        fund_normalized * 0.30
    )

    return {
        "technical_score": round(tech_score, 2),
        "momentum_score": round(momentum_score, 2),
        "volume_score": round(vol_score, 2),
        "fundamental_score": round(fund_normalized, 2),
        "composite_score": round(composite, 2),
        "close": round(latest["close"], 2),
        "ret_5d": round(ret_5d, 2),
        "ret_20d": round(ret_20d, 2),
        "rsi": round(latest.get("rsi", 0), 2),
    }
