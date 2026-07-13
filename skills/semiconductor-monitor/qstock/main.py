# -*- coding: utf-8 -*-
"""
Stock Prediction Program - CLI Entry Point

基于 tkfy920/qstock 的数据接口，实现A股股票预测分析程序。
融合技术指标、基本面评分和机器学习模型进行多因子选股预测。

Usage:
    python main.py predict <code>         预测单只股票走势
    python main.py scan <market>          扫描市场并排名
    python main.py backtest <market>      运行回测
    python main.py kline <code>           显示K线图
    python main.py info <code>            查看股票基本信息
"""

import sys
import os
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    ATR_STOP_MULTIPLIER, ATR_T_MULTIPLIER, ATR_TRAIL_MULTIPLIER,
)


def cmd_predict(args):
    """Predict stock direction using ML model."""
    from data.fetcher import get_kline, get_financial
    from model.predictor import StockPredictor
    from model.fundamental import compute_fundamental_score
    from view.chart import plot_prediction

    code = args.code
    days = args.days

    print(f"正在获取 {code} 的历史数据...")
    df = get_kline(code, start="20220101")
    if df.empty:
        print(f"错误: 无法获取 {code} 的数据")
        return

    name = df["name"].iloc[0] if "name" in df.columns else code
    print(f"股票: {name} ({code})")
    print(f"数据量: {len(df)} 条记录")

    # Train and predict
    print("\n正在训练模型并预测...")
    predictor = StockPredictor(predict_days=days)
    result = predictor.train_and_predict(df)

    if "error" in result:
        print(f"错误: {result['error']}")
        return

    # Display training results
    train = result["train"]
    print(f"\n{'='*50}")
    print(f"模型训练结果:")
    print(f"  训练集准确率: {train['train_accuracy']*100:.2f}%")
    print(f"  测试集准确率: {train['test_accuracy']*100:.2f}%")
    print(f"  训练样本数:   {train['train_samples']}")
    print(f"  测试样本数:   {train['test_samples']}")

    print(f"\n特征重要性 (Top 10):")
    for feat, imp in sorted(train["feature_importance"].items(),
                            key=lambda x: x[1], reverse=True)[:10]:
        bar = "█" * int(imp * 100)
        print(f"  {feat:20s} {imp:.4f} {bar}")

    # Display prediction
    pred = result["prediction"]
    print(f"\n{'='*50}")
    print(f"预测结果 ({days}个交易日):")
    print(f"  最新收盘价:   {pred['close']}")
    print(f"  预测方向:     {pred['direction']}")
    print(f"  上涨概率:     {pred['up_probability']}%")
    print(f"  下跌概率:     {pred['down_probability']}%")
    print(f"  预测置信度:   {pred['confidence']}%")
    print(f"  RSI:          {pred['rsi']}")
    print(f"  MACD柱:       {pred['macd_hist']}")

    # Fundamental score
    print(f"\n基本面评分:")
    financial = get_financial(code)
    if financial:
        fund_scores = compute_fundamental_score(financial)
        print(f"  市盈率(动):   {financial.get('市盈率(动)', 'N/A')}")
        print(f"  市净率:       {financial.get('市净率', 'N/A')}")
        print(f"  ROE:          {financial.get('ROE', 'N/A')}%")
        print(f"  毛利率:       {financial.get('毛利率', 'N/A')}%")
        print(f"  基本面总分:   {fund_scores['total_score']}/50")

    # Plot
    chart_path = plot_prediction(df, pred, code)
    if chart_path:
        print(f"\n预测图表已保存: {chart_path}")


