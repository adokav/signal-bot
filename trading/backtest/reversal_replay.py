"""Short-term cross-sectional reversal, tested on an untouched holdout (2017-09 → 2020-09).

Where the hypothesis comes from: in the Liquid-100 replay (2020-10 → 2026-08)
the coins that had just risen the most fell behind the equal-weight basket
over the next 12-72 hours. Testing that idea on the same data would be
circular, so it is tested here on Binance spot history that no earlier
trial in this repository has looked at.

Rule (pre-registered, docs/REVERSAL_REPLAY_REPORT.md): every day at 00:00
UTC, rank the point-in-time liquid universe (top 100 USDT pairs by trailing
24h quote volume, live identity rules) by trailing 24h return. Buy the
bottom decile (the biggest losers) at the next 15m open, equal weight, and
hold 1 day (daily cohorts) or 3 days (a cohort every third day, so cohorts
never overlap). Each cohort pays a full round trip.

Measured per cohort: net return and net return in excess of the equal-weight
universe over the same window. The top decile (biggest winners) is reported
as a diagnostic: the source finding predicts it lags the basket.

Long-only spot; no order authority anywhere (AGENTS.md §4, §10).
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from acce_unified.liquid_long import select_liquid_universe
from acce_unified.models import CexTicker
from trading.backtest import liquid_replay as lr
from trading.research.robustness import bootstrap_mean_ci


HOLDOUT_END = "2020-10-01"   # first second after the holdout; 2020-10 onwards was used by earlier trials
HOLDOUT_MONTHS = 37          # 2017-09 .. 2020-09
UNIVERSE_SIZE = 100
MIN_QUOTE_VOLUME = 1_000_000.0
MIN_UNIVERSE = 30            # fewer liquid pairs than this and a decile means nothing
DECILE = 0.10
MIN_DECILE = 3
DECISION_HOLDS = (1, 3)      # days
GROUPS = ("LOSERS", "WINNERS")
FAMILY_ALPHA = 0.05 / len(DECISION_HOLDS)   # decision groups: LOSERS@1d, LOSERS@3d
MIN_COHORTS = 200
BLOCK_COHORTS = 10

REPO_ROOT = Path(__file__).resolve().parents[2]
FINGERPRINT_FILES = (
    "acce_unified/liquid_long.py",
    "acce_unified/cex.py",
    "acce_unified/models.py",
    "trading/data/binance_vision.py",
    "trading/data/binance_universe.py",
    "trading/backtest/liquid_replay.py",
    "trading/backtest/reversal_replay.py",
)


@dataclass(frozen=True)
class ReversalCosts:
    fee_bps_per_side: float = 7.5
    slippage_bps_per_side: float = 5.0

    def round_trip_pct(self, *, fee_mult: float = 1.0, slip_mult: float = 1.0) -> float:
        return 2.0 * (self.fee_bps_per_side * fee_mult + self.slippage_bps_per_side * slip_mult) / 100.0


def holdout_decision_at() -> int:
    return int(datetime.fromisoformat(HOLDOUT_END).replace(tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# Cohorts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cohort:
    group: str
    hold_days: int
    day: int                 # UTC day number of the decision
    members: tuple[str, ...]
    universe_size: int
    gross_pct: float
    benchmark_pct: float
    delisted_exits: int


def decision_bars(market: lr.Market) -> list[int]:
    """Index of the 15m bar that closes just before each 00:00 UTC (decision uses bars up to it)."""

    closes = market.grid_open + lr.BAR_SECONDS
    return [int(i) for i in np.nonzero(closes % 86_400 == 0)[0]]


def universe_at(market: lr.Market, i: int) -> list[int]:
    available = np.isfinite(market.qv24[:, i]) & np.isfinite(market.chg24[:, i]) & np.isfinite(market.close[:, i])
    tickers = [
        CexTicker(symbol=market.symbols[s], last_price=float(market.close[s, i]),
                  change_pct=float(market.chg24[s, i]), quote_volume=float(market.qv24[s, i]),
                  venue="BINANCE_SPOT_REPLAY")
        for s in np.nonzero(available)[0]
    ]
    chosen = select_liquid_universe(tickers, size=UNIVERSE_SIZE, min_quote_volume=MIN_QUOTE_VOLUME)
    return [market.index_of[t.symbol] for t in chosen]


def deciles(market: lr.Market, i: int, universe: Sequence[int]) -> tuple[list[int], list[int]]:
    """(losers, winners) by trailing 24h return; ties broken by symbol so runs are reproducible."""

    k = max(MIN_DECILE, int(len(universe) * DECILE))
    ranked = sorted(universe, key=lambda s: (float(market.chg24[s, i]), market.symbols[s]))
    return ranked[:k], ranked[-k:]


def cohorts(market: lr.Market, hold_days: int) -> tuple[list[Cohort], int]:
    """Non-overlapping cohorts for both groups; returns (cohorts, decisions skipped as unresolvable)."""

    bars = hold_days * lr.DAY_BARS
    out: list[Cohort] = []
    skipped = 0
    for n, i in enumerate(decision_bars(market)):
        if n % hold_days:
            continue
        universe = universe_at(market, i)
        if len(universe) < MIN_UNIVERSE:
            continue
        bench_returns, _ = lr.forward_returns(market, universe, i, bars)
        bench = bench_returns[np.isfinite(bench_returns)]
        if len(bench) < MIN_UNIVERSE:
            skipped += 1
            continue
        losers, winners = deciles(market, i, universe)
        for group, members in (("LOSERS", losers), ("WINNERS", winners)):
            returns, delisted = lr.forward_returns(market, members, i, bars)
            if not np.isfinite(returns).all():
                skipped += 1
                continue
            out.append(Cohort(
                group=group, hold_days=hold_days, day=int(market.grid_open[i] + lr.BAR_SECONDS) // 86_400,
                members=tuple(market.symbols[s] for s in members), universe_size=len(universe),
                gross_pct=float(np.mean(returns)), benchmark_pct=float(np.mean(bench)),
                delisted_exits=int(delisted.sum()),
            ))
    return out, skipped


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupStats:
    name: str
    cohorts: int
    mean_universe: float
    mean_gross_pct: float | None
    mean_net_pct: float | None
    mean_benchmark_pct: float | None
    mean_excess_pct: float | None
    excess_hit_rate: float | None
    excess_ci95: tuple[float, float] | None
    excess_ci_family: tuple[float, float] | None
    stressed_mean_net_pct: float | None
    stressed_mean_excess_pct: float | None
    first_half_excess_pct: float | None
    second_half_excess_pct: float | None
    annualised_net_pct: float | None
    delisted_exits: int
    verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def _mean(values: Sequence[float]) -> float | None:
    return float(np.mean(values)) if len(values) else None


def group_stats(name: str, rows: Sequence[Cohort], costs: ReversalCosts, *, decision_group: bool = True,
                seed: int = 0) -> GroupStats:
    """PASS_CANDIDATE needs >= 200 cohorts, a family-wise block-bootstrap lower bound of mean net
    excess return above zero, positive net and excess return under stressed costs and positive net
    excess in both chronological halves."""

    rows = sorted(rows, key=lambda c: c.day)
    base, stress = costs.round_trip_pct(), costs.round_trip_pct(fee_mult=1.5, slip_mult=2.0)
    net = [c.gross_pct - base for c in rows]
    excess = [n - c.benchmark_pct for n, c in zip(net, rows)]
    s_net = [c.gross_pct - stress for c in rows]
    s_excess = [n - c.benchmark_pct for n, c in zip(s_net, rows)]
    half = len(rows) // 2
    enough = len(excess) > 2 * BLOCK_COHORTS
    ci95 = bootstrap_mean_ci(excess, block_size=BLOCK_COHORTS, seed=seed) if enough else None
    ci_family = bootstrap_mean_ci(excess, block_size=BLOCK_COHORTS, alpha=FAMILY_ALPHA, n_resamples=4000,
                                  seed=seed) if enough else None
    first, second = _mean(excess[:half]), _mean(excess[half:])
    hold = rows[0].hold_days if rows else 1
    if not decision_group:
        verdict = "DIAGNOSTIC_ONLY"
    elif ci_family is not None and ci_family[1] < 0:
        verdict = "NEGATIVE"
    elif len(rows) < MIN_COHORTS:
        verdict = "INSUFFICIENT"
    elif (ci_family is not None and ci_family[0] > 0 and _mean(s_excess) > 0 and _mean(s_net) > 0
          and first is not None and first > 0 and second is not None and second > 0):
        verdict = "PASS_CANDIDATE"
    else:
        verdict = "NO_EDGE"
    return GroupStats(
        name=name, cohorts=len(rows), mean_universe=float(np.mean([c.universe_size for c in rows])) if rows else 0.0,
        mean_gross_pct=_mean([c.gross_pct for c in rows]), mean_net_pct=_mean(net),
        mean_benchmark_pct=_mean([c.benchmark_pct for c in rows]), mean_excess_pct=_mean(excess),
        excess_hit_rate=(sum(e > 0 for e in excess) / len(excess)) if excess else None,
        excess_ci95=ci95, excess_ci_family=ci_family,
        stressed_mean_net_pct=_mean(s_net), stressed_mean_excess_pct=_mean(s_excess),
        first_half_excess_pct=first, second_half_excess_pct=second,
        annualised_net_pct=(_mean(net) * 365 / hold) if net else None,
        delisted_exits=sum(c.delisted_exits for c in rows), verdict=verdict,
    )


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
class ReversalReport:
    start_time: int
    end_time: int
    symbols: int
    rejected_rows: int
    costs: ReversalCosts
    engine_fingerprint: str
    decision: dict[str, GroupStats]
    diagnostics: dict[str, GroupStats]
    by_year: dict[str, GroupStats]
    skipped: dict[str, int]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time, "end_time": self.end_time, "symbols": self.symbols,
            "rejected_rows": self.rejected_rows, "costs": asdict(self.costs),
            "engine_fingerprint": self.engine_fingerprint,
            "decision": {k: v.to_dict() for k, v in self.decision.items()},
            "diagnostics": {k: v.to_dict() for k, v in self.diagnostics.items()},
            "by_year": {k: v.to_dict() for k, v in self.by_year.items()},
            "skipped": self.skipped, "notes": list(self.notes), "can_authorize_trade": False,
        }


def run_replay(market: lr.Market, *, costs: ReversalCosts = ReversalCosts(), fingerprint: str = "unknown"
               ) -> ReversalReport:
    decision, diagnostics, by_year, skipped = {}, {}, {}, {}
    for hold in DECISION_HOLDS:
        rows, skipped[f"{hold}d"] = cohorts(market, hold)
        for group in GROUPS:
            name = f"{group}@{hold}d"
            subset = [c for c in rows if c.group == group]
            stats = group_stats(name, subset, costs, decision_group=(group == "LOSERS"))
            (decision if group == "LOSERS" else diagnostics)[name] = stats
            years: dict[str, list[Cohort]] = {}
            for c in subset:
                years.setdefault(str(datetime.fromtimestamp(c.day * 86_400, tz=timezone.utc).year), []).append(c)
            for year, items in sorted(years.items()):
                by_year[f"{name} {year}"] = group_stats(f"{name} {year}", items, costs, decision_group=False)
    notes = [
        "Dönem 2017-09 → 2020-09: bu depodaki önceki denemelerin hiçbiri bu veriye bakmadı (hipotez 2020-10 sonrası veriden çıktı).",
        f"Evren: her gün son 24 saatlik hacme göre ilk {UNIVERSE_SIZE} USDT çifti (≥ {MIN_QUOTE_VOLUME:,.0f} USD), "
        f"canlı kimlik kuralları; {MIN_UNIVERSE}'dan az çift olan günler işlem yok.",
        f"Maliyet: taraf başına {costs.fee_bps_per_side:g} bp komisyon + {costs.slippage_bps_per_side:g} bp slippage "
        f"(gidiş-dönüş %{costs.round_trip_pct():.2f}); her kohort tam gidiş-dönüş öder (muhafazakâr).",
        "Giriş 00:00 UTC'den sonraki ilk 15 dakikalık mumun açılışında; kalkan coin son işlem fiyatından çıkar.",
        f"Karar grupları LOSERS@1g ve LOSERS@3g; Bonferroni α={FAMILY_ALPHA:g}; WINNERS yalnızca tanılayıcı.",
        "2017-2018 piyasası (ICO dönemi, düşük likidite) bugünkünden farklıdır; sonuç bugüne genellenirken dikkat.",
    ]
    if market.rejected_rows:
        notes.append(f"UYARI: {market.rejected_rows} bozuk/tekrarlı mum satırı atıldı (onarılmadı).")
    return ReversalReport(
        start_time=int(market.grid_open[0]), end_time=int(market.grid_open[-1]) + lr.BAR_SECONDS - 1,
        symbols=len(market.symbols), rejected_rows=market.rejected_rows, costs=costs, engine_fingerprint=fingerprint,
        decision=decision, diagnostics=diagnostics, by_year=by_year, skipped=skipped, notes=notes,
    )


def _fmt(value, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def print_summary(report: ReversalReport) -> None:
    print(f"symbols={report.symbols} rejected_rows={report.rejected_rows} skipped={report.skipped} "
          f"engine={report.engine_fingerprint}")
    print(f"{'group':<22}{'n':>6}{'univ':>6}{'gross%':>8}{'net%':>8}{'bench%':>8}{'excess%':>9}{'hit':>6}"
          f"{'famCI':>18}{'s.net%':>8}{'s.exc%':>8}{'h1':>7}{'h2':>7}{'ann.net%':>10}  verdict")
    for g in (*report.decision.values(), *report.diagnostics.values(), *report.by_year.values()):
        ci = "n/a" if g.excess_ci_family is None else f"[{g.excess_ci_family[0]:+.2f},{g.excess_ci_family[1]:+.2f}]"
        print(f"{g.name:<22}{g.cohorts:>6}{g.mean_universe:>6.0f}{_fmt(g.mean_gross_pct, '+.2f'):>8}"
              f"{_fmt(g.mean_net_pct, '+.2f'):>8}{_fmt(g.mean_benchmark_pct, '+.2f'):>8}"
              f"{_fmt(g.mean_excess_pct, '+.3f'):>9}{_fmt(g.excess_hit_rate, '.2f'):>6}{ci:>18}"
              f"{_fmt(g.stressed_mean_net_pct, '+.2f'):>8}{_fmt(g.stressed_mean_excess_pct, '+.2f'):>8}"
              f"{_fmt(g.first_half_excess_pct, '+.2f'):>7}{_fmt(g.second_half_excess_pct, '+.2f'):>7}"
              f"{_fmt(g.annualised_net_pct, '+.1f'):>10}  {g.verdict}")
    for note in report.notes:
        print(f"note: {note}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


REPLAY_FAMILY = "cross_sectional_reversal_spot"


def replay_trial_params(*, fingerprint: str, costs: ReversalCosts) -> dict:
    return {
        "engine_fingerprint": fingerprint,
        "holdout_end": HOLDOUT_END, "holdout_months": HOLDOUT_MONTHS,
        "universe_size": UNIVERSE_SIZE, "min_quote_volume": MIN_QUOTE_VOLUME, "min_universe": MIN_UNIVERSE,
        "signal": "trailing_24h_return_bottom_decile", "decile": DECILE, "hold_days": list(DECISION_HOLDS),
        "costs": asdict(costs),
        "outcome": "cohort_net_excess_vs_equal_weight_universe",
    }


def build(out_dir: Path) -> dict:
    """The survivorship-free universe for the holdout: nothing published after 2020-09-30 is read."""

    from trading.data import binance_universe

    return binance_universe.build_universe(out_dir, lookback_months=HOLDOUT_MONTHS, decision_at=holdout_decision_at())


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse
    import logging

    parser = argparse.ArgumentParser(description="Short-term reversal on the 2017-2020 holdout.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="download the holdout universe")
    b.add_argument("--out", type=Path, default=Path("research/data/reversal_universe"))
    r = sub.add_parser("run", help="run the pre-registered test")
    r.add_argument("--data-dir", type=Path, default=Path("research/data/reversal_universe"))
    r.add_argument("--out", type=Path, default=Path("research/data/reversal_replay.json"))
    r.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    r.add_argument("--record-trial", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "build":
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        manifest = build(args.out)
        print(json.dumps({k: v for k, v in manifest.items() if k != "symbols"}, indent=1))
        return 0

    from trading.research.report import StrategyReport
    from trading.research.robustness import TrialRecord, TrialRegistry, trial_id_for
    from trading.strategies.reversal_dossier import (
        REVERSAL_BLOW_UP_SCENARIOS,
        REVERSAL_FRAGILITY_ANSWERS,
        REVERSAL_HYPOTHESIS,
    )

    started = time.time()
    market, manifest = lr.load_market(args.data_dir)
    if int(market.grid_open[-1]) >= holdout_decision_at():
        raise SystemExit("data reaches past the holdout end; refusing to run")
    costs = ReversalCosts()
    fingerprint = engine_fingerprint()
    report = run_replay(market, costs=costs, fingerprint=fingerprint)
    stopped = manifest.get("stopped_before_window_end") or []
    report.notes.append(f"Evren: {manifest.get('candidates')} aday çift, {len(stopped)} tanesinin verisi pencere bitmeden duruyor.")
    if not stopped:
        report.notes.append("UYARI: verisi erken biten hiç çift yok; survivorship'ten arındırma doğrulanamadı.")
    payload = report.to_dict()
    payload["universe_manifest"] = {k: v for k, v in manifest.items() if k != "symbols"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), "utf-8")
    tmp.replace(args.out)
    args.out.with_suffix(".md").write_text(StrategyReport(
        hypothesis=REVERSAL_HYPOTHESIS, fragility_answers=REVERSAL_FRAGILITY_ANSWERS,
        blow_up_scenarios=REVERSAL_BLOW_UP_SCENARIOS,
        evidence={k: payload[k] for k in ("decision", "diagnostics", "notes", "engine_fingerprint", "can_authorize_trade")},
    ).render_markdown(), "utf-8")

    params = replay_trial_params(fingerprint=fingerprint, costs=costs)
    trial_id = trial_id_for(family=REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(args.trial_registry)
    known = {r.trial_id for r in registry.selection_trials(REPLAY_FAMILY)}
    print(f"trial {trial_id} ({'pre-registered' if trial_id in known else 'NOT pre-registered'})"
          f" · family trials: {len(known | {trial_id})}")
    if args.record_trial:
        registry.append(TrialRecord(
            trial_id=trial_id, family=REPLAY_FAMILY, kind="SELECTION_CANDIDATE",
            description="reversal replay CLI run", params=params,
            dataset={"start": report.start_time, "end": report.end_time},
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            n_trades=sum(g.cohorts for g in report.decision.values()), sharpe_per_trade=None,
        ))
    print_summary(report)
    print(f"wrote {args.out} and {args.out.with_suffix('.md')} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
