from __future__ import annotations

import gzip
import json
import math

import numpy as np
import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("pyarrow")

from acce_unified import long_alerts
from acce_unified.forward_ledger import record_from_assessment
from acce_unified.tactical_long_data import TacticalTimeframe
from trading.backtest import liquid_replay as lr
from trading.backtest import signal_quality as sq
from trading.backtest import tactical_replay as tr
from trading.data import universe_funding as uf

START = 1_704_067_200  # 2024-01-01T00:00:00Z, hour aligned
BARS = 12 * 96
PARAMS = lr.RadarParams(min_benchmark_members=2)
WINDOW = (0, 2_000_000_000)
ALERT = 400


def _rows(price_at):
    rows = []
    for i in range(BARS):
        o, c = price_at(i), price_at(i + 1)
        rows.append((START + i * 900, START + i * 900 + 899, o, max(o, c) * 1.002, min(o, c) * 0.998, c, 1e6))
    return rows


def _wave(base, k=0.0):
    return lambda i: base * (1.0 + 0.01 * math.sin(i / 7.0 + k))


def _market(overrides=None):
    rows = {"BTCUSDT": _rows(_wave(40_000.0)), "AAAUSDT": _rows(_wave(10.0, 1.0)), "BBBUSDT": _rows(_wave(5.0, 2.0))}
    rows.update(overrides or {})
    return lr.build_market({s: lr.rows_to_columns(r) for s, r in rows.items()})


def _steps(market, top_symbol="AAAUSDT", start=ALERT, stop=BARS):
    s = market.index_of[top_symbol]
    universe = tuple(range(len(market.symbols)))
    return [lr.Step(universe, "NEUTRAL", (s,), (s,)) if start <= i < stop else None for i in range(market.n_grid)]


def _outcome_key(key):
    return key.startswith(("stop72_", "ret", "bench"))


# ---------------------------------------------------------------------------
# Seal
# ---------------------------------------------------------------------------


def test_seal_refuses_any_candle_closing_at_or_after_the_boundary():
    sq.check_sealed(sq.DISCOVERY_END - 1, end=sq.DISCOVERY_END)
    with pytest.raises(sq.SealError):
        sq.check_sealed(sq.DISCOVERY_END + 899, end=sq.DISCOVERY_END)


def test_discovery_months_span_the_discovery_window_exactly():
    from trading.data.binance_vision import _latest_completed_month, months_ending_at

    year, month = _latest_completed_month(sq.DISCOVERY_END)
    months = sorted(months_ending_at(end_year=year, end_month=month, lookback_months=sq.DISCOVERY_MONTHS))
    assert months[0] == (2020, 10) and months[-1] == (2024, 8)


def test_tactical_loader_refuses_data_from_the_confirmation_window(tmp_path):
    for symbol in tr.SYMBOLS:
        for name, seconds in (("5m", 300), ("15m", 900), ("1h", 3600), ("4h", 14_400), ("1d", 86_400)):
            t0 = sq.DISCOVERY_END - 3 * seconds
            frame = pd.DataFrame.from_records([
                {"open_time": t0 + k * seconds, "close_time": t0 + (k + 1) * seconds - 1, "open": 1.0, "high": 1.0,
                 "low": 1.0, "close": 1.0, "volume": 1.0} for k in range(4)  # the last one closes after the seal
            ])
            frame.to_parquet(tmp_path / f"{symbol}_klines_{name}.parquet", index=False)
    with pytest.raises(sq.SealError):
        sq.load_tactical_data(tmp_path, end=sq.DISCOVERY_END)


def _write_universe(directory, rows_by_symbol):
    (directory / "15m").mkdir(parents=True)
    for symbol, rows in rows_by_symbol.items():
        pd.DataFrame.from_records([dict(zip(lr.COLUMNS, r)) for r in rows]).to_parquet(
            directory / "15m" / f"{symbol}.parquet", index=False)
    (directory / "manifest.json").write_text(json.dumps({"symbols": {s: {"months": ["2024-01"]} for s in rows_by_symbol}}))


