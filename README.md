# Capital.com Trading Bot (API REST, sin MetaTrader)

Bot de trading automático para **Capital.com** conectado exclusivamente por su API oficial
(cuenta DEMO o REAL). Incluye estrategia, gestión de riesgo, backtester con reportes,
historial de operaciones y ejecutables para Windows.

> **Aviso:** ninguna estrategia garantiza rentabilidad. Los resultados de backtest o de cuenta
> demo no aseguran resultados futuros. Use primero la cuenta DEMO.

---

## 1. Qué incluye

| Archivo | Función |
|---|---|
| `start_bot.bat` / `CapitalBot.exe` | Arranca el bot (lee `config.json`) |
| `check_connection.py` / `CapitalCheck.exe` | Verifica API key, saldo, mercados y velas |
| `run_backtest.py` / `CapitalBacktest.exe` | Backtest con reporte TXT/HTML, lista de operaciones y curva de capital |
| `export_history.py` / `CapitalHistory.exe` | Exporta el historial real de la cuenta (operaciones cerradas, P&L) a CSV |
| `order_test.py` / `CapitalOrderTest.exe` | Prueba de ejecución en DEMO: abre y cierra una posición de tamaño mínimo con stop (no es una señal de la estrategia) |
| `portfolio_report.py` | Combina varios backtests en una cartera |
| `config.example.json` | Configuración por defecto (estrategia *mean_reversion*, índices, diario) |
| `config.ema_trend.example.json` | Configuración alternativa (estrategia *ema_trend*, H4) |
| `capital_bot/` | Código fuente (API, estrategias, riesgo, backtester, bot) |
| `tests/` | Tests unitarios (`python -m unittest discover -s tests`) |
| `reports/` | Reportes de backtest generados |
| `logs/` | `bot.log`, `trades.csv` (diario de operaciones), `state.json` |

## 2. Instalación en Windows

**Opción A – ejecutables (sin Python):** copie la carpeta `dist/` a cualquier sitio, copie
`config.example.json` como `config.json`, edítelo y ejecute `start_bot.bat`.

**Opción B – desde código fuente:**
1. Instale Python 3.10+ desde <https://www.python.org/downloads/> marcando *Add Python to PATH*.
2. Ejecute `install.bat` (instala dependencias y crea `config.json`).
3. Edite `config.json` y ejecute `start_bot.bat`.

## 3. Crear la API key en Capital.com

1. Entre en Capital.com → **Configuración → Integraciones API** (API integrations).
2. Active la autenticación en dos pasos (2FA) si no está activa; es obligatoria para generar claves.
3. Pulse **Generar nueva clave**, ponga un nombre y una **contraseña personalizada** para la clave.
4. Copie la clave (solo se muestra una vez).
5. En `config.json`:
   - `api_key`: la clave generada
   - `identifier`: su e-mail de acceso a Capital.com
   - `password`: la contraseña personalizada de la API key (no la de la cuenta)
   - `demo`: `true` para cuenta demo, `false` para cuenta real

Las credenciales también se pueden pasar por variables de entorno
`CAPITAL_API_KEY`, `CAPITAL_IDENTIFIER`, `CAPITAL_PASSWORD`, `CAPITAL_DEMO`.

Compruebe la conexión:
```
python check_connection.py            (o CapitalCheck.exe)
python check_connection.py --search "US 500"      busca el epic exacto de un mercado
```

## 4. Estrategia incluida: *mean_reversion* (reversión a la media en índices)

Opera **índices bursátiles** (US500, US100, US30) en **velas diarias**, solo en largo:

- **Entrada:** cierre por encima de la media de 200 días (tendencia alcista) y RSI(2) < 10
  (retroceso fuerte de corto plazo). Se compra en la apertura de la siguiente vela.
- **Salida:** cuando el cierre supera la media de 5 días, o tras 10 velas, o si toca el
  stop de protección (3 × ATR14).
- **Tamaño:** valor de la posición = `exposure_pct` % del saldo (por defecto 100 %).
- **Protecciones:** límite de pérdida diaria (5 %), kill switch por drawdown (25 %),
  máximo de posiciones abiertas (3), una posición por símbolo, cada vela se evalúa
  una sola vez (reinicios seguros).

Es una regla clásica y documentada (reversión a la media de corto plazo en índices
de EE. UU.) y **no está optimizada a la curva**: en el escaneo de 216 combinaciones de
parámetros sobre el US500 (2000-2026), 215 son rentables (`reports/US500_DAY_scan.csv`).

### Resultados de backtest (10 000 de capital, spread + financiación nocturna 0,02 %/día)

| Mercado | Periodo | Neto | CAGR | Drawdown máx. | Operaciones | Aciertos | Profit factor |
|---|---|---|---|---|---|---|---|
| US500  | 2000-2026 (26,8 años) | +31,2 % | 1,0 % | 19,4 % | 209 | 66 % | 1,24 |
| US100  | 2000-2026 | +84,7 % | 2,3 % | 14,7 % | 216 | 65 % | 1,48 |
| US30   | 2000-2026 | +41,8 % | 1,3 % | 15,2 % | 231 | 69 % | 1,29 |
| US500  | oct-2023 → oct-2026 (3 años) | +14,6 % | 3,7 % | 5,1 % | 35 | 77 % | 2,14 |
| US100  | oct-2023 → oct-2026 | +23,9 % | 5,8 % | 6,6 % | 35 | 74 % | 2,69 |
| US30   | oct-2023 → oct-2026 | +9,8 % | 2,5 % | 5,4 % | 34 | 68 % | 1,63 |

