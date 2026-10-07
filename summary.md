# Results summary

Bars: 1h | formation 180 d, trading 30 d | 53 walk-forward windows
Sample: 2021-11-08 to 2026-10-07 | 38 coins
FDR q=0.05 | entry/exit/stop = 2.0/0.0/4.0 | cost = 0.10% per unit traded | half-life 0.25-10.0 d


## Table 1. Stationarity of log prices (Obj. 2)

|  | Value |
|---|---|
| Coins tested per window | 38.0 |
| Levels non-stationary (ADF) | 93.0% |
| Differences stationary (ADF) | 100.0% |
| Differences stationary (KPSS) | 96.7% |
| Classified I(1) (all three) | 90.1% |


## Table 2. Cointegration funnel (Obj. 3)

|  | Total | Per window |
|---|---|---|
| Pairs tested (EG, both orderings) | 15,205 | 286.9 |
| Raw p < 5% | 1,550 | 29.2 |
| Pass BH (q=5%) | 225 | 4.2 |
| Pass BY (q=5%) | 51 | 1.0 |
| Johansen rank 0 (of BH) | 0 | 0.0 |
| Johansen rank 1 (of BH) | 187 | 3.5 |
| Johansen rank 2 (of BH) | 38 | 0.7 |
| Rank 1 and correct EC sign | 187 | 3.5 |
| Half-life in range | 187 | 3.5 |
| Selected for trading | 130 | 2.5 |


## Table 3. Mean reversion of spreads (Obj. 4)

|  | Value |
|---|---|
| Pairs with rank 1 and correct sign | 187 |
| Median half-life (days) | 2.70 |
| Half-life IQR (days) | 2.23 - 3.27 |
| Share within filter | 100.0% |
| Median hedge ratio | 0.78 |


## Table 4. Trading activity (Obj. 5)

|  | Trades | Hit rate | Mean net trade (bps) | Mean holding (days) |
|---|---|---|---|---|
| Cointegration (static hedge) | 310 | 21.6% | -42.6 | 3.46 |
| Cointegration (rolling hedge) | 327 | 42.2% | -50.1 | 5.10 |
| Distance method | 2,820 | 25.0% | -18.2 | 5.02 |


## Table 5. Out-of-sample performance, net of costs (Obj. 6)

|  | Total ret | CAGR | Ann. ret (mean x 365) | Ann. vol | Sharpe | Max DD | VaR 5% | CVaR 5% |
|---|---|---|---|---|---|---|---|---|
| Cointegration (static) | -34.8% | -9.3% | -9.0% | 12.6% | -0.71 | -42.6% | 0.6% | 1.7% |
| Cointegration (rolling) | -36.6% | -9.9% | -8.8% | 18.0% | -0.49 | -43.9% | 0.8% | 2.4% |
| Distance method | -23.7% | -6.0% | -5.9% | 7.5% | -0.79 | -30.4% | 0.6% | 1.0% |
| Buy-and-hold (EW) | -1.9% | -0.4% | 24.5% | 70.2% | 0.35 | -72.8% | 5.7% | 8.8% |
| Buy-and-hold (BTC) | 115.4% | 19.2% | 29.9% | 49.5% | 0.60 | -55.5% | 3.7% | 5.9% |


## Table 6. Stability of selected pairs (Obj. 7)

|  | Value |
|---|---|
| Persistence, mean (pairs re-qualifying next window) | 12.4% |
| Persistence, median | 0.0% |
| Median hedge-ratio change between windows | 3.0% |
| Pairs selected per window | 2.5 |
| Windows with no pair | 30 of 53 |
| Sharpe, static hedge (net) | -0.71 (gross -0.48) |
| Sharpe, rolling hedge (net) | -0.49 |


## Table 7. How trades end (Obj. 5)

|  | Exit at mean (share) | Exit at mean (net bps) | Stop-out (share) | Stop-out (net bps) | Window-end close (share) | Window-end close (net bps) |
|---|---|---|---|---|---|---|
| Cointegration (static hedge) | 12.6% | 678.8 | 72.6% | -193.7 | 14.8% | 84.8 |
| Cointegration (rolling hedge) | 34.6% | 430.5 | 44.3% | -393.7 | 21.1% | -115.1 |
| Distance method | 12.1% | 831.2 | 68.9% | -214.2 | 19.0% | 149.8 |


## Table 8. Out-of-sample behaviour of selected pairs (Obj. 7)

|  | Value |
|---|---|
| Pair-windows selected | 130 |
| Still cointegrated in trading window (EG p<5%) | 19.2% |
| Median EG p-value in trading window | 0.252 |
| Median spread SD ratio (trading / formation) | 0.71 |
| Median |spread level shift| (formation SDs) | 0.93 |
| Pairs that hit the stop level | 28.5% |
| Median trades per pair-window | 1 |
| Pairs with positive net P&L | 36.9% |
| Mean net P&L per pair-window | -1.02% |
|   ... if still cointegrated | 3.65% |
|   ... if no longer cointegrated | -2.13% |


## Table 9. Pairs selected in the most windows

|  | Windows | Median half-life (d) | Share still coint. | Mean net P&L |
|---|---|---|---|---|
| DOGE / LTC | 4 | 2.65 | 0% | 4.44% |
| AXS / XRP | 3 | 2.39 | 0% | -6.23% |
| GTC / NMR | 3 | 1.92 | 100% | 2.85% |
| AVAX / SHIB | 3 | 2.03 | 33% | 0.00% |
| AXS / HBAR | 3 | 2.64 | 0% | 0.70% |
| AXS / FIL | 3 | 2.0 | 0% | 2.22% |
| DOT / TRB | 2 | 1.75 | 0% | -11.70% |
| DOT / SHIB | 2 | 2.01 | 0% | -4.55% |
| DOT / NEAR | 2 | 2.35 | 0% | 0.75% |
| DOGE / INJ | 2 | 1.85 | 50% | 2.19% |
| AXS / NEAR | 2 | 1.84 | 50% | 2.67% |
| DOT / NMR | 2 | 2.11 | 100% | 5.46% |
| NEAR / SHIB | 2 | 2.11 | 0% | -2.01% |
| ADA / NEAR | 2 | 2.73 | 50% | 5.67% |
| AR / TRB | 2 | 2.73 | 0% | 0.00% |


## Worst five windows for the static strategy

| window | trade_start | n_selected | static | rolling |
|---|---|---|---|---|
| 1 | 2022-06-06 | 4 | -18.0% | 3.8% |
| 0 | 2022-05-07 | 4 | -10.8% | -2.1% |
| 22 | 2024-02-26 | 2 | -9.6% | -13.0% |
| 26 | 2024-06-25 | 6 | -7.0% | -13.0% |
| 13 | 2023-06-01 | 4 | -7.0% | -28.1% |
