# -*- coding: utf-8 -*-
"""
Position store module (D11) - 本地持久化用户持仓信息，避免每次会话
重复询问 [持仓数量]/[加权成本]/[可用资金]/止损设定。

同时提供简单的决策/交易记录能力（D12），方便后续复盘。

存储位置：<本技能根目录>/.local/positions.json
（相对于技能自身目录计算，与技能安装在磁盘何处无关，跟着技能文件夹整体
移动；纯本地文件，不上传、不联网，用户可随时删除。该路径已在 .gitignore
中按文件名忽略，不会被提交到版本库）
"""

import json
import os
from datetime import datetime

# 技能根目录：本文件路径为 <技能根目录>/qstock/strategy/position_store.py，
# 向上三级目录（strategy → qstock → 技能根目录）即可得到技能根目录
_SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_STORE_DIR = os.path.join(_SKILL_ROOT, ".local")
_POSITION_FILE = os.path.join(_STORE_DIR, "positions.json")
_LOG_FILE = os.path.join(_STORE_DIR, "decision_log.jsonl")


def _ensure_dir():
    os.makedirs(_STORE_DIR, exist_ok=True)


def _load_all() -> dict:
    if not os.path.exists(_POSITION_FILE):
        return {}
    try:
        with open(_POSITION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_position(code: str, position: float, cost: float, cash: float = 0.0,
                  max_loss_pct: float = None, max_loss_amount: float = None,
                  stop_loss_price: float = None, atr_stop_n: float = None) -> None:
    """保存/更新指定标的的持仓信息到本地文件。"""
    _ensure_dir()
    data = _load_all()
    data[str(code)] = {
        "position": position,
        "cost": cost,
        "cash": cash,
        "max_loss_pct": max_loss_pct,
        "max_loss_amount": max_loss_amount,
        "stop_loss_price": stop_loss_price,
        "atr_stop_n": atr_stop_n,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(_POSITION_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_position(code: str) -> dict:
    """读取指定标的已保存的持仓信息，未找到返回 None。"""
    data = _load_all()
    return data.get(str(code))


def clear_position(code: str) -> None:
    """清除指定标的已保存的持仓信息。"""
    data = _load_all()
    if str(code) in data:
        del data[str(code)]
        _ensure_dir()
        with open(_POSITION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def append_decision_log(code: str, action: str, price: float,
                        shares: float = None, note: str = "") -> None:
    """
    D12: 追加一条决策/交易记录，便于后续复盘技能给出的建议是否有效。

    Args:
        code: 标的代码
        action: 操作类型，如 "止损清仓"/"做T买入"/"做T卖出"/"减仓"
        price: 成交或决策价格
        shares: 涉及份数（可选）
        note: 备注（可选）
    """
    _ensure_dir()
    entry = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "code": code,
        "action": action,
        "price": price,
        "shares": shares,
        "note": note,
    }
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_decision_log(code: str = None, limit: int = 50) -> list:
    """读取决策/交易记录，可按标的代码过滤。"""
    if not os.path.exists(_LOG_FILE):
        return []
    entries = []
    with open(_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if code is None or entry.get("code") == str(code):
                entries.append(entry)
    return entries[-limit:]
