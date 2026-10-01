from __future__ import annotations

import gzip
import json
import math
import random
from dataclasses import replace

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from acce_unified.liquid_long import calculate_long_metrics
from trading.backtest import liquid_replay as lr
from trading.research.robustness import TrialRegistry, trial_id_for

START = 1_704_067_200  # 2024-01-01T00:00:00Z
BARS = 4 * 96
PARAMS = lr.RadarParams(universe_size=8, min_quote_volume=1_000.0, min_benchmark_members=3)


def _path(seed, *, drift, price=10.0, scale=1e4, bars=BARS):
    rng = random.Random(seed)
    rows, p = [], price
    for i in range(bars):
        o = p
        c = o * math.exp(drift + rng.gauss(0, 0.003))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.0015)))
        low = min(o, c) * (1 - abs(rng.gauss(0, 0.0015)))
        rows.append((START + i * 900, START + i * 900 + 899, o, h, low, c, scale * rng.lognormvariate(0, 0.4) * c))
        p = c
    return rows


def _universe_rows():
    rows = {"BTCUSDT": _path(0, drift=0.0003, price=40_000.0, scale=50.0)}
    for k in range(11):
        rows[f"C{k:02d}USDT"] = _path(k + 1, drift=(0.0006 if k % 2 else -0.0002), scale=10 ** (3 + k / 4))
    rows["USDCUSDT"] = [(r[0], r[1], 1.0, 1.0001, 0.9999, 1.0, 1e9) for r in rows["BTCUSDT"]]
    return rows


def _market(rows=None, **kwargs):
    rows = rows or _universe_rows()
    return lr.build_market({s: lr.rows_to_columns(r) for s, r in rows.items()}, **kwargs)


@pytest.fixture(scope="module")
def market():
    return _market()


@pytest.fixture(scope="module")
def steps(market):
    return lr.evaluate(market, PARAMS)


# ---------------------------------------------------------------------------
# Data handling
# ---------------------------------------------------------------------------


def test_malformed_misaligned_and_duplicate_rows_are_dropped_not_repaired():
    good = (START, START + 899, 10.0, 11.0, 9.0, 10.5, 100.0)
    cols = lr.rows_to_columns([
        good, good,                                           # duplicate
        (START + 900, START + 1799, 10.0, 9.0, 8.0, 8.5, 1.0),  # high below open
        (START + 1800, START + 2699, float("nan"), 11, 9, 10, 1.0),
        (START + 2700 + 7, START + 3606, 10, 11, 9, 10, 1.0),   # not on the 15m grid
        (START + 3600, START + 4499, 10, 11, 9, 10, -1.0),      # negative volume
        (START + 4500, START + 5399, 10, 11, 9, 10, 1.0),
    ])
    kept, rejected = lr.clean_columns(cols)
    assert rejected == 5 and list(kept["open_time"]) == [START, START + 4500]


def test_rolling_24h_needs_every_bar():
    rows = _path(1, drift=0.0)
    gap = rows[:150] + rows[151:]
    m = _market({"AUSDT": rows, "BUSDT": gap})
    a, b = m.index_of["AUSDT"], m.index_of["BUSDT"]
    assert math.isnan(m.qv24[a, 94]) and math.isfinite(m.qv24[a, 95])
    assert m.qv24[a, 200] == pytest.approx(sum(r[6] for r in rows[105:201]))
    assert math.isnan(m.qv24[b, 200]) and math.isfinite(m.qv24[b, 150 + 96])
    assert m.chg24[a, 200] == pytest.approx((rows[200][5] / rows[104][5] - 1) * 100)


def test_metrics_use_only_closed_bars_and_match_the_live_function(market):
    s, i = market.index_of["C03USDT"], 250
    rows = [
        [int(market.grid_open[k]), market.open[s, k], market.high[s, k], market.low[s, k],
         market.close[s, k], market.quote_volume[s, k], int(market.grid_open[k]) + 899, market.quote_volume[s, k]]
        for k in range(i - 95, i + 1)
    ]
    expected = calculate_long_metrics(rows + [rows[-1]])
    assert lr.metrics_at(market, s, i) == expected
    assert expected["completed_candles"] == 96 and expected["last_close"] == market.close[s, i]


