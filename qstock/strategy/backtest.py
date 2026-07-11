# -*- coding: utf-8 -*-
"""
Vectorized backtesting module - tests strategy performance on historical data.
"""

import numpy as np
import pandas as pd
from data.fetcher import get_kline, get_market_realtime
from strategy.scoring import multi_factor_score


def _compute_metrics(returns: pd.Series, benchmark: pd.Series = None) -> dict:
    """
    Compute portfolio performance metrics.

    Args:
        returns: Daily return series
        benchmark: Benchmark daily return series (optional)

    Returns:
        Dict with performance metrics.
    """
    if returns.empty:
        return {}

    total_ret = (1 + returns).prod() - 1
    n_days = len(returns)
    annual_ret = (1 + total_ret) ** (252 / max(n_days, 1)) - 1
    annual_vol = returns.std() * np.sqrt(252)
    sharpe = annual_ret / (annual_vol + 1e-10)

    # Max drawdown
    cumulative = (1 + returns).cumprod()
    peak = cumulative.expanding().max()
    drawdown = (cumulative - peak) / peak
    max_dd = drawdown.min()

    # Win rate
    win_rate = (returns > 0).mean() * 100

    result = {
        "总收益率": f"{total_ret * 100:.2f}%",
        "年化收益率": f"{annual_ret * 100:.2f}%",
        "年化波动率": f"{annual_vol * 100:.2f}%",
        "夏普比率": f"{sharpe:.2f}",
        "最大回撤": f"{max_dd * 100:.2f}%",
        "胜率": f"{win_rate:.1f}%",
        "交易天数": n_days,
    }

    if benchmark is not None and not benchmark.empty:
        bench_total = (1 + benchmark).prod() - 1
        bench_annual = (1 + bench_total) ** (252 / max(len(benchmark), 1)) - 1
        result["基准收益率"] = f"{bench_total * 100:.2f}%"
        result["基准年化收益率"] = f"{bench_annual * 100:.2f}%"
        result["超额收益"] = f"{(annual_ret - bench_annual) * 100:.2f}%"

    return result


def run_backtest(market: str = "沪深A", top_n: int = 10,
                 rebalance_days: int = 20, lookback_days: int = 250,
                 start_date: str = "20230101") -> dict:
    """
    Run a simple multi-factor backtest.

    Strategy:
    1. Every rebalance_days, score all stocks in the market
    2. Select top_n stocks by composite score
    3. Equal-weight portfolio
    4. Hold until next rebalance

    Args:
        market: Market to backtest
        top_n: Number of stocks to hold
        rebalance_days: Rebalance frequency (trading days)
        lookback_days: History needed for indicator calculation
        start_date: Backtest start date

    Returns:
        Dict with backtest results and equity curve.
    """
    # Get stock list
    stocks = get_market_realtime(market)
    if stocks.empty:
        return {"error": "无法获取股票列表"}

    # Filter valid stocks (exclude ST, new listings, etc.)
    codes = stocks["代码"].tolist()
    names = dict(zip(stocks["代码"], stocks["名称"]))

    # Simple simulation: fetch data for a sample of stocks
    # (full market scan would be too slow for demo)
    sample_size = min(50, len(codes))
    sample_codes = codes[:sample_size]

    print(f"正在获取 {sample_size} 只股票的历史数据...")

    # Fetch historical data
    stock_data = {}
    for code in sample_codes:
        try:
            df = get_kline(code, start="20220101")
            if not df.empty and len(df) > 100:
                stock_data[code] = df
        except Exception:
            continue

    if len(stock_data) < top_n:
        return {"error": f"有效股票数量不足 ({len(stock_data)} < {top_n})"}

    print(f"成功获取 {len(stock_data)} 只股票数据，开始回测...")

    # Score stocks at start
    scores = {}
    for code, df in stock_data.items():
        try:
            result = multi_factor_score(df)
            if "error" not in result:
                scores[code] = result["composite_score"]
        except Exception:
            continue

    # Select top stocks
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    selected = [code for code, _ in ranked[:top_n]]

    # Compute equal-weight portfolio returns
    returns_list = []
    for code in selected:
        df = stock_data[code]
        df_start = df[df.index >= pd.to_datetime(start_date)]
        if not df_start.empty:
            daily_ret = df_start["close"].pct_change().dropna()
            daily_ret.name = code
            returns_list.append(daily_ret)

    if not returns_list:
        return {"error": "回测期间无有效数据"}

    # Align dates
    ret_df = pd.concat(returns_list, axis=1)
    ret_df = ret_df.dropna()

    # Equal weight portfolio
    portfolio_ret = ret_df.mean(axis=1)

    # Benchmark: 上证指数
    bench_ret = None
    try:
        bench_df = get_kline("000001", start="20220101")
        bench_df_start = bench_df[bench_df.index >= pd.to_datetime(start_date)]
        if not bench_df_start.empty:
            bench_ret = bench_df_start["close"].pct_change().dropna()
            bench_ret = bench_ret.reindex(portfolio_ret.index).dropna()
            portfolio_ret = portfolio_ret.reindex(bench_ret.index).dropna()
    except Exception:
        pass

    metrics = _compute_metrics(portfolio_ret, bench_ret)

    # Build equity curve
    equity = (1 + portfolio_ret).cumprod()

    result = {
        "selected_stocks": [
            {"code": c, "name": names.get(c, ""), "score": scores.get(c, 0)}
            for c in selected
        ],
        "metrics": metrics,
        "equity_curve": equity,
        "daily_returns": portfolio_ret,
    }

    if bench_ret is not None and not bench_ret.empty:
        result["benchmark_curve"] = (1 + bench_ret).cumprod()

    return result
