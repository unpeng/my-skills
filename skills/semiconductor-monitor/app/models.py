# -*- coding: utf-8 -*-
"""共享数据模型：交易时段枚举与各组件间传递的 dataclass。

字段严格对齐设计文档 "Data Models" 章节，供 Quote_Poller、Rule_Engine、
Alert_Manager、Position_Manager、Trade_Logger、LLM_Client、Monitor_App 等组件复用。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class TradingSession(enum.Enum):
    """交易时段枚举（互斥且穷尽地覆盖全天）。

    - 集合竞价：09:15:00–09:25:59
    - 开盘：09:30:00–09:59:59
    - 盘中：10:00:00–11:29:59、13:00:00–13:59:59
    - 尾盘：14:00:00–14:59:59
    - 非交易时段：其余时间与非交易日
    """

    CALL_AUCTION = "集合竞价"      # 09:15:00–09:25:59
    OPENING = "开盘"               # 09:30:00–09:59:59
    INTRADAY = "盘中"              # 10:00:00–11:29:59, 13:00:00–13:59:59
    CLOSING = "尾盘"               # 14:00:00–14:59:59
    NON_TRADING = "非交易时段"      # 其余时间与非交易日


@dataclass
class RoundResult:
    """一轮行情获取与计算的结果。

    对 ``compute_monitor_variables`` 返回字典的封装，新增派生字段、错误标识与时间戳。
    成功轮 ``ok=True`` 且 ``vars`` 为变量字典；失败轮 ``ok=False`` 且 ``error`` 说明原因。
    """

    ok: bool                                # 是否为成功轮次（无 error）
    vars: dict                              # compute_monitor_variables 原样返回（成功时）
    error: Optional[str]                    # 失败原因（含超时）
    price_source: Optional[str]             # realtime / kline_fallback / kline_only
    macd_hist_prev: Optional[float]         # 派生：上一周期 MACD 柱值（需求 5.1/5.2）
    macd_hist_curr: Optional[float]         # 派生：当前周期 MACD 柱值
    fetched_at: datetime                    # 本轮结果的获取时间


@dataclass
class SessionAdvice:
    """某一交易时段的操作建议（Rule_Engine 输出）。"""

    session: TradingSession                 # 所处交易时段
    scenario: Optional[str]                 # 如 "情景3"/"开盘情景B"/"尾盘-小亏"/None（数据不足）
    advice_text: str                        # 展示给用户的建议正文
    data_available: bool                    # 需求 4.7：False 时展示"数据不可用"提示


@dataclass
class Signal:
    """买入/卖出/止损等信号（Rule_Engine 输出，供 Alert_Manager 去重与展示）。"""

    kind: str                               # "买入"/"卖出"/"止损"/"放量下跌止损"
    trigger_price: float                    # 触发价（当前价）
    related_price: Optional[float]          # 关联关键价格（用于去重指纹）
    reasons: list                           # 成立的具体条件描述（list[str]）
    triggered_at: datetime                  # 触发时间

    def fingerprint(self) -> tuple:
        """去重指纹：(信号类型, 关联关键价格)（需求 6.4/6.5）。"""
        return (self.kind, self.related_price)


@dataclass
class Alert:
    """向用户发出的提醒（Alert_Manager 输出）。"""

    signal_kind: str                        # 信号类型（买入/卖出/止损 等）
    symbol: str                             # 标的代码，固定 "588170"
    trigger_price: float                    # 触发价格
    triggered_at: datetime                  # 触发时间
    play_sound: bool                        # 是否需要播放提示音（受声音开关影响）


@dataclass
class PositionInput:
    """持仓信息的应用内内存对象，字段与 ``.local/positions.json`` 一一对应。"""

    position: int                           # 持仓数量（份额）
    cost: float                             # 加权成本
    cash: float = 0.0                       # 可用资金
    max_loss_pct: Optional[float] = None    # 止损方式一：最大亏损比例（0.01%–100%）
    max_loss_amount: Optional[float] = None  # 止损方式二：最大亏损金额（正数）
    stop_loss_price: Optional[float] = None  # 止损方式三：直接指定止损价（正数且低于成本）
    atr_stop_n: Optional[float] = None      # 止损方式四：ATR 止损倍数 N（止损位=成本−N×ATR）
    updated_at: Optional[str] = None        # 最近更新时间（ISO 字符串）


@dataclass
class PositionForm:
    """持仓表单原始字符串输入，经 ``validate`` 转换为 ``PositionInput``。"""

    position: str = ""                      # 持仓数量（原始字符串）
    cost: str = ""                          # 加权成本（原始字符串）
    cash: str = ""                          # 可用资金（原始字符串）
    max_loss_pct: str = ""                  # 最大亏损比例（原始字符串，未选填空）
    max_loss_amount: str = ""               # 最大亏损金额（原始字符串，未选填空）
    stop_loss_price: str = ""               # 直接指定止损价（原始字符串，未选填空）
    atr_stop_n: str = ""                    # ATR 止损倍数（原始字符串，未选填空）


@dataclass
class ValidationResult:
    """持仓表单校验结果。"""

    valid: bool                             # 是否全部通过校验
    errors: dict = field(default_factory=dict)  # 字段名 -> 错误提示（指明越界字段）
    value: Optional[PositionInput] = None   # 校验通过时转换出的 PositionInput，否则 None


@dataclass
class SaveResult:
    """持仓保存结果。"""

    ok: bool                                # 是否保存成功
    message: str = ""                       # 成功确认或失败提示文本
    validation: Optional[ValidationResult] = None  # 校验失败时携带的校验结果


@dataclass
class AddResult:
    """交易记录写入结果。"""

    ok: bool                                # 是否写入成功
    message: str = ""                       # 成功确认或错误提示文本


@dataclass
class LogEntry:
    """一条交易/决策记录（对齐 ``read_decision_log`` 返回结构）。"""

    time: str                               # 时间戳（ISO 字符串）
    code: str                               # 标的代码
    action: str                             # 操作类型（做T买入/做T卖出/止损/减仓）
    price: float                            # 成交价格
    shares: Optional[float] = None          # 涉及份数（可选）
    note: str = ""                          # 备注（可选）


@dataclass
class LLMConfig:
    """大模型接口配置（对齐 app_settings.json 的 ``llm`` 段）。"""

    base_url: str = ""                      # 接口地址
    api_key: str = ""                       # API 密钥
    model: str = ""                         # 模型名称

    def is_complete(self) -> bool:
        """三项配置是否均已非空填写（需求 7.8/7.9 门控）。"""
        return bool(self.base_url and self.api_key and self.model)


@dataclass
class LLMResult:
    """外盘/新闻大模型研判结果（LLM_Client 输出）。"""

    status: str                             # "success"/"timeout"/"error"/"fallback_no_tools"
    briefing_text: Optional[str]            # 最终研判（success/fallback 时）
    raw_response: Optional[str]             # 原始返回（error 时原样展示，需求 7.10）
    used_tools: bool                        # 是否使用了联网工具（需求 7.7 展示）
    tool_rounds: int                        # 实际工具调用轮数（≤8）
    fetched_at: datetime                    # 本次获取时间


@dataclass
class AdviceLLMResult:
    """LLM 增强的建议/解读结果（AdviceLLM 输出，经队列回主线程）。

    kind 区分三种增强：
      - "advice"：时段建议综合研判
      - "signal_guidance"：信号操作指导
      - "intraday_analysis"：盘中技术面解读
    """

    kind: str                               # "advice" / "signal_guidance" / "intraday_analysis"
    text: Optional[str]                     # LLM 返回的文本；None 表示失败/超时（降级）
