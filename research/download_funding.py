"""Faz 1: Binance USDT-M perpetual funding rate -> parquet.

Funding her 8 saatte bir tahakkuk eder. ~5 yillik veride 5400+ kayit/coin.
Idempotent: mevcut parquet varsa son fundingTime'dan sonrasini ceker.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

URL = "https://fapi.binance.com/fapi/v1/fundingRate"
LIMIT = 1000

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT"]
START_DATE = "2021-01-01"

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def parquet_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol}_funding.parquet"


def fetch_chunk(symbol: str, start_ms: int) -> pd.DataFrame:
    params = {"symbol": symbol, "startTime": start_ms, "limit": LIMIT}
    r = requests.get(URL, params=params, timeout=20)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["fundingRate"] = df["fundingRate"].astype(float)
    return df[["symbol", "fundingTime", "fundingRate"]]


def load_existing(symbol: str) -> pd.DataFrame | None:
    path = parquet_path(symbol)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if df.empty:
        return None
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], utc=True)
    return df.sort_values("fundingTime").reset_index(drop=True)


def download(symbol: str) -> None:
    existing = load_existing(symbol)
    if existing is not None:
        start_ms = int(existing["fundingTime"].max().timestamp() * 1000) + 1
        print(f"  resume from {pd.Timestamp(start_ms, unit='ms', tz='UTC')} ({len(existing)} rows on disk)")
    else:
        start_ms = int(pd.Timestamp(START_DATE, tz="UTC").timestamp() * 1000)
        print(f"  fresh download from {START_DATE}")

    now_ms = int(time.time() * 1000)
    chunks: list[pd.DataFrame] = []
    while start_ms < now_ms:
        try:
            chunk = fetch_chunk(symbol, start_ms)
        except requests.RequestException as e:
            print(f"  error: {e}; sleeping 5s")
            time.sleep(5)
            continue
        if chunk.empty:
            break
        chunks.append(chunk)
        last_ms = int(chunk["fundingTime"].iloc[-1].timestamp() * 1000)
        if last_ms <= start_ms:
            break
        start_ms = last_ms + 1
        time.sleep(0.1)

    if not chunks:
        print("  nothing new")
        return

    new_df = pd.concat(chunks, ignore_index=True)
    combined = pd.concat([existing, new_df], ignore_index=True) if existing is not None else new_df
    combined = combined.drop_duplicates(subset=["fundingTime"]).sort_values("fundingTime").reset_index(drop=True)
    combined.to_parquet(parquet_path(symbol), index=False)
    added = len(combined) - (len(existing) if existing is not None else 0)
    print(f"  wrote {parquet_path(symbol).name}: total={len(combined)} (+{added} new)")


def main() -> None:
    for symbol in SYMBOLS:
        print(symbol)
        download(symbol)


if __name__ == "__main__":
    main()
