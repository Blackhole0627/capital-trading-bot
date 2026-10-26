"""Bar-by-bar backtester, strategy-agnostic.

Execution model (conservative, mirrors the live bot):
  * Signals are computed on candle close; entries and signal exits happen at
    the NEXT candle's open.
  * Spread is paid half on entry and half on exit.
  * Stop loss / take profit are checked against each candle's high/low. If
    both are touched in the same candle the stop loss is assumed first.
  * Overnight financing is charged per calendar day a position is held.
  * Position size follows the same risk rules as the live bot.
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from .config import RiskParams, StrategyParams
from .risk import MarketRules, position_size
from .strategy import build_strategy


@dataclass
class BacktestSettings:
    initial_balance: float = 10_000.0
    spread: float = 0.0             # full spread in price units
    value_per_point: float = 1.0    # account-currency P&L per unit of size per 1.0 move
    commission_per_trade: float = 0.0
    overnight_fee_pct: float = 0.0  # % of notional per calendar day held (e.g. 0.02)
    rules: MarketRules = field(default_factory=lambda: MarketRules(min_size=0.01, size_step=0.01, lot_size=1.0))


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: Optional[pd.Timestamp]
    direction: int
    size: float
    entry: float
    exit: Optional[float]
    stop: float
    target: Optional[float]
    pnl: float            # net of spread, commission and financing
    reason: str           # SL / TP / SIGNAL / TIME / END
    bars: int
    fee: float = 0.0

    @property
    def side(self) -> str:
        return "LONG" if self.direction > 0 else "SHORT"


@dataclass
class BacktestResult:
    trades: List[Trade]
    equity: pd.Series           # mark-to-market equity per candle
    balance: pd.Series          # closed balance per candle
    settings: BacktestSettings
    strategy: StrategyParams
    risk: RiskParams
    symbol: str = ""
    timeframe: str = ""

    # ----------------------------------------------------------- statistics
    def stats(self) -> dict:
        t = self.trades
        pnls = np.array([x.pnl for x in t]) if t else np.array([])
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        gross_profit = float(wins.sum()) if len(wins) else 0.0
        gross_loss = float(-losses.sum()) if len(losses) else 0.0
        eq = self.equity
        peak = eq.cummax()
        dd = eq - peak
        dd_pct = (dd / peak.replace(0, np.nan)).fillna(0.0) * 100.0
        bal_peak = self.balance.cummax()
        bal_dd_pct = ((self.balance - bal_peak) / bal_peak.replace(0, np.nan)).fillna(0.0) * 100.0

        streak = worst_streak = 0
        for p in pnls:
            streak = streak + 1 if p <= 0 else 0
            worst_streak = max(worst_streak, streak)

        days = max((eq.index[-1] - eq.index[0]).days, 1) if len(eq) else 1
        start = float(self.settings.initial_balance)
        end = float(eq.iloc[-1]) if len(eq) else start
        years = days / 365.25
        cagr = ((end / start) ** (1 / years) - 1) * 100 if start > 0 and end > 0 and years > 0 else 0.0

        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "period_start": str(eq.index[0]) if len(eq) else "",
            "period_end": str(eq.index[-1]) if len(eq) else "",
            "days": days,
            "years": years,
            "initial_balance": start,
            "final_balance": end,
            "net_profit": end - start,
            "net_profit_pct": (end - start) / start * 100 if start else 0.0,
            "cagr_pct": cagr,
            "trades": len(t),
            "trades_per_year": len(t) / years if years > 0 else 0.0,
            "wins": int(len(wins)),
            "losses": int(len(losses)),
            "win_rate_pct": len(wins) / len(t) * 100 if t else 0.0,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0),
            "avg_trade": float(pnls.mean()) if len(pnls) else 0.0,
            "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "largest_win": float(wins.max()) if len(wins) else 0.0,
            "largest_loss": float(losses.min()) if len(losses) else 0.0,
            "total_fees": float(sum(x.fee for x in t)),
            "max_drawdown": float(-dd.min()) if len(dd) else 0.0,
            "max_drawdown_pct": float(-dd_pct.min()) if len(dd_pct) else 0.0,
            "max_closed_drawdown_pct": float(-bal_dd_pct.min()) if len(bal_dd_pct) else 0.0,
            "max_consecutive_losses": worst_streak,
            "avg_bars_held": float(np.mean([x.bars for x in t])) if t else 0.0,
            "long_trades": sum(1 for x in t if x.direction > 0),
            "short_trades": sum(1 for x in t if x.direction < 0),
            "exits_tp": sum(1 for x in t if x.reason == "TP"),
            "exits_sl": sum(1 for x in t if x.reason == "SL"),
            "exits_signal": sum(1 for x in t if x.reason == "SIGNAL"),
            "exits_time": sum(1 for x in t if x.reason == "TIME"),
        }

    def monthly_pnl(self) -> pd.Series:
        if not self.trades:
            return pd.Series(dtype=float)
        s = pd.Series([x.pnl for x in self.trades],
                      index=pd.DatetimeIndex([x.exit_time for x in self.trades]))
        return s.groupby(s.index.to_period("M")).sum()

    def yearly_pnl(self) -> pd.Series:
        if not self.trades:
            return pd.Series(dtype=float)
        s = pd.Series([x.pnl for x in self.trades],
                      index=pd.DatetimeIndex([x.exit_time for x in self.trades]))
        return s.groupby(s.index.year).sum()

    def trades_df(self) -> pd.DataFrame:
        rows = [{
            "entry_time": x.entry_time, "exit_time": x.exit_time, "side": x.side, "size": round(x.size, 4),
            "entry": x.entry, "exit": x.exit, "stop": x.stop, "target": x.target,
            "pnl": round(x.pnl, 2), "fee": round(x.fee, 2), "reason": x.reason, "bars": x.bars,
        } for x in self.trades]
        return pd.DataFrame(rows)


def run_backtest(df: pd.DataFrame, strategy_params: StrategyParams, risk: RiskParams,
                 settings: BacktestSettings, symbol: str = "", timeframe: str = "") -> BacktestResult:
    strat = build_strategy(strategy_params)
    data = strat.compute(df)
    p = strategy_params

    o = data["open"].to_numpy()
    h = data["high"].to_numpy()
    l = data["low"].to_numpy()
    c = data["close"].to_numpy()
    a = data["atr"].to_numpy()
    sig = data["signal"].to_numpy()
    ex_long = data["exit_long"].to_numpy()
    ex_short = data["exit_short"].to_numpy()
    idx = data.index
    n = len(data)
    # calendar days between consecutive candles (index resolution may be ns/us/s)
    day_secs = (np.diff(idx.values.astype("datetime64[s]").astype(np.int64)) / 86400.0
                if n > 1 else np.array([]))

    half_spread = settings.spread / 2.0
    vpp = settings.value_per_point
    balance = float(settings.initial_balance)
    peak = balance
    equity = np.full(n, balance, dtype=float)
    closed = np.full(n, balance, dtype=float)
    trades: List[Trade] = []

    pos = None
    day_start_balance = balance
    current_day = None

    def check_levels(i, pos):
        d = pos["direction"]
        if d > 0:
            if l[i] <= pos["stop"]:
                return pos["stop"], "SL"
            if pos["target"] is not None and h[i] >= pos["target"]:
                return pos["target"], "TP"
        else:
            if h[i] >= pos["stop"]:
                return pos["stop"], "SL"
            if pos["target"] is not None and l[i] <= pos["target"]:
                return pos["target"], "TP"
        return None, None

    def close_trade(i, pos, price, reason):
        nonlocal balance
        d = pos["direction"]
        fill = price - half_spread * d
        pnl = (fill - pos["entry"]) * d * pos["size"] * vpp - settings.commission_per_trade - pos["fee"]
        balance += pnl
        trades.append(Trade(pos["entry_time"], idx[i], d, pos["size"], pos["entry"], fill, pos["stop"],
                            pos["target"], pnl, reason, i - pos["entry_i"], pos["fee"]))

    for i in range(1, n):
        day = idx[i].date()
        if day != current_day:
            current_day = day
            day_start_balance = balance

        # ---- manage open position
        if pos is not None:
            d = pos["direction"]
            # financing for the time between the previous candle and this one
            if settings.overnight_fee_pct:
                pos["fee"] += pos["entry"] * pos["size"] * vpp * settings.overnight_fee_pct / 100.0 * day_secs[i - 1]
            # signal / time exits decided on previous close -> executed at this open
            held = i - pos["entry_i"]
            if (d > 0 and ex_long[i - 1]) or (d < 0 and ex_short[i - 1]):
                close_trade(i, pos, o[i], "SIGNAL")
                pos = None
            elif p.max_hold_bars and held >= p.max_hold_bars:
                close_trade(i, pos, o[i], "TIME")
                pos = None
            else:
                price, reason = check_levels(i, pos)
                if price is not None:
                    close_trade(i, pos, price, reason)
                    pos = None
                elif p.trailing_stop and not np.isnan(a[i]):
                    if d > 0:
                        pos["stop"] = max(pos["stop"], c[i] - p.atr_sl * a[i])
                    else:
                        pos["stop"] = min(pos["stop"], c[i] + p.atr_sl * a[i])

        # ---- account guards (same as live bot)
        peak = max(peak, balance)
        day_loss_pct = (day_start_balance - balance) / day_start_balance * 100 if day_start_balance > 0 else 0
        dd_pct = (peak - balance) / peak * 100 if peak > 0 else 0
        blocked = day_loss_pct >= risk.daily_loss_limit_pct or dd_pct >= risk.max_drawdown_pct

        # ---- entry at this candle's open, from previous candle's signal
        if pos is None and not blocked and sig[i - 1] != 0 and not np.isnan(a[i - 1]):
            d = int(sig[i - 1])
            entry = o[i] + half_spread * d
            stop_dist = p.atr_sl * a[i - 1]
            tp_dist = p.atr_tp * a[i - 1] if p.atr_tp > 0 else 0.0
            size = position_size(balance, stop_dist, risk, settings.rules, vpp, entry)
            if size > 0:
                pos = {"direction": d, "size": size, "entry": entry, "stop": entry - stop_dist * d,
                       "target": entry + tp_dist * d if tp_dist > 0 else None,
                       "entry_i": i, "entry_time": idx[i], "fee": 0.0}
                price, reason = check_levels(i, pos)   # same-candle stop/target (SL first)
                if price is not None:
                    close_trade(i, pos, price, reason)
                    pos = None

        closed[i] = balance
        if pos is not None:
            unrealized = (c[i] - pos["entry"]) * pos["direction"] * pos["size"] * vpp - pos["fee"]
            equity[i] = balance + unrealized
        else:
            equity[i] = balance

    if pos is not None:   # close any open position at the last close so results are complete
        close_trade(n - 1, pos, c[-1], "END")
        equity[-1] = closed[-1] = balance

    return BacktestResult(trades, pd.Series(equity, index=idx), pd.Series(closed, index=idx),
                          settings, strategy_params, risk, symbol, timeframe)


# ------------------------------------------------------------------ reports
def format_report(res: BacktestResult) -> str:
    s = res.stats()
    pf = "inf" if s["profit_factor"] == math.inf else f"{s['profit_factor']:.2f}"
    strat = build_strategy(res.strategy)
    r = res.risk
    sizing = {"notional": f"{r.exposure_pct}% del saldo por posición",
              "risk": f"{r.risk_per_trade_pct}% de riesgo por operación",
              "fixed": f"tamaño fijo {r.fixed_size}"}[r.size_mode]
    lines = [
        "=" * 70,
        f" BACKTEST  {s['symbol']}  {s['timeframe']}",
        f" Periodo: {s['period_start'][:10]} -> {s['period_end'][:10]}  ({s['days']} días, {s['years']:.1f} años)",
        "=" * 70,
        f" Saldo inicial              : {s['initial_balance']:>12,.2f}",
        f" Saldo final                : {s['final_balance']:>12,.2f}",
        f" Beneficio neto             : {s['net_profit']:>12,.2f}  ({s['net_profit_pct']:+.2f}%)",
        f" CAGR (rentab. anual)       : {s['cagr_pct']:>11.2f}%",
        f" Drawdown máx. (capital)    : {s['max_drawdown']:>12,.2f}  ({s['max_drawdown_pct']:.2f}%)",
        f" Drawdown máx. (cerrado)    : {s['max_closed_drawdown_pct']:>11.2f}%",
        "-" * 70,
        f" Operaciones                : {s['trades']:>6}   ({s['trades_per_year']:.1f}/año)  largos {s['long_trades']}  cortos {s['short_trades']}",
        f" Ganadoras / Perdedoras     : {s['wins']:>6} / {s['losses']}",
        f" Tasa de acierto            : {s['win_rate_pct']:>9.2f}%",
        f" Profit factor              : {pf:>9}",
        f" Beneficio / pérdida bruta  : {s['gross_profit']:>12,.2f} / {s['gross_loss']:,.2f}",
        f" Operación media            : {s['avg_trade']:>12,.2f}",
        f" Ganancia / pérdida media   : {s['avg_win']:>12,.2f} / {s['avg_loss']:,.2f}",
        f" Mayor ganancia / pérdida   : {s['largest_win']:>12,.2f} / {s['largest_loss']:,.2f}",
        f" Máx. pérdidas consecutivas : {s['max_consecutive_losses']:>6}",
        f" Velas medias en mercado    : {s['avg_bars_held']:>9.1f}",
        f" Salidas SL/TP/señal/tiempo : {s['exits_sl']:>6} / {s['exits_tp']} / {s['exits_signal']} / {s['exits_time']}",
        f" Financiación pagada        : {s['total_fees']:>12,.2f}",
        "-" * 70,
        f" Estrategia: {strat.describe()}",
        f" Tamaño: {sizing}; límite de pérdida diaria {r.daily_loss_limit_pct}%, "
        f"stop por drawdown {r.max_drawdown_pct}%",
        f" Costes: spread {res.settings.spread}, financiación {res.settings.overnight_fee_pct}%/día, "
        f"comisión {res.settings.commission_per_trade}",
        "=" * 70,
    ]
    y = res.yearly_pnl()
    if len(y):
        lines.append(" G/P por año:")
        for year, v in y.items():
            lines.append(f"   {year}  {v:>10,.2f}")
    return "\n".join(lines)


def _svg_equity(eq: pd.Series, width=900, height=300) -> str:
    if len(eq) < 2:
        return ""
    y = eq.to_numpy()
    lo, hi = float(y.min()), float(y.max())
    span = hi - lo or 1.0
    pad = 30
    step = max(1, len(y) // 2000)
    pts = []
    for i in range(0, len(y), step):
        x = pad + (width - 2 * pad) * i / (len(y) - 1)
        yy = height - pad - (height - 2 * pad) * (y[i] - lo) / span
        pts.append(f"{x:.1f},{yy:.1f}")
    start_y = height - pad - (height - 2 * pad) * (y[0] - lo) / span
    return (f'<svg width="{width}" height="{height}" style="background:#fafafa;border:1px solid #ddd">'
            f'<line x1="{pad}" y1="{start_y:.1f}" x2="{width-pad}" y2="{start_y:.1f}" stroke="#bbb" stroke-dasharray="4"/>'
            f'<polyline fill="none" stroke="#1f77b4" stroke-width="1.5" points="{" ".join(pts)}"/>'
            f'<text x="{pad}" y="{pad-10}" font-size="12" fill="#444">máx {hi:,.0f}</text>'
            f'<text x="{pad}" y="{height-pad+20}" font-size="12" fill="#444">mín {lo:,.0f}</text>'
            f'<text x="{width-pad-150}" y="{pad-10}" font-size="12" fill="#444">{eq.index[0].date()} → {eq.index[-1].date()}</text>'
            f"</svg>")


def html_report(res: BacktestResult) -> str:
    s = res.stats()
    text = format_report(res)
    tdf = res.trades_df()
    if len(tdf):
        tdf = tdf.rename(columns={"entry_time": "entrada (fecha)", "exit_time": "salida (fecha)", "side": "lado",
                                  "size": "tamaño", "entry": "precio entrada", "exit": "precio salida",
                                  "stop": "stop", "target": "objetivo", "pnl": "G/P", "fee": "financiación",
                                  "reason": "motivo", "bars": "velas"})
        tdf["lado"] = tdf["lado"].map({"LONG": "LARGO", "SHORT": "CORTO"})
        tdf["motivo"] = tdf["motivo"].map({"SL": "stop loss", "TP": "objetivo", "SIGNAL": "señal de salida",
                                           "TIME": "tiempo máx.", "END": "fin del periodo"})
    table = tdf.to_html(index=False, border=0, float_format=lambda v: f"{v:.5g}") if len(tdf) else "<p>Sin operaciones</p>"
    y = res.yearly_pnl()
    yearly = "".join(
        f"<tr><td>{k}</td><td style='text-align:right;color:{'#1a7f37' if v >= 0 else '#c62828'}'>{v:,.2f}</td></tr>"
        for k, v in y.items())
    m = res.monthly_pnl()
    monthly = "".join(
        f"<tr><td>{k}</td><td style='text-align:right;color:{'#1a7f37' if v >= 0 else '#c62828'}'>{v:,.2f}</td></tr>"
        for k, v in m.items())
    pf = "inf" if s["profit_factor"] == math.inf else f"{s['profit_factor']:.2f}"
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Backtest {s['symbol']} {s['timeframe']}</title>
<style>body{{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222}}pre{{background:#f4f4f4;padding:12px}}
table{{border-collapse:collapse;font-size:13px}}td,th{{padding:4px 8px;border-bottom:1px solid #eee}}th{{text-align:left;background:#f0f0f0}}
.cols{{display:flex;gap:40px;flex-wrap:wrap}}</style></head>
<body><h1>Backtest {s['symbol']} {s['timeframe']}</h1>
<p>Beneficio neto <b>{s['net_profit']:,.2f} ({s['net_profit_pct']:+.2f}%)</b> · CAGR <b>{s['cagr_pct']:.2f}%</b> · Drawdown máximo <b>{s['max_drawdown_pct']:.2f}%</b> ·
Operaciones <b>{s['trades']}</b> · Tasa de acierto <b>{s['win_rate_pct']:.1f}%</b> · Profit factor <b>{pf}</b></p>
<h2>Curva de capital</h2>{_svg_equity(res.equity)}
<h2>Resumen</h2><pre>{text}</pre>
<div class="cols"><div><h2>G/P por año</h2><table><tr><th>Año</th><th>G/P</th></tr>{yearly}</table></div>
<div><h2>G/P por mes</h2><table><tr><th>Mes</th><th>G/P</th></tr>{monthly}</table></div></div>
<h2>Operaciones ({len(tdf)})</h2>{table}
<p style="color:#777;font-size:12px">Resultados simulados sobre datos históricos. Los resultados pasados, en backtest o en demo, no garantizan resultados futuros.</p>
</body></html>"""
