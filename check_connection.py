"""Verify API credentials: logs in, prints account balance, market details and last candles.

    python check_connection.py --config config.json [--epic EURUSD]
"""
import argparse
import os
import sys

from capital_bot.api import CapitalAPIError, CapitalClient
from capital_bot.config import load_config
from capital_bot.logger import setup_logger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--epic", default=None)
    ap.add_argument("--search", default=None, help="buscar mercados por nombre, p. ej. 'EUR/USD' o 'US 500'")
    args = ap.parse_args()
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    cfg = load_config(args.config)
    log = setup_logger(None)
    try:
        c = CapitalClient(cfg.api.api_key, cfg.api.identifier, cfg.api.password, demo=cfg.api.demo, logger=log)
        c.login()
        bal = c.get_balance()
        print(f"\nCuenta {c.current_account_id} ({'DEMO' if cfg.api.demo else 'REAL'}) {c.currency}")
        print(f"  saldo {bal['balance']:.2f}  disponible {bal['available']:.2f}  G/P {bal['profitLoss']:.2f}")

        if args.search:
            print(f"\nMercados que coinciden con '{args.search}':")
            for m in c.search_markets(args.search)[:20]:
                print(f"  {m.get('epic'):<15} {m.get('instrumentName'):<30} {m.get('marketStatus')}")

        for epic in ([args.epic] if args.epic else cfg.symbols):
            m = c.get_market(epic)
            inst, dr, snap = m.get("instrument", {}), m.get("dealingRules", {}), m.get("snapshot", {})
            print(f"\n{epic}: {inst.get('name')}  estado {snap.get('marketStatus')}  venta {snap.get('bid')} compra {snap.get('offer')}")
            print(f"  lote {inst.get('lotSize')}  tamaño mín. {dr.get('minDealSize', {}).get('value')}  "
                  f"paso {dr.get('minSizeIncrement', {}).get('value')}  "
                  f"stop mín. {dr.get('minStopOrProfitDistance')}  decimales {snap.get('decimalPlacesFactor')}")
            bars = c.get_prices(epic, cfg.timeframe, 3)
            for b in bars:
                print(f"  {b['time']:%Y-%m-%d %H:%M} apertura {b['open']} máx {b['high']} mín {b['low']} cierre {b['close']}")
        print("\nConexión OK")
    except CapitalAPIError as e:
        print(f"\nERROR DE API: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
