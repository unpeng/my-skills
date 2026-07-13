# -*- coding: utf-8 -*-
"""Monitor_App：tkinter 主窗口与编排（交互与展示层）。

职责（对齐设计 "Monitor_App" 小节与需求 3/9/10）：
  - 组装各组件并构建 tkinter 主窗口与各视图（持仓/变量指标/时段建议/信号/外盘新闻/
    交易记录/设置）；
  - 用 ``root.after(200, ...)`` 周期性、非阻塞地消费结果队列，取出后台线程回送的
    ``RoundResult`` / ``LLMResult`` 并刷新界面（tkinter 线程安全约定：只在主线程更新
    widget）；
  - 持有 ``last_good_result``，失败轮不清空已展示数值（需求 3.8/10.4）；
  - 首次启动展示免责声明确认门，确认前不进入主界面（需求 9.3/9.4）；
  - 主界面底部 ``pack(side=BOTTOM, fill=X)`` 常驻免责声明提示条（需求 9.1/9.2）。

为便于属性测试，本模块把三个与 UI 无关的**纯函数**抽出（不触碰 tkinter）：
  - :func:`format_indicator`：指标值为 None → "数据不足暂不可用"，否则为其数值内容
    （需求 3.4）；
  - :func:`price_source_annotation`：按价格来源产出"当前价可能非实时"标注文本
    （需求 3.5/3.7/10.3/10.6）；
  - :func:`choose_display_result`：失败轮取最近一次成功轮的结果（需求 3.8/10.4）。

注意：本模块在 ``import`` 时**不**创建任何 ``tk.Tk()`` 实例（无显示环境下实例化会失败），
所有 tkinter 对象仅在 :class:`MonitorApp` 的方法内按需创建。
"""

from __future__ import annotations

import queue
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import messagebox, ttk
from typing import List, Optional

from .models import (
    AdviceLLMResult,
    LLMResult,
    PositionForm,
    RoundResult,
    TradingSession,
)
from .rule_engine import classify_session

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 标的固定为 588170，本功能不支持切换其他标的。
SYMBOL = "588170"

# 免责声明全文（需求 9.1：底部常驻提示条完整文本不被截断）。
DISCLAIMER_TEXT = "以上仅供参考，不构成投资建议，市场有风险，操作需自行判断"

# 指标数据不足时统一展示的文本（需求 3.4）。
INDICATOR_UNAVAILABLE_TEXT = "数据不足暂不可用"

# 价格来源标注文本（需求 3.5/3.7/10.3/10.6）。
# realtime 不显示任何标注；kline_fallback 与 kline_only 均提示"当前价可能非实时"，
# 其中 kline_only 额外提示用户在做出决策前核实实时行情。
_ANNOTATION_NON_REALTIME = "当前价可能非实时，请核实实时行情"
_ANNOTATION_KLINE_ONLY = "当前价可能非实时（仅历史K线收盘价，可能非最新），请在做出决策前核实实时行情"

# 变量展示区字段（需求 3.2）：按展示顺序列出 (展示标签, vars 键名)。
_VARIABLE_FIELDS = [
    ("当前价", "当前价"),
    ("昨收价", "昨收价"),
    ("止损位", "止损位"),
    ("回本价", "回本价"),
    ("做T买入位", "做T买入位"),
    ("做T卖出位", "做T卖出位"),
    ("持仓市值", "持仓市值"),
    ("浮动盈亏", "浮动盈亏"),
    ("盈亏比例(%)", "盈亏比例"),
    ("距回本(%)", "距回本"),
]

# 关键价位/当前价高亮配色：操盘时最需要一眼看到的字段用更大字号+醒目色区分于
# 其余普通字段（持仓市值/浮亏等次要展示项保持默认样式）。
# 当前价用中性醒目蓝，止损位用警示红，做T买卖位用操作色（买绿/卖橙）。
_HIGHLIGHT_VARIABLE_STYLES = {
    "当前价": "#1a5fb4",
    "止损位": "#c01c28",
    "做T买入位": "#26a269",
    "做T卖出位": "#e66100",
}

# 技术指标展示区字段（需求 3.3）：RSI、MACD、KDJ、布林带。
_INDICATOR_FIELDS = [
    ("RSI", "RSI"),
    ("MACD-DIF", "MACD_DIF"),
    ("MACD-DEA", "MACD_DEA"),
    ("MACD-HIST", "MACD_HIST"),
    ("KDJ-K", "KDJ_K"),
    ("KDJ-D", "KDJ_D"),
    ("KDJ-J", "KDJ_J"),
    ("布林上轨", "布林上轨"),
    ("布林中轨", "布林中轨"),
    ("布林下轨", "布林下轨"),
]

# 主线程消费队列的轮询周期（毫秒）。远小于需求 3.2/3.3 的 1 秒渲染上限。
_DRAIN_INTERVAL_MS = 200


# ---------------------------------------------------------------------------
# 纯函数辅助（无 tkinter 依赖，供属性测试）
# ---------------------------------------------------------------------------


def format_indicator(value) -> str:
    """把一个指标/变量值格式化为展示文本（需求 3.4）。

    Args:
        value: 指标或变量的原始值。``None`` 表示数据不足。

    Returns:
        当 ``value`` 为 ``None`` 时返回文本 "数据不足暂不可用"；否则返回其数值内容的
        字符串表示（对布尔与数值原样字符串化）。
    """
    if value is None:
        return INDICATOR_UNAVAILABLE_TEXT
    return str(value)


def price_source_annotation(price_source: Optional[str]) -> str:
    """按价格来源返回"当前价可能非实时"标注文本（需求 3.5/3.7/10.3/10.6）。

    Args:
        price_source: ``compute_monitor_variables`` 返回的价格来源标记，取值为
            ``realtime`` / ``kline_fallback`` / ``kline_only`` 之一（其余/None 视为无标注）。

    Returns:
        - ``realtime``：返回空字符串（不显示任何标注，需求 3.7）；
        - ``kline_fallback``：返回含"当前价可能非实时"的标注（需求 3.5）；
        - ``kline_only``：返回含"当前价可能非实时"且额外提示决策前核实的标注
          （需求 3.5/10.3/10.6）;
        - 其他取值（含 None）：返回空字符串。
    """
    if price_source == "kline_only":
        return _ANNOTATION_KLINE_ONLY
    if price_source == "kline_fallback":
        return _ANNOTATION_NON_REALTIME
    # realtime 或未知取值：不显示标注。
    return ""


def choose_display_result(
    last_good: Optional[RoundResult], new_result: Optional[RoundResult]
) -> Optional[RoundResult]:
    """在收到新一轮结果时决定界面应展示哪一轮的数值（需求 3.8/10.4）。

    成功轮（``ok=True``）用新结果更新展示；失败轮（``ok=False`` 或 None）保留上一次成功轮
    的展示数值，不清空、不覆盖。据此在任一时刻界面展示的恒为"该时刻之前最近一次成功轮"
    的数值。

    Args:
        last_good: 当前正在展示的（最近一次成功轮）结果，尚无成功轮时为 None。
        new_result: 本轮新到达的结果。

    Returns:
        应展示的 ``RoundResult``：新结果成功则为新结果，否则沿用 ``last_good``。
    """
    if new_result is not None and new_result.ok:
        return new_result
    return last_good


# ---------------------------------------------------------------------------
# 依赖容器
# ---------------------------------------------------------------------------


@dataclass
class AppDeps:
    """Monitor_App 运行所需的各组件依赖（由 ``main_app.py`` 组装注入）。

    以容器形式注入，便于测试时替换为 mock/stub。字段不做类型强约束（避免在本模块顶层
    引入会拉起 qstock 依赖的组件导入），运行时按约定的方法名调用。
    """

    settings: object                 # SettingsStore
    position_manager: object         # PositionManager
    trade_logger: object             # TradeLogger
    rule_engine: object              # RuleEngine
    alert_manager: object            # AlertManager
    quote_poller: object             # QuotePoller
    llm_client: object               # LLMClient
    result_queue: "queue.Queue"      # RoundResult / LLMResult 结果队列


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------


