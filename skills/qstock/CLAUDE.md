# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股股票预测分析程序，基于 tkfy920/qstock 的东方财富数据接口，融合技术指标、基本面评分和机器学习模型进行多因子选股预测。

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Predict single stock direction
python main.py predict 000001
python main.py predict 600519 --days 10

# Scan market and rank stocks
python main.py scan 沪深A
python main.py scan 创业板 --top 20

# Run backtest
python main.py backtest 沪深A --top 10 --days 20 --start 20230101

# Show K-line chart with technical indicators
python main.py kline 000001 --last 120

# View stock financial info
python main.py info 600519
```

## Architecture

```
main.py              CLI entry point with subcommands (predict/scan/backtest/kline/info)
config.py            API endpoints, market mappings, indicator parameters
data/
  fetcher.py         East Money API data acquisition (kline, realtime, financials)
  processor.py       Data cleaning, feature engineering, label generation
model/
  technical.py       Technical indicators (MA, MACD, RSI, KDJ, Bollinger)
  fundamental.py     Financial metric scoring (PE, PB, ROE, margins)
  predictor.py       ML prediction (Random Forest + Gradient Boosting ensemble)
strategy/
  scoring.py         Multi-factor composite scoring (technical + momentum + fundamental)
  backtest.py        Vectorized backtesting with equity curve vs benchmark
view/
  chart.py           Matplotlib charts (K-line, backtest equity, prediction signals)
```

## Data Pipeline

1. **Fetch**: `data/fetcher.py` calls East Money push2 APIs for OHLCV and financial data
2. **Process**: `data/processor.py` cleans data, computes returns/volatility/volume features
3. **Indicators**: `model/technical.py` computes MA/MACD/RSI/KDJ/Bollinger from OHLCV
4. **Predict**: `model/predictor.py` trains RF+GB ensemble on features, predicts direction
5. **Score**: `strategy/scoring.py` combines technical + momentum + fundamental into composite score

## Key Design Decisions

- Data source: Direct East Money API (same as qstock), no external package dependency
- Prediction: Binary classification (up/down) with probability, not price regression
- Validation: Time-series split (no shuffling) to avoid look-ahead bias
- Ensemble: Random Forest + Gradient Boosting soft voting
- Features: 20+ features across returns, volatility, volume, and technical indicators

## Market Codes

Common market identifiers used in scan/commands:
- `沪深A` — Shanghai + Shenzhen A-shares (default)
- `创业板` — ChiNext (Growth Enterprise Market)
- `科创板` — STAR Market
- `上证A` — Shanghai A-shares only
- `深证A` — Shenzhen A-shares only
