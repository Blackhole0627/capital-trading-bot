"""Unit tests: indicators, strategies, risk sizing, backtester, config.

    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capital_bot.backtest import BacktestSettings, run_backtest  # noqa: E402
from capital_bot.config import RiskParams, StrategyParams, load_config  # noqa: E402
from capital_bot.data import candles_to_df  # noqa: E402
from capital_bot.indicators import atr, ema, rsi  # noqa: E402
from capital_bot.risk import AccountGuard, MarketRules, clamp_stop_distance, position_size  # noqa: E402
from capital_bot.strategy import EmaTrendStrategy, MeanReversionStrategy, build_strategy  # noqa: E402

EMA = dict(type="ema_trend", atr_sl=2.0, atr_tp=3.0, max_hold_bars=0, allow_short=True)
MR = dict(type="mean_reversion")


def make_df(n=600, seed=1, trend=0.0, freq="h", base=1.10, vol=0.0008):
    rng = np.random.default_rng(seed)
    close = base + np.cumsum(rng.normal(trend, vol, n))
    high = close + rng.uniform(vol / 4, vol * 1.5, n)
    low = close - rng.uniform(vol / 4, vol * 1.5, n)
    opn = np.concatenate([[close[0]], close[:-1]])
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": opn, "high": high, "low": low, "close": close, "volume": 0.0}, index=idx)


class IndicatorTests(unittest.TestCase):
    def test_ema_tracks_constant(self):
        s = pd.Series([2.0] * 50)
        self.assertAlmostEqual(ema(s, 10).iloc[-1], 2.0)

    def test_atr_positive(self):
        df = make_df(100)
        a = atr(df, 14)
        self.assertTrue((a.dropna() > 0).all())
        self.assertTrue(np.isnan(a.iloc[0]))

    def test_rsi_bounds(self):
        df = make_df(300)
        r = rsi(df["close"], 14).dropna()
        self.assertTrue(((r >= 0) & (r <= 100)).all())
        up = pd.Series(np.arange(1, 60, dtype=float))
        self.assertGreater(rsi(up, 14).iloc[-1], 99)
        down = pd.Series(np.arange(60, 1, -1, dtype=float))
        self.assertLess(rsi(down, 2).iloc[-1], 1)


class EmaStrategyTests(unittest.TestCase):
    def test_signals_only_after_warmup_and_valid_values(self):
        df = make_df(600)
        out = EmaTrendStrategy(StrategyParams(**EMA)).compute(df)
        self.assertEqual(set(out["signal"].unique()) - {0, 1, -1}, set())
        self.assertTrue((out["signal"].iloc[:200] == 0).all())
        self.assertGreater((out["signal"] != 0).sum(), 0)

    def test_long_signal_requires_trend(self):
        out = EmaTrendStrategy(StrategyParams(**EMA)).compute(make_df(600))
        longs = out[out["signal"] == 1]
        self.assertTrue((longs["close"] > longs["ema_trend"]).all())
        shorts = out[out["signal"] == -1]
        self.assertTrue((shorts["close"] < shorts["ema_trend"]).all())

    def test_latest_none_when_short(self):
        self.assertIsNone(EmaTrendStrategy(StrategyParams(**EMA)).latest(make_df(50)))

    def test_hours_filter(self):
        p = StrategyParams(**EMA, trade_hours_utc=[8, 16])
        out = EmaTrendStrategy(p).compute(make_df(600))
        hours = out.index[out["signal"] != 0].hour
        self.assertTrue(((hours >= 8) & (hours < 16)).all())

    def test_allow_flags(self):
        p = StrategyParams(**{**EMA, "allow_short": False})
        out = EmaTrendStrategy(p).compute(make_df(600))
        self.assertEqual((out["signal"] == -1).sum(), 0)

    def test_latest_signal_has_tp(self):
        df = make_df(600)
        strat = EmaTrendStrategy(StrategyParams(**EMA))
        out = strat.compute(df)
        i = int(np.flatnonzero(out["signal"].to_numpy())[0])
        dec = strat.latest(df.iloc[: i + 1])
        self.assertIsNotNone(dec.entry)
        self.assertGreater(dec.entry.profit_distance, dec.entry.stop_distance)


class MeanReversionTests(unittest.TestCase):
    def test_long_only_pullback_rules(self):
        df = make_df(1500, seed=7, trend=0.0003, freq="D", base=100.0, vol=1.0)
        out = MeanReversionStrategy(StrategyParams(**MR)).compute(df)
        longs = out[out["signal"] == 1]
        self.assertGreater(len(longs), 0)
        self.assertTrue((longs["close"] > longs["sma_trend"]).all())
        self.assertTrue((longs["rsi"] < 10).all())
        self.assertEqual((out["signal"] == -1).sum(), 0)          # shorts off by default
        self.assertTrue((out.loc[out["exit_long"], "close"] > out.loc[out["exit_long"], "sma_exit"]).all())

    def test_latest_no_take_profit(self):
        df = make_df(1500, seed=7, trend=0.0003, freq="D", base=100.0, vol=1.0)
        strat = MeanReversionStrategy(StrategyParams(**MR))
        out = strat.compute(df)
        i = int(np.flatnonzero(out["signal"].to_numpy())[0])
        dec = strat.latest(df.iloc[: i + 1])
        self.assertEqual(dec.entry.profit_distance, 0.0)
        self.assertAlmostEqual(dec.entry.stop_distance, dec.entry.atr * 3.0)

    def test_build_strategy(self):
        self.assertIsInstance(build_strategy(StrategyParams(type="mean_reversion")), MeanReversionStrategy)
        self.assertIsInstance(build_strategy(StrategyParams(type="ema_trend")), EmaTrendStrategy)
        with self.assertRaises(ValueError):
            build_strategy(StrategyParams(type="nope"))


class RiskTests(unittest.TestCase):
    def test_risk_sizing(self):
        rules = MarketRules(min_size=1, size_step=1, lot_size=1)
        size = position_size(10_000, 0.0020, RiskParams(size_mode="risk", risk_per_trade_pct=1.0), rules)
        self.assertEqual(size, 50_000)              # 100 risk / 0.002 = 50k units
        capped = position_size(10_000, 0.0020, RiskParams(size_mode="risk", risk_per_trade_pct=1.0, max_size=10_000), rules)
        self.assertEqual(capped, 10_000)

    def test_notional_sizing(self):
        rules = MarketRules(min_size=0.1, size_step=0.1, lot_size=1)
        size = position_size(10_000, 50.0, RiskParams(size_mode="notional", exposure_pct=100), rules, price=5000.0)
        self.assertAlmostEqual(size, 2.0)
        size = position_size(10_000, 50.0, RiskParams(size_mode="notional", exposure_pct=50), rules, price=5000.0)
        self.assertAlmostEqual(size, 1.0)
        self.assertEqual(position_size(10_000, 50.0, RiskParams(size_mode="notional"), rules, price=None), 0.0)

    def test_min_size_returns_zero(self):
        rules = MarketRules(min_size=1000, size_step=1000, lot_size=1)
        self.assertEqual(position_size(100, 0.01, RiskParams(size_mode="risk", risk_per_trade_pct=1.0), rules), 0.0)

    def test_fixed_mode_and_step(self):
        rules = MarketRules(min_size=0.1, size_step=0.1, lot_size=1)
        self.assertEqual(position_size(1, 1, RiskParams(size_mode="fixed", fixed_size=0.37), rules), 0.3)

    def test_clamp_stop(self):
        rules = MarketRules(min_stop_pct=0.1)
        self.assertAlmostEqual(clamp_stop_distance(1.0, 0.0001, rules), 0.001)
        self.assertAlmostEqual(clamp_stop_distance(1.0, 0.01, rules), 0.01)

    def test_account_guard(self):
        g = AccountGuard(RiskParams(daily_loss_limit_pct=3, max_drawdown_pct=15), 1000, 1000)
        self.assertIsNone(g.check(990))
        self.assertIn("daily", g.check(965))
        g.new_day(965)
        self.assertIsNone(g.check(965))
        self.assertIn("drawdown", g.check(840))


class BacktestTests(unittest.TestCase):
    def test_ema_backtest_reconciles(self):
        df = make_df(1500, seed=3)
        settings = BacktestSettings(initial_balance=10_000, spread=0.0001, value_per_point=1.0,
                                    rules=MarketRules(min_size=1, size_step=1, lot_size=1))
        res = run_backtest(df, StrategyParams(**EMA), RiskParams(size_mode="risk", max_drawdown_pct=100),
                           settings, "TEST", "HOUR")
        s = res.stats()
        self.assertGreater(s["trades"], 0)
        self.assertAlmostEqual(sum(t.pnl for t in res.trades), s["net_profit"], places=6)
        self.assertEqual(s["wins"] + s["losses"], s["trades"])
        self.assertGreaterEqual(s["max_drawdown_pct"], 0)
        for t in res.trades:
            self.assertLessEqual(t.entry_time, t.exit_time)
            if t.reason == "SL":
                self.assertLess(t.pnl, 0)
            if t.reason == "TP":
                self.assertGreater(t.pnl, 0)

    def test_losses_bounded_by_risk(self):
        df = make_df(1500, seed=5)
        settings = BacktestSettings(initial_balance=10_000, spread=0.0, value_per_point=1.0,
                                    rules=MarketRules(min_size=0.01, size_step=0.01, lot_size=1))
        res = run_backtest(df, StrategyParams(**{**EMA, "exit_on_opposite": False}),
                           RiskParams(size_mode="risk", risk_per_trade_pct=1.0, max_drawdown_pct=100),
                           settings, "TEST", "HOUR")
        for t in res.trades:
            if t.reason == "SL":
                self.assertLess(abs(t.pnl), 10_000 * 0.0105 * 2)

    def test_mean_reversion_backtest_exits_and_fees(self):
        df = make_df(2000, seed=9, trend=0.0003, freq="D", base=100.0, vol=1.0)
        settings = BacktestSettings(initial_balance=10_000, spread=0.1, value_per_point=1.0, overnight_fee_pct=0.02,
                                    rules=MarketRules(min_size=0.01, size_step=0.01, lot_size=1))
        res = run_backtest(df, StrategyParams(**MR), RiskParams(size_mode="notional", exposure_pct=100,
                                                                 max_drawdown_pct=100, daily_loss_limit_pct=100),
                           settings, "TEST", "DAY")
        s = res.stats()
        self.assertGreater(s["trades"], 5)
        self.assertEqual(s["exits_tp"], 0)
        self.assertGreater(s["exits_signal"] + s["exits_time"] + s["exits_sl"], 0)
        self.assertGreater(s["total_fees"], 0)
        self.assertAlmostEqual(sum(t.pnl for t in res.trades), s["net_profit"], places=6)
        for t in res.trades:
            self.assertLessEqual(t.bars, 10)
            self.assertIsNone(t.target)
            self.assertLessEqual(t.entry * t.size, 10_000 * 1.6)   # notional ~ balance

    def test_no_trades_on_flat_market(self):
        n = 500
        idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0}, index=idx)
        for p in (StrategyParams(**EMA), StrategyParams(**MR)):
            res = run_backtest(df, p, RiskParams(), BacktestSettings(), "FLAT", "HOUR")
            self.assertEqual(len(res.trades), 0)
            self.assertEqual(res.stats()["net_profit"], 0)


class DataAndConfigTests(unittest.TestCase):
    def test_candles_to_df(self):
        bars = [{"time": datetime(2024, 1, 1, h, tzinfo=timezone.utc), "open": 1, "high": 2, "low": 0.5,
                 "close": 1.5, "bid": 1.49, "ask": 1.51, "volume": 10} for h in (2, 0, 1)]
        df = candles_to_df(bars)
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertEqual(len(df), 3)

    def test_env_overrides_config(self):
        os.environ["CAPITAL_API_KEY"] = "k1"
        os.environ["CAPITAL_DEMO"] = "false"
        try:
            cfg = load_config("does_not_exist.json")
            self.assertEqual(cfg.api.api_key, "k1")
            self.assertFalse(cfg.api.demo)
            self.assertEqual(cfg.timeframe_seconds, 86400)
        finally:
            del os.environ["CAPITAL_API_KEY"]
            del os.environ["CAPITAL_DEMO"]

    def test_example_config_loads(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.example.json")
        cfg = load_config(path)
        self.assertTrue(cfg.api.demo)
        self.assertEqual(cfg.strategy.type, "mean_reversion")
        self.assertEqual(cfg.risk.size_mode, "notional")
        self.assertEqual(cfg.symbols, ["US500", "US100", "US30"])


if __name__ == "__main__":
    unittest.main()