def test_a_missing_bar_makes_metrics_unavailable():
    rows = _path(1, drift=0.0)
    m = _market({"AUSDT": rows[:200] + rows[201:], "BUSDT": rows})
    assert lr.metrics_at(m, m.index_of["AUSDT"], 250)["status"] == "PROVIDER_UNAVAILABLE"
    assert lr.metrics_at(m, m.index_of["AUSDT"], 50)["status"] == "INSUFFICIENT_KLINES"


# ---------------------------------------------------------------------------
# The live radar at each step
# ---------------------------------------------------------------------------


def test_universe_is_the_live_selection_by_trailing_volume(market, steps):
    i = 300
    step = steps[i]
    assert step is not None and len(step.universe) == PARAMS.universe_size
    assert market.index_of["USDCUSDT"] not in step.universe  # stablecoin excluded like live
    eligible = [s for s in range(len(market.symbols))
                if market.symbols[s] != "USDCUSDT" and math.isfinite(market.qv24[s, i])]
    by_volume = sorted(eligible, key=lambda s: market.qv24[s, i], reverse=True)[: PARAMS.universe_size]
    assert set(step.universe) == set(by_volume)
    assert set(step.top) <= set(step.ready) and len(step.top) <= PARAMS.top_n


def test_the_radar_does_produce_picks_so_the_checks_are_not_vacuous(steps):
    shown = [s for s in steps if s is not None and s.top]
    assert len(shown) > 20


def test_steps_before_24h_of_history_are_skipped(steps):
    assert all(s is None for s in steps[:95])


def test_a_step_without_btc_data_is_skipped_not_read_as_flat():
    rows = _universe_rows()
    rows["BTCUSDT"] = rows["BTCUSDT"][:200] + rows["BTCUSDT"][201:]
    m = _market(rows)
    assert lr.evaluate_step(m, 250, PARAMS) is None
    assert lr.evaluate_step(m, 300, PARAMS) is not None


def test_capitulation_shows_nothing():
    rows = _universe_rows()
    crash = {}
    for symbol, series in rows.items():
        if symbol == "USDCUSDT":
            crash[symbol] = series
            continue
        out = []
        for k, (ot, ct, o, h, low, c, qv) in enumerate(series):
            f = 1.0 if k < 250 else 0.9 ** min(k - 249, 3)  # everything falls ~27% from bar 250
            out.append((ot, ct, o * f, h * f, low * f, c * f, qv))
        crash[symbol] = out
    m = _market(crash)
    step = lr.evaluate_step(m, 260, PARAMS)
    assert step.regime == "CAPITULATION" and step.top == () and step.ready == ()


def test_future_bars_cannot_change_past_decisions(market, steps):
    cutoff = 280
    poisoned = _universe_rows()
    for symbol, series in poisoned.items():
        poisoned[symbol] = [
            (ot, ct, o * 3, h * 3, low * 3, c * 3, qv * 50) if k > cutoff else (ot, ct, o, h, low, c, qv)
            for k, (ot, ct, o, h, low, c, qv) in enumerate(series)
        ]
    after = lr.evaluate(_market(poisoned), PARAMS)
    assert after[: cutoff + 1] == steps[: cutoff + 1]
    assert after[cutoff + 5:] != steps[cutoff + 5:]  # the poison is visible later, so the test can fail


def test_parallel_matches_sequential(market, steps):
    assert lr.evaluate(market, PARAMS, workers=2, chunks=5) == steps


# ---------------------------------------------------------------------------
# Events and outcomes
# ---------------------------------------------------------------------------


