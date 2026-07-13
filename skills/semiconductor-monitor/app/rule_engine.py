# -*- coding: utf-8 -*-
"""Rule_Engine：交易时段判定 + 决策树 + 信号规则（纯逻辑核心）。

本模块是应用最核心的**纯函数逻辑**，不做任何 I/O、不触碰 UI、不依赖网络，
便于属性测试对其做大量随机输入验证。

本文件已实现**交易时段判定**（``classify_session``）与 ``RuleEngine`` 的
**分时段决策树**（``session_advice``）；买卖/止损信号（``evaluate_signals``）
由后续独立任务实现。

``TradingSession``/``SessionAdvice`` 等数据模型统一在 ``models`` 中定义，
此处直接复用（不重复定义）。
"""

from __future__ import annotations

import math
from datetime import datetime, time
from typing import List, Optional, Tuple

# 复用共享数据模型中的交易时段枚举、时段建议对象与信号对象（不在此重复定义）。
from .models import SessionAdvice, Signal, TradingSession


# ---------------------------------------------------------------------------
# 交易时段时间边界（半开区间 [start, end)，即包含 start、不包含 end）。
#
# 之所以用"下一分钟整点"作为不含的上界来表达"到 xx:59 秒为止"的闭区间，
# 是因为半开区间能天然保证各时段互斥且穷尽，杜绝边界重叠或空档：
#   - 集合竞价：09:15:00–09:25:59  ->  [09:15:00, 09:26:00)
#   - 开盘：    09:30:00–09:59:59  ->  [09:30:00, 10:00:00)
#   - 盘中：    10:00:00–11:29:59  ->  [10:00:00, 11:30:00)
#              13:00:00–13:59:59  ->  [13:00:00, 14:00:00)
#   - 尾盘：    14:00:00–14:59:59  ->  [14:00:00, 15:00:00)
#   - 其余时间（含 09:26:00–09:29:59、11:30:00–12:59:59、15:00:00 之后、
#     以及 09:15:00 之前）均归为非交易时段。
# ---------------------------------------------------------------------------
_CALL_AUCTION_START = time(9, 15, 0)
_CALL_AUCTION_END = time(9, 26, 0)      # 不含，等价于闭区间到 09:25:59

_OPENING_START = time(9, 30, 0)
_OPENING_END = time(10, 0, 0)           # 不含，等价于闭区间到 09:59:59

_INTRADAY_AM_START = time(10, 0, 0)
_INTRADAY_AM_END = time(11, 30, 0)      # 不含，等价于闭区间到 11:29:59
_INTRADAY_PM_START = time(13, 0, 0)
_INTRADAY_PM_END = time(14, 0, 0)       # 不含，等价于闭区间到 13:59:59

_CLOSING_START = time(14, 0, 0)
_CLOSING_END = time(15, 0, 0)           # 不含，等价于闭区间到 14:59:59


def _in_range(t: time, start: time, end: time) -> bool:
    """判断时间 ``t`` 是否落在半开区间 ``[start, end)`` 内。"""
    return start <= t < end


def classify_session(now: datetime, is_trading_day: bool) -> TradingSession:
    """把某一时间点归入五类互斥且穷尽的交易时段之一（需求 4.5）。

    交易日判定由上层根据数据新鲜度传入（``is_trading_day``）：非交易日全天
    直接归为非交易时段，不再按时间细分。

    Args:
        now: 待判定的时间点（仅使用其时刻部分做时段归属）。
        is_trading_day: 当日是否为交易日。为 ``False`` 时全天归非交易时段。

    Returns:
        该时间点所属的 :class:`TradingSession`。五类时段互斥且穷尽地覆盖全天，
        任一输入都恰好归入且仅归入一类。
    """
    # 非交易日：全天归非交易时段（需求 4.5）。
    if not is_trading_day:
        return TradingSession.NON_TRADING

    t = now.time()

    # 集合竞价 09:15:00–09:25:59。
    if _in_range(t, _CALL_AUCTION_START, _CALL_AUCTION_END):
        return TradingSession.CALL_AUCTION

    # 开盘 09:30:00–09:59:59。
    if _in_range(t, _OPENING_START, _OPENING_END):
        return TradingSession.OPENING

    # 盘中 10:00:00–11:29:59 或 13:00:00–13:59:59。
    if _in_range(t, _INTRADAY_AM_START, _INTRADAY_AM_END) or _in_range(
        t, _INTRADAY_PM_START, _INTRADAY_PM_END
    ):
        return TradingSession.INTRADAY

    # 尾盘 14:00:00–14:59:59。
    if _in_range(t, _CLOSING_START, _CLOSING_END):
        return TradingSession.CLOSING

    # 其余时间（时段间隙、开盘前、收盘后）均归非交易时段（需求 4.5）。
    return TradingSession.NON_TRADING


