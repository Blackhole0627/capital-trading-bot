"""Export the account's trade history (closed positions, P&L) from Capital.com to CSV.

    python export_history.py --config config.json --days 30
"""
import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

from capital_bot.api import CapitalClient
from capital_bot.config import load_config
from capital_bot.logger import setup_logger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="reports/account_history.csv")
    args = ap.parse_args()
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    cfg = load_config(args.config)
    log = setup_logger(None)
    c = CapitalClient(cfg.api.api_key, cfg.api.identifier, cfg.api.password, demo=cfg.api.demo, logger=log)
    c.login()
    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    txs = c.get_transactions(since)
    acts = c.get_activity(since)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tipo", "fecha_utc", "instrumento", "tipo_transaccion", "importe", "moneda", "referencia", "nota"])
        for t in txs:
            w.writerow(["transaction", t.get("dateUtc"), t.get("instrumentName"), t.get("transactionType"),
                        t.get("size"), t.get("currency"), t.get("reference"), t.get("note")])
        for a in acts:
            d = a.get("details", {}) or {}
            w.writerow(["activity", a.get("dateUtc"), a.get("epic"), a.get("type"),
                        d.get("size"), "", a.get("dealId"), f"{a.get('status')} {a.get('source')} "
                        f"dir={d.get('direction')} level={d.get('level')} sl={d.get('stopLevel')} tp={d.get('profitLevel')}"])

    trades = [t for t in txs if t.get("transactionType") == "TRADE"]
    pnl = [float(t.get("size", 0) or 0) for t in trades]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    print(f"\nÚltimos {args.days} días: {len(trades)} operaciones cerradas, neto {sum(pnl):.2f} {c.currency}, "
          f"ganadoras {len(wins)} ({sum(wins):.2f}), perdedoras {len(losses)} ({sum(losses):.2f})")
    print(f"Guardadas {len(txs)} transacciones y {len(acts)} actividades en {args.out}")


if __name__ == "__main__":
    main()
