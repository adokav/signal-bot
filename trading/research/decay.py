"""Signal decay analysis (spec §19).

For each signal time ``t`` and horizon ``h`` the forward return is measured
from the close of the last candle visible at ``t`` to the close of the last
candle whose close is at or before ``t + h``. Rules:

- Horizons finer than the candle interval are rejected: 1-minute decay
  cannot be measured from hourly bars, and pretending otherwise would be
  interpolation, not evidence.
- A horizon whose end falls outside the data (or into a gap) is dropped
  for that signal rather than truncated.
- Overlapping forward windows are not independent (AGENTS.md §7). Each
  horizon reports the full-sample mean and a **thinned** sample in which
  kept signals are at least ``h`` apart; the t-statistic uses the thinned
  sample only.
- Every horizon also reports the unconditional forward return of the same
  instrument (baseline). A long signal in a bull market "works" through
  beta alone; ``excess_mean_pct`` is what the signal adds beyond that.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import asdict, dataclass
from typing import Protocol, Sequence

from trading.research.metrics import mean, percentile, sample_stdev


class CandleLike(Protocol):
    open_time: int
    close_time: int
    close: float


@dataclass(frozen=True)
class HorizonStats:
    horizon_seconds: int
    n_signals: int
    n_independent: int
    mean_return_pct: float | None
    median_return_pct: float | None
    hit_rate: float | None
    independent_mean_pct: float | None
    independent_t_stat: float | None
    baseline_mean_pct: float | None
    excess_mean_pct: float | None


@dataclass(frozen=True)
class DecayProfile:
    bar_interval_seconds: int
    horizons: tuple[HorizonStats, ...]
    peak_horizon_seconds: int | None
    fade_horizon_seconds: int | None
    min_independent: int

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["horizons"] = [asdict(h) for h in self.horizons]
        return payload


def _thin(times: Sequence[int], spacing: int) -> list[int]:
    kept: list[int] = []
    for t in sorted(times):
        if not kept or t - kept[-1] >= spacing:
            kept.append(t)
    return kept


def _forward_return(
    t: int,
    horizon: int,
    close_times: Sequence[int],
    closes: Sequence[float],
    interval: int,
) -> float | None:
    start_idx = bisect_right(close_times, t) - 1
    end_idx = bisect_right(close_times, t + horizon) - 1
    if start_idx < 0 or end_idx <= start_idx:
        return None
    if t - close_times[start_idx] >= interval:
        return None  # entry price stale relative to the signal
    if (t + horizon) - close_times[end_idx] >= interval:
        return None  # data ends or gaps before the horizon closes
    return (closes[end_idx] / closes[start_idx] - 1.0) * 100.0


def signal_decay(
    signal_times: Sequence[int],
    candles: Sequence[CandleLike],
    *,
    horizons_seconds: Sequence[int],
    min_independent: int = 30,
) -> DecayProfile:
    if len(candles) < 2:
        raise ValueError("need at least two candles")
    interval = candles[1].open_time - candles[0].open_time
    if interval <= 0:
        raise ValueError("candles must be strictly chronological")
    for i in range(1, len(candles)):
        if candles[i].open_time - candles[i - 1].open_time <= 0:
            raise ValueError("candles must be strictly chronological")
    if not horizons_seconds:
        raise ValueError("at least one horizon is required")
    for h in horizons_seconds:
        if h < interval:
            raise ValueError(
                f"horizon {h}s is finer than the {interval}s candle interval"
            )
    close_times = [c.close_time for c in candles]
    closes = [c.close for c in candles]

    stats: list[HorizonStats] = []
    for h in sorted(horizons_seconds):
        rets = [
            r for r in (_forward_return(t, h, close_times, closes, interval) for t in signal_times)
            if r is not None
        ]
        independent = [
            r for r in (
                _forward_return(t, h, close_times, closes, interval)
                for t in _thin(signal_times, h)
            )
            if r is not None
        ]
        baseline = [
            r for r in (
                _forward_return(t, h, close_times, closes, interval)
                for t in _thin(close_times, h)
            )
            if r is not None
        ]
        ind_mean = mean(independent) if independent else None
        ind_sd = sample_stdev(independent)
        t_stat = (
            ind_mean / (ind_sd / math.sqrt(len(independent)))
            if ind_mean is not None and ind_sd > 0 and len(independent) >= 2
            else None
        )
        base_mean = mean(baseline) if baseline else None
        stats.append(
            HorizonStats(
                horizon_seconds=h,
                n_signals=len(rets),
                n_independent=len(independent),
                mean_return_pct=mean(rets) if rets else None,
                median_return_pct=percentile(rets, 0.5) if rets else None,
                hit_rate=(sum(1 for r in rets if r > 0) / len(rets)) if rets else None,
                independent_mean_pct=ind_mean,
                independent_t_stat=t_stat,
                baseline_mean_pct=base_mean,
                excess_mean_pct=(
                    ind_mean - base_mean if ind_mean is not None and base_mean is not None else None
                ),
            )
        )

    eligible = [
        s for s in stats
        if s.n_independent >= min_independent and s.independent_t_stat is not None
    ]
    peak = max(eligible, key=lambda s: s.independent_t_stat, default=None)
    fade = None
    if peak is not None and peak.independent_t_stat and peak.independent_t_stat > 0:
        for s in stats:
            if s.horizon_seconds <= peak.horizon_seconds:
                continue
            if s.independent_t_stat is None or s.independent_t_stat < peak.independent_t_stat / 2:
                fade = s.horizon_seconds
                break
    return DecayProfile(
        bar_interval_seconds=interval,
        horizons=tuple(stats),
        peak_horizon_seconds=peak.horizon_seconds if peak else None,
        fade_horizon_seconds=fade,
        min_independent=min_independent,
    )
