"""Historical replay of the live BTC/ETH tactical Long radar.

The live radar's setups (trend pullback, breakout retest, range reclaim,
liquidity sweep) have never been tested; this replays the *production*
engine over historical spot data and measures what its alerts would have
done. Faithfulness rules:

- **Same engine, same cadence.** ``TacticalLongEngine`` is evaluated at
  every M5 close, like the 5-minute live scan.
- **Point in time.** Each timeframe contributes its last 240 candles whose
  close is at or before the decision time (the live adapter's limit); a
  stale or gappy timeframe skips the step, as a live ``DataQualityError``
  would. Swings already require right-side confirmation inside the engine.
- **Same recording and resolution.** A record is opened exactly when the
  bot would alert (state:setup changes into READY/TRIGGERED) and resolved by
  ``acce_unified.forward_ledger`` — the same code that runs live — so the
  replay is "what the forward ledger would have recorded".
- **Honest costs.** The engine's own cost estimate (0.04-0.06%) is
  replaced by an explicit model (default 5 bps fee + 2 bps slippage per
  side), with a stress scenario (fee x1.5, slippage x2).

Known gaps, stated in every report: the quote is synthesized from the last
closed M5 close with an assumed spread (no historical order book);
Binance spot stands in for MEXC spot; a limit is assumed filled when price
touches it (queue position ignored, which flatters fills).

No order authority anywhere (AGENTS.md §4, §10).
"""

from __future__ import annotations

import hashlib
import json
import math
import multiprocessing
import os
import time
from array import array
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from acce_unified.forward_ledger import ForwardRecord, update_records
from acce_unified.tactical_long import Candle
from acce_unified.tactical_long_data import (
    CONTEXT_SYMBOLS,
    REQUIRED_TIMEFRAMES,
    TACTICAL_SYMBOLS,
    Quote,
    TacticalMarketSnapshot,
    TacticalTimeframe,
)
from acce_unified.tactical_long_engine import TacticalLongEngine
from trading.research.calibration import wilson_interval
from trading.research.metrics import max_consecutive_losses, max_drawdown_pct, mean
from trading.research.robustness import bootstrap_mean_ci


