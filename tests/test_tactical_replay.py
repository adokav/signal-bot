from __future__ import annotations

import json
import math
import random
from dataclasses import replace

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from acce_unified.forward_ledger import record_from_assessment
from acce_unified.tactical_long_data import REQUIRED_TIMEFRAMES, TacticalTimeframe
from acce_unified.tactical_long_engine import TacticalLongEngine
from trading.backtest import tactical_replay as tr
from trading.research.robustness import TrialRegistry, trial_id_for

START = 1_704_067_200  # 2024-01-01T00:00:00Z, day aligned
DAYS = 41  # 240 H4 bars need 40 days; leaves ~1 day of evaluated steps
FILE_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14_400, "1d": 86_400}


def _m5_path(seed: int, start_price: float) -> list[tuple]:
    rng = random.Random(seed)
    rows = []
    price = start_price
    for i in range(DAYS * 288):
        o = price
        c = o * math.exp(0.00004 * math.sin(i / 700) + rng.gauss(0, 0.0015))
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.0008)))
        low = min(o, c) * (1 - abs(rng.gauss(0, 0.0008)))
        rows.append((START + i * 300, o, h, low, c, rng.uniform(50, 150)))
        price = c
    return rows


def _aggregate(m5: list[tuple], seconds: int) -> list[tuple]:
    buckets: dict[int, list[tuple]] = {}
    for row in m5:
        buckets.setdefault(row[0] - row[0] % seconds, []).append(row)
    out = []
    for bucket, rows in sorted(buckets.items()):
        out.append((
            bucket, bucket + seconds - 1, rows[0][1], max(r[2] for r in rows),
            min(r[3] for r in rows), rows[-1][4], sum(r[5] for r in rows),
        ))
    return out


def _frame(rows: list[tuple]) -> "pd.DataFrame":
    return pd.DataFrame.from_records([
        {"open_time": r[0], "close_time": r[1], "available_at": r[1], "open": r[2], "high": r[3],
         "low": r[4], "close": r[5], "volume": r[6], "quote_volume": r[6] * r[5]}
        for r in rows
    ])


def _write_dataset(directory, *, mutate=None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for seed, (symbol, price) in enumerate((("BTCUSDT", 42_000.0), ("ETHUSDT", 2_300.0), ("ETHBTC", 0.055))):
        m5 = _m5_path(seed + 1, price)
        for name, seconds in FILE_SECONDS.items():
            rows = _aggregate(m5, seconds) if seconds > 300 else [
                (r[0], r[0] + 299, r[1], r[2], r[3], r[4], r[5]) for r in m5
            ]
            if mutate is not None:
                rows = mutate(symbol, name, rows)
            _frame(rows).to_parquet(directory / f"{symbol}_klines_{name}.parquet", index=False)


@pytest.fixture(scope="module")
def dataset_dir(tmp_path_factory):
    directory = tmp_path_factory.mktemp("spot")
    _write_dataset(directory)
    return directory


@pytest.fixture(scope="module")
def dataset(dataset_dir):
    return tr.load_dataset(dataset_dir)


def _first_evaluable(data) -> int:
    builder = tr.SnapshotBuilder(data, spread_bps=2.0)
    return next(t for t in tr.decision_times(data) if builder.at(t) is not None)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def test_series_drops_malformed_and_duplicate_rows_without_repairing_them():
    rows = [
        (0, 299, 10.0, 11.0, 9.0, 10.5, 1.0),
        (0, 299, 10.0, 11.0, 9.0, 10.5, 1.0),           # duplicate open time
        (300, 599, 10.0, 9.5, 9.0, 9.2, 1.0),           # high below open
        (600, 899, float("nan"), 11.0, 9.0, 10.0, 1.0),  # non-finite
        (900, 1199, 10.0, 11.0, 9.0, 10.0, -1.0),        # negative volume
        (1200, 1499, 10.0, 11.0, 9.0, 10.0, 1.0),
    ]
    series = tr._Series(rows)
    assert len(series) == 2
    assert series.rejected == 4
    assert list(series.open_time) == [0, 1200]


def test_series_refuses_out_of_order_rows():
    with pytest.raises(ValueError):
        tr._Series([(300, 599, 1.0, 1.0, 1.0, 1.0, 1.0), (0, 299, 1.0, 1.0, 1.0, 1.0, 1.0)])


def test_missing_file_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):
        tr.load_dataset(tmp_path)


