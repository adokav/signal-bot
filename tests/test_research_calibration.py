from __future__ import annotations

import random

import pytest

from trading.research.calibration import (
    CalibrationSample,
    InsufficientCalibrationData,
    IsotonicCalibrator,
    PlattCalibrator,
    brier_score,
    calibrate_as_of,
    chronological_split,
    evaluate_out_of_sample,
    expected_calibration_error,
    reliability_curve,
    wilson_interval,
)


def _overconfident_samples(n: int = 600, seed: int = 7) -> list[CalibrationSample]:
    """Model says p, reality is 0.5 + (p - 0.5) * 0.3 — the §11 overconfidence case."""

    rng = random.Random(seed)
    out = []
    for i in range(n):
        p = rng.uniform(0.5, 0.95)
        true_p = 0.5 + (p - 0.5) * 0.3
        out.append(
            CalibrationSample(
                decided_at=i * 3600,
                resolved_at=i * 3600 + 7200,
                probability=p,
                outcome=rng.random() < true_p,
            )
        )
    return out


def test_sample_rejects_invalid_probability_and_time_order():
    with pytest.raises(ValueError):
        CalibrationSample(decided_at=0, resolved_at=10, probability=1.2, outcome=True)
    with pytest.raises(ValueError):
        CalibrationSample(decided_at=10, resolved_at=10, probability=0.5, outcome=True)


def test_brier_and_reliability_on_known_values():
    probs = [0.9, 0.9, 0.1, 0.1]
    outcomes = [True, False, False, False]
    assert brier_score(probs, outcomes) == pytest.approx((0.01 + 0.81 + 0.01 + 0.01) / 4)
    bins = reliability_curve(probs, outcomes, n_bins=10)
    top = bins[9]
    assert top.count == 2 and top.observed_rate == pytest.approx(0.5)
    assert top.observed_low < 0.5 < top.observed_high
    assert expected_calibration_error(probs, outcomes) > 0


def test_wilson_interval_is_wide_for_small_samples():
    low, high = wilson_interval(3, 4)
    assert high - low > 0.4
    low_big, high_big = wilson_interval(300, 400)
    assert high_big - low_big < 0.1


def test_overconfident_model_is_detected_out_of_sample():
    evaluation, _ = evaluate_out_of_sample(
        _overconfident_samples(), calibrator=IsotonicCalibrator(), embargo_seconds=7200
    )
    assert evaluation.verdict == "OVERCONFIDENT"
    assert evaluation.raw_mean_predicted - evaluation.observed_rate > 0.1
    assert evaluation.calibrated_brier < evaluation.raw_brier
    assert abs(evaluation.calibrated_mean_predicted - evaluation.observed_rate) < 0.08


def test_split_purges_samples_whose_outcome_overlaps_the_test_window():
    samples = [
        CalibrationSample(decided_at=t, resolved_at=t + 50, probability=0.6, outcome=True)
        for t in range(0, 1000, 10)
    ]
    split = chronological_split(samples, train_fraction=0.5, embargo_seconds=50)
    assert all(s.resolved_at <= split.cut_time for s in split.train)
    assert all(s.decided_at > split.cut_time + 50 for s in split.test)
    assert split.purged > 0
    assert len(split.train) + len(split.test) + split.purged == len(samples)


def test_isotonic_is_monotone():
    samples = _overconfident_samples()
    cal = IsotonicCalibrator().fit([s.probability for s in samples], [s.outcome for s in samples])
    grid = [i / 100 for i in range(101)]
    values = [cal.predict(p) for p in grid]
    assert all(b >= a - 1e-12 for a, b in zip(values, values[1:]))


def test_platt_shrinks_overconfident_scores():
    samples = _overconfident_samples()
    cal = PlattCalibrator().fit([s.probability for s in samples], [s.outcome for s in samples])
    assert cal.predict(0.9) < 0.75
    assert 0.0 < cal.predict(0.5) < 1.0


def test_fit_fails_closed_on_thin_history():
    with pytest.raises(InsufficientCalibrationData):
        IsotonicCalibrator().fit([0.6] * 20, [True] * 10 + [False] * 10)
    with pytest.raises(InsufficientCalibrationData):
        IsotonicCalibrator().fit([0.6] * 200, [True] * 195 + [False] * 5)


def test_calibrate_as_of_only_sees_resolved_samples():
    samples = _overconfident_samples()
    early_cut = samples[50].decided_at
    result = calibrate_as_of(0.9, samples=samples, as_of=early_cut, calibrator=IsotonicCalibrator())
    assert result.status == "UNCALIBRATED"
    assert result.value is None
    late = calibrate_as_of(0.9, samples=samples, as_of=samples[-1].resolved_at, calibrator=IsotonicCalibrator())
    assert late.is_calibrated
    assert late.fitted_through <= samples[-1].resolved_at
    # Future outcomes after the cut must not change the calibrated value.
    cut = samples[400].decided_at
    a = calibrate_as_of(0.9, samples=samples[:420], as_of=cut, calibrator=IsotonicCalibrator())
    b = calibrate_as_of(0.9, samples=samples, as_of=cut, calibrator=IsotonicCalibrator())
    assert a.value == pytest.approx(b.value)
