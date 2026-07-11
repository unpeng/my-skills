# -*- coding: utf-8 -*-
"""
Visualization module - K-line charts, prediction signals, backtest equity curves.
Uses matplotlib for rendering.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

# Chinese font support
plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def plot_kline(df: pd.DataFrame, code: str = "", name: str = "",
               last_n: int = 120, save_path: str = None) -> str:
    """
    Plot K-line chart with MA lines, volume, MACD, and RSI.

    Args:
        df: K-line DataFrame (must have OHLCV + technical indicators)
        code: Stock code for title
        name: Stock name for title
        last_n: Number of recent candles to display
        save_path: File path to save (default: kline_{code}.png)

    Returns:
        Path to saved image file.
    """
    df = df.tail(last_n).copy()
    if df.empty:
        return ""

    if save_path is None:
        save_path = f"kline_{code}.png"

    fig, axes = plt.subplots(4, 1, figsize=(14, 10),
                              gridspec_kw={"height_ratios": [3, 1, 1, 1]},
                              sharex=True)
    fig.suptitle(f"{name} ({code}) K线图", fontsize=14, fontweight="bold")

    ax_price, ax_vol, ax_macd, ax_rsi = axes

    # --- Price + MA ---
    x = range(len(df))

    # Candlestick colors
    colors = ["red" if c >= o else "green"
              for o, c in zip(df["open"], df["close"])]

    # Plot candlestick bodies
    for i in range(len(df)):
        o, h, l, c = df["open"].iloc[i], df["high"].iloc[i], \
                      df["low"].iloc[i], df["close"].iloc[i]
        color = "red" if c >= o else "green"

        # Body
        body_bottom = min(o, c)
        body_height = abs(c - o)
        rect = Rectangle((i - 0.3, body_bottom), 0.6, body_height,
                         facecolor=color, edgecolor=color)
        ax_price.add_patch(rect)

        # Wicks
        ax_price.plot([i, i], [l, body_bottom], color=color, linewidth=0.8)
        ax_price.plot([i, i], [min(o, c) + body_height, h], color=color,
                     linewidth=0.8)

    # MA lines
    ma_colors = {"ma5": "blue", "ma10": "orange", "ma20": "purple",
                 "ma60": "brown", "ma120": "gray"}
    for ma, color in ma_colors.items():
        if ma in df.columns:
            ax_price.plot(x, df[ma].values, label=ma.upper(), color=color,
                         linewidth=1, alpha=0.8)

    ax_price.set_ylabel("价格")
    ax_price.legend(loc="upper left", fontsize=8)
    ax_price.grid(True, alpha=0.3)

    # --- Volume ---
    vol_colors = ["red" if c >= o else "green"
                  for o, c in zip(df["open"], df["close"])]
    ax_vol.bar(x, df["volume"], color=vol_colors, alpha=0.7)
    if "vol_ma5" in df.columns:
        ax_vol.plot(x, df["vol_ma5"].values, color="blue", linewidth=1,
                   label="VOL MA5")
    ax_vol.set_ylabel("成交量")
    ax_vol.grid(True, alpha=0.3)

    # --- MACD ---
    if "macd_hist" in df.columns:
        macd_colors = ["red" if v >= 0 else "green" for v in df["macd_hist"]]
        ax_macd.bar(x, df["macd_hist"], color=macd_colors, alpha=0.7)
        ax_macd.plot(x, df["macd_dif"].values, color="blue", linewidth=1,
                    label="DIF")
        ax_macd.plot(x, df["macd_dea"].values, color="orange", linewidth=1,
                    label="DEA")
        ax_macd.set_ylabel("MACD")
        ax_macd.legend(loc="upper left", fontsize=8)
        ax_macd.axhline(y=0, color="gray", linewidth=0.5)
    ax_macd.grid(True, alpha=0.3)

    # --- RSI ---
    if "rsi" in df.columns:
        ax_rsi.plot(x, df["rsi"].values, color="purple", linewidth=1)
        ax_rsi.axhline(y=70, color="red", linewidth=0.5, linestyle="--")
        ax_rsi.axhline(y=30, color="green", linewidth=0.5, linestyle="--")
        ax_rsi.set_ylabel("RSI")
        ax_rsi.set_ylim(0, 100)
    ax_rsi.grid(True, alpha=0.3)

    # X-axis labels
    if "date" in df.columns:
        dates = df["date"].values
        step = max(len(dates) // 10, 1)
        tick_pos = list(range(0, len(dates), step))
        tick_labels = [str(dates[i])[:10] for i in tick_pos]
        ax_rsi.set_xticks(tick_pos)
        ax_rsi.set_xticklabels(tick_labels, rotation=45, fontsize=8)

    ax_rsi.set_xlabel("日期")
    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return save_path


def plot_backtest(result: dict, save_path: str = None) -> str:
    """
    Plot backtest equity curve vs benchmark.

    Args:
        result: Backtest result dict from run_backtest()
        save_path: File path to save

    Returns:
        Path to saved image file.
    """
    if save_path is None:
        save_path = "backtest_result.png"

    equity = result.get("equity_curve")
    benchmark = result.get("benchmark_curve")
    metrics = result.get("metrics", {})

    if equity is None or equity.empty:
        return ""

    fig, ax = plt.subplots(figsize=(12, 6))

    ax.plot(equity.index, equity.values, label="策略净值", color="red",
            linewidth=2)

    if benchmark is not None and not benchmark.empty:
        ax.plot(benchmark.index, benchmark.values, label="基准(上证指数)",
                color="blue", linewidth=1.5, alpha=0.7)

    ax.set_title("回测净值曲线", fontsize=14, fontweight="bold")
    ax.set_xlabel("日期")
    ax.set_ylabel("净值")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    # Add metrics text box
    if metrics:
        text = "\n".join([f"{k}: {v}" for k, v in metrics.items()])
        ax.text(0.98, 0.02, text, transform=ax.transAxes, fontsize=9,
                verticalalignment="bottom", horizontalalignment="right",
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return save_path


def plot_prediction(df: pd.DataFrame, prediction: dict,
                    code: str = "", save_path: str = None) -> str:
    """
    Plot recent price action with prediction signal.

    Args:
        df: K-line DataFrame
        prediction: Prediction result dict
        code: Stock code
        save_path: File path to save

    Returns:
        Path to saved image file.
    """
    if save_path is None:
        save_path = f"prediction_{code}.png"

    df = df.tail(60).copy()
    if df.empty:
        return ""

    fig, ax = plt.subplots(figsize=(12, 6))

    x = range(len(df))
    ax.plot(x, df["close"].values, color="black", linewidth=1.5, label="收盘价")

    # MA lines
    if "ma20" in df.columns:
        ax.plot(x, df["ma20"].values, color="blue", linewidth=1,
               alpha=0.7, label="MA20")
    if "ma60" in df.columns:
        ax.plot(x, df["ma60"].values, color="orange", linewidth=1,
               alpha=0.7, label="MA60")

    # Prediction signal
    direction = prediction.get("direction", "")
    confidence = prediction.get("confidence", 0)
    up_prob = prediction.get("up_probability", 50)

    color = "red" if direction == "上涨" else "green"
    marker = "^" if direction == "上涨" else "v"
    ax.scatter(len(df) - 1, df["close"].iloc[-1], color=color, marker=marker,
              s=200, zorder=5, label=f"预测: {direction} ({confidence:.1f}%)")

    ax.set_title(f"{code} 预测信号: {direction} (上涨概率: {up_prob:.1f}%)",
                fontsize=13, fontweight="bold")
    ax.set_xlabel("日期")
    ax.set_ylabel("价格")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)

    # X-axis dates
    if "date" in df.columns:
        dates = df["date"].values
        step = max(len(dates) // 8, 1)
        tick_pos = list(range(0, len(dates), step))
        tick_labels = [str(dates[i])[:10] for i in tick_pos]
        ax.set_xticks(tick_pos)
        ax.set_xticklabels(tick_labels, rotation=45, fontsize=8)

    plt.tight_layout()
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return save_path
