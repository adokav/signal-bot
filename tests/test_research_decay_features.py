from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from trading.research.decay import signal_decay
from trading.research.features import (
    DataAlignmentError,
    relative_strength,
    trailing_percentile_rank,
    trailing_zscore,
)
from trading.research.regime import trend_state, trend_vol_regime, volatility_state


@dataclass(frozen=True)
class Bar:
    open_time: int
    close_time: int
    close: float


def _hourly(closes, start=0):
    return [Bar(start + i * 3600, start + i * 3600 + 3599, c) for i, c in enumerate(closes)]


def test_decay_rejects_horizons_finer_than_the_data():
    bars = _hourly([100.0] * 10)
    with pytest.raises(ValueError, match="finer than"):
        signal_decay([3599], bars, horizons_seconds=[60])


def test_decay_drops_horizons_beyond_available_data():
    bars = _hourly([100.0 + i for i in range(10)])
    profile = signal_decay([bars[2].close_time], bars, horizons_seconds=[3600, 100 * 3600])
    short, long = profile.horizons
    assert short.n_signals == 1
    assert short.mean_return_pct == pytest.approx((103.0 / 102.0 - 1) * 100)
    assert long.n_signals == 0 and long.mean_return_pct is None


def test_decay_thins_overlapping_signals():
    bars = _hourly([100.0 + i * 0.1 for i in range(200)])
    signals = [bars[i].close_time for i in range(0, 150)]
    profile = signal_decay(signals, bars, horizons_seconds=[24 * 3600], min_independent=1)
    h = profile.horizons[0]
    assert h.n_signals == 150
    assert h.n_independent == 7  # one kept per 24 bars over 150 signals


def test_decay_signal_on_pure_drift_has_no_excess_over_baseline():
    closes = [100.0 * math.exp(0.001 * i) for i in range(500)]
    bars = _hourly(closes)
    signals = [bars[i].close_time for i in range(0, 400, 5)]
    profile = signal_decay(signals, bars, horizons_seconds=[4 * 3600, 24 * 3600], min_independent=1)
    for h in profile.horizons:
        assert h.independent_mean_pct > 0
        assert h.excess_mean_pct == pytest.approx(0.0, abs=1e-9)


def test_trailing_zscore_uses_only_prior_values():
    values = [1.0, 2.0, 1.0, 2.0, 10.0, 1.0]
    z = trailing_zscore(values, window=4)
    assert z[:4] == [None, None, None, None]
    ref = [1.0, 2.0, 1.0, 2.0]
    mu = 1.5
    sd = math.sqrt(sum((v - mu) ** 2 for v in ref) / 3)
    assert z[4] == pytest.approx((10.0 - mu) / sd)
    # Appending future values must not change past outputs.
    assert trailing_zscore(values + [50.0], window=4)[:6] == z


def test_trailing_percentile_rank():
    ranks = trailing_percentile_rank([1, 2, 3, 4, 2.5], window=4)
    assert ranks[4] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        trailing_zscore([1.0, math.nan, 2.0], window=2)


@dataclass(frozen=True)
class C:
    close_time: int
    close: float


def test_relative_strength_matches_spec_examples():
    asset = [C(0, 100.0), C(1, 102.0)]
    market = [C(0, 100.0), C(1, 96.0)]
    rs = relative_strength(asset, market, lookback=1)[1]
    assert rs == pytest.approx((math.log(1.02) - math.log(0.96)) * 100)
    assert rs > 5
    weak = relative_strength([C(0, 100.0), C(1, 103.0)], [C(0, 100.0), C(1, 107.0)], lookback=1)[1]
    assert weak < 0


def test_misaligned_series_are_refused():
    with pytest.raises(DataAlignmentError):
        relative_strength([C(0, 1.0), C(1, 1.0)], [C(0, 1.0), C(2, 1.0)], lookback=1)
    with pytest.raises(DataAlignmentError):
        relative_strength([C(0, 1.0)], [C(0, 1.0), C(1, 1.0)], lookback=1)


def test_regime_labels_unknown_without_history_and_are_point_in_time():
    assert trend_vol_regime([100.0] * 50) == "UNKNOWN"
    up = [100.0 * math.exp(0.002 * i) for i in range(300)]
    assert trend_state(up) == "UP"
    down = list(reversed(up))
    assert trend_state(down) == "DOWN"
    wiggle = [100.0 * (1 + 0.01 * math.sin(i)) * math.exp(0.001 * i) for i in range(500)]
    label = trend_vol_regime(wiggle[:450])
    assert label == trend_vol_regime(wiggle[:450])
    assert trend_vol_regime(wiggle[:450]) == trend_vol_regime(list(wiggle[:450]))
    assert volatility_state(wiggle[:450]) in {"LOW_VOL", "NORMAL_VOL", "HIGH_VOL"}
    with pytest.raises(ValueError):
        trend_vol_regime([100.0, -1.0])
