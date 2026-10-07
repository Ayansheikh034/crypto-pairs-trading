#!/usr/bin/env python3
"""
Collect 1-minute OHLCV data for the top-N Binance spot USDT pairs.

History   : monthly zips from https://data.binance.vision  (fast, no rate limit)
Recent    : REST /api/v3/klines for the not-yet-archived tail (rate limited)
Storage   : data/1m/<SYMBOL>/<YYYY-MM>.parquet  (zstd, one file per month)
Resumable : finished months are skipped; the current month is topped up.

Usage
  pip install pandas pyarrow requests
  python collect_minute_data.py --top 100 --since 2019-01 --out data
  python collect_minute_data.py --symbols my_universe.txt        # fixed list
  python collect_minute_data.py --update                         # top up later

Reuse in other scripts
  from collect_minute_data import load_panel
  close = load_panel("data", ["BTCUSDT", "ETHUSDT"], freq="1h")   # wide DataFrame
"""
import argparse
import io
import re
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

API = "https://api.binance.com"
VISION = "https://data.binance.vision/data/spot/monthly/klines"
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]
KEEP = ["open", "high", "low", "close", "volume", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote"]

STABLES = {"USDT", "USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD", "USDS", "USDE",
           "USD1", "PYUSD", "XUSD", "BFUSD", "UST", "AEUR", "EURI", "EUR", "GBP",
           "TRY", "BRL", "ARS", "RUB", "UAH", "NGN", "PLN", "RON", "CZK", "JPY",
           "MXN", "COP", "ZAR", "IDRT"}
WRAPPED = {"WBTC", "WBETH", "BETH", "WEETH", "STETH", "RSETH", "CBBTC", "BNSOL"}


# ------------------------------------------------------------------ helpers
class RateLimiter:
    """Keep REST calls under ~8/s (klines weight 2 -> ~960 of 1200 weight/min)."""
    def __init__(self, per_sec=8):
        self.gap, self.lock, self.next = 1.0 / per_sec, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            sleep = max(0.0, self.next - now)
            self.next = max(now, self.next) + self.gap
        if sleep:
            time.sleep(sleep)


LIMITER = RateLimiter()


def get(sess, url, **kw):
    """GET with retries and Binance 418/429 back-off."""
    for attempt in range(6):
        try:
            r = sess.get(url, timeout=60, **kw)
            if r.status_code in (418, 429):
                time.sleep(int(r.headers.get("Retry-After", 30)))
                continue
            return r
        except requests.RequestException:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"failed: {url}")


def to_frame(df):
    """Raw kline columns -> clean UTC-indexed float frame."""
    df = df.copy()
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
    df = df.dropna(subset=["open_time"])
    ot = df["open_time"].astype("int64")
    ot = ot.where(ot < 10**14, ot // 1000)        # spot files from 2025 use microseconds
    df.index = pd.to_datetime(ot, unit="ms", utc=True)
    df.index.name = "time"
    out = df[KEEP].apply(pd.to_numeric, errors="coerce")
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.astype({"trades": "float64"})


def parse_zip(content):
    z = zipfile.ZipFile(io.BytesIO(content))
    raw = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])), header=None, names=COLS)
    return to_frame(raw)


def save(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, compression="zstd")
    tmp.replace(path)


# ----------------------------------------------------------------- universe
def get_universe(sess, n, quote="USDT"):
    info = get(sess, f"{API}/api/v3/exchangeInfo").json()
    syms = {s["symbol"]: s for s in info["symbols"]
            if s["quoteAsset"] == quote and s["status"] == "TRADING"
            and s.get("isSpotTradingAllowed")}
    bases = {s["baseAsset"] for s in syms.values()}

    def leveraged(b):
        # 'BTCUP' is leveraged only if 'BTC' itself is a listed base (so JUP, SYRUP survive)
        m = re.fullmatch(r"(.+)(UP|DOWN|BULL|BEAR)", b)
        return bool(m and m.group(1) in bases)

    tick = get(sess, f"{API}/api/v3/ticker/24hr").json()
    rows = []
    for t in tick:
        s = syms.get(t["symbol"])
        if not s:
            continue
        b = s["baseAsset"]
        if b in STABLES or b in WRAPPED or leveraged(b):
            continue
        rows.append({"symbol": t["symbol"], "base": b,
                     "quote_volume_24h": float(t["quoteVolume"])})
    df = pd.DataFrame(rows).sort_values("quote_volume_24h", ascending=False)
    return df.head(n).reset_index(drop=True)


# ---------------------------------------------------------------- downloads
def first_kline(sess, sym):
    LIMITER.wait()
    r = get(sess, f"{API}/api/v3/klines",
            params=dict(symbol=sym, interval="1m", startTime=0, limit=1)).json()
    return pd.Timestamp(r[0][0], unit="ms", tz="UTC") if r else None


