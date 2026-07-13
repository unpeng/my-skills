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
        self.notebook: Optional[ttk.Notebook] = None
        self.disclaimer_bar: Optional[tk.Label] = None
        self.var_labels: dict = {}
        self.indicator_labels: dict = {}
        self.price_annotation_label: Optional[tk.Label] = None
        self.data_quality_label: Optional[tk.Label] = None
        self.error_label: Optional[tk.Label] = None
        self.advice_label: Optional[tk.Text] = None
        self.signals_text: Optional[tk.Text] = None
        self.update_time_label: Optional[tk.Label] = None
        # 最近一次成功取数的时间，用于展示更新时间与计算下次更新倒计时。
        self._last_update_time: Optional[datetime] = None
        self._countdown_job: Optional[str] = None
        self.llm_text: Optional[tk.Text] = None
        self.trade_list: Optional[tk.Listbox] = None

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
    def run(self) -> None:
        """启动应用：首启需先确认免责声明，之后进入主界面。"""
        self.root.title(f"{SYMBOL} 盯盘助手")
        # 放大初始窗口，确保盯盘页所有面板一屏可见、无需滚动。
        self.root.geometry("1280x820")
        try:
            self.root.minsize(1024, 680)
        except Exception:  # noqa: BLE001  个别平台不支持时忽略
            pass
        # 按用户要求：去掉首启"我已阅读并确认"免责声明确认门，启动直接进入主界面。
        # 底部常驻的免责声明提示条仍保留（需求 9.1/9.2）。
        self._enter_main()
        self._bring_to_front()

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

    def _refresh_update_time_label(self) -> None:
        """刷新"数据更新时间 + 下次更新倒计时"文本（需求：展示数据新鲜度）。"""
        if self.update_time_label is None:
            return
        if self._last_update_time is None:
            self.update_time_label.config(text="数据更新时间：--　|　下次更新倒计时：等待首轮数据…")
            return
        updated = self._last_update_time.strftime("%H:%M:%S")
        try:
            interval = int(self.deps.settings.get_interval())
        except Exception:  # noqa: BLE001
            interval = 60
        elapsed = (datetime.now() - self._last_update_time).total_seconds()
        remaining = int(max(0, round(interval - elapsed)))
        self.update_time_label.config(
            text=f"数据更新时间：{updated}　|　下次更新倒计时：{remaining} 秒"
        )

    def _tick_countdown(self) -> None:
        """每秒刷新一次倒计时显示（在主线程内周期执行）。"""
        self._refresh_update_time_label()
        self._countdown_job = self.root.after(1000, self._tick_countdown)

    def _autostart_polling(self) -> None:
        """进入主界面后自动启动轮询并请求立即刷新首轮数据。"""
        try:
            self.deps.quote_poller.start()
            # 立即触发第一轮（否则要等待 refresh_event.wait(interval) 到点）。
            self.deps.quote_poller.request_refresh()
        except Exception:  # noqa: BLE001  自动启动失败不影响手动"开始轮询"
            pass

    def _build_main_ui(self) -> None:
        """构建主界面：底部常驻免责声明条 + 单页可滚动仪表盘（所有信息整合在同一界面）。"""
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

        # 两个 Tab：盯盘（所有信息整合在一页、网格布局、不滚动）+ 设置（单独一页）。
        notebook = ttk.Notebook(self.root)
        notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.notebook = notebook

        board = ttk.Frame(notebook, padding=8)
        notebook.add(board, text="盯盘")
        settings_tab = ttk.Frame(notebook, padding=12)
        notebook.add(settings_tab, text="设置")

        # 盯盘页按功能上下分区：上方为只读展示区、下方为可交互输入区，中间以分隔线区隔。
        # 左右两列布局：
        #   左列：上=变量指标、下=外盘新闻
        #   右列：上→下 = 时段建议、信号、持仓录入
        board.rowconfigure(0, weight=1)
        board.columnconfigure(0, weight=3, uniform="cols")  # 左列略宽（含变量指标）
        board.columnconfigure(1, weight=2, uniform="cols")

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
        right.rowconfigure(0, weight=2)   # 时段建议
        right.rowconfigure(1, weight=2)   # 信号
        right.rowconfigure(2, weight=3)   # 持仓录入

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
        self._build_settings_view(settings_tab)

        # 启动回填持仓（需求 1.2）。
        self._reload_position_form()

    # --- 持仓视图（需求 1.x）---------------------------------------------
    def _build_position_view(self, parent) -> None:
        frame = parent

        self._pos_vars = {
            "position": tk.StringVar(),
            "cost": tk.StringVar(),
            "cash": tk.StringVar(),
            "max_loss_pct": tk.StringVar(),
            "max_loss_amount": tk.StringVar(),
            "stop_loss_price": tk.StringVar(),
        }
        self._stop_mode = tk.StringVar(value="max_loss_pct")

        rows = [
            ("持仓数量（份）", "position"),
            ("加权成本", "cost"),
            ("可用资金", "cash"),
        ]
        for i, (label, key) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky=tk.W, pady=3)
            ttk.Entry(frame, textvariable=self._pos_vars[key], width=20).grid(
                row=i, column=1, sticky=tk.W, pady=3
            )

        # 止损三选一（需求 1.5）。
        ttk.Label(frame, text="止损设定（三选一）").grid(
            row=3, column=0, sticky=tk.W, pady=(10, 3)
        )
        stop_rows = [
            ("最大亏损比例(%)", "max_loss_pct"),
            ("最大亏损金额", "max_loss_amount"),
            ("直接指定止损价", "stop_loss_price"),
        ]
        for j, (label, key) in enumerate(stop_rows):
            ttk.Radiobutton(
                frame, text=label, variable=self._stop_mode, value=key
            ).grid(row=4 + j, column=0, sticky=tk.W, pady=2)
            ttk.Entry(frame, textvariable=self._pos_vars[key], width=20).grid(
                row=4 + j, column=1, sticky=tk.W, pady=2
            )

        ttk.Button(frame, text="保存持仓", command=self._on_save_position).grid(
            row=8, column=0, columnspan=2, pady=(12, 0)
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
            ttk.Label(frame, text=label + "：").grid(row=r, column=c, sticky=tk.W, pady=2)
            value_label = ttk.Label(frame, text="-", width=18, anchor=tk.W)
            value_label.grid(row=r, column=c + 1, sticky=tk.W, pady=2)
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

    # --- 设置视图（需求 2.x/6.6/7.1）------------------------------------
    def _build_settings_view(self, parent) -> None:
        frame = parent

        settings = self.deps.settings
        # 轮询间隔（需求 2.2/2.3/2.4）。
        ttk.Label(frame, text="轮询间隔（秒，5-3600）").grid(row=0, column=0, sticky=tk.W, pady=3)
        self._interval_var = tk.StringVar(value=str(settings.get_interval()))
        ttk.Entry(frame, textvariable=self._interval_var, width=12).grid(
            row=0, column=1, sticky=tk.W, pady=3
        )

        # 声音开关（需求 6.6）。
        self._sound_var = tk.BooleanVar(value=settings.is_sound_enabled())
        ttk.Checkbutton(frame, text="启用声音提醒", variable=self._sound_var).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, pady=3
        )

        # 大模型三项配置（需求 7.1）。
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
            row=6, column=0, columnspan=2, pady=(12, 0)
        )

        # 轮询控制按钮（需求 2.5/2.6）。
        ctrl = ttk.Frame(frame)
        ctrl.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        ttk.Button(ctrl, text="开始轮询", command=self._on_start_poll).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(ctrl, text="停止轮询", command=self._on_stop_poll).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(ctrl, text="立即刷新", command=self._on_refresh_now).pack(side=tk.LEFT)

    def _on_save_settings(self) -> None:
        """保存设置：间隔校验（需求 2.3/2.4）、声音开关（6.6）、大模型三项（7.1）。"""
        settings = self.deps.settings
        # 轮询间隔：非整数/越界由 Settings_Store 拒绝并保留原值。
        raw = (self._interval_var.get() or "").strip()
        interval_val: object = raw
        try:
            if raw and float(raw).is_integer():
                interval_val = int(float(raw))
        except (TypeError, ValueError):
            interval_val = raw
        res = settings.set_interval(interval_val)
        if not res.ok:
            messagebox.showerror("设置未保存", res.message)
            # 回填为保留的原间隔值。
            self._interval_var.set(str(settings.get_interval()))
            return

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
        self.deps.quote_poller.start()

    def _on_stop_poll(self) -> None:
        self.deps.quote_poller.stop()

    def _on_refresh_now(self) -> None:
        # 需求 2.8：忙时被忽略，返回 False 时提示用户。
        accepted = self.deps.quote_poller.request_refresh()
        if not accepted:
            messagebox.showinfo("立即刷新", "当前轮次尚未完成，已忽略本次立即刷新请求。")

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
        # 其他类型静默忽略，避免异常中断消费循环。

    def _on_round_result(self, r: RoundResult) -> None:
        """处理一轮行情结果：失败轮保留上一轮展示、成功轮全量刷新（需求 3.x/10.x）。"""
        if not r.ok:
            # 需求 3.8/10.1/10.4：展示错误提示并保留上一轮成功展示数据不清空。
            if self.error_label is not None:
                self.error_label.config(text=f"数据获取失败：{r.error or '未知错误'}")
            return

        # 成功轮：清空错误提示、更新最近成功轮、刷新全部展示。
        if self.error_label is not None:
            self.error_label.config(text="")
        # 记录本轮成功取数时间，供更新时间展示与倒计时使用。
        self._last_update_time = r.fetched_at
        self._refresh_update_time_label()
        display = choose_display_result(self.last_good_result, r)
        self.last_good_result = display
        self._render_variables(display)
        self._render_advice(display)
        self._render_signals(display)

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
        """依据当前交易时段渲染时段建议（需求 4.x）。"""
        if self.advice_label is None:
            return
        now = datetime.now()
        # 交易日近似：周一至周五视为交易日（MVP 近似，见设计标注的可增强点）。
        is_trading_day = now.weekday() < 5
        session = classify_session(now, is_trading_day)
        advice = self.deps.rule_engine.session_advice(session, r.vars or {})
        header = f"【{session.value}】"
        self._set_text(self.advice_label, f"{header} {advice.advice_text}")

    def _render_signals(self, r: RoundResult) -> None:
        """渲染信号并交由 Alert_Manager 去重与提醒（需求 5.x/6.x）。"""
        vars_ = dict(r.vars or {})
        # 把派生 MACD 柱值并入变量字典供信号判断使用。
        vars_.setdefault("macd_hist_prev", r.macd_hist_prev)
        vars_.setdefault("macd_hist_curr", r.macd_hist_curr)
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

        self._set_text(self.signals_text, "\n".join(lines))

        # 触发信号时以弹窗强化提示（需求 6.1）。
        for a in alerts:
            if a.signal_kind in ("买入", "卖出", "止损", "放量下跌止损"):
                messagebox.showwarning(
                    "交易信号提醒",
                    f"信号类型：{a.signal_kind}\n标的：{a.symbol}\n"
                    f"触发价格：{a.trigger_price}\n触发时间：{a.triggered_at:%Y-%m-%d %H:%M:%S}",
                )

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