def test_entry_events_are_new_entries_and_a_skipped_scan_keeps_the_list():
    a = lr.Step(universe=(0, 1, 2), regime="RISK_ON", top=(0, 1), ready=(0, 1, 2))
    b = lr.Step(universe=(0, 1, 2), regime="RISK_ON", top=(1, 2), ready=(1, 2))
    assert lr.entry_events([a, None, a, b], "TOP3") == [(0, 0), (0, 1), (3, 2)]
    assert lr.entry_events([a, b, a], "ALL_READY") == [(0, 0), (0, 1), (0, 2), (2, 0)]


def test_forward_return_delisting_gap_and_download_edge():
    rows = _path(5, drift=0.0)
    m = _market(
        {"LIVE": rows, "GAP": rows[:200] + rows[210:], "DELIST": rows[:150], "EDGE": rows[:150]},
        coverage_end_time={"DELIST": rows[-1][0]},  # downloaded far beyond where its data stops
    )
    live, gap, delist, edge = (m.index_of[k] for k in ("LIVE", "GAP", "DELIST", "EDGE"))
    value, flagged = lr.forward_return(m, live, 100, 16)
    assert value == pytest.approx((rows[116][5] / rows[101][2] - 1) * 100) and not flagged
    assert lr.forward_return(m, gap, 190, 16) is None             # exit bar missing, data resumes
    value, flagged = lr.forward_return(m, delist, 140, 96)
    assert flagged and value == pytest.approx((rows[149][5] / rows[141][2] - 1) * 100)
    assert lr.forward_return(m, edge, 140, 96) is None            # end of download, not a delisting
    assert lr.forward_return(m, live, len(rows) - 5, 16) is None  # beyond the data


def test_observations_are_thinned_per_symbol_and_benchmarked(market, steps):
    obs, unresolvable = lr.observations(market, steps, PARAMS, group="ALL_READY", horizon_h=4)
    assert obs
    by_symbol: dict[str, list[int]] = {}
    for o in obs:
        by_symbol.setdefault(o.symbol, []).append(o.step)
    assert all(b - a >= 16 for steps_ in by_symbol.values() for a, b in zip(steps_, steps_[1:]))
    o = obs[0]
    universe = steps[o.step].universe
    expected = np.nanmean([lr.forward_return(market, u, o.step, 16)[0] for u in universe
                           if lr.forward_return(market, u, o.step, 16)])
    assert o.benchmark_pct == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Pre-declared decision rules
# ---------------------------------------------------------------------------