def test_likit_loader_drops_historical_non_coins_and_refuses_unsealed_data(tmp_path):
    rows = _rows(_wave(10.0))
    _write_universe(tmp_path / "a", {"BTCUSDT": rows, "PAXUSDT": rows, "BULLUSDT": rows, "WBTCUSDT": rows})
    market, _, excluded = sq.load_likit_market(tmp_path / "a", end=START + BARS * 900)
    assert market.symbols == ["BTCUSDT"]
    assert excluded == {"PAXUSDT": "STABLE", "BULLUSDT": "LEVERAGED", "WBTCUSDT": "PEGGED"}
    with pytest.raises(sq.SealError):
        sq.load_likit_market(tmp_path / "a", end=START + BARS * 900 - 1)


# ---------------------------------------------------------------------------
# Point-in-time features
# ---------------------------------------------------------------------------


def test_hourly_rows_use_complete_closed_hours_only():
    market = _market()
    s = market.index_of["AAAUSDT"]
    i = 401  # bar opens at :15 → the forming hour (bars 400-403) is excluded
    rows = sq.hourly_rows(market, s, i)
    assert len(rows) == sq.HOURLY_ROWS
    assert rows[-1][0] == START + 396 * 900
    assert rows[-1][2] == pytest.approx(float(market.high[s, 396:400].max()))
    assert rows[-1][4] == pytest.approx(float(market.close[s, 399]))
    market.high[s, 398] = np.nan  # a missing bar removes its whole hour, never a partial candle
    assert [r[0] for r in sq.hourly_rows(market, s, i)].count(START + 396 * 900) == 0


def test_features_do_not_change_when_the_future_changes():
    market = _market()
    base = sq.likit_rows(market, _steps(market), params=PARAMS, window=WINDOW)[0]
    wave = _wave(10.0, 1.0)
    crash = _market({"AAAUSDT": _rows(lambda i: wave(i) if i <= ALERT + 1 else 5.0)})  # bar ALERT closes at wave(ALERT+1)
    changed = sq.likit_rows(crash, _steps(crash), params=PARAMS, window=WINDOW)[0]
    features = {k: v for k, v in base.items() if not _outcome_key(k)}
    assert features == {k: v for k, v in changed.items() if not _outcome_key(k)}
    assert changed["stop72_status"] == long_alerts.STOPPED and base["stop72_status"] == long_alerts.EXPIRED
    assert base["decided_at"] == START + ALERT * 900 + 899
    assert base["can_authorize_trade"] is False


def test_missing_history_is_none_not_neutral():
    market = _market()
    row = sq.likit_rows(market, _steps(market, start=ALERT), params=PARAMS, window=WINDOW)[0]
    assert row["chg_7d"] is None and row["sma20d_dist"] is None and row["btc_30d"] is None  # < 30 days of data
    assert row["breadth_sma20d"] is None and row["basket_7d"] is None
    assert row["funding_status"] == "NOT_LOADED" and row["funding_last"] is None


# ---------------------------------------------------------------------------
# Live alert rule and stop tracking
# ---------------------------------------------------------------------------


def test_no_new_alert_while_the_entry_is_open_then_one_after_it_closes():
    market = _market()
    rows = sq.likit_rows(market, _steps(market), params=PARAMS, window=WINDOW)
    assert [r["decided_at"] for r in rows][:2] == [START + ALERT * 900 + 899, START + (ALERT + 288) * 900 + 899]
    assert rows[0]["stop72_status"] == long_alerts.EXPIRED and rows[0]["stop72_bars"] == sq.TRACK_BARS


def test_alert_rule_state_warms_up_before_the_window():
    market = _market()
    window = (START + (ALERT + 100) * 900, START + BARS * 900)
    rows = sq.likit_rows(market, _steps(market), params=PARAMS, window=window)
    # the entry opened before the window is still open at its start: no alert until it expires
    assert rows[0]["decided_at"] == START + (ALERT + 288) * 900 + 899