# ---------------------------------------------------------------------------
# Point-in-time snapshots
# ---------------------------------------------------------------------------


def test_snapshot_contains_only_candles_closed_at_decision_time(dataset):
    builder = tr.SnapshotBuilder(dataset, spread_bps=2.0)
    first = _first_evaluable(dataset)
    for t in (first, first + 300, first + 3 * 3600 + 1200, first + 7 * 3600):
        snapshot = builder.at(t)
        assert snapshot is not None
        for symbol in tr.SYMBOLS:
            for tf in REQUIRED_TIMEFRAMES:
                series = dataset[symbol][tf]
                expected = [k for k in range(len(series)) if series.close_time[k] <= t][-tr.WINDOW_BARS:]
                window = snapshot.candles[symbol][tf]
                assert [c.open_time for c in window] == [series.open_time[k] for k in expected]
                assert all(c.close_time <= t and c.available_at <= t for c in window)
            quote = snapshot.quotes[symbol]
            assert quote.available_at == t
            assert quote.midpoint == pytest.approx(snapshot.candles[symbol][TacticalTimeframe.M5][-1].close)
            assert quote.spread_bps == pytest.approx(2.0, rel=1e-6)


def test_snapshot_is_none_before_warmup(dataset):
    builder = tr.SnapshotBuilder(dataset, spread_bps=2.0)
    assert builder.at(START + 10 * 86_400) is None
    assert builder.last_skip == "WARMUP"


def test_stale_timeframe_skips_the_step(dataset):
    first = _first_evaluable(dataset)
    h1 = dataset["ETHUSDT"][TacticalTimeframe.H1]
    cut = h1.last_closed_index(first + 6 * 3600) - 2
    rows = [
        (h1.open_time[k], h1.close_time[k], h1.open[k], h1.high[k], h1.low[k], h1.close[k], h1.volume[k])
        for k in range(len(h1)) if k <= cut or k > cut + 3
    ]
    data = {s: dict(frames) for s, frames in dataset.items()}
    data["ETHUSDT"][TacticalTimeframe.H1] = tr._Series(rows)
    builder = tr.SnapshotBuilder(data, spread_bps=2.0)
    stale_at = h1.close_time[cut] + 2 * 3600 + 300
    assert builder.at(stale_at) is None
    assert builder.last_skip == "STALE"
    assert builder.at(h1.close_time[cut + 4] + 300) is not None


def test_decision_grid_counts_missing_btc_data_as_skipped(dataset):
    m5 = dataset["BTCUSDT"][TacticalTimeframe.M5]
    first = _first_evaluable(dataset)
    hole = {k for k in range(len(m5)) if first + 3600 <= m5.close_time[k] < first + 3 * 3600}
    rows = [
        (m5.open_time[k], m5.close_time[k], m5.open[k], m5.high[k], m5.low[k], m5.close[k], m5.volume[k])
        for k in range(len(m5)) if k not in hole
    ]
    data = {s: dict(frames) for s, frames in dataset.items()}
    data["BTCUSDT"][TacticalTimeframe.M5] = tr._Series(rows)
    grid = tr.decision_times(data)
    assert len(grid) == len(m5)  # the hole is still on the grid
    assert all(b - a == 300 for a, b in zip(grid, grid[1:]))
    builder = tr.SnapshotBuilder(data, spread_bps=2.0)
    assert builder.at(first + 2 * 3600 + 3599 - 300) is None  # well inside the hole: stale M5
    assert builder.last_skip == "STALE"
    result = tr.evaluate(data, spread_bps=2.0)
    assert result.skipped_data > 0  # missing data is counted, never mistaken for warm-up


