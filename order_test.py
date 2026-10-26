"""Execution test: open a minimum-size position with a stop loss, confirm it,
wait a few seconds and close it. Proves the API order flow works end to end.
This is NOT a strategy signal. Refuses to run on a LIVE account.

    python order_test.py --epic US500 --side BUY --hold 10
"""
import argparse
import os
import sys
import time

from capital_bot.api import CapitalAPIError, CapitalClient
from capital_bot.config import load_config
from capital_bot.logger import setup_logger


def main():
    ap = argparse.ArgumentParser(description="Open and close one minimum-size DEMO position")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--epic", default="US500")
    ap.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    ap.add_argument("--hold", type=int, default=10, help="seconds to keep the position open")
    args = ap.parse_args()
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    cfg = load_config(args.config)
    log = setup_logger(cfg.log_path)
    if not cfg.api.demo:
        log.error("La prueba de ejecución solo funciona en una cuenta DEMO (\"demo\": true en config.json)")
        sys.exit(1)

    c = CapitalClient(cfg.api.api_key, cfg.api.identifier, cfg.api.password, demo=True, logger=log)
    try:
        c.login()
        bal = c.get_balance()
        log.info("PRUEBA DE EJECUCIÓN (no es una señal de la estrategia) | saldo %.2f %s", bal["balance"], c.currency)

        m = c.get_market(args.epic)
        snap, dr = m["snapshot"], m["dealingRules"]
        if snap.get("marketStatus") != "TRADEABLE":
            log.error("%s está %s, pruebe otro mercado o en horario de mercado", args.epic, snap.get("marketStatus"))
            sys.exit(1)
        size = float(dr["minDealSize"]["value"])
        price = float(snap["offer"] if args.side == "BUY" else snap["bid"])
        d = 1 if args.side == "BUY" else -1
        decimals = int(snap.get("decimalPlacesFactor", 2))
        stop = round(price * (1 - 0.01 * d), decimals)          # 1% protective stop
        log.info("%s %s tamaño %s @ ~%s con stop %s", "COMPRA" if args.side == "BUY" else "VENTA", args.epic, size, price, stop)

        ref = c.open_position(args.epic, args.side, size, stop_level=stop)
        conf = c.confirm(ref)
        if conf.get("dealStatus") != "ACCEPTED":
            log.error("Orden rechazada: %s", conf.get("rejectReason") or conf)
            sys.exit(1)
        deals = conf.get("affectedDeals") or []
        deal_id = (deals[0].get("dealId") if deals else None) or conf.get("dealId")
        log.info("ABIERTA dealId %s a %s (estado: aceptada)", deal_id, conf.get("level"))

        for p in c.get_positions():
            pos = p["position"]
            if pos["dealId"] == deal_id:
                log.info("Posición visible en la cuenta: %s %s tamaño %s nivel %s stop %s",
                         p["market"]["epic"], "COMPRA" if pos["direction"] == "BUY" else "VENTA",
                         pos["size"], pos["level"], pos.get("stopLevel"))

        log.info("Manteniendo %d segundos ...", args.hold)
        time.sleep(args.hold)

        cref = c.close_position(deal_id)
        cconf = c.confirm(cref)
        log.info("CERRADA dealId %s a %s (estado: %s)", deal_id, cconf.get("level"),
                 "aceptada" if cconf.get("dealStatus") == "ACCEPTED" else cconf.get("dealStatus"))
        bal2 = c.get_balance()
        log.info("Saldo después de la prueba %.2f %s (cambio %.2f, coste del spread)", bal2["balance"], c.currency,
                 bal2["balance"] - bal["balance"])
        log.info("Prueba de ejecución OK")
    except CapitalAPIError as e:
        log.error("Error de API: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
