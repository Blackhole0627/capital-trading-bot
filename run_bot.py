"""Start the trading bot on Capital.com (DEMO by default).

    python run_bot.py --config config.json
"""
import argparse
import os
import sys

from capital_bot.api import CapitalClient
from capital_bot.bot import TradingBot
from capital_bot.config import load_config
from capital_bot.logger import setup_logger


def main():
    ap = argparse.ArgumentParser(description="Bot de trading para Capital.com")
    ap.add_argument("--config", default="config.json", help="ruta de config.json")
    ap.add_argument("--live", action="store_true", help="forzar cuenta REAL (por defecto según config)")
    args = ap.parse_args()

    # when packaged as .exe, work relative to the exe folder
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))

    cfg = load_config(args.config)
    if args.live:
        cfg.api.demo = False
    log = setup_logger(cfg.log_path)

    if not (cfg.api.api_key and cfg.api.identifier and cfg.api.password):
        log.error("Faltan las credenciales de la API. Copie config.example.json como config.json y complete "
                  "api_key / identifier / password (o defina CAPITAL_API_KEY, CAPITAL_IDENTIFIER, CAPITAL_PASSWORD).")
        sys.exit(1)

    log.info("Iniciando bot de Capital.com | modo %s", "DEMO" if cfg.api.demo else "REAL  <<< DINERO REAL >>>")
    if not cfg.api.demo:
        answer = input("Va a operar con una cuenta REAL. Escriba SI para continuar: ")
        if answer.strip().upper() not in ("SI", "SÍ"):
            log.info("Cancelado")
            sys.exit(0)

    client = CapitalClient(cfg.api.api_key, cfg.api.identifier, cfg.api.password, demo=cfg.api.demo, logger=log)
    bot = TradingBot(cfg, client, log)
    try:
        bot.run()
    except KeyboardInterrupt:
        log.info("Detenido por el usuario")


if __name__ == "__main__":
    main()
