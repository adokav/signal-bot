"""Build a conservative historical macro/BTC replay dataset.

Example:
    python scripts/build_macro_history.py --start 2017-08-18 --end 2026-08-24

Requires FRED_API_KEY. Uses public Binance spot 1h BTCUSDT bars. This is an
offline research command and never changes live bot state or trading decisions.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path

from macro_backfill import BinanceHourlyBackfill, FredRevisionBackfill
from macro_dataset import build_daily_replay_rows, write_replay_jsonl


def _parse_day(value: str) -> date:
    return date.fromisoformat(value)


def _atomic_jsonl_dicts(rows, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), sort_keys=True) + "\n")
    tmp.replace(path)


def _guard_research_path(path: Path) -> None:
    if path.name == "macro_observations.jsonl":
        raise ValueError("historical reconstruction must not overwrite the live first-seen archive")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2017-08-18")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--fred-output", default="research/data/macro_alfred_reconstructed.jsonl")
    parser.add_argument("--btc-output", default="research/data/btcusdt_1h_bars.jsonl")
    parser.add_argument("--replay-output", default="research/data/macro_btc_daily_replay.jsonl")
    args = parser.parse_args()

    if not os.getenv("FRED_API_KEY"):
        raise SystemExit("FRED_API_KEY is required")

    start_day, end_day = _parse_day(args.start), _parse_day(args.end)
    if end_day < start_day:
        raise SystemExit("--end must be on/after --start")

    fred_path = Path(args.fred_output)
    btc_path = Path(args.btc_output)
    replay_path = Path(args.replay_output)
    _guard_research_path(fred_path)

    # Pull extra macro history so 5-observation features exist at the first
    # requested replay cut. Pull extra BTC history/future so the anchor and 7d
    # outcome labels are fully measurable at the dataset edges.
    macro_start = start_day - timedelta(days=30)
    fred = FredRevisionBackfill()
    observations = fred.fetch_core(start=macro_start, end=end_day)
    _atomic_jsonl_dicts(observations, fred_path)

    price_start = datetime.combine(start_day - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    price_end = datetime.combine(end_day + timedelta(days=8), datetime.max.time(), tzinfo=timezone.utc)
    bars = BinanceHourlyBackfill().fetch(start=price_start, end=price_end)
    _atomic_jsonl_dicts(bars, btc_path)

    replay = build_daily_replay_rows(observations, bars, start=start_day, end=end_day)
    write_replay_jsonl(replay, replay_path)

    complete = sum(row.factor_complete for row in replay)
    with_labels = sum(bool(row.outcomes) for row in replay)
    print(f"macro observations: {len(observations)} -> {fred_path}")
    print(f"BTC hourly bars: {len(bars)} -> {btc_path}")
    print(f"replay rows: {len(replay)} (complete factors={complete}, labeled={with_labels}) -> {replay_path}")
    print("No score/regime/trade decision was generated.")


if __name__ == "__main__":
    main()
