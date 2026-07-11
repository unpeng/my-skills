# -*- coding: utf-8 -*-
"""
Technical indicators module - computes MA, MACD, RSI, KDJ, Bollinger Bands.
All implementations use pure pandas/numpy for portability.
"""

import numpy as np
import pandas as pd

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    MA_PERIODS, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    RSI_PERIOD, KDJ_PERIOD, BOLL_PERIOD, BOLL_STD,
)


def compute_ma(df: pd.DataFrame, periods: list = None) -> pd.DataFrame:
    """
    Compute Simple Moving Averages for given periods.

    Args:
        df: DataFrame with 'close' column
        periods: List of MA periods

    Returns:
        DataFrame with MA columns added (ma5, ma10, ...).
    """
    if periods is None:
        periods = MA_PERIODS

    df = df.copy()
    for p in periods:
        df[f"ma{p}"] = df["close"].rolling(p).mean()

    # MA slope (trend direction)
    for p in [20, 60]:
        col = f"ma{p}"
        if col in df.columns:
            df[f"{col}_slope"] = df[col].pct_change(5) * 100

    return df


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def compute_macd(df: pd.DataFrame, fast: int = None, slow: int = None,
                 signal: int = None) -> pd.DataFrame:
    """
    Compute MACD (Moving Average Convergence Divergence).

    Args:
        df: DataFrame with 'close' column
        fast: Fast EMA period
        slow: Slow EMA period
        signal: Signal line period

    Returns:
        DataFrame with macd_dif, macd_dea, macd_hist columns added.
    """
    if fast is None:
        fast = MACD_FAST
    if slow is None:
        slow = MACD_SLOW
    if signal is None:
        signal = MACD_SIGNAL

    df = df.copy()

    ema_fast = compute_ema(df["close"], fast)
    ema_slow = compute_ema(df["close"], slow)

    df["macd_dif"] = ema_fast - ema_slow
    df["macd_dea"] = compute_ema(df["macd_dif"], signal)
    df["macd_hist"] = (df["macd_dif"] - df["macd_dea"]) * 2

    # MACD cross signals
    df["macd_golden"] = (
        (df["macd_dif"] > df["macd_dea"]) &
        (df["macd_dif"].shift(1) <= df["macd_dea"].shift(1))
    ).astype(int)

    df["macd_death"] = (
        (df["macd_dif"] < df["macd_dea"]) &
        (df["macd_dif"].shift(1) >= df["macd_dea"].shift(1))
    ).astype(int)

    return df


def compute_rsi(df: pd.DataFrame, period: int = None) -> pd.DataFrame:
    """
    Compute RSI (Relative Strength Index).

    Args:
        df: DataFrame with 'close' column
        period: RSI period

    Returns:
        DataFrame with rsi column added.
    """
    if period is None:
        period = RSI_PERIOD

    df = df.copy()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    df["rsi"] = 100 - (100 / (1 + rs))

    # RSI zones
    df["rsi_overbought"] = (df["rsi"] > 70).astype(int)
    df["rsi_oversold"] = (df["rsi"] < 30).astype(int)

    return df


def compute_kdj(df: pd.DataFrame, period: int = None) -> pd.DataFrame:
    """
    Compute KDJ (Stochastic Oscillator).

    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: KDJ period

    Returns:
        DataFrame with kdj_k, kdj_d, kdj_j columns added.
    """
    if period is None:
        period = KDJ_PERIOD

    df = df.copy()

    low_min = df["low"].rolling(period).min()
    high_max = df["high"].rolling(period).max()

    rsv = (df["close"] - low_min) / (high_max - low_min + 1e-10) * 100

    # Fill NaN RSV with 50 (neutral) to prevent cascading NaN in loop
    rsv = rsv.fillna(50)

    # K = 2/3 * prev_K + 1/3 * RSV
    k = pd.Series(index=df.index, dtype=float)
    d = pd.Series(index=df.index, dtype=float)

    k.iloc[0] = 50
    d.iloc[0] = 50

    for i in range(1, len(df)):
        k.iloc[i] = 2 / 3 * k.iloc[i - 1] + 1 / 3 * rsv.iloc[i]
        d.iloc[i] = 2 / 3 * d.iloc[i - 1] + 1 / 3 * k.iloc[i]

    df["kdj_k"] = k
    df["kdj_d"] = d
    df["kdj_j"] = 3 * k - 2 * d

    # KDJ cross signals
    df["kdj_golden"] = (
        (df["kdj_k"] > df["kdj_d"]) &
        (df["kdj_k"].shift(1) <= df["kdj_d"].shift(1))
    ).astype(int)

    df["kdj_death"] = (
        (df["kdj_k"] < df["kdj_d"]) &
        (df["kdj_k"].shift(1) >= df["kdj_d"].shift(1))
    ).astype(int)

    return df


def compute_bollinger(df: pd.DataFrame, period: int = None,
                      std_dev: float = None) -> pd.DataFrame:
    """
    Compute Bollinger Bands.

    Args:
        df: DataFrame with 'close' column
        period: Moving average period
        std_dev: Standard deviation multiplier

    Returns:
        DataFrame with boll_upper, boll_mid, boll_lower columns added.
    """
    if period is None:
        period = BOLL_PERIOD
    if std_dev is None:
        std_dev = BOLL_STD

    df = df.copy()

    df["boll_mid"] = df["close"].rolling(period).mean()
    rolling_std = df["close"].rolling(period).std()

    df["boll_upper"] = df["boll_mid"] + std_dev * rolling_std
    df["boll_lower"] = df["boll_mid"] - std_dev * rolling_std

    # Bandwidth (volatility indicator)
    df["boll_width"] = (df["boll_upper"] - df["boll_lower"]) / df["boll_mid"] * 100

    # Position within bands (0 = lower, 1 = upper)
    df["boll_pct"] = (df["close"] - df["boll_lower"]) / (
        df["boll_upper"] - df["boll_lower"] + 1e-10
    )

    return df


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all technical indicators.

    Args:
        df: K-line DataFrame with OHLCV columns

    Returns:
        DataFrame with all technical indicator columns added.
    """
    df = compute_ma(df)
    df = compute_macd(df)
    df = compute_rsi(df)
    df = compute_kdj(df)
    df = compute_bollinger(df)
    return df
