# -*- coding: utf-8 -*-
"""Variable_Calculator：盯盘变量与技术指标计算封装。

职责（对应需求 3.1）：把持仓与止损设定转换为 ``compute_monitor_variables`` 的参数并
调用之，附加单轮 10 秒超时控制；并为 Rule_Engine 的买入/卖出信号（需求 5.1/5.2）补取
跨周期 MACD 柱值派生字段 ``macd_hist_prev`` / ``macd_hist_curr``。

设计决策（MACD 柱值跨周期条件）：需求 5.1/5.2 的 MACD 条件定义为"上一周期柱值 < 0 且
当前周期柱值 ≥ 0"（卖出为镜像），而 ``compute_monitor_variables`` 现仅返回当前
``MACD_HIST``。为满足需求且**不改动 qstock 对外契约**，本封装：
  - ``macd_hist_curr`` 直接取自结果字典的 ``MACD_HIST``（qstock 已按 26 根门控并四舍五入）；
  - ``macd_hist_prev`` 复用 qstock 内部同款指标计算路径（``get_kline_cached`` →
    ``detect_and_truncate_split`` → ``compute_all_indicators``）补取 ``macd_hist`` 倒数
    第二根，并施加与 qstock 一致的 26 根门控。
该做法不侵入 qstock 契约，仅在应用层补充派生字段。

结果封装（对齐设计 "RoundResult"）：成功返回 ``RoundResult(ok=True, vars=..., ...)``；
异常、超时、``MonitorInputError`` 或结果含 ``error`` 标识时兜底返回
``RoundResult(ok=False, error=...)``，且不清空调用方已有展示数据（由上层保留）。
"""

from __future__ import annotations

import concurrent.futures
import math
from datetime import datetime
from typing import Optional, Tuple

from . import ensure_qstock_on_path
from .models import PositionInput, RoundResult

# 先把 <技能根>/qstock 加入 sys.path，随后按 qstock 内部约定直接 import 其模块。
ensure_qstock_on_path()
from strategy.monitor import (  # noqa: E402  (须在 ensure_qstock_on_path 之后导入)
    compute_monitor_variables,
    MonitorInputError,
    MIN_BARS_FOR,
)
from data.kline_cache import get_kline_cached  # noqa: E402
from data.processor import detect_and_truncate_split  # noqa: E402
from model.technical import compute_all_indicators  # noqa: E402

# 本功能标的固定为 588170，不支持切换其他标的。
SYMBOL = "588170"
# 历史数据起始日期，与 compute_monitor_variables 默认值保持一致，确保派生 MACD 与主结果同源。
_KLINE_START = "20200101"
# MACD 所需最小 K 线数量（复用 qstock 的门控阈值，通常为 26）。
_MACD_MIN_BARS = MIN_BARS_FOR.get("macd", 26)


def _round_or_none(value, ndigits: int = 4) -> Optional[float]:
    """安全四舍五入：None / NaN / 非数值一律返回 None，避免抛异常或输出 'nan'。"""
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f):
            return None
        return round(f, ndigits)
    except (TypeError, ValueError):
        return None


