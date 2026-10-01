from __future__ import annotations

import json
import math
import random

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from trading.backtest import carry_replay as cr
from trading.research.robustness import TrialRegistry, trial_id_for

START = 1_704_067_200  # 2024-01-01T00:00:00Z, multiple of 8h
P = cr.PERIOD
N_PERIODS = 150
ZERO_COSTS = cr.CarryCosts(spot_fee_bps=0.0, perp_fee_bps=0.0, slippage_bps_per_leg=0.0)


def _klines(prices, *, volume=1e6, start=START, highs=None):
    rows = {k: [] for k in cr.KLINE_COLUMNS}
    for i, (o, c) in enumerate(zip(prices[:-1], prices[1:])):
        rows["open_time"].append(start + i * P)
        rows["open"].append(o)
        rows["close"].append(c)
        rows["high"].append(max(o, c) * 1.001 if highs is None else highs[i])
        rows["low"].append(min(o, c) * 0.999)
        rows["quote_volume"].append(volume)
    return {k: np.array(v) for k, v in rows.items()}


def _walk(seed, n=N_PERIODS + 1, price=100.0):
    rng = random.Random(seed)
    out = [price]
    for _ in range(n - 1):
        out.append(out[-1] * math.exp(rng.gauss(0, 0.01)))
    return out


def _market(n_symbols=4, *, funding_rate=lambda k, s: 0.0001, basis=0.0, volumes=None, n=N_PERIODS):
    perp, spot, funding = {}, {}, {}
    for k in range(n_symbols):
        symbol = f"S{k}USDT"
        prices = _walk(k, n + 1)
        spot[symbol] = _klines(prices, volume=(volumes or {}).get(symbol, 1e6 * (k + 1)))
        perp[symbol] = _klines([p * (1 + basis) for p in prices], volume=(volumes or {}).get(symbol, 1e6 * (k + 1)))
        funding[symbol] = [(START + (j + 1) * P, funding_rate(j, k)) for j in range(n)]
    return perp, spot, funding


# ---------------------------------------------------------------------------
# Data alignment
# ---------------------------------------------------------------------------


def test_malformed_misaligned_and_duplicate_candles_are_dropped():
    cols = _klines([100.0, 101.0, 102.0, 103.0])
    for k in cr.KLINE_COLUMNS:
        cols[k] = np.append(cols[k], cols[k][0])           # duplicate
    cols["open_time"] = np.append(cols["open_time"], START + 7)  # misaligned
    for k in ("open", "high", "low", "close", "quote_volume"):
        cols[k] = np.append(cols[k], 1.0)
    cols["high"][1] = 50.0                                   # high below open
    kept, rejected = cr._clean_klines(cols)
    assert rejected == 3 and list(kept["open_time"]) == [START, START + 2 * P]


def test_funding_paid_at_a_boundary_belongs_to_the_period_that_ends_there():
    perp, spot, _ = _market(1, n=5)
    funding = {"S0USDT": [(START, 0.5e-3), (START + 1, 0.1e-3), (START + P, 0.2e-3), (START + P + 1, 0.3e-3)]}
    m = cr.build_market(perp, spot, funding)
    # paid at O_0 is income of the period before the grid (not held); (O_0, O_1] -> period 0
    assert m.funding[0, 0] == pytest.approx(0.3e-3) and m.funding_rows[0, 0] == 2
    assert m.funding[0, 1] == pytest.approx(0.3e-3) and m.funding_rows[0, 1] == 1


def test_trailing_volume_and_funding_use_only_the_past():
    perp, spot, funding = _market(2)
    m = cr.build_market(perp, spot, funding)
    volume, (signal, complete) = cr.trailing_volume(m), cr.trailing_funding(m)
    k = 120
    assert volume[0, k] == pytest.approx(np.sum(m.perp_qv[0, k - cr.VOLUME_PERIODS:k]))
    assert signal[0, k] == pytest.approx(np.sum(m.funding[0, k - cr.SIGNAL_PERIODS:k]))
    assert complete[0, k]
    assert math.isnan(volume[0, cr.VOLUME_PERIODS - 1]) and not complete[0, cr.SIGNAL_PERIODS - 1]
    m.perp_qv[0, k] = 1e18          # the current candle has not closed at O_k
    m.funding[0, k] = 0.9           # paid after O_k
    assert cr.trailing_volume(m)[0, k] == pytest.approx(volume[0, k])
    assert cr.trailing_funding(m)[0][0, k] == pytest.approx(signal[0, k])


def test_a_funding_hole_makes_the_signal_unavailable():
    perp, spot, funding = _market(1)
    funding["S0USDT"] = [row for j, row in enumerate(funding["S0USDT"]) if j != 110]
    m = cr.build_market(perp, spot, funding)
    _, complete = cr.trailing_funding(m)
    assert not complete[0, 115] and complete[0, 140]


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


