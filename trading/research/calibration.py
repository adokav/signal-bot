"""Probability calibration (spec §2, §11).

A model's confidence score is not a probability. "Bullish 83%" becomes an
empirical probability only after we have compared many past 80-90% calls
with what the market actually did (target hit before stop, or not).

This module keeps three things apart:

- **raw confidence** — whatever number a model/scorer emits;
- **empirical probability** — the observed hit rate of past calls in the
  same confidence band, with its sampling uncertainty (Wilson interval);
- **calibrated probability** — raw confidence mapped through a calibrator
  that was fitted **only** on samples resolved before the evaluation cut.

Point-in-time rules (AGENTS.md §1, §7):

- A sample becomes usable for fitting only at its ``resolved_at`` time.
- The out-of-sample split purges samples that were decided before the cut
  but resolved after it, and embargoes a gap before the test window so
  overlapping outcome windows cannot leak across the split.
- Too little data fails closed: ``InsufficientCalibrationData`` is raised
  and callers must treat the probability as ``UNCALIBRATED``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Protocol, Sequence


MIN_CALIBRATION_SAMPLES = 100
MIN_SAMPLES_PER_CLASS = 10
_EPS = 1e-6


class InsufficientCalibrationData(ValueError):
    """Not enough resolved samples to fit or evaluate a calibrator."""


@dataclass(frozen=True)
class CalibrationSample:
    decided_at: int
    resolved_at: int
    probability: float
    outcome: bool

    def __post_init__(self) -> None:
        if not math.isfinite(self.probability) or not 0.0 <= self.probability <= 1.0:
            raise ValueError("probability must be a finite value within [0, 1]")
        if self.resolved_at <= self.decided_at:
            raise ValueError("an outcome cannot resolve at or before its decision")


@dataclass(frozen=True)
class CalibratedProbability:
    raw: float
    value: float | None
    status: str  # CALIBRATED | UNCALIBRATED
    method: str | None = None
    n_train: int = 0
    fitted_through: int | None = None

    @property
    def is_calibrated(self) -> bool:
        return self.status == "CALIBRATED" and self.value is not None


def uncalibrated(raw: float) -> CalibratedProbability:
    return CalibratedProbability(raw=raw, value=None, status="UNCALIBRATED")


def _validate(probs: Sequence[float], outcomes: Sequence[bool]) -> None:
    if len(probs) != len(outcomes):
        raise ValueError("probabilities and outcomes must align")
    if not probs:
        raise InsufficientCalibrationData("no samples")
    for p in probs:
        if not math.isfinite(p) or not 0.0 <= p <= 1.0:
            raise ValueError("probability outside [0, 1]")


def wilson_interval(successes: int, n: int, *, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial rate; (0, 1) when ``n == 0``."""

    if n <= 0:
        return 0.0, 1.0
    if successes < 0 or successes > n:
        raise ValueError("successes must be within [0, n]")
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def brier_score(probs: Sequence[float], outcomes: Sequence[bool]) -> float:
    _validate(probs, outcomes)
    return sum((p - float(y)) ** 2 for p, y in zip(probs, outcomes)) / len(probs)


@dataclass(frozen=True)
class ReliabilityBin:
    lower: float
    upper: float
    count: int
    mean_predicted: float | None
    observed_rate: float | None
    observed_low: float
    observed_high: float


def reliability_curve(
    probs: Sequence[float],
    outcomes: Sequence[bool],
    *,
    n_bins: int = 10,
) -> list[ReliabilityBin]:
    """Equal-width bins; each reports predicted mean vs observed hit rate."""

    _validate(probs, outcomes)
    if n_bins < 1:
        raise ValueError("n_bins must be >= 1")
    grouped: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for p, y in zip(probs, outcomes):
        index = min(n_bins - 1, int(p * n_bins))
        grouped[index].append((p, y))
    bins: list[ReliabilityBin] = []
    for index, rows in enumerate(grouped):
        count = len(rows)
        hits = sum(1 for _, y in rows if y)
        low, high = wilson_interval(hits, count)
        bins.append(
            ReliabilityBin(
                lower=index / n_bins,
                upper=(index + 1) / n_bins,
                count=count,
                mean_predicted=(sum(p for p, _ in rows) / count) if count else None,
                observed_rate=(hits / count) if count else None,
                observed_low=low,
                observed_high=high,
            )
        )
    return bins


def expected_calibration_error(
    probs: Sequence[float],
    outcomes: Sequence[bool],
    *,
    n_bins: int = 10,
) -> float:
    bins = reliability_curve(probs, outcomes, n_bins=n_bins)
    total = len(probs)
    return sum(
        (b.count / total) * abs(b.observed_rate - b.mean_predicted)
        for b in bins
        if b.count and b.observed_rate is not None and b.mean_predicted is not None
    )


