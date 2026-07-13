# -*- coding: utf-8 -*-
"""app 包：588170 本地桌面盯盘应用（Monitor_App）。

本包构建在既有 ``qstock`` 包之上，复用其行情获取、盯盘变量与技术指标计算、
本地持仓持久化与决策日志能力。为让各组件能像 ``qstock`` 内部模块一样直接
``from strategy.monitor import ...`` / ``from strategy.position_store import ...``，
本模块提供把 ``<技能根>/qstock`` 目录加入 ``sys.path`` 的复用辅助函数。
"""

import os
import sys

# 本文件路径为 <技能根>/app/__init__.py，其父目录的父目录即技能根目录。
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# qstock 包位于技能根目录下。
_QSTOCK_DIR = os.path.join(_SKILL_ROOT, "qstock")


def skill_root() -> str:
    """返回技能根目录的绝对路径（``app/`` 与 ``qstock/`` 的公共父目录）。"""
    return _SKILL_ROOT


def qstock_dir() -> str:
    """返回 ``qstock`` 目录的绝对路径。"""
    return _QSTOCK_DIR


def ensure_qstock_on_path() -> str:
    """把 ``<技能根>/qstock`` 加入 ``sys.path``（幂等），供各组件导入 qstock 能力。

    与 ``qstock`` 内部模块彼此 import 的现有约定保持一致：加入 qstock 目录后，
    即可 ``from strategy.monitor import compute_monitor_variables``、
    ``from strategy.position_store import save_position`` 等。

    Returns:
        加入到 ``sys.path`` 的 ``qstock`` 目录绝对路径。
    """
    if _QSTOCK_DIR not in sys.path:
        # 插入到最前，确保优先解析 qstock 内部模块（strategy/model/data 等）。
        sys.path.insert(0, _QSTOCK_DIR)
    return _QSTOCK_DIR
