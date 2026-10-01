from __future__ import annotations

import json
import math
import random

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from trading.backtest import liquid_replay as lr
from trading.backtest import reversal_replay as rv
from trading.research.robustness import TrialRegistry, trial_id_for

START = 1_514_764_800  # 2018-01-01T00:00:00Z
DAYS = 14


def _rows(seed, *, n_days=DAYS, price=10.0, volume=1e6, start=START):
    rng = random.Random(seed)
    rows, p = [], price
    for d in range(n_days):
        day_drift = rng.gauss(0, 0.04) / 96
        for b in range(96):
            i = d * 96 + b
            o = p
            c = o * math.exp(day_drift + rng.gauss(0, 0.002))
            h, low = max(o, c) * 1.001, min(o, c) * 0.999
            rows.append((start + i * 900, start + i * 900 + 899, o, h, low, c, volume * c * rng.uniform(0.5, 1.5)))
            p = c
    return rows


def _universe(n=40, **kwargs):
    rows = {f"C{k:02d}USDT": _rows(k, volume=1e5 * (k + 1), **kwargs) for k in range(n)}
    rows["USDCUSDT"] = [(r[0], r[1], 1.0, 1.001, 0.999, 1.0, 1e12) for r in rows["C00USDT"]]
    return rows


def _market(rows=None):
    rows = rows or _universe()
    return lr.build_market({s: lr.rows_to_columns(r) for s, r in rows.items()})


@pytest.fixture(scope="module")
def market():
    return _market()


def test_decisions_are_at_midnight_and_use_closed_bars(market):
    bars = rv.decision_bars(market)
    assert bars and all((market.grid_open[i] + 900) % 86_400 == 0 for i in bars)
    i = bars[3]
    s = market.index_of["C05USDT"]
    assert market.chg24[s, i] == pytest.approx((market.close[s, i] / market.close[s, i - 96] - 1) * 100)


def test_universe_uses_live_identity_rules_and_volume_floor(market):
    i = rv.decision_bars(market)[3]
    universe = rv.universe_at(market, i)
    names = {market.symbols[s] for s in universe}
    assert "USDCUSDT" not in names
    assert all(market.qv24[s, i] >= rv.MIN_QUOTE_VOLUME for s in universe)


def test_deciles_pick_the_extremes(market):
    i = rv.decision_bars(market)[3]
    universe = rv.universe_at(market, i)
    losers, winners = rv.deciles(market, i, universe)
    assert len(losers) == len(winners) == max(rv.MIN_DECILE, int(len(universe) * rv.DECILE))
    rest = [s for s in universe if s not in losers and s not in winners]
    assert max(market.chg24[s, i] for s in losers) <= min(market.chg24[s, i] for s in rest)
    assert min(market.chg24[s, i] for s in winners) >= max(market.chg24[s, i] for s in rest)