def _obs(gross, bench, *, start_day=19_700, per_day=1):
    return [
        lr.Observation(group="TOP3", horizon_h=24, step=k, symbol=f"S{k % 7}",
                       decided_at=(start_day + k // per_day) * 86_400 + 3600, regime="RISK_ON",
                       gross_pct=g, benchmark_pct=b, delisted_exit=False)
        for k, (g, b) in enumerate(zip(gross, bench))
    ]


COSTS = lr.ReplayCosts()


def test_pass_candidate_needs_every_rule():
    rng = random.Random(1)
    gross = [1.5 + rng.gauss(0, 1.0) for _ in range(400)]
    stats = lr.group_stats("x", _obs(gross, [0.2] * 400), COSTS)
    assert stats.excess_ci_family[0] > 0 and stats.stressed_mean_net_pct > 0
    assert stats.verdict == "PASS_CANDIDATE"


def test_beating_a_falling_market_while_losing_money_is_not_a_pass():
    rng = random.Random(2)
    gross = [-1.0 + rng.gauss(0, 0.5) for _ in range(400)]
    stats = lr.group_stats("x", _obs(gross, [-3.0] * 400), COSTS)
    assert stats.excess_ci_family[0] > 0 and stats.stressed_mean_net_pct < 0
    assert stats.verdict == "NO_EDGE"


def test_negative_insufficient_and_decaying_edges():
    rng = random.Random(3)
    losing = [-1.0 + rng.gauss(0, 0.5) for _ in range(400)]
    assert lr.group_stats("x", _obs(losing, [0.0] * 400), COSTS).verdict == "NEGATIVE"
    small = [1.5 + rng.gauss(0, 1.0) for _ in range(100)]
    assert lr.group_stats("x", _obs(small, [0.0] * 100), COSTS).verdict == "INSUFFICIENT"
    decaying = [3.0 + rng.gauss(0, 0.3) for _ in range(200)] + [-0.6 + rng.gauss(0, 0.3) for _ in range(200)]
    stats = lr.group_stats("x", _obs(decaying, [0.0] * 400), COSTS)
    assert stats.excess_ci_family[0] > 0 and stats.second_half_excess_pct < 0
    assert stats.verdict == "NO_EDGE"
    assert lr.group_stats("x", _obs(small, [0.0] * 100), COSTS, decision_group=False).verdict == "DIAGNOSTIC_ONLY"


def test_picks_on_the_same_day_are_one_cluster():
    rng = random.Random(4)
    shocks = [rng.gauss(0, 1.0) for _ in range(10)]
    values = [shocks[k // 30] + rng.gauss(0, 0.05) for k in range(300)]  # one market move per day
    as_if_independent = lr.day_cluster_ci(values, list(range(300)), alpha=0.05, seed=1)
    by_day = lr.day_cluster_ci(values, [k // 30 for k in range(300)], alpha=0.05, seed=1)
    assert by_day[1] - by_day[0] > 2 * (as_if_independent[1] - as_if_independent[0])
    assert lr.day_cluster_ci(values, [7] * 300, alpha=0.05) is None


# ---------------------------------------------------------------------------
# CLI, report and pre-registration
# ---------------------------------------------------------------------------


def test_cli_end_to_end_without_trade_authority(tmp_path, capsys, monkeypatch):
    data = tmp_path / "universe"
    (data / "15m").mkdir(parents=True)
    symbols = {}
    for symbol, rows in _universe_rows().items():
        frame = pd.DataFrame(rows, columns=["open_time", "close_time", "open", "high", "low", "close", "quote_volume"])
        frame.to_parquet(data / "15m" / f"{symbol}.parquet", index=False)
        symbols[symbol] = {"months": ["2024-01"], "bars": len(rows)}
    (data / "manifest.json").write_text(json.dumps({
        "candidates": len(symbols), "stopped_before_window_end": [], "symbols": symbols,
    }))
    monkeypatch.setattr(lr, "RadarParams", lambda: PARAMS)
    out = tmp_path / "replay.json"
    registry = tmp_path / "registry.jsonl"
    argv = ["--data-dir", str(data), "--out", str(out), "--workers", "1", "--trial-registry", str(registry)]
    assert lr._cli([*argv, "--record-trial"]) == 0
    assert "NOT pre-registered" in capsys.readouterr().out
    payload = json.loads(out.read_text())
    assert payload["can_authorize_trade"] is False
    assert set(payload["decision"]) == {"TOP3@4h", "TOP3@24h", "ALL_READY@4h", "ALL_READY@24h"}
    assert any("survivorship" in note for note in payload["notes"])
    assert "WHAT COULD BLOW UP" in out.with_suffix(".md").read_text().upper()
    with gzip.open(tmp_path / "replay_observations.csv.gz", "rt") as handle:
        assert handle.readline().startswith("group,horizon_h,step,symbol")
    assert len(TrialRegistry(registry).selection_trials(lr.REPLAY_FAMILY)) == 1
    assert lr._cli(argv) == 0
    assert "(pre-registered)" in capsys.readouterr().out


def test_liquid_replay_is_pre_registered_for_the_current_code():
    """Tripwire: a change to the radar, the universe builder or the replay is a new trial."""

    params = lr.replay_trial_params(fingerprint=lr.engine_fingerprint(), params=lr.RadarParams(),
                                    costs=lr.ReplayCosts())
    trial_id = trial_id_for(family=lr.REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(lr.REPO_ROOT / "research" / "trials" / "registry.jsonl")
    assert trial_id in {r.trial_id for r in registry.selection_trials(lr.REPLAY_FAMILY)}
