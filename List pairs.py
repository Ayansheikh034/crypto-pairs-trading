"""
list_pairs.py  -  show WHICH coins were found cointegrated.

Usage (PowerShell):
  python list_pairs.py --results "E:\\pair trading\\results"

It scans the results folder for CSV files, prints every file with its columns,
picks the one that lists selected pairs, and writes:
  pairs_summary.csv    one row per pair (how many windows, half-life, hedge ratio)
  pairs_by_window.csv  every selected pair in every window
  pairs_table.tex      top-10 table ready for the paper
If it cannot find the pair columns, it tells you the file names and columns
so you can paste them and the script can be adjusted.
"""
import argparse
import re
from pathlib import Path

import pandas as pd

COIN_A = ["coin_i", "asset_i", "sym_i", "y", "leg1", "coin1", "coin_a", "a", "i", "dep", "dependent", "sym1", "asset1", "base"]
COIN_B = ["coin_j", "asset_j", "sym_j", "x", "leg2", "coin2", "coin_b", "b", "j", "reg", "regressor", "sym2", "asset2", "quote"]
WIN = ["window", "win", "w", "window_id", "k"]
HL = ["half_life", "halflife", "hl", "half_life_days", "hl_days"]
HEDGE = ["hedge", "hedge_ratio", "beta", "b", "b_hat", "hr"]
PVAL = ["p_eg", "p_adj", "p_bh", "pval", "p_value", "pvalue", "p", "eg_p", "p_raw"]
DATE = ["trade_start", "start", "form_end", "window_start", "date"]


def pick(cols, names):
    low = {c.lower(): c for c in cols}
    for n in names:
        if n in low:
            return low[n]
    for n in names:                       # partial match
        for lc, c in low.items():
            if len(n) > 2 and n in lc:
                return c
    return None


def split_pair(s):
    parts = re.split(r"\s*[/\-_,|:]\s*|\s+vs\.?\s+", str(s))
    parts = [p for p in parts if p]
    return (parts[0], parts[1]) if len(parts) >= 2 else (None, None)


def find_pair_file(csvs):
    best, best_score = None, -1
    for f in csvs:
        try:
            df = pd.read_csv(f, nrows=5)
        except Exception:
            continue
        cols = list(df.columns)
        score = 0
        has_two = pick(cols, COIN_A) and pick(cols, COIN_B) and pick(cols, COIN_A) != pick(cols, COIN_B)
        has_pair = any(c.lower() in ("pair", "pairs") for c in cols)
        if has_two or has_pair:
            score += 5
        for names in (WIN, HL, HEDGE, PVAL):
            if pick(cols, names):
                score += 1
        name = f.name.lower()
        if "select" in name:
            score += 3
        if "pair" in name:
            score += 2
        if "trade" in name and "pair" not in name and "select" not in name:
            score -= 2
        if score > best_score:
            best, best_score = f, score
    return best if best_score >= 5 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=".", help="folder with the analysis CSV files")
    ap.add_argument("--file", default=None, help="force a specific CSV file")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()

    root = Path(a.results)
    csvs = sorted(root.rglob("*.csv"))
    if not csvs:
        raise SystemExit(f"No CSV files found under {root}")

    print("CSV files found:")
    for f in csvs:
        try:
            cols = list(pd.read_csv(f, nrows=1).columns)
        except Exception:
            cols = ["<unreadable>"]
        print(f"  {f.relative_to(root)}: {cols}")

    f = Path(a.file) if a.file else find_pair_file(csvs)
    if f is None:
        raise SystemExit("\nCould not tell which file lists the selected pairs. "
                         "Paste the file list above and I will adjust the script, "
                         "or rerun with --file <name>.csv")
    print(f"\nUsing: {f}")
    df = pd.read_csv(f)
    cols = list(df.columns)

    ca, cb = pick(cols, COIN_A), pick(cols, COIN_B)
    pair_col = next((c for c in cols if c.lower() in ("pair", "pairs")), None)
    if pair_col is not None and (ca is None or cb is None or ca == cb):
        sp = df[pair_col].map(split_pair)
        df["coin_i"] = sp.map(lambda t: t[0])
        df["coin_j"] = sp.map(lambda t: t[1])
        ca, cb = "coin_i", "coin_j"
    if ca is None or cb is None:
        raise SystemExit(f"No coin columns found in {f.name}: {cols}")

    cw, chl, ch, cp, cd = (pick(cols, n) for n in (WIN, HL, HEDGE, PVAL, DATE))
    clean = lambda s: str(s).replace("USDT", "")
    df["A"] = df[ca].map(clean)
    df["B"] = df[cb].map(clean)
    df["pair"] = [" / ".join(sorted([x, y])) for x, y in zip(df["A"], df["B"])]

    if cw is None:
        df["_w"] = range(len(df))
        cw = "_w"

    # per-window listing
    keep = [c for c in (cw, cd, "A", "B", ch, chl, cp) if c is not None and c in df.columns]
    by_win = df[keep].rename(columns={"A": "coin_i", "B": "coin_j"}).sort_values(cw)
    by_win.to_csv("pairs_by_window.csv", index=False)

    # per-pair summary
    g = df.groupby("pair")
    summ = pd.DataFrame({
        "windows_selected": g[cw].nunique(),
        "first_window": g[cw].min(),
        "last_window": g[cw].max(),
    })
    if chl:
        summ["median_half_life_d"] = g[chl].median().round(2)
    if ch:
        summ["median_hedge_ratio"] = g[ch].median().round(3)
    if cp:
        summ["best_p"] = g[cp].min()
    summ = summ.sort_values(["windows_selected"] + (["best_p"] if cp else []),
                            ascending=[False] + ([True] if cp else []))
    summ.to_csv("pairs_summary.csv")

    print(f"\n{len(df)} selected-pair rows, {summ.shape[0]} distinct pairs, "
          f"{df['A'].nunique() + 0} distinct lead coins.\n")
    print(f"Top {a.top} pairs (most windows selected):")
    print(summ.head(a.top).to_string())

    coin_counts = pd.concat([df["A"], df["B"]]).value_counts()
    print("\nCoins that appear most often in selected pairs:")
    print(coin_counts.head(10).to_string())

    # LaTeX top-10
    top = summ.head(10).reset_index()
    lines = [r"\begin{table}[htbp]", r"\centering", r"\small",
             r"\caption{Pairs selected in the most walk-forward windows.}",
             r"\label{tab:toppairs}"]
    heads = ["Pair", "Windows"] + (["Half-life (d)"] if chl else []) + (["Hedge ratio"] if ch else [])
    lines += [r"\begin{tabular}{@{}l" + "r" * (len(heads) - 1) + r"@{}}", r"\toprule",
              " & ".join(heads) + r" \\", r"\midrule"]
    for _, r in top.iterrows():
        row = [r["pair"].replace("/", "--"), str(int(r["windows_selected"]))]
        if chl:
            row.append(f"{r['median_half_life_d']:.2f}")
        if ch:
            row.append(f"{r['median_hedge_ratio']:.2f}")
        lines.append(" & ".join(row) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    Path("pairs_table.tex").write_text("\n".join(lines), encoding="utf-8")
    print("\nSaved: pairs_summary.csv, pairs_by_window.csv, pairs_table.tex")


if __name__ == "__main__":
    main()