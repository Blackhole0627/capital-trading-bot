"""Backtest the strategy on historical candles and write a report.

    python run_backtest.py --yahoo ^GSPC --symbol US500 --timeframe DAY --spread 0.6 --fee 0.02
    python run_backtest.py --csv data/US500_DAY.csv --symbol US500 --timeframe DAY --spread 0.6 --fee 0.02
    python run_backtest.py --csv data/US500_DAY.csv --symbol US500 --timeframe DAY --spread 0.6 --fee 0.02 --scan
    python run_backtest.py --csv data/US500_DAY.csv --from 2024-01-01            (sub-period)

Outputs in reports/: <symbol>_<tf>_report.txt, _report.html, _trades.csv, _equity.csv
Yahoo tickers: ^GSPC (US500), ^NDX (US100), ^DJI (US30), ^GDAXI (DE40), ^FTSE (UK100),
               EURUSD=X, GBPUSD=X, GC=F (gold) ...
"""
import argparse
import copy
import itertools
import os
import sys

from capital_bot.backtest import BacktestSettings, format_report, html_report, run_backtest
from capital_bot.config import load_config
from capital_bot.data import download_yahoo, load_csv, save_csv
from capital_bot.risk import MarketRules


def main():
    ap = argparse.ArgumentParser(description="Strategy backtester")
    ap.add_argument("--config", default="config.json", help="config.json (strategy + risk params)")
    ap.add_argument("--csv", help="candle CSV (time,open,high,low,close[,volume])")
    ap.add_argument("--yahoo", help="Yahoo ticker to download, e.g. ^GSPC, ^NDX, EURUSD=X")
    ap.add_argument("--symbol", default="US500", help="label / Capital.com epic")
    ap.add_argument("--timeframe", default=None, help="DAY, HOUR, MINUTE_15 ... (default from config)")
    ap.add_argument("--from", dest="from_date", default=None, help="start date YYYY-MM-DD")
    ap.add_argument("--to", dest="to_date", default=None, help="end date YYYY-MM-DD")
    ap.add_argument("--balance", type=float, default=10_000.0)
    ap.add_argument("--spread", type=float, default=None,
                    help="full spread in price units (default 0.015%% of price)")
    ap.add_argument("--fee", type=float, default=0.0, help="overnight financing, %% of notional per day (e.g. 0.02)")
    ap.add_argument("--vpp", type=float, default=1.0, help="P&L per unit of size per 1.0 price move")
    ap.add_argument("--min-size", type=float, default=0.01)
    ap.add_argument("--step", type=float, default=0.01)
    ap.add_argument("--out", default="reports")
    ap.add_argument("--tag", default="", help="suffix for output file names")
    ap.add_argument("--scan", action="store_true", help="run a grid of strategy parameters")
    args = ap.parse_args()

    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    cfg = load_config(args.config)
    tf = args.timeframe or cfg.timeframe

    if args.csv:
        df = load_csv(args.csv)
    elif args.yahoo:
        print(f"Downloading {args.yahoo} {tf} from Yahoo Finance ...")
        df = download_yahoo(args.yahoo, tf, start_year=2000)
        path = os.path.join("data", f"{args.symbol}_{tf}.csv")
        save_csv(df, path)
        print(f"Saved {len(df)} candles to {path}")
    else:
        ap.error("give --csv or --yahoo")
    if args.from_date:
        df = df[df.index >= args.from_date]
    if args.to_date:
        df = df[df.index <= args.to_date]
    if df.empty:
        sys.exit("No candles in the selected period")
    print(f"{len(df)} candles  {df.index[0]} -> {df.index[-1]}")

    spread = args.spread if args.spread is not None else float(df["close"].iloc[-1]) * 0.00015
    settings = BacktestSettings(initial_balance=args.balance, spread=spread, value_per_point=args.vpp,
                                overnight_fee_pct=args.fee,
                                rules=MarketRules(min_size=args.min_size, size_step=args.step, lot_size=1.0))
    tag = f"_{args.tag}" if args.tag else ""

    if args.scan:
        rows = []
        if cfg.strategy.type == "mean_reversion":
            header = ("rsi_entry", "sma_trend", "sma_exit", "atr_sl", "max_hold")
            grid = itertools.product([5, 10, 15, 20], [100, 150, 200], [3, 5, 10], [2.0, 3.0, 4.0], [5, 10])
        else:
            header = ("ema_fast", "ema_slow", "ema_trend", "atr_sl", "atr_tp")
            grid = itertools.product([10, 20, 30], [50, 100], [150, 200], [1.5, 2.0, 2.5], [2.0, 3.0, 4.0])
        for combo in grid:
            p = copy.deepcopy(cfg.strategy)
            if cfg.strategy.type == "mean_reversion":
                p.rsi_entry, p.sma_trend, p.sma_exit, p.atr_sl, p.max_hold_bars = combo
            else:
                p.ema_fast, p.ema_slow, p.ema_trend, p.atr_sl, p.atr_tp = combo
                if p.ema_fast >= p.ema_slow:
                    continue
            s = run_backtest(df, p, cfg.risk, settings, args.symbol, tf).stats()
            rows.append((*combo, s["trades"], s["net_profit_pct"], s["cagr_pct"], s["max_drawdown_pct"],
                         s["win_rate_pct"], s["profit_factor"]))
        rows.sort(key=lambda r: r[len(header) + 1], reverse=True)
        print("\n" + " ".join(f"{h:>9}" for h in header) + f" {'trades':>6} {'net%':>8} {'cagr%':>6} {'maxDD%':>7} {'win%':>6} {'PF':>5}")
        for r in rows:
            print(" ".join(f"{x:>9}" for x in r[:len(header)]) +
                  f" {r[-6]:>6} {r[-5]:>8.2f} {r[-4]:>6.2f} {r[-3]:>7.2f} {r[-2]:>6.1f} {r[-1]:>5.2f}")
        pos = sum(1 for r in rows if r[-5] > 0)
        print(f"\n{pos}/{len(rows)} parameter sets profitable")
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, f"{args.symbol}_{tf}{tag}_scan.csv"), "w") as f:
            f.write(",".join(header) + ",trades,net_pct,cagr_pct,max_dd_pct,win_pct,profit_factor\n")
            for r in rows:
                f.write(",".join(str(x) for x in r) + "\n")
        return

    res = run_backtest(df, cfg.strategy, cfg.risk, settings, args.symbol, tf)
    report = format_report(res)
    print(report)

    os.makedirs(args.out, exist_ok=True)
    base = os.path.join(args.out, f"{args.symbol}_{tf}{tag}")
    with open(base + "_report.txt", "w", encoding="utf-8") as f:
        f.write(report)
    with open(base + "_report.html", "w", encoding="utf-8") as f:
        f.write(html_report(res))
    res.trades_df().to_csv(base + "_trades.csv", index=False)
    res.equity.rename("equity").to_csv(base + "_equity.csv")
    print(f"\nReports written to {base}_report.txt / .html / _trades.csv / _equity.csv")


if __name__ == "__main__":
    main()
