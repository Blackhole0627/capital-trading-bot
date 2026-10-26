"""Scripted walkthrough for recording a demo video (DEMO account only).

Shows, step by step with pauses: connection + account, markets, open positions
and P&L, the bot's trade journal, a live execution test, and the backtest
reports (opened in the browser). All on-screen text is in Spanish.

    python demo_video.py            (or double-click demo_video.bat)
    python demo_video.py --no-order (skip the execution test)
"""
import argparse
import csv
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime, timedelta, timezone

from capital_bot.api import CapitalAPIError, CapitalClient
from capital_bot.config import load_config

PAUSE = 4

EVENTS = {"OPEN": "APERTURA", "CLOSE": "CIERRE", "CLOSE_REQ": "CIERRE", "REJECT": "RECHAZO", "HALT": "PAUSA"}
SIDES = {"BUY": "COMPRA", "SELL": "VENTA"}
STATUS = {"TRADEABLE": "ABIERTO", "CLOSED": "CERRADO", "EDITS_ONLY": "SOLO EDICION", "OFFLINE": "FUERA DE LINEA"}
TF = {"DAY": "diarias", "HOUR": "de 1 hora", "HOUR_4": "de 4 horas", "MINUTE_15": "de 15 min",
      "MINUTE_5": "de 5 min", "MINUTE": "de 1 min", "MINUTE_30": "de 30 min", "WEEK": "semanales"}


def title(n, text):
    print("\n" + "=" * 74)
    print(f"  {n}. {text}")
    print("=" * 74)
    time.sleep(1.5)


def pause(sec=None):
    time.sleep(PAUSE if sec is None else sec)


def main():
    global PAUSE
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--no-order", action="store_true", help="skip the execution test")
    ap.add_argument("--pause", type=float, default=PAUSE)
    args = ap.parse_args()
    PAUSE = args.pause
    if getattr(sys, "frozen", False):
        os.chdir(os.path.dirname(sys.executable))
    os.system("")  # enable ANSI on Windows consoles

    cfg = load_config(args.config)
    if not cfg.api.demo:
        sys.exit("Esta demostración solo funciona con una cuenta DEMO")

    print("\n  BOT DE TRADING PARA CAPITAL.COM  -  solo API, sin MetaTrader")
    print("  Demostración en el entorno DEMO de Capital.com (según lo solicitado)")
    print(f"  {datetime.now():%d/%m/%Y %H:%M}  |  estrategia: reversión a la media en índices")
    pause()

    # 1 -------------------------------------------------------------- login
    title(1, "Conexión a Capital.com mediante la API oficial")
    c = CapitalClient(cfg.api.api_key, cfg.api.identifier, cfg.api.password, demo=True)
    try:
        c.login()
    except CapitalAPIError as e:
        sys.exit(f"Error de inicio de sesión: {e}")
    bal = c.get_balance()
    print(f"  Conectado        cuenta {c.current_account_id} (DEMO, {c.currency})")
    print(f"  Saldo            {bal['balance']:,.2f}   disponible {bal['available']:,.2f}   "
          f"G/P abierta {bal['profitLoss']:+,.2f}")
    pause()

    # 2 ------------------------------------------------------------ markets
    title(2, f"Mercados que opera el bot: {', '.join(cfg.symbols)}  (velas {TF.get(cfg.timeframe, cfg.timeframe)})")
    for epic in cfg.symbols:
        m = c.get_market(epic)
        s = m["snapshot"]
        print(f"  {epic:<7} {m['instrument']['name']:<22} venta {s['bid']:>10}  compra {s['offer']:>10}  "
              f"{STATUS.get(s['marketStatus'], s['marketStatus'])}")
    pause()

    # 3 ---------------------------------------------------------- positions
    title(3, "Posiciones abiertas (abiertas por el bot)")
    positions = c.get_positions()
    if not positions:
        print("  No hay posiciones abiertas ahora - el bot está esperando una señal.")
    for p in positions:
        pos, mk = p["position"], p["market"]
        print(f"  {mk['epic']:<7} {SIDES.get(pos['direction'], pos['direction']):<6} tamaño {pos['size']:<7} "
              f"entrada {pos['level']:>10}  stop {pos.get('stopLevel')}  actual {mk['bid']}  "
              f"G/P {float(pos.get('upl', 0)):+.2f} {pos['currency']}")
        print(f"          abierta el {pos['createdDateUTC'][:19].replace('T', ' ')} UTC")
    pause()

    # 4 ------------------------------------------------------------ journal
    title(4, "Diario de operaciones del bot  (logs/trades.csv)")
    if os.path.exists(cfg.trades_csv):
        with open(cfg.trades_csv, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["event"] in EVENTS]
        for r in rows[-15:]:
            print(f"  {r['time_utc']}  {EVENTS[r['event']]:<9} {r['epic']:<7} {SIDES.get(r['side'], r['side']):<6} "
                  f"tamaño {r['size']:<7} precio {r['price']:<10} stop {r['stop']:<10} G/P {r['pnl']}")
        if not rows:
            print("  (todavía no hay operaciones)")
    else:
        print("  (el diario todavía no existe)")
    pause()

    # 5 ---------------------------------------------------- closed trades
    title(5, "Operaciones cerradas en la cuenta (historial de Capital.com, últimos 30 días)")
    txs = [t for t in c.get_transactions(datetime.now(timezone.utc) - timedelta(days=30))
           if t.get("transactionType") == "TRADE"]
    total = 0.0
    for t in txs:
        v = float(t.get("size") or 0)
        total += v
        print(f"  {t['dateUtc'][:19].replace('T', ' ')}  {t.get('instrumentName', ''):<10} "
              f"operación cerrada  {v:+.2f} {t.get('currency')}")
    print(f"  Operaciones cerradas: {len(txs)}   G/P realizada: {total:+.2f}")
    pause()

    # 6 ------------------------------------------------------ execution
    if not args.no_order:
        title(6, "Prueba de ejecución (abre y cierra el tamaño mínimo; no es una señal de la estrategia)")
        cmd = (["CapitalOrderTest.exe"] if getattr(sys, "frozen", False)
               else [sys.executable, "order_test.py"]) + ["--config", args.config, "--hold", "6"]
        subprocess.call(cmd)
        pause()

    # 7 ------------------------------------------------------- backtests
    title(7, "Resultados de backtest de la estrategia (simulación histórica 2000-2026)")
    for name in ("US500_DAY", "NAS100_DAY", "US30_DAY"):
        path = os.path.join("reports", f"{name}_report.txt")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            keep = [l for l in lines if any(k in l for k in ("BACKTEST", "Periodo", "Beneficio neto", "CAGR",
                                                              "Drawdown máx. (capital)", "Tasa de acierto",
                                                              "Profit factor", "Operaciones   "))]
            print("\n".join("  " + l.strip() for l in keep))
            print()
    html = os.path.abspath(os.path.join("reports", "US500_DAY_report.html"))
    if os.path.exists(html):
        print("  Abriendo el informe completo (curva de capital, todas las operaciones) en el navegador ...")
        webbrowser.open("file:///" + html.replace("\\", "/"))
    pause()

    print("\n" + "=" * 74)
    print("  Fin de la demostración. Los backtests son simulaciones; los resultados pasados")
    print("  no garantizan resultados futuros.")
    print("=" * 74)


if __name__ == "__main__":
    main()
