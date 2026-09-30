from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from trading.backtest.cost_model import CostBreakdown
from trading.research.robustness import (
    PerturbationResult,
    TrialRecord,
    TrialRegistry,
    assess_parameter_stability,
    bootstrap_mean_ci,
    deflated_sharpe,
    expected_max_sharpe,
    monte_carlo_drawdown,
    perturbation_neighbors,
    probabilistic_sharpe_ratio,
    run_cost_stress,
    trial_id_for,
)


@dataclass
class _Trade:
    gross_return_pct: float
    cost: CostBreakdown
    scale: float = 1.0


def _trades(gross: float, n: int = 50) -> list[_Trade]:
    cost = CostBreakdown(taker_fee_pct=0.08, funding_pct=0.02, slippage_pct=0.04)
    return [_Trade(gross_return_pct=gross, cost=cost) for _ in range(n)]


def test_cost_stress_flags_edge_that_dies_under_realistic_cost_drift():
    report = run_cost_stress(_trades(0.20))  # base net 0.06 → slippage×2 net 0.02, fee×1.5 net 0.02, combined −0.02
    by_name = {r.scenario: r.expectancy_pct for r in report.results}
    assert by_name["base"] == pytest.approx(0.06)
    assert by_name["commission_x1_5_slippage_x2"] == pytest.approx(-0.02)
    assert report.verdict == "EXECUTION_FRAGILE"


def test_cost_stress_verdicts_for_robust_and_no_edge():
    assert run_cost_stress(_trades(1.0)).verdict == "COST_ROBUST"
    assert run_cost_stress(_trades(0.05)).verdict == "NO_EDGE"
    assert run_cost_stress([]).verdict == "NO_EDGE"


def test_cost_stress_scales_costs_with_exposure():
    cost = CostBreakdown(taker_fee_pct=0.08, funding_pct=0.0, slippage_pct=0.04)
    half = run_cost_stress([_Trade(gross_return_pct=0.5, cost=cost, scale=0.5)])
    assert half.results[0].expectancy_pct == pytest.approx(0.5 - 0.12 * 0.5)


def test_perturbation_neighbors_keep_integer_types_and_skip_duplicates():
    neighbors = perturbation_neighbors({"lookback": 3, "mult": 1.5}, keys=["lookback", "mult"])
    lookbacks = [p["lookback"] for label, p in neighbors if label.startswith("lookback")]
    assert all(isinstance(v, int) for v in lookbacks)
    assert 3 not in lookbacks
    assert len(lookbacks) == len(set(lookbacks))


def _neighbors(values):
    return [PerturbationResult(label=f"n{i}", params={}, metric=v, n_trades=50) for i, v in enumerate(values)]


def test_stability_verdicts():
    robust = assess_parameter_stability(metric_name="ev", base_metric=0.5, neighbors=_neighbors([0.4, 0.45, 0.55, 0.5]))
    assert robust.verdict == "ROBUST"
    fragile = assess_parameter_stability(metric_name="ev", base_metric=0.5, neighbors=_neighbors([0.4, -0.1, 0.5]))
    assert fragile.verdict == "FRAGILE"
    peak = assess_parameter_stability(metric_name="ev", base_metric=1.0, neighbors=_neighbors([0.1, 0.12, 0.15]))
    assert peak.verdict == "FRAGILE" and peak.isolated_peak
    invalid = assess_parameter_stability(metric_name="ev", base_metric=0.5, neighbors=_neighbors([0.5, None]))
    assert invalid.verdict == "FRAGILE"
    assert assess_parameter_stability(metric_name="ev", base_metric=-0.1, neighbors=_neighbors([0.1])).verdict == "NO_EDGE"
    assert assess_parameter_stability(metric_name="ev", base_metric=0.5, neighbors=[]).verdict == "NOT_RUN"


def test_bootstrap_is_deterministic_and_brackets_the_mean():
    values = [0.5, -0.3, 1.2, -0.8, 0.1, 0.4, -0.2, 0.9] * 5
    a = bootstrap_mean_ci(values, seed=3)
    b = bootstrap_mean_ci(values, seed=3)
    assert a == b
    low, high = a
    assert low < sum(values) / len(values) < high
    assert bootstrap_mean_ci([1.0]) is None


def test_monte_carlo_drawdown_is_at_least_as_bad_as_typical_path():
    values = [1.0, -1.0, 0.5, -0.5, 2.0, -1.5] * 10
    mc = monte_carlo_drawdown(values, seed=1, threshold_pct=5.0)
    assert mc.p05_max_dd_pct <= mc.median_max_dd_pct <= 0
    assert mc.worst_max_dd_pct <= mc.p01_max_dd_pct
    assert 0 <= mc.prob_breach_threshold <= 1
    with pytest.raises(ValueError):
        monte_carlo_drawdown(values, method="magic")


def test_deflated_sharpe_penalises_many_trials():
    assert expected_max_sharpe(n_trials=1, sharpe_variance=0.1) == 0.0
    assert expected_max_sharpe(n_trials=100, sharpe_variance=0.01) > expected_max_sharpe(n_trials=10, sharpe_variance=0.01)
    returns = [0.3, -0.2, 0.5, -0.1, 0.4, -0.3, 0.2, 0.1] * 20
    one = deflated_sharpe(returns, n_trials=1)
    many = deflated_sharpe(returns, n_trials=200)
    assert one.deflated_sharpe_probability > many.deflated_sharpe_probability
    assert one.variance_source == "null_sampling_variance"
    psr = probabilistic_sharpe_ratio(sharpe=0.0, n_obs=100, skewness=0.0, kurtosis=3.0)
    assert psr == pytest.approx(0.5)


def _record(tid: str, sharpe: float | None = None, kind: str = "SELECTION_CANDIDATE") -> TrialRecord:
    return TrialRecord(
        trial_id=tid, family="fam", kind=kind, description="test trial",
        params={"a": 1}, dataset={}, recorded_at="2026-09-30T00:00:00Z",
        n_trades=10, sharpe_per_trade=sharpe,
    )


def test_trial_registry_is_append_only_and_counts_selection_trials(tmp_path):
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    assert registry.load() == []
    assert registry.append(_record("a", 0.1))
    assert not registry.append(_record("a", 0.9))  # duplicate id: no overwrite
    assert registry.append(_record("b"))
    assert registry.append(_record("s", kind="SANITY"))
    assert [r.trial_id for r in registry.selection_trials("fam")] == ["a", "b"]
    assert registry.load()[0].sharpe_per_trade == 0.1


def test_trial_registry_fails_closed_on_corruption(tmp_path):
    path = tmp_path / "trials.jsonl"
    path.write_text(json.dumps({"trial_id": "x"}) + "\nnot json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        TrialRegistry(path).load()


def test_trial_id_ignores_key_order():
    a = trial_id_for(family="f", params={"x": 1, "y": 2}, dataset={})
    b = trial_id_for(family="f", params={"y": 2, "x": 1}, dataset={})
    assert a == b
    assert a != trial_id_for(family="f", params={"x": 1, "y": 3}, dataset={})
