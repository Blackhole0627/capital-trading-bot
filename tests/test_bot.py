"""Integration test of the live loop against a fake Capital.com client.

    python -m unittest tests.test_bot -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from capital_bot.bot import TradingBot  # noqa: E402
from capital_bot.config import BotConfig, RiskParams, StrategyParams  # noqa: E402
from capital_bot.strategy import MeanReversionStrategy  # noqa: E402


def synthetic_daily(n=400, seed=11):
    rng = np.random.default_rng(seed)
    close = 5000 + np.cumsum(rng.normal(1.0, 40.0, n))
    high = close + rng.uniform(5, 40, n)
    low = close - rng.uniform(5, 40, n)
    opn = np.concatenate([[close[0]], close[:-1]])
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    idx = pd.date_range(end=end, periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"open": opn, "high": high, "low": low, "close": close, "volume": 0.0}, index=idx)


class FakeClient:
    """Minimal stand-in for CapitalClient."""

    def __init__(self, df):
        self.df = df
        self.currency = "USD"
        self.current_account_id = "ACC1"
        self.balance = 10_000.0
        self.positions = []
        self.orders = []
        self.closed = []
        self.transactions = []
        self._deal_no = 0

    def login(self):
        return {}

    def ping(self):
        return True

    def get_balance(self):
        return {"balance": self.balance, "deposit": self.balance, "profitLoss": 0.0, "available": self.balance}

    def get_market(self, epic):
        last = float(self.df["close"].iloc[-1])
        return {
            "instrument": {"epic": epic, "name": epic, "lotSize": 1},
            "dealingRules": {"minDealSize": {"unit": "POINTS", "value": 0.1},
                             "minSizeIncrement": {"unit": "POINTS", "value": 0.1},
                             "minStopOrProfitDistance": {"unit": "PERCENTAGE", "value": 0.1},
                             "maxStopOrProfitDistance": {"unit": "PERCENTAGE", "value": 60}},
            "snapshot": {"marketStatus": "TRADEABLE", "bid": last - 0.3, "offer": last + 0.3,
                         "decimalPlacesFactor": 1},
        }

    def get_prices(self, epic, resolution, max_bars=300, from_dt=None, to_dt=None):
        # the last row is the candle still forming "today"
        rows = self.df.iloc[-max_bars:]
        return [{"time": t.to_pydatetime(), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                 "bid": r.close, "ask": r.close, "volume": 0} for t, r in rows.iterrows()]

    def get_positions(self):
        return self.positions

    def open_position(self, epic, direction, size, stop_level=None, profit_level=None, **kw):
        self._deal_no += 1
        ref = f"ref{self._deal_no}"
        self.orders.append({"epic": epic, "direction": direction, "size": size, "stop": stop_level,
                            "tp": profit_level, "ref": ref})
        self.positions.append({"position": {"dealId": f"deal{self._deal_no}", "direction": direction, "size": size,
                                            "level": self.get_market(epic)["snapshot"]["offer"],
                                            "stopLevel": stop_level, "profitLevel": profit_level,
                                            "createdDateUTC": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")},
                               "market": {"epic": epic, "marketStatus": "TRADEABLE"}})
        return ref

    def confirm(self, ref):
        o = next(x for x in self.orders if x["ref"] == ref)
        return {"dealStatus": "ACCEPTED", "affectedDeals": [{"dealId": f"deal{self._deal_no}"}],
                "level": self.get_market(o["epic"])["snapshot"]["offer"]}

    def close_position(self, deal_id):
        self.closed.append(deal_id)
        self.positions = [p for p in self.positions if p["position"]["dealId"] != deal_id]
        self.transactions.append({"transactionType": "TRADE", "instrumentName": "US500", "size": "12.5",
                                  "reference": deal_id, "note": "Trade closed"})
        return "closeref"

    def update_position(self, deal_id, stop_level=None, profit_level=None):
        return "upd"

    def get_transactions(self, from_dt, to_dt=None, tx_type=None):
        return self.transactions


class BotLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = BotConfig(symbols=["US500"], timeframe="DAY", poll_seconds=0,
                             strategy=StrategyParams(type="mean_reversion"),
                             risk=RiskParams(size_mode="notional", exposure_pct=100, max_open_positions=3),
                             log_path=os.path.join(self.tmp, "bot.log"),
                             trades_csv=os.path.join(self.tmp, "trades.csv"),
                             state_path=os.path.join(self.tmp, "state.json"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _df_with_signal_on_last_closed(self):
        """Build candles whose last CLOSED candle (second to last row) carries a long signal."""
        df = synthetic_daily()
        strat = MeanReversionStrategy(self.cfg.strategy)
        out = strat.compute(df)
        sig_idx = np.flatnonzero(out["signal"].to_numpy() == 1)
        self.assertGreater(len(sig_idx), 0, "synthetic data produced no signal")
        i = int(sig_idx[-1])
        # keep rows up to the signal candle, then add one forming candle for "today"
        hist = df.iloc[: i + 1].copy()
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        hist.index = pd.date_range(end=today - timedelta(days=1), periods=len(hist), freq="D", tz="UTC")
        forming = hist.iloc[[-1]].copy()
        forming.index = pd.DatetimeIndex([today])
        return pd.concat([hist, forming])

    def test_opens_position_once_and_syncs_close(self):
        df = self._df_with_signal_on_last_closed()
        client = FakeClient(df)
        bot = TradingBot(self.cfg, client)
        bot.guard = bot.guard or None
        # mimic run() init without the loop
        bot.state["day"] = datetime.now(timezone.utc).date().isoformat()
        bot.state["day_start_balance"] = client.balance
        bot.state["peak_balance"] = client.balance
        from capital_bot.risk import AccountGuard
        bot.guard = AccountGuard(self.cfg.risk, client.balance, client.balance)

        bot.iteration()
        self.assertEqual(len(client.orders), 1)
        o = client.orders[0]
        self.assertEqual(o["direction"], "BUY")
        self.assertIsNone(o["tp"])
        price = client.get_market("US500")["snapshot"]["offer"]
        self.assertAlmostEqual(o["size"] * price, 10_000, delta=price * 0.1 + 1)   # ~100% notional
        self.assertLess(o["stop"], price)
        self.assertIn("deal1", bot.state["open_deals"])

        # same candle again -> no second order
        bot.iteration()
        self.assertEqual(len(client.orders), 1)

        # position closed by broker (SL/TP) -> journal gets the CLOSE row with pnl
        client.positions = []
        client.transactions.append({"transactionType": "TRADE", "instrumentName": "US500", "size": "-33.0",
                                    "reference": "deal1", "note": "Position closed"})
        bot.iteration()
        self.assertNotIn("deal1", bot.state["open_deals"])
        journal = open(self.cfg.trades_csv, encoding="utf-8").read()
        self.assertIn("OPEN", journal)
        self.assertIn("CLOSE", journal)
        self.assertIn("-33.0", journal)
        with open(self.cfg.state_path, encoding="utf-8") as f:
            self.assertIn("US500", json.load(f)["last_candle"])

    def test_exit_signal_closes_position(self):
        df = self._df_with_signal_on_last_closed()
        client = FakeClient(df)
        bot = TradingBot(self.cfg, client)
        from capital_bot.risk import AccountGuard
        bot.guard = AccountGuard(self.cfg.risk, client.balance, client.balance)
        bot.state["day"] = datetime.now(timezone.utc).date().isoformat()
        bot.iteration()
        self.assertEqual(len(client.positions), 1)

        # next day: a strong up candle so close > SMA5 -> exit signal
        new_closed = df.iloc[[-1]].copy()
        up = float(df["close"].iloc[-2]) * 1.03
        new_closed.loc[:, ["open", "high", "low", "close"]] = [up * 0.99, up * 1.005, up * 0.985, up]
        forming = new_closed.copy()
        forming.index = new_closed.index + timedelta(days=1)
        client.df = pd.concat([df.iloc[:-1], new_closed, forming])
        # pretend the clock moved one day: the candle at 'today' is now closed
        bot.state["last_candle"]["US500"] = df.index[-2].isoformat()
        import capital_bot.bot as botmod
        real_now = botmod._now
        botmod._now = lambda: real_now() + timedelta(days=1)
        try:
            bot.iteration()
        finally:
            botmod._now = real_now
        self.assertEqual(client.closed, ["deal1"])
        self.assertEqual(client.positions, [])

    def test_halt_blocks_new_entries(self):
        df = self._df_with_signal_on_last_closed()
        client = FakeClient(df)
        bot = TradingBot(self.cfg, client)
        from capital_bot.risk import AccountGuard
        bot.guard = AccountGuard(self.cfg.risk, 20_000, 20_000)   # balance 10k = 50% drawdown -> halted
        bot.state["day"] = datetime.now(timezone.utc).date().isoformat()
        bot.iteration()
        self.assertEqual(client.orders, [])
        self.assertIn("HALT", open(self.cfg.trades_csv, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
