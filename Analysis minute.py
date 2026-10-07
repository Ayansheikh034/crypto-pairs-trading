#!/usr/bin/env python3
"""
Cointegration pairs trading on the 1-minute Binance dataset (resampled to bars).

Reads   : <data>/1m/<SYMBOL>/<YYYY-MM>.parquet   (from the collector)
Pipeline: Section 4 of the paper
  4.1 walk-forward           -> Obj. 6      4.2 I(1) tests            -> Obj. 2
  4.3 corr screen + EG       -> Obj. 3      4.4 BH / BY FDR control   -> Obj. 3
  4.5 Johansen + VECM        -> Obj. 3      4.6 half-life / z-score   -> Obj. 4
  4.7 trading rules + costs  -> Obj. 5      4.8 performance and risk  -> Obj. 6
  4.9 persistence, static vs rolling hedge, baselines -> Obj. 7
Writes  : <out>/summary.md, results_tables.tex, one CSV per table, equity plot,
          selected_pairs.csv (WHICH coins, with out-of-sample diagnostics),
          pairs_summary.csv (one row per pair), window_returns.csv.

Quick test : python analysis_minute.py --data data --freq 1h --max_windows 2
Full run   : python analysis_minute.py --data data --freq 1h --out results_1h
After the collector finishes: add --refresh to rebuild the price cache.

Changes in this version (see the chat for details)
  * FIX  per-trade net return now includes the exit cost (Table 4 was too optimistic).
  * FIX  equal-weight buy-and-hold is rebalanced at the start of each trading window
         (as in the paper), not every bar.
  * FIX  rolling hedge: mean/sd are those of the spread with the CURRENT hedge ratio.
  * FIX  price cache is rebuilt when minute files are newer (or with --refresh).
  * NEW  names of selected pairs, out-of-sample cointegration re-test per pair,
         trade exit reasons, per-window returns, CAGR column.
"""
import argparse
import os
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.stats.multitest import multipletests
from statsmodels.tsa.stattools import adfuller, kpss
try:                                   # statsmodels <= 0.14
    from statsmodels.tsa.stattools import mackinnonp
except ImportError:                    # newer versions moved it
    from statsmodels.tsa.adfvalues import mackinnonp
from statsmodels.tsa.vector_ar.vecm import VECM, coint_johansen

warnings.filterwarnings("ignore")


# =============================================================== data loading
def load_close(root, freq, exclude, refresh=False):
    """Close prices resampled to `freq`; cached on disk, rebuilt if data are newer."""
    root = Path(root)
    cache = root / f"close_{freq}.parquet"
    newest = max((f.stat().st_mtime for f in (root / "1m").glob("*/*.parquet")), default=0)
    if cache.exists() and not refresh and cache.stat().st_mtime >= newest:
        panel = pd.read_parquet(cache)
    else:
        if cache.exists():
            print("rebuilding price cache (new minute files or --refresh)")
        cols = {}
        syms = sorted(p.name for p in (root / "1m").iterdir() if p.is_dir())
        for i, sym in enumerate(syms, 1):
            parts = [pd.read_parquet(f, columns=["close"])["close"].resample(freq).last()
                     for f in sorted((root / "1m" / sym).glob("*.parquet"))]
            if parts:
                s = pd.concat(parts)
                cols[sym] = s[~s.index.duplicated(keep="last")]
            print(f"  loaded {i}/{len(syms)} {sym}", end="\r")
        panel = pd.DataFrame(cols).sort_index().iloc[:-1]   # drop last (partial) bar
        panel.to_parquet(cache)
        print()
    drop = [c for c in exclude if c in panel.columns]
    if drop:
        print("excluded:", drop)
    return panel.drop(columns=drop).where(lambda d: d > 0)


def align_universe(P, L, M, min_windows, min_coins, end_tol_bars, max_missing, start=None):
    """Common sample: start date maximising coins x bars (see paper, Data section)."""
    first = P.apply(lambda c: c.first_valid_index())
    last = P.apply(lambda c: c.last_valid_index())
    end = P.index[-1]
    step = P.index[1] - P.index[0]
    alive = (last >= end - end_tol_bars * step) & first.notna()
    cands = [pd.Timestamp(start, tz="UTC")] if start else sorted(first[alive].unique())
    best, rows = None, []
    for s in cands:
        sub = P.loc[s:]
        if len(sub) < L + M * min_windows:
            continue
        ok = alive & (first <= s)
        cols = [c for c in P.columns[ok.values] if sub[c].isna().mean() <= max_missing]
        rows.append({"start": s, "coins": len(cols), "bars": len(sub)})
        score = len(cols) * len(sub)
        if len(cols) >= min_coins and (best is None or score > best[0]):
            best = (score, s, cols)
    if best is None:
        raise SystemExit("No common start satisfies min_coins / min_windows.")
    return best[1], best[2], pd.DataFrame(rows)