def test_stopped_entry_allows_a_new_alert_only_after_the_cooldown():
    market = _market({"AAAUSDT": _rows(lambda i: 10.0 if i <= ALERT + 1 else 8.0)})
    rows = sq.likit_rows(market, _steps(market), params=PARAMS, window=WINDOW)
    first = rows[0]
    assert first["stop72_status"] == long_alerts.STOPPED
    assert first["stop72_result_pct"] < -first["stop_pct"] + 1e-9  # gapped below the stop: exit at the open
    cooldown = long_alerts.COOLDOWN_HOURS * 3_600 // 900
    assert rows[1]["decided_at"] == first["decided_at"] + cooldown * 900


def test_data_ending_before_72_hours_is_unresolvable_not_a_result():
    market = _market()
    end = BARS - 100
    rows = sq.likit_rows(market, _steps(market, start=end), params=PARAMS, window=WINDOW)
    assert rows[0]["stop72_status"] == "UNRESOLVABLE" and rows[0]["stop72_result_pct"] is None
    assert rows[0]["ret72_pct"] is None


def test_delisting_exits_at_the_last_price_but_a_hole_that_resumes_is_unresolvable():
    rows = _rows(_wave(10.0, 1.0))
    delisted = _market({"AAAUSDT": rows[: ALERT + 50]})
    delisted.coverage_end[delisted.index_of["AAAUSDT"]] = BARS - 1
    row = sq.likit_rows(delisted, _steps(delisted, stop=ALERT + 1), params=PARAMS, window=WINDOW)[0]
    assert row["stop72_status"] == long_alerts.EXPIRED and row["stop72_note"]
    assert row["stop72_result_pct"] == pytest.approx((rows[ALERT + 49][5] / rows[ALERT][5] - 1) * 100)

    holed = _market({"AAAUSDT": rows[: ALERT + 50] + rows[ALERT + 400:]})
    row = sq.likit_rows(holed, _steps(holed, stop=ALERT + 1), params=PARAMS, window=WINDOW)[0]
    assert row["stop72_status"] == "UNRESOLVABLE"


def test_stop_plan_missing_is_recorded_as_no_plan():
    market = _market()
    rows = sq.likit_rows(market, _steps(market, start=20, stop=21), params=PARAMS, window=WINDOW)
    assert rows[0]["stop72_status"] == "NO_PLAN" and rows[0]["stop_pct"] is None


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------


def test_funding_uses_settled_rows_only_and_flags_missing_or_stale():
    funding = {"AAAUSDT": ([1000, 1000 + 28_800, 1000 + 57_600], [0.0001, 0.0003, 0.05])}
    seen = sq.funding_at(funding, "AAAUSDT", 1000 + 57_599)
    assert seen["funding_status"] == "OK"
    assert seen["funding_last"] == pytest.approx(0.03) and seen["funding_3d"] == pytest.approx(0.02)
    assert sq.funding_at(funding, "BBBUSDT", 5000)["funding_status"] == "NO_PERP"
    assert sq.funding_at(funding, "AAAUSDT", 999)["funding_status"] == "STALE"
    assert sq.funding_at(funding, "AAAUSDT", 1000 + 57_600 + sq.FUNDING_MAX_AGE + 1)["funding_last"] is None


def test_perp_mapping_drops_spot_pairs_claimed_twice():
    mapping, ambiguous = uf.perp_for_spot(["1000PEPEUSDT", "XUSDT", "1000XUSDT", "USDCUSDT"],
                                          ["PEPEUSDT", "XUSDT", "USDCUSDT"])
    assert mapping == {"PEPEUSDT": "1000PEPEUSDT"} and ambiguous == ["XUSDT"]


def test_funding_download_drops_rows_after_the_decision_time_and_refuses_conflicts(monkeypatch):
    head = "calc_time,funding_interval_hours,last_funding_rate\n"
    files = {"2024-01": head + f"{START}000,8,0.0001\n{START + 28_800}000,8,0.0002\n"}
    monkeypatch.setattr(uf.carry, "_csv", lambda session, url, what: files.get(what.split()[-1]))
    assert uf.fetch_funding(None, "AUSDT", ["2024-01", "2024-02"], decision_at=START + 100) == [(START, 0.0001)]
    files["2024-02"] = head + f"{START}000,8,0.0009\n"
    with pytest.raises(uf.carry.DataQualityError):
        uf.fetch_funding(None, "AUSDT", ["2024-01", "2024-02"], decision_at=START + 30_000)


