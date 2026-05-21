"""Faz 1: order book snapshot tabanli slippage tahmini.

Binance public depth endpoint'inden top 1000 seviyeyi cek, alis/satis
tarafinda farkli pozisyon buyuklukleri icin walk-the-book vwap hesapla;
vwap ile mid-price arasi fark = anlik slippage tahmini (basis points).

KISIT: Bu anlik snapshot. Tarihsel order book ucretli (Tardis vb.) ve
disk acidan agir. v1 yaklasimi: snapshot tablosu - statik referans
olarak Faz 2 backtest'te kullanilir. Kucuk pozisyonlarda iyi
approximation, kapasite limitine yaklasinca yanilir.

Iki ayri kullanim:
  1. python cost_model.py     -> SYMBOLS icin snapshot + tablo + JSON
  2. from cost_model import estimate_slippage_bps
     estimate_slippage_bps("BTCUSDT", "buy", 5_000) -> float bps
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests

URL = "https://api.binance.com/api/v3/depth"
BOOK_LIMIT = 1000

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT"]
SIZES_USD = [100, 1_000, 10_000, 50_000, 100_000]

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOT_PATH = DATA_DIR / "cost_model_snapshot.json"


def fetch_book(symbol: str, limit: int = BOOK_LIMIT) -> dict:
    r = requests.get(URL, params={"symbol": symbol, "limit": limit}, timeout=15)
    r.raise_for_status()
    return r.json()


def walk_book(levels: list[list[str]], target_usd: float) -> tuple[float, float]:
    """Walk a side until target_usd consumed. Returns (vwap, filled_usd).

    levels = [[price_str, qty_str], ...] in book order (bids desc, asks asc).
    Eger kitap target'i karsilayamayacaksa filled_usd < target doner.
    """
    spent_usd = 0.0
    spent_units = 0.0
    for price_str, qty_str in levels:
        price = float(price_str)
        qty = float(qty_str)
        level_usd = price * qty
        if spent_usd + level_usd >= target_usd:
            need_usd = target_usd - spent_usd
            need_qty = need_usd / price
            spent_units += need_qty
            spent_usd += need_usd
            return spent_usd / spent_units, spent_usd
        spent_units += qty
        spent_usd += level_usd
    if spent_units == 0:
        return 0.0, 0.0
    return spent_usd / spent_units, spent_usd


def estimate(symbol: str) -> dict:
    book = fetch_book(symbol)
    bids = book["bids"]
    asks = book["asks"]
    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid = (best_bid + best_ask) / 2
    row = {
        "symbol": symbol,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": (best_ask - best_bid) / mid * 10_000,
        "buy_slippage_bps": {},
        "sell_slippage_bps": {},
        "buy_fill_pct": {},
        "sell_fill_pct": {},
    }
    for size in SIZES_USD:
        buy_vwap, buy_filled = walk_book(asks, size)
        sell_vwap, sell_filled = walk_book(bids, size)
        row["buy_slippage_bps"][str(size)] = (
            (buy_vwap - mid) / mid * 10_000 if buy_vwap > 0 else None
        )
        row["sell_slippage_bps"][str(size)] = (
            (mid - sell_vwap) / mid * 10_000 if sell_vwap > 0 else None
        )
        row["buy_fill_pct"][str(size)] = buy_filled / size * 100 if size else 0.0
        row["sell_fill_pct"][str(size)] = sell_filled / size * 100 if size else 0.0
    return row


def load_snapshot() -> dict | None:
    if not SNAPSHOT_PATH.exists():
        return None
    return json.loads(SNAPSHOT_PATH.read_text())


def estimate_slippage_bps(symbol: str, side: str, size_usd: float) -> float | None:
    """Faz 2'de backtest'in cagiracagi helper.

    Cached snapshot varsa onu kullanir, yoksa canli cag yapar.
    side: "buy" or "sell". size_usd: yaklasik pozisyon notional.

    Returns slippage bps (positive = worse than mid). None = veri yok.
    """
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be buy or sell, got {side!r}")

    snapshot = load_snapshot()
    row = None
    if snapshot:
        for r in snapshot.get("rows", []):
            if r.get("symbol") == symbol:
                row = r
                break
    if row is None:
        row = estimate(symbol)

    table = row["buy_slippage_bps"] if side == "buy" else row["sell_slippage_bps"]
    sizes = sorted(int(s) for s in table.keys())
    if not sizes:
        return None

    if size_usd <= sizes[0]:
        return table[str(sizes[0])]
    if size_usd >= sizes[-1]:
        return table[str(sizes[-1])]

    for i in range(len(sizes) - 1):
        lo, hi = sizes[i], sizes[i + 1]
        if lo <= size_usd <= hi:
            v_lo = table[str(lo)]
            v_hi = table[str(hi)]
            if v_lo is None or v_hi is None:
                return v_hi if v_hi is not None else v_lo
            ratio = (size_usd - lo) / (hi - lo)
            return v_lo + (v_hi - v_lo) * ratio
    return table[str(sizes[-1])]


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for symbol in SYMBOLS:
        print(symbol)
        try:
            row = estimate(symbol)
        except requests.RequestException as e:
            print(f"  error: {e}")
            continue
        print(f"  mid={row['mid']:.4f}  spread={row['spread_bps']:.2f} bps")
        for size in SIZES_USD:
            buy = row["buy_slippage_bps"][str(size)]
            sell = row["sell_slippage_bps"][str(size)]
            buy_s = f"{buy:.2f}" if buy is not None else "  NA"
            sell_s = f"{sell:.2f}" if sell is not None else "  NA"
            print(f"  ${size:>7}: buy {buy_s} bps | sell {sell_s} bps")
        results.append(row)

    payload = {
        "snapshot_at_utc": datetime.now(timezone.utc).isoformat(),
        "book_depth_levels": BOOK_LIMIT,
        "sizes_usd": SIZES_USD,
        "rows": results,
    }
    SNAPSHOT_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
