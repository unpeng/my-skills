# -*- coding: utf-8 -*-
"""
kline_cache 增量缓存的单元测试。

使用注入的假 fetch_func（不联网），覆盖：
1. 首次调用 → 全量拉取并生成缓存
2. 二次调用 → 走增量，只请求重叠+新增区间，结果与全量一致
3. 新增交易日 → 增量合并新K线
4. 复权漂移 → 历史重叠日收盘价变化时触发全量刷新
5. 增量拉取失败 → 退回旧缓存
6. 请求区间早于缓存 → 全量重拉
7. 按 start/end 区间过滤正确
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import data.kline_cache as kc


def _make_df(dates, closes=None, code="588170"):
    """构造与 get_kline 结构一致的 DataFrame。"""
    if closes is None:
        closes = [1.0 + i * 0.01 for i in range(len(dates))]
    rows = []
    for d, c in zip(dates, closes):
        rows.append({
            "name": "测试ETF", "code": code, "date": d,
            "open": c, "high": c + 0.02, "low": c - 0.02,
            "close": c, "volume": 1000000 + 1, "turnover": 1.0,
            "turnover_rate": 0.5, "amplitude": 1.0,
            "pct_change": 0.1, "change": 0.001,
        })
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["date"])
    return df


def _date_range(start_str, n):
    """生成 n 个连续自然日（简化，测试用不校验是否交易日）。"""
    d0 = datetime.strptime(start_str, "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)]


class FakeFetcher:
    """记录调用参数的假数据源，可配置返回不同数据。"""
    def __init__(self, full_df):
        self.full_df = full_df
        self.calls = []

    def __call__(self, code, start="19000101", end=None, fqt=1):
        self.calls.append({"start": start, "end": end, "fqt": fqt})
        s = kc._fmt_date(start)
        e = kc._fmt_date(end) if end else self.full_df["date"].max()
        df = self.full_df[(self.full_df["date"] >= s) & (self.full_df["date"] <= e)]
        return df.copy()


class KlineCacheTest(unittest.TestCase):
    def setUp(self):
        # 把缓存目录重定向到临时目录，避免污染真实 .local
        self._orig_dir = kc._CACHE_DIR
        self.tmp = tempfile.mkdtemp()
        kc._CACHE_DIR = self.tmp

    def tearDown(self):
        kc._CACHE_DIR = self._orig_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _today_dates(self, n):
        """生成以今天为最后一天的 n 个连续日期（保证不触发过期兜底）。"""
        today = datetime.now()
        return [(today - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d")
                for i in range(n)]

    def test_first_call_full_fetch_and_cache(self):
        dates = self._today_dates(10)
        full = _make_df(dates)
        fake = FakeFetcher(full)

        df = kc.get_kline_cached("588170", start=dates[0].replace("-", ""),
                                 fetch_func=fake)
        self.assertEqual(len(df), 10)
        self.assertEqual(len(fake.calls), 1)  # 只全量拉一次
        self.assertTrue(os.path.exists(kc._cache_path("588170", 1)))

    def test_second_call_incremental(self):
        dates = self._today_dates(10)
        full = _make_df(dates)
        fake = FakeFetcher(full)

        kc.get_kline_cached("588170", start=dates[0].replace("-", ""), fetch_func=fake)
        fake.calls.clear()

        df = kc.get_kline_cached("588170", start=dates[0].replace("-", ""), fetch_func=fake)
        self.assertEqual(len(df), 10)  # 结果不变
        self.assertEqual(len(fake.calls), 1)  # 增量只拉一次
        # 增量起点应是缓存倒数第 OVERLAP_BARS 条，而非最早日期
        inc_start = kc._fmt_date(fake.calls[0]["start"])
        self.assertEqual(inc_start, dates[10 - kc.OVERLAP_BARS])

    def test_incremental_appends_new_bar(self):
        dates = self._today_dates(10)
        fake = FakeFetcher(_make_df(dates))
        kc.get_kline_cached("588170", start=dates[0].replace("-", ""), fetch_func=fake)

        # 新增一个交易日（今天+1），更新假数据源
        new_dates = self._today_dates(10)
        # 追加“明天”一条
        extra = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        all_dates = new_dates + [extra]
        fake.full_df = _make_df(all_dates)

        df = kc.get_kline_cached("588170", start=dates[0].replace("-", ""),
                                 end=extra.replace("-", ""), fetch_func=fake)
        self.assertEqual(len(df), 11)
        self.assertIn(extra, list(df["date"]))

    def test_adjustment_drift_triggers_full_refresh(self):
        dates = self._today_dates(10)
        fake = FakeFetcher(_make_df(dates))
        kc.get_kline_cached("588170", start=dates[0].replace("-", ""), fetch_func=fake)

        # 模拟前复权调整：所有历史收盘价整体缩小 20%（除权）
        adj_closes = [(1.0 + i * 0.01) * 0.8 for i in range(10)]
        fake.full_df = _make_df(dates, closes=adj_closes)
        fake.calls.clear()

        df = kc.get_kline_cached("588170", start=dates[0].replace("-", ""), fetch_func=fake)
        # 漂移检测应触发：先增量拉一次，检测到漂移再全量拉一次
        self.assertGreaterEqual(len(fake.calls), 2)
        # 返回的应是调整后的数据
        self.assertAlmostEqual(float(df.iloc[0]["close"]), adj_closes[0], places=4)

    def test_incremental_fetch_failure_falls_back_to_cache(self):
        dates = self._today_dates(10)
        fake = FakeFetcher(_make_df(dates))
        kc.get_kline_cached("588170", start=dates[0].replace("-", ""), fetch_func=fake)

        # 增量返回空（模拟网络失败）
        def failing_fetch(code, start="19000101", end=None, fqt=1):
            return pd.DataFrame()

        df = kc.get_kline_cached("588170", start=dates[0].replace("-", ""),
                                 fetch_func=failing_fetch)
        self.assertEqual(len(df), 10)  # 退回旧缓存

    def test_request_earlier_than_cache_triggers_full(self):
        dates = self._today_dates(10)
        fake = FakeFetcher(_make_df(dates))
        kc.get_kline_cached("588170", start=dates[5].replace("-", ""), fetch_func=fake)
        fake.calls.clear()

        # 请求比缓存更早的起点 → 应全量重拉
        kc.get_kline_cached("588170", start=dates[0].replace("-", ""), fetch_func=fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(kc._fmt_date(fake.calls[0]["start"]), dates[0])

    def test_range_filter(self):
        dates = self._today_dates(10)
        fake = FakeFetcher(_make_df(dates))
        df = kc.get_kline_cached("588170", start=dates[0].replace("-", ""),
                                 end=dates[4].replace("-", ""), fetch_func=fake)
        self.assertEqual(len(df), 5)
        self.assertEqual(df["date"].max(), dates[4])


if __name__ == "__main__":
    unittest.main(verbosity=2)
