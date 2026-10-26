"""Candle loading: CSV files, Yahoo Finance download, Capital.com API."""
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pandas as pd

from .config import RESOLUTION_SECONDS

COLUMNS = ["open", "high", "low", "close", "volume"]

# Yahoo intervals for Capital.com resolutions
YAHOO_INTERVAL = {"MINUTE_5": "5m", "MINUTE_15": "15m", "MINUTE_30": "30m",
                  "HOUR": "1h", "DAY": "1d", "WEEK": "1wk"}


def candles_to_df(candles: List[dict]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(candles).set_index("time")
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "time"
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = 0.0
    return df[COLUMNS].astype(float).sort_index()


def load_csv(path: str) -> pd.DataFrame:
    """CSV with columns time,open,high,low,close[,volume]; time in UTC."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = next((c for c in ("time", "datetime", "date", "timestamp") if c in df.columns), None)
    if tcol is None:
        raise ValueError("CSV needs a time/datetime/date column")
    df[tcol] = pd.to_datetime(df[tcol], utc=True)
    df = df.set_index(tcol).sort_index()
    df.index.name = "time"
    if "volume" not in df.columns:
        df["volume"] = 0.0
    df = df[COLUMNS].astype(float)
    return df[~df.index.duplicated(keep="last")]


def save_csv(df: pd.DataFrame, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    out = df.copy()
    out.index = out.index.strftime("%Y-%m-%d %H:%M:%S")
    out.to_csv(path, index_label="time")


def download_yahoo(ticker: str, resolution: str = "HOUR", period: Optional[str] = None,
                   start_year: Optional[int] = None) -> pd.DataFrame:
    """Free historical data for backtests.

    Yahoo keeps ~730 days of hourly bars, 60 days of 5/15/30-minute bars and
    decades of daily bars (period defaults to "max" for DAY/WEEK).
    """
    import yfinance as yf

    interval = YAHOO_INTERVAL.get(resolution)
    if interval is None:
        raise ValueError(f"Yahoo has no interval for {resolution}")
    if period is None:
        period = "max" if resolution in ("DAY", "WEEK") else ("730d" if resolution == "HOUR" else "60d")
    raw = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=False)
    if raw.empty:
        raise ValueError(f"No data from Yahoo for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "time"
    df = df[COLUMNS].astype(float).dropna(subset=["open", "high", "low", "close"])
    # Yahoo FX bars occasionally have a zero/garbage row
    df = df[(df["high"] >= df["low"]) & (df["low"] > 0)]
    if start_year:
        df = df[df.index.year >= start_year]
    return df[~df.index.duplicated(keep="last")].sort_index()


def download_capital(client, epic: str, resolution: str = "HOUR", days: int = 365,
                     to_dt: Optional[datetime] = None) -> pd.DataFrame:
    """Pull history through the Capital.com API (depth depends on the broker)."""
    to_dt = to_dt or datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=days)
    bars = client.get_history(epic, resolution, from_dt, to_dt, RESOLUTION_SECONDS[resolution])
    return candles_to_df(bars)