def test_cohorts_enter_next_open_and_never_overlap(market):
    rows, _ = rv.cohorts(market, 3)
    losers = [c for c in rows if c.group == "LOSERS"]
    assert len(losers) >= 3
    days = [c.day for c in losers]
    assert all(b - a >= 3 for a, b in zip(days, days[1:]))
    c = losers[0]
    i = next(i for i in rv.decision_bars(market) if (market.grid_open[i] + 900) // 86_400 == c.day)
    members = [market.index_of[s] for s in c.members]
    manual = np.mean([(market.close[s, i + 288] / market.open[s, i + 1] - 1) * 100 for s in members])
    assert c.gross_pct == pytest.approx(manual)
    universe = rv.universe_at(market, i)
    bench = np.mean([(market.close[s, i + 288] / market.open[s, i + 1] - 1) * 100 for s in universe])
    assert c.benchmark_pct == pytest.approx(bench)


def test_thin_universe_days_are_not_traded():
    m = _market(_universe(n=20))
    rows, _ = rv.cohorts(m, 1)
    assert rows == []


def test_future_bars_cannot_change_past_cohorts(market):
    base = {(c.group, c.day): c for c in rv.cohorts(market, 1)[0]}
    cutoff_day = min(d for _, d in base) + 5
    cutoff = cutoff_day * 86_400
    poisoned = _universe()
    for symbol, series in poisoned.items():
        poisoned[symbol] = [
            (ot, ct, o * 5, h * 5, low * 5, c * 5, qv * 100) if ot >= cutoff else (ot, ct, o, h, low, c, qv)
            for ot, ct, o, h, low, c, qv in series
        ]
    after = {(c.group, c.day): c for c in rv.cohorts(_market(poisoned), 1)[0]}
    settled = [k for k in base if k[1] < cutoff_day - 1]   # entered and exited before the poison
    assert settled
    for key in settled:
        assert after[key] == base[key]
    assert any(after.get(k) != base[k] for k in base if k[1] >= cutoff_day)


def _cohorts(gross, bench, *, hold=1):
    return [rv.Cohort("LOSERS", hold, 17_500 + k * hold, ("A",), 50, g, b, 0) for k, (g, b) in enumerate(zip(gross, bench))]


def test_verdict_rules():
    rng = random.Random(1)
    costs = rv.ReversalCosts()
    good = [1.0 + rng.gauss(0, 1.0) for _ in range(400)]
    assert rv.group_stats("x", _cohorts(good, [0.0] * 400), costs).verdict == "PASS_CANDIDATE"
    falling = [g - 2.0 for g in good]
    assert rv.group_stats("x", _cohorts(falling, [-3.0] * 400), costs).verdict == "NO_EDGE"  # beats basket, loses money
    losing = [-0.5 + rng.gauss(0, 0.5) for _ in range(400)]
    assert rv.group_stats("x", _cohorts(losing, [0.0] * 400), costs).verdict == "NEGATIVE"
    assert rv.group_stats("x", _cohorts(good[:150], [0.0] * 150), costs).verdict == "INSUFFICIENT"
    decaying = [2.0 + rng.gauss(0, 0.3) for _ in range(200)] + [-0.4 + rng.gauss(0, 0.3) for _ in range(200)]
    stats = rv.group_stats("x", _cohorts(decaying, [0.0] * 400), costs)
    assert stats.second_half_excess_pct < 0 and stats.verdict == "NO_EDGE"
    assert rv.group_stats("x", _cohorts(good, [0.0] * 400), costs, decision_group=False).verdict == "DIAGNOSTIC_ONLY"


def test_build_reads_nothing_after_the_holdout(monkeypatch, tmp_path):
    seen = {}

    def fake_build(out_dir, *, lookback_months, decision_at):
        seen.update(lookback_months=lookback_months, decision_at=decision_at)
        return {"symbols": {}}

    from trading.data import binance_universe

    monkeypatch.setattr(binance_universe, "build_universe", fake_build)
    rv.build(tmp_path)
    assert seen == {"lookback_months": 37, "decision_at": 1_601_510_400}


def _write_dataset(directory, rows):
    (directory / "15m").mkdir(parents=True)
    symbols = {}
    for symbol, series in rows.items():
        frame = pd.DataFrame(series, columns=["open_time", "close_time", "open", "high", "low", "close", "quote_volume"])
        frame.to_parquet(directory / "15m" / f"{symbol}.parquet", index=False)
        symbols[symbol] = {"months": ["2018-01"]}
    (directory / "manifest.json").write_text(json.dumps({"candidates": len(symbols), "stopped_before_window_end": ["C01USDT"],
                                                         "symbols": symbols}))


def test_cli_runs_and_refuses_data_past_the_holdout(tmp_path, capsys):
    data = tmp_path / "ok"
    _write_dataset(data, _universe())
    out = tmp_path / "rev.json"
    registry = tmp_path / "registry.jsonl"
    argv = ["run", "--data-dir", str(data), "--out", str(out), "--trial-registry", str(registry)]
    assert rv._cli([*argv, "--record-trial"]) == 0
    assert "NOT pre-registered" in capsys.readouterr().out
    payload = json.loads(out.read_text())
    assert set(payload["decision"]) == {"LOSERS@1d", "LOSERS@3d"} and payload["can_authorize_trade"] is False
    assert "WHAT COULD BLOW UP" in out.with_suffix(".md").read_text().upper()
    assert len(TrialRegistry(registry).selection_trials(rv.REPLAY_FAMILY)) == 1
    assert rv._cli(argv) == 0 and "(pre-registered)" in capsys.readouterr().out

    late = tmp_path / "late"
    _write_dataset(late, _universe(start=1_601_510_400 - 3 * 86_400, n_days=5))
    with pytest.raises(SystemExit):
        rv._cli(["run", "--data-dir", str(late), "--out", str(tmp_path / "x.json"), "--trial-registry", str(registry)])


def test_reversal_replay_is_pre_registered_for_the_current_code():
    """Tripwire: changing the universe rules, the data path or the replay is a new trial."""

    params = rv.replay_trial_params(fingerprint=rv.engine_fingerprint(), costs=rv.ReversalCosts())
    trial_id = trial_id_for(family=rv.REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(rv.REPO_ROOT / "research" / "trials" / "registry.jsonl")
    assert trial_id in {r.trial_id for r in registry.selection_trials(rv.REPLAY_FAMILY)}
