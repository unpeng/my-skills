# -*- coding: utf-8 -*-
"""Variable_Calculator：盯盘变量与技术指标计算封装。

职责（对应需求 3.1）：把持仓与止损设定转换为 ``compute_monitor_variables`` 的参数并
调用之，附加单轮 10 秒超时控制；并为 Rule_Engine 的买入/卖出信号（需求 5.1/5.2）读取
跨周期 MACD 柱值字段 ``macd_hist_prev`` / ``macd_hist_curr``。

设计决策（MACD 柱值跨周期条件）：需求 5.1/5.2 的 MACD 条件定义为"上一周期柱值 < 0 且
当前周期柱值 ≥ 0"（卖出为镜像）。``compute_monitor_variables`` 已同时返回当前柱值
``MACD_HIST`` 与上一周期柱值 ``MACD_HIST_PREV``（qstock 内部计算指标时本就算出了
完整序列，顺带返回不增加成本）。本封装只需原样读取这两个字段即可，**不再重新拉取
K线、重新计算指标**——早期实现为避免侵入 qstock 契约，在应用层重复调用了一遍
``get_kline_cached``+``compute_all_indicators`` 来补取上一周期值，但 East Money
K线接口单次耗时约 4~5 秒，重复拉取会使单轮总耗时逼近甚至超过 10 秒超时上限，
导致轮询频繁失败；现改为让 qstock 直接返回该字段，从根源消除重复请求。

结果封装（对齐设计 "RoundResult"）：成功返回 ``RoundResult(ok=True, vars=..., ...)``；
异常、超时、``MonitorInputError`` 或结果含 ``error`` 标识时兜底返回
``RoundResult(ok=False, error=...)``，且不清空调用方已有展示数据（由上层保留）。
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime
from typing import Optional, Tuple

from . import ensure_qstock_on_path
from .models import PositionInput, RoundResult

# 先把 <技能根>/qstock 加入 sys.path，随后按 qstock 内部约定直接 import 其模块。
ensure_qstock_on_path()
from strategy.monitor import (  # noqa: E402  (须在 ensure_qstock_on_path 之后导入)
    compute_monitor_variables,
    MonitorInputError,
)

# 本功能标的固定为 588170，不支持切换其他标的。
SYMBOL = "588170"


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
        """在工作线程内执行：调用 qstock 计算主结果，并原样读取其派生的 MACD 柱值字段。"""
        result = compute_monitor_variables(
            code=self._code,
            position=position.position,
            cost=position.cost,
            cash=position.cash,
            max_loss_pct=position.max_loss_pct,
            max_loss_amount=position.max_loss_amount,
            stop_loss_price=position.stop_loss_price,
            atr_stop_n=position.atr_stop_n,
        )
        macd_prev, macd_curr = self._extract_macd_hist(result)
        return result, macd_prev, macd_curr

    @staticmethod
    def _extract_macd_hist(result: dict) -> Tuple[Optional[float], Optional[float]]:
        """从 ``compute_monitor_variables`` 结果中读取 ``MACD_HIST``/``MACD_HIST_PREV``。

        两个字段均已由 qstock 按统一的 26 根门控计算并四舍五入；任一环节缺失
        （获取失败、数据不足）时对应字段本就是 None，此处直接原样传递。
        """
        if not isinstance(result, dict) or result.get("error"):
            return (None, None)
        return (result.get("MACD_HIST_PREV"), result.get("MACD_HIST"))

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