class VariableCalculator:
    """封装 ``compute_monitor_variables`` 调用，附加 10 秒超时与派生 MACD 字段。"""

    def __init__(self, code: str = SYMBOL) -> None:
        # 标的代码固定为 588170，保留参数仅便于测试注入。
        self._code = code

    def calculate(self, position: PositionInput, *, timeout: float = 10.0) -> RoundResult:
        """执行一轮盯盘变量计算，返回封装后的 ``RoundResult``。

        Args:
            position: 持仓与止损设定（应用内内存对象）。
            timeout: 单轮计算的超时上限（秒），默认 10 秒（需求 2.7 单轮超时约束）。

        Returns:
            成功轮 ``RoundResult(ok=True, vars=..., price_source=..., macd_hist_*=...)``；
            失败轮 ``RoundResult(ok=False, error=...)``（异常/超时/参数错误/数据获取失败）。
        """
        fetched_at = datetime.now()

        # 在独立工作线程中执行阻塞式 I/O 与计算，并对本轮施加超时上限。
        # 超时后不阻塞等待遗留线程（shutdown(wait=False)），遗留线程会在网络返回后自然结束。
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(self._compute, position)
            result, macd_prev, macd_curr = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return self._error_result(
                f"本轮行情获取超过 {timeout:.0f} 秒未返回，已按失败处理", fetched_at
            )
        except MonitorInputError as exc:
            # 持仓非正数等参数错误：Position_Manager 校验阶段通常已拦截，此处兜底捕获。
            return self._error_result(f"输入参数不合法：{exc}", fetched_at)
        except Exception as exc:  # noqa: BLE001  兜底任何计算/网络异常，保证轮询线程不崩溃
            return self._error_result(f"行情获取或计算失败：{exc}", fetched_at)
        finally:
            # 不等待遗留任务，避免超时后仍被阻塞。
            executor.shutdown(wait=False)

        # compute_monitor_variables 以返回值（而非异常）表达"无法获取历史数据"等失败。
        if not isinstance(result, dict) or result.get("error"):
            error_msg = result.get("error") if isinstance(result, dict) else "计算结果格式异常"
            return self._error_result(str(error_msg), fetched_at)

        # 成功轮：原样封装 qstock 返回的变量字典，并附加价格来源与派生 MACD 字段。
        return RoundResult(
            ok=True,
            vars=result,
            error=None,
            price_source=result.get("价格来源"),
            macd_hist_prev=macd_prev,
            macd_hist_curr=macd_curr,
            fetched_at=fetched_at,
        )

    # --- 内部实现 ---------------------------------------------------------

    def _compute(self, position: PositionInput) -> Tuple[dict, Optional[float], Optional[float]]:
        """在工作线程内执行：调用 qstock 计算主结果，并补取派生 MACD 柱值。"""
        result = compute_monitor_variables(
            code=self._code,
            position=position.position,
            cost=position.cost,
            cash=position.cash,
            max_loss_pct=position.max_loss_pct,
            max_loss_amount=position.max_loss_amount,
            stop_loss_price=position.stop_loss_price,
        )
        macd_prev, macd_curr = self._derive_macd_hist(result)
        return result, macd_prev, macd_curr

    def _derive_macd_hist(self, result: dict) -> Tuple[Optional[float], Optional[float]]:
        """派生 ``macd_hist_prev`` / ``macd_hist_curr``（不侵入 qstock 契约）。

        - 数据获取失败或 MACD 数据不足（``MACD_HIST`` 为 None）时，两者均为 None；
        - ``macd_hist_curr`` 取自 qstock 结果的 ``MACD_HIST``（已按 26 根门控并四舍五入）；
        - ``macd_hist_prev`` 通过同款指标路径补取倒数第二根柱值，施加同样的 26 根门控；
        - 派生过程任何异常都不影响主结果，静默降级为 prev=None。
        """
        if not isinstance(result, dict) or result.get("error"):
            return (None, None)

        macd_curr = result.get("MACD_HIST")
        # MACD_HIST 为 None 说明数据不足以支撑 MACD，跨周期条件无从判断。
        if macd_curr is None:
            return (None, None)

        macd_prev: Optional[float] = None
        try:
            df = get_kline_cached(self._code, start=_KLINE_START)
            if df is not None and not df.empty:
                # 与 compute_monitor_variables 内部一致：先截断拆分跳空，再计算指标。
                df, _split_detected, _split_date = detect_and_truncate_split(df)
                usable_bars = len(df)
                if usable_bars >= _MACD_MIN_BARS and usable_bars >= 2:
                    df = compute_all_indicators(df)
                    macd_prev = _round_or_none(df["macd_hist"].iloc[-2])
        except Exception:  # noqa: BLE001  派生失败不影响主结果
            macd_prev = None

        return (macd_prev, macd_curr)

    @staticmethod
    def _error_result(error: str, fetched_at: datetime) -> RoundResult:
        """构造失败轮 RoundResult：不携带变量、派生字段与价格来源均为空。"""
        return RoundResult(
            ok=False,
            vars={},
            error=error,
            price_source=None,
            macd_hist_prev=None,
            macd_hist_curr=None,
            fetched_at=fetched_at,
        )
