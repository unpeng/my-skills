# -*- coding: utf-8 -*-
"""Position_Manager：持仓信息录入、校验、持久化与启动回填。

职责（对齐设计 "Position_Manager" 章节与需求 1）：
- ``validate``：把持仓表单原始字符串输入校验并转换为 ``PositionInput``，
  覆盖持仓数量、加权成本、可用资金以及止损三选一及其数值边界（需求 1.3/1.4/1.5/1.7）。
- ``save``：全部校验通过后调用 qstock 的 ``save_position`` 覆盖写入
  ``.local/positions.json``，并捕获写盘异常返回失败结果（需求 1.1/1.6/1.8）。
- ``load``：应用启动时读取已保存的 588170 持仓并回填（需求 1.2）。

复用约定：先经 ``ensure_qstock_on_path()`` 把 ``<技能根>/qstock`` 加入 ``sys.path``，
再 ``from strategy.position_store import save_position, load_position``，与 qstock
内部模块彼此 import 的现有约定保持一致；不改动 qstock 对外契约。
"""

from __future__ import annotations

from typing import Optional, Tuple

from . import ensure_qstock_on_path
from .models import PositionForm, PositionInput, SaveResult, ValidationResult

# 标的固定为 588170，本功能不支持切换其他标的。
SYMBOL = "588170"

# 各字段的合法边界（严格对齐需求 1.3/1.4/1.7）。
_POSITION_MIN = 1
_POSITION_MAX = 1_000_000_000
_COST_MIN = 0.01
_COST_MAX = 999_999.99
_CASH_MIN = 0.0
_CASH_MAX = 999_999_999.99
_LOSS_PCT_MIN_EXCLUSIVE = 0.0   # (0, 100]，下界开、上界闭
_LOSS_PCT_MAX = 100.0
# ATR 止损倍数 N 的合法区间 (0, 20]：0.5–3 为常用区间，放宽到 20 兜底极端输入。
_ATR_STOP_N_MIN_EXCLUSIVE = 0.0
_ATR_STOP_N_MAX = 20.0

# 确保导入 qstock 能力时其内部模块可被解析。
ensure_qstock_on_path()
from strategy.position_store import save_position, load_position  # noqa: E402


def _parse_int(raw: str) -> Tuple[bool, Optional[int]]:
    """把原始字符串解析为整数份额。

    仅接受整数值（如 "16000"、"16000.0" 视为整数）；非数值、含小数部分、
    NaN/Inf 一律解析失败。返回 (是否成功, 值)。
    """
    text = (raw or "").strip()
    if not text:
        return False, None
    try:
        num = float(text)
    except (TypeError, ValueError):
        return False, None
    # 排除 NaN / Inf 与含小数部分的值。
    if num != num or num in (float("inf"), float("-inf")):
        return False, None
    if not float(num).is_integer():
        return False, None
    return True, int(num)


def _parse_float(raw: str) -> Tuple[bool, Optional[float]]:
    """把原始字符串解析为浮点数；空/非数值/NaN/Inf 解析失败。"""
    text = (raw or "").strip()
    if not text:
        return False, None
    try:
        num = float(text)
    except (TypeError, ValueError):
        return False, None
    if num != num or num in (float("inf"), float("-inf")):
        return False, None
    return True, num


