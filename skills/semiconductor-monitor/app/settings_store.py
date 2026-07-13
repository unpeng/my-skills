# -*- coding: utf-8 -*-
"""Settings_Store：应用配置持久化组件。

负责读写 ``<技能根>/.local/app_settings.json``，持久化轮询间隔、大模型接口三项配置、
声音开关与免责声明确认位。缺省（文件不存在或损坏）时回退到默认配置。

配置结构（对齐设计文档 "Data Models" 章节）::

    {
      "poll_interval_seconds": 60,
      "sound_enabled": true,
      "disclaimer_acknowledged": false,
      "llm": {"base_url": "", "api_key": "", "model": ""}
    }

需求覆盖：2.2（默认间隔 60）、2.3/2.4（间隔校验 [5,3600] 整数）、6.6（声音开关）、
7.1（大模型三项配置持久化）、9.3（免责声明确认位）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

from . import skill_root
from .models import LLMConfig

# 轮询间隔的合法边界与默认值（需求 2.2/2.3/2.4）。
DEFAULT_INTERVAL = 60
MIN_INTERVAL = 5
MAX_INTERVAL = 3600


@dataclass
class SetResult:
    """配置写入结果（供 set_interval 等写入方法返回）。"""

    ok: bool                        # 是否成功写入
    message: str = ""               # 成功确认或错误提示文本
    value: Optional[int] = None     # 成功时写入的值；失败时为保留的原值


def _default_config() -> dict:
    """返回一份全新的默认配置字典。"""
    return {
        "poll_interval_seconds": DEFAULT_INTERVAL,
        "sound_enabled": True,
        "disclaimer_acknowledged": False,
        "llm": {"base_url": "", "api_key": "", "model": ""},
    }


def _is_valid_interval(seconds) -> bool:
    """轮询间隔是否为 [5,3600]（含端点）区间内的整数（需求 2.3/2.4）。

    仅接受 ``int`` 类型且排除 ``bool``（``bool`` 是 ``int`` 子类），
    非整数（如浮点数）一律视为非法。
    """
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        return False
    return MIN_INTERVAL <= seconds <= MAX_INTERVAL


class SettingsStore:
    """应用配置的持久化读写。

    每个读方法都从磁盘加载最新配置并对缺失键回填默认值，保证即便配置文件
    被外部损坏或字段缺失也能稳健返回；写方法则整体回写合并后的配置。
    """

    def __init__(self, settings_path: Optional[str] = None):
        """初始化。

        Args:
            settings_path: 配置文件绝对路径；缺省时使用 ``<技能根>/.local/app_settings.json``。
        """
        if settings_path is None:
            settings_path = os.path.join(skill_root(), ".local", "app_settings.json")
        self._path = settings_path
        # 若配置文件不存在，立即创建一份默认配置落盘（需求：缺省时创建默认配置）。
        if not os.path.exists(self._path):
            self._save(_default_config())

    # ------------------------------------------------------------------
    # 底层读写
    # ------------------------------------------------------------------
    def _ensure_dir(self) -> None:
        """确保配置文件所在目录存在。"""
        os.makedirs(os.path.dirname(self._path), exist_ok=True)

    def _load(self) -> dict:
        """从磁盘加载配置；文件缺失/损坏时回退默认配置，并对缺失键回填默认值。"""
        cfg = _default_config()
        if not os.path.exists(self._path):
            return cfg
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            # 文件损坏时不抛出，回退默认配置以保证读方法稳健。
            return cfg
        if not isinstance(data, dict):
            return cfg
        # 顶层字段回填默认值。
        cfg["poll_interval_seconds"] = data.get("poll_interval_seconds", cfg["poll_interval_seconds"])
        cfg["sound_enabled"] = data.get("sound_enabled", cfg["sound_enabled"])
        cfg["disclaimer_acknowledged"] = data.get("disclaimer_acknowledged", cfg["disclaimer_acknowledged"])
        # llm 段回填默认值。
        llm = data.get("llm")
        if isinstance(llm, dict):
            cfg["llm"] = {
                "base_url": llm.get("base_url", ""),
                "api_key": llm.get("api_key", ""),
                "model": llm.get("model", ""),
            }
        return cfg

    def _save(self, cfg: dict) -> None:
        """把配置整体回写磁盘。"""
        self._ensure_dir()
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # 轮询间隔（需求 2.2/2.3/2.4）
    # ------------------------------------------------------------------
    def get_interval(self) -> int:
        """返回当前轮询间隔（秒）。

        已保存值非法（越界或非整数）时回退默认 60 秒（需求 2.2）。
        """
        value = self._load().get("poll_interval_seconds", DEFAULT_INTERVAL)
        if _is_valid_interval(value):
            return value
        return DEFAULT_INTERVAL

    def set_interval(self, seconds) -> SetResult:
        """设置轮询间隔。

        仅当 ``seconds`` 为 [5,3600]（含端点）区间内的整数时持久化并生效（需求 2.3）；
        否则拒绝写入并保留原间隔不变，返回指示越界的错误提示（需求 2.4）。
        """
        if not _is_valid_interval(seconds):
            return SetResult(
                ok=False,
                message="轮询间隔需为 5 至 3600 秒之间的整数，输入值超出允许范围。",
                value=self.get_interval(),
            )
        cfg = self._load()
        cfg["poll_interval_seconds"] = seconds
        self._save(cfg)
        return SetResult(ok=True, message="轮询间隔已保存。", value=seconds)

    # ------------------------------------------------------------------
    # 大模型接口配置（需求 7.1）
    # ------------------------------------------------------------------
    def get_llm_config(self) -> LLMConfig:
        """读取已持久化的大模型接口三项配置。"""
        llm = self._load().get("llm", {})
        return LLMConfig(
            base_url=llm.get("base_url", ""),
            api_key=llm.get("api_key", ""),
            model=llm.get("model", ""),
        )

    def set_llm_config(self, cfg: LLMConfig) -> None:
        """持久化大模型接口三项配置，使其在应用重启后仍可读取（需求 7.1）。"""
        data = self._load()
        data["llm"] = {
            "base_url": cfg.base_url,
            "api_key": cfg.api_key,
            "model": cfg.model,
        }
        self._save(data)

    # ------------------------------------------------------------------
    # 声音开关（需求 6.6）
    # ------------------------------------------------------------------
    def is_sound_enabled(self) -> bool:
        """声音提醒是否处于开启状态。"""
        return bool(self._load().get("sound_enabled", True))

    def set_sound_enabled(self, enabled: bool) -> None:
        """设置声音提醒开关并持久化。"""
        data = self._load()
        data["sound_enabled"] = bool(enabled)
        self._save(data)

    # ------------------------------------------------------------------
    # 免责声明确认位（需求 9.3）
    # ------------------------------------------------------------------
    def is_disclaimer_acknowledged(self) -> bool:
        """用户是否已完成首次启动的免责声明确认。"""
        return bool(self._load().get("disclaimer_acknowledged", False))

    def acknowledge_disclaimer(self) -> None:
        """记录用户已确认免责声明（写回确认位），供下次启动读取（需求 9.3）。"""
        data = self._load()
        data["disclaimer_acknowledged"] = True
        self._save(data)