def cmd_scan(args):
    """Scan market and rank stocks by multi-factor score."""
    from data.fetcher import get_kline, get_market_realtime
    from strategy.scoring import multi_factor_score

    market = args.market
    top_n = args.top

    print(f"正在扫描 {market} 市场...")

    stocks = get_market_realtime(market)
    if stocks.empty:
        print("错误: 无法获取市场数据")
        return

    print(f"共 {len(stocks)} 只股票，正在评分...")

    # Score each stock (limit to first 30 for speed)
    results = []
    sample = stocks.head(30)

    for _, row in sample.iterrows():
        code = row["代码"]
        name = row["名称"]
        try:
            df = get_kline(code, start="20230101")
            if df.empty or len(df) < 60:
                continue
            score = multi_factor_score(df)
            if "error" not in score:
                score["code"] = code
                score["name"] = name
                results.append(score)
        except Exception:
            continue

    if not results:
        print("错误: 无法获取有效股票数据")
        return

    # Sort by composite score
    results.sort(key=lambda x: x["composite_score"], reverse=True)

    print(f"\n{'='*70}")
    print(f"多因子排名 (Top {min(top_n, len(results))}):")
    print(f"{'='*70}")
    print(f"{'排名':>4s} {'代码':>8s} {'名称':>8s} {'综合分':>8s} "
          f"{'技术分':>8s} {'动量分':>8s} {'基本面':>8s} {'收盘价':>8s}")
    print(f"{'-'*70}")

    for i, r in enumerate(results[:top_n], 1):
        print(f"{i:4d} {r['code']:>8s} {r['name']:>8s} "
              f"{r['composite_score']:8.2f} {r['technical_score']:8.2f} "
              f"{r['momentum_score']:8.2f} {r['fundamental_score']:8.2f} "
              f"{r['close']:8.2f}")


def cmd_backtest(args):
    """Run backtest on selected stocks."""
    from strategy.backtest import run_backtest
    from view.chart import plot_backtest

    market = args.market
    top_n = args.top
    days = args.days

    print(f"正在运行回测 (市场: {market}, 持仓: {top_n}只, 周期: {days}天)...")

    result = run_backtest(
        market=market,
        top_n=top_n,
        rebalance_days=days,
        start_date=args.start,
    )

    if "error" in result:
        print(f"错误: {result['error']}")
        return

    # Display results
    print(f"\n{'='*50}")
    print("回测结果:")
    print(f"{'='*50}")

    for code_info in result.get("selected_stocks", []):
        print(f"  {code_info['code']} {code_info['name']} "
              f"(评分: {code_info['score']:.2f})")

    print(f"\n绩效指标:")
    for k, v in result.get("metrics", {}).items():
        print(f"  {k}: {v}")

    # Plot
    chart_path = plot_backtest(result)
    if chart_path:
        print(f"\n回测图表已保存: {chart_path}")


def cmd_kline(args):
    """Display K-line chart with technical indicators."""
    from data.fetcher import get_kline
    from model.technical import compute_all_indicators
    from view.chart import plot_kline

    code = args.code
    last_n = args.last

    print(f"正在获取 {code} 的K线数据...")
    df = get_kline(code, start="20230101")
    if df.empty:
        print(f"错误: 无法获取 {code} 的数据")
        return

    name = df["name"].iloc[0] if "name" in df.columns else code
    df = compute_all_indicators(df)

    chart_path = plot_kline(df, code=code, name=name, last_n=last_n)
    print(f"K线图已保存: {chart_path}")


def cmd_info(args):
    """Display stock/ETF basic information.

    A1: ETF 没有 PE/PB/ROE 等财务指标，改用 ETF 专属信息（价格、流动性等），
    不再套用个股基本面评分体系。
    """
    from data.fetcher import is_etf_code, get_etf_info
    from model.fundamental import score_stock

    code = args.code

    print(f"正在获取 {code} 的基本信息...")

    if is_etf_code(code):
        try:
            result = get_etf_info(code)
        except Exception as e:
            print(f"错误: 获取ETF信息失败: {e}")
            return
        if "error" in result:
            print(f"错误: {result['error']}")
            return

        print(f"\n{'='*40}")
        print(f"ETF: {code}（基金类标的，不适用个股基本面评分）")
        print(f"{'='*40}")
        print(f"  当前价:       {result.get('price', 'N/A')}")
        print(f"  涨跌幅:       {result.get('change_pct', 'N/A')}%")
        print(f"  换手率:       {result.get('turnover_rate', 'N/A')}%")
        print(f"  20日均成交额: {result.get('avg_turnover_20d', 'N/A')}")
        print(f"  20日均成交量: {result.get('avg_volume_20d', 'N/A')}")
        print(f"  数据来源:     {result.get('source', 'N/A')}")
        if result.get("premium_pct") is None:
            print(f"  溢价率:       暂无数据源，请勿凭空估算")
        return

    # Financial info (个股才适用 PE/PB/ROE 评分)
    result = score_stock(code)
    if "error" in result:
        print(f"错误: {result['error']}")
        return

    print(f"\n{'='*40}")
    print(f"股票: {result.get('name', code)} ({code})")
    print(f"行业: {result.get('industry', 'N/A')}")
    print(f"{'='*40}")
    print(f"  市盈率(动):   {result.get('pe', 'N/A')}")
    print(f"  市净率:       {result.get('pb', 'N/A')}")
    print(f"  ROE:          {result.get('roe', 'N/A')}%")
    print(f"  毛利率:       {result.get('gross_margin', 'N/A')}%")
    print(f"  净利率:       {result.get('net_margin', 'N/A')}%")
    print(f"\n  基本面评分:")
    print(f"    PE得分:     {result.get('pe_score', 0)}/10")
    print(f"    PB得分:     {result.get('pb_score', 0)}/10")
    print(f"    ROE得分:    {result.get('roe_score', 0)}/10")
    print(f"    毛利得分:   {result.get('margin_score', 0)}/10")
    print(f"    净利得分:   {result.get('net_margin_score', 0)}/10")
    print(f"    总分:       {result.get('total_score', 0)}/50")