def _require_fit_data(probs: Sequence[float], outcomes: Sequence[bool]) -> None:
    _validate(probs, outcomes)
    positives = sum(1 for y in outcomes if y)
    negatives = len(outcomes) - positives
    if len(probs) < MIN_CALIBRATION_SAMPLES:
        raise InsufficientCalibrationData(
            f"need >= {MIN_CALIBRATION_SAMPLES} resolved samples, got {len(probs)}"
        )
    if positives < MIN_SAMPLES_PER_CLASS or negatives < MIN_SAMPLES_PER_CLASS:
        raise InsufficientCalibrationData("both outcome classes need enough samples")


class Calibrator(Protocol):
    method: str

    def fit(self, probs: Sequence[float], outcomes: Sequence[bool]) -> "Calibrator": ...

    def predict(self, p: float) -> float: ...


class IsotonicCalibrator:
    """Monotone non-decreasing map fitted with pool-adjacent-violators."""

    method = "isotonic"

    def __init__(self) -> None:
        self._xs: list[float] = []
        self._ys: list[float] = []

    def fit(self, probs: Sequence[float], outcomes: Sequence[bool]) -> "IsotonicCalibrator":
        _require_fit_data(probs, outcomes)
        rows = sorted(zip(probs, outcomes), key=lambda row: row[0])
        # Each block: [sum_y, weight, sum_x]
        blocks: list[list[float]] = []
        for p, y in rows:
            blocks.append([float(y), 1.0, p])
            while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
                last = blocks.pop()
                blocks[-1][0] += last[0]
                blocks[-1][1] += last[1]
                blocks[-1][2] += last[2]
        self._xs = [b[2] / b[1] for b in blocks]
        self._ys = [b[0] / b[1] for b in blocks]
        return self

    def predict(self, p: float) -> float:
        if not self._xs:
            raise RuntimeError("calibrator is not fitted")
        if not math.isfinite(p) or not 0.0 <= p <= 1.0:
            raise ValueError("probability outside [0, 1]")
        xs, ys = self._xs, self._ys
        if p <= xs[0]:
            return ys[0]
        if p >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if p <= xs[i]:
                span = xs[i] - xs[i - 1]
                if span <= 0:
                    return ys[i]
                w = (p - xs[i - 1]) / span
                return ys[i - 1] * (1 - w) + ys[i] * w
        return ys[-1]


def _logit(p: float) -> float:
    q = min(1 - _EPS, max(_EPS, p))
    return math.log(q / (1 - q))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class PlattCalibrator:
    """Logistic recalibration ``sigmoid(a * logit(p) + b)`` (Platt scaling).

    Uses Platt's smoothed targets so a perfectly separable sample cannot
    drive the map to 0/1 certainty.
    """

    method = "platt"

    def __init__(self, *, max_iter: int = 100, ridge: float = 1e-6) -> None:
        self.a = 1.0
        self.b = 0.0
        self._fitted = False
        self._max_iter = max_iter
        self._ridge = ridge

    def fit(self, probs: Sequence[float], outcomes: Sequence[bool]) -> "PlattCalibrator":
        _require_fit_data(probs, outcomes)
        n_pos = sum(1 for y in outcomes if y)
        n_neg = len(outcomes) - n_pos
        t_pos = (n_pos + 1.0) / (n_pos + 2.0)
        t_neg = 1.0 / (n_neg + 2.0)
        xs = [_logit(p) for p in probs]
        ts = [t_pos if y else t_neg for y in outcomes]
        a, b = 1.0, 0.0
        for _ in range(self._max_iter):
            g_a = g_b = 0.0
            h_aa = h_ab = h_bb = 0.0
            for x, t in zip(xs, ts):
                q = _sigmoid(a * x + b)
                r = q - t
                w = q * (1 - q)
                g_a += r * x
                g_b += r
                h_aa += w * x * x
                h_ab += w * x
                h_bb += w
            g_a += self._ridge * a
            h_aa += self._ridge
            h_bb += self._ridge
            det = h_aa * h_bb - h_ab * h_ab
            if det <= 0:
                break
            step_a = (h_bb * g_a - h_ab * g_b) / det
            step_b = (h_aa * g_b - h_ab * g_a) / det
            a -= step_a
            b -= step_b
            if abs(step_a) < 1e-10 and abs(step_b) < 1e-10:
                break
        self.a, self.b = a, b
        self._fitted = True
        return self

    def predict(self, p: float) -> float:
        if not self._fitted:
            raise RuntimeError("calibrator is not fitted")
        if not math.isfinite(p) or not 0.0 <= p <= 1.0:
            raise ValueError("probability outside [0, 1]")
        return _sigmoid(self.a * _logit(p) + self.b)


@dataclass(frozen=True)
class CalibrationSplit:
    cut_time: int
    train: tuple[CalibrationSample, ...]
    test: tuple[CalibrationSample, ...]
    purged: int


