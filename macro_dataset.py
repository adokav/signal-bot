"""Daily point-in-time macro/BTC replay dataset construction.

No factor is scored here. The dataset contains transparent factor snapshots and
future labels only, so later walk-forward validation can decide whether any
factor deserves influence over live trading.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable

from macro_data import MacroObservation
from macro_factors import build_factor_snapshot
from macro_replay import PriceBar, ReplayRow, build_replay_row_from_bars


def _day(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def daily_replay_cuts(
    *,
    start: str | date | datetime,
    end: str | date | datetime,
) -> tuple[datetime, ...]:
    """UTC end-of-day cuts; end is inclusive."""
    start_day, end_day = _day(start), _day(end)
    if end_day < start_day:
        raise ValueError("dataset end precedes start")
    cuts: list[datetime] = []
    current = start_day
    while current <= end_day:
        cuts.append(datetime.combine(current, time(23, 59, 59), tzinfo=timezone.utc))
        current += timedelta(days=1)
    return tuple(cuts)


def build_daily_replay_rows(
    observations: Iterable[MacroObservation],
    bars: Iterable[PriceBar],
    *,
    start: str | date | datetime,
    end: str | date | datetime,
) -> tuple[ReplayRow, ...]:
    observation_rows = tuple(observations)
    price_rows = tuple(bars)
    replay: list[ReplayRow] = []
    for cut in daily_replay_cuts(start=start, end=end):
        snapshot = build_factor_snapshot(observation_rows, as_of=cut)
        replay.append(build_replay_row_from_bars(snapshot, price_rows))
    return tuple(replay)


def _outcome_dict(outcome) -> dict:
    return asdict(outcome)


def replay_row_to_dict(row: ReplayRow) -> dict:
    return {
        "as_of": row.as_of,
        "factors": dict(row.factors),
        "factor_complete": bool(row.factor_complete),
        "outcomes": {key: _outcome_dict(value) for key, value in row.outcomes.items()},
    }


def write_replay_jsonl(rows: Iterable[ReplayRow], path: str | Path) -> int:
    """Atomically write an immutable research dataset snapshot."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = tuple(rows)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in data:
            handle.write(json.dumps(replay_row_to_dict(row), sort_keys=True) + "\n")
    tmp.replace(target)
    return len(data)
