"""Faz 2 v1: Time-series momentum, BTCUSDT 1h.

Klasik formulasyon (Moskowitz, Ooi, Pedersen 2012):
    signal(t) = sign( close[t] / close[t - lookback] - 1 )
    +1 long, -1 short, 0 flat.

Disiplin (PROJECT.md):
  - Tek coin (BTCUSDT)
  - Tek timeframe (1h)
  - Tek strateji
  - Tek parametre: lookback (gun cinsinden)
  - Sweep: [7, 14, 30, 60, 90]
  - In-sample: 2021-01-01 .. 2023-12-31
  - Out-of-sample: 2024-01-01 .. bugun
  - Verdict: bir lookback HEM in-sample HEM OOS'ta
    Buy&Hold'u yenmeli. Tek periodda yenen = sans veya overfit.

Maliyet varsayimi (BTC perp MEXC, koruyucu):
  - Taker fee: 0.05% per side
  - Slippage:  5 bps per side
  -> Round trip ~0.20%; vectorbt'a fees=0.0005 + slippage=0.0005
     (bunlar her order'a uygulaniyor, long->short flip ikisini de
     yiyor)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

DATA_DIR = Path(__file__).parent / "data"
OUT_JSON = Path(__file__).parent / "data" / "ts_momentum_results.json"

SYMBOL = "BTCUSDT"
INTERVAL = "1h"
BARS_PER_DAY = 24
BARS_PER_YEAR = 24 * 365

LOOKBACK_DAYS = [7, 14, 30, 60, 90]
IN_SAMPLE_END = pd.Timestamp("2024-01-01", tz="UTC")

FEE = 0.0005        # 0.05% taker per side
SLIPPAGE = 0.0005   # 5 bps per side
INIT_CASH = 10_000


def load_close() -> pd.Series:
    path = DATA_DIR / f"{SYMBOL}_{INTERVAL}.parquet"
    if not path.exists():
        raise SystemExit(f"missing {path} -- run download_binance_data.py first")
    df = pd.read_parquet(path)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.sort_values("open_time").drop_duplicates("open_time").set_index("open_time")
    return df["close"].astype(float)


def annualized_sharpe(returns: pd.Series) -> float:
    if len(returns) == 0:
        return float("nan")
    std = float(returns.std())
    if std == 0:
        return float("nan")
    return float(returns.mean() / std * np.sqrt(BARS_PER_YEAR))


def ts_momentum_signal(close: pd.Series, lookback_bars: int) -> pd.Series:
    past_return = close.pct_change(lookback_bars)
    signal = np.where(past_return > 0, 1, np.where(past_return < 0, -1, 0))
    return pd.Series(signal, index=close.index, dtype=int)


def run_strategy(close: pd.Series, lookback_bars: int) -> vbt.Portfolio:
    signal = ts_momentum_signal(close, lookback_bars)
    prev = signal.shift(1).fillna(0).astype(int)
    long_entries = (signal == 1) & (prev != 1)
    long_exits = (signal != 1) & (prev == 1)
    short_entries = (signal == -1) & (prev != -1)
    short_exits = (signal != -1) & (prev == -1)
    return vbt.Portfolio.from_signals(
        close,
        entries=long_entries,
        exits=long_exits,
        short_entries=short_entries,
        short_exits=short_exits,
        init_cash=INIT_CASH,
        fees=FEE,
        slippage=SLIPPAGE,
        freq="1h",
    )


def run_buy_hold(close: pd.Series) -> vbt.Portfolio:
    entries = pd.Series(False, index=close.index)
    entries.iloc[0] = True
    exits = pd.Series(False, index=close.index)
    return vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        init_cash=INIT_CASH,
        fees=FEE,
        slippage=SLIPPAGE,
        freq="1h",
    )


def split_stats(pf: vbt.Portfolio) -> dict:
    returns = pf.returns()
    is_mask = returns.index < IN_SAMPLE_END
    is_ret = returns[is_mask]
    oos_ret = returns[~is_mask]
    trades = pf.trades
    return {
        "full_sharpe": annualized_sharpe(returns),
        "in_sample_sharpe": annualized_sharpe(is_ret),
        "oos_sharpe": annualized_sharpe(oos_ret),
        "total_return_pct": float(pf.total_return() * 100),
        "max_drawdown_pct": float(pf.max_drawdown() * 100),
        "trades": int(trades.count()),
        "win_rate_pct": float(trades.win_rate() * 100) if trades.count() > 0 else None,
    }


def fmt(v, w=6, dp=2) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return f"{'NA':>{w}}"
    return f"{v:>{w}.{dp}f}"


def verdict_label(strategy: dict, bh: dict) -> str:
    is_strat = strategy["in_sample_sharpe"]
    oos_strat = strategy["oos_sharpe"]
    is_bh = bh["in_sample_sharpe"]
    oos_bh = bh["oos_sharpe"]
    beats_is = is_strat is not None and is_bh is not None and is_strat > is_bh
    beats_oos = oos_strat is not None and oos_bh is not None and oos_strat > oos_bh
    if beats_is and beats_oos:
        return "BOTH (sag kalan)"
    if beats_is:
        return "IS only (overfit suphesi)"
    if beats_oos:
        return "OOS only (sans suphesi)"
    return "NONE"


def main() -> None:
    close = load_close()
    print(f"Loaded {len(close)} {INTERVAL} bars BTCUSDT")
    print(f"  Range : {close.index[0]}  ->  {close.index[-1]}")
    print(f"  IS end: {IN_SAMPLE_END}")
    print()

    bh = run_buy_hold(close)
    bh_stats = split_stats(bh)
    print("=== Buy & Hold benchmark ===")
    print(f"  IS  Sharpe: {fmt(bh_stats['in_sample_sharpe'])}")
    print(f"  OOS Sharpe: {fmt(bh_stats['oos_sharpe'])}")
    print(f"  Full      : {fmt(bh_stats['full_sharpe'])}  "
          f"Return {fmt(bh_stats['total_return_pct'])}%  MaxDD {fmt(bh_stats['max_drawdown_pct'])}%")
    print()

    print("=== Time-series momentum lookback sweep (sign-of-past-return, L/S) ===")
    print(f"{'days':>5} | {'bars':>5} | {'IS':>6} | {'OOS':>6} | {'Full':>6} | "
          f"{'Ret%':>7} | {'MaxDD%':>7} | {'Trades':>6} | {'WR%':>5} | Verdict")
    print("-" * 100)

    rows = []
    for lb_days in LOOKBACK_DAYS:
        lb_bars = lb_days * BARS_PER_DAY
        pf = run_strategy(close, lb_bars)
        s = split_stats(pf)
        v = verdict_label(s, bh_stats)
        print(
            f"{lb_days:>5} | {lb_bars:>5} | "
            f"{fmt(s['in_sample_sharpe'])} | {fmt(s['oos_sharpe'])} | {fmt(s['full_sharpe'])} | "
            f"{fmt(s['total_return_pct'], 7)} | {fmt(s['max_drawdown_pct'], 7)} | "
            f"{s['trades']:>6} | {fmt(s['win_rate_pct'], 5, 1)} | {v}"
        )
        rows.append({"lookback_days": lb_days, "lookback_bars": lb_bars, **s, "verdict": v})

    survivors = [r for r in rows if r["verdict"].startswith("BOTH")]
    print()
    if survivors:
        print(f"Hem IS hem OOS'ta B&H'yi yenen lookback(ler): "
              f"{[r['lookback_days'] for r in survivors]}")
        print("Sonraki adim: bu lookback'lerin tutarliligi (timeframe robustness, "
              "farkli coin'lerde test). Ayrica Sharpe >= 0.8 esigine bak.")
    else:
        print("Hicbir lookback hem IS hem OOS'ta B&H'yi yenmedi.")
        print("Sonuc: bu basit formulasyonda TS momentum BTCUSDT 1h'de edge gozukmuyor.")
        print("Sonraki adim secenekleri:")
        print("  1) Farkli timeframe (4h, 1d) -- 1h fazla gurultulu olabilir")
        print("  2) Volatility-targeted version -- pozisyon = sign * (target_vol / realized_vol)")
        print("  3) Farkli edge ailesine gec -- funding rate carry veya cross-sectional momentum")

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "in_sample_end": IN_SAMPLE_END.isoformat(),
        "fee_per_side": FEE,
        "slippage_per_side": SLIPPAGE,
        "buy_hold": bh_stats,
        "results": rows,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nWrote {OUT_JSON}")


if __name__ == "__main__":
    main()