class PositionManager:
    """持仓录入与管理组件。"""

    def __init__(self, code: str = SYMBOL) -> None:
        # 标的代码固定，构造参数仅为便于测试注入。
        self._code = str(code)

    # ------------------------------------------------------------------ #
    # 需求 1.2：启动回填
    # ------------------------------------------------------------------ #
    def load(self) -> Optional[PositionInput]:
        """读取已保存的持仓信息并转换为 ``PositionInput``；未找到返回 None。"""
        data = load_position(self._code)
        if not data:
            return None
        return PositionInput(
            position=int(data.get("position") or 0),
            cost=float(data.get("cost") or 0.0),
            cash=float(data.get("cash") or 0.0),
            max_loss_pct=data.get("max_loss_pct"),
            max_loss_amount=data.get("max_loss_amount"),
            stop_loss_price=data.get("stop_loss_price"),
            atr_stop_n=data.get("atr_stop_n"),
            updated_at=data.get("updated_at"),
        )

    # ------------------------------------------------------------------ #
    # 需求 1.3/1.4/1.5/1.7：表单校验
    # ------------------------------------------------------------------ #
    def validate(self, form: PositionForm) -> ValidationResult:
        """校验持仓表单，返回校验结果；全部通过时携带转换出的 ``PositionInput``。

        校验规则：
        - 持仓数量 ∈ [1, 1_000_000_000] 的正整数（需求 1.3）。
        - 加权成本 ∈ [0.01, 999_999.99]（需求 1.3）。
        - 可用资金 ∈ [0, 999_999_999.99] 的非负数（需求 1.4）。
        - 止损四选一：最大亏损比例/最大亏损金额/直接指定止损价/ATR止损倍数恰好选一种（需求 1.5）。
        - 止损数值边界：比例 ∈ (0, 100]、金额为正数、止损价为正数且低于加权成本、
          ATR 止损倍数 ∈ (0, 20]（需求 1.7）。
        """
        errors: dict = {}

        # --- 持仓数量：正整数且 ∈ [1, 1e9] ---
        ok_pos, position = _parse_int(form.position)
        if not ok_pos or position is None:
            errors["position"] = "持仓数量必须为 1 至 1,000,000,000 的正整数份额"
        elif position < _POSITION_MIN or position > _POSITION_MAX:
            errors["position"] = "持仓数量必须为 1 至 1,000,000,000 的正整数份额"

        # --- 加权成本：∈ [0.01, 999_999.99] ---
        ok_cost, cost = _parse_float(form.cost)
        if not ok_cost or cost is None:
            errors["cost"] = "加权成本必须为 0.01 至 999,999.99 的正数"
        elif cost < _COST_MIN or cost > _COST_MAX:
            errors["cost"] = "加权成本必须为 0.01 至 999,999.99 的正数"

        # --- 可用资金：∈ [0, 999_999_999.99]，空视为 0 ---
        cash_text = (form.cash or "").strip()
        if not cash_text:
            cash = _CASH_MIN
        else:
            ok_cash, parsed_cash = _parse_float(form.cash)
            if not ok_cash or parsed_cash is None:
                errors["cash"] = "可用资金必须为 0 至 999,999,999.99 的非负数"
                cash = None
            elif parsed_cash < _CASH_MIN or parsed_cash > _CASH_MAX:
                errors["cash"] = "可用资金必须为 0 至 999,999,999.99 的非负数"
                cash = None
            else:
                cash = parsed_cash

        # --- 止损四选一：恰好一个字段非空 ---
        stop_raw = {
            "max_loss_pct": (form.max_loss_pct or "").strip(),
            "max_loss_amount": (form.max_loss_amount or "").strip(),
            "stop_loss_price": (form.stop_loss_price or "").strip(),
            "atr_stop_n": (form.atr_stop_n or "").strip(),
        }
        filled = [name for name, val in stop_raw.items() if val]
        max_loss_pct = None
        max_loss_amount = None
        stop_loss_price = None
        atr_stop_n = None

        if len(filled) != 1:
            errors["stop_loss"] = (
                "止损设定必须在最大亏损比例、最大亏损金额、直接指定止损价、ATR止损倍数"
                "四种方式中恰好选择一种"
            )
        else:
            chosen = filled[0]
            if chosen == "max_loss_pct":
                ok_pct, pct = _parse_float(stop_raw["max_loss_pct"])
                # 最大亏损比例 ∈ (0, 100]（即 0.01%–100%），下界开、上界闭。
                if not ok_pct or pct is None or pct <= _LOSS_PCT_MIN_EXCLUSIVE or pct > _LOSS_PCT_MAX:
                    errors["max_loss_pct"] = "最大亏损比例必须在 0.01% 至 100% 之间"
                else:
                    max_loss_pct = pct
            elif chosen == "max_loss_amount":
                ok_amt, amt = _parse_float(stop_raw["max_loss_amount"])
                # 最大亏损金额必须为正数。
                if not ok_amt or amt is None or amt <= 0:
                    errors["max_loss_amount"] = "最大亏损金额必须为正数"
                else:
                    max_loss_amount = amt
            elif chosen == "stop_loss_price":
                ok_sp, sp = _parse_float(stop_raw["stop_loss_price"])
                # 直接指定止损价必须为正数，且低于加权成本。
                if not ok_sp or sp is None or sp <= 0:
                    errors["stop_loss_price"] = "直接指定止损价必须为正数且低于加权成本"
                elif ok_cost and cost is not None and sp >= cost:
                    errors["stop_loss_price"] = "直接指定止损价必须为正数且低于加权成本"
                else:
                    stop_loss_price = sp
            else:  # atr_stop_n
                ok_n, n = _parse_float(stop_raw["atr_stop_n"])
                # ATR 止损倍数必须为 (0, 20] 的正数。
                if not ok_n or n is None or n <= _ATR_STOP_N_MIN_EXCLUSIVE or n > _ATR_STOP_N_MAX:
                    errors["atr_stop_n"] = "ATR止损倍数必须为 0 到 20 之间的正数（不含0）"
                else:
                    atr_stop_n = n

        if errors:
            return ValidationResult(valid=False, errors=errors, value=None)

        value = PositionInput(
            position=position,
            cost=cost,
            cash=cash,
            max_loss_pct=max_loss_pct,
            max_loss_amount=max_loss_amount,
            stop_loss_price=stop_loss_price,
            atr_stop_n=atr_stop_n,
        )
        return ValidationResult(valid=True, errors={}, value=value)

    # ------------------------------------------------------------------ #
    # 需求 1.1/1.6/1.8：保存（覆盖写入，写盘异常保留输入）
    # ------------------------------------------------------------------ #
    def save(self, form: PositionForm) -> SaveResult:
        """校验通过后覆盖写入持仓信息；校验失败或写盘异常均返回失败结果。

        - 校验失败：返回 ``ok=False`` 并携带 ``ValidationResult``，不触碰持久化文件，
          原有 ``.local/positions.json`` 内容不变（需求 1.3/1.4/1.7）。
        - 校验通过：调用 ``save_position`` 用新值覆盖写入（需求 1.1/1.6）。
        - 写盘异常：捕获后返回保存失败提示，保留用户已输入内容（需求 1.8）。
        """
        validation = self.validate(form)
        if not validation.valid or validation.value is None:
            return SaveResult(
                ok=False,
                message="输入校验未通过，未保存持仓信息",
                validation=validation,
            )

        v = validation.value
        try:
            # 覆盖写入指定标的的持仓信息（save_position 内部即按标的代码覆盖）。
            save_position(
                self._code,
                position=v.position,
                cost=v.cost,
                cash=v.cash,
                max_loss_pct=v.max_loss_pct,
                max_loss_amount=v.max_loss_amount,
                stop_loss_price=v.stop_loss_price,
                atr_stop_n=v.atr_stop_n,
            )
        except Exception as exc:  # noqa: BLE001 - 写盘异常兜底，保留用户输入
            # 需求 1.8：保存失败时展示失败提示并保留用户已输入内容（value 原样带回）。
            return SaveResult(
                ok=False,
                message=f"保存持仓信息失败：{exc}",
                validation=validation,
            )

        return SaveResult(ok=True, message="持仓信息保存成功", validation=validation)