def cmd_monitor(args):
    """计算588170盯盘所需的全部动态变量（昨收价/止损位/做T价位/技术指标等）。"""
    from strategy.monitor import compute_monitor_variables, MonitorInputError
    from strategy.position_store import save_position

    # D11: 若用户提供了持仓参数，顺手保存到本地配置，下次可用 --use-saved
    # 复用，避免每次都要重新输入持仓数量/成本/资金
    if not args.no_save:
        save_position(
            code=args.code, position=args.position, cost=args.cost,
            cash=args.cash, max_loss_pct=args.max_loss_pct,
            max_loss_amount=args.max_loss_amount,
            stop_loss_price=args.stop_loss_price,
        )

    try:
        result = compute_monitor_variables(
            code=args.code,
            position=args.position,
            cost=args.cost,
            cash=args.cash,
            max_loss_pct=args.max_loss_pct,
            max_loss_amount=args.max_loss_amount,
            stop_loss_price=args.stop_loss_price,
            start=args.start,
            atr_stop_mult=args.atr_stop_mult,
            atr_t_mult=args.atr_t_mult,
            atr_trail_mult=args.atr_trail_mult,
        )
    except MonitorInputError as e:
        print(f"输入参数错误: {e}")
        return

    if "error" in result:
        print(f"错误: {result['error']}")
        return

    print(f"\n{'='*40}")
    print(f"{args.code} 盯盘变量")
    print(f"{'='*40}")
    for key, value in result.items():
        if key.startswith("_"):
            continue
        print(f"  [{key}] = {value}")

    if result.get("_数据质量_检测到拆分跳空"):
        print(f"\n⚠️  {result['_数据质量_说明']}")

    print(f"\n{result.get('_风险提示', '')}")


def cmd_position(args):
    """管理本地保存的持仓信息（D11：避免每次会话重复询问）。"""
    from strategy.position_store import load_position, clear_position

    if args.action == "show":
        pos = load_position(args.code)
        if pos is None:
            print(f"未找到 {args.code} 的已保存持仓信息")
            return
        print(f"\n{args.code} 已保存的持仓信息:")
        for k, v in pos.items():
            print(f"  {k}: {v}")
    elif args.action == "clear":
        clear_position(args.code)
        print(f"已清除 {args.code} 的已保存持仓信息")


def cmd_log(args):
    """记录或查看决策/交易记录（D12：便于复盘技能建议的历史表现）。"""
    from strategy.position_store import append_decision_log, read_decision_log

    if args.action == "add":
        if not args.decision_action or args.price is None:
            print("错误: add 操作需要提供 --decision-action 和 --price")
            return
        append_decision_log(
            code=args.code, action=args.decision_action, price=args.price,
            shares=args.shares, note=args.note,
        )
        print(f"已记录: {args.code} {args.decision_action} @ {args.price}")
    elif args.action == "show":
        entries = read_decision_log(code=args.code, limit=args.limit)
        if not entries:
            print(f"{args.code} 暂无决策/交易记录")
            return
        print(f"\n{args.code} 最近 {len(entries)} 条决策/交易记录:")
        for e in entries:
            shares_str = f" x{e['shares']}份" if e.get("shares") else ""
            note_str = f" ({e['note']})" if e.get("note") else ""
            print(f"  [{e['time']}] {e['action']} @ {e['price']}{shares_str}{note_str}")