Detalles, curva de capital, P&L por año/mes y lista completa de operaciones en
`reports/<símbolo>_DAY_report.html` y `reports/<símbolo>_DAY_2024_report.html`.
Resumen completo en `RESULTADOS.md`.

Lectura honesta: es una estrategia de **baja frecuencia (8-9 operaciones al año por índice),
alta tasa de acierto y drawdown moderado**, con rendimiento modesto a exposición 1×. El
rendimiento escala con `exposure_pct` (p. ej. 200 % ≈ el doble de beneficio y de drawdown).
Los últimos 3 años han sido mejores que la media histórica; no hay que extrapolarlos.

### Estrategia alternativa: *ema_trend* (seguimiento de tendencia)

Cruce de EMA 20/50 con filtro EMA 200 y RSI, stop 2 × ATR y objetivo 3 × ATR
(`config.ema_trend.example.json`). En los backtests de 2024-2026 en H1 **no es
consistentemente rentable** en forex (EURUSD −10 %, PF 0,84) y es marginal en oro (+10 %,
PF 1,14). Se entrega como opción configurable, no como estrategia recomendada.

## 5. Backtest

```
python run_backtest.py --yahoo ^GSPC --symbol US500 --timeframe DAY --spread 0.6 --fee 0.02
python run_backtest.py --csv data/US500_DAY.csv --symbol US500 --timeframe DAY --spread 0.6 --fee 0.02 --from 2023-01-01 --tag 2024
python run_backtest.py --csv data/US500_DAY.csv --symbol US500 --timeframe DAY --spread 0.6 --fee 0.02 --scan
python portfolio_report.py reports/US500_DAY_equity.csv reports/NAS100_DAY_equity.csv reports/US30_DAY_equity.csv
```
Tickers de Yahoo: `^GSPC` US500, `^NDX` US100, `^DJI` US30, `^GDAXI` DE40, `^FTSE` UK100,
`EURUSD=X`, `GC=F` oro. Las velas también pueden venir de un CSV propio
(`time,open,high,low,close[,volume]`, UTC).

Modelo de ejecución (conservador): señales al cierre de vela, ejecución en la apertura
siguiente, spread pagado en entrada y salida, stop/objetivo comprobados con máximo/mínimo
de cada vela (si ambos se tocan en la misma vela se asume el stop), financiación nocturna
por día natural.

## 6. Funcionamiento en vivo

Cada `poll_seconds` el bot: mantiene viva la sesión (expira a los 10 min de inactividad),
lee el saldo, aplica límites de pérdida diaria y drawdown, sincroniza operaciones cerradas
y, **solo cuando cierra una vela nueva**, evalúa la estrategia por símbolo y envía órdenes
con stop (y objetivo si aplica) directamente a Capital.com.

- `logs/bot.log` – registro completo.
- `logs/trades.csv` – diario: aperturas, cierres, P&L, rechazos, paradas.
- `logs/state.json` – última vela evaluada, saldo inicial del día, pico de saldo, deals abiertos.
- `python export_history.py --days 30` – historial oficial de la cuenta en CSV.

**Pasar a cuenta real:** ponga `"demo": false` (o arranque con `--live`); el bot pide
confirmación escribiendo `YES`. Recomendado: mínimo 4 semanas en DEMO antes.

## 7. Parámetros de `config.json`

| Clave | Significado |
|---|---|
| `symbols` | Epics de Capital.com (verifique con `check_connection.py --search`) |
| `timeframe` | `MINUTE`, `MINUTE_5`, `MINUTE_15`, `MINUTE_30`, `HOUR`, `HOUR_4`, `DAY`, `WEEK` |
| `strategy.type` | `mean_reversion` o `ema_trend` |
| `strategy.atr_sl` / `atr_tp` | Stop y objetivo en múltiplos de ATR (`atr_tp` 0 = sin objetivo) |
| `strategy.max_hold_bars` | Cierre forzado tras N velas (0 = sin límite) |
| `strategy.allow_long` / `allow_short` | Permitir largos / cortos |
| `strategy.trade_hours_utc` | Ventana horaria [inicio, fin) en UTC para abrir operaciones |
| `risk.size_mode` | `notional` (% del saldo), `risk` (% arriesgado hasta el stop) o `fixed` |
| `risk.exposure_pct` | Valor de la posición como % del saldo (modo notional) |
| `risk.risk_per_trade_pct` | % del saldo que se pierde si salta el stop (modo risk) |
| `risk.daily_loss_limit_pct` | Deja de abrir operaciones el resto del día |
| `risk.max_drawdown_pct` | Kill switch desde el pico de saldo |
| `risk.max_open_positions` | Máximo de posiciones simultáneas |
| `risk.value_per_point` | Opcional: `{ "EPIC": valor }` P&L por unidad de tamaño y punto |

El tamaño se redondea al mínimo e incremento del mercado y el stop se ajusta a la distancia
mínima que exige Capital.com. **Compruebe en DEMO que el tamaño calculado es el esperado**
para cada mercado antes de operar en real.

## 8. Compilar los ejecutables

```
build_exe.bat
```
Genera `dist/CapitalBot.exe`, `CapitalBacktest.exe`, `CapitalCheck.exe`, `CapitalHistory.exe`.

## 9. Tests

```
python -m unittest discover -s tests -v
```
