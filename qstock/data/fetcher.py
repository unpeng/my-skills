# -*- coding: utf-8 -*-
"""
Data acquisition module - fetches stock data from East Money (东方财富) APIs.
Adapted from tkfy920/qstock data/trade.py.
"""

import time
import requests
import pandas as pd
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    KLINE_URL, REALTIME_URL, SINGLE_REALTIME_URL, STOCK_INFO_URL,
    REQUEST_HEADER, MARKET_FILTER, MARKET_NUM_DICT,
    TRADE_DETAIL_DICT, KLINE_FIELD, STOCK_INFO_DICT, FREQ_MAP,
)

# Reusable session for connection pooling
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
})


def _request_with_retry(url: str, params: dict, max_retries: int = 5,
                        timeout: int = 20) -> requests.Response:
    """Make HTTP request with retry logic for rate limiting."""
    import random
    for attempt in range(max_retries):
        try:
            # Add small random delay to avoid synchronized requests
            time.sleep(random.uniform(0.5, 1.5))
            resp = _session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5 + random.uniform(1, 3)
                time.sleep(wait)
            else:
                raise


def _get_code_id(code: str) -> str:
    """Convert stock code to East Money secid format (market.code)."""
    code = str(code).strip()
    if "." in code:
        return code
    if code.startswith("6") or code.startswith("9") or code.startswith("5"):
        return f"1.{code}"
    elif code.startswith("0") or code.startswith("3") or code.startswith("2"):
        return f"0.{code}"
    elif code.startswith("4") or code.startswith("8"):
        return f"0.{code}"
    elif code.startswith("1"):  # 债券/ETF
        return f"1.{code}"
    else:
        return f"0.{code}"


def _trans_num(df: pd.DataFrame, ignore_cols: list) -> pd.DataFrame:
    """Convert columns to numeric types, ignoring specified columns."""
    for col in df.columns:
        if col not in ignore_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def get_stock_list(market: str = "沪深A") -> list:
    """
    Get all stock codes for a given market.

    Args:
        market: Market name (沪深A, 上证A, 深证A, 创业板, 科创板, etc.)

    Returns:
        List of stock codes.
    """
    fs = MARKET_FILTER.get(market, MARKET_FILTER["沪深A"])
    fields = ",".join(TRADE_DETAIL_DICT.keys())
    codes = []
    page = 1

    while True:
        params = {
            "pn": str(page),
            "pz": "5000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": fields,
        }
        try:
            resp = _request_with_retry(REALTIME_URL, params=params)
            data = resp.json()
            diff = data.get("data", {}).get("diff", [])
            if not diff:
                break
            for item in diff:
                code = item.get("f12", "")
                if code:
                    codes.append(code)
            page += 1
            time.sleep(0.3)
        except Exception:
            break

    return codes