def test_funding_build_writes_rows_and_lists_pairs_without_perp(tmp_path, monkeypatch):
    universe = tmp_path / "u"
    universe.mkdir()
    (universe / "manifest.json").write_text(json.dumps({"window": ["2024-01", "2024-01"], "symbols": {
        "AUSDT": {"months": ["2024-01"]}, "BUSDT": {"months": ["2024-01"]}}}))
    text = f"calc_time,funding_interval_hours,last_funding_rate\n{START}000,8,0.0001\n"
    monkeypatch.setattr(uf.carry, "_csv", lambda session, url, what: text)
    summary = uf.build_funding(universe, tmp_path / "f", decision_at=START, session=object(), perps=["AUSDT"])
    assert summary["with_perp"] == 1 and summary["without_perp"] == ["BUSDT"] and summary["rows"] == 1
    assert uf.load_funding(tmp_path / "f") == {"AUSDT": ([START], [0.0001])}
    assert summary["can_authorize_trade"] is False


# ---------------------------------------------------------------------------
# Tactical rows
# ---------------------------------------------------------------------------


def _series(t0, seconds, n, price_at):
    return tr._Series(
        (t0 + k * seconds, t0 + (k + 1) * seconds - 1, price_at(k * seconds), price_at(k * seconds) * 1.001,
         price_at(k * seconds) * 0.999, price_at((k + 1) * seconds), 1.0)
        for k in range(n)
    )


