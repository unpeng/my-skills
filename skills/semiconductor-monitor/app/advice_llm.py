# -*- coding: utf-8 -*-
"""LLM 增强的时段建议、信号操作指导与盘中技术面解读。

职责：
  1. 时段建议综合研判：把规则引擎的情景判定 + 全部技术指标状态 + SKILL.md 操作原则，
     组装成 prompt 交给 LLM 生成个性化操作建议。
  2. 信号操作指导：信号触发后，结合信号类型/触发条件/持仓状态/做T纪律，让 LLM 给出
     具体操作指导。
  3. 盘中技术面解读：把 RSI/KDJ/MACD/布林/成交量状态交给 LLM 生成量价配合综合解读。

降级原则：LLM 不通（超时/未配置/报错）时所有增强返回 None，由调用方展示代码规则引擎
的原始输出（不影响任何既有功能）。

线程模型：本模块所有方法都是同步阻塞调用（含网络 I/O），必须在后台线程中调用，
不能在 tkinter 主线程中直接使用。调用方应通过队列+root.after 回主线程展示。
"""

from __future__ import annotations

import concurrent.futures
from typing import Callable, Optional

import requests

from .models import LLMConfig


# --- 超时常量 ---
_LLM_TIMEOUT = 15  # 单次 LLM 调用 HTTP 超时（秒）
_OVERALL_TIMEOUT = 12  # 整体超时（含线程调度开销，需短于轮询间隔的一半，避免阻塞下一轮）


# --- Prompt 模板 ---

_ADVICE_SYSTEM = """\
你是一名 A 股 ETF 盯盘助手。用户正在实时监控 588170（科创半导体ETF华夏）。
请根据提供的当前行情数据和技术指标，给出简明、实用的操作建议（3-5句话）。
不要重复列举数据，直接给出判断和建议，附带简短理由。
必须在最后一行附加"⚠️ 以上仅供参考，不构成投资建议"。"""

_ADVICE_USER_TEMPLATE = """\
当前时段：{session}
情景判定：{scenario}
规则引擎建议：{rule_advice}

关键数据：
- 当前价：{当前价}，昨收价：{昨收价}，加权成本：{加权成本}
- 盈亏比例：{盈亏比例}%，距回本：{距回本}%
- 止损位：{止损位}（跟踪止损有效止损位：{有效止损位}）
- 做T买入位：{做T买入位}，做T卖出位：{做T卖出位}
- ATR：{ATR}，ATR百分比：{ATR百分比}%

技术指标：
- RSI={RSI}，KDJ-J={KDJ_J}
- MACD柱={MACD_HIST}（上一周期={MACD_HIST_PREV}）
- 布林上轨={布林上轨}，中轨={布林中轨}，下轨={布林下轨}
- 今日成交量={今日成交量}，20日均量={20日均量}

请综合以上数据，给出个性化操作建议。"""


_SIGNAL_SYSTEM = """\
你是一名 A 股 ETF 盯盘助手。当前有交易信号触发，请结合信号内容和持仓状态，
给出具体、可执行的操作指导（2-3句话）。
注意做T纪律：买了就挂卖单、每天只做一个方向、赚3-4%就走、跌破止损线停止做T。
最后一行附加"⚠️ 以上仅供参考，不构成投资建议"。"""

_SIGNAL_USER_TEMPLATE = """\
触发信号：
{signals_text}

持仓状态：
- 持仓数量：{持仓数量}份，加权成本：{加权成本}
- 当前价：{当前价}，浮动盈亏：{浮动盈亏}
- 做T可买份数：{做T可买份数}，做T买入位：{做T买入位}
- 止损位：{止损位}

请给出具体操作指导。"""


_INTRADAY_SYSTEM = """\
你是一名 A 股 ETF 盯盘助手，当前处于盘中监控时段。请根据提供的全部技术指标，
从以下维度做综合技术面解读（每条1句话）：
1. RSI/KDJ 超买超卖状态
2. MACD 趋势方向
3. 布林带位置（压力/支撑）
4. 量价配合（放量/缩量 + 涨/跌）
最后给出1句话盘中操作建议。
最后一行附加"⚠️ 以上仅供参考，不构成投资建议"。"""

_INTRADAY_USER_TEMPLATE = """\
当前价：{当前价}，昨收价：{昨收价}
RSI={RSI}（{rsi_zone}），KDJ-K={KDJ_K}，KDJ-D={KDJ_D}，KDJ-J={KDJ_J}
MACD-DIF={MACD_DIF}，MACD-DEA={MACD_DEA}，MACD柱={MACD_HIST}
布林上轨={布林上轨}，中轨={布林中轨}，下轨={布林下轨}
今日成交量={今日成交量}，20日均量={20日均量}（量比={volume_ratio}）
做T买入位={做T买入位}，做T卖出位={做T卖出位}

请做综合技术面解读。"""


# --- 辅助函数 ---