def _get_kline_sina(code: str, start: str = "20200101",
                    end: str = None) -> pd.DataFrame:
    """
    Fetch K-line data from Sina Finance API (fallback when East Money is blocked).

    Args:
        code: Stock code
        start: Start date YYYYMMDD
        end: End date YYYYMMDD

    Returns:
        DataFrame with OHLCV data.
    """
    # Determine market prefix
    code = str(code).strip()
    if code.startswith("6") or code.startswith("9") or code.startswith("5"):
        sina_code = f"sh{code}"
    else:
        sina_code = f"sz{code}"

    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    params = {
        "symbol": sina_code,
        "scale": "240",  # daily
        "ma": "no",
        "datalen": "5000",
    }

    try:
        resp = requests.get(url, params=params, headers=REQUEST_HEADER, timeout=15)
        data = resp.json()

        if not data:
            return pd.DataFrame()

        rows = []
        for item in data:
            rows.append({
                "date": item["day"],
                "open": float(item["open"]),
                "close": float(item["close"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "volume": float(item["volume"]),
            })

        df = pd.DataFrame(rows)
        if df.empty:
            return df

        # Filter by date range
        start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        df = df[df["date"] >= start_fmt]

        df["name"] = ""
        df["code"] = code
        df["turnover"] = 0.0
        df["turnover_rate"] = 0.0
        df.index = pd.to_datetime(df["date"])

        return df

    except Exception:
        return pd.DataFrame()


def get_kline(code: str, start: str = "19000101", end: str = None,
              freq: str = "d", fqt: int = 1) -> pd.DataFrame:
    """
    Fetch historical K-line (OHLCV) data for a stock.
    Tries East Money API first, falls back to Tencent API.

    Args:
        code: Stock code (e.g., '000001', '600519')
        start: Start date in 'YYYYMMDD' or 'YYYY-MM-DD' format
        end: End date (defaults to latest trading day)
        freq: Frequency - 'd'(daily), 'w'(weekly), 'm'(monthly)
        fqt: Adjustment type - 1(qfq), 2(hfq), 0(no adjust)

    Returns:
        DataFrame with columns: date, name, code, open, high, low, close,
        volume, turnover, turnover_rate
    """
    if end is None:
        end = datetime.now().strftime("%Y%m%d")
    start = start.replace("-", "")
    end = end.replace("-", "")

    # Try East Money API first
    try:
        klt = FREQ_MAP.get(freq, 101)
        code_id = _get_code_id(code)
        fields2 = ",".join(KLINE_FIELD.keys())

        params = {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": fields2,
            "beg": start,
            "end": end,
            "rtntype": "6",
            "secid": code_id,
            "klt": str(klt),
            "fqt": str(fqt),
        }

        resp = _request_with_retry(KLINE_URL, params=params, max_retries=2)
        json_data = resp.json()

        klines = json_data.get("data", {}).get("klines", [])
        if klines:
            name = json_data["data"].get("name", "")
            stock_code = code_id.split(".")[-1]

            rows = [k.split(",") for k in klines]
            columns = list(KLINE_FIELD.values())
            df = pd.DataFrame(rows, columns=columns)

            # Rename to English
            col_map = {
                "日期": "date", "名称": "name", "代码": "code",
                "开盘": "open", "最高": "high", "最低": "low",
                "收盘": "close", "成交量": "volume", "成交额": "turnover",
                "换手率": "turnover_rate", "振幅": "amplitude",
                "涨跌幅": "pct_change", "涨跌额": "change",
            }
            df.insert(0, "code", stock_code)
            df.insert(0, "name", name)
            df = df.rename(columns=col_map)
            df.index = pd.to_datetime(df["date"])

            ignore = ["name", "code", "date"]
            df = _trans_num(df, ignore)
            return df
    except Exception:
        pass

    # Fallback to Sina Finance API
    return _get_kline_sina(code, start, end)


def get_realtime(code_list) -> pd.DataFrame:
    """
    Fetch real-time quote data for one or more stocks.

    Args:
        code_list: Single code string or list of codes

    Returns:
        DataFrame with real-time market data.
    """
    if isinstance(code_list, str):
        code_list = [code_list]

    secids = [_get_code_id(c) for c in code_list]
    fields = ",".join(TRADE_DETAIL_DICT.keys())

    params = {
        "fields": fields,
        "fltt": "2",
        "secids": ",".join(secids),
    }

    resp = _request_with_retry(SINGLE_REALTIME_URL, params=params)
    data = resp.json()
    diff = data.get("data", {}).get("diff", [])

    if not diff:
        return pd.DataFrame(columns=TRADE_DETAIL_DICT.values())

    df = pd.DataFrame(diff)
    # Keep only mapped columns
    available = [k for k in TRADE_DETAIL_DICT.keys() if k in df.columns]
    df = df[available].rename(columns=TRADE_DETAIL_DICT)

    ignore = ["名称", "代码", "所处行业"]
    df = _trans_num(df, ignore)
    return df


def get_financial(code: str) -> dict:
    """
    Fetch basic financial indicators for a single stock.

    Args:
        code: Stock code

    Returns:
        Dict with financial metrics (PE, PB, ROE, etc.)
    """
    code_id = _get_code_id(code)
    fields = ",".join(STOCK_INFO_DICT.keys())

    params = {
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "invt": "2",
        "fltt": "2",
        "fields": fields,
        "secid": code_id,
    }

    resp = _request_with_retry(STOCK_INFO_URL, params=params)
    data = resp.json().get("data", {})

    if not data:
        return {}

    result = {}
    for api_key, cn_name in STOCK_INFO_DICT.items():
        result[cn_name] = data.get(api_key, None)

    return result


def get_market_realtime(market: str = "沪深A") -> pd.DataFrame:
    """
    Fetch real-time quotes for all stocks in a market.

    Args:
        market: Market name

    Returns:
        DataFrame with all stocks' real-time data.
    """
    fs = MARKET_FILTER.get(market, MARKET_FILTER["沪深A"])
    fields = ",".join(TRADE_DETAIL_DICT.keys())
    df_total = pd.DataFrame()
    page = 1

    while True:
        params = {
            "pn": str(page),
            "pz": "5000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": fs,
            "fields": fields,
        }
        try:
            time.sleep(0.3)
            resp = _request_with_retry(REALTIME_URL, params=params)
            data = resp.json()
            diff = data.get("data", {}).get("diff", [])
            if not diff:
                break
            df_page = pd.DataFrame(diff)
            df_total = pd.concat([df_total, df_page], ignore_index=True)
            page += 1
        except Exception:
            break

    if df_total.empty:
        return df_total

    available = [k for k in TRADE_DETAIL_DICT.keys() if k in df_total.columns]
    df_total = df_total[available].rename(columns=TRADE_DETAIL_DICT)

    ignore = ["代码", "名称", "所处行业"]
    df_total = _trans_num(df_total, ignore)
    return df_total
