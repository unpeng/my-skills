# -*- coding: utf-8 -*-
"""
Configuration constants for the stock prediction program.
API endpoints, request headers, market mappings, and default parameters.
"""

# ============================================================
# East Money API Endpoints
# ============================================================
KLINE_URL = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
REALTIME_URL = "http://push2.eastmoney.com/api/qt/clist/get"
SINGLE_REALTIME_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
# push2.eastmoney.com 对单只行情接口存在系统性连接故障（ConnectionError，非限流），
# 同域名重试无意义，故障时改用 push2delay 域名兜底（数据经核对与主域名一致，非真正延迟）。
SINGLE_REALTIME_URL_FALLBACK = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
STOCK_INFO_URL = "http://push2.eastmoney.com/api/qt/stock/get"
INTRADAY_URL = "https://push2.eastmoney.com/api/qt/stock/details/get"
TRENDS_URL = "http://push2his.eastmoney.com/api/qt/stock/trends2/get"

# ============================================================
# Request Headers (mimic mobile app)
# ============================================================
REQUEST_HEADER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

# ============================================================
# Market Code Mappings
# ============================================================
MARKET_FILTER = {
    "沪深A": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23",
    "上证A": "m:1 t:2,m:1 t:23",
    "深证A": "m:0 t:6,m:0 t:80",
    "北证A": "m:0 t:81 s:2048",
    "创业板": "m:0 t:80",
    "科创板": "m:1 t:23",
    "沪深京A": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
    "上证指数": "m:1 s:2",
    "深证指数": "m:0 t:5",
    "沪深指数": "m:1 s:2,m:0 t:5",
}

MARKET_NUM_DICT = {
    "1": "沪A",
    "0": "深A",
    "2": "北A",
    "100": "港",
    "105": "美",
    "106": "美",
    "107": "美",
}

# ============================================================
# Trade Detail Field Mapping (East Money API fields → Chinese)
# ============================================================
TRADE_DETAIL_DICT = {
    "f12": "代码",
    "f14": "名称",
    "f2": "最新价",
    "f3": "涨跌幅",
    "f4": "涨跌额",
    "f5": "成交量",
    "f6": "成交额",
    "f7": "振幅",
    "f8": "换手率",
    "f9": "市盈率(动)",
    "f10": "量比",
    "f15": "最高",
    "f16": "最低",
    "f17": "今开",
    "f18": "昨收",
    "f126": "更新时间戳",
    "f100": "所处行业",
}

# ============================================================
# K-line Field Mapping
# ============================================================
KLINE_FIELD = {
    "f51": "日期",
    "f52": "开盘",
    "f53": "收盘",
    "f54": "最高",
    "f55": "最低",
    "f56": "成交量",
    "f57": "成交额",
    "f58": "振幅",
    "f59": "涨跌幅",
    "f60": "涨跌额",
    "f61": "换手率",
}

# ============================================================
# Stock Info Field Mapping
# ============================================================
STOCK_INFO_DICT = {
    "f57": "代码",
    "f58": "名称",
    "f162": "市盈率(动)",
    "f167": "市净率",
    "f127": "所处行业",
    "f116": "总市值",
    "f117": "流通市值",
    "f173": "ROE",
    "f187": "净利率",
    "f105": "净利润",
    "f186": "毛利率",
}

# ============================================================
# Frequency Mapping
# ============================================================
FREQ_MAP = {
    "d": 101,
    "w": 102,
    "m": 103,
    "1": 1,
    "5": 5,
    "15": 15,
    "30": 30,
    "60": 60,
}

# ============================================================
# Default Technical Indicator Parameters
# ============================================================
MA_PERIODS = [5, 10, 20, 60, 120]
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
RSI_PERIOD = 14
KDJ_PERIOD = 9
BOLL_PERIOD = 20
BOLL_STD = 2

# ============================================================
# Prediction Parameters
# ============================================================
PREDICT_DAYS = 5          # Predict N-day forward return
TRAIN_RATIO = 0.8         # Train/test split ratio
RANDOM_STATE = 42         # Random seed for reproducibility
