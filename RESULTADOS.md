# Resultados de backtest – estrategia *mean_reversion*

Generado el 2026-10-22 con `run_backtest.py` y `portfolio_report.py`. Datos diarios de
Yahoo Finance (índices al contado: ^GSPC, ^NDX, ^DJI, ^GDAXI, ^FTSE). Capital inicial
10 000 por mercado, exposición 100 % del saldo por posición, spread (0,6 / 1,5 / 2,0 /
1,2 / 1,0 puntos) y financiación nocturna 0,02 % del nominal por día natural.

Reglas: largo cuando cierre > SMA200 y RSI(2) < 10; salida cuando cierre > SMA5, tras 10
velas o en el stop de 3 × ATR14. Límite diario 5 %, kill switch 25 % de drawdown.

## Periodo completo 2000-01 → 2026-10 (26,8 años)

| Mercado | Neto | CAGR | DD máx. | Ops. | Ops./año | Aciertos | PF | Rachas perdedoras máx. | Velas medias |
|---|---|---|---|---|---|---|---|---|---|
| US500 (S&P 500) | +31,2 % | 1,02 % | 19,4 % | 209 | 7,8 | 66,0 % | 1,24 | 3 | 2,9 |
| US100 (Nasdaq 100) | +84,7 % | 2,32 % | 14,7 % | 216 | 8,1 | 64,8 % | 1,48 | 6 | 3,3 |
| US30 (Dow Jones) | +41,8 % | 1,31 % | 15,2 % | 231 | 8,6 | 69,3 % | 1,29 | 7 | 3,0 |
| UK100 (FTSE 100) | +12,1 % | 0,43 % | 23,5 % | 186 | 6,9 | 66,1 % | 1,11 | 6 | 3,1 |
| DE40 (DAX) | −17,5 % | −0,72 % | 26,7 % | 142 | 5,3 | 59,2 % | 0,86 | 5 | 3,6 |

DE40 y UK100 **no** se incluyen en la configuración por defecto.

### Cartera US500 + US100 + US30 (pesos iguales)

- Neto +52,6 %, CAGR 1,59 %, **drawdown máximo 12,3 %**
- Mejor año +9,6 % (2003), peor año −4,2 % (2022), 17 de 27 años positivos

| Año | Ret. | Año | Ret. | Año | Ret. |
|---|---|---|---|---|---|
| 2001 | +0,3 % | 2010 | −1,9 % | 2019 | +0,7 % |
| 2002 | −1,0 % | 2011 | −2,9 % | 2020 | −1,1 % |
| 2003 | +9,6 % | 2012 | +3,2 % | 2021 | +6,1 % |
| 2004 | +0,6 % | 2013 | +2,5 % | 2022 | −4,2 % |
| 2005 | −0,2 % | 2014 | +0,1 % | 2023 | +2,3 % |
| 2006 | +2,7 % | 2015 | +2,8 % | 2024 | +9,0 % |
| 2007 | +0,4 % | 2016 | −1,6 % | 2025 | +4,6 % |
| 2008 | −1,9 % | 2017 | +4,5 % | 2026 (hasta oct.) | +3,3 % |
| 2009 | +8,7 % | 2018 | −2,3 % | | |

## Periodo reciente oct-2023 → oct-2026 (3 años operando; los primeros 200 días son calentamiento de la SMA200)

| Mercado | Neto | CAGR | DD máx. | Ops. | Aciertos | PF |
|---|---|---|---|---|---|---|
| US500 | +14,6 % | 3,65 % | 5,1 % | 35 | 77,1 % | 2,14 |
| US100 | +23,9 % | 5,80 % | 6,6 % | 35 | 74,3 % | 2,69 |
| US30 | +9,8 % | 2,48 % | 5,4 % | 34 | 67,6 % | 1,63 |
| **Cartera** | **+16,1 %** | **4,0 %** | **3,5 %** | 104 | — | — |

Cartera por año: 2024 +8,7 %, 2025 +4,7 %, 2026 (hasta octubre) +3,2 %.

## Robustez

Escaneo de 216 combinaciones (RSI de entrada 5-20, SMA de tendencia 100-200, SMA de
salida 3-10, stop 2-4 ATR, 5-10 velas) sobre US500 2000-2026: **215 de 216 rentables**
(`reports/US500_DAY_scan.csv`). Los parámetros por defecto están en el centro de la
distribución, no en el máximo.

Mismos parámetros en 5 índices distintos y 4 sub-periodos (2000-07, 2008-12, 2013-19,
2020-26): positivo en todos los sub-periodos para US500 y US100.

## Lo que esto significa

- Edge real, documentado y estable: ~70 % de aciertos, drawdown contenido, 8-9 operaciones
  al año por índice, 3 días de media en mercado.
- Rendimiento **modesto** a exposición 1× (1-2,3 % anual neto en 26 años; 2,5-5,8 % anual
  en los últimos 3). Escala linealmente con `exposure_pct` (y también el drawdown).
- Costes importan: la financiación nocturna se come ~40 % del beneficio bruto. Si Capital.com
  cobra más del 0,02 %/día, el resultado baja.
- Los datos son de índices al contado; los CFD siguen el futuro y el horario de la vela
  diaria de Capital.com difiere de Yahoo. La validación final es la **cuenta DEMO**.

## Estrategia alternativa *ema_trend* (no recomendada)

Backtest 2024-2026, H1, riesgo 1 % por operación: EURUSD −10,1 % (PF 0,84, DD 15,8 %),
oro +10,0 % (PF 1,14, DD 11,1 %). Sin consistencia entre mercados; se entrega solo como
opción configurable.

## Archivos

- `reports/<MERCADO>_DAY_report.html` – informe completo con curva de capital, P&L anual y
  mensual y todas las operaciones (`_2024` para el periodo reciente).
- `reports/<MERCADO>_DAY_trades.csv` – operaciones (entrada, salida, tamaño, stop, P&L, motivo).
- `reports/<MERCADO>_DAY_equity.csv` – curva de capital diaria.
- `reports/portfolio_equity_2000.csv`, `reports/portfolio_equity_2024.csv` – cartera.
- `reports/US500_DAY_scan.csv` – escaneo de parámetros.
