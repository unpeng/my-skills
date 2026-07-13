# -*- coding: utf-8 -*-
"""Sound_Player：跨平台提示音后端。

按 ``sys.platform`` 选择声音后端并封装统一入口 ``SoundPlayer.play()``：

- Windows：标准库 ``winsound.Beep`` 发出短促蜂鸣；
- macOS：无 ``winsound``，用标准库 ``subprocess`` 调用系统 ``afplay`` 播放内置系统音效；
- Linux：尝试 ``paplay`` / ``aplay`` 播放系统音效；
- 上述后端均不可用时，退化为终端响铃（``\\a``）作兜底。

设计约束（对齐设计文档 "跨平台声音" 小节）：

1. 单次播放时长不超过 3 秒（需求 6.2）：外部播放进程以 3 秒为上限，超时即强制结束。
2. 播放在后台线程执行且任何后端异常都被静默吞掉——不抛出、不阻塞、不影响主流程；
   声音失败时调用方仍可正常完成弹窗/高亮等视觉提醒（需求 6.6）。

注意：本模块在 ``import`` 时不会发出任何声音；只有显式调用 ``play()`` 才会播放。

需求覆盖：6.2（声音开启时播放一次 ≤3 秒提示音）、6.6（后端不可用时静默降级为仅视觉）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# 单次提示音的时长上限（秒）。外部播放进程超过该时长将被强制结束（需求 6.2）。
MAX_DURATION_SECONDS = 3

# macOS 内置系统音效候选路径（存在其一即可）。
_MACOS_SOUND_CANDIDATES = [
    "/System/Library/Sounds/Ping.aiff",
    "/System/Library/Sounds/Glass.aiff",
    "/System/Library/Sounds/Submarine.aiff",
]

# Linux 常见系统音效候选路径（不同发行版路径不一，存在其一即可）。
_LINUX_SOUND_CANDIDATES = [
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
    "/usr/share/sounds/alsa/Front_Center.wav",
]


def _first_existing(paths: List[str]) -> Optional[str]:
    """返回候选列表中第一个存在的文件路径；都不存在则返回 ``None``。"""
    import os

    for path in paths:
        if os.path.isfile(path):
            return path
    return None


def _terminal_bell() -> None:
    """终端响铃兜底：向标准输出写入 BEL 字符（``\\a``）。

    即便终端不支持响铃也不会报错，是最后的、无害的降级手段。
    """
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 - 兜底手段本身失败也须静默
        pass


def _run_command_capped(cmd: List[str]) -> None:
    """以子进程运行播放命令，并将播放时长限制在 ``MAX_DURATION_SECONDS`` 内。

    超过上限则强制结束子进程，保证单次提示音不超过 3 秒（需求 6.2）。
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        proc.wait(timeout=MAX_DURATION_SECONDS)
    except subprocess.TimeoutExpired:
        # 播放过长时强制结束，避免占用与超时（需求 6.2）。
        proc.kill()
        proc.wait()


class SoundPlayer:
    """跨平台提示音播放器。

    构造时按 ``sys.platform`` 选定后端；``play()`` 每次在后台线程触发一次播放，
    任何后端异常都被静默处理，不影响调用方主流程（需求 6.6）。
    """

    def __init__(self) -> None:
        # 记录当前平台标识，便于测试与排查。
        self._platform = sys.platform
        # 选定后端播放函数；若无可用后端则回退到终端响铃。
        self._backend: Callable[[], None] = self._select_backend()

    # ------------------------------------------------------------------
    # 后端选择
    # ------------------------------------------------------------------
    def _select_backend(self) -> Callable[[], None]:
        """根据 ``sys.platform`` 选择合适的后端播放函数。

        选择过程只做能力探测（是否有 ``winsound`` 模块、命令是否在 PATH、
        音效文件是否存在），不会实际发声。
        """
        platform = self._platform

        # Windows：使用标准库 winsound 蜂鸣。
        if platform.startswith("win"):
            if self._winsound_available():
                return self._play_windows
            return _terminal_bell

        # macOS：使用系统 afplay 播放内置音效。
        if platform == "darwin":
            sound_file = _first_existing(_MACOS_SOUND_CANDIDATES)
            if shutil.which("afplay") and sound_file:
                return lambda: _run_command_capped(["afplay", sound_file])
            return _terminal_bell

        # Linux 及其他类 Unix：优先 paplay，其次 aplay。
        sound_file = _first_existing(_LINUX_SOUND_CANDIDATES)
        if sound_file:
            if shutil.which("paplay"):
                return lambda: _run_command_capped(["paplay", sound_file])
            if shutil.which("aplay"):
                return lambda: _run_command_capped(["aplay", "-q", sound_file])
        # 无可用命令或音效文件：静默降级为终端响铃兜底（需求 6.6）。
        return _terminal_bell

    @staticmethod
    def _winsound_available() -> bool:
        """探测 ``winsound`` 标准库是否可用（仅 Windows 提供）。"""
        try:
            import winsound  # noqa: F401  # 仅用于能力探测
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _play_windows() -> None:
        """Windows 后端：发出一声短促蜂鸣（800Hz，500 毫秒，远小于 3 秒）。"""
        import winsound

        winsound.Beep(800, 500)

    # ------------------------------------------------------------------
    # 播放入口
    # ------------------------------------------------------------------
    def play(self) -> None:
        """播放一次提示音（≤3 秒），在后台线程执行且不阻塞主流程。

        任何后端异常都被静默吞掉：调用方无需 try/except，声音失败不影响
        弹窗/高亮等视觉提醒（需求 6.2 / 6.6）。
        """
        try:
            thread = threading.Thread(target=self._safe_play, daemon=True)
            thread.start()
        except Exception:  # noqa: BLE001 - 连线程都无法启动时也须静默降级
            logger.debug("提示音线程启动失败，已静默降级为仅视觉提醒。", exc_info=True)

    def _safe_play(self) -> None:
        """在后台线程内实际调用后端，捕获并吞掉所有异常（需求 6.6）。"""
        try:
            self._backend()
        except Exception:  # noqa: BLE001 - 声音后端失败一律静默降级
            logger.debug("提示音后端播放失败，已静默降级为仅视觉提醒。", exc_info=True)
