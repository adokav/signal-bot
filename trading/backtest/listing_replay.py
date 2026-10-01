"""New-listing cohort study: does buying a new Binance spot listing lose money?

Why: practitioner reports say first-day buyers of new CEX listings lose most
of their money over the following months. The live MEXC new-listing radar
labels candidates HOT / BUILDING / WATCH, and those labels have never been
checked against history. This study asks the loss-avoidance question
(pre-registered, docs/LISTING_REPLAY_REPORT.md):

    Buying every genuinely new Binance USDT listing at a fixed time after
    its first trade (+1h, +24h, +7d) and holding 30 or 90 days: is the mean
    return below zero, and below holding BTC over the same window?

Decisions use **gross** returns (no fees, no slippage). For a claim that
something loses money, ignoring costs is the conservative choice: costs can
only make it worse. Net figures are reported alongside.

Point in time: the first trade time is the open of the first 1h candle with
volume; entry is the open of the 1h candle at first trade + offset; every
feature used for a diagnostic split is computed from candles that closed
before entry. A listing whose data stops before exit (delisting) exits at
its last close. Observations whose exit is after the end of the data are
left out, not truncated.

Research only; no order authority anywhere (AGENTS.md §4, §9).
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np


HOUR = 3_600
DAY = 86_400
ENTRY_OFFSETS = {"1h": HOUR, "24h": DAY, "7d": 7 * DAY}
HORIZONS = {"30d": 30 * DAY, "90d": 90 * DAY}
FAMILY_ALPHA = 0.05 / (len(ENTRY_OFFSETS) * len(HORIZONS))
MIN_LISTINGS = 100
CROWDED_PUMP_PCT = 80.0       # live radar: first pump > 80% -> CROWDED ("kovalama yasak")
HEAVY_SELL_PCT = -35.0        # live radar: < -35% -> "listeleme sonrası ağır satış"
VOLUME_BANDS = (5e6, 50e6)    # quote volume traded before entry (USD), fixed in advance
PRICE_TOLERANCE = 6 * HOUR    # exchange maintenance leaves 1-4h holes; a longer hole is unresolvable
BENCHMARK = "BTCUSDT"

REPO_ROOT = Path(__file__).resolve().parents[2]
FINGERPRINT_FILES = (
    "acce_unified/cex.py",
    "trading/data/binance_vision.py",
    "trading/data/binance_universe.py",
    "trading/data/binance_history_identity.py",
    "trading/data/binance_listings.py",
    "trading/backtest/listing_replay.py",
)


@dataclass(frozen=True)
class ListingCosts:
    fee_bps_per_side: float = 7.5
    slippage_bps_per_side: float = 25.0   # new listings: wide spreads and thin books

    def round_trip_pct(self, *, fee_mult: float = 1.0, slip_mult: float = 1.0) -> float:
        return 2.0 * (self.fee_bps_per_side * fee_mult + self.slippage_bps_per_side * slip_mult) / 100.0


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    open_time: np.ndarray
    open: np.ndarray
    close: np.ndarray
    quote_volume: np.ndarray
    rejected: int = 0


def clean_series(cols: Mapping[str, Sequence[float]]) -> Series:
    """Hour-aligned, strictly increasing, finite, positive, OHLC-consistent rows; the rest is counted."""

    t = np.asarray(cols["open_time"], dtype=np.int64)
    o, h, low, c, qv = (np.asarray(cols[k], dtype=float) for k in ("open", "high", "low", "close", "quote_volume"))
    with np.errstate(invalid="ignore"):
        ok = (
            (t % HOUR == 0) & np.isfinite(o) & np.isfinite(h) & np.isfinite(low) & np.isfinite(c) & np.isfinite(qv)
            & (o > 0) & (h > 0) & (low > 0) & (c > 0) & (qv >= 0)
            & (h >= np.maximum(o, c)) & (low <= np.minimum(o, c))
        )
    idx = np.nonzero(ok)[0]
    idx = idx[np.argsort(t[idx], kind="stable")]
    keep = np.ones(len(idx), dtype=bool)
    keep[1:] = t[idx][1:] != t[idx][:-1]           # duplicates: keep the first
    idx = idx[keep]
    return Series(t[idx], o[idx], c[idx], qv[idx], rejected=int(len(t) - len(idx)))


def load_dataset(data_dir: Path) -> tuple[dict[str, Series], Series, dict]:
    import pandas as pd

    manifest = json.loads((data_dir / "manifest.json").read_text("utf-8"))
    columns = ("open_time", "open", "high", "low", "close", "quote_volume")

    def read(symbol: str) -> Series:
        frame = pd.read_parquet(data_dir / "1h" / f"{symbol}.parquet", columns=list(columns))
        return clean_series({k: frame[k].to_numpy() for k in columns})

    listings = {symbol: read(symbol) for symbol in sorted(manifest["listings"])}
    return listings, read(BENCHMARK), manifest


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    symbol: str
    entry: str
    horizon: str
    first_trade: int
    entry_time: int
    gross_pct: float
    btc_pct: float
    pump_pct: float               # entry price vs the first trade's open (known at entry)
    volume_before_entry: float    # quote volume of candles closed before entry (known at entry)
    delisted: bool

    @property
    def excess_pct(self) -> float:
        return self.gross_pct - self.btc_pct

    @property
    def month(self) -> int:
        dt = datetime.fromtimestamp(self.first_trade, tz=timezone.utc)
        return dt.year * 12 + dt.month - 1


def _price_at(series: Series, when: int) -> float | None:
    """Open of the first candle opening in [when, when + PRICE_TOLERANCE]; None if there is none."""

    i = int(np.searchsorted(series.open_time, when))
    if i < len(series.open_time) and series.open_time[i] - when <= PRICE_TOLERANCE:
        return float(series.open[i])
    return None


@dataclass
class ObservationSet:
    rows: list[Observation] = field(default_factory=list)
    no_trade: int = 0             # listings with no candle that has volume
    ended_before_entry: dict[str, int] = field(default_factory=dict)
    incomplete_window: dict[str, int] = field(default_factory=dict)
    unresolvable: dict[str, int] = field(default_factory=dict)


def observations(listings: Mapping[str, Series], btc: Series, *, data_end: int) -> ObservationSet:
    out = ObservationSet()
    for symbol in sorted(listings):
        s = listings[symbol]
        traded = np.nonzero(s.quote_volume > 0)[0]
        if not len(traded):
            out.no_trade += 1
            continue
        i0 = int(traded[0])
        first, first_open = int(s.open_time[i0]), float(s.open[i0])
        last_open, last_close = int(s.open_time[-1]), float(s.close[-1])
        for entry, offset in ENTRY_OFFSETS.items():
            entry_time = first + offset
            if last_open < entry_time:
                out.ended_before_entry[entry] = out.ended_before_entry.get(entry, 0) + 1
                continue
            entry_price = _price_at(s, entry_time)
            btc_entry = _price_at(btc, entry_time)
            before = (s.open_time >= first) & (s.open_time + HOUR <= entry_time)
            for horizon, length in HORIZONS.items():
                key = f"{entry}@{horizon}"
                exit_time = entry_time + length
                if exit_time > data_end:
                    out.incomplete_window[key] = out.incomplete_window.get(key, 0) + 1
                    continue
                btc_exit = _price_at(btc, exit_time)
                if last_open < exit_time:
                    exit_price, delisted = last_close, True
                else:
                    exit_price, delisted = _price_at(s, exit_time), False
                if entry_price is None or exit_price is None or btc_entry is None or btc_exit is None:
                    out.unresolvable[key] = out.unresolvable.get(key, 0) + 1
                    continue
                out.rows.append(Observation(
                    symbol=symbol, entry=entry, horizon=horizon, first_trade=first, entry_time=entry_time,
                    gross_pct=(exit_price / entry_price - 1.0) * 100.0,
                    btc_pct=(btc_exit / btc_entry - 1.0) * 100.0,
                    pump_pct=(entry_price / first_open - 1.0) * 100.0,
                    volume_before_entry=float(s.quote_volume[before].sum()),
                    delisted=delisted,
                ))
    return out


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def month_cluster_ci(values: Sequence[float], months: Sequence[int], *, alpha: float, n_resamples: int = 4000,
                     seed: int = 0) -> tuple[float, float] | None:
    """Percentile CI of the mean, resampling whole listing months (listings in a month share the market)."""

    if len(values) < 2:
        return None
    order: dict[int, int] = {}
    labels = np.array([order.setdefault(int(m), len(order)) for m in months])
    sums = np.bincount(labels, weights=np.asarray(values, dtype=float))
    counts = np.bincount(labels).astype(float)
    if len(sums) < 2:
        return None
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(sums), size=(n_resamples, len(sums)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


@dataclass(frozen=True)
class GroupStats:
    name: str
    n: int
    months: int
    delisted: int
    mean_gross_pct: float | None
    median_gross_pct: float | None
    share_positive: float | None
    mean_btc_pct: float | None
    mean_excess_pct: float | None
    share_beating_btc: float | None
    gross_ci_family: tuple[float, float] | None
    excess_ci_family: tuple[float, float] | None
    first_half_gross_pct: float | None
    second_half_gross_pct: float | None
    first_half_excess_pct: float | None
    second_half_excess_pct: float | None
    mean_net_pct: float | None
    stressed_mean_net_pct: float | None
    verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def _mean(values: Sequence[float]) -> float | None:
    return float(np.mean(values)) if len(values) else None


def group_stats(name: str, rows: Sequence[Observation], costs: ListingCosts, *, decision_group: bool = True,
                seed: int = 0) -> GroupStats:
    """AVOID_CONFIRMED needs >= 100 listings, family-wise month-cluster upper bounds below zero for both
    the gross return and the return in excess of BTC, and both means negative in each chronological half."""

    rows = sorted(rows, key=lambda r: (r.first_trade, r.symbol))
    gross = [r.gross_pct for r in rows]
    excess = [r.excess_pct for r in rows]
    months = [r.month for r in rows]
    half = len(rows) // 2
    g_ci = month_cluster_ci(gross, months, alpha=FAMILY_ALPHA, seed=seed)
    e_ci = month_cluster_ci(excess, months, alpha=FAMILY_ALPHA, seed=seed)
    g1, g2, e1, e2 = _mean(gross[:half]), _mean(gross[half:]), _mean(excess[:half]), _mean(excess[half:])
    if not decision_group:
        verdict = "DIAGNOSTIC_ONLY"
    elif len(rows) < MIN_LISTINGS:
        verdict = "INSUFFICIENT"
    elif (g_ci is not None and e_ci is not None and g_ci[1] < 0 and e_ci[1] < 0
          and all(v is not None and v < 0 for v in (g1, g2, e1, e2))):
        verdict = "AVOID_CONFIRMED"
    elif g_ci is not None and e_ci is not None and g_ci[0] > 0 and e_ci[0] > 0:
        verdict = "POSITIVE_SURPRISE"
    else:
        verdict = "NO_CLAIM"
    base, stress = costs.round_trip_pct(), costs.round_trip_pct(fee_mult=1.5, slip_mult=2.0)
    return GroupStats(
        name=name, n=len(rows), months=len(set(months)), delisted=sum(r.delisted for r in rows),
        mean_gross_pct=_mean(gross), median_gross_pct=float(np.median(gross)) if gross else None,
        share_positive=(sum(g > 0 for g in gross) / len(gross)) if gross else None,
        mean_btc_pct=_mean([r.btc_pct for r in rows]), mean_excess_pct=_mean(excess),
        share_beating_btc=(sum(e > 0 for e in excess) / len(excess)) if excess else None,
        gross_ci_family=g_ci, excess_ci_family=e_ci,
        first_half_gross_pct=g1, second_half_gross_pct=g2, first_half_excess_pct=e1, second_half_excess_pct=e2,
        mean_net_pct=_mean([g - base for g in gross]), stressed_mean_net_pct=_mean([g - stress for g in gross]),
        verdict=verdict,
    )


def pump_band(pump_pct: float) -> str:
    if pump_pct > CROWDED_PUMP_PCT:
        return "pump>80%"
    if pump_pct < HEAVY_SELL_PCT:
        return "pump<-35%"
    return "pump-35..80%"


def volume_band(volume: float) -> str:
    low, high = VOLUME_BANDS
    if volume < low:
        return "vol<5M"
    if volume < high:
        return "vol 5-50M"
    return "vol>50M"


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def engine_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


@dataclass
class ListingReport:
    listings: int
    rejected_rows: int
    data_end: int
    costs: ListingCosts
    engine_fingerprint: str
    decision: dict[str, GroupStats]
    diagnostics: dict[str, GroupStats]
    counts: dict
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "listings": self.listings, "rejected_rows": self.rejected_rows, "data_end": self.data_end,
            "costs": asdict(self.costs), "engine_fingerprint": self.engine_fingerprint,
            "decision": {k: v.to_dict() for k, v in self.decision.items()},
            "diagnostics": {k: v.to_dict() for k, v in self.diagnostics.items()},
            "counts": self.counts, "notes": list(self.notes), "can_authorize_trade": False,
        }


def run_replay(listings: Mapping[str, Series], btc: Series, *, data_end: int, costs: ListingCosts = ListingCosts(),
               fingerprint: str = "unknown") -> ListingReport:
    obs = observations(listings, btc, data_end=data_end)
    decision, diagnostics = {}, {}
    for entry in ENTRY_OFFSETS:
        for horizon in HORIZONS:
            name = f"{entry}@{horizon}"
            subset = [r for r in obs.rows if r.entry == entry and r.horizon == horizon]
            decision[name] = group_stats(name, subset, costs)
            years: dict[int, list[Observation]] = {}
            for r in subset:
                years.setdefault(datetime.fromtimestamp(r.first_trade, tz=timezone.utc).year, []).append(r)
            for year, items in sorted(years.items()):
                diagnostics[f"{name} {year}"] = group_stats(f"{name} {year}", items, costs, decision_group=False)
            if entry != "1h":
                for label, fn in (("pump", lambda r: pump_band(r.pump_pct)),
                                  ("volume", lambda r: volume_band(r.volume_before_entry))):
                    bands: dict[str, list[Observation]] = {}
                    for r in subset:
                        bands.setdefault(fn(r), []).append(r)
                    for band, items in sorted(bands.items()):
                        diagnostics[f"{name} {band}"] = group_stats(f"{name} {band}", items, costs,
                                                                    decision_group=False)
    notes = [
        "Karar brüt getiriyle (komisyon ve kayma yok): kayıp iddiası için muhafazakâr seçim; net değerler ayrıca.",
        f"Karşılaştırma: aynı giriş-çıkış saatlerinde BTCUSDT. Güven aralığı listeleme ayına göre küme bootstrap, "
        f"Bonferroni α={FAMILY_ALPHA:.4f} (6 test).",
        "Giriş: ilk işlem mumunun açılışından +1s / +24s / +7g sonraki saatlik mumun açılışı (bakım boşluğunda "
        "6 saat içindeki ilk mum). Borsadan kalkan coin son kapanıştan çıkar; çıkışı veri sonundan sonraya düşen "
        "gözlem alınmaz.",
        "Kanıt Binance listelemeleri içindir; canlı radar MEXC'de çalışır ve MEXC daha erken, daha riskli coin listeler.",
    ]
    rejected = sum(s.rejected for s in listings.values()) + btc.rejected
    if rejected:
        notes.append(f"UYARI: {rejected} bozuk/tekrarlı mum satırı atıldı (onarılmadı).")
    counts = {"no_trade": obs.no_trade, "ended_before_entry": obs.ended_before_entry,
              "incomplete_window": obs.incomplete_window, "unresolvable": obs.unresolvable}
    return ListingReport(listings=len(listings), rejected_rows=rejected, data_end=data_end, costs=costs,
                         engine_fingerprint=fingerprint, decision=decision, diagnostics=diagnostics,
                         counts=counts, notes=notes)


def _fmt(value, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def _ci(ci) -> str:
    return "n/a" if ci is None else f"[{ci[0]:+.1f},{ci[1]:+.1f}]"


def print_summary(report: ListingReport) -> None:
    print(f"listings={report.listings} rejected_rows={report.rejected_rows} counts={report.counts} "
          f"engine={report.engine_fingerprint}")
    print(f"{'group':<28}{'n':>5}{'mo':>4}{'del':>4}{'mean%':>8}{'med%':>8}{'pos':>5}{'btc%':>7}{'exc%':>8}"
          f"{'>btc':>5}{'grossCI':>17}{'excessCI':>17}{'g.h1':>7}{'g.h2':>7}{'e.h1':>7}{'e.h2':>7}{'net%':>7}  verdict")
    for g in (*report.decision.values(), *report.diagnostics.values()):
        print(f"{g.name:<28}{g.n:>5}{g.months:>4}{g.delisted:>4}{_fmt(g.mean_gross_pct, '+.1f'):>8}"
              f"{_fmt(g.median_gross_pct, '+.1f'):>8}{_fmt(g.share_positive, '.2f'):>5}{_fmt(g.mean_btc_pct, '+.1f'):>7}"
              f"{_fmt(g.mean_excess_pct, '+.1f'):>8}{_fmt(g.share_beating_btc, '.2f'):>5}{_ci(g.gross_ci_family):>17}"
              f"{_ci(g.excess_ci_family):>17}{_fmt(g.first_half_gross_pct, '+.1f'):>7}"
              f"{_fmt(g.second_half_gross_pct, '+.1f'):>7}{_fmt(g.first_half_excess_pct, '+.1f'):>7}"
              f"{_fmt(g.second_half_excess_pct, '+.1f'):>7}{_fmt(g.mean_net_pct, '+.1f'):>7}  {g.verdict}")
    for note in report.notes:
        print(f"note: {note}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


REPLAY_FAMILY = "new_listing_avoidance_spot"


def replay_trial_params(*, fingerprint: str, costs: ListingCosts) -> dict:
    return {
        "engine_fingerprint": fingerprint,
        "entries_seconds": dict(ENTRY_OFFSETS), "horizons_seconds": dict(HORIZONS),
        "price_tolerance_seconds": PRICE_TOLERANCE,
        "benchmark": BENCHMARK, "window_start": "2020-10", "min_listings": MIN_LISTINGS,
        "family_alpha": FAMILY_ALPHA, "decision_on": "gross_return_and_excess_vs_btc",
        "costs": asdict(costs), "outcome": "avoid_confirmed_if_upper_bounds_below_zero_and_both_halves_negative",
    }


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="New-listing cohort study (loss avoidance).")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="download the new-listing dataset")
    b.add_argument("--out", type=Path, default=Path("research/data/listings"))
    r = sub.add_parser("run", help="run the pre-registered test")
    r.add_argument("--data-dir", type=Path, default=Path("research/data/listings"))
    r.add_argument("--out", type=Path, default=Path("research/data/listing_replay.json"))
    r.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    r.add_argument("--record-trial", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "build":
        from trading.data.binance_listings import build_listings

        logging.basicConfig(level=logging.INFO, format="%(message)s")
        manifest = build_listings(args.out)
        print(json.dumps({k: v for k, v in manifest.items() if k not in ("listings", "excluded")}, indent=1))
        return 0

    from trading.research.report import StrategyReport
    from trading.research.robustness import TrialRecord, TrialRegistry, trial_id_for
    from trading.strategies.listing_dossier import (
        LISTING_BLOW_UP_SCENARIOS,
        LISTING_FRAGILITY_ANSWERS,
        LISTING_HYPOTHESIS,
    )

    started = time.time()
    listings, btc, manifest = load_dataset(args.data_dir)
    data_end = int(manifest["data_end"])
    if not len(btc.open_time) or int(btc.open_time[-1]) + HOUR - 1 > data_end:
        raise SystemExit("benchmark data reaches past data_end; refusing to run")
    costs = ListingCosts()
    fingerprint = engine_fingerprint()
    report = run_replay(listings, btc, data_end=data_end, costs=costs, fingerprint=fingerprint)
    excluded = manifest.get("excluded") or {}
    reasons: dict[str, int] = {}
    for reason in excluded.values():
        key = reason.split("_IN_")[0].split("_FROM_")[0]
        reasons[key] = reasons.get(key, 0) + 1
    report.notes.append(f"Seçim: {len(listings)} yeni listeleme; dışlananlar {dict(sorted(reasons.items()))}.")
    bad = {k: v for k, v in (manifest.get("migration_check") or {}).items() if v != "OK"}
    if bad:
        report.notes.append(f"UYARI: ticker değişikliği listesi veriyle uyuşmuyor: {bad}.")
    payload = report.to_dict()
    payload["dataset_manifest"] = {k: v for k, v in manifest.items() if k not in ("listings",)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), "utf-8")
    tmp.replace(args.out)
    args.out.with_suffix(".md").write_text(StrategyReport(
        hypothesis=LISTING_HYPOTHESIS, fragility_answers=LISTING_FRAGILITY_ANSWERS,
        blow_up_scenarios=LISTING_BLOW_UP_SCENARIOS,
        evidence={k: payload[k] for k in ("decision", "counts", "notes", "engine_fingerprint", "can_authorize_trade")},
    ).render_markdown(), "utf-8")

    params = replay_trial_params(fingerprint=fingerprint, costs=costs)
    trial_id = trial_id_for(family=REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(args.trial_registry)
    known = {rec.trial_id for rec in registry.selection_trials(REPLAY_FAMILY)}
    print(f"trial {trial_id} ({'pre-registered' if trial_id in known else 'NOT pre-registered'})"
          f" · family trials: {len(known | {trial_id})}")
    if args.record_trial:
        registry.append(TrialRecord(
            trial_id=trial_id, family=REPLAY_FAMILY, kind="SELECTION_CANDIDATE",
            description="listing replay CLI run", params=params,
            dataset={"listings": report.listings, "data_end": data_end},
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            n_trades=sum(g.n for g in report.decision.values()), sharpe_per_trade=None,
        ))
    print_summary(report)
    print(f"wrote {args.out} and {args.out.with_suffix('.md')} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
