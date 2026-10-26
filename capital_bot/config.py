import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


# Capital.com candle resolutions -> seconds
RESOLUTION_SECONDS = {
    "MINUTE": 60,
    "MINUTE_5": 300,
    "MINUTE_15": 900,
    "MINUTE_30": 1800,
    "HOUR": 3600,
    "HOUR_4": 14400,
    "DAY": 86400,
    "WEEK": 604800,
}


@dataclass
class ApiConfig:
    demo: bool = True
    api_key: str = ""
    identifier: str = ""   # Capital.com login e-mail
    password: str = ""     # API key custom password (set when the key is created)


@dataclass
class StrategyParams:
    type: str = "mean_reversion"   # "mean_reversion" (indices, daily) or "ema_trend"
    # ---- shared
    atr_period: int = 14
    atr_sl: float = 3.0            # stop loss = atr_sl * ATR (protective stop)
    atr_tp: float = 0.0            # take profit = atr_tp * ATR; 0 = no take profit
    max_hold_bars: int = 10        # force exit after N candles; 0 = unlimited
    trailing_stop: bool = False    # trail SL at atr_sl * ATR behind close
    trade_hours_utc: List[int] = field(default_factory=lambda: [0, 24])  # [start, end)
    allow_long: bool = True
    allow_short: bool = False
    # ---- mean_reversion: buy pullbacks in an uptrend
    rsi_period: int = 2
    rsi_entry: float = 10.0        # long when RSI(rsi_period) < rsi_entry (short when > 100 - rsi_entry)
    sma_trend: int = 200           # only long above this SMA (short below)
    sma_exit: int = 5              # exit long when close > SMA(sma_exit) (short when below)
    # ---- ema_trend: EMA crossover with trend filter
    ema_fast: int = 20
    ema_slow: int = 50
    ema_trend: int = 200
    rsi_filter_period: int = 14
    rsi_max_long: float = 70.0     # don't buy when already overbought
    rsi_min_short: float = 30.0    # don't sell when already oversold
    exit_on_opposite: bool = True  # close when an opposite signal appears


@dataclass
class RiskParams:
    size_mode: str = "notional"         # "notional" | "risk" | "fixed"
    exposure_pct: float = 100.0         # notional: position value = balance * exposure_pct / 100
    risk_per_trade_pct: float = 1.0     # risk: % of balance lost if the stop is hit
    fixed_size: float = 1.0             # fixed: constant order size
    daily_loss_limit_pct: float = 5.0   # stop opening trades for the day after this loss
    max_drawdown_pct: float = 25.0      # kill switch from peak balance
    max_open_positions: int = 3         # across all symbols
    max_size: float = 100000.0          # hard cap on any order size
    value_per_point: Dict[str, float] = field(default_factory=dict)  # per-symbol override


@dataclass
class BotConfig:
    api: ApiConfig = field(default_factory=ApiConfig)
    symbols: List[str] = field(default_factory=lambda: ["US500", "US100", "US30"])
    timeframe: str = "DAY"
    poll_seconds: int = 60
    candles_to_fetch: int = 300
    strategy: StrategyParams = field(default_factory=StrategyParams)
    risk: RiskParams = field(default_factory=RiskParams)
    log_path: str = "logs/bot.log"
    trades_csv: str = "logs/trades.csv"
    state_path: str = "logs/state.json"

    @property
    def timeframe_seconds(self) -> int:
        if self.timeframe not in RESOLUTION_SECONDS:
            raise ValueError(f"Unknown timeframe {self.timeframe}. Use one of {list(RESOLUTION_SECONDS)}")
        return RESOLUTION_SECONDS[self.timeframe]


def _merge(dc, data: Optional[dict]):
    if not data:
        return dc
    for k, v in data.items():
        if hasattr(dc, k):
            setattr(dc, k, v)
    return dc


def load_config(path: str = "config.json") -> BotConfig:
    """Load config.json; environment variables override API credentials."""
    cfg = BotConfig()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        _merge(cfg.api, raw.get("api"))
        _merge(cfg.strategy, raw.get("strategy"))
        _merge(cfg.risk, raw.get("risk"))
        for k in ("symbols", "timeframe", "poll_seconds", "candles_to_fetch",
                  "log_path", "trades_csv", "state_path"):
            if k in raw:
                setattr(cfg, k, raw[k])

    cfg.api.api_key = os.getenv("CAPITAL_API_KEY", cfg.api.api_key)
    cfg.api.identifier = os.getenv("CAPITAL_IDENTIFIER", cfg.api.identifier)
    cfg.api.password = os.getenv("CAPITAL_PASSWORD", cfg.api.password)
    if os.getenv("CAPITAL_DEMO") is not None:
        cfg.api.demo = os.getenv("CAPITAL_DEMO", "true").lower() in ("1", "true", "yes")
    return cfg


def config_to_dict(cfg: BotConfig) -> dict:
    return asdict(cfg)
