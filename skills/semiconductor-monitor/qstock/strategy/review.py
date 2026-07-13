# -*- coding: utf-8 -*-
"""
决策日志复盘模块（改进5，借鉴 abu 的 UMP 拦截"从历史交易中学习"思想的简化版）。

读取 .local/decision_log.jsonl 中记录的实际操作（做T买入/做T卖出/止损清仓/
减仓等），配对计算已实现盈亏、胜率、总盈亏，用于复盘技能给出的建议历史
表现，为后续调参（k/n/档数/阈值）提供依据。

纯本地统计，不联网。数据由用户通过 `log add` 命令积累。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategy.position_store import read_decision_log


def _is_buy(action: str) -> bool:
    return action is not None and ("买" in action or "补仓" in action or "建仓" in action)


def _is_sell(action: str) -> bool:
    return action is not None and (
        "卖" in action or "止损" in action or "减仓" in action or "清仓" in action
    )


def review_decisions(code: str = None, limit: int = 1000) -> dict:
    """
    读取决策日志并配对统计做T/止损的已实现盈亏。

    采用 FIFO 配对：每笔卖出与最早未平仓的买入配对，按较小份数结算，
    剩余份数留待后续卖出继续配对（支持部分平仓）。

    Args:
        code: 标的代码，None 表示全部
        limit: 最多读取的日志条数

    Returns:
        dict：含配对明细 pairs、胜率、总已实现盈亏、买卖笔数等统计。
    """
    entries = read_decision_log(code=code, limit=limit)
    if not entries:
        return {"error": "暂无决策/交易记录", "记录数": 0}

    # 未平仓买入队列，元素为 [price, remaining_shares, time]
    open_buys = []
    pairs = []
    buy_count = 0
    sell_count = 0

    for e in entries:
        action = e.get("action", "")
        price = e.get("price")
        shares = e.get("shares")
        if price is None:
            continue

        if _is_buy(action):
            buy_count += 1
            qty = shares if shares else 0
            open_buys.append([float(price), float(qty), e.get("time")])
        elif _is_sell(action):
            sell_count += 1
            sell_qty = float(shares) if shares else 0.0
            sell_price = float(price)
            # 若卖出未记份数，则与最早一笔买入按其份数全额配对
            if sell_qty <= 0 and open_buys:
                sell_qty = open_buys[0][1]

            while sell_qty > 0 and open_buys:
                buy = open_buys[0]
                buy_price, buy_remaining, buy_time = buy
                matched = min(sell_qty, buy_remaining) if buy_remaining > 0 else sell_qty
                if matched <= 0:
                    # 买入未记份数，无法按份数配对，按1份象征结算避免死循环
                    matched = 1
                pnl = (sell_price - buy_price) * matched
                pairs.append({
                    "买入价": round(buy_price, 4),
                    "卖出价": round(sell_price, 4),
                    "份数": matched,
                    "盈亏": round(pnl, 2),
                    "动作": action,
                    "买入时间": buy_time,
                    "卖出时间": e.get("time"),
                })
                buy[1] = buy_remaining - matched
                sell_qty -= matched
                if buy[1] <= 0:
                    open_buys.pop(0)

    total_pnl = sum(p["盈亏"] for p in pairs)
    win = sum(1 for p in pairs if p["盈亏"] > 0)
    loss = sum(1 for p in pairs if p["盈亏"] < 0)
    n_pairs = len(pairs)
    win_rate = (win / n_pairs * 100) if n_pairs else 0.0

    return {
        "记录数": len(entries),
        "买入笔数": buy_count,
        "卖出笔数": sell_count,
        "已配对次数": n_pairs,
        "盈利次数": win,
        "亏损次数": loss,
        "胜率": round(win_rate, 1),
        "总已实现盈亏": round(total_pnl, 2),
        "平均每次盈亏": round(total_pnl / n_pairs, 2) if n_pairs else 0.0,
        "未平仓买入笔数": len(open_buys),
        "pairs": pairs,
    }