# ============================================================ statistical tests
def i1_tests(x, alpha, maxlag):
    """(level non-stationary, diff stationary by ADF, diff stationary by KPSS)."""
    try:
        p_lvl = adfuller(x, maxlag=maxlag, autolag="AIC")[1]
        d = np.diff(x)
        p_d = adfuller(d, maxlag=maxlag, autolag="AIC")[1]
        p_k = kpss(d, regression="c", nlags="auto")[1]
    except Exception:
        return False, False, False
    return p_lvl > alpha, p_d < alpha, p_k > alpha


def eg_pvalue(y, x, lags):
    X = np.column_stack([np.ones_like(x), x])
    coef = np.linalg.lstsq(X, y, rcond=None)[0]
    stat = adfuller(y - X @ coef, maxlag=lags, autolag=None, regression="n")[0]
    return float(mackinnonp(stat, regression="c", N=2)), coef


def eg_both(a, b, lags):
    """Both orderings; doubled smaller p-value. Returns (p, ordering)."""
    p1, _ = eg_pvalue(a, b, lags)
    p2, _ = eg_pvalue(b, a, lags)
    return (min(1.0, 2 * p1), 0) if p1 <= p2 else (min(1.0, 2 * p2), 1)


def johansen(Y, lags):
    """Returns (rank, hedge ratio b, speed adj, ok). spread = y - b x."""
    try:
        j = coint_johansen(Y, det_order=0, k_ar_diff=lags)
        rank = 0
        for k in range(2):
            if j.lr1[k] > j.cvt[k, 1]:      # 5% critical value
                rank += 1
            else:
                break
        if rank != 1:
            return rank, np.nan, np.nan, False
        res = VECM(Y, k_ar_diff=lags, coint_rank=1, deterministic="ci").fit()
        alpha, beta = res.alpha[:, 0], res.beta[:, 0]
        b = -beta[1] / beta[0]
        adj = (alpha[0] - b * alpha[1]) * beta[0]
        return 1, float(b), float(adj), bool(b > 0 and adj < 0)
    except Exception:
        return -1, np.nan, np.nan, False


def half_life(s):
    y, x = s[1:], s[:-1]
    phi = np.linalg.lstsq(np.column_stack([np.ones_like(x), x]), y, rcond=None)[0][1]
    return -np.log(2) / np.log(phi) if 0 < phi < 1 else np.inf


# ================================================================ trading engine
def run_pair(ly, lx, b, mu, sd, entry, exit_, stop, cost):
    """Signal at bar k close -> position earns bar k+1 (no look-ahead).
    Returns gross, net bar returns and trades [(net return incl. exit cost, bars held,
    reason)], reason in {'exit' (mean crossing), 'stop', 'forced' (window end)}."""
    M = len(ly) - 1
    b = np.broadcast_to(np.asarray(b, float), (M + 1,))
    mu = np.broadcast_to(np.asarray(mu, float), (M + 1,))
    sd = np.broadcast_to(np.asarray(sd, float), (M + 1,))
    z = (ly - b * lx - mu) / sd
    sret = np.nan_to_num(np.diff(ly) - b[:-1] * np.diff(lx))
    norm = 1.0 + np.abs(np.nan_to_num(b[:-1], nan=1.0))
    pos, cur = np.zeros(M), 0
    why = [""] * M                      # why the position became flat at bar k
    for k in range(M):
        zk = z[k]
        if np.isnan(zk):
            cur = 0
        elif cur == 0:
            if entry <= zk < stop:
                cur = -1
            elif -stop < zk <= -entry:
                cur = 1
        elif cur == 1 and (zk >= -exit_ or zk <= -stop):
            why[k] = "stop" if zk <= -stop else "exit"
            cur = 0
        elif cur == -1 and (zk <= exit_ or zk >= stop):
            why[k] = "stop" if zk >= stop else "exit"
            cur = 0
        pos[k] = cur
    gross = pos * sret / norm
    prev = np.concatenate([[0.0], pos[:-1]])
    costs = cost * np.abs(pos - prev)
    costs[-1] += cost * abs(pos[-1])
    net = gross - costs
    trades, start = [], None
    for k in range(M):
        if pos[k] != 0 and (k == 0 or pos[k - 1] == 0):
            start = k
        if pos[k] != 0 and (k == M - 1 or pos[k + 1] != pos[k]):
            pnl = float(net[start:k + 1].sum())
            if k == M - 1:
                reason = "forced"       # closing cost is already inside net[M-1]
            else:
                reason = why[k + 1] or "exit"
                pnl += float(net[k + 1])        # exit cost is charged on the closing bar
            trades.append((pnl, k - start + 1, reason))
    return gross, net, trades