def fetch_api(sess, sym, start, end):
    """Minutes in [start, end) from REST; completed candles only."""
    out, t = [], int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    while t < end_ms:
        LIMITER.wait()
        r = get(sess, f"{API}/api/v3/klines",
                params=dict(symbol=sym, interval="1m", startTime=t,
                            endTime=end_ms - 1, limit=1000))
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        out += rows
        t = rows[-1][0] + 60_000
        if len(rows) < 1000:
            break
    if not out:
        return pd.DataFrame(columns=KEEP)
    return to_frame(pd.DataFrame(out, columns=COLS))


def fetch_month_zip(sess, sym, ym):
    r = get(sess, f"{VISION}/{sym}/1m/{sym}-1m-{ym}.zip")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return parse_zip(r.content)


def collect_symbol(sym, root, since, force_months=False):
    sess = requests.Session()
    now = pd.Timestamp.now(tz="UTC").floor("min")
    cur = now.to_period("M")
    first = first_kline(sess, sym)
    if first is None:
        return sym, "no data"
    start_m = max(first.to_period("M"), pd.Period(since, "M"))
    n_new = 0
    for m in pd.period_range(start_m, cur - 1, freq="M"):       # completed months
        path = root / sym / f"{m}.parquet"
        if path.exists():
            continue
        df = fetch_month_zip(sess, sym, str(m))
        if df is None:                                           # archive not out yet
            df = fetch_api(sess, sym, max(m.start_time.tz_localize("UTC"), first),
                           (m + 1).start_time.tz_localize("UTC"))
        if len(df):
            save(df, path)
            n_new += 1
    # current (partial) month: top up from last stored minute
    path = root / sym / f"{cur}.parquet"
    old = pd.read_parquet(path) if path.exists() else None
    start = (old.index[-1] + pd.Timedelta(minutes=1)) if old is not None \
        else max(cur.start_time.tz_localize("UTC"), first)
    new = fetch_api(sess, sym, start, now)
    if len(new):
        df = pd.concat([old, new]) if old is not None else new
        save(df[~df.index.duplicated(keep="last")].sort_index(), path)
    return sym, f"ok (+{n_new} months)"


# ------------------------------------------------------------------ quality
def quality_report(root, symbols):
    rows = []
    for sym in symbols:
        files = sorted((root / sym).glob("*.parquet"))
        if not files:
            continue
        idx = pd.concat([pd.read_parquet(f, columns=["close"]) for f in files]).index
        exp = int((idx[-1] - idx[0]).total_seconds() // 60) + 1
        rows.append({"symbol": sym, "first": idx[0], "last": idx[-1],
                     "rows": len(idx), "expected": exp,
                     "missing_pct": 100 * (1 - len(idx) / exp)})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------- loader
def load_panel(root, symbols, field="close", start=None, end=None, freq=None):
    """Wide DataFrame (time x symbol). freq e.g. '5min','1h','1D' resamples
    (close/open: last/first, high: max, low: min, volume fields: sum)."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    cols = {}
    for sym in symbols:
        files = sorted((Path(root) / sym).glob("*.parquet"))
        if not files:
            continue
        df = pd.concat([pd.read_parquet(f, columns=[field]) for f in files])[field]
        df = df.loc[start:end]
        if freq:
            df = getattr(df.resample(freq), "agg")(agg.get(field, "sum"))
        cols[sym] = df.astype("float32") if field != "close" else df
    return pd.DataFrame(cols)


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--since", default="2019-01", help="earliest month, YYYY-MM")
    ap.add_argument("--out", default="data")
    ap.add_argument("--symbols", default=None, help="txt file, one symbol (BTCUSDT) per line")
    ap.add_argument("--update", action="store_true", help="reuse saved universe.csv")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    root = Path(args.out) / "1m"
    root.mkdir(parents=True, exist_ok=True)
    uni_path = Path(args.out) / "universe.csv"
    sess = requests.Session()

    if args.symbols:
        symbols = Path(args.symbols).read_text().split()
    elif args.update and uni_path.exists():
        symbols = pd.read_csv(uni_path)["symbol"].tolist()
    else:
        uni = get_universe(sess, args.top)
        uni["selected_on"] = pd.Timestamp.now(tz="UTC").date()
        uni.to_csv(uni_path, index=False)
        symbols = uni["symbol"].tolist()
    print(f"{len(symbols)} symbols -> {root.resolve()}")

    with ThreadPoolExecutor(args.workers) as ex:
        futs = {ex.submit(collect_symbol, s, root, args.since): s for s in symbols}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                sym, msg = f.result()
            except Exception as e:
                sym, msg = futs[f], f"ERROR {e}"
            print(f"[{i}/{len(symbols)}] {sym}: {msg}", flush=True)

    q = quality_report(root, symbols)
    q.to_csv(Path(args.out) / "quality_report.csv", index=False)
    print(q.describe(include="all").to_string())


if __name__ == "__main__":
    main()