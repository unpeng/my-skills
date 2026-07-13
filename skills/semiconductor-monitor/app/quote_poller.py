# -*- coding: utf-8 -*-
"""Quote_Poller：后台行情轮询线程（调度层）。

职责（对应需求 2.1/2.5/2.6/2.7/2.8、10.2/10.5）：在一个后台守护线程中按 Settings_Store
配置的轮询间隔周期性触发一轮行情获取与计算；支持启动、停止（当前轮完成后退出）、立即
刷新（忙时忽略）；每轮结果以线程安全方式经 ``queue.Queue`` 回送主线程消费。

线程模型（对齐设计 "线程模型"）：
  - 本组件是一个 ``threading.Thread(daemon=True)``，主窗口关闭即随进程退出。
  - 用 ``threading.Event`` 控制启停（``stop_event``）与立即刷新（``refresh_event``）。
  - 间隔等待用 ``refresh_event.wait(timeout=interval)`` 实现：既能按间隔到点触发（触发
    误差 ±1 秒内，需求 2.1），又能被"立即刷新"提前唤醒且不忙等（需求 2.6）。
  - 后台线程**只做计算与 I/O，不触碰任何 tkinter widget**；结果封装为 ``RoundResult``
    放入队列，由主线程通过 ``root.after`` 非阻塞消费。
  - ``busy`` 标志表示"本轮进行中"：主线程点击立即刷新时若 ``busy`` 为真则被忽略
    （``request_refresh`` 返回 False，需求 2.8）。

容错（需求 2.7/10.2/10.5）：每轮在 ``try/except`` 中调用 Variable_Calculator，单轮失败
或超时都只入队一个 ``RoundResult(ok=False, error=...)``，后台循环**不因单轮失败退出**，
下一个轮询间隔继续重试，进程保持运行。
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime
from typing import Callable, Optional

from .models import PositionInput, RoundResult
from .variable_calculator import VariableCalculator

# 轮询间隔的兜底下限（秒）：当 get_interval 返回非法值时使用，避免后台线程忙等空转。
_FALLBACK_INTERVAL_SECONDS = 60


class QuotePoller:
    """后台轮询线程：按间隔触发一轮行情获取与计算，结果入队回送主线程。"""

    def __init__(
        self,
        calculator: VariableCalculator,
        result_queue: "queue.Queue",
        get_interval: Callable[[], int],
        get_position: Callable[[], Optional[PositionInput]],
    ) -> None:
        """初始化轮询器。

        Args:
            calculator: 盯盘变量计算封装，负责单轮计算与 10 秒超时（其内部已兜底异常）。
            result_queue: 线程安全队列，本组件把每轮 ``RoundResult`` 放入其中供主线程消费。
            get_interval: 取当前轮询间隔（秒）的回调，每轮实时读取以便设置变更下一轮即生效
                （需求 2.3）。
            get_position: 取当前持仓（``PositionInput``）的回调；返回 None 表示尚未配置持仓，
                本轮以失败结果入队并继续下一轮。
        """
        self._calculator = calculator
        self._result_queue = result_queue
        self._get_interval = get_interval
        self._get_position = get_position

        # 启停与立即刷新的事件开关。
        self._stop_event = threading.Event()
        self._refresh_event = threading.Event()

        # busy 标志：True 表示当前正有一轮在进行中（需求 2.8 立即刷新去重依据）。
        # 用锁保护读写，避免主线程与后台线程并发访问出现竞态。
        self._busy_lock = threading.Lock()
        self._busy = False

        # 后台线程对象，start() 时创建。
        self._thread: Optional[threading.Thread] = None

    # --- 对外接口 ---------------------------------------------------------

    def start(self) -> None:
        """启动后台守护线程开始周期性轮询（幂等：已在运行则不重复启动）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        # 复位事件，确保重新启动时状态干净。
        self._stop_event.clear()
        self._refresh_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="QuotePoller", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """请求停止轮询（需求 2.5）：当前进行中的一轮完成后退出后续周期，不中断当前轮。

        置位 ``stop_event`` 表示停止意图，并置位 ``refresh_event`` 唤醒可能正在间隔等待的
        线程，使其尽快检查停止标志并退出，而非等到本次间隔自然到点。
        """
        self._stop_event.set()
        # 唤醒等待中的线程以尽快响应停止；若正处于计算轮，则该轮跑完后循环自然退出。
        self._refresh_event.set()

    def request_refresh(self) -> bool:
        """请求立即触发一轮行情获取（需求 2.6），忙时忽略（需求 2.8）。

        Returns:
            True 表示已接受本次立即刷新请求并唤醒后台线程；False 表示当前有一轮正在进行中
            （``busy`` 为真），本次请求被忽略。
        """
        # 需求 2.8：某一轮尚未完成时的立即刷新请求应被忽略。
        with self._busy_lock:
            if self._busy:
                return False
        # 未在忙碌：置位刷新事件，提前唤醒间隔等待，立即进入下一轮。
        self._refresh_event.set()
        return True

    def is_busy(self) -> bool:
        """返回当前是否有一轮正在进行中（供主线程查询）。"""
        with self._busy_lock:
            return self._busy

    def is_running(self) -> bool:
        """返回后台轮询线程是否处于运行状态。"""
        return self._thread is not None and self._thread.is_alive()

    # --- 后台线程主循环 ----------------------------------------------------

    def _run_loop(self) -> None:
        """后台线程主循环：间隔等待 → 触发一轮 → 循环，直至收到停止请求。"""
        while not self._stop_event.is_set():
            interval = self._current_interval()
            # 需求 2.1：以 refresh_event.wait(timeout=interval) 计时，到点或被立即刷新唤醒即返回。
            self._refresh_event.wait(timeout=interval)
            # 无论是超时到点还是被唤醒，都清除刷新标志，供下一次等待复用。
            self._refresh_event.clear()

            # 需求 2.5：被 stop() 唤醒时不再执行新的一轮，直接退出循环。
            if self._stop_event.is_set():
                break

            # 执行一轮（单轮异常/超时不会传播出来，循环得以继续）。
            self._run_one_round()

    def _run_one_round(self) -> None:
        """执行一轮行情获取与计算，并把结果入队。

        需求 2.7/10.2/10.5：本方法整体被 ``try/except`` 包裹，任何异常都被转成失败结果
        入队而不向上传播，保证后台循环不因单轮失败退出、下一间隔继续重试。
        """
        # 标记进入忙碌状态（此后到本轮结束前，request_refresh 会被忽略）。
        self._set_busy(True)
        try:
            position = self._get_position()
            if position is None:
                # 尚未配置持仓：本轮无法计算，入队失败结果并等待下一轮。
                self._result_queue.put(
                    self._error_result("尚未配置持仓信息，无法进行盯盘计算")
                )
                return
            # VariableCalculator.calculate 内部已含 10 秒超时与异常兜底，
            # 成功返回 ok=True 的结果，失败返回 ok=False 的错误结果。
            result = self._calculator.calculate(position)
            self._result_queue.put(result)
        except Exception as exc:  # noqa: BLE001  兜底任何意外异常，绝不让后台线程崩溃
            self._result_queue.put(
                self._error_result(f"本轮行情获取失败：{exc}")
            )
        finally:
            # 无论成功失败，本轮结束都要清除忙碌标志，使后续立即刷新可被接受。
            self._set_busy(False)

    # --- 内部辅助 ---------------------------------------------------------

    def _current_interval(self) -> float:
        """读取当前轮询间隔（秒）；非法值（非正数或异常）时回退到兜底间隔，避免忙等。"""
        try:
            interval = self._get_interval()
        except Exception:  # noqa: BLE001  取间隔失败不应中断轮询
            return float(_FALLBACK_INTERVAL_SECONDS)
        try:
            interval = float(interval)
        except (TypeError, ValueError):
            return float(_FALLBACK_INTERVAL_SECONDS)
        if interval <= 0:
            return float(_FALLBACK_INTERVAL_SECONDS)
        return interval

    def _set_busy(self, value: bool) -> None:
        """线程安全地设置忙碌标志。"""
        with self._busy_lock:
            self._busy = value

    @staticmethod
    def _error_result(error: str) -> RoundResult:
        """构造失败轮 ``RoundResult``：不携带变量，价格来源与派生字段均为空。"""
        return RoundResult(
            ok=False,
            vars={},
            error=error,
            price_source=None,
            macd_hist_prev=None,
            macd_hist_curr=None,
            fetched_at=datetime.now(),
        )
