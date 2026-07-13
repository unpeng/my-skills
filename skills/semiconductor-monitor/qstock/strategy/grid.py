# -*- coding: utf-8 -*-
"""
网格做T模块（改进4，借鉴 Rockyzsu 的网格交易思想）。

把原本"单档 ±2% 挂单"的做T升级为"分档网格"：
- 现价下方按档位挂买单（跌一档买一档，越跌买越多，摊低成本）
- 现价上方按档位挂卖单（涨一档卖一档，分批止盈）

档间距优先用 ATR 自适应（step = step_atr_mult × ATR），ATR 不可用时
回退固定百分比（step = 现价 × step_pct_fallback%）。每档买入资金为
"做T可用资金上限 ÷ 档数"，避免一次打满。

纯计算函数，不联网、无副作用，便于单测。
"""

import math


def _round4(x):
    if x is None:
        return None
    try:
        f = float(x)
        if math.isnan(f):
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


def compute_grid(current_price: float,
                 atr: float = None,
                 cash: float = 0.0,
                 levels: int = 3,
                 step_atr_mult: float = 1.0,
                 cash_cap_pct: float = 80.0,
                 step_pct_fallback: float = 2.0,
                 stop_loss: float = None) -> dict:
    """
    计算做T网格挂单价位与份数。

    Args:
        current_price: 当前价（网格中枢）
        atr: 当前 ATR，用于自适应档间距；None 或<=0 时回退固定百分比
        cash: 可用资金
        levels: 网格档数（买/卖各 levels 档）
        step_atr_mult: 档间距 = step_atr_mult × ATR
        cash_cap_pct: 做T最多使用可用资金比例（默认80%，与C8一致，不满仓做T）
        step_pct_fallback: ATR不可用时的档间距百分比（默认2%）
        stop_loss: 止损位；低于该价的买入档会被标记，提示不宜在止损位下方补仓

    Returns:
        dict：含 step / step_source / 买入网格 / 卖出网格 / 每档资金上限 等。
        参数非法（现价<=0 或 levels<=0）时返回 {"error": ...}。
    """
    if current_price is None or current_price <= 0:
        return {"error": "当前价必须为正数"}
    if levels is None or levels <= 0:
        return {"error": "网格档数必须为正整数"}

    if atr is not None and atr > 0:
        step = step_atr_mult * atr
        step_source = f"ATR自适应({step_atr_mult}×ATR)"
    else:
        step = current_price * step_pct_fallback / 100.0
        step_source = f"固定{step_pct_fallback}%(ATR不可用回退)"

    cap = max(0.0, cash) * max(0.0, min(cash_cap_pct, 100.0)) / 100.0
    per_level_cash = cap / levels if levels > 0 else 0.0

    buy_grid = []
    for i in range(1, levels + 1):
        price = current_price - i * step
        if price <= 0:
            continue
        shares = int(per_level_cash // price) if price > 0 else 0
        below_stop = (stop_loss is not None and price < stop_loss)
        buy_grid.append({
            "档位": i,
            "买入价": _round4(price),
            "份数": shares,
            "占用资金": _round4(price * shares),
            "低于止损": below_stop,
        })

    sell_grid = []
    for i in range(1, levels + 1):
        price = current_price + i * step
        # 卖出档与同档买入档对称：卖出份数默认等于同档买入份数（平掉该档T仓）
        mirror_shares = buy_grid[i - 1]["份数"] if i - 1 < len(buy_grid) else 0
        sell_grid.append({
            "档位": i,
            "卖出价": _round4(price),
            "份数": mirror_shares,
        })

    return {
        "中枢价": _round4(current_price),
        "档间距": _round4(step),
        "档间距来源": step_source,
        "档数": levels,
        "做T可用资金上限": _round4(cap),
        "每档资金上限": _round4(per_level_cash),
        "买入网格": buy_grid,
        "卖出网格": sell_grid,
        "_说明": (
            "跌一档买一档、涨一档卖一档；越跌买越多摊低成本，越涨分批止盈。"
            "标记'低于止损'的买入档不建议执行（已跌破止损位）。"
        ),
    }
