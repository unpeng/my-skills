# -*- coding: utf-8 -*-
"""Trade_Logger：交易记录组件。

职责（对齐设计文档 "Trade_Logger" 章节与需求 8.x）：
- ``add(action, price)``：校验操作类型与成交价格后，调用 qstock 决策日志能力
  ``append_decision_log`` 将记录追加写入本地日志；校验不通过则返回错误结果且不写入。
- ``list(limit=50)``：调用 ``read_decision_log`` 读取 588170 已保存记录，按时间由早到晚
  返回最多 50 条；无记录返回空列表。

本组件仅做输入校验与对 qstock 决策日志的封装，不触碰 UI、不做网络 I/O。
"""

from __future__ import annotations

from typing import List, Optional

from . import ensure_qstock_on_path
from .models import AddResult, LogEntry

# 加入 <技能根>/qstock 到 sys.path 后，即可像 qstock 内部模块一样直接导入决策日志能力。
ensure_qstock_on_path()
from strategy.position_store import append_decision_log, read_decision_log  # noqa: E402

# 标的固定为 588170，本功能不支持切换其他标的。
_CODE = "588170"

# 合法操作类型集合（需求 8.1/8.3）。
_VALID_ACTIONS = {"做T买入", "做T卖出", "止损", "减仓"}


class TradeLogger:
    """交易记录：输入校验 + 调用 qstock 决策日志 + 读取展示。"""

    def add(self, action: Optional[str], price: Optional[float]) -> AddResult:
        """提交一条操作记录。

        Args:
            action: 操作类型，须为 做T买入/做T卖出/止损/减仓 之一。
            price: 成交价格，须为大于 0 的数值。

        Returns:
            AddResult：写入成功返回 ``ok=True`` 与成功提示；校验不通过返回 ``ok=False``
            与错误提示，且不写入本地日志文件（需求 8.2/8.3）。
        """
        # 需求 8.2：缺少操作类型或缺少成交价格 → 错误提示、不写入。
        # 操作类型缺失：None 或去除首尾空白后为空字符串。
        if action is None or (isinstance(action, str) and action.strip() == ""):
            return AddResult(ok=False, message="请填写操作类型")
        if price is None:
            return AddResult(ok=False, message="请填写成交价格")

        # 需求 8.3：操作类型不在合法集合内 → 错误提示、不写入。
        if action not in _VALID_ACTIONS:
            return AddResult(
                ok=False,
                message="操作类型不合法，仅支持：做T买入、做T卖出、止损、减仓",
            )

        # 需求 8.3：成交价格须为大于 0 的数值（拒绝非数值、bool 与 <=0）。
        # 注意：bool 是 int 的子类，需显式排除，避免 True 被当作 1 通过。
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            return AddResult(ok=False, message="成交价格必须为大于 0 的数值")
        if not price > 0:
            return AddResult(ok=False, message="成交价格必须为大于 0 的数值")

        # 需求 8.1：校验通过 → 调用决策日志追加写入本地文件。
        try:
            append_decision_log(code=_CODE, action=action, price=float(price))
        except Exception as exc:  # noqa: BLE001 - 写盘异常兜底，如实提示不崩溃
            return AddResult(ok=False, message="保存失败：%s" % exc)

        return AddResult(ok=True, message="操作记录已保存")

    def list(self, limit: int = 50) -> List[LogEntry]:
        """读取 588170 已保存的交易记录。

        Args:
            limit: 最多返回的记录条数，默认 50。

        Returns:
            按时间由早到晚排列的 ``LogEntry`` 列表，最多 ``limit`` 条；无记录返回空列表
            （需求 8.4/8.5）。``read_decision_log`` 返回的是按追加顺序（即时间由早到晚）
            的最近 ``limit`` 条记录，此处直接沿用其顺序。
        """
        raw_entries = read_decision_log(code=_CODE, limit=limit)
        entries: List[LogEntry] = []
        for item in raw_entries:
            entries.append(
                LogEntry(
                    time=item.get("time", ""),
                    code=item.get("code", _CODE),
                    action=item.get("action", ""),
                    price=item.get("price"),
                    shares=item.get("shares"),
                    note=item.get("note", "") or "",
                )
            )
        return entries
