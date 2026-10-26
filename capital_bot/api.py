"""Thin client for the Capital.com REST API.

Docs: https://open-api.capital.com
Auth: POST /api/v1/session with X-CAP-API-KEY header returns CST and
X-SECURITY-TOKEN headers that must be sent on every later request. Sessions
expire after ~10 minutes of inactivity, so the bot pings regularly and
re-logs-in on 401.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

LIVE_URL = "https://api-capital.backend-capital.com/api/v1"
DEMO_URL = "https://demo-api-capital.backend-capital.com/api/v1"

TIME_FMT = "%Y-%m-%dT%H:%M:%S"


class CapitalAPIError(Exception):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.payload = payload


class CapitalClient:
    def __init__(self, api_key: str, identifier: str, password: str, demo: bool = True,
                 timeout: int = 20, logger: Optional[logging.Logger] = None):
        if not (api_key and identifier and password):
            raise ValueError("api_key, identifier and password are required")
        self.api_key = api_key
        self.identifier = identifier
        self.password = password
        self.base_url = DEMO_URL if demo else LIVE_URL
        self.demo = demo
        self.timeout = timeout
        self.log = logger or logging.getLogger("capital_bot")

        self._session = requests.Session()
        self._cst: Optional[str] = None
        self._token: Optional[str] = None
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._last_session_call = 0.0
        self.account_info: Dict[str, Any] = {}
        self.current_account_id: Optional[str] = None
        self.currency: Optional[str] = None

    # ------------------------------------------------------------------ auth
    def login(self) -> Dict[str, Any]:
        # Capital.com allows 1 session request per second
        wait = 1.05 - (time.time() - self._last_session_call)
        if wait > 0:
            time.sleep(wait)
        headers = {"X-CAP-API-KEY": self.api_key, "Content-Type": "application/json"}
        body = {"identifier": self.identifier, "password": self.password, "encryptedPassword": False}
        r = self._session.post(f"{self.base_url}/session", json=body, headers=headers, timeout=self.timeout)
        self._last_session_call = time.time()
        if r.status_code != 200:
            raise CapitalAPIError(r.status_code, f"error de inicio de sesión: {r.text}")
        self._cst = r.headers.get("CST")
        self._token = r.headers.get("X-SECURITY-TOKEN")
        data = r.json()
        self.account_info = data.get("accountInfo", {})
        self.current_account_id = data.get("currentAccountId")
        self.currency = data.get("currencyIsoCode")
        self.log.info("Conectado a Capital.com %s | cuenta %s | %s",
                      "DEMO" if self.demo else "LIVE", self.current_account_id, self.currency)
        return data

    @property
    def logged_in(self) -> bool:
        return bool(self._cst and self._token)

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "X-CAP-API-KEY": self.api_key,
            "CST": self._cst or "",
            "X-SECURITY-TOKEN": self._token or "",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, params: Optional[dict] = None,
                 json: Optional[dict] = None, _retry: bool = True) -> Any:
        if not self.logged_in:
            self.login()
        with self._lock:
            # global limit is 10 req/s; trading endpoints 1 per 0.1 s
            gap = 0.12 - (time.time() - self._last_request)
            if gap > 0:
                time.sleep(gap)
            try:
                r = self._session.request(method, f"{self.base_url}{path}", params=params, json=json,
                                          headers=self._auth_headers(), timeout=self.timeout)
            finally:
                self._last_request = time.time()

        if r.status_code == 401 and _retry:
            self.log.warning("Sesión expirada, iniciando sesión de nuevo")
            self._cst = self._token = None
            self.login()
            return self._request(method, path, params, json, _retry=False)
        if r.status_code == 429 and _retry:
            self.log.warning("Límite de peticiones alcanzado, esperando 2 s")
            time.sleep(2)
            return self._request(method, path, params, json, _retry=False)
        if r.status_code >= 400:
            try:
                payload = r.json()
            except ValueError:
                payload = r.text
            raise CapitalAPIError(r.status_code, f"{method} {path}: {payload}", payload)
        if not r.text:
            return {}
        return r.json()

    def ping(self) -> bool:
        try:
            self._request("GET", "/ping")
            return True
        except CapitalAPIError as e:
            self.log.warning("Fallo de ping: %s", e)
            return False

    # -------------------------------------------------------------- accounts
    def get_accounts(self) -> List[dict]:
        return self._request("GET", "/accounts").get("accounts", [])

    def get_balance(self) -> Dict[str, float]:
        """Balance of the current account: balance, deposit, profitLoss, available."""
        for acc in self.get_accounts():
            if acc.get("accountId") == self.current_account_id or self.current_account_id is None:
                b = acc.get("balance", {})
                return {k: float(b.get(k, 0.0)) for k in ("balance", "deposit", "profitLoss", "available")}
        raise CapitalAPIError(0, "current account not found")

    # --------------------------------------------------------------- markets
    def search_markets(self, term: str) -> List[dict]:
        return self._request("GET", "/markets", params={"searchTerm": term}).get("markets", [])

    def get_market(self, epic: str) -> dict:
        return self._request("GET", f"/markets/{epic}")

    def get_prices(self, epic: str, resolution: str = "HOUR", max_bars: int = 300,
                   from_dt: Optional[datetime] = None, to_dt: Optional[datetime] = None) -> List[dict]:
        """Candles as dicts with UTC time and mid OHLC (plus bid/ask close)."""
        params: Dict[str, Any] = {"resolution": resolution, "max": min(max_bars, 1000)}
        if from_dt:
            params["from"] = from_dt.strftime(TIME_FMT)
        if to_dt:
            params["to"] = to_dt.strftime(TIME_FMT)
        data = self._request("GET", f"/prices/{epic}", params=params)
        out = []
        for p in data.get("prices", []):
            def mid(key):
                v = p.get(key, {})
                bid, ask = v.get("bid"), v.get("ask")
                if bid is None:
                    return ask
                if ask is None:
                    return bid
                return round((bid + ask) / 2.0, 8)
            t = p.get("snapshotTimeUTC") or p.get("snapshotTime")
            out.append({
                "time": datetime.strptime(t, TIME_FMT).replace(tzinfo=timezone.utc),
                "open": mid("openPrice"), "high": mid("highPrice"),
                "low": mid("lowPrice"), "close": mid("closePrice"),
                "bid": p.get("closePrice", {}).get("bid"),
                "ask": p.get("closePrice", {}).get("ask"),
                "volume": p.get("lastTradedVolume", 0),
            })
        out.sort(key=lambda c: c["time"])
        return out

    def get_history(self, epic: str, resolution: str, from_dt: datetime, to_dt: Optional[datetime] = None,
                    step_seconds: int = 3600) -> List[dict]:
        """Page backwards through /prices to collect as much history as the API offers."""
        to_dt = to_dt or datetime.now(timezone.utc)
        all_bars: Dict[datetime, dict] = {}
        cursor = to_dt
        while cursor > from_dt:
            window_start = max(from_dt, cursor - timedelta(seconds=step_seconds * 950))
            bars = self.get_prices(epic, resolution, 1000, window_start, cursor)
            for b in bars:
                all_bars[b["time"]] = b
            if not bars or bars[0]["time"] <= window_start:
                cursor = window_start - timedelta(seconds=step_seconds)
            else:
                cursor = bars[0]["time"] - timedelta(seconds=step_seconds)
            if not bars and window_start == from_dt:
                break
        return [all_bars[k] for k in sorted(all_bars)]

    # ------------------------------------------------------------- positions
    def get_positions(self) -> List[dict]:
        return self._request("GET", "/positions").get("positions", [])

    def open_position(self, epic: str, direction: str, size: float,
                      stop_level: Optional[float] = None, profit_level: Optional[float] = None,
                      stop_distance: Optional[float] = None, profit_distance: Optional[float] = None,
                      guaranteed_stop: bool = False, trailing_stop: bool = False) -> str:
        body: Dict[str, Any] = {"epic": epic, "direction": direction.upper(), "size": size,
                                "guaranteedStop": guaranteed_stop, "trailingStop": trailing_stop}
        if stop_level is not None:
            body["stopLevel"] = stop_level
        if profit_level is not None:
            body["profitLevel"] = profit_level
        if stop_distance is not None:
            body["stopDistance"] = stop_distance
        if profit_distance is not None:
            body["profitDistance"] = profit_distance
        data = self._request("POST", "/positions", json=body)
        return data.get("dealReference", "")

    def confirm(self, deal_reference: str) -> dict:
        return self._request("GET", f"/confirms/{deal_reference}")

    def update_position(self, deal_id: str, stop_level: Optional[float] = None,
                        profit_level: Optional[float] = None) -> str:
        body: Dict[str, Any] = {}
        if stop_level is not None:
            body["stopLevel"] = stop_level
        if profit_level is not None:
            body["profitLevel"] = profit_level
        return self._request("PUT", f"/positions/{deal_id}", json=body).get("dealReference", "")

    def close_position(self, deal_id: str) -> str:
        return self._request("DELETE", f"/positions/{deal_id}").get("dealReference", "")

    # --------------------------------------------------------------- history
    def get_activity(self, from_dt: datetime, to_dt: Optional[datetime] = None, detailed: bool = True) -> List[dict]:
        params = {"from": from_dt.strftime(TIME_FMT), "detailed": str(detailed).lower()}
        if to_dt:
            params["to"] = to_dt.strftime(TIME_FMT)
        return self._request("GET", "/history/activity", params=params).get("activities", [])

    def get_transactions(self, from_dt: datetime, to_dt: Optional[datetime] = None,
                         tx_type: Optional[str] = None) -> List[dict]:
        params: Dict[str, Any] = {"from": from_dt.strftime(TIME_FMT)}
        if to_dt:
            params["to"] = to_dt.strftime(TIME_FMT)
        if tx_type:
            params["type"] = tx_type
        return self._request("GET", "/history/transactions", params=params).get("transactions", [])