def rolling_params(ly, lx, R):
    """Rolling OLS hedge ratio b_t and the mean / sd of the spread y - b_t x over the
    same window, using the CURRENT b_t (closed form from rolling moments)."""
    mx, my = lx.rolling(R).mean(), ly.rolling(R).mean()
    vx, vy = lx.rolling(R).var(), ly.rolling(R).var()
    cxy = ly.rolling(R).cov(lx)
    b = cxy / vx
    mu = my - b * mx
    var = vy - 2 * b * cxy + b * b * vx
    return b.values, mu.values, np.sqrt(var.clip(lower=0)).values


def distance_pairs(norm, n):
    A = norm.T
    sq = (A ** 2).sum(1)
    ssd = sq[:, None] + sq[None, :] - 2 * A @ A.T
    iu = np.triu_indices(len(A), 1)
    order = np.argsort(ssd[iu])[:n]
    return list(zip(iu[0][order], iu[1][order]))


# ============================================================== one window (worker)
def process_window(task):
    p, cols, lp = task["p"], task["cols"], task["lp"]
    L, M, bpd = p["L"], p["M"], p["bpd"]
    lpf = pd.DataFrame(lp).ffill().values            # delisted coins stay flat
    form, trade = lp[:L], lpf[L - 1:L + M]
    elig = [j for j in range(len(cols)) if not np.isnan(form[:, j]).any()
            and (p["short"] is None or cols[j] in p["short"])]
    row = dict(n_elig=len(elig))

    # 4.2 stationarity
    t = [i1_tests(form[:, j], p["alpha"], p["maxlag"]) for j in elig]
    row["n_lvl"] = sum(a for a, _, _ in t)
    row["n_dadf"] = sum(b for _, b, _ in t)
    row["n_dkpss"] = sum(c for _, _, c in t)
    i1 = [j for j, (a, b, c) in zip(elig, t) if a and b and c]
    row["n_I1"] = len(i1)

    # 4.3 correlation screen + Engle-Granger
    cand = []
    if len(i1) >= 2:
        C = np.corrcoef(np.diff(form[:, i1], axis=0).T)
        iu = np.triu_indices(len(i1), 1)
        rho = C[iu]
        keep = np.where(rho >= p["rho_min"])[0]
        keep = keep[np.argsort(-rho[keep])][:p["max_candidates"]]
        cand = [(i1[iu[0][k]], i1[iu[1][k]]) for k in keep]
    row["n_cand"] = len(cand)
    tested = []
    for a, b in cand:
        pv, which = eg_both(form[:, a], form[:, b], p["eg_lags"])
        tested.append((a, b, pv) if which == 0 else (b, a, pv))
    row["n_eg"] = len(tested)

    # 4.4 FDR
    sel, hl_list = [], []
    row.update(n_raw05=0, n_bh=0, n_by=0, r0=0, r1=0, r2=0, n_vecm_ok=0, n_hl_ok=0)
    if tested:
        pv = np.array([x[2] for x in tested])
        bh = multipletests(pv, alpha=p["q"], method="fdr_bh")[0]
        by = multipletests(pv, alpha=p["q"], method="fdr_by")[0]
        row.update(n_raw05=int((pv < 0.05).sum()), n_bh=int(bh.sum()), n_by=int(by.sum()))
        # 4.5 Johansen + VECM, 4.6 half-life
        for k in np.where(bh)[0]:
            y, x, pe = tested[k]
            Y = form[:, [y, x]]
            rank, b, adj, ok = johansen(Y, p["vecm_lags"])
            if rank in (0, 1, 2):
                row[f"r{rank}"] += 1
            if not ok:
                continue
            row["n_vecm_ok"] += 1
            sp = Y[:, 0] - b * Y[:, 1]
            hl = half_life(sp) / bpd                 # in days
            hl_list.append((hl, adj, b))
            if not (p["h_min"] <= hl <= p["h_max"]):
                continue
            row["n_hl_ok"] += 1
            sel.append(dict(y=cols[y], x=cols[x], yi=y, xi=x, p_eg=pe, b=b, adj=adj,
                            half_life_days=hl, mu=sp.mean(), sd=sp.std(), pass_BY=bool(by[k])))
    if p["max_pairs"] and len(sel) > p["max_pairs"]:
        sel = sorted(sel, key=lambda d: d["p_eg"])[:p["max_pairs"]]
    row["n_sel"] = len(sel)

    # 4.7 trade the next window: static and rolling hedge ratio
    zero = np.zeros(M)
    g_s, n_s, n_r, tr_s, tr_r = [], [], [], [], []
    R = min(p["roll"], L // 2)
    for d in sel:
        ly, lx = trade[:, d["yi"]], trade[:, d["xi"]]
        g, n_, tr = run_pair(ly, lx, d["b"], d["mu"], d["sd"], p["entry"], p["exit"],
                             p["stop"], p["cost"])
        g_s.append(g); n_s.append(n_); tr_s += tr

        # out-of-sample diagnostics for this pair (formation parameters, trading data)
        d["n_trades"] = len(tr)
        d["pnl_static"] = float(n_.sum())
        try:
            d["p_oos"] = float(eg_pvalue(ly, lx, p["eg_lags"])[0])
        except Exception:
            d["p_oos"] = np.nan
        d["coint_oos"] = bool(d["p_oos"] < 0.05)          # NaN -> False
        sp_t = ly - d["b"] * lx
        d["oos_sd_ratio"] = float(np.std(sp_t) / d["sd"])
        d["oos_mean_shift"] = float((np.mean(sp_t) - d["mu"]) / d["sd"])   # level shift, formation SDs
        d["frac_beyond_stop"] = float(np.mean(np.abs((sp_t - d["mu"]) / d["sd"]) >= p["stop"]))
        try:
            d["half_life_oos_days"] = float(half_life(sp_t) / bpd)
        except Exception:
            d["half_life_oos_days"] = np.nan

        sy = pd.Series(lpf[L - 2 * R:L + M, d["yi"]])   # history for the rolling estimates
        sx = pd.Series(lpf[L - 2 * R:L + M, d["xi"]])
        bb, mm, ss = rolling_params(sy, sx, R)
        _, n2, tr2 = run_pair(ly, lx, bb[-(M + 1):], mm[-(M + 1):], ss[-(M + 1):],
                              p["entry"], p["exit"], p["stop"], p["cost"])
        n_r.append(n2); tr_r += tr2
    # baseline: distance method
    n_d, tr_d = [], []
    if len(elig) >= 2:
        norm = np.exp(form[:, elig] - form[0, elig])
        for i, j in distance_pairs(norm, max(p["max_pairs"], 20)):
            yj, xj = elig[i], elig[j]
            sp = form[:, yj] - form[:, xj]
            _, n_, tr = run_pair(trade[:, yj], trade[:, xj], 1.0, sp.mean(), sp.std(),
                                 p["entry"], p["exit"], p["stop"], p["cost"])
            n_d.append(n_); tr_d += tr
    # baseline: buy-and-hold, equal weights set at the START of the trading window
    if elig:
        value = np.exp(trade[:, elig] - trade[0, elig]).mean(axis=1)
        bh_ew = value[1:] / value[:-1] - 1
    else:
        bh_ew = zero
    btc = None
    if "BTCUSDT" in cols:
        btc = np.nan_to_num(np.expm1(np.diff(trade[:, cols.index("BTCUSDT")])))
    mean = lambda lst: np.mean(lst, 0) if lst else zero
    return dict(w=task["w"], times=task["times"], row=row, pairs=sel, hl=hl_list,
                ret=dict(static=mean(n_s), static_gross=mean(g_s), rolling=mean(n_r),
                         distance=mean(n_d), bh_ew=np.nan_to_num(bh_ew), btc=btc),
                trades=dict(static=tr_s, rolling=tr_r, distance=tr_d))


# ========================================================================= reporting
def perf(daily, name):
    r = pd.Series(daily).dropna()
    sd = r.std()
    eq = (1 + r).cumprod()
    q = np.percentile(r, 5) if len(r) else np.nan
    n = len(r)
    cagr = eq.iloc[-1] ** (365 / n) - 1 if n and eq.iloc[-1] > 0 else np.nan
    return dict(strategy=name, total=eq.iloc[-1] - 1, cagr=cagr, ann_ret=r.mean() * 365,
                ann_vol=sd * np.sqrt(365),
                sharpe=r.mean() / sd * np.sqrt(365) if sd > 0 else np.nan,
                mdd=(eq / eq.cummax() - 1).min(), var=-q, cvar=-r[r <= q].mean())


def to_daily(r):
    return (1 + r).groupby(r.index.floor("D")).prod() - 1


def pct(a, b):
    return f"{100 * a / b:.1f}%" if b else "n/a"


def esc(s):
    return str(s).replace("\\", "").replace("_", r"\_").replace("%", r"\%").replace("&", r"\&")


def md_table(df, first=""):
    head = "| " + " | ".join([first] + list(df.columns)) + " |"
    sep = "|" + "---|" * (df.shape[1] + 1)
    body = ["| " + " | ".join([str(i)] + [str(v) for v in row]) + " |"
            for i, row in zip(df.index, df.values)]
    return "\n".join([head, sep] + body)


def tex_table(df, caption, label, first=""):
    cols = "l" + "r" * df.shape[1]
    lines = [r"\begin{table}[htbp]", r"\centering", r"\small",
             rf"\caption{{{esc(caption)}}}", rf"\label{{{label}}}",
             r"\resizebox{\columnwidth}{!}{%", rf"\begin{{tabular}}{{{cols}}}", r"\toprule",
             " & ".join(esc(x) for x in [first] + list(df.columns)) + r" \\", r"\midrule"]
    for i, row in zip(df.index, df.values):
        lines.append(" & ".join(esc(x) for x in [i] + list(row)) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}}", r"\end{table}", ""]
    return "\n".join(lines)


