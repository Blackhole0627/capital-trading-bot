"""Live/demo trading loop.

Every poll the bot: keeps the session alive, refreshes the balance, applies
the daily-loss / drawdown guards, syncs closed deals into the trade journal,
and for each symbol evaluates the strategy on the last CLOSED candle. A
candle is only evaluated once (state.json remembers the last candle time),
so restarts never double-trade.
"""
import csv
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd

from .api import CapitalAPIError, CapitalClient
from .config import BotConfig
from .data import candles_to_df
from .risk import AccountGuard, MarketRules, clamp_stop_distance, position_size
from .strategy import Signal, build_strategy

JOURNAL_FIELDS = ["time_utc", "event", "epic", "side", "size", "price", "stop", "target",
                  "deal_id", "pnl", "note"]


def _side(side: str) -> str:
    return {"BUY": "COMPRA", "SELL": "VENTA"}.get(side, side)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TradingBot:
    def __init__(self, cfg: BotConfig, client: CapitalClient, logger: Optional[logging.Logger] = None):
        self.cfg = cfg
        self.client = client
        self.log = logger or logging.getLogger("capital_bot")
        self.strategy = build_strategy(cfg.strategy)
        self.rules: Dict[str, MarketRules] = {}
        self.state = self._load_state()
        self.guard: Optional[AccountGuard] = None
        self._halt_logged: Optional[str] = None
        self._running = False

    # ------------------------------------------------------------ persistence
    def _load_state(self) -> dict:
        default = {"last_candle": {}, "day": None, "day_start_balance": None,
                   "peak_balance": None, "open_deals": {}}
        try:
            with open(self.cfg.state_path, "r", encoding="utf-8") as f:
                default.update(json.load(f))
        except (OSError, ValueError):
            pass
        return default

    def _save_state(self):
        os.makedirs(os.path.dirname(self.cfg.state_path) or ".", exist_ok=True)
        with open(self.cfg.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, default=str)

    def _journal(self, event: str, epic: str = "", side: str = "", size: float = "", price="", stop="",
                 target="", deal_id: str = "", pnl="", note: str = ""):
        path = self.cfg.trades_csv
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
            if new:
                w.writeheader()
            w.writerow({"time_utc": _now().strftime("%Y-%m-%d %H:%M:%S"), "event": event, "epic": epic,
                        "side": side, "size": size, "price": price, "stop": stop, "target": target,
                        "deal_id": deal_id, "pnl": pnl, "note": note})

    # ---------------------------------------------------------------- markets
    def _market_rules(self, epic: str) -> MarketRules:
        if epic in self.rules:
            return self.rules[epic]
        m = self.client.get_market(epic)
        dr = m.get("dealingRules", {})
        inst = m.get("instrument", {})
        snap = m.get("snapshot", {})
        price = snap.get("offer") or snap.get("bid") or 1.0

        def pct(rule):
            if not rule:
                return None
            v = float(rule.get("value", 0))
            if rule.get("unit") == "PERCENTAGE":
                return v
            return v / price * 100.0 if price else None  # POINTS -> %

        rules = MarketRules(
            min_size=float(dr.get("minDealSize", {}).get("value", 0.01)),
            size_step=float(dr.get("minSizeIncrement", {}).get("value", 0.01)),
            lot_size=float(inst.get("lotSize", 1) or 1),
            min_stop_pct=pct(dr.get("minStopOrProfitDistance")),
            max_stop_pct=pct(dr.get("maxStopOrProfitDistance")),
            decimals=int(snap.get("decimalPlacesFactor", 5)),
        )
        self.rules[epic] = rules
        self.log.info("%s reglas: tamaño mín. %s paso %s lote %s stop mín. %s%% decimales %s",
                      epic, rules.min_size, rules.size_step, rules.lot_size, rules.min_stop_pct, rules.decimals)
        return rules

    # ------------------------------------------------------------------- run
    def run(self):
        self.client.login()
        bal = self.client.get_balance()
        balance = bal["balance"]
        self.log.info("Saldo %.2f %s | disponible %.2f | mercados %s | velas %s",
                      balance, self.client.currency, bal["available"], self.cfg.symbols, self.cfg.timeframe)
        today = _now().date().isoformat()
        if self.state.get("day") != today or not self.state.get("day_start_balance"):
            self.state["day"] = today
            self.state["day_start_balance"] = balance
        self.state["peak_balance"] = max(self.state.get("peak_balance") or 0.0, balance)
        self.guard = AccountGuard(self.cfg.risk, self.state["day_start_balance"], self.state["peak_balance"])
        for epic in self.cfg.symbols:
            self._market_rules(epic)
        self._save_state()
        self._journal("START", note=f"saldo {balance:.2f} {self.client.currency}")

        self._running = True
        last_ping = time.time()
        while self._running:
            try:
                if time.time() - last_ping > 240:
                    self.client.ping()
                    last_ping = time.time()
                self.iteration()
            except CapitalAPIError as e:
                self.log.error("Error de API: %s", e)
            except KeyboardInterrupt:
                self.log.info("Detenido por el usuario")
                self._running = False
                break
            except Exception as e:  # keep the loop alive on unexpected errors
                self.log.exception("Error inesperado: %s", e)
            time.sleep(self.cfg.poll_seconds)
        self._journal("STOP")

    def stop(self):
        self._running = False

    # ------------------------------------------------------------- iteration
    def iteration(self):
        now = _now()
        balance = self.client.get_balance()["balance"]

        today = now.date().isoformat()
        if self.state.get("day") != today:
            self.state["day"] = today
            self.state["day_start_balance"] = balance
            self.guard.new_day(balance)
            self.log.info("Nuevo día de trading, saldo inicial %.2f", balance)

        halted = self.guard.check(balance)
        self.state["peak_balance"] = self.guard.peak_balance
        if halted and halted != self._halt_logged:
            self.log.warning("NUEVAS ENTRADAS EN PAUSA: %s (saldo %.2f)", halted, balance)
            self._journal("HALT", note=halted)
            self._halt_logged = halted
        elif not halted:
            self._halt_logged = None

        positions = self.client.get_positions()
        self._sync_closed(positions)

        for epic in self.cfg.symbols:
            try:
                self._process_symbol(epic, positions, balance, halted is not None, now)
            except CapitalAPIError as e:
                self.log.error("%s: %s", epic, e)
        self._save_state()

    def _process_symbol(self, epic: str, positions: List[dict], balance: float, halted: bool, now: datetime):
        candles = self.client.get_prices(epic, self.cfg.timeframe, self.cfg.candles_to_fetch)
        df = candles_to_df(candles)
        if df.empty:
            return
        tf = timedelta(seconds=self.cfg.timeframe_seconds)
        if df.index[-1] + tf > now:       # last candle still forming
            df = df.iloc[:-1]
        if df.empty:
            return
        last_time = df.index[-1].isoformat()
        if self.state["last_candle"].get(epic) == last_time:
            return                          # candle already evaluated
        self.state["last_candle"][epic] = last_time

        mine = [p for p in positions if p.get("market", {}).get("epic") == epic]
        dec = self.strategy.latest(df)
        if dec is None:
            self.log.warning("%s: solo %d velas, se necesitan %d", epic, len(df), self.strategy.min_bars)
            return
        signal = dec.entry
        self.log.info("%s vela %s cierre %.2f | entrada %s | salida %s | posiciones abiertas %d",
                      epic, last_time[:16], dec.close, _side(signal.side) if signal else "no",
                      "sí" if (dec.exit_long or dec.exit_short) and mine else "no", len(mine))

        if mine and self.cfg.strategy.trailing_stop:
            self._trail(epic, mine, df)

        # signal / time based exits
        still_open = []
        for p in mine:
            pos = p["position"]
            is_long = pos["direction"] == "BUY"
            reason = None
            if (is_long and dec.exit_long) or (not is_long and dec.exit_short):
                reason = "señal de salida"
            elif signal and ((is_long and signal.direction < 0) or (not is_long and signal.direction > 0)):
                reason = "señal contraria"
            elif self.cfg.strategy.max_hold_bars:
                opened = self.state["open_deals"].get(pos["dealId"], {}).get("time") or pos.get("createdDateUTC")
                if opened:
                    opened_ts = pd.Timestamp(opened)
                    if opened_ts.tzinfo is None:
                        opened_ts = opened_ts.tz_localize("UTC")
                    held = int((df.index > opened_ts).sum())
                    if held >= self.cfg.strategy.max_hold_bars:
                        reason = f"tiempo máximo {held} velas"
            if reason:
                self.log.info("%s cerrando %s (%s)", epic, pos["dealId"], reason)
                self.client.close_position(pos["dealId"])
                self._journal("CLOSE_REQ", epic, pos["direction"], pos["size"], deal_id=pos["dealId"], note=reason)
            else:
                still_open.append(p)
        mine = still_open

        if signal and not mine and not halted:
            if len(positions) >= self.cfg.risk.max_open_positions:
                self.log.info("%s señal omitida: máximo de posiciones abiertas alcanzado", epic)
                return
            self._open(epic, signal, balance)

    # -------------------------------------------------------------- trading
    def _open(self, epic: str, signal: Signal, balance: float):
        market = self.client.get_market(epic)
        snap = market.get("snapshot", {})
        if snap.get("marketStatus") != "TRADEABLE":
            self.log.info("%s no se puede operar ahora (%s)", epic, snap.get("marketStatus"))
            return
        rules = self._market_rules(epic)
        d = signal.direction
        price = float(snap.get("offer") if d > 0 else snap.get("bid"))
        stop_dist = clamp_stop_distance(price, signal.stop_distance, rules)
        stop_level = round(price - stop_dist * d, rules.decimals)
        profit_level = None
        if signal.profit_distance > 0:
            tp_dist = clamp_stop_distance(price, signal.profit_distance, rules)
            profit_level = round(price + tp_dist * d, rules.decimals)
        vpp = self.cfg.risk.value_per_point.get(epic)
        size = position_size(balance, stop_dist, self.cfg.risk, rules, vpp, price)
        if size <= 0:
            self.log.warning("%s señal %s omitida: tamaño calculado inferior al mínimo (saldo %.2f, stop %.5f)",
                             epic, _side(signal.side), balance, stop_dist)
            return

        self.log.info("%s %s tamaño %s @ ~%.2f stop %.2f objetivo %s (ATR %.2f)", epic, _side(signal.side), size, price,
                      stop_level, profit_level if profit_level is not None else "-", signal.atr)
        ref = self.client.open_position(epic, signal.side, size, stop_level=stop_level, profit_level=profit_level)
        conf = self.client.confirm(ref)
        if conf.get("dealStatus") == "ACCEPTED":
            deals = conf.get("affectedDeals") or []
            deal_id = (deals[0].get("dealId") if deals else None) or conf.get("dealId", "")
            level = conf.get("level", price)
            self.state["open_deals"][deal_id] = {"epic": epic, "side": signal.side, "size": size,
                                                 "entry": level, "time": _now().isoformat()}
            self._journal("OPEN", epic, signal.side, size, level, stop_level, profit_level, deal_id,
                          note=f"ref {ref}")
            self.log.info("%s posición ABIERTA dealId %s a %s", epic, deal_id, level)
        else:
            reason = conf.get("rejectReason") or conf.get("reason") or conf
            self._journal("REJECT", epic, signal.side, size, price, stop_level, profit_level, note=str(reason))
            self.log.warning("%s orden rechazada: %s", epic, reason)

    def _trail(self, epic: str, mine: List[dict], df: pd.DataFrame):
        data = self.strategy.compute(df)
        atr_val = float(data["atr"].iloc[-1])
        close = float(data["close"].iloc[-1])
        rules = self._market_rules(epic)
        for p in mine:
            pos = p["position"]
            d = 1 if pos["direction"] == "BUY" else -1
            current = pos.get("stopLevel")
            new_stop = round(close - self.cfg.strategy.atr_sl * atr_val * d, rules.decimals)
            if current is None or (d > 0 and new_stop > current) or (d < 0 and new_stop < current):
                self.log.info("%s stop dinámico %s -> %s", epic, current, new_stop)
                self.client.update_position(pos["dealId"], stop_level=new_stop)

    def _sync_closed(self, positions: List[dict]):
        open_ids = {p["position"]["dealId"] for p in positions if "position" in p}
        gone = [d for d in self.state["open_deals"] if d not in open_ids]
        if not gone:
            return
        txs = []
        try:
            txs = self.client.get_transactions(_now() - timedelta(days=3))
        except CapitalAPIError as e:
            self.log.warning("No se pudieron obtener las transacciones: %s", e)
        for deal_id in gone:
            info = self.state["open_deals"].pop(deal_id)
            pnl, note = "", "cerrada (SL/TP/manual)"
            match = next((t for t in txs if t.get("transactionType") == "TRADE"
                          and deal_id in (str(t.get("dealId", "")), str(t.get("reference", "")))), None)
            if match is None:
                cands = [t for t in txs if t.get("transactionType") == "TRADE"
                         and info["epic"].replace("/", "") in str(t.get("instrumentName", "")).replace("/", "")]
                match = cands[-1] if cands else None
            if match:
                pnl = match.get("size", "")
                note = match.get("note", note)
            self._journal("CLOSE", info["epic"], info["side"], info["size"], deal_id=deal_id, pnl=pnl, note=note)
            self.log.info("%s posición CERRADA dealId %s | G/P %s | %s", info["epic"], deal_id, pnl or "n/a", note)
