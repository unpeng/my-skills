# -*- coding: utf-8 -*-
"""main_app：应用组装入口（Composition Root）。

职责（对齐设计 "代码放置位置与复用方式" 与 "组件关系图"，覆盖需求 1.2/2.1/9.3）：
  1. 注入 ``<技能根>/qstock`` 到 ``sys.path``（复用 ``app`` 包的
     :func:`ensure_qstock_on_path`），使各封装组件能像 qstock 内部模块一样直接
     ``from strategy.monitor import ...`` / ``from strategy.position_store import ...``；
  2. 实例化 Settings_Store / Position_Manager / Trade_Logger / Variable_Calculator /
     Rule_Engine / Alert_Manager / Quote_Poller / LLM_Client，并按依赖关系装配为
     :class:`AppDeps` 容器；
  3. 创建 ``tk.Tk()`` 主窗口，注入依赖后 ``MonitorApp(root, deps).run()`` 启动。

运行方式（两者皆可）::

    python3 app/main_app.py       # 脚本方式：sys.path[0] 为 app 目录
    python3 -m app.main_app       # 模块方式：从技能根以包形式运行

导入兼容：脚本方式运行时 ``__package__`` 为空、相对导入不可用，故下方以
``try 相对导入 / except 绝对导入`` 兜底——绝对导入前把技能根（``app`` 的父目录）
加入 ``sys.path``，再以 ``app.xxx`` 形式导入各组件。

约定：本模块在 ``import`` 时**不**创建任何 ``tk.Tk()`` 实例、**不**启动主循环
（无显示环境下实例化会失败）；这些副作用仅在 :func:`main` / ``__main__`` 内发生，
因此 ``import app.main_app`` 可安全用于测试与静态检查。
"""

from __future__ import annotations

import os
import queue
import sys

# 静默 macOS 系统 Tk 的弃用警告（不影响功能，仅避免终端噪声）。
os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

# ---------------------------------------------------------------------------
# 导入兼容：同时支持"包内相对导入"（python3 -m app.main_app / import app.main_app）
# 与"脚本绝对导入"（python3 app/main_app.py，此时 sys.path[0] 为 app 目录）。
# ---------------------------------------------------------------------------
try:  # 包上下文可用时优先走相对导入。
    from . import ensure_qstock_on_path
    from .alert_manager import AlertManager
    from .llm_client import LLMClient
    from .monitor_app import AppDeps, MonitorApp
    from .position_manager import PositionManager
    from .quote_poller import QuotePoller
    from .rule_engine import RuleEngine
    from .settings_store import SettingsStore
    from .sound import SoundPlayer
    from .trade_logger import TradeLogger
    from .variable_calculator import VariableCalculator
except ImportError:  # 脚本方式运行：无包上下文，改用绝对导入。
    # 把技能根（app 的父目录）加入 sys.path，使 `app` 包可被解析。
    _SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _SKILL_ROOT not in sys.path:
        sys.path.insert(0, _SKILL_ROOT)
    from app import ensure_qstock_on_path
    from app.alert_manager import AlertManager
    from app.llm_client import LLMClient
    from app.monitor_app import AppDeps, MonitorApp
    from app.position_manager import PositionManager
    from app.quote_poller import QuotePoller
    from app.rule_engine import RuleEngine
    from app.settings_store import SettingsStore
    from app.sound import SoundPlayer
    from app.trade_logger import TradeLogger
    from app.variable_calculator import VariableCalculator


def build_deps() -> AppDeps:
    """实例化并装配所有组件，返回注入 Monitor_App 的依赖容器（AppDeps）。

    装配关系（对齐设计 "组件关系图"）：
      - ``result_queue``：线程安全队列，Quote_Poller / LLM_Client 的结果经此回主线程；
      - ``QuotePoller``：注入盯盘计算封装、结果队列、以及"取轮询间隔""取当前持仓"的回调
        （间隔与持仓每轮实时读取，设置/持仓变更下一轮即生效——需求 2.1/1.2）；
      - ``AlertManager``：注入"读声音开关"的回调与跨平台 SoundPlayer（需求 6.2/6.6）；
      - ``LLMClient``：注入"读大模型三项配置"的回调（需求 7.1/7.8）。

    Returns:
        组装完成、可直接注入 :class:`MonitorApp` 的 :class:`AppDeps`。
    """
    # 先把 <技能根>/qstock 加入 sys.path，确保后续各组件对 qstock 能力的导入可解析。
    ensure_qstock_on_path()

    # 结果队列：后台线程（行情轮询 / 外盘研判）回送结果，主线程经 root.after 消费。
    result_queue: "queue.Queue" = queue.Queue()

    # 配置持久化：轮询间隔、大模型三项配置、声音开关、免责声明确认位。
    settings = SettingsStore()

    # 持仓录入/校验/持久化/回填（需求 1.x）。
    position_manager = PositionManager()

    # 交易记录（需求 8.x）。
    trade_logger = TradeLogger()

    # 盯盘变量与技术指标计算封装（单轮 10 秒超时，需求 3.1/2.7）。
    calculator = VariableCalculator()

    # 规则引擎：分时段建议与买卖/止损信号（纯逻辑，需求 4.x/5.x）。
    rule_engine = RuleEngine()

    # 跨平台提示音 + 触发提醒去重（需求 6.x）。声音开关每轮实时读取以便即时生效。
    sound = SoundPlayer()
    alert_manager = AlertManager(
        sound_enabled=settings.is_sound_enabled,
        sound=sound,
    )

    # 后台轮询线程：间隔与持仓均以回调实时读取（需求 2.1/2.3/1.2）。
    quote_poller = QuotePoller(
        calculator=calculator,
        result_queue=result_queue,
        get_interval=settings.get_interval,
        get_position=position_manager.load,
    )

    # 大模型接入：外盘/新闻研判，配置以回调实时读取（需求 7.1）。
    llm_client = LLMClient(get_config=settings.get_llm_config)

    return AppDeps(
        settings=settings,
        position_manager=position_manager,
        trade_logger=trade_logger,
        rule_engine=rule_engine,
        alert_manager=alert_manager,
        quote_poller=quote_poller,
        llm_client=llm_client,
        result_queue=result_queue,
    )


def main() -> None:
    """应用入口：装配依赖、创建主窗口并启动。

    仅在此处（或 ``__main__``）创建 ``tk.Tk()`` 与进入主循环，保证模块可被安全导入。
    """
    # 延迟到函数内导入 tkinter，避免模块 import 阶段触及 GUI 库。
    import tkinter as tk

    deps = build_deps()
    root = tk.Tk()
    MonitorApp(root, deps).run()
    root.mainloop()


if __name__ == "__main__":
    main()
