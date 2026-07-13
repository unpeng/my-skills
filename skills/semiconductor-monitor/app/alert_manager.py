# -*- coding: utf-8 -*-
"""Alert_Manager：触发提醒（弹窗/高亮内容 + 声音）与去重。

职责（对齐设计文档 "Alert_Manager" 小节与需求 6）：

1. 需求 6.1：当 Rule_Engine 生成买入/卖出/止损等信号时，产出提醒对象 ``Alert``，
   其内容至少包含 信号类型、标的代码（588170）、触发价格与触发时间，供 UI 弹窗/高亮展示。
2. 需求 6.3：当"当前价"首次达到或穿越 止损位 / 做T买入位 / 做T卖出位 之一时，产出触及
   提醒，并在提醒内容（``signal_kind``）中标明所触及的关键价位类型与对应价格。
   "首次穿越"通过对比上一轮当前价与本轮当前价相对阈值的位置变化判定。
3. 需求 6.4 / 6.5（去重核心）：为每类信号维护"上次已提醒指纹" =
   ``(信号类型, 关联关键价格)``。指纹相同则不重复提醒；指纹变化（类型或关联关键价格
   改变）则再次提醒。
4. 需求 6.2 / 6.6：``sound_enabled()`` 为真时才在本轮播放一次提示音；关闭时仅产出
   视觉提醒（弹窗/高亮）而不播放。

本组件为纯逻辑 + 一次声音副作用：``process`` 不触碰任何 tkinter widget，返回的
``Alert`` 列表由主线程负责渲染，便于属性测试（去重、首次触及）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from .models import Alert, Signal
from .sound import SoundPlayer

# 本应用标的固定为 588170（需求 6.1 要求提醒含标的代码）。
SYMBOL = "588170"

# 需求 6.3 中需监测"首次触及/穿越"的三类关键价位，按 vars 字典中的键名列出。
_WATCHED_KEY_PRICES: Tuple[str, ...] = ("止损位", "做T买入位", "做T卖出位")


def _sign(value: float) -> int:
    """返回数值相对 0 的符号：负数 -1、正数 +1、恰为 0 时 0。"""
    if value < 0:
        return -1
    if value > 0:
        return 1
    return 0


class AlertManager:
    """触发提醒管理器：生成提醒、去重、按开关播放提示音。

    内部维护两类状态：

    - ``_last_fingerprints``：每类信号"上次已提醒指纹"，用于需求 6.4/6.5 的去重。
    - ``_prev_current_price``：上一轮当前价，用于需求 6.3 的"首次达到或穿越"判定。
    """

    def __init__(self, sound_enabled: Callable[[], bool], sound: SoundPlayer) -> None:
        """构造提醒管理器。

        Args:
            sound_enabled: 无参回调，返回当前声音提醒开关状态（True=开启）。
                每轮实时读取，用户在设置中切换开关后即刻生效（需求 6.2/6.6）。
            sound: 跨平台提示音播放器，``play()`` 播放一次 ≤3 秒提示音。
        """
        self._sound_enabled = sound_enabled
        self._sound = sound
        # 每类信号 -> 上次已提醒指纹 (信号类型, 关联关键价格)（需求 6.4/6.5 去重）。
        self._last_fingerprints: Dict[str, Tuple] = {}
        # 上一轮当前价（需求 6.3 判定"首次达到或穿越"用）；首轮为 None。
        self._prev_current_price: Optional[float] = None

    def process(
        self,
        signals: List[Signal],
        vars: dict,
        now: datetime,
    ) -> List[Alert]:
        """处理本轮信号与关键价位，返回需要展示的提醒列表。

        步骤：
        1. 对每个信号按指纹去重（需求 6.4/6.5），未提醒过或指纹变化则产出信号提醒
           （需求 6.1）。
        2. 检测当前价是否"首次达到或穿越"三类关键价位之一（需求 6.3），是则产出触及提醒。
        3. 若本轮有任一提醒且声音开关开启，则播放一次提示音（需求 6.2/6.6）。

        Args:
            signals: Rule_Engine 本轮输出的信号列表。
            vars: 本轮盯盘变量字典（含"当前价"及各关键价位）。
            now: 本轮处理时间，用于关键价位触及提醒的触发时间。

        Returns:
            本轮需要展示的 ``Alert`` 列表（可能为空）。
        """
        alerts: List[Alert] = []

        # ---- 1. 信号提醒 + 指纹去重（需求 6.1 / 6.4 / 6.5）----
        for sig in signals:
            fingerprint = sig.fingerprint()
            last = self._last_fingerprints.get(sig.kind)
            if last == fingerprint:
                # 指纹与上次完全相同：信号状态未变化，去重不重复提醒（需求 6.4）。
                continue
            # 首次出现或指纹发生变化（类型/关联关键价格改变）：再次提醒（需求 6.5）。
            self._last_fingerprints[sig.kind] = fingerprint
            alerts.append(
                Alert(
                    signal_kind=sig.kind,           # 信号类型（买入/卖出/止损 等）
                    symbol=SYMBOL,                  # 标的代码 588170
                    trigger_price=sig.trigger_price,  # 触发价格
                    triggered_at=sig.triggered_at,  # 触发时间
                    play_sound=False,               # 稍后统一按声音开关设置
                )
            )

        # ---- 2. 关键价位"首次达到或穿越"提醒（需求 6.3）----
        current_price = vars.get("当前价")
        for key in _WATCHED_KEY_PRICES:
            threshold = vars.get(key)
            if self._crossed(self._prev_current_price, current_price, threshold):
                alerts.append(
                    Alert(
                        # 在提醒内容中标明所触及的关键价位类型与对应价格（需求 6.3）。
                        signal_kind=f"触及关键价位：{key}（{threshold}）",
                        symbol=SYMBOL,
                        trigger_price=current_price,  # 触发的当前价
                        triggered_at=now,
                        play_sound=False,             # 稍后统一按声音开关设置
                    )
                )

        # 更新"上一轮当前价"状态，供下一轮判定穿越（None 也如实记录，避免跨空档误判）。
        self._prev_current_price = current_price

        # ---- 3. 声音：本轮有提醒且开关开启才播放一次（需求 6.2 / 6.6）----
        sound_on = bool(self._sound_enabled())
        if alerts:
            for alert in alerts:
                alert.play_sound = sound_on
            if sound_on:
                # 每轮至多播放一次提示音（≤3 秒，由 SoundPlayer 保证）。
                self._sound.play()

        return alerts

    @staticmethod
    def _crossed(
        prev_price: Optional[float],
        curr_price: Optional[float],
        threshold: Optional[float],
    ) -> bool:
        """判定当前价本轮是否"首次达到或穿越"某一关键价位阈值（需求 6.3）。

        通过对比上一轮当前价与本轮当前价相对阈值的位置（低于/等于/高于）变化判定：

        - 任一入参为 None（首轮无上一价、当前价缺失或阈值未知）时，无法判定，返回 False；
        - 上一轮已处于阈值上（等于）时，本轮离开阈值不算"新的触及"，返回 False；
        - 位置由"严格低于/高于"变为"达到（等于）或穿越到另一侧"时，返回 True。

        Args:
            prev_price: 上一轮当前价。
            curr_price: 本轮当前价。
            threshold: 关键价位阈值。

        Returns:
            本轮是否构成一次"首次达到或穿越"事件。
        """
        if prev_price is None or curr_price is None or threshold is None:
            return False
        prev_side = _sign(prev_price - threshold)
        curr_side = _sign(curr_price - threshold)
        if prev_side == curr_side:
            # 相对阈值位置未变化：未发生新的达到或穿越。
            return False
        if prev_side == 0:
            # 上一轮恰在阈值上，本轮离开不视为"首次达到"。
            return False
        # 上一轮严格在一侧，本轮达到（等于）或穿越到另一侧：构成首次触及事件。
        return True
