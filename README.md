# Cointegration Pairs Trading in Cryptocurrency Markets

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Code and results for the paper **"Does Cointegration Pairs Trading Survive Multiple Testing? Out-of-Sample Evidence from Cryptocurrency Markets"** (Ayan Bashir Sheikh, M.Sc. Statistics, Savitribai Phule Pune University).

The pipeline collects 1-minute Binance spot data, aggregates it to bars, screens coin pairs for cointegration with false discovery rate control, and evaluates a pairs trading strategy out of sample with a walk-forward design, after transaction costs.

> **Status:** the results in `results_1h/` come from a **pilot run** (38 coins, 1-hour bars, 53 walk-forward windows). They will be replaced by the final run before the paper is submitted.

## Method in brief

1. **Universe:** top 100 USDT spot pairs by 24-hour volume, excluding stablecoins, wrapped and liquid-staking tokens, leveraged tokens, and gold-linked assets.
2. **Walk-forward design:** 180-day formation window, 30-day trading window, rolled forward without overlap.
3. **Stationarity:** ADF and KPSS tests classify each log price as I(1).
4. **Pair selection:** correlation screen, Engle-Granger test in both orderings, Benjamini-Hochberg false discovery control (Benjamini-Yekutieli as a robustness check).
5. **Confirmation:** Johansen test (rank 1), VECM, positive hedge ratio, correct error-correction sign, half-life filter.
6. **Trading:** entry at 2, exit at 0 and stop at 4 standard deviations of the standardised spread, with a cost of 0.10% per unit traded.
7. **Evaluation:** static and rolling hedge ratios against a distance-method baseline and buy-and-hold (equal-weighted and BTC). Reported with Sharpe ratio, maximum drawdown, VaR and CVaR.

## Repository structure

```
.
├── collect_minute_data.py   # download 1-minute klines (Binance archive + REST API)
├── analysis_minute.py       # walk-forward analysis, tables, CSVs, equity plot
├── plot_pairs.py            # pair plots, persistence chart, selection timeline
├── requirements.txt
├── results_1h/              # output of the analysis for 1-hour bars
│   ├── summary.md           # all tables in Markdown
│   ├── table1.csv ... table9.csv
│   ├── selected_pairs.csv   # every selected pair-window with out-of-sample diagnostics
│   ├── pairs_summary.csv    # one row per pair
│   ├── window_returns.csv   # return of each strategy in each window
│   ├── daily_returns.csv, funnel_by_window.csv, alignment_tradeoff.csv, coin_counts.csv
│   ├── equity_curves.png, half_life_hist.png
│   └── plots/               # figures made by plot_pairs.py
├── paper/                   # LaTeX source (main.tex, figures/)
├── LICENSE
└── README.md
```

## Installation

Python 3.10 or newer (developed with Python 3.13).

```bash
git clone https://github.com/Ayansheikh034/crypto-pairs-trading.git
cd crypto-pairs-trading
pip install -r requirements.txt
```

## Usage

**1. Collect the data** (large; the raw data are not stored in this repository):

```bash
python collect_minute_data.py --top 100 --since 2019-01 --out data
```

The collector is resumable. Completed months are skipped and the current month is topped up, so `--update` refreshes an existing dataset using the saved `universe.csv`.

**2. Run a quick test** (a few minutes) to check that the statistical code runs:

```bash
python analysis_minute.py --data data --freq 1h --max_windows 2 --jobs 2 --out test_run
```

**3. Run the full analysis:**

```bash
python analysis_minute.py --data data --freq 1h --out results_1h
```

Add `--refresh` after new data have been collected, to rebuild the price cache.

**4. Make the plots:**

```bash
python plot_pairs.py --data data --results results_1h --freq 1h --top 6
python plot_pairs.py --data data --results results_1h --freq 1h --pairs "NMR/GTC,SHIB/AVAX,AXS/HBAR"
```

On Windows PowerShell, use paths such as `--data "E:\pair trading\data"`.

## Main options of `analysis_minute.py`

| Option | Default | Meaning |
|---|---|---|
| `--freq` | `1h` | Bar size (`5min`, `15min`, `1h`, ...) |
| `--L_days` / `--M_days` | 180 / 30 | Formation and trading window (days) |
| `--rho_min` | 0.6 | Correlation screen |
| `--q` | 0.05 | False discovery rate |
| `--h_min` / `--h_max` | 0.25 / 10 | Half-life range (days) |
| `--entry` / `--exit` / `--stop` | 2 / 0 / 4 | Trading thresholds (standard deviations) |
| `--cost` | 0.001 | Cost per unit traded |
| `--max_pairs` | 20 | Maximum pairs traded per window |
| `--roll_days` | 60 | Rolling hedge-ratio window (days) |
| `--start` | auto | Force the common start date (`YYYY-MM-DD`) |
| `--jobs` | CPUs - 1 | Parallel workers |

Run `python analysis_minute.py --help` for the full list.

## Data

The raw data come from the public Binance archive (<https://data.binance.vision>) and the Binance REST API, and `collect_minute_data.py` rebuilds them. The coin universe is chosen by 24-hour volume on the collection date, so a later run can give a different universe. The file `data/universe.csv` created by the collector records the universe that was used.

## Reproducibility

The analysis uses no random numbers and is deterministic for a given dataset and settings. To record the exact package versions of a run:

```bash
pip freeze > requirements-lock.txt
```

## Limitations

The universe is ranked on a single recent date, so it is subject to survivorship bias. The data are spot prices only, so borrowing and funding costs are not modelled separately. Prices are last-trade prices from one venue. See Section 6 of the paper for details.

## Citation

If you use this code, please cite the paper. A DOI will be added after the first release.

```bibtex
@misc{sheikh2026pairs,
  author = {Sheikh, Ayan Bashir},
  title  = {Does Cointegration Pairs Trading Survive Multiple Testing? Out-of-Sample Evidence from Cryptocurrency Markets},
  year   = {2026},
  url    = {https://github.com/Ayansheikh034/crypto-pairs-trading}
}
```

## License

This project is released under the [MIT License](LICENSE).

## Disclaimer

This code is for research and education. It is not investment advice, and past results do not predict future returns.