def test_future_candles_cannot_change_past_decisions(dataset, tmp_path):
    """Poison every candle that closes after T; events up to T must not move."""

    first = _first_evaluable(dataset)
    cutoff = first + 10 * 3600
    baseline = [e for e in tr.evaluate(dataset, spread_bps=2.0).events if e[0] <= cutoff]

    def poison(symbol, name, rows):
        return [
            (r[0], r[1], r[2] * 1.7, r[3] * 1.7, r[4] * 1.7, r[5] * 1.7, r[6] * 9) if r[1] > cutoff else r
            for r in rows
        ]

    _write_dataset(tmp_path / "poisoned", mutate=poison)
    poisoned = tr.load_dataset(tmp_path / "poisoned")
    after = [e for e in tr.evaluate(poisoned, spread_bps=2.0).events if e[0] <= cutoff]
    assert after == baseline
    assert baseline  # the comparison is not vacuous


# ---------------------------------------------------------------------------
# Engine evaluation
# ---------------------------------------------------------------------------


def _dedupe(events):
    previous: dict[str, str] = {}
    out = []
    for t, symbol, key, item in events:
        if previous.get(symbol) == key:
            continue
        previous[symbol] = key
        out.append((t, symbol, key, item))
    return out


def test_parallel_and_sequential_evaluation_agree(dataset):
    single = tr.evaluate(dataset, spread_bps=2.0, workers=1, chunks_per_worker=1)
    parallel = tr.evaluate(dataset, spread_bps=2.0, workers=2, chunks_per_worker=3)
    assert single.evaluated == parallel.evaluated > 0
    assert single.skipped_warmup == parallel.skipped_warmup > 0
    assert single.skipped_data == parallel.skipped_data == 0
    assert _dedupe(single.events) == _dedupe(parallel.events)
    end = tr.decision_times(dataset)[-1]
    records = tr.record_outcomes(single.events, dataset, end_time=end)
    assert records  # the synthetic market does produce alerts, so this is not vacuous
    assert records == tr.record_outcomes(parallel.events, dataset, end_time=end)


def test_engine_failures_are_counted_not_treated_as_no_setup(dataset, monkeypatch):
    def boom(self, snapshot):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(TacticalLongEngine, "analyze", boom)
    report, records = tr.run_replay(dataset, workers=1)
    assert report.evaluated == 0
    assert report.engine_errors > 0
    assert records == []
    assert any("motor hatası" in note for note in report.notes)


# ---------------------------------------------------------------------------
# Recording and resolution (live forward-ledger semantics)
# ---------------------------------------------------------------------------


def _item(symbol, decided_at, state="READY"):
    return {
        "symbol": symbol, "decision_at": decided_at, "state": state,
        "setup": "TREND_PULLBACK", "structure_4h": "BULLISH",
        "plan": {
            "entry_low": 99.0, "entry_high": 100.0, "technical_invalidation": 98.0,
            "hard_stop": 97.0, "target_1": 104.0, "target_2": 108.0,
            "estimated_round_trip_cost_pct": 0.05, "expires_at": decided_at + 6 * 3600,
        },
    }


def _m5_series(t0, bars):
    return tr._Series(
        (t0 + i * 300, t0 + i * 300 + 299, o, h, low, c, 1.0) for i, (o, h, low, c) in enumerate(bars)
    )


