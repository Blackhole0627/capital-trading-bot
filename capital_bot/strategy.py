"""Trading strategies. Each one turns a candle DataFrame into entry signals,
exit flags and an ATR used for the protective stop. Everything is evaluated
on CLOSED candles only; the bot/backtester executes at the next candle open.

mean_reversion (default; stock indices, daily candles)
    LONG  when close > SMA(sma_trend) and RSI(rsi_period) < rsi_entry
    EXIT  when close > SMA(sma_exit), or after max_hold_bars, or at the ATR stop
    (shorts are the mirror image and are off by default: the documented edge is long-only)

ema_trend (trend following, any timeframe)
    LONG  when EMA(fast) crosses above EMA(slow), close > EMA(trend), RSI not overbought
    SHORT mirror image
    EXIT  at ATR stop / ATR take-profit, or on an opposite signal
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .config import StrategyParams
from .indicators import atr, ema, rsi


@dataclass
class Signal:
    direction: int          # 1 long, -1 short
    close: float            # close of the signal candle
    atr: float
    stop_distance: float
    profit_distance: float  # 0 = no take profit
    time: pd.Timestamp

    @property
    def side(self) -> str:
        return "BUY" if self.direction > 0 else "SELL"


@dataclass
class Decision:
    entry: Optional[Signal]
    exit_long: bool
    exit_short: bool
    close: float
    atr: float
    time: pd.Timestamp


class Strategy:
    name = "base"

    def __init__(self, params: Optional[StrategyParams] = None):
        self.p = params or StrategyParams()

    @property
    def min_bars(self) -> int:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError

    def _rules(self, out: pd.DataFrame) -> pd.DataFrame:
        """Subclasses fill long_ok / short_ok / exit_long / exit_short / atr columns."""
        raise NotImplementedError

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        out = df.copy()
        out["atr"] = atr(out, p.atr_period)
        out = self._rules(out)

        start, end = p.trade_hours_utc
        hours = out.index.hour
        in_hours = (hours >= start) & (hours < end) if start < end else (hours >= start) | (hours < end)

        sig = np.zeros(len(out), dtype=int)
        if p.allow_long:
            sig[(out["long_ok"] & in_hours).to_numpy()] = 1
        if p.allow_short:
            sig[(out["short_ok"] & in_hours).to_numpy()] = -1
        sig[~out["valid"].to_numpy()] = 0
        out["signal"] = sig
        out["exit_long"] = out["exit_long"].fillna(False).astype(bool)
        out["exit_short"] = out["exit_short"].fillna(False).astype(bool)
        return out

    def latest(self, df: pd.DataFrame) -> Optional[Decision]:
        """Decision for the last (closed) candle of df, or None if not enough data."""
        if len(df) < self.min_bars:
            return None
        data = self.compute(df)
        last = data.iloc[-1]
        entry = None
        if last["signal"] != 0 and not np.isnan(last["atr"]):
            entry = Signal(
                direction=int(last["signal"]), close=float(last["close"]), atr=float(last["atr"]),
                stop_distance=float(last["atr"] * self.p.atr_sl),
                profit_distance=float(last["atr"] * self.p.atr_tp) if self.p.atr_tp > 0 else 0.0,
                time=data.index[-1],
            )
        return Decision(entry, bool(last["exit_long"]), bool(last["exit_short"]),
                        float(last["close"]), float(last["atr"]) if not np.isnan(last["atr"]) else 0.0,
                        data.index[-1])


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    @property
    def min_bars(self) -> int:
        return max(self.p.sma_trend, self.p.sma_exit, self.p.atr_period, self.p.rsi_period) + 5

    def describe(self) -> str:
        p = self.p
        return (f"Reversión a la media: RSI{p.rsi_period} < {p.rsi_entry} por encima de SMA{p.sma_trend}, "
                f"salida cierre > SMA{p.sma_exit} / {p.max_hold_bars} velas, stop {p.atr_sl} x ATR{p.atr_period}"
                + (f", objetivo {p.atr_tp} x ATR" if p.atr_tp > 0 else ""))

    def _rules(self, out: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        close = out["close"]
        out["rsi"] = rsi(close, p.rsi_period)
        out["sma_trend"] = close.rolling(p.sma_trend).mean()
        out["sma_exit"] = close.rolling(p.sma_exit).mean()
        out["long_ok"] = (close > out["sma_trend"]) & (out["rsi"] < p.rsi_entry)
        out["short_ok"] = (close < out["sma_trend"]) & (out["rsi"] > 100.0 - p.rsi_entry)
        out["exit_long"] = close > out["sma_exit"]
        out["exit_short"] = close < out["sma_exit"]
        out["valid"] = out[["sma_trend", "sma_exit", "rsi", "atr"]].notna().all(axis=1)
        return out


class EmaTrendStrategy(Strategy):
    name = "ema_trend"

    @property
    def min_bars(self) -> int:
        return max(self.p.ema_trend, self.p.ema_slow, self.p.atr_period, self.p.rsi_filter_period) + 5

    def describe(self) -> str:
        p = self.p
        return (f"Tendencia EMA: cruce EMA {p.ema_fast}/{p.ema_slow}, tendencia EMA {p.ema_trend}, filtro RSI{p.rsi_filter_period}, "
                f"stop {p.atr_sl} x ATR{p.atr_period}, objetivo {p.atr_tp} x ATR"
                + (", salida con señal contraria" if p.exit_on_opposite else ""))

    def _rules(self, out: pd.DataFrame) -> pd.DataFrame:
        p = self.p
        close = out["close"]
        out["ema_fast"] = ema(close, p.ema_fast)
        out["ema_slow"] = ema(close, p.ema_slow)
        out["ema_trend"] = ema(close, p.ema_trend)
        out["rsi"] = rsi(close, p.rsi_filter_period)
        fast, slow = out["ema_fast"], out["ema_slow"]
        cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        cross_dn = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        out["long_ok"] = cross_up & (close > out["ema_trend"]) & (out["rsi"] < p.rsi_max_long)
        out["short_ok"] = cross_dn & (close < out["ema_trend"]) & (out["rsi"] > p.rsi_min_short)
        if p.exit_on_opposite:
            out["exit_long"] = cross_dn
            out["exit_short"] = cross_up
        else:
            out["exit_long"] = False
            out["exit_short"] = False
        out["valid"] = out[["ema_trend", "atr", "rsi"]].notna().all(axis=1)
        return out


STRATEGIES = {"mean_reversion": MeanReversionStrategy, "ema_trend": EmaTrendStrategy}


def build_strategy(params: StrategyParams) -> Strategy:
    try:
        return STRATEGIES[params.type](params)
    except KeyError:
        raise ValueError(f"Unknown strategy type '{params.type}'. Use one of {list(STRATEGIES)}")