def _fetch_price_atr(code, start):
    """获取标的当前价与最新ATR（供 grid 命令复用），失败时用K线收盘价兜底。"""
    import math as _math
    from data.kline_cache import get_kline_cached
    from data.fetcher import get_current_quote
    from data.processor import detect_and_truncate_split
    from model.technical import compute_all_indicators

    df = get_kline_cached(code, start=start)
    if df.empty:
        return None, None
    df, _, _ = detect_and_truncate_split(df)
    df = compute_all_indicators(df)
    latest = df.iloc[-1]

    atr_raw = latest.get("atr")
    atr = None
    try:
        if atr_raw is not None and not _math.isnan(float(atr_raw)):
            atr = float(atr_raw)
    except (TypeError, ValueError):
        atr = None

    quote = get_current_quote(code)
    if "error" not in quote:
        price = float(quote["price"])
    else:
        price = float(latest["close"])
    return price, atr


def cmd_grid(args):
    """改进4：计算做T分档网格挂单价位与份数。"""
    from strategy.grid import compute_grid

    price, atr = args.price, args.atr
    if price is None or atr is None:
        f_price, f_atr = _fetch_price_atr(args.code, args.start)
        if f_price is None:
            print(f"错误: 无法获取 {args.code} 的数据")
            return
        price = price if price is not None else f_price
        atr = atr if atr is not None else f_atr

    result = compute_grid(
        current_price=price, atr=atr, cash=args.cash, levels=args.levels,
        step_atr_mult=args.step_atr_mult, cash_cap_pct=args.cash_cap_pct,
        stop_loss=args.stop_loss_price,
    )
    if "error" in result:
        print(f"错误: {result['error']}")
        return

    print(f"\n{'='*50}")
    print(f"{args.code} 做T网格 (中枢价 {result['中枢价']})")
    print(f"{'='*50}")
    print(f"  档间距: {result['档间距']} ({result['档间距来源']})")
    print(f"  做T可用资金上限: {result['做T可用资金上限']}，每档: {result['每档资金上限']}")
    print(f"\n  买入网格（跌一档买一档）:")
    for g in result["买入网格"]:
        flag = "  ⚠️低于止损" if g["低于止损"] else ""
        print(f"    第{g['档位']}档  买入价 {g['买入价']}  {g['份数']}份  "
              f"占用{g['占用资金']}{flag}")
    print(f"\n  卖出网格（涨一档卖一档）:")
    for g in result["卖出网格"]:
        print(f"    第{g['档位']}档  卖出价 {g['卖出价']}  {g['份数']}份")
    print(f"\n  {result['_说明']}")
    print(f"\n以上数据仅供参考，不构成投资建议，市场有风险，操作需自行判断")


def cmd_btgrid(args):
    """改进5：单标的 ATR 网格做T + ATR止损 规则回测（验证参数有效性）。"""
    from data.kline_cache import get_kline_cached
    from data.processor import detect_and_truncate_split
    from strategy.rule_backtest import backtest_atr_grid

    df = get_kline_cached(args.code, start=args.start)
    if df.empty:
        print(f"错误: 无法获取 {args.code} 的数据")
        return
    df, _, _ = detect_and_truncate_split(df)

    result = backtest_atr_grid(
        df, init_cash=args.cash, k_atr=args.k_atr, n_stop=args.n_stop,
        lot_cash_pct=args.lot_pct, max_lots=args.max_lots,
    )
    if "error" in result:
        print(f"错误: {result['error']}")
        return

    p = result["params"]
    print(f"\n{'='*50}")
    print(f"{args.code} 规则回测 (ATR网格做T+ATR止损)")
    print(f"{'='*50}")
    print(f"  参数: k(网格)={p['k_atr']} n(止损)={p['n_stop']} "
          f"每手资金%={p['lot_cash_pct']} 最大手数={p['max_lots']} "
          f"初始资金={p['init_cash']}")
    print(f"\n  绩效指标:")
    for k, v in result["metrics"].items():
        print(f"    {k}: {v}")

    trades = result["trades"]
    if trades:
        print(f"\n  最近 {min(10, len(trades))} 笔成交:")
        for t in trades[-10:]:
            print(f"    [{t['date']}] {t['action']} @ {t['price']} x{t['shares']}份")
    print(f"\n以上为历史模拟，不代表未来收益，不构成投资建议")