def test_records_open_on_transition_once_per_symbol_and_resolve_with_ledger_rules():
    t0 = 1_800_000_000
    flat = [(101.0, 101.5, 100.5, 101.0)] * 3
    fill_and_win = [(100.5, 100.8, 99.5, 100.2), (100.2, 104.5, 100.0, 104.2)]
    btc_bars = flat + fill_and_win + flat * 4 + [(100.5, 100.6, 96.0, 96.5)]
    eth_bars = flat + [(100.5, 100.6, 96.5, 97.0)] + flat * 5
    data = {
        "BTCUSDT": {TacticalTimeframe.M5: _m5_series(t0, btc_bars)},
        "ETHUSDT": {TacticalTimeframe.M5: _m5_series(t0, eth_bars)},
    }
    decided = t0 + 299  # close of the first M5
    events = [
        (decided, "BTCUSDT", "READY:TREND_PULLBACK", _item("BTCUSDT", decided)),
        (decided, "ETHUSDT", "READY:TREND_PULLBACK", _item("ETHUSDT", decided)),
        # same key again (chunk boundary duplicate): must not open anything
        (decided + 300, "BTCUSDT", "READY:TREND_PULLBACK", _item("BTCUSDT", decided + 300)),
        # key change while BTC is still open: one open record per symbol
        (decided + 600, "BTCUSDT", "TRIGGERED:TREND_PULLBACK", _item("BTCUSDT", decided + 600, "TRIGGERED")),
        # transition after the first BTC record resolved: a new record opens
        (decided + 2400, "BTCUSDT", "NO_LONG:-", None),
        (decided + 2700, "BTCUSDT", "READY:TREND_PULLBACK", _item("BTCUSDT", decided + 2700)),
    ]
    end = data["BTCUSDT"][TacticalTimeframe.M5].close_time[-1]
    records = tr.record_outcomes(events, data, end_time=end)
    by_symbol = {}
    for record in records:
        by_symbol.setdefault(record.symbol, []).append(record)
    assert [r.decided_at for r in by_symbol["BTCUSDT"]] == [decided, decided + 2700]
    assert [r.status for r in by_symbol["BTCUSDT"]] == ["WIN_T1", "LOSS_STOP"]
    assert [r.status for r in by_symbol["ETHUSDT"]] == ["LOSS_STOP"]
    assert all(r.can_authorize_trade is False for r in records)


def test_realized_uses_replay_costs_not_engine_estimate():
    record = record_from_assessment(_item("BTCUSDT", 1_800_000_299))
    win = replace(record, status="WIN_T1", filled_at=1, fill_price=100.0, exit_price=104.0)
    costs = tr.ReplayCosts(fee_bps_per_side=5.0, slippage_bps_per_side=2.0)
    assert costs.round_trip_pct() == pytest.approx(0.14)
    assert costs.round_trip_pct(fee_mult=1.5, slip_mult=2.0) == pytest.approx(0.23)
    net, r = tr.realized(win, costs.round_trip_pct())
    assert net == pytest.approx(4.0 - 0.14)
    assert r == pytest.approx((4.0 - 0.14) / (3.0 + 0.14))
    assert tr.realized(replace(record, status="NOT_FILLED"), 0.14) is None
    assert tr.realized(replace(record, status="UNRESOLVABLE"), 0.14) is None


# ---------------------------------------------------------------------------
# Pre-declared decision rules
# ---------------------------------------------------------------------------


def _outcomes(pattern):
    base = record_from_assessment(_item("BTCUSDT", 1_800_000_299))
    rows = []
    for i, won in enumerate(pattern):
        decided = 1_800_000_299 + i * 86_400
        rows.append(replace(
            base, record_id=f"r{i}", decided_at=decided, expires_at=decided + 3600,
            status="WIN_T1" if won else "LOSS_STOP", filled_at=decided + 300, fill_price=100.0,
            exit_price=104.0 if won else 97.0,
        ))
    return rows


COSTS = tr.ReplayCosts()


def test_verdict_pass_candidate_requires_all_rules():
    stats = tr.group_stats("x", _outcomes([i % 5 < 3 for i in range(150)]), COSTS)
    assert stats.resolved == 150
    assert stats.mean_r_ci_family[0] > 0
    assert stats.stressed_mean_r > 0
    assert stats.verdict == "PASS_CANDIDATE"