def _v(vars_: dict, key: str, default: str = "N/A") -> str:
    """从 vars 字典取值并转为展示字符串；None 时用 default 替代。"""
    val = vars_.get(key)
    if val is None:
        return default
    return str(val)


def _rsi_zone(vars_: dict) -> str:
    """根据 RSI 值给出区间描述。"""
    rsi = vars_.get("RSI")
    if rsi is None:
        return "数据不足"
    try:
        r = float(rsi)
        if r < 30:
            return "超卖"
        if r > 70:
            return "超买"
        return "中性"
    except (TypeError, ValueError):
        return "N/A"


def _volume_ratio(vars_: dict) -> str:
    """计算今日成交量/20日均量的量比。"""
    vol = vars_.get("今日成交量")
    avg = vars_.get("20日均量")
    if vol is None or avg is None:
        return "N/A"
    try:
        v, a = float(vol), float(avg)
        if a <= 0:
            return "N/A"
        return f"{v / a:.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _chat_once(messages: list, cfg: LLMConfig, timeout: float = _LLM_TIMEOUT) -> Optional[str]:
    """发起一次 LLM 调用，返回助手回复文本；任何异常返回 None（供降级）。"""
    try:
        base = (cfg.base_url or "").rstrip("/")
        url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        payload = {"model": cfg.model, "messages": messages}
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001  任何异常都降级
        return None


# --- 公开 API ---


class AdviceLLM:
    """LLM 增强的盯盘建议生成器。

    所有方法均为同步阻塞（含网络 I/O），必须在后台线程中调用。
    返回 None 时表示 LLM 不可用，调用方应降级为代码规则引擎输出。
    """

    def __init__(self, get_config: Callable[[], LLMConfig]) -> None:
        self._get_config = get_config

    def _cfg_or_none(self) -> Optional[LLMConfig]:
        try:
            cfg = self._get_config()
            if not cfg or not cfg.is_complete():
                return None
            return cfg
        except Exception:  # noqa: BLE001
            return None

    def generate_advice(
        self,
        session: str,
        scenario: Optional[str],
        rule_advice: str,
        vars_: dict,
        effective_stop: Optional[float] = None,
    ) -> Optional[str]:
        """生成时段建议综合研判。LLM 不通返回 None（降级为 rule_advice）。"""
        cfg = self._cfg_or_none()
        if cfg is None:
            return None

        user_msg = _ADVICE_USER_TEMPLATE.format(
            session=session,
            scenario=scenario or "无",
            rule_advice=rule_advice,
            有效止损位=f"{effective_stop:.4f}" if effective_stop else "N/A",
            **{k: _v(vars_, k) for k in [
                "当前价", "昨收价", "加权成本", "盈亏比例", "距回本",
                "止损位", "做T买入位", "做T卖出位", "ATR", "ATR百分比",
                "RSI", "KDJ_J", "MACD_HIST", "MACD_HIST_PREV",
                "布林上轨", "布林中轨", "布林下轨", "今日成交量", "20日均量",
            ]},
        )
        messages = [
            {"role": "system", "content": _ADVICE_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        return _chat_once(messages, cfg)

    def generate_signal_guidance(
        self, signals_text: str, vars_: dict
    ) -> Optional[str]:
        """生成信号操作指导。LLM 不通返回 None。"""
        cfg = self._cfg_or_none()
        if cfg is None:
            return None

        user_msg = _SIGNAL_USER_TEMPLATE.format(
            signals_text=signals_text,
            持仓数量=_v(vars_, "持仓数量", "未知"),
            加权成本=_v(vars_, "加权成本"),
            当前价=_v(vars_, "当前价"),
            浮动盈亏=_v(vars_, "浮动盈亏"),
            做T可买份数=_v(vars_, "做T可买份数"),
            做T买入位=_v(vars_, "做T买入位"),
            止损位=_v(vars_, "止损位"),
        )
        messages = [
            {"role": "system", "content": _SIGNAL_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        return _chat_once(messages, cfg)

    def generate_intraday_analysis(self, vars_: dict) -> Optional[str]:
        """生成盘中技术面综合解读。LLM 不通返回 None。"""
        cfg = self._cfg_or_none()
        if cfg is None:
            return None

        user_msg = _INTRADAY_USER_TEMPLATE.format(
            rsi_zone=_rsi_zone(vars_),
            volume_ratio=_volume_ratio(vars_),
            **{k: _v(vars_, k) for k in [
                "当前价", "昨收价", "RSI", "KDJ_K", "KDJ_D", "KDJ_J",
                "MACD_DIF", "MACD_DEA", "MACD_HIST",
                "布林上轨", "布林中轨", "布林下轨",
                "今日成交量", "20日均量", "做T买入位", "做T卖出位",
            ]},
        )
        messages = [
            {"role": "system", "content": _INTRADAY_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        return _chat_once(messages, cfg)
