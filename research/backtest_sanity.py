"""Faz 0 sanity: SMA(20)/SMA(50) crossover on BTCUSDT 1h.

Purpose is NOT to find an edge. Purpose is to prove the pipeline:
  parquet load -> vectorbt backtest -> Sharpe / MaxDD / WinRate / Trades
  with realistic fees (0.10%) and slippage (0.05%) baked in.

SMA cross is expected to lose to buy-and-hold. If it does, the pipeline works.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

DATA_DIR = Path(__file__).parent / "data"
SYMBOL = "BTCUSDT"
INTERVAL = "1h"
FAST = 20
SLOW = 50

FEE = 0.001        # 0.10% taker
SLIPPAGE = 0.0005  # 0.05%

BARS_PER_YEAR = 24 * 365  # 1h timeframe


def load_close() -> pd.Series:
    path = DATA_DIR / f"{SYMBOL}_{INTERVAL}.parquet"
    if not path.exists():
        raise SystemExit(f"missing {path} — run download_binance_data.py first")
    df = pd.read_parquet(path)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.sort_values("open_time").drop_duplicates("open_time").set_index("open_time")
    return df["close"].astype(float)


def report(label: str, pf: vbt.Portfolio, close: pd.Series) -> None:
    returns = pf.returns()
    total_return = float(pf.total_return())

    ann_factor = np.sqrt(BARS_PER_YEAR)
    mean_ret = float(returns.mean())
    std_ret = float(returns.std())
    sharpe = (mean_ret / std_ret) * ann_factor if std_ret > 0 else float("nan")

    max_dd = float(pf.max_drawdown())

    trades = pf.trades
    num_trades = int(trades.count())
    win_rate = float(trades.win_rate()) if num_trades > 0 else float("nan")
    avg_trade = float(trades.pnl.mean()) if num_trades > 0 else float("nan")

    print(f"\n=== {label} ===")
    print(f"period          : {close.index[0]}  ->  {close.index[-1]}")
    print(f"bars            : {len(close)}")
    print(f"total return    : {total_return * 100:+.2f}%")
    print(f"sharpe (annual) : {sharpe:.2f}")
    print(f"max drawdown    : {max_dd * 100:.2f}%")
    print(f"trades          : {num_trades}")
    if num_trades > 0:
        print(f"win rate        : {win_rate * 100:.1f}%")
        print(f"avg trade pnl   : ${avg_trade:.2f} (on $10k base)")


def main() -> None:
    close = load_close()
    print(f"loaded {len(close)} {INTERVAL} bars of {SYMBOL}")

    fast = vbt.MA.run(close, FAST).ma
    slow = vbt.MA.run(close, SLOW).ma
    entries = fast > slow
    exits = fast < slow

    # Strategy portfolio: SMA crossover, long-only.
    pf = vbt.Portfolio.from_signals(
        close,
        entries=entries,
        exits=exits,
        init_cash=10_000,
        fees=FEE,
        slippage=SLIPPAGE,
        freq="1h",
    )
    report(f"SMA({FAST}/{SLOW}) crossover, long-only", pf, close)

    # Buy-and-hold benchmark.
    bh_entries = pd.Series(False, index=close.index)
    bh_entries.iloc[0] = True
    bh_exits = pd.Series(False, index=close.index)
    bh = vbt.Portfolio.from_signals(
        close,
        entries=bh_entries,
        exits=bh_exits,
        init_cash=10_000,
        fees=FEE,
        slippage=SLIPPAGE,
        freq="1h",
    )
    report("Buy & Hold benchmark", bh, close)

    print("\nsanity check passed if the report above printed numeric values.")
    print("SMA losing to B&H is expected — purpose is pipeline validation, not finding edge.")


if __name__ == "__main__":
    main()
