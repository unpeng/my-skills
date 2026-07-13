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
            atr_stop_n=args.atr_stop_n,
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
            atr_stop_n=args.atr_stop_n,
            start=args.start,
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
    p_monitor.add_argument("--atr-stop-n", type=float, default=None,
                          dest="atr_stop_n",
                          help="ATR止损倍数N（止损位=成本−N×ATR，借鉴abu波动率止损）")
    p_monitor.add_argument("--start", default="20200101", help="历史数据起始日期")
    p_monitor.add_argument("--no-save", action="store_true", dest="no_save",
                          help="本次不保存持仓信息到本地（默认会自动保存）")

    # position (D11: 本地持仓持久化管理)
    p_position = subparsers.add_parser("position", help="查看/清除本地已保存的持仓信息")
    p_position.add_argument("action", choices=["show", "clear"], help="操作类型")
    p_position.add_argument("code", help="股票代码 (如 588170)")

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
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