class MonitorApp:
    """tkinter 主窗口与状态编排。

    生命周期：``__init__`` 只保存依赖与 root，不构建任何界面；``run()`` 先按需展示
    首启免责声明确认门（需求 9.3/9.4），确认后再构建主界面并启动队列消费循环。
    """

    def __init__(self, root: "tk.Tk", deps: AppDeps) -> None:
        """初始化。

        Args:
            root: 由调用方创建的 ``tk.Tk()`` 主窗口（本模块不自行创建，避免无显示环境
                导入即失败）。
            deps: 各组件依赖容器。
        """
        self.root = root
        self.deps = deps

        # 最近一次成功轮的结果；失败轮不覆盖它（需求 3.8/10.4）。
        self.last_good_result: Optional[RoundResult] = None

        # 界面组件引用（在 _enter_main 中创建）。
        self._gate_frame: Optional[tk.Frame] = None
        self.disclaimer_bar: Optional[tk.Label] = None
        # 齿轮图标弹出的设置对话框（声音开关+大模型配置），懒创建、单例复用。
        self._settings_dialog: Optional[tk.Toplevel] = None
        self.var_labels: dict = {}
        self.indicator_labels: dict = {}
        self.price_annotation_label: Optional[tk.Label] = None
        self.data_quality_label: Optional[tk.Label] = None
        self.error_label: Optional[tk.Label] = None
        self.advice_label: Optional[tk.Text] = None
        self.signals_text: Optional[tk.Text] = None
        self.update_time_label: Optional[tk.Label] = None
        # 最近一次成功取数的时间，仅用于展示"数据更新时间"（需求 3.8/10.4：
        # 失败轮不覆盖上一次成功展示）。
        self._last_update_time: Optional[datetime] = None
        # 最近一次"一轮结束"（无论成功失败）的时间，用作倒计时锚点：
        # Quote_Poller 内部按"上一轮结束后等待 interval 秒"调度下一轮，
        # 若倒计时锚点只用成功轮时间，失败轮之后倒计时会与实际调度错位。
        self._last_round_at: Optional[datetime] = None
        self._countdown_job: Optional[str] = None
        self.llm_text: Optional[tk.Text] = None
        self.trade_list: Optional[tk.Listbox] = None

        # 工具栏按钮引用（用于同步"开始/暂停/立即刷新"的启用状态，需与
        # Quote_Poller 的实际运行/忙碌状态一致，而不是点击后一直保持可点）。
        self._btn_start: Optional[ttk.Button] = None
        self._btn_pause: Optional[ttk.Button] = None
        self._btn_refresh: Optional[ttk.Button] = None
        # 乐观加载标记：点击"开始"/"立即刷新"后立即置真，弥合"后台线程真正
        # 置位 busy"之间的调度延迟空档；本轮结果到达（成功或失败）后清除，
        # 使倒计时能在"加载中"提示结束后立即继续读秒（而不是等下一次 tick）。
        self._loading = False
        self._loading_started_at: Optional[datetime] = None
        # 降级兜底：Quote_Poller.is_running() 调用失败时沿用最近一次已知状态，
        # 避免按钮因单次查询异常而在"可点/不可点"间无意义抖动。
        self._last_known_running = False

        # --- 跟踪止损状态（借鉴 vnpy CTA 的 Stop Order Trailing 机制）---
        # 盯盘期间维护"当日最高价"，跟踪止损位 = max(原始止损位, 当日最高 − N×ATR)，
        # 只升不降（价格冲高后回落时锁住部分利润）。每日开盘（9:15）自动重置。
        self._trailing_high: Optional[float] = None   # 当日观测到的最高价
        self._trailing_stop: Optional[float] = None   # 当前跟踪止损位（仅 ≥ 原始止损时有效）
        self._trailing_date: Optional[str] = None     # 跟踪状态所属交易日（用于日切重置）
        # 跟踪止损的 ATR 倍数（回退距离 = N×ATR），默认 1.5 倍。
        # 若 ATR 不可用则退回原始止损位（不跟踪），保证降级可用。
        _TRAILING_ATR_N = 1.5
        self._trailing_atr_n = _TRAILING_ATR_N

        # --- LLM 增强建议（异步，降级为代码规则引擎输出）---
        from .advice_llm import AdviceLLM  # 延迟导入避免循环依赖
        self._advice_llm = AdviceLLM(get_config=deps.settings.get_llm_config)

        # 主界面是否已构建（用于测试与守卫，确认前不进入主界面——需求 9.4）。
        self.main_ui_built = False

        # 队列消费的 after 任务句柄，便于停止。
        self._drain_job: Optional[str] = None

        # 表单变量（在构建对应视图时初始化）。
        self._pos_vars: dict = {}
        self._stop_mode = None            # 止损方式单选变量
        self._trade_action = None         # 交易记录操作类型变量
        self._trade_price = None          # 交易记录成交价格变量
        self._interval_var = None         # 轮询间隔变量
        self._sound_var = None            # 声音开关变量
        self._llm_vars: dict = {}         # 大模型三项配置变量

    # ------------------------------------------------------------------
    # 启动与免责声明门（需求 9.3/9.4）
    # ------------------------------------------------------------------
    # 默认窗口尺寸（用户当前使用中实测的舒适尺寸，替代此前"按屏幕 85% 自适应"
    # 的动态计算——固定默认尺寸更符合"设置成默认"的预期，且已验证在常见屏幕
    # 分辨率下能完整容纳所有面板不裁切）。
    _DEFAULT_WIDTH = 1394
    _DEFAULT_HEIGHT = 852

    def run(self) -> None:
        """启动应用：首启需先确认免责声明，之后进入主界面。"""
        self.root.title(f"{SYMBOL} 盯盘助手")
        # 固定默认尺寸并居中显示；超大屏/小屏兜底见 _size_to_default。
        self._size_to_default()
        try:
            self.root.minsize(1024, 680)
        except Exception:  # noqa: BLE001  个别平台不支持时忽略
            pass
        # 按用户要求：去掉首启"我已阅读并确认"免责声明确认门，启动直接进入主界面。
        # 底部常驻的免责声明提示条仍保留（需求 9.1/9.2）。
        self._enter_main()
        self._bring_to_front()

    def _size_to_default(self) -> None:
        """按固定默认尺寸（``_DEFAULT_WIDTH`` x ``_DEFAULT_HEIGHT``）居中显示窗口。

        屏幕小于默认尺寸时按屏幕的 95% 收缩，避免窗口超出屏幕边界；正常情况下
        使用固定默认尺寸，不再随屏幕分辨率浮动缩放。
        """
        w, h = self._DEFAULT_WIDTH, self._DEFAULT_HEIGHT
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            w = min(w, int(screen_w * 0.95))
            h = min(h, int(screen_h * 0.95))
        except Exception:  # noqa: BLE001  取屏幕尺寸失败时直接用默认尺寸、不居中
            self.root.geometry(f"{self._DEFAULT_WIDTH}x{self._DEFAULT_HEIGHT}")
            return

        x = max(0, (screen_w - w) // 2)
        y = max(0, (screen_h - h) // 2)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _bring_to_front(self) -> None:
        """把主窗口刷新并置于最前，避免内容未绘制或被其他窗口遮挡而看似白屏。"""
        try:
            self.root.update_idletasks()
            self.root.lift()
            self.root.attributes("-topmost", True)
            # 立即取消置顶，避免长期强制悬浮遮挡其他窗口。
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:  # noqa: BLE001  置前失败不影响功能
            pass
        # macOS 系统自带 Tk 8.5 存在"窗口初次显示白屏、需手动拖动边缘才重绘"的已知缺陷。
        # 通过在启动后多次微调窗口尺寸（+1 像素再还原）强制触发一次重绘，规避白屏。
        self._force_repaint()
        self.root.after(120, self._force_repaint)
        self.root.after(400, self._force_repaint)

    def _force_repaint(self) -> None:
        """微调窗口尺寸强制 Tk 重绘（规避 macOS Tk 8.5 白屏缺陷）。"""
        try:
            self.root.update_idletasks()
            w = self.root.winfo_width()
            h = self.root.winfo_height()
            if w <= 1 or h <= 1:
                return
            self.root.geometry(f"{w + 1}x{h + 1}")
            self.root.update_idletasks()
            self.root.geometry(f"{w}x{h}")
            self.root.update_idletasks()
        except Exception:  # noqa: BLE001  重绘规避失败不影响功能
            pass

    def _show_disclaimer_gate(self) -> None:
        """构建首启免责声明确认门（需求 9.3/9.4）。

        用一个占满窗口的 Frame 承载免责声明全文与"我已阅读并确认"按钮；仅当用户点击
        确认后才写回确认位并进入主界面。未确认（含直接关闭窗口）则不会构建主界面。
        """
        frame = tk.Frame(self.root, padx=24, pady=24)
        frame.pack(fill=tk.BOTH, expand=True)
        self._gate_frame = frame

        tk.Label(
            frame,
            text="风险免责声明",
            font=("", 16, "bold"),
        ).pack(pady=(0, 12))

        tk.Label(
            frame,
            text=DISCLAIMER_TEXT,
            wraplength=460,
            justify=tk.LEFT,
        ).pack(pady=(0, 8))

        tk.Label(
            frame,
            text=(
                "本应用所有输出均基于历史行情与技术指标的规则化计算，不构成投资建议。"
                "外盘/新闻等大模型返回内容仅为不确定信息，仅供参考。"
            ),
            wraplength=460,
            justify=tk.LEFT,
            fg="#666666",
        ).pack(pady=(0, 20))

        tk.Button(
            frame,
            text="我已阅读并确认",
            command=self._on_disclaimer_acknowledged,
        ).pack()

    def _on_disclaimer_acknowledged(self) -> None:
        """用户确认免责声明：写回确认位、销毁确认门并进入主界面。"""
        self.deps.settings.acknowledge_disclaimer()
        if self._gate_frame is not None:
            self._gate_frame.destroy()
            self._gate_frame = None
        self._enter_main()

    # ------------------------------------------------------------------
    # 主界面构建
    # ------------------------------------------------------------------
    def _enter_main(self) -> None:
        """构建主界面并启动队列消费循环。"""
        self._build_main_ui()
        self.main_ui_built = True
        # 强制刷新一次布局，确保 Notebook 与各视图立即绘制（避免切换后白屏）。
        try:
            self.root.update_idletasks()
        except Exception:  # noqa: BLE001
            pass
        self._bring_to_front()
        # 启动周期性队列消费（需求 3.2/3.3：远小于 1 秒的刷新周期）。
        self._schedule_drain()
        # 自动启动后台轮询并立即触发第一轮，使打开即可见数据（否则需等一个轮询间隔）。
        self._autostart_polling()
        # 启动每秒刷新的"更新时间 + 下次更新倒计时"显示。
        self._tick_countdown()

    def _poller_is_running(self) -> bool:
        """查询 Quote_Poller 是否处于运行状态，带降级兜底。

        Quote_Poller 是后台线程封装，理论上 ``is_running()`` 不应抛异常；但为避免
        单次查询异常（如属性缺失、跨线程竞态）导致按钮状态与倒计时显示崩溃或
        剧烈抖动，查询失败时沿用最近一次已知的运行状态而非默认展示"未运行"。
        """
        try:
            running = bool(self.deps.quote_poller.is_running())
        except Exception:  # noqa: BLE001  降级：保留最近已知状态
            return self._last_known_running
        self._last_known_running = running
        return running

    def _poller_is_busy(self) -> bool:
        """查询 Quote_Poller 是否有一轮正在进行中，带降级兜底。

        查询失败时退回本地的乐观加载标记 ``self._loading``（点击"开始"/"立即
        刷新"时置真、结果到达后置假），保证即使查询异常也不会永久卡在"加载中"
        或漏显加载态。
        """
        try:
            return bool(self.deps.quote_poller.is_busy())
        except Exception:  # noqa: BLE001  降级：退回本地乐观加载标记
            return self._loading

    def _request_refresh_with_loading(self) -> bool:
        """请求立即刷新一轮，并同步置位本地乐观加载标记。

        Returns:
            True 表示请求已被 Quote_Poller 接受（本轮将立即开始）；False 表示
            被忽略（当前有一轮正在进行中）或请求过程本身异常（降级为拒绝）。
        """
        try:
            accepted = bool(self.deps.quote_poller.request_refresh())
        except Exception:  # noqa: BLE001  请求异常时降级为"未接受"，不假装成功
            accepted = False
        if accepted:
            self._loading = True
            self._loading_started_at = datetime.now()
        return accepted

    def _sync_toolbar_buttons(self) -> None:
        """按 Quote_Poller 的实际运行/忙碌状态同步"开始/暂停/立即刷新"按钮的启用状态。

        - 运行中：禁用"开始"（已在运行，无需重复启动）、启用"暂停"；
        - 未运行：启用"开始"、禁用"暂停"（未运行时也无法有意义地暂停）；
        - 忙碌（本轮进行中，含本地乐观加载态）或未运行：禁用"立即刷新"
          （未运行时没有后台线程消费刷新请求，点击无意义；忙碌时点击会被忽略，
          禁用能更直接地告知用户"当前不可操作"而非点击后被静默忽略或弹提示）。
        """
        running = self._poller_is_running()
        busy = self._poller_is_busy() or self._loading
        if self._btn_start is not None:
            self._btn_start.config(state=(tk.DISABLED if running else tk.NORMAL))
        if self._btn_pause is not None:
            self._btn_pause.config(state=(tk.NORMAL if running else tk.DISABLED))
        if self._btn_refresh is not None:
            self._btn_refresh.config(
                state=(tk.NORMAL if (running and not busy) else tk.DISABLED)
            )

    def _refresh_update_time_label(self) -> None:
        """刷新"数据更新时间 + 运行状态/倒计时"文本（需求：展示数据新鲜度）。

        三种互斥状态：
          - 未运行（已暂停）：提示"轮询已暂停"；
          - 有一轮正在进行（含刚点击尚未真正置忙的过渡态）：展示"正在获取最新
            行情…"替换秒数读数（需求：加载中要有明确提示替换读秒）；
          - 运行中且空闲：正常倒计时读数，锚点用"上一轮结束时间"
            （``_last_round_at``，无论该轮成功或失败都会更新——Quote_Poller
            内部就是按"上一轮结束后等待 interval 秒"调度下一轮，用这个锚点
            才能与实际调度保持同步，不会因为失败轮而与倒计时错位）。
        """
        if self.update_time_label is None:
            return

        updated = (
            self._last_update_time.strftime("%H:%M:%S")
            if self._last_update_time is not None
            else "--"
        )
        running = self._poller_is_running()
        busy = self._poller_is_busy() or self._loading

        if not running:
            status = "轮询已暂停（点击「开始」恢复）"
        elif busy:
            status = "正在获取最新行情…"
        elif self._last_round_at is None:
            status = "等待首轮数据…"
        else:
            try:
                interval = int(self.deps.settings.get_interval())
            except Exception:  # noqa: BLE001  降级：取间隔失败时按默认 60 秒展示
                interval = 60
            elapsed = (datetime.now() - self._last_round_at).total_seconds()
            remaining = int(max(0, round(interval - elapsed)))
            status = f"下次更新倒计时：{remaining} 秒"

        self.update_time_label.config(text=f"数据更新时间：{updated}　|　{status}")

    def _tick_countdown(self) -> None:
        """每秒刷新一次倒计时显示，并同步工具栏按钮状态（在主线程内周期执行）。

        按钮状态放在这里周期同步（而非仅在点击后一次性设置），是为了兜底
        "自动调度的轮询开始/结束"（未经用户点击按钮触发）也能及时反映到按钮
        的可用性上，例如某一轮自动开始后"立即刷新"应立刻变为不可点。
        """
        self._refresh_update_time_label()
        self._sync_toolbar_buttons()
        self._countdown_job = self.root.after(1000, self._tick_countdown)

    def _autostart_polling(self) -> None:
        """进入主界面后自动启动轮询并请求立即刷新首轮数据。"""
        try:
            self.deps.quote_poller.start()
            # 立即触发第一轮（否则要等待 refresh_event.wait(interval) 到点）。
            self._request_refresh_with_loading()
        except Exception:  # noqa: BLE001  自动启动失败不影响手动"开始轮询"
            pass
        self._sync_toolbar_buttons()

    def _build_main_ui(self) -> None:
        """构建主界面：顶部工具栏 + 单页网格仪表盘 + 底部常驻免责声明条。

        不再使用"盯盘/设置"两个 Tab：轮询间隔/开始/暂停/立即刷新统一放在顶部
        工具栏（一直可见、无需切换页签）；声音开关与大模型三项配置移入工具栏
        右上角齿轮图标点击后弹出的独立设置对话框（不常用、点击后才展示）。
        """
        # 需求 9.1/9.2：底部提示条先以 side=BOTTOM 常驻布局，确保缩放/滚动不遮挡、不被截断。
        self.disclaimer_bar = tk.Label(
            self.root,
            text=DISCLAIMER_TEXT,
            anchor=tk.W,
            bg="#f5f5dc",
            fg="#8a6d3b",
            padx=8,
            pady=4,
        )
        self.disclaimer_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 顶部工具栏：轮询间隔 + 开始/暂停/立即刷新 + 右侧齿轮设置入口。
        self._build_toolbar(self.root)

        board = ttk.Frame(self.root, padding=8)
        board.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 盯盘页按功能上下分区：上方为只读展示区、下方为可交互输入区，中间以分隔线区隔。
        # 左右两列布局：
        #   左列：上=变量指标、下=外盘新闻
        #   右列：上→下 = 时段建议、信号、持仓录入
        board.rowconfigure(0, weight=1)
        # 左右两列等宽（此前左列 weight=3:2 明显偏宽，把持仓录入等右列内容挤
        # 得过窄）；变量指标区内容本身不多，等宽即可容纳，不需要额外偏重左列。
        board.columnconfigure(0, weight=1, uniform="cols")
        board.columnconfigure(1, weight=1, uniform="cols")

        # ----- 左列 -----
        left = ttk.Frame(board)
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=3)   # 变量指标较高
        left.rowconfigure(1, weight=2)   # 外盘新闻

        var_frame = ttk.LabelFrame(left, text="变量指标（只读）", padding=10)
        var_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        market_frame = ttk.LabelFrame(left, text="外盘新闻", padding=10)
        market_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)

        # ----- 右列 -----
        right = ttk.Frame(board)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        # 持仓录入改为左右两列后所需高度大幅降低，把空间让给时段建议/信号
        # （这两块是操盘时最需要持续关注的核心信息，理应占更大展示面积）。
        right.rowconfigure(0, weight=3)   # 时段建议（加大）
        right.rowconfigure(1, weight=3)   # 信号（加大）
        right.rowconfigure(2, weight=0)   # 持仓录入：内容定高，不参与额外空间分配

        advice_frame = ttk.LabelFrame(right, text="时段建议（只读）", padding=10)
        advice_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        signals_frame = ttk.LabelFrame(right, text="信号（只读）", padding=10)
        signals_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        pos_frame = ttk.LabelFrame(right, text="持仓录入", padding=10)
        pos_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=6)

        # 各面板把内容填充进对应的分区容器。
        self._build_variables_view(var_frame)
        self._build_market_view(market_frame)
        self._build_advice_view(advice_frame)
        self._build_signals_view(signals_frame)
        self._build_position_view(pos_frame)

        # 启动回填持仓（需求 1.2）。
        self._reload_position_form()

    # --- 顶部工具栏：轮询控制 + 齿轮设置入口 -----------------------------
    def _build_toolbar(self, parent) -> None:
        """构建顶部常驻工具栏。

        左侧：轮询间隔输入框 + 开始/暂停/立即刷新按钮（原设置页内容，改为
        随时可见、无需切换页签）。
        右侧：齿轮图标 ⚙ 按钮，点击弹出独立设置对话框（声音开关 + 大模型
        三项配置，这些是不常用的一次性配置项，弹窗承载更合适）。
        """
        toolbar = ttk.Frame(parent, padding=(8, 6))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        settings = self.deps.settings
        ttk.Label(toolbar, text="轮询间隔(秒)").pack(side=tk.LEFT, padx=(0, 4))
        self._interval_var = tk.StringVar(value=str(settings.get_interval()))
        ttk.Entry(toolbar, textvariable=self._interval_var, width=8).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(
            toolbar, text="应用间隔", command=self._on_apply_interval
        ).pack(side=tk.LEFT, padx=(0, 12))

        # 开始/暂停/立即刷新按钮引用需保留，以便按 Quote_Poller 实际运行/忙碌
        # 状态同步启用与禁用（而不是点击后一直保持可点、与真实状态脱节）。
        self._btn_start = ttk.Button(toolbar, text="▶ 开始", command=self._on_start_poll)
        self._btn_start.pack(side=tk.LEFT, padx=(0, 6))
        self._btn_pause = ttk.Button(toolbar, text="⏸ 暂停", command=self._on_stop_poll)
        self._btn_pause.pack(side=tk.LEFT, padx=(0, 6))
        self._btn_refresh = ttk.Button(
            toolbar, text="⟳ 立即刷新", command=self._on_refresh_now
        )
        self._btn_refresh.pack(side=tk.LEFT, padx=(0, 6))

        # 右上角齿轮设置入口（声音开关 + 大模型三项配置）。
        ttk.Button(
            toolbar, text="⚙", width=3, command=self._open_settings_dialog
        ).pack(side=tk.RIGHT)

    def _on_apply_interval(self) -> None:
        """工具栏"应用间隔"：校验并持久化轮询间隔（需求 2.3/2.4），非法则回填原值。"""
        settings = self.deps.settings
        raw = (self._interval_var.get() or "").strip()
        interval_val: object = raw
        try:
            if raw and float(raw).is_integer():
                interval_val = int(float(raw))
        except (TypeError, ValueError):
            interval_val = raw
        res = settings.set_interval(interval_val)
        if not res.ok:
            messagebox.showerror("轮询间隔未保存", res.message)
            self._interval_var.set(str(settings.get_interval()))
            return
        messagebox.showinfo("轮询间隔", res.message)
        # 新间隔立即影响倒计时展示（Quote_Poller 每轮都实时读取间隔配置，
        # 下一轮即会按新值调度；这里仅是让倒计时读数不必等到下一次 tick 才刷新）。
        self._refresh_update_time_label()

    def _open_settings_dialog(self) -> None:
        """点击齿轮图标：弹出设置对话框（声音开关 + 大模型三项配置）。

        单例复用：已打开则直接提到最前，不重复创建多个窗口。
        """
        if self._settings_dialog is not None:
            try:
                if self._settings_dialog.winfo_exists():
                    self._settings_dialog.lift()
                    self._settings_dialog.focus_force()
                    return
            except Exception:  # noqa: BLE001  引用失效则重新创建
                pass

        dialog = tk.Toplevel(self.root)
        dialog.title("设置")
        dialog.resizable(False, False)
        # 独立小窗口，不阻塞主窗口交互（非 modal），关闭后清空引用以便下次重建。
        dialog.protocol("WM_DELETE_WINDOW", lambda: self._close_settings_dialog(dialog))
        self._settings_dialog = dialog

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        self._build_settings_form(frame)

        try:
            dialog.transient(self.root)
            dialog.update_idletasks()
            # 相对主窗口居中弹出。
            rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
            rw, rh = self.root.winfo_width(), self.root.winfo_height()
            dw, dh = dialog.winfo_width(), dialog.winfo_height()
            dialog.geometry(f"+{rx + max(0, (rw - dw)//2)}+{ry + max(0, (rh - dh)//2)}")
        except Exception:  # noqa: BLE001  定位失败不影响弹窗可用性
            pass

    def _close_settings_dialog(self, dialog: "tk.Toplevel") -> None:
        dialog.destroy()
        self._settings_dialog = None

    # --- 持仓视图（需求 1.x）---------------------------------------------
    def _build_position_view(self, parent) -> None:
        """构建持仓录入表单：左右两列布局，降低整体高度，把空间让给时段建议/信号。

        左列：基本信息（持仓数量/加权成本/可用资金）；
        右列：止损设定四选一（比例/金额/直接指定价/ATR止损倍数）。
        ATR 止损（借鉴 abu）：止损位=成本−N×ATR，波动越大止损越宽、不易被日内
        震荡扫出；需 ATR 数据可用（历史K线足够），否则展示时会说明暂不可用。
        """
        frame = parent
        frame.columnconfigure(0, weight=0)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(2, weight=0)
        frame.columnconfigure(3, weight=1)

        self._pos_vars = {
            "position": tk.StringVar(),
            "cost": tk.StringVar(),
            "cash": tk.StringVar(),
            "max_loss_pct": tk.StringVar(),
            "max_loss_amount": tk.StringVar(),
            "stop_loss_price": tk.StringVar(),
            "atr_stop_n": tk.StringVar(),
        }
        self._stop_mode = tk.StringVar(value="max_loss_pct")

        # 左列：基本信息（3 行）。
        ttk.Label(frame, text="基本信息", font=("", 9, "bold")).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 2)
        )
        left_rows = [
            ("持仓数量（份）", "position"),
            ("加权成本", "cost"),
            ("可用资金", "cash"),
        ]
        for i, (label, key) in enumerate(left_rows):
            ttk.Label(frame, text=label).grid(row=1 + i, column=0, sticky=tk.W, pady=2)
            ttk.Entry(frame, textvariable=self._pos_vars[key], width=14).grid(
                row=1 + i, column=1, sticky=tk.W, pady=2, padx=(4, 12)
            )

        # 右列：止损设定四选一（需求 1.5，4 行）。
        ttk.Label(frame, text="止损设定（四选一）", font=("", 9, "bold")).grid(
            row=0, column=2, columnspan=2, sticky=tk.W, pady=(0, 2)
        )
        stop_rows = [
            ("最大亏损比例(%)", "max_loss_pct"),
            ("最大亏损金额", "max_loss_amount"),
            ("直接指定止损价", "stop_loss_price"),
            ("ATR止损倍数N", "atr_stop_n"),
        ]
        for j, (label, key) in enumerate(stop_rows):
            ttk.Radiobutton(
                frame, text=label, variable=self._stop_mode, value=key
            ).grid(row=1 + j, column=2, sticky=tk.W, pady=2)
            ttk.Entry(frame, textvariable=self._pos_vars[key], width=14).grid(
                row=1 + j, column=3, sticky=tk.W, pady=2, padx=(4, 0)
            )

        # 保存按钮放在两列下方（右列现有 4 行，取 row=5 让按钮位于两列内容之下）。
        ttk.Button(frame, text="保存持仓", command=self._on_save_position).grid(
            row=5, column=0, columnspan=4, pady=(8, 0)
        )

    def _reload_position_form(self) -> None:
        """启动回填已保存的持仓信息（需求 1.2）。"""
        try:
            pos = self.deps.position_manager.load()
        except Exception:  # noqa: BLE001  回填失败不应阻断界面
            pos = None
        if pos is None:
            return
        self._pos_vars["position"].set(str(pos.position))
        self._pos_vars["cost"].set(str(pos.cost))
        self._pos_vars["cash"].set(str(pos.cash))
        if pos.max_loss_pct is not None:
            self._pos_vars["max_loss_pct"].set(str(pos.max_loss_pct))
            self._stop_mode.set("max_loss_pct")
        elif pos.max_loss_amount is not None:
            self._pos_vars["max_loss_amount"].set(str(pos.max_loss_amount))
            self._stop_mode.set("max_loss_amount")
        elif pos.stop_loss_price is not None:
            self._pos_vars["stop_loss_price"].set(str(pos.stop_loss_price))
            self._stop_mode.set("stop_loss_price")
        elif pos.atr_stop_n is not None:
            self._pos_vars["atr_stop_n"].set(str(pos.atr_stop_n))
            self._stop_mode.set("atr_stop_n")

    def _on_save_position(self) -> None:
        """保存持仓：仅提交所选止损方式对应字段，其余止损字段置空（需求 1.1/1.5）。"""
        mode = self._stop_mode.get()
        form = PositionForm(
            position=self._pos_vars["position"].get(),
            cost=self._pos_vars["cost"].get(),
            cash=self._pos_vars["cash"].get(),
            max_loss_pct=self._pos_vars["max_loss_pct"].get() if mode == "max_loss_pct" else "",
            max_loss_amount=self._pos_vars["max_loss_amount"].get() if mode == "max_loss_amount" else "",
            stop_loss_price=self._pos_vars["stop_loss_price"].get() if mode == "stop_loss_price" else "",
            atr_stop_n=self._pos_vars["atr_stop_n"].get() if mode == "atr_stop_n" else "",
        )
        result = self.deps.position_manager.save(form)
        if result.ok:
            messagebox.showinfo("持仓保存", result.message)
        else:
            # 校验失败时列出各字段错误；写盘失败时展示失败提示（需求 1.3/1.4/1.7/1.8）。
            detail = result.message
            if result.validation is not None and result.validation.errors:
                detail += "\n" + "\n".join(
                    f"· {msg}" for msg in result.validation.errors.values()
                )
            messagebox.showerror("持仓保存失败", detail)

    # --- 变量/指标视图（需求 3.x/10.x）----------------------------------
    def _build_variables_view(self, parent) -> None:
        frame = parent

        # 数据更新时间 + 下次更新倒计时（每秒刷新，见 _tick_countdown）。
        self.update_time_label = tk.Label(
            frame, text="数据更新时间：--　|　下次更新倒计时：--",
            fg="#31708f", anchor=tk.W, justify=tk.LEFT, font=("", 11, "bold"),
        )
        self.update_time_label.grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 6))

        # 错误提示（失败轮展示，需求 3.8/10.1）。
        self.error_label = tk.Label(frame, text="", fg="#a94442", anchor=tk.W, justify=tk.LEFT)
        self.error_label.grid(row=1, column=0, columnspan=4, sticky=tk.W, pady=(0, 4))

        # 当前价非实时标注（需求 3.5/3.7/10.3/10.6）。
        self.price_annotation_label = tk.Label(
            frame, text="", fg="#8a6d3b", anchor=tk.W, justify=tk.LEFT, wraplength=520
        )
        self.price_annotation_label.grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(0, 4))

        # 数据质量说明（拆分跳空，需求 3.6）。
        self.data_quality_label = tk.Label(
            frame, text="", fg="#8a6d3b", anchor=tk.W, justify=tk.LEFT, wraplength=520
        )
        self.data_quality_label.grid(row=3, column=0, columnspan=4, sticky=tk.W, pady=(0, 8))

        # 关键变量区（需求 3.2）。
        ttk.Label(frame, text="关键价位与盈亏", font=("", 11, "bold")).grid(
            row=4, column=0, columnspan=4, sticky=tk.W, pady=(0, 4)
        )
        for idx, (label, key) in enumerate(_VARIABLE_FIELDS):
            r = 5 + idx // 2
            c = (idx % 2) * 2
            style = _HIGHLIGHT_VARIABLE_STYLES.get(key)
            label_font = ("", 12, "bold") if style else ("", 10)
            ttk.Label(frame, text=label + "：", font=label_font).grid(
                row=r, column=c, sticky=tk.W, pady=3
            )
            if style is not None:
                # 高亮字段（当前价/止损位/做T买卖位）：用 tk.Label 加大字号+
                # 醒目前景色，与其余普通字段区分，避免关键信息被忽略。
                value_label = tk.Label(
                    frame, text="-", width=18, anchor=tk.W,
                    font=("", 13, "bold"), fg=style,
                )
            else:
                value_label = ttk.Label(frame, text="-", width=18, anchor=tk.W)
            value_label.grid(row=r, column=c + 1, sticky=tk.W, pady=3)
            self.var_labels[key] = value_label

        base = 5 + (len(_VARIABLE_FIELDS) + 1) // 2
        # 技术指标区（需求 3.3）。
        ttk.Label(frame, text="技术指标", font=("", 11, "bold")).grid(
            row=base, column=0, columnspan=4, sticky=tk.W, pady=(10, 4)
        )
        for idx, (label, key) in enumerate(_INDICATOR_FIELDS):
            r = base + 1 + idx // 2
            c = (idx % 2) * 2
            ttk.Label(frame, text=label + "：").grid(row=r, column=c, sticky=tk.W, pady=2)
            value_label = ttk.Label(frame, text="-", width=18, anchor=tk.W)
            value_label.grid(row=r, column=c + 1, sticky=tk.W, pady=2)
            self.indicator_labels[key] = value_label

    # --- 时段建议视图（需求 4.x）----------------------------------------
    def _build_advice_view(self, parent) -> None:
        frame = parent
        # 时段建议是核心信息：用较大加粗字体、醒目底色/前景色，避免被忽略；
        # 只读 Text 自动按宽度换行，避免定宽 Label 截断。
        self.advice_label = tk.Text(
            frame, height=6, wrap=tk.WORD, state=tk.DISABLED,
            font=("", 14, "bold"), fg="#0b6b3a", bg="#f0fff4",
            relief=tk.FLAT, padx=8, pady=6,
        )
        self.advice_label.pack(fill=tk.BOTH, expand=True)
        self._set_text(self.advice_label, "等待行情数据…")

    # --- 信号视图（需求 5.x/6.x）----------------------------------------
    def _build_signals_view(self, parent) -> None:
        frame = parent
        # 信号同样是核心信息：加大加粗、醒目底色，触发时更易被注意到。
        self.signals_text = tk.Text(
            frame, height=6, wrap=tk.WORD, state=tk.DISABLED,
            font=("", 14, "bold"), fg="#8a1f11", bg="#fff5f5",
            relief=tk.FLAT, padx=8, pady=6,
        )
        self.signals_text.pack(fill=tk.BOTH, expand=True)

    # --- 外盘/新闻视图（需求 7.x）---------------------------------------
    def _build_market_view(self, parent) -> None:
        frame = parent
        ttk.Button(
            frame, text="获取外盘与新闻信息", command=self._on_fetch_market
        ).pack(anchor=tk.W, pady=(0, 6))
        self.llm_text = tk.Text(frame, height=8, wrap=tk.WORD, state=tk.DISABLED)
        self.llm_text.pack(fill=tk.BOTH, expand=True)

    def _on_fetch_market(self) -> None:
        """点击获取外盘与新闻：先门控配置完整性（需求 7.8），再后台发起。"""
        cfg = self.deps.settings.get_llm_config()
        if not cfg.is_complete():
            messagebox.showwarning(
                "未配置大模型接口",
                "请先在设置中完整填写大模型接口地址、API 密钥与模型名称。",
            )
            return
        self._set_text(self.llm_text, "正在获取外盘与新闻信息，请稍候…")
        # 在 LLM_Client 的后台线程中运行，结果经共享队列回主线程展示（需求 7.2/7.11）。
        self.deps.llm_client.start_async(self.deps.result_queue)

    # --- 交易记录视图（需求 8.x）----------------------------------------
    def _build_trades_view(self, parent) -> None:
        frame = parent

        form = ttk.Frame(frame)
        form.pack(fill=tk.X)
        ttk.Label(form, text="操作类型").grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self._trade_action = tk.StringVar(value="做T买入")
        ttk.Combobox(
            form,
            textvariable=self._trade_action,
            values=["做T买入", "做T卖出", "止损", "减仓"],
            state="readonly",
            width=10,
        ).grid(row=0, column=1, sticky=tk.W, padx=(0, 8))
        ttk.Label(form, text="成交价格").grid(row=0, column=2, sticky=tk.W, padx=(0, 4))
        self._trade_price = tk.StringVar()
        ttk.Entry(form, textvariable=self._trade_price, width=12).grid(
            row=0, column=3, sticky=tk.W, padx=(0, 8)
        )
        ttk.Button(form, text="保存记录", command=self._on_add_trade).grid(
            row=0, column=4, sticky=tk.W
        )

        self.trade_list = tk.Listbox(frame, height=8)
        self.trade_list.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

    def _on_add_trade(self) -> None:
        """保存一条交易记录（需求 8.1/8.2/8.3）。"""
        action = self._trade_action.get()
        raw_price = (self._trade_price.get() or "").strip()
        price: Optional[float]
        if not raw_price:
            price = None
        else:
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                # 交给 Trade_Logger 校验拒绝；此处传原始不可解析值触发错误提示。
                messagebox.showerror("保存失败", "成交价格必须为大于 0 的数值")
                return
        result = self.deps.trade_logger.add(action, price)
        if result.ok:
            messagebox.showinfo("交易记录", result.message)
            self._trade_price.set("")
            self._reload_trades()
        else:
            messagebox.showerror("保存失败", result.message)

    def _reload_trades(self) -> None:
        """读取并展示交易记录，无记录显示空状态（需求 8.4/8.5）。"""
        if self.trade_list is None:
            return
        self.trade_list.delete(0, tk.END)
        try:
            entries = self.deps.trade_logger.list(limit=50)
        except Exception:  # noqa: BLE001
            entries = []
        if not entries:
            self.trade_list.insert(tk.END, "暂无交易记录")
            return
        for e in entries:
            self.trade_list.insert(
                tk.END, f"{e.time}  {e.action}  价格 {e.price}"
            )

    # --- 齿轮设置对话框内容（需求 6.6/7.1）：声音开关 + 大模型三项配置 -----
    def _build_settings_form(self, frame) -> None:
        """在齿轮弹出的设置对话框中构建表单：声音开关 + 大模型三项配置。

        轮询间隔与开始/暂停/立即刷新已移至顶部工具栏常驻展示，不再放在本
        对话框中；本对话框只保留不常用的一次性配置项。
        """
        settings = self.deps.settings

        # 声音开关（需求 6.6）。
        self._sound_var = tk.BooleanVar(value=settings.is_sound_enabled())
        ttk.Checkbutton(frame, text="启用声音提醒", variable=self._sound_var).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, pady=3
        )

        # 大模型三项配置（需求 7.1）。
        ttk.Label(frame, text="大模型接口配置", font=("", 10, "bold")).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=(10, 3)
        )
        cfg = settings.get_llm_config()
        self._llm_vars = {
            "base_url": tk.StringVar(value=cfg.base_url),
            "api_key": tk.StringVar(value=cfg.api_key),
            "model": tk.StringVar(value=cfg.model),
        }
        llm_rows = [
            ("接口地址", "base_url"),
            ("API 密钥", "api_key"),
            ("模型名称", "model"),
        ]
        for i, (label, key) in enumerate(llm_rows):
            ttk.Label(frame, text=label).grid(row=2 + i, column=0, sticky=tk.W, pady=3)
            show = "*" if key == "api_key" else None
            ttk.Entry(
                frame, textvariable=self._llm_vars[key], width=36, show=show
            ).grid(row=2 + i, column=1, sticky=tk.W, pady=3)

        ttk.Button(frame, text="保存设置", command=self._on_save_settings).grid(
            row=5, column=0, columnspan=2, pady=(12, 0)
        )

    def _on_save_settings(self) -> None:
        """保存设置弹窗中的内容：声音开关（6.6）、大模型三项（7.1）。"""
        settings = self.deps.settings
        settings.set_sound_enabled(bool(self._sound_var.get()))
        from .models import LLMConfig  # 局部导入，避免顶层耦合
        settings.set_llm_config(
            LLMConfig(
                base_url=self._llm_vars["base_url"].get().strip(),
                api_key=self._llm_vars["api_key"].get().strip(),
                model=self._llm_vars["model"].get().strip(),
            )
        )
        messagebox.showinfo("设置", "设置已保存。")

    def _on_start_poll(self) -> None:
        """点击"开始"：启动轮询并立即触发首轮刷新，随后同步按钮状态与倒计时。"""
        try:
            self.deps.quote_poller.start()
        except Exception as exc:  # noqa: BLE001  启动异常时明确提示而非静默失败
            messagebox.showerror("开始轮询失败", f"无法启动轮询：{exc}")
            return
        # 启动后立即触发一轮，避免用户点击"开始"后要空等一个完整间隔才看到数据。
        self._request_refresh_with_loading()
        self._sync_toolbar_buttons()
        self._refresh_update_time_label()

    def _on_stop_poll(self) -> None:
        """点击"暂停"：停止轮询（当前进行中的一轮跑完后不再继续），同步按钮状态。"""
        try:
            self.deps.quote_poller.stop()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("暂停轮询失败", f"无法暂停轮询：{exc}")
            return
        self._sync_toolbar_buttons()
        self._refresh_update_time_label()

    def _on_refresh_now(self) -> None:
        """点击"立即刷新"：请求立即触发一轮，忙时/未运行时给出明确提示（需求 2.8）。"""
        if not self._poller_is_running():
            messagebox.showinfo("立即刷新", "轮询未运行，请先点击「开始」。")
            return
        accepted = self._request_refresh_with_loading()
        if not accepted:
            messagebox.showinfo("立即刷新", "当前轮次尚未完成，已忽略本次立即刷新请求。")
        self._sync_toolbar_buttons()
        self._refresh_update_time_label()

    # ------------------------------------------------------------------
    # 队列消费与渲染（root.after 周期驱动）
    # ------------------------------------------------------------------
    def _schedule_drain(self) -> None:
        """安排下一次队列消费（在主线程内、非阻塞）。"""
        self._drain_job = self.root.after(_DRAIN_INTERVAL_MS, self._drain_queue)

    def _drain_queue(self) -> None:
        """非阻塞消费结果队列，取出 RoundResult / LLMResult 并刷新界面。"""
        try:
            while True:
                try:
                    item = self.deps.result_queue.get_nowait()
                except queue.Empty:
                    break
                self._dispatch(item)
        finally:
            # 无论本轮是否取到结果，都继续安排下一次消费。
            self._schedule_drain()

    def _dispatch(self, item) -> None:
        """按结果类型分派渲染。"""
        if isinstance(item, RoundResult):
            self._on_round_result(item)
        elif isinstance(item, LLMResult):
            self._render_llm(item)
        elif isinstance(item, AdviceLLMResult):
            self._on_advice_llm_result(item)
        # 其他类型静默忽略，避免异常中断消费循环。

    def _on_round_result(self, r: RoundResult) -> None:
        """处理一轮行情结果：失败轮保留上一轮展示、成功轮全量刷新（需求 3.x/10.x）。

        无论本轮成功或失败，都要：
          - 清除本地乐观加载标记（``_loading``），使倒计时能从"加载中"提示
            切回正常读秒，而不是永远卡在加载态；
          - 更新 ``_last_round_at`` 作为下一次倒计时的锚点（Quote_Poller 是
            "上一轮结束后等待 interval 秒"调度下一轮，失败轮同样会推进下一轮
            的调度时刻，倒计时锚点必须与之同步，否则会显示与实际不一致的读数）。
        """
        self._loading = False
        self._loading_started_at = None
        self._last_round_at = r.fetched_at
        self._sync_toolbar_buttons()

        if not r.ok:
            # 需求 3.8/10.1/10.4：展示错误提示并保留上一轮成功展示数据不清空。
            if self.error_label is not None:
                self.error_label.config(text=f"数据获取失败：{r.error or '未知错误'}")
            self._refresh_update_time_label()
            return

        # 成功轮：清空错误提示、更新最近成功轮、刷新全部展示。
        if self.error_label is not None:
            self.error_label.config(text="")
        # 记录本轮成功取数时间，仅用于展示"数据更新时间"文本。
        self._last_update_time = r.fetched_at
        self._refresh_update_time_label()
        display = choose_display_result(self.last_good_result, r)
        self.last_good_result = display
        self._render_variables(display)
        self._update_trailing_stop(display)
        self._render_advice(display)
        self._render_signals(display)

    def _update_trailing_stop(self, r: RoundResult) -> None:
        """更新跟踪止损状态：维护当日最高价并计算跟踪止损位（只升不降）。

        借鉴 vnpy CTA 策略中 Trailing Stop 的设计：
        - 当日最高价 = max(历史所有轮次的当前价)
        - 跟踪止损位 = 当日最高 − N×ATR
        - 有效止损位 = max(原始止损位, 跟踪止损位)，即止损位只升不降

        日切逻辑：以本轮时间日期与记录的 _trailing_date 比较，日期变化时重置
        （新交易日的价格走势与前一日无关，不能继承前一日的最高价）。

        降级：ATR 不可用（数据不足 / 为 None）时，跟踪止损不生效，退回原始止损位。
        """
        vars_ = r.vars or {}
        cur = vars_.get("当前价")
        atr = vars_.get("ATR")
        today = r.fetched_at.strftime("%Y-%m-%d")

        # 日切重置：新的一天不继承前一天的最高价。
        if self._trailing_date != today:
            self._trailing_high = None
            self._trailing_stop = None
            self._trailing_date = today

        if cur is None:
            return

        try:
            cur_f = float(cur)
        except (TypeError, ValueError):
            return

        # 更新当日最高价（只升不降）。
        if self._trailing_high is None or cur_f > self._trailing_high:
            self._trailing_high = cur_f

        # 计算跟踪止损位（当日最高 − N×ATR）。
        if atr is not None:
            try:
                atr_f = float(atr)
                if atr_f > 0:
                    trail = self._trailing_high - self._trailing_atr_n * atr_f
                    # 只升不降：与当前跟踪止损位取 max。
                    if self._trailing_stop is None or trail > self._trailing_stop:
                        self._trailing_stop = trail
            except (TypeError, ValueError):
                pass

    def _effective_stop_loss(self, vars_: dict) -> Optional[float]:
        """计算有效止损位 = max(原始止损位, 跟踪止损位)。

        - 原始止损位由 compute_monitor_variables 按用户选择的方式计算（比例/金额/ATR/指定价）。
        - 跟踪止损位由 _update_trailing_stop 维护（当日最高−N×ATR，只升不降）。
        - 取二者的 max 作为最终有效止损位传给信号判断，保证"止损位只升不降"。
        - 任一为 None 时取另一方；二者均 None 返回 None（保持原有行为）。
        """
        import math

        def _num(v):
            if v is None:
                return None
            try:
                f = float(v)
                return None if math.isnan(f) else f
            except (TypeError, ValueError):
                return None

        original = _num(vars_.get("止损位"))
        trailing = _num(self._trailing_stop)

        if original is not None and trailing is not None:
            return max(original, trailing)
        if trailing is not None:
            return trailing
        return original

    def _render_variables(self, r: RoundResult) -> None:
        """渲染关键变量、技术指标、价格来源标注与数据质量说明（需求 3.2-3.7/10.3/10.6）。"""
        vars_ = r.vars or {}
        for key, label in self.var_labels.items():
            label.config(text=format_indicator(vars_.get(key)))
        for key, label in self.indicator_labels.items():
            label.config(text=format_indicator(vars_.get(key)))

        # 价格来源标注（需求 3.5/3.7/10.3/10.6）。
        if self.price_annotation_label is not None:
            self.price_annotation_label.config(
                text=price_source_annotation(r.price_source)
            )

        # 数据质量说明：检测到拆分跳空时展示原文（需求 3.6）。
        if self.data_quality_label is not None:
            if vars_.get("_数据质量_检测到拆分跳空"):
                self.data_quality_label.config(text=str(vars_.get("_数据质量_说明") or ""))
            else:
                self.data_quality_label.config(text="")

    def _render_advice(self, r: RoundResult) -> None:
        """依据当前交易时段渲染时段建议（需求 4.x）+ 异步 LLM 综合研判增强。"""
        if self.advice_label is None:
            return
        now = datetime.now()
        # 交易日近似：周一至周五视为交易日（MVP 近似，见设计标注的可增强点）。
        is_trading_day = now.weekday() < 5
        session = classify_session(now, is_trading_day)
        advice = self.deps.rule_engine.session_advice(session, r.vars or {})
        header = f"【{session.value}】"
        rule_text = f"{header} {advice.advice_text}"
        # 立即展示规则引擎文本（保证无 LLM 时也有内容）。
        self._set_text(self.advice_label, rule_text)

        # 异步 LLM 增强：后台调用，结果经队列回主线程覆盖展示（降级：LLM 失败时保留规则文本）。
        vars_ = dict(r.vars or {})
        effective_stop = self._effective_stop_loss(vars_)
        self._fire_advice_llm(
            kind="advice",
            session_name=session.value,
            scenario=advice.scenario,
            rule_advice=advice.advice_text,
            vars_=vars_,
            effective_stop=effective_stop,
        )

        # 盘中时段额外触发技术面解读 LLM 增强（展示在 advice 面板后追加，非独立面板）。
        from .models import TradingSession as TS
        if session is TS.INTRADAY:
            self._fire_advice_llm(kind="intraday_analysis", vars_=vars_)

    def _render_signals(self, r: RoundResult) -> None:
        """渲染信号并交由 Alert_Manager 去重与提醒（需求 5.x/6.x）。"""
        vars_ = dict(r.vars or {})
        # 把派生 MACD 柱值并入变量字典供信号判断使用。
        vars_.setdefault("macd_hist_prev", r.macd_hist_prev)
        vars_.setdefault("macd_hist_curr", r.macd_hist_curr)

        # 跟踪止损：用有效止损位（max(原始, 跟踪)）替换 vars 中的止损位，
        # 使信号判断（_eval_stop_loss）以跟踪后的更紧止损线为基准。
        effective_stop = self._effective_stop_loss(vars_)
        if effective_stop is not None:
            vars_["止损位"] = effective_stop

        now = datetime.now()
        signals = self.deps.rule_engine.evaluate_signals(vars_)
        alerts = self.deps.alert_manager.process(signals, vars_, now)

        lines: List[str] = []
        if signals:
            for s in signals:
                reasons = "；".join(s.reasons) if s.reasons else ""
                lines.append(
                    f"[{s.kind}] 触发价 {s.trigger_price} "
                    f"{('（' + reasons + '）') if reasons else ''}"
                )
        else:
            lines.append("本轮无买入/卖出/止损信号。")

        for a in alerts:
            lines.append(f"⚠ 提醒：{a.signal_kind} @ {a.trigger_price}（{a.triggered_at:%H:%M:%S}）")

        # 跟踪止损状态展示（让用户知道止损位是否已被上移）。
        original_stop = (r.vars or {}).get("止损位")
        if self._trailing_stop is not None and original_stop is not None:
            try:
                orig_f = float(original_stop)
                if self._trailing_stop > orig_f:
                    lines.append(
                        f"📈 跟踪止损已上移：原始止损位 {orig_f:.4f} → "
                        f"有效止损位 {self._trailing_stop:.4f}（当日最高 {self._trailing_high:.4f}）"
                    )
            except (TypeError, ValueError):
                pass

        self._set_text(self.signals_text, "\n".join(lines))

        # 异步 LLM 信号操作指导：仅在有信号触发时调用（无信号时不浪费 LLM 调用）。
        if signals:
            signals_text_for_llm = "\n".join(
                f"[{s.kind}] 触发价{s.trigger_price}（{'；'.join(s.reasons)}）"
                for s in signals
            )
            self._fire_advice_llm(
                kind="signal_guidance",
                signals_text=signals_text_for_llm,
                vars_=vars_,
            )

        # 触发信号时以弹窗强化提示（需求 6.1）。
        for a in alerts:
            if a.signal_kind in ("买入", "卖出", "止损", "放量下跌止损"):
                messagebox.showwarning(
                    "交易信号提醒",
                    f"信号类型：{a.signal_kind}\n标的：{a.symbol}\n"
                    f"触发价格：{a.trigger_price}\n触发时间：{a.triggered_at:%Y-%m-%d %H:%M:%S}",
                )

    def _fire_advice_llm(self, kind: str, **kwargs) -> None:
        """在后台线程异步调用 AdviceLLM，结果经队列回主线程展示。

        kind: "advice" | "signal_guidance" | "intraday_analysis"
        kwargs: 各种参数（session_name, scenario, rule_advice, vars_, signals_text, effective_stop）
        """
        import concurrent.futures

        def _worker():
            try:
                if kind == "advice":
                    text = self._advice_llm.generate_advice(
                        session=kwargs.get("session_name", ""),
                        scenario=kwargs.get("scenario"),
                        rule_advice=kwargs.get("rule_advice", ""),
                        vars_=kwargs.get("vars_", {}),
                        effective_stop=kwargs.get("effective_stop"),
                    )
                elif kind == "signal_guidance":
                    text = self._advice_llm.generate_signal_guidance(
                        signals_text=kwargs.get("signals_text", ""),
                        vars_=kwargs.get("vars_", {}),
                    )
                elif kind == "intraday_analysis":
                    text = self._advice_llm.generate_intraday_analysis(
                        vars_=kwargs.get("vars_", {}),
                    )
                else:
                    text = None
            except Exception:  # noqa: BLE001  任何异常都降级为 None
                text = None
            self.deps.result_queue.put(AdviceLLMResult(kind=kind, text=text))

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        executor.submit(_worker)
        executor.shutdown(wait=False)

    def _on_advice_llm_result(self, item: "AdviceLLMResult") -> None:
        """处理 LLM 增强建议结果：成功时追加/覆盖对应面板文本，失败时不动（降级）。"""
        if item.text is None:
            # LLM 不通：降级，保持规则引擎已展示的文本不变。
            return

        if item.kind == "advice":
            # 用 LLM 研判覆盖时段建议面板（保留开头的规则引擎标题行作为参考）。
            if self.advice_label is not None:
                self._set_text(self.advice_label, item.text)
        elif item.kind == "intraday_analysis":
            # 盘中技术面解读追加在时段建议面板下方。
            if self.advice_label is not None:
                try:
                    self.advice_label.config(state="normal")
                    self.advice_label.insert("end", "\n\n" + item.text)
                    self.advice_label.config(state="disabled")
                except Exception:  # noqa: BLE001
                    pass
        elif item.kind == "signal_guidance":
            # 信号操作指导追加在信号面板下方。
            if self.signals_text is not None:
                try:
                    self.signals_text.config(state="normal")
                    self.signals_text.insert("end", "\n\n💡 操作指导：" + item.text)
                    self.signals_text.config(state="disabled")
                except Exception:  # noqa: BLE001
                    pass

    def _render_llm(self, res: LLMResult) -> None:
        """渲染外盘/新闻研判结果（需求 7.6/7.7/7.10/7.11）。"""
        stamp = res.fetched_at.strftime("%Y-%m-%d %H:%M:%S")
        if res.status in ("success", "fallback_no_tools"):
            text = res.briefing_text or ""
            # 需求 7.10：内容不含任一可识别外盘项判为失败，原样展示原始返回。
            if self.deps.llm_client.is_briefing_failure(text):
                body = (
                    "外盘/新闻数据获取失败：返回内容未包含可识别的外盘数据项。\n\n"
                    f"模型原始返回：\n{text}"
                )
            else:
                # 需求 7.6：附"不确定信息，仅供参考"标记与获取时间。
                note = "【不确定信息，仅供参考】"
                if not res.used_tools:
                    # 需求 7.7：回退未使用联网工具时提示。
                    note += " 本次研判未使用联网工具。"
                body = f"{note}\n获取时间：{stamp}\n\n{text}"
        elif res.status == "timeout":
            # 需求 7.11：整体超时提示。
            body = f"外盘/新闻请求超时（超过 {int(self.deps.llm_client.overall_timeout)} 秒），已终止本次请求。\n获取时间：{stamp}"
        else:  # error
            # 需求 7.10：失败时原样展示原始返回供核实。
            body = f"外盘/新闻数据获取失败。\n获取时间：{stamp}\n\n模型/接口原始返回：\n{res.raw_response or ''}"

        self._set_text(self.llm_text, body)

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    @staticmethod
    def _set_text(widget: Optional["tk.Text"], text: str) -> None:
        """把只读 Text 组件的内容整体替换为 ``text``。"""
        if widget is None:
            return
        widget.config(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.config(state=tk.DISABLED)
