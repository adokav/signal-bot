"""Faz 0: Binance public klines -> parquet.

Coverage: BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT x {1h, 4h, 1d} from 2021-01-01 UTC.
Idempotent: if research/data/<SYMBOL>_<TF>.parquet exists, only fetch bars after
the latest stored open_time.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

BINANCE_BASE = "https://api.binance.com"
KLINES_URL = f"{BINANCE_BASE}/api/v3/klines"
LIMIT = 1000

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT"]
INTERVALS = ["1h", "4h", "1d"]
START_DATE = "2021-01-01"

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

INTERVAL_MS = {
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}

COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]
KEEP = ["open_time", "open", "high", "low", "close", "volume", "trades"]


def parquet_path(symbol: str, interval: str) -> Path:
    return DATA_DIR / f"{symbol}_{interval}.parquet"


def fetch_chunk(symbol: str, interval: str, start_ms: int) -> pd.DataFrame:
    params = {"symbol": symbol, "interval": interval, "startTime": start_ms, "limit": LIMIT}
    r = requests.get(KLINES_URL, params=params, timeout=20)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return pd.DataFrame(columns=KEEP)
    df = pd.DataFrame(rows, columns=COLUMNS)
    df = df[KEEP].copy()
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype(float)
    df["trades"] = df["trades"].astype("int64")
    return df


def load_existing(symbol: str, interval: str) -> pd.DataFrame | None:
    path = parquet_path(symbol, interval)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if df.empty:
        return None
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    return df.sort_values("open_time").reset_index(drop=True)


def download(symbol: str, interval: str) -> None:
    existing = load_existing(symbol, interval)
    if existing is not None:
        last_open = existing["open_time"].max()
        start_ms = int(last_open.timestamp() * 1000) + INTERVAL_MS[interval]
        print(f"  resume from {pd.Timestamp(start_ms, unit='ms', tz='UTC')} ({len(existing)} bars on disk)")
    else:
        start_ms = int(pd.Timestamp(START_DATE, tz="UTC").timestamp() * 1000)
        print(f"  fresh download from {START_DATE}")

    now_ms = int(time.time() * 1000)
    chunks: list[pd.DataFrame] = []
    while start_ms < now_ms:
        try:
            chunk = fetch_chunk(symbol, interval, start_ms)
        except requests.RequestException as e:
            print(f"  error: {e}; sleeping 5s and retrying")
            time.sleep(5)
            continue

        if chunk.empty:
            break
        chunks.append(chunk)
        last_open_ms = int(chunk["open_time"].iloc[-1].timestamp() * 1000)
        if last_open_ms <= start_ms:
            break
        start_ms = last_open_ms + INTERVAL_MS[interval]
        time.sleep(0.1)

    if not chunks:
        print(f"  nothing new for {symbol} {interval}")
        return

    new_df = pd.concat(chunks, ignore_index=True)
    if existing is not None:
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    else:
        combined = new_df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

    combined.to_parquet(parquet_path(symbol, interval), index=False)
    added = len(combined) - (len(existing) if existing is not None else 0)
    print(f"  wrote {parquet_path(symbol, interval).name}: total={len(combined)} (+{added} new)")


def main() -> None:
    for symbol in SYMBOLS:
        for interval in INTERVALS:
            print(f"{symbol} {interval}")
            download(symbol, interval)


if __name__ == "__main__":
    main()
