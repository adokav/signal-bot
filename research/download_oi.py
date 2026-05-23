"""Faz 1: Binance USDT-M perpetual open interest history -> parquet.

UYARI / KISIT:
Binance public OI history endpoint sadece son ~30 gunlugu doner.
Bu Faz 2 backtest'i icin 5 yillik tarihsel OI verisi anlamina GELMEZ.
Bu script'in v1 amaci:
  - ileriye donuk gunluk birikim icin sade bir veri toplayici
  - "OI feature'una gercekten ihtiyac var mi?" sorusunu cevaplayana
    kadar ucretli saglayici taahhudu vermemek

Gercek 5 yillik tarihsel OI istenirse Coinalyze, Coinglass, CryptoQuant
gibi ucretli saglayicilar gerekir. PROJECT.md "Acik Sorular" bolumune
karar olarak eklenmeli.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

URL = "https://fapi.binance.com/futures/data/openInterestHist"
LIMIT = 500
PERIOD = "1h"
LOOKBACK_DAYS = 30

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT"]

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def parquet_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol}_oi.parquet"


def load_existing(symbol: str) -> pd.DataFrame | None:
    path = parquet_path(symbol)
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if df.empty:
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def fetch_chunk(symbol: str, start_ms: int) -> pd.DataFrame:
    params = {"symbol": symbol, "period": PERIOD, "limit": LIMIT, "startTime": start_ms}
    r = requests.get(URL, params=params, timeout=20)
    r.raise_for_status()
    rows = r.json()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)
    df["sumOpenInterestValue"] = df["sumOpenInterestValue"].astype(float)
    df["symbol"] = symbol
    return df[["symbol", "timestamp", "sumOpenInterest", "sumOpenInterestValue"]]


def download(symbol: str) -> None:
    existing = load_existing(symbol)
    if existing is not None:
        start_ms = int(existing["timestamp"].max().timestamp() * 1000) + 1
        print(f"  resume from {pd.Timestamp(start_ms, unit='ms', tz='UTC')} ({len(existing)} rows on disk)")
    else:
        start_ms = int(time.time() * 1000) - LOOKBACK_DAYS * 24 * 60 * 60 * 1000
        print(f"  fresh download (last {LOOKBACK_DAYS} days; API limit)")

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
        last_ms = int(chunk["timestamp"].iloc[-1].timestamp() * 1000)
        if last_ms <= start_ms:
            break
        start_ms = last_ms + 60_000
        time.sleep(0.1)

    if not chunks:
        print("  nothing new")
        return

    new_df = pd.concat(chunks, ignore_index=True)
    combined = pd.concat([existing, new_df], ignore_index=True) if existing is not None else new_df
    combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    combined.to_parquet(parquet_path(symbol), index=False)
    added = len(combined) - (len(existing) if existing is not None else 0)
    print(f"  wrote {parquet_path(symbol).name}: total={len(combined)} (+{added} new)")


def main() -> None:
    for symbol in SYMBOLS:
        print(symbol)
        download(symbol)


if __name__ == "__main__":
    main()
