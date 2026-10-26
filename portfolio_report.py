"""Combine several single-symbol backtests into one portfolio equity curve.

    python portfolio_report.py reports/US500_DAY_equity.csv reports/NAS100_DAY_equity.csv reports/US30_DAY_equity.csv

Each symbol is backtested on its own balance (run_backtest.py); the portfolio
equals running them in one account with the same starting capital split
equally, which is how the live bot behaves with max_open_positions >= symbols.
"""
import os
import sys

import numpy as np
import pandas as pd


def main(paths):
    if not paths:
        sys.exit(__doc__)
    curves = []
    for p in paths:
        s = pd.read_csv(p, index_col=0, parse_dates=True)["equity"]
        s.index = pd.to_datetime(s.index, utc=True)
        curves.append(s.rename(os.path.basename(p).split("_")[0]))
    df = pd.concat(curves, axis=1).sort_index().ffill().dropna()
    df = df / df.iloc[0]                       # normalise each to 1.0
    port = df.mean(axis=1) * 10_000            # equal weight, 10k total
    peak = port.cummax()
    dd = (port - peak) / peak * 100
    years = (port.index[-1] - port.index[0]).days / 365.25
    cagr = ((port.iloc[-1] / port.iloc[0]) ** (1 / years) - 1) * 100
    yearly = port.resample("YE").last().pct_change() * 100
    yearly.iloc[0] = (port.resample("YE").last().iloc[0] / port.iloc[0] - 1) * 100

    print(f"Portfolio of {', '.join(df.columns)}  ({port.index[0].date()} -> {port.index[-1].date()}, {years:.1f} years)")
    print(f"  Start 10,000.00 -> End {port.iloc[-1]:,.2f}   net {port.iloc[-1] / 100 - 100:+.2f}%   CAGR {cagr:.2f}%")
    print(f"  Max drawdown {-dd.min():.2f}%   worst year {yearly.min():+.2f}%   best year {yearly.max():+.2f}%   "
          f"positive years {(yearly > 0).sum()}/{len(yearly)}")
    print("  Yearly returns:")
    for d, v in yearly.items():
        print(f"    {d.year}  {v:+6.2f}%")
    os.makedirs("reports", exist_ok=True)
    out = "reports/portfolio_equity.csv"
    port.rename("equity").to_csv(out)
    print(f"  Written {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