# ---------------------------------------------------------------------------
# 分时段决策树辅助函数（纯逻辑，无 I/O）。
#
# 决策树代码化的核心是"有序区间边界列表 + 降序级联判定"：把 SKILL.md 中的价位
# 比较条件表达为一串**从高到低**排列的阈值，价格从最高阈值开始逐一比较，命中首个
# ``价格 > 阈值`` 的区间即归入对应情景；列表最后一项阈值为 None 作为兜底分支
# （承接"价格 ≤ 前一阈值"的全部剩余情况）。
#
# 这种级联结构天然保证"互斥且穷尽"：无论各关键价位之间的相对大小关系如何
# （例如加权成本可能高于或低于昨收±2%），任一价格都必然落入且仅落入一个区间，
# 匹配计数恒为 1，既不会重叠也不会出现空档（属性测试 Property 2/3/4 的重点）。
# ---------------------------------------------------------------------------

# 降序区间边界列表的元素：(上界阈值 或 None, 情景标签, 建议正文)。
_Bound = Tuple[Optional[float], str, str]


def _num(value) -> Optional[float]:
    """安全取数：None / NaN / 非数值一律返回 None，供信号判断"先判 None 再比较"。

    需求 5.5/5.6 要求任何比较在取值前先判空——本函数把一切无法参与数值比较的取值
    （None、NaN、非数字字符串等）统一归一为 None，使调用方只需判断"是否为 None"即可
    决定该条件是否"可参与"，杜绝 None 进入比较表达式导致异常。
    """
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _has_missing(variables: dict, keys: List[str]) -> bool:
    """判断 ``variables`` 中 ``keys`` 是否存在缺失或为 None 的关键价位（需求 4.7）。

    只要任一必需关键价位缺失或为 None，就应降级为"数据不可用"、不产出情景建议。
    """
    for key in keys:
        if variables.get(key) is None:
            return True
    return False


def _classify_descending(value: float, bounds: List[_Bound]) -> Tuple[str, str]:
    """按降序阈值级联把 ``value`` 归入唯一区间，返回 ``(情景标签, 建议正文)``。

    ``bounds`` 必须从高到低排列，且最后一项阈值为 None 作为兜底。命中规则为首个满足
    ``value > 阈值`` 的区间；阈值为 None 时无条件命中。该结构保证互斥且穷尽——
    任一 ``value`` 恰好命中一个区间（匹配计数恒为 1）。
    """
    for threshold, scenario, advice in bounds:
        if threshold is None or value > threshold:
            return scenario, advice
    # 正常不会到达：最后一项阈值为 None 已兜底。防御性返回最后一个分支。
    return bounds[-1][1], bounds[-1][2]