def trade_stats(trades, bpd):
    if not trades:
        return ["0", "n/a", "n/a", "n/a"]
    r = np.array([t[0] for t in trades])
    d = np.array([t[1] for t in trades]) / bpd
    return [f"{len(r):,}", f"{100 * np.mean(r > 0):.1f}%", f"{1e4 * r.mean():.1f}", f"{d.mean():.2f}"]


def exit_stats(trades):
    """Share of trades ending at the mean / by stop-out / forced at window end."""
    out = []
    for reason in ("exit", "stop", "forced"):
        sub = [t[0] for t in trades if t[2] == reason]
        out += [pct(len(sub), len(trades)), f"{1e4 * np.mean(sub):.1f}" if sub else "n/a"]
    return out


# ============================================================================== main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default=None)
    ap.add_argument("--freq", default="1h", help="bar size: 5min, 15min, 1h, ...")
    ap.add_argument("--refresh", action="store_true", help="rebuild the price cache")
    ap.add_argument("--L_days", type=float, default=180, help="formation window (days)")
    ap.add_argument("--M_days", type=float, default=30, help="trading window (days)")
    ap.add_argument("--rho_min", type=float, default=0.6)
    ap.add_argument("--max_candidates", type=int, default=3000)
    ap.add_argument("--q", type=float, default=0.05)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--h_min", type=float, default=0.25, help="min half-life (days)")
    ap.add_argument("--h_max", type=float, default=10.0, help="max half-life (days)")
    ap.add_argument("--entry", type=float, default=2.0)
    ap.add_argument("--exit", type=float, default=0.0)
    ap.add_argument("--stop", type=float, default=4.0)
    ap.add_argument("--cost", type=float, default=0.001, help="per unit notional traded")
    ap.add_argument("--max_pairs", type=int, default=20)
    ap.add_argument("--roll_days", type=float, default=60, help="rolling hedge window (days)")
    ap.add_argument("--eg_lags", type=int, default=4)
    ap.add_argument("--vecm_lags", type=int, default=1)
    ap.add_argument("--maxlag", type=int, default=24, help="max ADF lag")
    ap.add_argument("--exclude", default="RLUSDUSDT,PAXGUSDT,XAUTUSDT")
    ap.add_argument("--shortable", default=None, help="txt file of coins that can be shorted")
    ap.add_argument("--start", default=None, help="force common start date YYYY-MM-DD")
    ap.add_argument("--min_coins", type=int, default=30)
    ap.add_argument("--min_windows", type=int, default=12)
    ap.add_argument("--max_missing", type=float, default=0.02)
    ap.add_argument("--max_windows", type=int, default=0, help="limit windows (testing)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    a = ap.parse_args()

    out = Path(a.out or f"results_{a.freq}")
    out.mkdir(parents=True, exist_ok=True)

    P = load_close(a.data, a.freq, [s for s in a.exclude.split(",") if s], a.refresh)
    step = P.index[1] - P.index[0]
    bpd = int(round(pd.Timedelta("1D") / step))
    L, M, R = int(a.L_days * bpd), int(a.M_days * bpd), int(a.roll_days * bpd)
    start, cols, tradeoff = align_universe(P, L, M, a.min_windows, a.min_coins, 7 * bpd,
                                           a.max_missing, a.start)
    tradeoff.to_csv(out / "alignment_tradeoff.csv", index=False)
    P = P.loc[start:, cols].ffill(limit=3)
    LP = np.log(P)
    n = len(P)
    print(f"Bars/day={bpd}  L={L} M={M} bars | {P.index[0]} -> {P.index[-1]} | "
          f"{len(cols)} coins common timeline")

    short = set(Path(a.shortable).read_text().split()) if a.shortable else None
    p = dict(L=L, M=M, bpd=bpd, roll=R, alpha=a.alpha, maxlag=a.maxlag, rho_min=a.rho_min,
             max_candidates=a.max_candidates, eg_lags=a.eg_lags, q=a.q,
             vecm_lags=a.vecm_lags, h_min=a.h_min, h_max=a.h_max, entry=a.entry,
             exit=a.exit, stop=a.stop, cost=a.cost, max_pairs=a.max_pairs, short=short)
    starts = list(enumerate(range(0, n - L - M + 1, M)))
    if a.max_windows:
        starts = starts[:a.max_windows]
    print(f"{len(starts)} walk-forward windows, {a.jobs} worker(s)")

    def make(w, s0):
        return dict(w=w, p=p, cols=list(P.columns), lp=LP.values[s0:s0 + L + M],
                    times=P.index[s0 + L:s0 + L + M])

    results = []
    if a.jobs > 1:
        with ProcessPoolExecutor(a.jobs) as ex:
            for i in range(0, len(starts), a.jobs):
                futs = [ex.submit(process_window, make(w, s0)) for w, s0 in starts[i:i + a.jobs]]
                for f in futs:
                    r = f.result(); results.append(r)
                    print(f"[{len(results)}/{len(starts)}] I1={r['row']['n_I1']} "
                          f"BH={r['row']['n_bh']} sel={r['row']['n_sel']}", flush=True)
    else:
        for w, s0 in starts:
            r = process_window(make(w, s0)); results.append(r)
            print(f"[{len(results)}/{len(starts)}] I1={r['row']['n_I1']} "
                  f"BH={r['row']['n_bh']} sel={r['row']['n_sel']}", flush=True)
    results.sort(key=lambda r: r["w"])

    # ------------------------------------------------------------- assemble results
    F = pd.DataFrame([r["row"] for r in results])
    S = F.sum()
    idx = lambda r: r["times"]
    series = {}
    for key in ("static", "static_gross", "rolling", "distance", "bh_ew"):
        series[key] = pd.concat([pd.Series(r["ret"][key], idx(r)) for r in results])
    if all(r["ret"]["btc"] is not None for r in results):
        series["btc"] = pd.concat([pd.Series(r["ret"]["btc"], idx(r)) for r in results])
    daily = pd.DataFrame({k: to_daily(v) for k, v in series.items()})
    daily.to_csv(out / "daily_returns.csv")
    pairs = pd.DataFrame([{"window": r["w"], "trade_start": r["times"][0],
                           **{k: v for k, v in d.items() if k not in ("yi", "xi")}}
                          for r in results for d in r["pairs"]])
    pairs.to_csv(out / "selected_pairs.csv", index=False)
    F.to_csv(out / "funnel_by_window.csv", index=False)

    # per-window returns (does the loss come from a few windows?)
    wr = []
    for r in results:
        rw = dict(window=r["w"], trade_start=r["times"][0], n_selected=r["row"]["n_sel"])
        for k in ("static", "rolling", "distance", "bh_ew"):
            rw[k] = float(np.prod(1 + r["ret"][k]) - 1)
        rw["btc"] = float(np.prod(1 + r["ret"]["btc"]) - 1) if r["ret"]["btc"] is not None else np.nan
        wr.append(rw)
    WR = pd.DataFrame(wr)
    WR.to_csv(out / "window_returns.csv", index=False)

    # sanity check: are static and rolling really different series?
    chk = daily[["static", "rolling"]].dropna()
    print(f"check static vs rolling: total {np.prod(1 + chk['static']) - 1:.5f} vs "
          f"{np.prod(1 + chk['rolling']) - 1:.5f}, daily correlation {chk.corr().iloc[0, 1]:.3f}")

    # ------------------------------------------------------------------- tables
    T = {}
    T["Table 1. Stationarity of log prices (Obj. 2)"] = pd.DataFrame({"Value": [
        f"{F.n_elig.mean():.1f}", pct(S.n_lvl, S.n_elig), pct(S.n_dadf, S.n_elig),
        pct(S.n_dkpss, S.n_elig), pct(S.n_I1, S.n_elig)]},
        index=["Coins tested per window", "Levels non-stationary (ADF)",
               "Differences stationary (ADF)", "Differences stationary (KPSS)",
               "Classified I(1) (all three)"])
    stages = [("Pairs tested (EG, both orderings)", "n_eg"), ("Raw p < 5%", "n_raw05"),
              ("Pass BH (q=5%)", "n_bh"), ("Pass BY (q=5%)", "n_by"),
              ("Johansen rank 0 (of BH)", "r0"), ("Johansen rank 1 (of BH)", "r1"),
              ("Johansen rank 2 (of BH)", "r2"), ("Rank 1 and correct EC sign", "n_vecm_ok"),
              ("Half-life in range", "n_hl_ok"), ("Selected for trading", "n_sel")]
    T["Table 2. Cointegration funnel (Obj. 3)"] = pd.DataFrame(
        {"Total": [f"{int(S[k]):,}" for _, k in stages],
         "Per window": [f"{F[k].mean():.1f}" for _, k in stages]},
        index=[s for s, _ in stages])

    hl = np.array([h for r in results for h in r["hl"]]).reshape(-1, 3)
    if len(hl):
        hd = hl[:, 0]
        T["Table 3. Mean reversion of spreads (Obj. 4)"] = pd.DataFrame({"Value": [
            f"{len(hd):,}", f"{np.median(hd):.2f}",
            f"{np.percentile(hd, 25):.2f} - {np.percentile(hd, 75):.2f}",
            pct(((hd >= a.h_min) & (hd <= a.h_max)).sum(), len(hd)),
            f"{np.median(hl[:, 2]):.2f}"]},
            index=["Pairs with rank 1 and correct sign", "Median half-life (days)",
                   "Half-life IQR (days)", "Share within filter", "Median hedge ratio"])
    names = {"static": "Cointegration (static hedge)", "rolling": "Cointegration (rolling hedge)",
             "distance": "Distance method"}
    all_tr = {k: [t for r in results for t in r["trades"][k]] for k in names}
    ts = {k: trade_stats(all_tr[k], bpd) for k in names}
    T["Table 4. Trading activity (Obj. 5)"] = pd.DataFrame(
        {names[k]: ts[k] for k in names},
        index=["Trades", "Hit rate", "Mean net trade (bps)", "Mean holding (days)"]).T

    labels = {"static": "Cointegration (static)", "rolling": "Cointegration (rolling)",
              "distance": "Distance method", "bh_ew": "Buy-and-hold (EW)", "btc": "Buy-and-hold (BTC)"}
    pr = pd.DataFrame([perf(daily[k], labels[k]) for k in labels if k in daily]).set_index("strategy")
    gross_sharpe = perf(daily["static_gross"], "g")["sharpe"]
    fmt = pr.copy()
    for c in ("total", "cagr", "ann_ret", "ann_vol", "mdd", "var", "cvar"):
        fmt[c] = (100 * pr[c]).map("{:.1f}%".format)
    fmt["sharpe"] = pr["sharpe"].map("{:.2f}".format)
    fmt.columns = ["Total ret", "CAGR", "Ann. ret (mean x 365)", "Ann. vol", "Sharpe",
                   "Max DD", "VaR 5%", "CVaR 5%"]
    T["Table 5. Out-of-sample performance, net of costs (Obj. 6)"] = fmt

    sets = [{frozenset((d["y"], d["x"])) for d in r["pairs"]} for r in results]
    pers = [len(x & y) / len(x) for x, y in zip(sets[:-1], sets[1:]) if x]
    db = []
    for r1, r2 in zip(results[:-1], results[1:]):
        m2 = {(d["y"], d["x"]): d["b"] for d in r2["pairs"]}
        db += [abs(m2[(d["y"], d["x"])] - d["b"]) / d["b"] for d in r1["pairs"]
               if (d["y"], d["x"]) in m2]
    T["Table 6. Stability of selected pairs (Obj. 7)"] = pd.DataFrame({"Value": [
        f"{np.mean(pers):.1%}" if pers else "n/a", f"{np.median(pers):.1%}" if pers else "n/a",
        f"{np.median(db):.1%}" if db else "n/a", f"{F.n_sel.mean():.1f}",
        f"{int((F.n_sel == 0).sum())} of {len(F)}",
        f"{pr.loc[labels['static'], 'sharpe']:.2f} (gross {gross_sharpe:.2f})",
        f"{pr.loc[labels['rolling'], 'sharpe']:.2f}"]},
        index=["Persistence, mean (pairs re-qualifying next window)", "Persistence, median",
               "Median hedge-ratio change between windows", "Pairs selected per window",
               "Windows with no pair", "Sharpe, static hedge (net)", "Sharpe, rolling hedge (net)"])

    # Table 7: how trades end
    ex_names = ["Exit at mean (share)", "Exit at mean (net bps)", "Stop-out (share)",
                "Stop-out (net bps)", "Window-end close (share)", "Window-end close (net bps)"]
    T["Table 7. How trades end (Obj. 5)"] = pd.DataFrame(
        {names[k]: exit_stats(all_tr[k]) for k in names}, index=ex_names).T

    # Table 8 and 9: out-of-sample behaviour and WHICH coins
    if len(pairs):
        co = pairs["coint_oos"].astype(bool)
        mean_pnl = lambda m: f"{100 * pairs['pnl_static'][m].mean():.2f}%" if m.any() else "n/a"
        T["Table 8. Out-of-sample behaviour of selected pairs (Obj. 7)"] = pd.DataFrame({"Value": [
            f"{len(pairs):,}",
            pct(co.sum(), len(pairs)),
            f"{pairs['p_oos'].median():.3f}",
            f"{pairs['oos_sd_ratio'].median():.2f}",
            f"{pairs['oos_mean_shift'].abs().median():.2f}",
            pct((pairs["frac_beyond_stop"] > 0).sum(), len(pairs)),
            f"{pairs['n_trades'].median():.0f}",
            pct((pairs["pnl_static"] > 0).sum(), len(pairs)),
            mean_pnl(pd.Series(True, index=pairs.index)),
            mean_pnl(co), mean_pnl(~co)]},
            index=["Pair-windows selected",
                   "Still cointegrated in trading window (EG p<5%)",
                   "Median EG p-value in trading window",
                   "Median spread SD ratio (trading / formation)",
                   "Median |spread level shift| (formation SDs)",
                   "Pairs that hit the stop level",
                   "Median trades per pair-window",
                   "Pairs with positive net P&L",
                   "Mean net P&L per pair-window",
                   "  ... if still cointegrated",
                   "  ... if no longer cointegrated"])

        pairs["pair"] = [" / ".join(sorted([u.replace("USDT", ""), v.replace("USDT", "")]))
                         for u, v in zip(pairs["y"], pairs["x"])]
        g = pairs.groupby("pair")
        top = pd.DataFrame({
            "Windows": g["window"].nunique(),
            "Median half-life (d)": g["half_life_days"].median().round(2),
            "Share still coint.": g["coint_oos"].mean().map(lambda v: f"{v:.0%}"),
            "Mean net P&L": g["pnl_static"].mean().map(lambda v: f"{v:.2%}"),
            "_p": g["p_eg"].min()})
        top = top.sort_values(["Windows", "_p"], ascending=[False, True]).drop(columns="_p")
        top.to_csv(out / "pairs_summary.csv")
        T["Table 9. Pairs selected in the most windows"] = top.head(15)
        coin_counts = pd.concat([pairs["y"], pairs["x"]]).str.replace("USDT", "").value_counts()
        coin_counts.to_csv(out / "coin_counts.csv", header=["pair_windows"])

    # ------------------------------------------------------------------ write files
    header = (f"# Results summary\n\nBars: {a.freq} | formation {a.L_days:g} d, trading {a.M_days:g} d | "
              f"{len(results)} walk-forward windows\n"
              f"Sample: {P.index[0].date()} to {P.index[-1].date()} | {len(cols)} coins\n"
              f"FDR q={a.q} | entry/exit/stop = {a.entry}/{a.exit}/{a.stop} | cost = {a.cost:.2%} "
              f"per unit traded | half-life {a.h_min}-{a.h_max} d\n")
    md, tex = [header], [r"% needs: booktabs, graphicx"]
    for i, (title, df) in enumerate(T.items(), 1):
        md.append(f"\n## {title}\n\n{md_table(df)}\n")
        tex.append(tex_table(df, title.split(". ", 1)[1], f"tab:res{i}"))
        df.to_csv(out / f"table{i}.csv")
    worst = WR.sort_values("static").head(5)[["window", "trade_start", "n_selected", "static", "rolling"]]
    md.append("\n## Worst five windows for the static strategy\n\n" + md_table(
        worst.assign(trade_start=worst["trade_start"].astype(str).str[:10],
                     static=worst["static"].map("{:.1%}".format),
                     rolling=worst["rolling"].map("{:.1%}".format)).set_index("window"), "window") + "\n")
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    (out / "results_tables.tex").write_text("\n".join(tex), encoding="utf-8")
    print("\n".join(md))

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ax = (1 + daily.drop(columns="static_gross")).cumprod().plot(figsize=(9, 5), logy=True)
        ax.set_ylabel("Growth of 1 (log scale)")
        plt.tight_layout(); plt.savefig(out / "equity_curves.png", dpi=150)
        if len(hl):
            plt.figure(figsize=(6, 4)); plt.hist(np.clip(hl[:, 0], 0, 30), bins=40)
            plt.xlabel("Half-life (days)"); plt.tight_layout()
            plt.savefig(out / "half_life_hist.png", dpi=150)
    except Exception as e:
        print("plots skipped:", e)
    print(f"\nSaved to {out.resolve()}")


if __name__ == "__main__":
    main()