def chronological_split(
    samples: Sequence[CalibrationSample],
    *,
    train_fraction: float = 0.6,
    embargo_seconds: int = 0,
) -> CalibrationSplit:
    """Split by decision time with purge + embargo.

    - ``cut_time`` is the decision time at the ``train_fraction`` quantile.
    - Train keeps samples whose outcome was **known** by ``cut_time``.
    - Test keeps samples decided strictly after ``cut_time + embargo``.
    - Samples decided before the cut but resolved after it are purged:
      their outcome window overlaps the test period.
    """

    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be within (0, 1)")
    if embargo_seconds < 0:
        raise ValueError("embargo cannot be negative")
    ordered = sorted(samples, key=lambda s: s.decided_at)
    if len(ordered) < 2:
        raise InsufficientCalibrationData("need at least two samples to split")
    cut_index = max(0, min(len(ordered) - 1, int(len(ordered) * train_fraction) - 1))
    cut_time = ordered[cut_index].decided_at
    train = tuple(s for s in ordered if s.decided_at <= cut_time and s.resolved_at <= cut_time)
    test = tuple(s for s in ordered if s.decided_at > cut_time + embargo_seconds)
    purged = len(ordered) - len(train) - len(test)
    return CalibrationSplit(cut_time=cut_time, train=train, test=test, purged=purged)


@dataclass(frozen=True)
class CalibrationEvaluation:
    method: str
    cut_time: int
    n_train: int
    n_test: int
    n_purged: int
    raw_brier: float
    calibrated_brier: float
    raw_ece: float
    calibrated_ece: float
    raw_mean_predicted: float
    calibrated_mean_predicted: float
    observed_rate: float
    overconfidence: float
    verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_out_of_sample(
    samples: Sequence[CalibrationSample],
    *,
    calibrator: Calibrator,
    train_fraction: float = 0.6,
    embargo_seconds: int = 0,
    n_bins: int = 10,
    overconfidence_tolerance: float = 0.05,
) -> tuple[CalibrationEvaluation, Calibrator]:
    """Fit on the train slab only, then score raw vs calibrated on test.

    ``verdict``:
    - ``OVERCONFIDENT`` — raw mean prediction exceeds the observed test hit
      rate by more than the tolerance (the §11 "75% predicted, 54% real"
      case);
    - ``CALIBRATION_HELPS`` / ``CALIBRATION_DOES_NOT_HELP`` — whether the
      fitted map lowered out-of-sample Brier score.
    """

    split = chronological_split(
        samples, train_fraction=train_fraction, embargo_seconds=embargo_seconds
    )
    if len(split.test) < MIN_CALIBRATION_SAMPLES // 2:
        raise InsufficientCalibrationData("test slab too small for an honest evaluation")
    calibrator.fit([s.probability for s in split.train], [s.outcome for s in split.train])
    test_p = [s.probability for s in split.test]
    test_y = [s.outcome for s in split.test]
    calibrated = [calibrator.predict(p) for p in test_p]
    observed = sum(1 for y in test_y if y) / len(test_y)
    raw_mean = sum(test_p) / len(test_p)
    raw_brier = brier_score(test_p, test_y)
    cal_brier = brier_score(calibrated, test_y)
    overconfidence = raw_mean - observed
    if overconfidence > overconfidence_tolerance:
        verdict = "OVERCONFIDENT"
    elif cal_brier < raw_brier:
        verdict = "CALIBRATION_HELPS"
    else:
        verdict = "CALIBRATION_DOES_NOT_HELP"
    evaluation = CalibrationEvaluation(
        method=calibrator.method,
        cut_time=split.cut_time,
        n_train=len(split.train),
        n_test=len(split.test),
        n_purged=split.purged,
        raw_brier=raw_brier,
        calibrated_brier=cal_brier,
        raw_ece=expected_calibration_error(test_p, test_y, n_bins=n_bins),
        calibrated_ece=expected_calibration_error(calibrated, test_y, n_bins=n_bins),
        raw_mean_predicted=raw_mean,
        calibrated_mean_predicted=sum(calibrated) / len(calibrated),
        observed_rate=observed,
        overconfidence=overconfidence,
        verdict=verdict,
    )
    return evaluation, calibrator


def calibrate_as_of(
    raw: float,
    *,
    samples: Sequence[CalibrationSample],
    as_of: int,
    calibrator: Calibrator,
) -> CalibratedProbability:
    """Calibrate ``raw`` using only samples resolved by ``as_of``.

    Fails closed to ``UNCALIBRATED`` when the visible history is too thin.
    """

    visible = [s for s in samples if s.resolved_at <= as_of]
    try:
        calibrator.fit([s.probability for s in visible], [s.outcome for s in visible])
    except InsufficientCalibrationData:
        return uncalibrated(raw)
    return CalibratedProbability(
        raw=raw,
        value=calibrator.predict(raw),
        status="CALIBRATED",
        method=calibrator.method,
        n_train=len(visible),
        fitted_through=max(s.resolved_at for s in visible),
    )