WINDOW_BARS = 240
SYMBOLS = (*TACTICAL_SYMBOLS, *CONTEXT_SYMBOLS)
FILE_TIMEFRAME = {
    TacticalTimeframe.M5: "5m",
    TacticalTimeframe.M15: "15m",
    TacticalTimeframe.H1: "1h",
    TacticalTimeframe.H4: "4h",
    TacticalTimeframe.D1: "1d",
}
# The engine never reads D1 (only H4/H1/M15/M5); the snapshot contract still
# requires the frame, so D1 accepts a short window instead of a 240-day warm-up.
MIN_BARS = {tf: (2 if tf is TacticalTimeframe.D1 else WINDOW_BARS) for tf in REQUIRED_TIMEFRAMES}
RESOLVED = ("WIN_T1", "LOSS_STOP", "TIME_EXIT")
SETUP_FAMILIES = ("TREND_PULLBACK", "BREAKOUT_RETEST", "RANGE_RECLAIM", "LIQUIDITY_SWEEP_RECLAIM")
# Pre-declared multiple-testing correction: four setup families + the pool.
FAMILY_ALPHA = 0.05 / 5
MIN_RESOLVED_FOR_DECISION = 100
REPO_ROOT = Path(__file__).resolve().parents[2]
# Everything that decides, resolves or measures a setup. Changing any of
# these files changes the fingerprint and therefore the trial id, so a
# tweak made after seeing results is counted as a new trial (AGENTS.md §6-7).
FINGERPRINT_FILES = (
    "acce_unified/tactical_long.py",
    "acce_unified/tactical_long_data.py",
    "acce_unified/tactical_long_engine.py",
    "acce_unified/forward_ledger.py",
    "trading/backtest/tactical_replay.py",
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def _valid_row(open_time: int, close_time: int, o: float, h: float, low: float, c: float, v: float) -> bool:
    if close_time <= open_time or open_time < 0:
        return False
    if not all(math.isfinite(x) for x in (o, h, low, c, v)):
        return False
    return min(o, h, low, c) > 0 and h >= max(o, c) and low <= min(o, c) and v >= 0


class _Series:
    """Compact column storage for one symbol/timeframe.

    Malformed rows and duplicate open times are dropped and counted, never
    repaired: the hole they leave makes the snapshot stale (step skipped) or
    the ledger UNRESOLVABLE, which is the fail-closed outcome (AGENTS.md §2).
    """

    def __init__(self, rows: Iterable[tuple[int, int, float, float, float, float, float]]):
        self.open_time = array("q")
        self.close_time = array("q")
        self.open = array("d")
        self.high = array("d")
        self.low = array("d")
        self.close = array("d")
        self.volume = array("d")
        self.rejected = 0
        previous_open = None
        for open_time, close_time, o, h, low, c, v in rows:
            if previous_open is not None and open_time < previous_open:
                raise ValueError("series must be chronological")
            if (previous_open is not None and open_time == previous_open) or not _valid_row(
                open_time, close_time, o, h, low, c, v
            ):
                self.rejected += 1
                continue
            previous_open = open_time
            self.open_time.append(open_time)
            self.close_time.append(close_time)
            self.open.append(o)
            self.high.append(h)
            self.low.append(low)
            self.close.append(c)
            self.volume.append(v)

    def __len__(self) -> int:
        return len(self.open_time)

    def candle(self, i: int) -> Candle:
        return Candle(
            open_time=self.open_time[i],
            close_time=self.close_time[i],
            available_at=self.close_time[i],
            open=self.open[i],
            high=self.high[i],
            low=self.low[i],
            close=self.close[i],
            volume=self.volume[i],
        )

    def last_closed_index(self, t: int) -> int:
        return bisect_right(self.close_time, t) - 1


def load_series_from_parquet(path: Path) -> _Series:
    import pandas as pd

    frame = pd.read_parquet(path).sort_values("open_time")
    return _Series(
        (int(r.open_time), int(r.close_time), float(r.open), float(r.high),
         float(r.low), float(r.close), float(r.volume))
        for r in frame.itertuples(index=False)
    )


def load_dataset(data_dir: Path) -> dict[str, dict[TacticalTimeframe, _Series]]:
    data: dict[str, dict[TacticalTimeframe, _Series]] = {}
    for symbol in SYMBOLS:
        data[symbol] = {}
        for tf in REQUIRED_TIMEFRAMES:
            path = data_dir / f"{symbol}_klines_{FILE_TIMEFRAME[tf]}.parquet"
            if not path.exists():
                raise FileNotFoundError(f"missing {path}")
            data[symbol][tf] = load_series_from_parquet(path)
    return data


# ---------------------------------------------------------------------------
# Point-in-time snapshots
# ---------------------------------------------------------------------------


class _Window:
    """Incrementally maintained window of the last ``WINDOW_BARS`` closed candles."""

    def __init__(self, series: _Series) -> None:
        self.series = series
        self.buffer: deque[Candle] = deque()
        self.last = -1

    def at(self, j: int) -> tuple[Candle, ...]:
        start = max(0, j - WINDOW_BARS + 1)
        if j < self.last or j - self.last > WINDOW_BARS or not self.buffer:
            self.buffer = deque(self.series.candle(k) for k in range(start, j + 1))
        else:
            for k in range(self.last + 1, j + 1):
                self.buffer.append(self.series.candle(k))
            while len(self.buffer) > j - start + 1:
                self.buffer.popleft()
        self.last = j
        return tuple(self.buffer)


class SnapshotBuilder:
    def __init__(self, data: dict[str, dict[TacticalTimeframe, _Series]], *, spread_bps: float) -> None:
        if spread_bps < 0 or not math.isfinite(spread_bps):
            raise ValueError("spread_bps must be finite and non-negative")
        self.data = data
        self.spread_bps = spread_bps
        self.windows = {
            symbol: {tf: _Window(series) for tf, series in frames.items()}
            for symbol, frames in data.items()
        }
        self.last_skip: str | None = None

    def at(self, t: int) -> TacticalMarketSnapshot | None:
        """Snapshot visible at ``t``; None when any frame is short (WARMUP) or stale (STALE)."""

        self.last_skip = None
        candles: dict[str, dict[TacticalTimeframe, tuple[Candle, ...]]] = {}
        quotes: dict[str, Quote] = {}
        for symbol in SYMBOLS:
            frames: dict[TacticalTimeframe, tuple[Candle, ...]] = {}
            for tf in REQUIRED_TIMEFRAMES:
                series = self.data[symbol][tf]
                j = series.last_closed_index(t)
                if j + 1 < MIN_BARS[tf]:
                    self.last_skip = "WARMUP"
                    return None
                if t - series.close_time[j] > 2 * tf.seconds:
                    self.last_skip = "STALE"
                    return None
                frames[tf] = self.windows[symbol][tf].at(j)
            candles[symbol] = frames
            mid = frames[TacticalTimeframe.M5][-1].close
            half = mid * self.spread_bps / 2.0 / 10_000.0
            quotes[symbol] = Quote(symbol, t, t, mid - half, mid + half)
        return TacticalMarketSnapshot(decision_at=t, candles=candles, quotes=quotes, source="BINANCE_SPOT_REPLAY")


# ---------------------------------------------------------------------------
# Phase 1: engine evaluation (parallel)
# ---------------------------------------------------------------------------


_WORKER_DATA: dict | None = None


@dataclass
class _ChunkResult:
    events: list[tuple[int, str, str, dict | None]]
    evaluated: int = 0
    skipped_warmup: int = 0
    skipped_data: int = 0
    engine_errors: int = 0


def _evaluate_chunk(args: tuple[Sequence[int], float]) -> _ChunkResult:
    times, spread_bps = args
    if _WORKER_DATA is None:
        raise RuntimeError("dataset not initialised")
    builder = SnapshotBuilder(_WORKER_DATA, spread_bps=spread_bps)
    engine = TacticalLongEngine()
    result = _ChunkResult(events=[])
    previous: dict[str, str] = {}
    for t in times:
        snapshot = builder.at(t)
        if snapshot is None:
            if builder.last_skip == "WARMUP":
                result.skipped_warmup += 1
            else:
                result.skipped_data += 1
            continue
        try:
            report = engine.analyze(snapshot)
        except Exception:  # live bot also keeps previous states on any scan failure
            result.engine_errors += 1
            continue
        result.evaluated += 1
        for assessment in report.assessments:
            item = assessment.to_dict()
            state = str(item.get("state") or "NO_LONG")
            key = f"{state}:{item.get('setup') or '-'}"
            if previous.get(assessment.symbol) == key:
                continue
            previous[assessment.symbol] = key
            payload = item if state in {"READY", "TRIGGERED"} and item.get("plan") else None
            result.events.append((t, assessment.symbol, key, payload))
    return result


def decision_times(data: dict[str, dict[TacticalTimeframe, _Series]]) -> list[int]:
    """Every M5 close between the first and last BTCUSDT M5 close, like the live 5-minute scan.

    A regular grid (not the candles that happen to exist) so that missing
    data shows up as skipped steps instead of silently vanishing.
    """

    closes = data["BTCUSDT"][TacticalTimeframe.M5].close_time
    if not closes:
        return []
    step = TacticalTimeframe.M5.seconds
    return list(range(closes[0], closes[-1] + 1, step))


def evaluate(
    data: dict[str, dict[TacticalTimeframe, _Series]],
    *,
    spread_bps: float,
    workers: int = 1,
    chunks_per_worker: int = 4,
    progress: bool = False,
) -> _ChunkResult:
    """Run the engine at every decision time; returns change events in time order."""

    global _WORKER_DATA
    times = decision_times(data)
    n_chunks = max(1, workers * chunks_per_worker)
    size = max(1, math.ceil(len(times) / n_chunks))
    chunks = [(times[i:i + size], spread_bps) for i in range(0, len(times), size)]
    _WORKER_DATA = data
    parts: list[_ChunkResult] = []
    pool = None
    try:
        if workers <= 1:
            results: Iterable[_ChunkResult] = map(_evaluate_chunk, chunks)
        else:
            pool = multiprocessing.get_context("fork").Pool(workers)
            results = pool.imap(_evaluate_chunk, chunks)  # preserves chunk order
        for part in results:
            parts.append(part)
            if progress:
                print(f"phase 1: {len(parts)}/{len(chunks)} chunks", flush=True)
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()
        _WORKER_DATA = None
    merged = _ChunkResult(events=[])
    for part in parts:  # chunks are contiguous and returned in order
        merged.events.extend(part.events)
        merged.evaluated += part.evaluated
        merged.skipped_warmup += part.skipped_warmup
        merged.skipped_data += part.skipped_data
        merged.engine_errors += part.engine_errors
    return merged


# ---------------------------------------------------------------------------
# Phase 2: bot-equivalent recording and resolution (sequential)
# ---------------------------------------------------------------------------


def _m5_slices(records: Sequence[ForwardRecord], data, t: int) -> dict[str, list[Candle]]:
    slices: dict[str, list[Candle]] = {}
    for symbol in TACTICAL_SYMBOLS:
        cursors = [r.cursor for r in records if r.symbol == symbol and r.status == "OPEN"]
        if not cursors:
            continue
        series = data[symbol][TacticalTimeframe.M5]
        lo = bisect_left(series.open_time, min(cursors))
        hi = bisect_right(series.close_time, t)
        slices[symbol] = [series.candle(k) for k in range(lo, hi)]
    return slices


def record_outcomes(
    events: Sequence[tuple[int, str, str, dict | None]],
    data,
    *,
    end_time: int,
) -> list[ForwardRecord]:
    """Open a record exactly when the bot would alert; resolve with live ledger code."""

    previous: dict[str, str] = {}
    records: list[ForwardRecord] = []
    for t, symbol, key, item in events:
        old = previous.get(symbol)
        previous[symbol] = key
        if old == key or item is None:
            continue
        records = update_records(records, new_assessments=[item], m5_by_symbol=_m5_slices(records, data, t))
    return update_records(records, new_assessments=[], m5_by_symbol=_m5_slices(records, data, end_time))


# ---------------------------------------------------------------------------
# Outcome statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayCosts:
    fee_bps_per_side: float = 5.0
    slippage_bps_per_side: float = 2.0

    def round_trip_pct(self, *, fee_mult: float = 1.0, slip_mult: float = 1.0) -> float:
        return 2.0 * (self.fee_bps_per_side * fee_mult + self.slippage_bps_per_side * slip_mult) / 100.0


def realized(record: ForwardRecord, cost_pct: float) -> tuple[float, float] | None:
    """(net % of notional, net R) with R measured against the planned risk at the limit."""

    if record.status not in RESOLVED or record.fill_price is None or record.exit_price is None:
        return None
    net = (record.exit_price / record.fill_price - 1.0) * 100.0 - cost_pct
    risk = (record.entry_high - record.hard_stop) / record.entry_high * 100.0 + cost_pct
    return net, net / risk


def _breakeven_hit_rate(record: ForwardRecord, cost_pct: float) -> float:
    reward = (record.target_1 - record.entry_high) / record.entry_high * 100.0 - cost_pct
    risk = (record.entry_high - record.hard_stop) / record.entry_high * 100.0 + cost_pct
    return risk / (reward + risk) if reward + risk > 0 else 1.0


@dataclass(frozen=True)
class GroupStats:
    name: str
    recorded: int
    not_filled: int
    unresolvable: int
    open_at_end: int
    resolved: int
    wins: int
    hit_rate: float | None
    hit_rate_ci: tuple[float, float] | None
    breakeven_hit_rate: float | None
    mean_r: float | None
    mean_r_ci95: tuple[float, float] | None
    mean_r_ci_family: tuple[float, float] | None
    mean_net_pct: float | None
    profit_factor: float | None
    max_consecutive_losses: int
    max_drawdown_r: float | None
    first_half_mean_r: float | None
    second_half_mean_r: float | None
    stressed_mean_r: float | None
    verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def group_stats(
    name: str,
    records: Sequence[ForwardRecord],
    costs: ReplayCosts,
    *,
    seed: int = 0,
    decision_group: bool = True,
) -> GroupStats:
    """Outcome statistics; only the pool and the four setup families are decision groups.

    Per-symbol and per-year slices are diagnostics: they were not part of the
    multiple-testing budget, so they never get a pass/fail verdict.
    """

    base_cost = costs.round_trip_pct()
    stress_cost = costs.round_trip_pct(fee_mult=1.5, slip_mult=2.0)
    resolved = sorted((r for r in records if r.status in RESOLVED), key=lambda r: r.decided_at)
    outcomes = [realized(r, base_cost) for r in resolved]
    rs = [o[1] for o in outcomes if o is not None]
    nets = [o[0] for o in outcomes if o is not None]
    stressed = [o[1] for o in (realized(r, stress_cost) for r in resolved) if o is not None]
    wins = sum(1 for r in resolved if r.status == "WIN_T1")
    half = len(rs) // 2
    gains = sum(v for v in nets if v > 0)
    losses = -sum(v for v in nets if v <= 0)
    ci95 = bootstrap_mean_ci(rs, seed=seed, block_size=5) if len(rs) >= 10 else None
    ci_family = bootstrap_mean_ci(rs, seed=seed, block_size=5, alpha=FAMILY_ALPHA, n_resamples=4000) if len(rs) >= 10 else None
    first = mean(rs[:half]) if half else None
    second = mean(rs[half:]) if len(rs) - half else None
    stressed_mean = mean(stressed) if stressed else None
    if not decision_group:
        verdict = "DIAGNOSTIC_ONLY"
    elif len(rs) < MIN_RESOLVED_FOR_DECISION:
        verdict = "NEGATIVE" if ci_family and ci_family[1] < 0 else "INSUFFICIENT"
    elif ci_family and ci_family[1] < 0:
        verdict = "NEGATIVE"
    elif (
        ci_family and ci_family[0] > 0
        and stressed_mean is not None and stressed_mean > 0
        and first is not None and first > 0 and second is not None and second > 0
    ):
        verdict = "PASS_CANDIDATE"
    else:
        verdict = "NO_EDGE"
    return GroupStats(
        name=name,
        recorded=len(records),
        not_filled=sum(1 for r in records if r.status == "NOT_FILLED"),
        unresolvable=sum(1 for r in records if r.status == "UNRESOLVABLE"),
        open_at_end=sum(1 for r in records if r.status == "OPEN"),
        resolved=len(resolved),
        wins=wins,
        hit_rate=(wins / len(resolved)) if resolved else None,
        hit_rate_ci=wilson_interval(wins, len(resolved)) if resolved else None,
        breakeven_hit_rate=mean([_breakeven_hit_rate(r, base_cost) for r in resolved]) if resolved else None,
        mean_r=mean(rs) if rs else None,
        mean_r_ci95=ci95,
        mean_r_ci_family=ci_family,
        mean_net_pct=mean(nets) if nets else None,
        profit_factor=(gains / losses) if losses > 0 else None,
        max_consecutive_losses=max_consecutive_losses(rs),
        max_drawdown_r=max_drawdown_pct(rs) if rs else None,
        first_half_mean_r=first,
        second_half_mean_r=second,
        stressed_mean_r=stressed_mean,
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def engine_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    """Hash of the code that decides and resolves setups; any change means a new trial."""

    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


@dataclass
class ReplayReport:
    start_time: int
    end_time: int
    decision_steps: int
    evaluated: int
    skipped_warmup: int
    skipped_data: int
    engine_errors: int
    rejected_rows: int
    spread_bps: float
    costs: ReplayCosts
    engine_fingerprint: str
    overall: GroupStats
    by_setup: dict[str, GroupStats]
    by_symbol: dict[str, GroupStats]
    by_year: dict[str, GroupStats]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "decision_steps": self.decision_steps,
            "evaluated": self.evaluated,
            "skipped_warmup": self.skipped_warmup,
            "skipped_data": self.skipped_data,
            "engine_errors": self.engine_errors,
            "rejected_rows": self.rejected_rows,
            "spread_bps": self.spread_bps,
            "costs": asdict(self.costs),
            "engine_fingerprint": self.engine_fingerprint,
            "overall": self.overall.to_dict(),
            "by_setup": {k: v.to_dict() for k, v in self.by_setup.items()},
            "by_symbol": {k: v.to_dict() for k, v in self.by_symbol.items()},
            "by_year": {k: v.to_dict() for k, v in self.by_year.items()},
            "notes": list(self.notes),
            "can_authorize_trade": False,
        }


def _year(record: ForwardRecord) -> str:
    return str(datetime.fromtimestamp(record.decided_at, tz=timezone.utc).year)


def run_replay(
    data: dict[str, dict[TacticalTimeframe, _Series]],
    *,
    spread_bps: float = 2.0,
    costs: ReplayCosts = ReplayCosts(),
    workers: int = 1,
    fingerprint: str = "unknown",
    progress: bool = False,
) -> tuple[ReplayReport, list[ForwardRecord]]:
    times = decision_times(data)
    if not times:
        raise ValueError("no decision times")
    phase1 = evaluate(data, spread_bps=spread_bps, workers=workers, progress=progress)
    end_time = times[-1]
    records = record_outcomes(phase1.events, data, end_time=end_time)

    def grouped(key, *, decision_group: bool) -> dict[str, GroupStats]:
        buckets: dict[str, list[ForwardRecord]] = {}
        for record in records:
            buckets.setdefault(key(record), []).append(record)
        return {
            name: group_stats(name, rows, costs, decision_group=decision_group)
            for name, rows in sorted(buckets.items())
        }

    by_setup = grouped(lambda r: r.setup, decision_group=True)
    for family in SETUP_FAMILIES:
        by_setup.setdefault(family, group_stats(family, [], costs))
    notes = [
        "Binance spot verisi MEXC spot yerine kullanıldı; fitil ve derinlik farkları sonucu değiştirebilir.",
        f"Kotasyon son kapanmış M5 kapanışından {spread_bps:g} bp varsayılan spread ile türetildi (geçmiş emir defteri yok).",
        "Limit giriş, fiyat bölgeye değdiğinde dolmuş sayıldı; kuyruk sırası yok sayıldı (dolumları iyimser gösterir).",
        "Sonuç: T1 stop'tan önce mi (48 saat), canlı forward_ledger kodu ile; T2 modellenmedi.",
        "R, limit fiyatındaki planlanan riske göre ölçülür; maliyet dahil.",
        f"Karar eşiği: ≥{MIN_RESOLVED_FOR_DECISION} çözümlenmiş, Bonferroni (α={FAMILY_ALPHA:g}) bootstrap alt sınırı > 0, stres altında ortalama R > 0, iki kronolojik yarı > 0.",
        "BTC ve ETH kayıtları korele olabilir; birleşik güven aralıkları bağımsızlığı abartabilir (blok bootstrap=5 kısmen hesaba katar).",
    ]
    if phase1.engine_errors > 0.01 * max(1, phase1.evaluated):
        notes.append("UYARI: motor hatası değerlendirilen adımların %1'ini aşıyor; veri veya motor sorunu incelenmeli.")
    if phase1.skipped_data > 0.02 * max(1, len(times) - phase1.skipped_warmup):
        notes.append("UYARI: ısınma sonrası adımların %2'sinden fazlası eksik/bayat veri nedeniyle atlandı; veri kapsamı incelenmeli.")
    rejected = sum(series.rejected for frames in data.values() for series in frames.values())
    if rejected:
        notes.append(f"UYARI: {rejected} bozuk/tekrarlı mum satırı atıldı (onarılmadı).")
    report = ReplayReport(
        start_time=times[0],
        end_time=end_time,
        decision_steps=len(times),
        evaluated=phase1.evaluated,
        skipped_warmup=phase1.skipped_warmup,
        skipped_data=phase1.skipped_data,
        engine_errors=phase1.engine_errors,
        rejected_rows=rejected,
        spread_bps=spread_bps,
        costs=costs,
        engine_fingerprint=fingerprint,
        overall=group_stats("ALL", records, costs),
        by_setup=by_setup,
        by_symbol=grouped(lambda r: r.symbol, decision_group=False),
        by_year=grouped(_year, decision_group=False),
        notes=notes,
    )
    return report, records


def _fmt(value: float | None, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def print_summary(report: ReplayReport) -> None:
    print(f"steps={report.decision_steps} evaluated={report.evaluated} skipped_warmup={report.skipped_warmup} "
          f"skipped_data={report.skipped_data} "
          f"engine_errors={report.engine_errors} rejected_rows={report.rejected_rows} "
          f"engine={report.engine_fingerprint}")
    header = f"{'group':<26}{'rec':>5}{'nf':>5}{'res':>5}{'hit':>7}{'hit_ci':>15}{'be_hit':>8}{'meanR':>8}{'famCI':>18}{'stressR':>9}{'h1R':>7}{'h2R':>7}  verdict"
    print(header)
    rows = [report.overall, *report.by_setup.values(), *report.by_symbol.values(), *report.by_year.values()]
    for g in rows:
        ci = "n/a" if g.hit_rate_ci is None else f"[{g.hit_rate_ci[0]:.2f},{g.hit_rate_ci[1]:.2f}]"
        fam = "n/a" if g.mean_r_ci_family is None else f"[{g.mean_r_ci_family[0]:+.2f},{g.mean_r_ci_family[1]:+.2f}]"
        print(f"{g.name:<26}{g.recorded:>5}{g.not_filled:>5}{g.resolved:>5}{_fmt(g.hit_rate, '.2f'):>7}{ci:>15}"
              f"{_fmt(g.breakeven_hit_rate, '.2f'):>8}{_fmt(g.mean_r, '+.3f'):>8}{fam:>18}"
              f"{_fmt(g.stressed_mean_r, '+.3f'):>9}{_fmt(g.first_half_mean_r, '+.2f'):>7}"
              f"{_fmt(g.second_half_mean_r, '+.2f'):>7}  {g.verdict}")
    for note in report.notes:
        print(f"note: {note}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


REPLAY_FAMILY = "tactical_long_engine"


def replay_trial_params(*, fingerprint: str, spread_bps: float, costs: ReplayCosts) -> dict:
    return {
        "engine_fingerprint": fingerprint,
        "cadence_seconds": 300,
        "window_bars": WINDOW_BARS,
        "spread_bps": spread_bps,
        "fee_bps_per_side": costs.fee_bps_per_side,
        "slippage_bps_per_side": costs.slippage_bps_per_side,
        "outcome": "T1_before_stop_48h_forward_ledger",
    }


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    from trading.research.report import StrategyReport
    from trading.research.robustness import TrialRecord, TrialRegistry, trial_id_for
    from trading.strategies.tactical_dossier import (
        TACTICAL_BLOW_UP_SCENARIOS,
        TACTICAL_FRAGILITY_ANSWERS,
        TACTICAL_HYPOTHESIS,
    )

    parser = argparse.ArgumentParser(description="Replay the live tactical Long radar over historical spot data.")
    parser.add_argument("--data-dir", type=Path, default=Path("research/data/binance_spot"))
    parser.add_argument("--out", type=Path, default=Path("research/data/tactical_replay.json"))
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 1)))
    parser.add_argument("--spread-bps", type=float, default=2.0)
    parser.add_argument("--fee-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=2.0)
    parser.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    parser.add_argument("--record-trial", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    data = load_dataset(args.data_dir)
    costs = ReplayCosts(fee_bps_per_side=args.fee_bps, slippage_bps_per_side=args.slippage_bps)
    fingerprint = engine_fingerprint()
    report, records = run_replay(
        data, spread_bps=args.spread_bps, costs=costs, workers=args.workers, fingerprint=fingerprint,
        progress=True,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    payload["records"] = [asdict(r) for r in records]
    tmp = args.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    tmp.replace(args.out)

    evidence = {k: v for k, v in report.to_dict().items() if k not in {"by_year", "by_symbol"}}
    strategy_report = StrategyReport(
        hypothesis=TACTICAL_HYPOTHESIS,
        fragility_answers=TACTICAL_FRAGILITY_ANSWERS,
        blow_up_scenarios=TACTICAL_BLOW_UP_SCENARIOS,
        evidence=evidence,
    )
    args.out.with_suffix(".md").write_text(strategy_report.render_markdown(), encoding="utf-8")

    params = replay_trial_params(fingerprint=fingerprint, spread_bps=args.spread_bps, costs=costs)
    trial_id = trial_id_for(family=REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(args.trial_registry)
    known = {r.trial_id for r in registry.selection_trials(REPLAY_FAMILY)}
    print(f"trial {trial_id} ({'pre-registered' if trial_id in known else 'NOT pre-registered'})"
          f" · family trials: {len(known | {trial_id})}")
    if args.record_trial:
        registry.append(TrialRecord(
            trial_id=trial_id, family=REPLAY_FAMILY, kind="SELECTION_CANDIDATE",
            description="tactical replay CLI run", params=params,
            dataset={"start": report.start_time, "end": report.end_time},
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            n_trades=report.overall.resolved, sharpe_per_trade=None,
        ))
    print_summary(report)
    print(f"wrote {args.out} and {args.out.with_suffix('.md')} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