def cmd_review(args):
    """改进5：复盘决策日志，统计做T/止损的已实现盈亏与胜率。"""
    from strategy.review import review_decisions

    result = review_decisions(code=args.code)
    if "error" in result:
        print(f"{result['error']}")
        return

    print(f"\n{'='*50}")
    print(f"{args.code or '全部标的'} 决策复盘")
    print(f"{'='*50}")
    for k, v in result.items():
        if k == "pairs":
            continue
        print(f"  {k}: {v}")

    pairs = result.get("pairs", [])
    if pairs:
        print(f"\n  配对明细（最近 {min(10, len(pairs))} 笔）:")
        for pr in pairs[-10:]:
            print(f"    买{pr['买入价']} → 卖{pr['卖出价']} x{pr['份数']}份 "
                  f"盈亏{pr['盈亏']} ({pr['动作']})")


def main():
    parser = argparse.ArgumentParser(
        description="A股股票预测分析程序",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py predict 000001           预测平安银行走势
  python main.py predict 600519 --days 10 预测茅台未来10天走势
  python main.py scan 沪深A               扫描沪深A股并排名
  python main.py scan 创业板 --top 20     扫描创业板Top20
  python main.py backtest 沪深A           运行回测
  python main.py kline 000001             显示K线图
  python main.py info 600519              查看茅台基本信息
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # predict
    p_predict = subparsers.add_parser("predict", help="预测股票走势")
    p_predict.add_argument("code", help="股票代码 (如 000001, 600519)")
    p_predict.add_argument("--days", type=int, default=5,
                          help="预测天数 (默认5)")

    # scan
    p_scan = subparsers.add_parser("scan", help="扫描市场并排名")
    p_scan.add_argument("market", default="沪深A", nargs="?",
                       help="市场名称 (沪深A/创业板/科创板 等)")
    p_scan.add_argument("--top", type=int, default=10, help="显示前N名")

    # backtest
    p_backtest = subparsers.add_parser("backtest", help="运行回测")
    p_backtest.add_argument("market", default="沪深A", nargs="?",
                           help="市场名称")
    p_backtest.add_argument("--top", type=int, default=10, help="持仓数量")
    p_backtest.add_argument("--days", type=int, default=20, help="调仓周期")
    p_backtest.add_argument("--start", default="20230101", help="回测起始日期")

    # kline
    p_kline = subparsers.add_parser("kline", help="显示K线图")
    p_kline.add_argument("code", help="股票代码")
    p_kline.add_argument("--last", type=int, default=120, help="显示最近N根K线")

    # info
    p_info = subparsers.add_parser("info", help="查看股票基本信息")
    p_info.add_argument("code", help="股票代码")

    # monitor
    p_monitor = subparsers.add_parser("monitor", help="计算盯盘所需的全部动态变量")
    p_monitor.add_argument("code", help="股票代码 (如 588170)")
    p_monitor.add_argument("--position", type=float, required=True, help="持仓数量")
    p_monitor.add_argument("--cost", type=float, required=True, help="加权平均成本价")
    p_monitor.add_argument("--cash", type=float, default=0.0, help="可用资金")
    p_monitor.add_argument("--max-loss-pct", type=float, default=None,
                          dest="max_loss_pct", help="最大可承受亏损比例（如10表示10%%）")
    p_monitor.add_argument("--max-loss-amount", type=float, default=None,
                          dest="max_loss_amount", help="最大可承受亏损金额")
    p_monitor.add_argument("--stop-loss-price", type=float, default=None,
                          dest="stop_loss_price", help="用户直接指定的止损价格")
    p_monitor.add_argument("--start", default="20200101", help="历史数据起始日期")
    p_monitor.add_argument("--atr-stop-mult", type=float, default=ATR_STOP_MULTIPLIER,
                          dest="atr_stop_mult",
                          help=f"ATR止损倍数n，止损位=成本-n×ATR（默认{ATR_STOP_MULTIPLIER}）")
    p_monitor.add_argument("--atr-t-mult", type=float, default=ATR_T_MULTIPLIER,
                          dest="atr_t_mult",
                          help=f"做T价差ATR倍数k，做T买/卖位=现价∓k×ATR（默认{ATR_T_MULTIPLIER}）")
    p_monitor.add_argument("--atr-trail-mult", type=float, default=ATR_TRAIL_MULTIPLIER,
                          dest="atr_trail_mult",
                          help=f"移动止盈ATR倍数m，移动止盈位=区间最高-m×ATR（默认{ATR_TRAIL_MULTIPLIER}）")
    p_monitor.add_argument("--no-save", action="store_true", dest="no_save",
                          help="本次不保存持仓信息到本地（默认会自动保存）")

    # position (D11: 本地持仓持久化管理)
    p_position = subparsers.add_parser("position", help="查看/清除本地已保存的持仓信息")
    p_position.add_argument("action", choices=["show", "clear"], help="操作类型")
    p_position.add_argument("code", help="股票代码 (如 588170)")

    # grid (改进4: 做T分档网格)
    p_grid = subparsers.add_parser("grid", help="计算做T分档网格挂单价位与份数")
    p_grid.add_argument("code", help="股票/ETF代码 (如 588170)")
    p_grid.add_argument("--cash", type=float, default=0.0, help="可用资金")
    p_grid.add_argument("--levels", type=int, default=3, help="网格档数（默认3）")
    p_grid.add_argument("--step-atr-mult", type=float, default=1.0,
                       dest="step_atr_mult", help="档间距ATR倍数（默认1.0）")
    p_grid.add_argument("--cash-cap-pct", type=float, default=80.0,
                       dest="cash_cap_pct", help="做T资金上限比例（默认80%%）")
    p_grid.add_argument("--stop-loss-price", type=float, default=None,
                       dest="stop_loss_price", help="止损位，低于此价的买入档会被标记")
    p_grid.add_argument("--price", type=float, default=None,
                       help="手动指定中枢价（默认取实时价）")
    p_grid.add_argument("--atr", type=float, default=None,
                       help="手动指定ATR（默认自动计算）")
    p_grid.add_argument("--start", default="20200101", help="历史数据起始日期")

    # btgrid (改进5: 单标的规则回测)
    p_bt = subparsers.add_parser("btgrid", help="单标的ATR网格做T+ATR止损规则回测")
    p_bt.add_argument("code", help="股票/ETF代码 (如 588170)")
    p_bt.add_argument("--cash", type=float, default=100000.0, help="初始资金（默认10万）")
    p_bt.add_argument("--k-atr", type=float, default=1.0, dest="k_atr",
                     help="网格band = k×ATR（默认1.0）")
    p_bt.add_argument("--n-stop", type=float, default=2.5, dest="n_stop",
                     help="止损 = 均价 - n×ATR（默认2.5）")
    p_bt.add_argument("--lot-pct", type=float, default=20.0, dest="lot_pct",
                     help="每手使用初始资金比例（默认20%%）")
    p_bt.add_argument("--max-lots", type=int, default=4, dest="max_lots",
                     help="最多持有手数（默认4）")
    p_bt.add_argument("--start", default="20200101", help="历史数据起始日期")

    # review (改进5: 决策日志复盘)
    p_review = subparsers.add_parser("review", help="复盘决策日志的做T/止损盈亏与胜率")
    p_review.add_argument("code", nargs="?", default=None,
                         help="标的代码（省略则统计全部）")

    # log (D12: 决策/交易记录，便于复盘)
    p_log = subparsers.add_parser("log", help="记录或查看决策/交易记录")
    p_log.add_argument("action", choices=["add", "show"], help="操作类型")
    p_log.add_argument("code", help="股票代码 (如 588170)")
    p_log.add_argument("--decision-action", dest="decision_action", default=None,
                      help="操作类型，如 止损清仓/做T买入/做T卖出/减仓 (add时必填)")
    p_log.add_argument("--price", type=float, default=None, help="成交/决策价格 (add时必填)")
    p_log.add_argument("--shares", type=float, default=None, help="涉及份数")
    p_log.add_argument("--note", default="", help="备注")
    p_log.add_argument("--limit", type=int, default=50, help="show时显示最近N条")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    commands = {
        "predict": cmd_predict,
        "scan": cmd_scan,
        "backtest": cmd_backtest,
        "kline": cmd_kline,
        "info": cmd_info,
        "monitor": cmd_monitor,
        "position": cmd_position,
        "log": cmd_log,
        "grid": cmd_grid,
        "btgrid": cmd_btgrid,
        "review": cmd_review,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
