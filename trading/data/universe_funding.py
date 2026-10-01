"""Point-in-time perpetual funding history for a liquid spot universe. Research only.

The signal-quality study (docs/SIGNAL_QUALITY.md) asks whether crowded longs,
measured by perpetual funding, make worse long signals. For every spot pair in
a ``binance_universe`` manifest this:

1. maps the spot pair to its USDⓈ-M perpetual (``1000PEPEUSDT`` -> ``PEPEUSDT``)
   with the carry study's mapping; a spot pair claimed by two perpetuals is
   ambiguous and left out;
2. downloads the monthly fundingRate files for the months the spot pair was
   downloaded;
3. keeps rows settled at or before ``decision_at``.

A pair without a perpetual stays absent: missing funding is reported as
missing, never as zero (AGENTS.md §2). Failure policy as in
``binance_universe``: 404 means "not published"; any other failure is retried
and then aborts the build.

Files that fingerprint pre-registered trials are imported, never modified.
No order authority (AGENTS.md §4).
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Mapping

import requests

from trading.data import binance_carry_universe as carry
from trading.data.binance_vision import _session


log = logging.getLogger(__name__)

COLUMNS = ("spot_symbol", "perp_symbol", "funding_time", "rate")


def perp_for_spot(perps: Iterable[str], spots: Iterable[str]) -> tuple[dict[str, str], list[str]]:
    """spot -> perp for unambiguous pairs, and the spot pairs two perpetuals claim."""

    mapping = carry.map_perps_to_spot(perps, spots).mapped
    claims: dict[str, list[str]] = {}
    for perp, spot in mapping.items():
        claims.setdefault(spot, []).append(perp)
    unique = {spot: perps[0] for spot, perps in claims.items() if len(perps) == 1}
    return unique, sorted(spot for spot, perps in claims.items() if len(perps) > 1)


def fetch_funding(session: requests.Session, perp: str, months: Iterable[str], *, decision_at: int) -> list[tuple[int, float]]:
    rows: set[tuple[int, float]] = set()
    for month in months:
        year, mon = (int(x) for x in month.split("-"))
        text = carry._csv(session, carry.funding_url(perp, year, mon), f"{perp} funding {month}")
        if text is not None:
            rows.update((t, rate) for t, rate in carry.parse_funding(text) if t <= decision_at)
    times = [t for t, _ in rows]
    if len(times) != len(set(times)):
        # two different rates for one settlement: the file is ambiguous, keep none of it
        raise carry.DataQualityError(f"{perp}: conflicting funding rows")
    return sorted(rows)


def build_funding(
    universe_dir: Path,
    out_dir: Path,
    *,
    decision_at: int,
    session: requests.Session | None = None,
    workers: int = 16,
    perps: Iterable[str] | None = None,
) -> dict:
    manifest = json.loads((universe_dir / "manifest.json").read_text("utf-8"))
    symbols: Mapping[str, dict] = manifest["symbols"]
    if session is None:
        session = _session(timeout=60)
        session.mount("https://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=64))
    if perps is None:
        perps = carry.list_symbols(session, carry.PERP_PREFIX)
        if len(perps) < carry.MIN_PERPS:
            raise carry.DataQualityError(f"only {len(perps)} perpetuals listed; listing looks incomplete")
    mapping, ambiguous = perp_for_spot(perps, symbols)

    def one(spot: str) -> tuple[str, list[tuple[int, float]]]:
        return spot, fetch_funding(session, mapping[spot], symbols[spot]["months"], decision_at=decision_at)

    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "funding.csv.gz.tmp"
    counts: dict[str, int] = {}
    with gzip.open(tmp, "wt", newline="", encoding="utf-8") as handle, ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for spot, rows in pool.map(one, sorted(mapping)):
            counts[spot] = len(rows)
            writer.writerows((spot, mapping[spot], t, repr(rate)) for t, rate in rows)
    tmp.replace(out_dir / "funding.csv.gz")
    summary = {
        "schema": "universe-funding/v1",
        "decision_at": decision_at,
        "universe_window": manifest.get("window"),
        "spot_pairs": len(symbols),
        "with_perp": len(mapping),
        "with_rows": sum(1 for n in counts.values() if n),
        "without_perp": sorted(set(symbols) - set(mapping) - set(ambiguous)),
        "ambiguous": ambiguous,
        "rows": sum(counts.values()),
        "perp_of": mapping,
        "can_authorize_trade": False,
    }
    tmp_manifest = out_dir / "manifest.json.tmp"
    tmp_manifest.write_text(json.dumps(summary, indent=1), "utf-8")
    tmp_manifest.replace(out_dir / "manifest.json")
    return summary


def load_funding(funding_dir: Path) -> dict[str, tuple[list[int], list[float]]]:
    """spot symbol -> (settlement times, rates), chronological."""

    out: dict[str, tuple[list[int], list[float]]] = {}
    with gzip.open(funding_dir / "funding.csv.gz", "rt", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        if tuple(next(reader)) != COLUMNS:
            raise carry.DataQualityError("unexpected funding columns")
        for spot, _, t, rate in reader:
            times, rates = out.setdefault(spot, ([], []))
            times.append(int(t))
            rates.append(float(rate))
    for spot, (times, rates) in out.items():
        order = sorted(range(len(times)), key=times.__getitem__)
        out[spot] = ([times[k] for k in order], [rates[k] for k in order])
    return out


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Perpetual funding history for a liquid spot universe.")
    parser.add_argument("--universe-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--decision-at", type=int, default=None, help="UTC seconds; default now")
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    summary = build_funding(args.universe_dir, args.out, decision_at=int(args.decision_at or time.time()))
    print(json.dumps({k: v for k, v in summary.items() if k not in {"perp_of", "without_perp"}}, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
