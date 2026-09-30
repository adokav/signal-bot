"""Overfitting and fragility controls (spec §15, §17).

- **Cost stress** — re-price every trade with commission ×1.5, slippage ×2,
  both combined, and funding ×2. An edge that disappears under realistic
  cost drift is labelled ``EXECUTION_FRAGILE``.
- **Parameter perturbation** — evaluate neighbours of the chosen parameter
  set. A result that only survives at the exact chosen values is
  ``FRAGILE``; an isolated peak is flagged as an overfitting symptom.
- **Bootstrap** — confidence interval of expectancy (moving-block resampling
  so mild serial dependence is not ignored).
- **Monte Carlo drawdown** — distribution of max drawdown under resampled
  trade sequences; the realized path is one draw, not the risk.
- **Deflated Sharpe ratio** — Bailey & López de Prado (2014): the
  probability that the true Sharpe is above what the best of ``N`` null
  trials would show by luck. Needs an honest trial count (``TrialRegistry``).

Spread note: the backtest cost model has no separate spread term — its
slippage figure stands in for half-spread crossing plus impact. "Spread ×2"
is therefore covered by the slippage ×2 scenario rather than invented.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import NormalDist
from typing import Callable, Mapping, Protocol, Sequence

from trading.research.metrics import (
    max_drawdown_pct,
    mean,
    moments,
    percentile,
    sample_stdev,
)


EULER_GAMMA = 0.5772156649015329
_NORMAL = NormalDist()


# ---------------------------------------------------------------------------
# Cost stress
# ---------------------------------------------------------------------------


class CostedTrade(Protocol):
    scale: float
    gross_return_pct: float  # already multiplied by scale

    @property
    def cost(self): ...  # CostBreakdown per unit notional


@dataclass(frozen=True)
class CostStressScenario:
    name: str
    fee_multiplier: float = 1.0
    slippage_multiplier: float = 1.0
    funding_multiplier: float = 1.0


DEFAULT_COST_SCENARIOS: tuple[CostStressScenario, ...] = (
    CostStressScenario("base"),
    CostStressScenario("commission_x1_5", fee_multiplier=1.5),
    CostStressScenario("slippage_x2", slippage_multiplier=2.0),
    CostStressScenario("commission_x1_5_slippage_x2", fee_multiplier=1.5, slippage_multiplier=2.0),
    CostStressScenario("funding_x2", funding_multiplier=2.0),
)


def stressed_net_returns(
    trades: Sequence[CostedTrade], scenario: CostStressScenario
) -> list[float]:
    out: list[float] = []
    for trade in trades:
        cost = trade.cost
        unit_cost = (
            cost.taker_fee_pct * scenario.fee_multiplier
            + cost.slippage_pct * scenario.slippage_multiplier
            + cost.funding_pct * scenario.funding_multiplier
        )
        out.append(trade.gross_return_pct - unit_cost * trade.scale)
    return out


@dataclass(frozen=True)
class CostStressResult:
    scenario: str
    expectancy_pct: float
    total_net_return_pct: float
    max_drawdown_pct: float


@dataclass(frozen=True)
class CostStressReport:
    results: tuple[CostStressResult, ...]
    verdict: str  # NO_EDGE | EXECUTION_FRAGILE | COST_ROBUST

    def to_dict(self) -> dict:
        return {"results": [asdict(r) for r in self.results], "verdict": self.verdict}


def run_cost_stress(
    trades: Sequence[CostedTrade],
    scenarios: Sequence[CostStressScenario] = DEFAULT_COST_SCENARIOS,
) -> CostStressReport:
    if not scenarios or scenarios[0].name != "base":
        raise ValueError("first scenario must be the unstressed base")
    results = []
    for scenario in scenarios:
        net = stressed_net_returns(trades, scenario)
        results.append(
            CostStressResult(
                scenario=scenario.name,
                expectancy_pct=mean(net),
                total_net_return_pct=sum(net),
                max_drawdown_pct=max_drawdown_pct(net),
            )
        )
    base = results[0].expectancy_pct
    if not trades or base <= 0:
        verdict = "NO_EDGE"
    elif any(r.expectancy_pct <= 0 for r in results[1:]):
        verdict = "EXECUTION_FRAGILE"
    else:
        verdict = "COST_ROBUST"
    return CostStressReport(results=tuple(results), verdict=verdict)


# ---------------------------------------------------------------------------
# Parameter perturbation
# ---------------------------------------------------------------------------


def perturbation_neighbors(
    base: Mapping[str, float | int],
    *,
    keys: Sequence[str],
    relative_steps: Sequence[float] = (-0.2, -0.1, 0.1, 0.2),
) -> list[tuple[str, dict[str, float | int]]]:
    """One-at-a-time neighbours of ``base``. Integer params stay integers."""

    neighbors: list[tuple[str, dict[str, float | int]]] = []
    for key in keys:
        value = base[key]
        seen = {value}
        for step in relative_steps:
            if isinstance(value, bool):
                raise ValueError("boolean parameters cannot be perturbed")
            if isinstance(value, int):
                candidate: float | int = max(1, int(round(value * (1.0 + step))))
            else:
                candidate = value * (1.0 + step)
            if candidate in seen:
                continue
            seen.add(candidate)
            params = dict(base)
            params[key] = candidate
            neighbors.append((f"{key}{step:+.0%}", params))
    return neighbors


@dataclass(frozen=True)
class PerturbationResult:
    label: str
    params: dict
    metric: float | None  # None: neighbour produced an invalid configuration
    n_trades: int


@dataclass(frozen=True)
class StabilityVerdict:
    metric_name: str
    base_metric: float
    worst_neighbor_metric: float | None
    median_neighbor_metric: float | None
    n_neighbors: int
    isolated_peak: bool
    verdict: str  # NO_EDGE | FRAGILE | ROBUST | NOT_RUN
    neighbors: tuple[PerturbationResult, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["neighbors"] = [asdict(n) for n in self.neighbors]
        return payload


def assess_parameter_stability(
    *,
    metric_name: str,
    base_metric: float,
    neighbors: Sequence[PerturbationResult],
    max_relative_degradation: float = 0.5,
) -> StabilityVerdict:
    if not neighbors:
        return StabilityVerdict(metric_name, base_metric, None, None, 0, False, "NOT_RUN")
    values = [n.metric for n in neighbors if n.metric is not None]
    invalid = len(values) < len(neighbors)
    worst = min(values) if values else None
    med = percentile(values, 0.5) if values else None
    isolated = bool(
        values and base_metric > 0 and base_metric > max(values)
        and base_metric > 1.5 * max(med or 0.0, 0.0)
    )
    if base_metric <= 0:
        verdict = "NO_EDGE"
    elif invalid or worst is None or worst <= 0 or worst < base_metric * (1.0 - max_relative_degradation):
        verdict = "FRAGILE"
    else:
        verdict = "ROBUST"
    return StabilityVerdict(
        metric_name=metric_name,
        base_metric=base_metric,
        worst_neighbor_metric=worst,
        median_neighbor_metric=med,
        n_neighbors=len(neighbors),
        isolated_peak=isolated,
        verdict=verdict,
        neighbors=tuple(neighbors),
    )


def run_perturbation(
    base_params: Mapping[str, float | int],
    *,
    keys: Sequence[str],
    evaluate: Callable[[dict], tuple[float | None, int]],
    metric_name: str,
    base_metric: float,
) -> StabilityVerdict:
    results = []
    for label, params in perturbation_neighbors(base_params, keys=keys):
        metric, n = evaluate(params)
        results.append(PerturbationResult(label=label, params=params, metric=metric, n_trades=n))
    return assess_parameter_stability(
        metric_name=metric_name, base_metric=base_metric, neighbors=results
    )


# ---------------------------------------------------------------------------
# Bootstrap and Monte Carlo
# ---------------------------------------------------------------------------


def _block_resample(values: Sequence[float], rng: random.Random, block_size: int) -> list[float]:
    n = len(values)
    out: list[float] = []
    while len(out) < n:
        start = rng.randrange(n)
        for offset in range(block_size):
            out.append(values[(start + offset) % n])
            if len(out) == n:
                break
    return out


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    n_resamples: int = 2000,
    alpha: float = 0.05,
    block_size: int = 1,
    seed: int = 0,
) -> tuple[float, float] | None:
    """Percentile CI of the mean; ``None`` when fewer than two values."""

    if len(values) < 2:
        return None
    if not 0 < alpha < 1 or n_resamples < 100 or block_size < 1:
        raise ValueError("invalid bootstrap arguments")
    rng = random.Random(seed)
    means = [mean(_block_resample(values, rng, block_size)) for _ in range(n_resamples)]
    return percentile(means, alpha / 2), percentile(means, 1 - alpha / 2)


@dataclass(frozen=True)
class MonteCarloDrawdown:
    n_paths: int
    method: str
    realized_max_dd_pct: float
    median_max_dd_pct: float
    p05_max_dd_pct: float  # 95% of paths are no worse than this
    p01_max_dd_pct: float
    worst_max_dd_pct: float
    threshold_pct: float | None
    prob_breach_threshold: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def monte_carlo_drawdown(
    returns: Sequence[float],
    *,
    n_paths: int = 2000,
    seed: int = 0,
    method: str = "bootstrap",
    threshold_pct: float | None = None,
) -> MonteCarloDrawdown | None:
    """``bootstrap`` resamples with replacement (sample + order risk);
    ``shuffle`` permutes the realized trades (order risk only)."""

    if len(returns) < 2:
        return None
    if method not in {"bootstrap", "shuffle"}:
        raise ValueError("method must be 'bootstrap' or 'shuffle'")
    rng = random.Random(seed)
    values = list(returns)
    dds: list[float] = []
    for _ in range(n_paths):
        if method == "shuffle":
            path = values[:]
            rng.shuffle(path)
        else:
            path = [values[rng.randrange(len(values))] for _ in values]
        dds.append(max_drawdown_pct(path))
    breach = None
    if threshold_pct is not None:
        limit = -abs(threshold_pct)
        breach = sum(1 for d in dds if d <= limit) / n_paths
    return MonteCarloDrawdown(
        n_paths=n_paths,
        method=method,
        realized_max_dd_pct=max_drawdown_pct(values),
        median_max_dd_pct=percentile(dds, 0.5),
        p05_max_dd_pct=percentile(dds, 0.05),
        p01_max_dd_pct=percentile(dds, 0.01),
        worst_max_dd_pct=min(dds),
        threshold_pct=threshold_pct,
        prob_breach_threshold=breach,
    )


# ---------------------------------------------------------------------------
# Deflated Sharpe ratio
# ---------------------------------------------------------------------------


def probabilistic_sharpe_ratio(
    *,
    sharpe: float,
    n_obs: int,
    skewness: float,
    kurtosis: float,
    benchmark_sharpe: float = 0.0,
) -> float | None:
    """P(true per-period Sharpe > benchmark). ``kurtosis`` is non-excess."""

    if n_obs < 2:
        return None
    denom = 1.0 - skewness * sharpe + (kurtosis - 1.0) / 4.0 * sharpe * sharpe
    if denom <= 0:
        return None
    z = (sharpe - benchmark_sharpe) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return _NORMAL.cdf(z)


def expected_max_sharpe(*, n_trials: int, sharpe_variance: float) -> float:
    """Expected best per-period Sharpe among ``n_trials`` skill-less trials."""

    if n_trials <= 1:
        return 0.0
    if sharpe_variance < 0:
        raise ValueError("variance cannot be negative")
    sd = math.sqrt(sharpe_variance)
    a = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    b = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return sd * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b)


@dataclass(frozen=True)
class DeflatedSharpe:
    sharpe_per_trade: float
    n_obs: int
    n_trials: int
    sharpe_variance: float
    variance_source: str
    expected_max_null_sharpe: float
    deflated_sharpe_probability: float | None
    probabilistic_sharpe_vs_zero: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def deflated_sharpe(
    returns: Sequence[float],
    *,
    n_trials: int,
    trial_sharpes: Sequence[float] = (),
) -> DeflatedSharpe | None:
    """Deflated Sharpe from per-trade returns and the honest trial count.

    Variance across trials comes from the registry's recorded Sharpes when
    at least two are known; otherwise the sampling variance of a Sharpe
    estimate under the null, ``1 / (T - 1)``, is used and labelled.
    """

    n = len(returns)
    if n < 4 or n_trials < 1:
        return None
    sd = sample_stdev(returns)
    if sd <= 0:
        return None
    sr = mean(returns) / sd
    skew, kurt = moments(returns)
    if skew is None or kurt is None:
        return None
    if len(trial_sharpes) >= 2:
        variance = sample_stdev(list(trial_sharpes)) ** 2
        source = "registry_trial_sharpes"
    else:
        variance = 1.0 / (n - 1)
        source = "null_sampling_variance"
    sr0 = expected_max_sharpe(n_trials=n_trials, sharpe_variance=variance)
    return DeflatedSharpe(
        sharpe_per_trade=sr,
        n_obs=n,
        n_trials=n_trials,
        sharpe_variance=variance,
        variance_source=source,
        expected_max_null_sharpe=sr0,
        deflated_sharpe_probability=probabilistic_sharpe_ratio(
            sharpe=sr, n_obs=n, skewness=skew, kurtosis=kurt, benchmark_sharpe=sr0
        ),
        probabilistic_sharpe_vs_zero=probabilistic_sharpe_ratio(
            sharpe=sr, n_obs=n, skewness=skew, kurtosis=kurt
        ),
    )


# ---------------------------------------------------------------------------
# Trial registry
# ---------------------------------------------------------------------------


TRIAL_KINDS = {"SELECTION_CANDIDATE", "SANITY"}


def trial_id_for(*, family: str, params: Mapping, dataset: Mapping) -> str:
    canonical = json.dumps(
        {"family": family, "params": params, "dataset": dataset},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    family: str
    kind: str
    description: str
    params: dict
    dataset: dict
    recorded_at: str
    n_trades: int | None
    sharpe_per_trade: float | None

    def __post_init__(self) -> None:
        if self.kind not in TRIAL_KINDS:
            raise ValueError(f"unknown trial kind {self.kind!r}")
        if not self.family or not self.trial_id:
            raise ValueError("trial needs a family and id")


class TrialRegistry:
    """Append-only record of every strategy/parameter set evaluated.

    Stored as JSONL. A malformed line raises instead of being skipped: an
    undercounted registry would make the deflated Sharpe look better than
    it is (fail closed, AGENTS.md §2).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[TrialRecord]:
        if not self.path.exists():
            return []
        records: list[TrialRecord] = []
        for number, line in enumerate(self.path.read_text("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                records.append(TrialRecord(**payload))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"malformed trial registry line {number}") from exc
        ids = [r.trial_id for r in records]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate trial ids in registry")
        return records

    def selection_trials(self, family: str) -> list[TrialRecord]:
        return [r for r in self.load() if r.family == family and r.kind == "SELECTION_CANDIDATE"]

    def append(self, record: TrialRecord) -> bool:
        """Atomically append ``record``; returns False if already present."""

        existing = self.load()
        if any(r.trial_id == record.trial_id for r in existing):
            return False
        lines = [json.dumps(asdict(r), sort_keys=True) for r in (*existing, record)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        return True