class RuleEngine:
    """规则引擎：把 SKILL.md 分时段决策树与买卖/止损信号代码化的纯逻辑核心。

    本类不做任何 I/O、不触碰 UI、不依赖网络，便于属性测试对其做大量随机输入验证。
    当前实现 :meth:`session_advice`（分时段决策树）；:meth:`evaluate_signals`
    （买卖/止损信号）由后续独立任务实现。
    """

    def session_advice(self, session: TradingSession, vars: dict) -> SessionAdvice:
        """依据当前交易时段与行情变量，产出该时段对应的操作建议（需求 4.1-4.4, 4.6, 4.7）。

        Args:
            session: 当前所处交易时段（由 :func:`classify_session` 判定）。
            vars: ``compute_monitor_variables`` 返回的盯盘变量字典。

        Returns:
            :class:`SessionAdvice`。集合竞价/开盘/尾盘时段归入唯一情景/分支；盘中展示
            做 T 机会与关键价位突破建议；非交易时段展示盘前研判提示、不匹配盘中决策树。
            当所需关键价位缺失或为 None 时返回 ``data_available=False`` 且不产出情景建议
            （需求 4.7）。
        """
        if session is TradingSession.CALL_AUCTION:
            return self._call_auction_advice(vars)
        if session is TradingSession.OPENING:
            return self._opening_advice(vars)
        if session is TradingSession.INTRADAY:
            return self._intraday_advice(vars)
        if session is TradingSession.CLOSING:
            return self._closing_advice(vars)
        # 非交易时段（含时段间隙、盘前、收盘后、非交易日）：不匹配盘中决策树（需求 4.5）。
        return SessionAdvice(
            session=session,
            scenario=None,
            advice_text="非交易时段：展示盘前研判信息，不匹配盘中决策树建议。",
            data_available=True,
        )

    # ------------------------------------------------------------------
    # 集合竞价情景 1-7（需求 4.1）。以竞价价（=当前价）与关键价位的区间归属确定唯一情景。
    # 降序阈值：成本+2% > 加权成本 > 昨收+2% > 昨收-2% > 昨收-2.5% > 昨收-4% > 兜底。
    # ------------------------------------------------------------------
    def _call_auction_advice(self, vars: dict) -> SessionAdvice:
        session = TradingSession.CALL_AUCTION
        required = ["当前价", "成本+2%", "加权成本", "昨收+2%", "昨收-2%", "昨收-2.5%", "昨收-4%"]
        if _has_missing(vars, required):
            return SessionAdvice(
                session, None, "集合竞价所需关键价位数据不足，暂不展示情景建议。", False
            )

        price = vars["当前价"]
        bounds: List[_Bound] = [
            (vars["成本+2%"], "情景1",
             "超预期高开，可能冲高回落。操作：不追高、等回调，开盘后回落到加权成本以下再考虑。"),
            (vars["加权成本"], "情景2",
             "接近回本，获利盘可能出逃。操作：观望不操作；若放量站稳加权成本可持有。"),
            (vars["昨收+2%"], "情景3",
             "反弹延续但距回本仍有距离。操作：观望看开盘方向；涨到加权成本附近考虑减仓。"),
            (vars["昨收-2%"], "情景4",
             "中性，等待方向选择。操作：不动等方向；跌到昨收-4%再买、涨到昨收+2%再卖。"),
            (vars["昨收-2.5%"], "情景5",
             "偏弱但未破位。操作：准备买入等企稳；开盘观察5分钟，企稳则在昨收-2%挂单买入。"),
            (vars["昨收-4%"], "情景6",
             "弱势、接近止损线。操作：谨慎不急于买入；等开盘看是否继续下跌，企稳再考虑。"),
            (None, "情景7",
             "可能暴跌、恐慌情绪。操作：不操作等恐慌盘结束；5分钟内反弹到昨收-2%以上为假跌，"
             "继续跌破止损位则准备止损。"),
        ]
        scenario, advice = _classify_descending(price, bounds)
        text = f"集合竞价{scenario}（竞价价 {price}）：{advice}"
        return SessionAdvice(session, scenario, text, True)

    # ------------------------------------------------------------------
    # 开盘情景 A-F（需求 4.2）。以开盘价（=当前价）与关键价位的区间归属确定唯一情景。
    # 降序阈值：加权成本 > 昨收+2% > 昨收-2% > 昨收-2.5% > 昨收-4% > 兜底。
    # ------------------------------------------------------------------
    def _opening_advice(self, vars: dict) -> SessionAdvice:
        session = TradingSession.OPENING
        required = ["当前价", "加权成本", "昨收+2%", "昨收-2%", "昨收-2.5%", "昨收-4%"]
        if _has_missing(vars, required):
            return SessionAdvice(
                session, None, "开盘所需关键价位数据不足，暂不展示情景建议。", False
            )

        price = vars["当前价"]
        bounds: List[_Bound] = [
            (vars["加权成本"], "开盘情景A",
             "开盘价在加权成本上方。不追高，观察前1分钟走势；站稳加权成本则持有，"
             "涨到成本+2%挂卖20%锁利，跌破加权成本则观望。"),
            (vars["昨收+2%"], "开盘情景B",
             "开盘价在昨收+2%与加权成本之间。开盘观望看量能；上涨到加权成本考虑减仓，"
             "站稳昨收+3%以上持有等回本，跌破昨收价则观望不急于割肉。"),
            (vars["昨收-2%"], "开盘情景C",
             "开盘价在昨收±2%区间。开盘不动看方向；涨到昨收+2%挂卖20%，"
             "跌到做T买入位挂买做T份数，区间内震荡则不动。"),
            (vars["昨收-2.5%"], "开盘情景D",
             "开盘价在昨收-2.5%与昨收-2%之间。观察是否企稳；在昨收-2%附近企稳可挂单买入，"
             "跌破昨收-2.5%则不买等更低位置，跌破止损位分批止损清50%。"),
            (vars["昨收-4%"], "开盘情景E",
             "开盘价在昨收-4%与昨收-2.5%之间。谨慎观察恐慌程度；跌破止损位准备止损，"
             "反弹到昨收-2%以上可持有。"),
            (None, "开盘情景F",
             "开盘价跌破昨收-4%，恐慌低开。先不操作观察；反弹到昨收-2.5%以上再确认，"
             "跌破止损位则分批止损先清50%。"),
        ]
        scenario, advice = _classify_descending(price, bounds)
        text = f"{scenario}（开盘价 {price}）：{advice}"
        return SessionAdvice(session, scenario, text, True)

    # ------------------------------------------------------------------
    # 尾盘决策树（需求 4.3）。以当前价相对加权成本、成本×0.98、成本×0.96、止损位的
    # 区间归属确定唯一分支。降序阈值：加权成本 > 成本×0.98 > 成本×0.96 > 止损位 > 兜底。
    # ------------------------------------------------------------------
    def _closing_advice(self, vars: dict) -> SessionAdvice:
        session = TradingSession.CLOSING
        required = ["当前价", "加权成本", "止损位"]
        if _has_missing(vars, required):
            return SessionAdvice(
                session, None, "尾盘所需关键价位数据不足，暂不展示决策建议。", False
            )

        price = vars["当前价"]
        cost = vars["加权成本"]
        stop = vars["止损位"]
        bounds: List[_Bound] = [
            (cost, "尾盘-盈利", "盈利。考虑全部或部分卖出，落袋为安。"),
            (cost * 0.98, "尾盘-小赚",
             "小赚。若今日做过T则卖出T的部分锁定利润；未做T则继续持有等明天。"),
            (cost * 0.96, "尾盘-持平", "基本持平。持有不动，等明天。"),
            (stop, "尾盘-小亏",
             "小亏。观察是否有反弹迹象：有反弹迹象则持有，继续下跌则考虑减仓一半。"),
            (None, "尾盘-大亏",
             "大亏，已触及/跌破止损位。分批止损：首次跌破先清50%观察走势，"
             "若继续下跌或反弹后再破位则清剩余仓位。"),
        ]
        scenario, advice = _classify_descending(price, bounds)
        text = f"{scenario}（当前价 {price}，加权成本 {cost}，止损位 {stop}）：{advice}"
        return SessionAdvice(session, scenario, text, True)

    # ------------------------------------------------------------------
    # 盘中监控（需求 4.4, 4.6）。展示做 T 挂单机会与昨收±2% 关键价位突破建议；
    # 做 T 可买份数直接复用 compute_monitor_variables 已算好的字段（需求 4.6）。
    # 盘中非离散情景分类，故不返回单一 scenario 标签。
    # ------------------------------------------------------------------
    def _intraday_advice(self, vars: dict) -> SessionAdvice:
        session = TradingSession.INTRADAY
        required = ["当前价", "做T买入位", "做T卖出位", "昨收+2%", "昨收-2%"]
        if _has_missing(vars, required):
            return SessionAdvice(
                session, None, "盘中所需关键价位数据不足，暂不展示做T与突破建议。", False
            )

        price = vars["当前价"]
        t_buy = vars["做T买入位"]
        t_sell = vars["做T卖出位"]
        up = vars["昨收+2%"]
        down = vars["昨收-2%"]

        parts: List[str] = []

        # 做 T 机会：做T可买份数复用 vars 的 做T可买份数/做T可用资金上限（需求 4.6）。
        # 二者仅在提供了可用资金时才由 compute_monitor_variables 产出，缺失时如实说明。
        t_shares = vars.get("做T可买份数")
        t_cash_cap = vars.get("做T可用资金上限")
        if t_shares is not None:
            parts.append(
                f"做T机会：可在做T买入位 {t_buy} 挂买约 {t_shares} 份"
                f"（做T可用资金上限 {t_cash_cap}），涨到做T卖出位 {t_sell} 挂卖锁定收益。"
            )
        else:
            parts.append(
                f"做T机会：可在做T买入位 {t_buy} 挂买、涨到做T卖出位 {t_sell} 挂卖；"
                "做T可买份数需先配置可用资金后计算。"
            )

        # 关键价位突破：以当前价相对昨收±2% 判定突破/跌破/区间震荡。
        if price >= up:
            parts.append(
                f"关键价位：当前价 {price} 已突破昨收+2%（{up}），关注涨势延续，可分批减仓锁利。"
            )
        elif price <= down:
            parts.append(
                f"关键价位：当前价 {price} 已跌破昨收-2%（{down}），警惕继续走弱，留意止损位。"
            )
        else:
            parts.append(
                f"关键价位：当前价 {price} 在昨收±2%（{down}~{up}）区间内震荡，暂无突破。"
            )

        text = "盘中监控：" + " ".join(parts)
        return SessionAdvice(session, None, text, True)

    # ==================================================================
    # 买入 / 卖出 / 止损 / 放量下跌止损 信号（需求 5.1-5.7）。
    #
    # 设计要点：
    #   - 每个条件表达为"可参与 + 是否成立"两个布尔量。某条件依赖的任一取值为 None
    #     （指标数据不足或比较基准缺失）时，该条件既不计成立也不计不成立，直接被排除出
    #     该类信号的"可参与条件"集合（需求 5.5/5.6）。
    #   - 买入/卖出为"条件集合"型信号：可参与条件数 < 2 不生成；可参与条件中成立数 ≥ 2
    #     才生成（需求 5.1/5.2/5.7）。
    #   - 止损、放量下跌止损为"独立单信号"型：各自条件全部满足即生成（需求 5.3/5.4）。
    #   - 所有比较前先经 :func:`_num` 归一，杜绝 None 进入数值比较。
    # ==================================================================

    # 买入/卖出成立所需的最少条件数与最少可参与条件数（需求 5.7/5.1/5.2）。
    _MIN_PARTICIPATING = 2
    _MIN_ESTABLISHED = 2

    def evaluate_signals(
        self, vars: dict, prev_vars: Optional[dict] = None
    ) -> List[Signal]:
        """依据本轮盯盘变量产出买入/卖出/止损/放量下跌止损信号列表（需求 5.1-5.7）。

        本方法为纯逻辑：不做 I/O、不触碰 UI、不依赖网络，便于属性测试大量随机输入验证。

        Args:
            vars: ``compute_monitor_variables`` 返回的盯盘变量字典。跨周期 MACD 柱值可由
                宿主以 ``macd_hist_prev`` / ``macd_hist_curr`` 两个派生键放入本字典
                （见 ``variable_calculator.py`` 的 ``RoundResult`` 派生字段）。
            prev_vars: 上一轮的变量字典（可选）。当 ``vars`` 未携带派生 MACD 键时，退化为
                用本轮 ``MACD_HIST`` 作当前周期、上一轮 ``MACD_HIST`` 作上一周期。

        Returns:
            本轮生成的 :class:`Signal` 列表（可能为空）。每个信号携带 ``kind``、
            ``trigger_price``、``related_price``（关联关键价格，供 Alert_Manager 去重）、
            ``reasons``（成立的具体条件描述）与 ``triggered_at``。
        """
        now = datetime.now()
        macd_prev, macd_curr = self._macd_hist_pair(vars, prev_vars)
        cur = _num(vars.get("当前价"))

        signals: List[Signal] = []

        buy = self._eval_buy(vars, cur, macd_prev, macd_curr, now)
        if buy is not None:
            signals.append(buy)

        sell = self._eval_sell(vars, cur, macd_prev, macd_curr, now)
        if sell is not None:
            signals.append(sell)

        stop = self._eval_stop_loss(vars, cur, now)
        if stop is not None:
            signals.append(stop)

        vol_stop = self._eval_volume_stop_loss(vars, cur, now)
        if vol_stop is not None:
            signals.append(vol_stop)

        return signals

    # ------------------------------------------------------------------
    # 跨周期 MACD 柱值取用（需求 5.1/5.2 的 MACD 条件依赖上一周期与当前周期柱值）。
    # ------------------------------------------------------------------
    @staticmethod
    def _macd_hist_pair(
        vars: dict, prev_vars: Optional[dict]
    ) -> Tuple[Optional[float], Optional[float]]:
        """解析 ``(macd_hist_prev, macd_hist_curr)``，兼容两种宿主传入约定。

        优先级：
          1. 若 ``vars`` 显式携带派生键 ``macd_hist_prev`` / ``macd_hist_curr``
             （Variable_Calculator 在同一轮内取指标 DataFrame 末两根柱值派生，最准确），
             直接采用（缺失者归一为 None）。
          2. 否则退化为跨轮近似：当前周期取本轮 ``MACD_HIST``、上一周期取 ``prev_vars``
             的 ``MACD_HIST``（无上一轮时为 None）。
        """
        if "macd_hist_prev" in vars or "macd_hist_curr" in vars:
            return _num(vars.get("macd_hist_prev")), _num(vars.get("macd_hist_curr"))
        curr = _num(vars.get("MACD_HIST"))
        prev = _num(prev_vars.get("MACD_HIST")) if isinstance(prev_vars, dict) else None
        return prev, curr

    # ------------------------------------------------------------------
    # 买入信号（需求 5.1/5.5/5.6/5.7）。
    # 条件集合：RSI<30、KDJ_J<0、当前价≤布林下轨、macd_hist_prev<0 且 macd_hist_curr≥0。
    # ------------------------------------------------------------------
    def _eval_buy(
        self,
        vars: dict,
        cur: Optional[float],
        macd_prev: Optional[float],
        macd_curr: Optional[float],
        now: datetime,
    ) -> Optional[Signal]:
        rsi = _num(vars.get("RSI"))
        kdj_j = _num(vars.get("KDJ_J"))
        boll_lower = _num(vars.get("布林下轨"))

        # 每个元素为 (是否成立, 成立描述)；仅"可参与"（依赖值均非 None）的条件才入列。
        conditions: List[Tuple[bool, str]] = []
        if rsi is not None:
            conditions.append((rsi < 30, f"RSI={rsi} 小于30（超卖）"))
        if kdj_j is not None:
            conditions.append((kdj_j < 0, f"KDJ-J={kdj_j} 小于0"))
        if cur is not None and boll_lower is not None:
            conditions.append((cur <= boll_lower, f"当前价{cur} ≤ 布林下轨{boll_lower}"))
        if macd_prev is not None and macd_curr is not None:
            conditions.append(
                (macd_prev < 0 and macd_curr >= 0,
                 f"MACD柱由负转正（上一周期{macd_prev}<0 且 当前周期{macd_curr}≥0）")
            )

        return self._build_set_signal(
            kind="买入",
            conditions=conditions,
            trigger_price=cur,
            related_price=boll_lower,
            now=now,
        )

    # ------------------------------------------------------------------
    # 卖出信号（需求 5.2/5.5/5.6/5.7）。
    # 条件集合：RSI>70、KDJ_J>100、当前价≥布林上轨、macd_hist_prev>0 且 macd_hist_curr≤0、
    #           回本距离绝对值 < 2%（=|(当前价−成本)/成本|×100%）。
    # ------------------------------------------------------------------
    def _eval_sell(
        self,
        vars: dict,
        cur: Optional[float],
        macd_prev: Optional[float],
        macd_curr: Optional[float],
        now: datetime,
    ) -> Optional[Signal]:
        rsi = _num(vars.get("RSI"))
        kdj_j = _num(vars.get("KDJ_J"))
        boll_upper = _num(vars.get("布林上轨"))
        cost = _num(vars.get("加权成本"))

        conditions: List[Tuple[bool, str]] = []
        if rsi is not None:
            conditions.append((rsi > 70, f"RSI={rsi} 大于70（超买）"))
        if kdj_j is not None:
            conditions.append((kdj_j > 100, f"KDJ-J={kdj_j} 大于100"))
        if cur is not None and boll_upper is not None:
            conditions.append((cur >= boll_upper, f"当前价{cur} ≥ 布林上轨{boll_upper}"))
        if macd_prev is not None and macd_curr is not None:
            conditions.append(
                (macd_prev > 0 and macd_curr <= 0,
                 f"MACD柱由正转负（上一周期{macd_prev}>0 且 当前周期{macd_curr}≤0）")
            )
        # 回本距离条件依赖当前价与成本；成本为 0 时无法计算，视为不可参与。
        if cur is not None and cost is not None and cost != 0:
            dist = abs((cur - cost) / cost) * 100.0
            conditions.append((dist < 2.0, f"回本距离{round(dist, 2)}% 小于2%（接近回本）"))

        return self._build_set_signal(
            kind="卖出",
            conditions=conditions,
            trigger_price=cur,
            related_price=boll_upper,
            now=now,
        )

    def _build_set_signal(
        self,
        *,
        kind: str,
        conditions: List[Tuple[bool, str]],
        trigger_price: Optional[float],
        related_price: Optional[float],
        now: datetime,
    ) -> Optional[Signal]:
        """按"条件集合"规则生成买入/卖出信号（需求 5.7/5.1/5.2）。

        可参与条件数（``len(conditions)``）< 2 时不生成；可参与条件中成立数 < 2 时不生成。
        成立数 ≥ 2 时生成，``reasons`` 为全部成立条件的描述。
        """
        if len(conditions) < self._MIN_PARTICIPATING:
            return None
        reasons = [desc for ok, desc in conditions if ok]
        if len(reasons) < self._MIN_ESTABLISHED:
            return None
        return Signal(
            kind=kind,
            trigger_price=trigger_price if trigger_price is not None else 0.0,
            related_price=related_price,
            reasons=reasons,
            triggered_at=now,
        )

    # ------------------------------------------------------------------
    # 止损信号（需求 5.3）。当前价 < 止损位即生成；止损位或当前价缺失则跳过（需求 5.6）。
    # ------------------------------------------------------------------
    def _eval_stop_loss(
        self, vars: dict, cur: Optional[float], now: datetime
    ) -> Optional[Signal]:
        stop = _num(vars.get("止损位"))
        if cur is None or stop is None:
            return None
        if cur < stop:
            return Signal(
                kind="止损",
                trigger_price=cur,
                related_price=stop,
                reasons=[f"当前价{cur} 低于止损位{stop}"],
                triggered_at=now,
            )
        return None

    # ------------------------------------------------------------------
    # 放量下跌止损信号（需求 5.4）。
    # 今日成交量 > 20日均量×1.5 且 当日跌幅 > 3%（当日跌幅=(昨收−当前价)/昨收×100%）。
    # 任一依赖值（今日成交量/20日均量/昨收价/当前价）缺失则跳过（需求 5.6）。
    # ------------------------------------------------------------------
    def _eval_volume_stop_loss(
        self, vars: dict, cur: Optional[float], now: datetime
    ) -> Optional[Signal]:
        volume = _num(vars.get("今日成交量"))
        vol_ma20 = _num(vars.get("20日均量"))
        prev_close = _num(vars.get("昨收价"))
        if volume is None or vol_ma20 is None or prev_close is None or cur is None:
            return None
        if prev_close == 0:
            return None

        volume_surge = volume > vol_ma20 * 1.5
        drop_pct = (prev_close - cur) / prev_close * 100.0
        if volume_surge and drop_pct > 3.0:
            return Signal(
                kind="放量下跌止损",
                trigger_price=cur,
                related_price=_num(vars.get("止损位")),
                reasons=[
                    f"今日成交量{volume} > 20日均量{vol_ma20}×1.5",
                    f"当日跌幅{round(drop_pct, 2)}% 大于3%",
                ],
                triggered_at=now,
            )
        return None
