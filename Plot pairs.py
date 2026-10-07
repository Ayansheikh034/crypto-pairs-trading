#!/usr/bin/env python3
"""
Plot the most strongly / persistently cointegrated pairs found by analysis_minute.py.

Reads  : <results>/selected_pairs.csv, <results>/pairs_summary.csv
         <data>/close_<freq>.parquet   (price cache made by analysis_minute.py)
Writes : <results>/plots/
           00_overview_persistence.png   pairs selected in the most windows
           01_selection_timeline.png     which pairs were selected in which window
           pair_<A>_<B>.png              prices, spread z-score and trades for the best window

Usage
  python plot_pairs.py --data "E:\\pair trading\\data" --results "E:\\pair trading\\results_1h" --freq 1h --top 6
Options
  --rank persistence   pairs selected in most windows (default)
  --rank pvalue        pairs with the smallest Engle-Granger p-value in any window
  --rank oos           pairs that stayed cointegrated out of sample, then by p-value
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def short(sym):
    return sym.replace("USDT", "")


def signals(z, entry, exit_, stop):
    """Same rules as run_pair in analysis_minute.py. Returns position array and event list."""
    pos, cur, events = np.zeros(len(z)), 0, []
    for k, zk in enumerate(z):
        if np.isnan(zk):
            cur = 0
        elif cur == 0:
            if entry <= zk < stop:
                cur = -1; events.append((k, "short"))
            elif -stop < zk <= -entry:
                cur = 1; events.append((k, "long"))
        elif cur == 1 and (zk >= -exit_ or zk <= -stop):
            events.append((k, "stop" if zk <= -stop else "exit")); cur = 0
        elif cur == -1 and (zk <= exit_ or zk >= stop):
            events.append((k, "stop" if zk >= stop else "exit")); cur = 0
        pos[k] = cur
    return pos, events


def overview(summary, out):
    top = summary.head(15).iloc[::-1]
    share = top["Share still coint."].str.rstrip("%").astype(float) / 100
    fig, ax = plt.subplots(figsize=(9, 6))
    bars = ax.barh(top.index, top["Windows"], color=plt.cm.RdYlGn(share))
    for b, s, h in zip(bars, share, top["Median half-life (d)"]):
        ax.text(b.get_width() + 0.05, b.get_y() + b.get_height() / 2,
                f"{s:.0%} still coint. | HL {h:.1f} d", va="center", fontsize=8)
    ax.set_xlabel("Walk-forward windows in which the pair was selected")
    ax.set_title("Most persistent cointegrated pairs\n(colour = share of windows still cointegrated out of sample)")
    ax.set_xlim(0, top["Windows"].max() * 1.35)
    fig.tight_layout(); fig.savefig(out / "00_overview_persistence.png", dpi=150); plt.close(fig)


def timeline(pairs, summary, out):
    names = list(summary.head(15).index)[::-1]
    wins = sorted(pairs["window"].unique())
    grid = np.full((len(names), len(wins)), np.nan)
    for i, n in enumerate(names):
        sub = pairs[pairs["pair"] == n]
        for _, r in sub.iterrows():
            grid[i, wins.index(r["window"])] = 1.0 if r["coint_oos"] else 0.5
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.imshow(grid, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1, interpolation="nearest")
    ax.set_yticks(range(len(names))); ax.set_yticklabels(names, fontsize=8)
    step = max(1, len(wins) // 15)
    ax.set_xticks(range(0, len(wins), step)); ax.set_xticklabels(wins[::step])
    ax.set_xlabel("Walk-forward window")
    ax.set_title("When each pair was selected (green = still cointegrated in trading window, "
                 "orange = selected but not; blank = not selected)")
    fig.tight_layout(); fig.savefig(out / "01_selection_timeline.png", dpi=150); plt.close(fig)


def plot_pair(P, row, a, L, M, out):
    y, x = row["y"], row["x"]
    t0 = pd.Timestamp(row["trade_start"])
    pos0 = P.index.get_indexer([t0])[0]
    if pos0 < L:
        return False
    lo, hi = pos0 - L, pos0 + M
    sub = np.log(P[[y, x]].ffill(limit=3).iloc[lo:hi])
    if sub.isna().any().any():
        return False
    idx = sub.index
    spread = sub[y].values - row["b"] * sub[x].values
    z = (spread - row["mu"]) / row["sd"]
    ztr = z[L:]
    _, ev = signals(ztr, a.entry, a.exit, a.stop)
    tr_idx = idx[L:]

    fig, axs = plt.subplots(3, 1, figsize=(11, 10), gridspec_kw={"height_ratios": [2, 2, 1.3]})
    # 1) prices rebased to 1
    ax = axs[0]
    for s, c in ((y, "tab:blue"), (x, "tab:orange")):
        ax.plot(idx, np.exp(sub[s] - sub[s].iloc[0]), label=short(s), color=c, lw=1)
    ax.axvline(t0, color="k", ls="--", lw=1); ax.set_ylabel("Price (start = 1)")
    ax.set_title(f"{short(y)} / {short(x)} | window {int(row['window'])} | EG p = {row['p_eg']:.4f} | "
                 f"hedge b = {row['b']:.2f} | half-life = {row['half_life_days']:.1f} d | "
                 f"OOS p = {row['p_oos']:.3f}")
    ax.legend(loc="upper left"); ax.grid(alpha=.3)
    ax.text(idx[L // 2], ax.get_ylim()[1], "formation", ha="center", va="top", fontsize=9)
    ax.text(tr_idx[M // 2], ax.get_ylim()[1], "trading (out of sample)", ha="center", va="top", fontsize=9)
    # 2) z-score with bands and trades
    ax = axs[1]
    ax.plot(idx, z, color="tab:purple", lw=.9)
    for lv, st in ((a.entry, ":"), (-a.entry, ":"), (a.stop, "--"), (-a.stop, "--"), (0, "-")):
        ax.axhline(lv, color="grey", ls=st, lw=.8)
    ax.axvline(t0, color="k", ls="--", lw=1)
    style = {"long": ("^", "green"), "short": ("v", "red"), "exit": ("o", "blue"), "stop": ("X", "black")}
    for k, kind in ev:
        m, c = style[kind]
        ax.scatter(tr_idx[k], ztr[k], marker=m, color=c, s=45, zorder=3,
                   label=kind if kind not in ax.get_legend_handles_labels()[1] else None)
    ax.set_ylabel("Spread z-score"); ax.grid(alpha=.3)
    if ev:
        ax.legend(loc="upper left", ncol=4, fontsize=8)
    # 3) formation scatter, coloured by time
    ax = axs[2]
    ax.axis("off")
    txt = (f"Trades in window: {int(row['n_trades'])}    Net P&L (static hedge): {row['pnl_static']:.2%}\n"
           f"Spread SD ratio (trading/formation): {row['oos_sd_ratio']:.2f}    "
           f"Level shift: {row['oos_mean_shift']:.2f} formation SDs    "
           f"Passes BY: {bool(row['pass_BY'])}    Still cointegrated OOS: {bool(row['coint_oos'])}")
    ax.text(0.01, 0.6, txt, fontsize=10, va="center")
    fig.tight_layout()
    fig.savefig(out / f"pair_{short(y)}_{short(x)}.png", dpi=150); plt.close(fig)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--results", default="results_1h")
    ap.add_argument("--freq", default="1h")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--rank", choices=["persistence", "pvalue", "oos"], default="persistence")
    ap.add_argument("--L_days", type=float, default=180)
    ap.add_argument("--M_days", type=float, default=30)
    ap.add_argument("--entry", type=float, default=2.0)
    ap.add_argument("--exit", type=float, default=0.0)
    ap.add_argument("--stop", type=float, default=4.0)
    a = ap.parse_args()

    res = Path(a.results)
    out = res / "plots"; out.mkdir(exist_ok=True)
    pairs = pd.read_csv(res / "selected_pairs.csv")
    if pairs.empty:
        raise SystemExit("selected_pairs.csv is empty: no pair was selected in any window.")
    pairs["pair"] = [" / ".join(sorted([short(u), short(v)])) for u, v in zip(pairs["y"], pairs["x"])]
    summary = pd.read_csv(res / "pairs_summary.csv", index_col=0)
    P = pd.read_parquet(Path(a.data) / f"close_{a.freq}.parquet")
    P.index = pd.to_datetime(P.index, utc=True)
    bpd = int(round(pd.Timedelta("1D") / (P.index[1] - P.index[0])))
    L, M = int(a.L_days * bpd), int(a.M_days * bpd)

    overview(summary, out)
    timeline(pairs, summary, out)

    pairs["trade_start"] = pd.to_datetime(pairs["trade_start"], utc=True)
    if a.rank == "pvalue":
        order = pairs.groupby("pair")["p_eg"].min().sort_values().index
    elif a.rank == "oos":
        g = pairs.groupby("pair").agg(c=("coint_oos", "mean"), p=("p_eg", "min"))
        order = g.sort_values(["c", "p"], ascending=[False, True]).index
    else:
        order = summary.index
    done = 0
    for name in order:
        if done >= a.top:
            break
        sub = pairs[pairs["pair"] == name]
        best = sub[sub["coint_oos"]] if sub["coint_oos"].any() else sub
        row = best.sort_values("p_eg").iloc[0]
        if plot_pair(P, row, a, L, M, out):
            done += 1
            print(f"plotted {name} (window {int(row['window'])})")
    print(f"\nSaved {done} pair plots + 2 overview charts to {out.resolve()}")


if __name__ == "__main__":
    main()