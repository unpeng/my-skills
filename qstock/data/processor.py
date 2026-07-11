# -*- coding: utf-8 -*-
"""
Data processing module - cleans raw data, engineers features, generates labels.
"""

import numpy as np
import pandas as pd


def clean_kline(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean raw K-line data: drop NaN rows, ensure proper types.

    Args:
        df: Raw K-line DataFrame from fetcher.get_kline()

    Returns:
        Cleaned DataFrame.
    """
    if df.empty:
        return df

    df = df.copy()

    # Drop rows where close is NaN
    df = df.dropna(subset=["close"])

    # Ensure numeric types
    numeric_cols = ["open", "high", "low", "close", "volume", "turnover"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows with zero close price (invalid data)
    df = df[df["close"] > 0]

    return df


def compute_returns(df: pd.DataFrame, periods: list = None) -> pd.DataFrame:
    """
    Compute forward and backward returns for various periods.

    Args:
        df: K-line DataFrame with 'close' column
        periods: List of periods (in trading days)

    Returns:
        DataFrame with return columns added.
    """
    if periods is None:
        periods = [1, 3, 5, 10, 20]

    df = df.copy()

    # Historical returns
    for p in periods:
        df[f"ret_{p}d"] = df["close"].pct_change(p) * 100

    # Log returns
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))

    return df


def compute_volatility(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """
    Compute rolling volatility features.

    Args:
        df: K-line DataFrame
        window: Rolling window size

    Returns:
        DataFrame with volatility features added.
    """
    df = df.copy()

    # Historical volatility (annualized)
    df["volatility"] = df["log_ret"].rolling(window).std() * np.sqrt(252)

    # Average True Range (ATR)
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low - close_prev).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window).mean()

    # Price range ratio
    df["price_range"] = (df["high"] - df["low"]) / df["close"] * 100

    return df


def compute_volume_features(df: pd.DataFrame, periods: list = None) -> pd.DataFrame:
    """
    Compute volume-based features.

    Args:
        df: K-line DataFrame
        periods: Rolling periods for volume MA

    Returns:
        DataFrame with volume features added.
    """
    if periods is None:
        periods = [5, 10, 20]

    df = df.copy()

    # Volume moving averages
    for p in periods:
        df[f"vol_ma{p}"] = df["volume"].rolling(p).mean()

    # Volume ratio (current / MA20)
    if "vol_ma20" in df.columns:
        df["vol_ratio"] = df["volume"] / df["vol_ma20"]
    else:
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()

    # Volume change rate
    df["vol_change"] = df["volume"].pct_change() * 100

    # Amount per volume (average trade price proxy)
    df["avg_trade_price"] = df["turnover"] / (df["volume"] + 1e-8)

    return df


def generate_labels(df: pd.DataFrame, predict_days: int = 5) -> pd.DataFrame:
    """
    Generate prediction labels based on forward returns.

    Args:
        df: K-line DataFrame
        predict_days: Number of trading days to predict forward

    Returns:
        DataFrame with label columns:
        - forward_ret: N-day forward return (%)
        - label: 1 (up) if forward_ret > 0, else 0 (down)
    """
    df = df.copy()

    # Forward return
    df["forward_ret"] = (df["close"].shift(-predict_days) / df["close"] - 1) * 100

    # Binary label: 1 = up, 0 = down
    df["label"] = (df["forward_ret"] > 0).astype(int)

    return df


def prepare_features(df: pd.DataFrame, predict_days: int = 5) -> pd.DataFrame:
    """
    Full feature engineering pipeline.

    Args:
        df: Raw K-line DataFrame
        predict_days: Forward prediction period

    Returns:
        DataFrame with all features and labels, NaN rows dropped.
    """
    df = clean_kline(df)
    if df.empty:
        return df

    df = compute_returns(df)
    df = compute_volatility(df)
    df = compute_volume_features(df)
    df = generate_labels(df, predict_days)

    # Drop rows with NaN (from rolling calculations)
    df = df.dropna()

    return df


def train_test_split(df: pd.DataFrame, ratio: float = 0.8):
    """
    Time-series aware train/test split (no shuffling).

    Args:
        df: Feature DataFrame
        ratio: Train set ratio

    Returns:
        (train_df, test_df) tuple
    """
    split_idx = int(len(df) * ratio)
    train = df.iloc[:split_idx].copy()
    test = df.iloc[split_idx:].copy()
    return train, test