def test_verdict_insufficient_below_minimum_sample():
    stats = tr.group_stats("x", _outcomes([i % 5 < 3 for i in range(60)]), COSTS)
    assert stats.verdict == "INSUFFICIENT"


def test_verdict_negative_when_family_ci_is_below_zero():
    stats = tr.group_stats("x", _outcomes([i % 5 == 0 for i in range(150)]), COSTS)
    assert stats.mean_r_ci_family[1] < 0
    assert stats.verdict == "NEGATIVE"


def test_verdict_no_edge_when_ci_includes_zero():
    stats = tr.group_stats("x", _outcomes([i % 20 < 9 for i in range(200)]), COSTS)
    assert stats.mean_r_ci_family[0] <= 0 <= stats.mean_r_ci_family[1]
    assert stats.verdict == "NO_EDGE"


def test_verdict_no_edge_when_second_half_decays():
    pattern = [i % 10 < 9 for i in range(100)] + [i % 5 < 2 for i in range(100)]
    stats = tr.group_stats("x", _outcomes(pattern), COSTS)
    assert stats.mean_r_ci_family[0] > 0 and stats.stressed_mean_r > 0
    assert stats.second_half_mean_r < 0
    assert stats.verdict == "NO_EDGE"


def test_symbol_and_year_slices_never_get_a_verdict():
    stats = tr.group_stats("2024", _outcomes([i % 5 < 3 for i in range(150)]), COSTS, decision_group=False)
    assert stats.verdict == "DIAGNOSTIC_ONLY"


def test_unfilled_and_unresolvable_records_are_not_outcomes():
    rows = _outcomes([True] * 3)
    rows += [replace(rows[0], record_id="nf", status="NOT_FILLED", fill_price=None, exit_price=None)]
    rows += [replace(rows[0], record_id="un", status="UNRESOLVABLE", exit_price=None)]
    stats = tr.group_stats("x", rows, COSTS)
    assert (stats.recorded, stats.resolved, stats.not_filled, stats.unresolvable) == (5, 3, 1, 1)


# ---------------------------------------------------------------------------
# CLI, report and pre-registration
# ---------------------------------------------------------------------------


def test_cli_writes_report_without_trade_authority(dataset_dir, tmp_path, capsys):
    out = tmp_path / "replay.json"
    registry = tmp_path / "registry.jsonl"
    argv = ["--data-dir", str(dataset_dir), "--out", str(out), "--workers", "1",
            "--trial-registry", str(registry)]
    assert tr._cli([*argv, "--record-trial"]) == 0
    first = capsys.readouterr().out
    assert "NOT pre-registered" in first
    payload = json.loads(out.read_text())
    assert payload["can_authorize_trade"] is False
    assert payload["evaluated"] > 0
    assert set(payload["by_setup"]) >= set(tr.SETUP_FAMILIES)
    assert isinstance(payload["records"], list)
    markdown = out.with_suffix(".md").read_text()
    assert "WHAT COULD BLOW UP" in markdown.upper()
    assert len(TrialRegistry(registry).selection_trials(tr.REPLAY_FAMILY)) == 1

    assert tr._cli(argv) == 0
    assert "(pre-registered)" in capsys.readouterr().out


def test_replay_trial_is_pre_registered_for_the_current_code():
    """Tripwire: editing the engine, ledger or replay changes the fingerprint.

    That is deliberate — a change after the pre-registration is a new trial
    and must be registered (and counted) before it is run.
    """

    params = tr.replay_trial_params(
        fingerprint=tr.engine_fingerprint(), spread_bps=2.0, costs=tr.ReplayCosts(),
    )
    trial_id = trial_id_for(family=tr.REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(tr.REPO_ROOT / "research" / "trials" / "registry.jsonl")
    assert trial_id in {r.trial_id for r in registry.selection_trials(tr.REPLAY_FAMILY)}