def test_zero_basis_carry_earns_exactly_the_funding():
    m = cr.build_market(*_market(3, funding_rate=lambda k, s: 0.0001))
    path = cr.simulate(m, "STATIC_CARRY", ZERO_COSTS, size=3)
    assert path.returns and all(r == pytest.approx(0.01) for r in path.returns)
    assert path.positions[0] == 3 and path.period_open[0] == START + cr.VOLUME_PERIODS * P


def test_costs_are_charged_on_entry_and_exit_only():
    m = cr.build_market(*_market(3, funding_rate=lambda k, s: 0.0))
    costs = cr.CarryCosts(spot_fee_bps=10.0, perp_fee_bps=10.0, slippage_bps_per_leg=0.0)
    path = cr.simulate(m, "STATIC_CARRY", costs, size=3)
    assert path.returns[0] == pytest.approx(-0.20)       # all three enter once
    assert all(r == pytest.approx(0.0) for r in path.returns[1:])
    assert path.entries == 3


def test_signed_carry_skips_pairs_paying_funding():
    m = cr.build_market(*_market(4, funding_rate=lambda k, s: 0.0002 if s % 2 else -0.0002))
    path = cr.simulate(m, "SIGNED_CARRY", ZERO_COSTS, size=4)
    assert set(path.positions) == {2}
    assert all(r == pytest.approx(0.02) for r in path.returns)
    static = cr.simulate(m, "STATIC_CARRY", ZERO_COSTS, size=4)
    assert all(r == pytest.approx(0.0) for r in static.returns)  # pays as much as it earns


def test_universe_is_the_top_by_trailing_perp_volume():
    vols = {"S0USDT": 9e6, "S1USDT": 1e6, "S2USDT": 5e6, "S3USDT": 7e6}
    m = cr.build_market(*_market(4, volumes=vols))
    volume = cr.trailing_volume(m)
    assert [m.symbols[s] for s in cr.universe_at(m, volume, 100, size=2)] == ["S0USDT", "S3USDT"]


def test_delisted_pair_exits_at_its_last_close_with_cost():
    perp, spot, funding = _market(3)
    for d in (perp, spot):
        d["S2USDT"] = {k: v[:110] for k, v in d["S2USDT"].items()}
    m = cr.build_market(perp, spot, funding)
    path = cr.simulate(m, "STATIC_CARRY", cr.CarryCosts(), size=3)
    assert path.forced_exits == 1
    assert path.positions[109 - cr.VOLUME_PERIODS] == 3 and path.positions[110 - cr.VOLUME_PERIODS] == 2


def test_margin_stress_is_counted_once_per_episode():
    perp, spot, funding = _market(1)
    highs = list(perp["S0USDT"]["high"])
    highs[100], highs[101] = perp["S0USDT"]["open"][100] * 3, perp["S0USDT"]["open"][101] * 3
    perp["S0USDT"]["high"] = np.array(highs)
    m = cr.build_market(perp, spot, funding)
    path = cr.simulate(m, "STATIC_CARRY", ZERO_COSTS, size=1)
    assert path.margin_stress == {"1.5x": 1, "2x": 1} and path.stressed_symbols == {"S0USDT"}


def test_future_data_cannot_change_past_decisions():
    def rate(k, s):
        return 0.0001 * (s - 1.5) + 0.00005 * math.sin(k / 4 + s)

    cut = 120
    base = _market(4, funding_rate=rate)
    poisoned = _market(4, funding_rate=lambda k, s: rate(k, s) if k < cut else -0.05)
    for d in (poisoned[0], poisoned[1]):
        for sym in d:
            late = d[sym]["open_time"] > START + cut * P
            for col in ("open", "high", "low", "close"):
                d[sym][col] = np.where(late, d[sym][col] * 2, d[sym][col])
            d[sym]["quote_volume"] = np.where(late, 1e12, d[sym]["quote_volume"])

    def by_period(path):
        return {t: (n, r) for t, n, r in zip(path.period_open, path.positions, path.returns)}

    a = by_period(cr.simulate(cr.build_market(*base), "SIGNED_CARRY", cr.CarryCosts(), size=3))
    b = by_period(cr.simulate(cr.build_market(*poisoned), "SIGNED_CARRY", cr.CarryCosts(), size=3))
    settled = [t for t in a if t <= START + (cut - 1) * P]
    assert len(settled) > 10 and any(a[t][0] for t in settled)  # the comparison is not vacuous
    for t in settled:
        assert b[t][0] == a[t][0] and b[t][1] == pytest.approx(a[t][1])
    later = [t for t in a if t > START + (cut + 2) * P]
    assert any(a[t] != b.get(t) for t in later)  # the poison does show up afterwards