def _tactical_data(price_at, days=40):
    t0 = START
    frames = {}
    for tf, seconds in ((TacticalTimeframe.M5, 300), (TacticalTimeframe.M15, 900), (TacticalTimeframe.H1, 3600),
                        (TacticalTimeframe.H4, 14_400), (TacticalTimeframe.D1, 86_400)):
        frames[tf] = _series(t0, seconds, days * 86_400 // seconds, price_at)
    return {"BTCUSDT": frames, "ETHUSDT": frames}


def _record(t, entry=100.0):
    return record_from_assessment({
        "symbol": "BTCUSDT", "decision_at": t, "state": "READY", "setup": "TREND_PULLBACK", "structure_4h": "BULLISH",
        "plan": {"entry_low": entry * 0.99, "entry_high": entry, "technical_invalidation": entry * 0.98,
                 "hard_stop": entry * 0.97, "target_1": entry * 1.04, "target_2": entry * 1.08,
                 "estimated_round_trip_cost_pct": 0.05, "expires_at": t + 6 * 3600},
    })


def test_tactical_rows_are_point_in_time_with_three_exits():
    t = START + 30 * 86_400 - 1
    flat = _tactical_data(lambda s: 100.0)
    row = sq.tactical_rows([_record(t)], flat, window=WINDOW)[0]
    assert row["engine72_status"] == long_alerts.EXPIRED and row["atr72_status"] == long_alerts.EXPIRED
    assert row["engine72_r"] == pytest.approx(-sq.TACTICAL_COST_PCT / (3.0 + sq.TACTICAL_COST_PCT))
    assert row["ret24_pct"] == pytest.approx(0.0) and row["sma20d_dist"] == pytest.approx(0.0)
    assert row["radar_log_eligible"] is True and row["can_authorize_trade"] is False

    drop = _tactical_data(lambda s: 100.0 if s <= 30 * 86_400 else 90.0)
    changed = sq.tactical_rows([_record(t)], drop, window=WINDOW)[0]
    assert changed["engine72_status"] == long_alerts.STOPPED
    assert {k: v for k, v in row.items() if not k.startswith(("engine72", "atr72", "ret", "mfe", "mae"))} == \
        {k: v for k, v in changed.items() if not k.startswith(("engine72", "atr72", "ret", "mfe", "mae"))}


def test_tactical_radar_log_eligibility_follows_the_live_rule():
    t = START + 30 * 86_400 - 1
    rows = sq.tactical_rows([_record(t), _record(t + 3_600)], _tactical_data(lambda s: 100.0), window=WINDOW)
    assert [r["radar_log_eligible"] for r in rows] == [True, False]  # first entry still open


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


def test_description_keeps_missing_values_in_their_own_bucket():
    rows = [{"decided_at": START + k * 86_400, "symbol": "A", "x": (None if k % 5 == 0 else float(k)),
             "r": float(k % 3 - 1)} for k in range(100)]
    report = sq.describe(rows, features=("x",), categories=(), outcomes={"r": lambda row: row["r"]})
    buckets = report["features"]["x"]["buckets"]
    assert buckets["n/a"]["n"] == 20 and sum(b["n"] for b in buckets.values()) == 100
    assert len(report["features"]["x"]["edges"]) == 4
    assert report["all"]["r"]["ci95"] is not None


def test_rows_file_round_trips_missing_as_empty(tmp_path):
    path = tmp_path / "rows.csv.gz"
    sq.write_rows([{"a": 1, "b": None}, {"a": 2, "c": "x"}], path)
    with gzip.open(path, "rt") as handle:
        assert handle.read().splitlines() == ["a,b,c", "1,,", "2,,x"]


# ---------------------------------------------------------------------------
# Basis (perpetual vs spot)
# ---------------------------------------------------------------------------


def _perp_series(market, s, *, premium=0.01, factor=1.0, upto=None):
    """Hourly perpetual closes = spot hourly close × (1 + premium) × factor."""

    times, closes = [], []
    for k in range(3, market.n_grid if upto is None else upto, 4):
        hour = int(market.grid_open[k - 3])
        times.append(hour)
        closes.append(float(market.close[s, k]) * (1 + premium) * factor)
    return np.asarray(times, dtype=np.int64), np.asarray(closes)


def test_basis_compares_the_same_closed_hour_and_ignores_the_forming_one():
    market = _market()
    s = market.index_of["AAAUSDT"]
    times, closes = _perp_series(market, s)
    closes[(times >= START + 400 * 900)] *= 10   # the forming hour (bars 400-403) and later: must not be read
    out = sq.basis_at({"AAAUSDT": (times, closes)}, market, s, 401)
    assert out["basis_status"] == "OK" and out["basis_pct"] == pytest.approx(1.0)
    assert out["basis_24h"] == pytest.approx(1.0)


def test_basis_flags_missing_stale_and_mismatched_perpetuals():
    market = _market()
    s = market.index_of["AAAUSDT"]
    assert sq.basis_at(None, market, s, 401)["basis_status"] == "NOT_LOADED"
    assert sq.basis_at({}, market, s, 401)["basis_status"] == "NO_PERP"
    stale = _perp_series(market, s, upto=390)
    assert sq.basis_at({"AAAUSDT": stale}, market, s, 401)["basis_pct"] is None
    thousand = _perp_series(market, s, factor=1000.0)   # e.g. 1000PEPE not divided by its multiplier
    out = sq.basis_at({"AAAUSDT": thousand}, market, s, 401)
    assert out["basis_status"] == "MISMATCH" and out["basis_pct"] is None
    few = _perp_series(market, s)
    keep = few[0] >= START + (400 - 4 * 10) * 900       # only 10 of the last 24 hours
    out = sq.basis_at({"AAAUSDT": (few[0][keep], few[1][keep])}, market, s, 401)
    assert out["basis_pct"] == pytest.approx(1.0) and out["basis_24h"] is None


def test_likit_rows_carry_basis_without_changing_other_features():
    market = _market()
    s = market.index_of["AAAUSDT"]
    plain = sq.likit_rows(market, _steps(market), params=PARAMS, window=WINDOW)[0]
    with_perp = sq.likit_rows(market, _steps(market), params=PARAMS, window=WINDOW,
                              perp={"AAAUSDT": _perp_series(market, s, premium=-0.002)})[0]
    assert with_perp["basis_pct"] == pytest.approx(-0.2) and plain["basis_status"] == "NOT_LOADED"
    assert {k: v for k, v in plain.items() if not k.startswith("basis")} == \
        {k: v for k, v in with_perp.items() if not k.startswith("basis")}


def test_perp_prices_are_divided_by_the_contract_multiplier(tmp_path, monkeypatch):
    assert uf.multiplier_of("1000PEPEUSDT", "PEPEUSDT") == 1000 and uf.multiplier_of("XUSDT", "XUSDT") == 1
    with pytest.raises(uf.carry.DataQualityError):
        uf.multiplier_of("YUSDT", "XUSDT")
    universe = tmp_path / "u"
    universe.mkdir()
    (universe / "manifest.json").write_text(json.dumps({"symbols": {"PEPEUSDT": {"months": ["2024-01"]}}}))
    kline = lambda t, c: f"{t}000,{c},{c},{c},{c},1,{t + 3599}999,1,1,1,1,0"
    text = "\n".join([kline(START, 0.012), kline(START + 3600, 0.013), kline(START + 7200, 0.014)])
    monkeypatch.setattr(uf.carry, "_csv", lambda session, url, what: text)
    summary = uf.build_perp_hourly(universe, tmp_path / "p", decision_at=START + 7199, session=object(),
                                   perps=["1000PEPEUSDT"])
    assert summary["rows"] == 2                     # the third candle closes after the decision time
    times, closes = uf.load_perp_hourly(tmp_path / "p")["PEPEUSDT"]
    assert list(times) == [START, START + 3600] and closes == pytest.approx([0.000012, 0.000013])


def test_tactical_basis_uses_spot_and_perp_hours_closed_at_the_alert():
    t = START + 30 * 86_400 - 1
    data = _tactical_data(lambda s: 100.0)
    h1 = data["BTCUSDT"][TacticalTimeframe.H1]
    hours = np.arange(START, START + 31 * 86_400, 3600, dtype=np.int64)
    perp = {"BTCUSDT": (hours, np.where(hours < t, 100.5, 150.0))}
    out = sq.tactical_basis(perp, h1, "BTCUSDT", t)
    assert out["basis_status"] == "OK" and out["basis_pct"] == pytest.approx(0.5)
    row = sq.tactical_rows([_record(t)], data, perp=perp, window=WINDOW)[0]
    assert row["basis_pct"] == pytest.approx(0.5) and row["funding_status"] == "NOT_LOADED"


# ---------------------------------------------------------------------------
# Pre-registered tests and the confirmation gate
# ---------------------------------------------------------------------------

CONF = sq.DISCOVERY_END + 86_400 * 3


def _likit_row(day, *, atr, rel, result, bench=0.0):
    return {"symbol": "A", "decided_at": CONF + day * 86_400, "atr15_pct": atr, "rel_24h": rel,
            "stop72_result_pct": result, "stop72_bench_pct": bench}


def test_filters_use_inclusive_thresholds_and_say_unknown():
    assert sq.likit_calm_not_chasing({"atr15_pct": 1.0, "rel_24h": 5.0}) is True
    assert sq.likit_calm_not_chasing({"atr15_pct": 1.01, "rel_24h": 0.0}) is False
    assert sq.likit_calm_not_chasing({"atr15_pct": None, "rel_24h": 0.0}) is None
    assert sq.tactical_perp_below_spot({"basis_pct": -0.01}) is True
    assert sq.tactical_perp_below_spot({"basis_pct": 0.0}) is False
    assert sq.tactical_perp_below_spot({"basis_pct": None}) is None


def test_difference_ci_resamples_days_and_needs_both_groups():
    a = [(1.0 + (k % 3) * 0.1, CONF + k * 86_400) for k in range(60)]
    b = [(-1.0 + (k % 3) * 0.1, CONF + k * 86_400) for k in range(60)]
    lo, hi = sq.diff_ci(a, b)
    assert 1.8 < lo <= 2.0 <= hi < 2.2
    assert sq.diff_ci(a[:1], b) is None


def test_verdicts_separate_pass_loss_reduction_and_no_effect():
    late = sq.HALF_AT - sq.DISCOVERY_END
    days = [k for k in range(0, 700, 2)]
    good = [_likit_row(d, atr=0.5, rel=1.0, result=1.0 + (d % 5) * 0.1) for d in days]
    bad = [_likit_row(d + 1, atr=3.0, rel=9.0, result=-2.0 + (d % 5) * 0.1) for d in days]
    out = sq.evaluate_f1(good + bad)
    assert out["verdict"] == "PASS" and out["kept"] == len(good) and out["unknown"] == 0
    assert any(r["decided_at"] >= sq.HALF_AT for r in good) and late > 0

    flat = [_likit_row(d, atr=0.5, rel=1.0, result=0.2 + ((d % 5) - 2) * 0.5) for d in days]
    out = sq.evaluate_f1(flat + bad)
    assert out["verdict"] == "KAYBI_AZALTIR"          # kept ≈ 0 after costs, the dropped ones are worse
    assert sq.evaluate_f1(flat + [dict(r, atr15_pct=3.0) for r in flat])["verdict"] == "NO_EFFECT"
    unknown = sq.evaluate_f1([dict(good[0], atr15_pct=None)] + good + bad)
    assert unknown["unknown"] == 1 and unknown["kept"] == len(good)


def test_confirmation_refuses_code_that_was_not_registered(tmp_path):
    with pytest.raises(SystemExit):
        sq.require_registration(tmp_path / "registry.jsonl")
    from trading.research.robustness import TrialRecord, TrialRegistry

    registry = TrialRegistry(tmp_path / "registry.jsonl")
    registry.append(TrialRecord(trial_id=sq.filter_trial_id(), family=sq.FILTER_FAMILY, kind="SELECTION_CANDIDATE",
                                description="x", params=sq.filter_trial_params(), dataset=sq.FILTER_DATASET,
                                recorded_at="2026-10-01T00:00:00Z", n_trades=None, sharpe_per_trade=None))
    assert sq.require_registration(tmp_path / "registry.jsonl") == sq.filter_trial_id()
    with pytest.raises(SystemExit):
        sq._cli(["confirm", "likit", "--data-dir", str(tmp_path), "--trial-registry", str(tmp_path / "none.jsonl")])


def test_fingerprint_covers_stop_rules_but_not_message_text(monkeypatch):
    base = sq.logic_fingerprint()
    monkeypatch.setattr(long_alerts, "alert_text", lambda *a, **k: "changed")
    assert sq.logic_fingerprint() == base
    monkeypatch.setattr(long_alerts, "TRACK_HOURS", 48)
    assert sq.logic_fingerprint() != base


def test_signal_quality_filters_are_pre_registered_for_the_current_code():
    """Tripwire: changing a signal, a feature, an outcome or a threshold is a new trial."""

    from trading.research.robustness import TrialRegistry

    registry = TrialRegistry(sq.REPO_ROOT / "research" / "trials" / "registry.jsonl")
    assert sq.filter_trial_id() in {r.trial_id for r in registry.selection_trials(sq.FILTER_FAMILY)}


def test_random_hour_baseline_uses_the_same_stop_rule_inside_the_window():
    data = _tactical_data(lambda s: 100.0)
    window = (START + 38 * 86_400, START + 39 * 86_400)
    rows = sq.tactical_baseline_rows(data, perp=None, window=window)
    assert len(rows) == 2 * 24 and all(window[0] <= r["decided_at"] < window[1] for r in rows)
    assert {r["atr72_status"] for r in rows} == {"UNRESOLVABLE"}      # 72h runs past the data: no result
    assert all(r["basis_status"] == "NOT_LOADED" and r["can_authorize_trade"] is False for r in rows)
    early = sq.tactical_baseline_rows(data, perp=None, window=(START + 20 * 86_400, START + 21 * 86_400))
    assert {r["atr72_status"] for r in early} == {long_alerts.EXPIRED}