# ---------------------------------------------------------------------------
# Pre-declared decision rules
# ---------------------------------------------------------------------------


def _path(daily_returns):
    path = cr.PortfolioPath(margin_stress={})
    for d, r in enumerate(daily_returns):
        for j in range(3):
            path.period_open.append(START + d * 86_400 + j * P)
            path.returns.append(r / 3)
            path.funding.append(r / 3)
            path.basis.append(0.0)
            path.costs.append(0.0)
            path.positions.append(5)
    path.position_periods = len(path.returns) * 5
    return path


def test_verdicts_follow_the_rules():
    rng = random.Random(1)
    good = [0.05 + rng.gauss(0, 0.1) for _ in range(800)]
    assert cr.variant_stats("x", _path(good), _path(good)).verdict == "PASS_CANDIDATE"
    stressed_bad = [g - 0.2 for g in good]
    assert cr.variant_stats("x", _path(good), _path(stressed_bad)).verdict == "NO_EDGE"
    losing = [-0.05 + rng.gauss(0, 0.1) for _ in range(800)]
    assert cr.variant_stats("x", _path(losing), _path(losing)).verdict == "NEGATIVE"
    short = good[:300]
    assert cr.variant_stats("x", _path(short), _path(short)).verdict == "INSUFFICIENT"
    decaying = [0.15 + rng.gauss(0, 0.05) for _ in range(400)] + [-0.02 + rng.gauss(0, 0.05) for _ in range(400)]
    stats = cr.variant_stats("x", _path(decaying), _path(decaying))
    assert stats.ci_family_daily[0] > 0 and stats.second_half_annual_pct < 0 and stats.verdict == "NO_EDGE"


def test_report_decomposes_funding_basis_and_costs():
    m = cr.build_market(*_market(3, funding_rate=lambda k, s: 0.0001))
    report = cr.run_replay(m, costs=cr.CarryCosts(), size=3)
    v = report.variants["STATIC_CARRY"]
    assert v.annual_funding_pct == pytest.approx(0.01 * 3 * 365, rel=1e-6)
    assert v.annual_basis_pct == pytest.approx(0.0, abs=1e-9) and v.annual_cost_pct > 0
    assert report.to_dict()["can_authorize_trade"] is False


# ---------------------------------------------------------------------------
# CLI and pre-registration
# ---------------------------------------------------------------------------


def test_cli_end_to_end(tmp_path, capsys, monkeypatch):
    data = tmp_path / "carry"
    perp, spot, funding = _market(3)
    symbols = {}
    for sym in perp:
        for kind, d in (("perp", perp), ("spot", spot)):
            (data / kind).mkdir(parents=True, exist_ok=True)
            pd.DataFrame(d[sym]).to_parquet(data / kind / f"{sym}.parquet", index=False)
        (data / "funding").mkdir(parents=True, exist_ok=True)
        pd.DataFrame(funding[sym], columns=["funding_time", "rate"]).to_parquet(data / "funding" / f"{sym}.parquet", index=False)
        symbols[sym] = {"spot": sym, "months": ["2024-01"]}
    (data / "manifest.json").write_text(json.dumps({"candidates": 3, "stopped_before_window_end": [], "symbols": symbols}))
    monkeypatch.setattr(cr, "UNIVERSE_SIZE", 3)
    out = tmp_path / "carry.json"
    registry = tmp_path / "registry.jsonl"
    argv = ["--data-dir", str(data), "--out", str(out), "--trial-registry", str(registry)]
    assert cr._cli([*argv, "--record-trial"]) == 0
    assert "NOT pre-registered" in capsys.readouterr().out
    payload = json.loads(out.read_text())
    assert set(payload["variants"]) == set(cr.VARIANTS) and payload["can_authorize_trade"] is False
    assert "WHAT COULD BLOW UP" in out.with_suffix(".md").read_text().upper()
    assert len(TrialRegistry(registry).selection_trials(cr.REPLAY_FAMILY)) == 1
    assert cr._cli(argv) == 0 and "(pre-registered)" in capsys.readouterr().out


def test_carry_replay_is_pre_registered_for_the_current_code():
    """Tripwire: a change to the data builder, parsers or replay is a new trial."""

    params = cr.replay_trial_params(fingerprint=cr.engine_fingerprint(), costs=cr.CarryCosts())
    trial_id = trial_id_for(family=cr.REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(cr.REPO_ROOT / "research" / "trials" / "registry.jsonl")
    assert trial_id in {r.trial_id for r in registry.selection_trials(cr.REPLAY_FAMILY)}
