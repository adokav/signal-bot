from __future__ import annotations

import json
import math

import numpy as np
import pytest

from trading.backtest import breakout_cohort as bc

ORIGIN = bc.day_of(2021, 9, 1)            # a Wednesday
DATA_END = bc.day_of(2026, 9, 1)


def _series(symbol, start_day, closes, *, lows=None, opens=None, qv=1e7, qvs=None):
    """Daily rows from closes; open = previous close; ±1% range unless a low/open is overridden."""

    rows = []
    previous = closes[0]
    for k, close in enumerate(closes):
        if close is None:
            previous = previous
            continue
        day = start_day + k
        o = (opens or {}).get(k, previous)
        lo = (lows or {}).get(k, min(o, close) * 0.99)
        hi = max(o, close) * 1.01
        rows.append((day, o, hi, min(lo, o, close), close, (qvs or {}).get(k, qv)))
        previous = close
    return bc.series_from_rows(symbol, rows)


def _flat(symbol, start_day, n, price=100.0, **kw):
    return _series(symbol, start_day, [price] * n, **kw)


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------


def test_discovery_labels_end_before_the_first_confirmation_origin():
    assert bc.purge_satisfied()
    assert bc.annual_origins(bc.DISCOVERY_YEARS)[-1] + bc.LABEL_DAYS <= bc.SEAL_DAY
    assert bc.iso(bc.SEAL_DAY) == "2022-09-01"
    assert [bc.iso(o) for o in bc.annual_origins(bc.CONFIRMATION_YEARS)][-1] == "2025-09-01"
    assert bc.annual_origins(bc.CONFIRMATION_YEARS)[-1] + bc.LABEL_DAYS <= DATA_END
    monthly = bc.monthly_origins(DATA_END)
    assert bc.iso(monthly[0]) == "2017-11-01" and monthly[-1] + bc.LABEL_DAYS <= DATA_END
    assert bc.iso(monthly[-1]) == "2025-09-01"                      # its 365-day window ends exactly at the data end


def test_candidate_eligibility_is_coin_indicators_only():
    params = bc.trial_params([{"indicator": "RS90", "direction": "UP"}])
    assert params["eligible"] == list(bc.COIN_INDICATORS)
    for name in bc.PERP_INDICATORS + bc.MARKET_INDICATORS + bc.MACRO_INDICATORS:
        assert name in params["descriptive_only"] and name not in params["eligible"]
    assert params["code_fingerprint"] == bc.code_fingerprint()
    other = bc.trial_id([{"indicator": "R90", "direction": "UP"}])
    assert bc.trial_id([{"indicator": "RS90", "direction": "UP"}]) != other


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def _path_with_2x():
    before = [100.0] * 100
    after = [100.0] * 10 + [80.0] + [100.0 + 2.5 * k for k in range(1, 40)] + [200.0] + [150.0] * 320
    return before + after                                      # 2x on the 50th day after the origin


def test_label_time_to_2x_drawdowns_wick_and_flash():
    closes = _path_with_2x()
    start = ORIGIN - 100
    lows = {110: 70.0}                                           # the dip day: intraday 70, close 80
    series = _series("AUSDT", start, closes, lows=lows)
    label = bc.label_at(series, ORIGIN, bc.LABEL_DAYS, data_end_day=DATA_END)
    assert label is not None and label.hit and label.t2x == 50
    assert label.entry == pytest.approx(100.0)                   # the origin day's open = the previous close
    assert label.multiple == pytest.approx(2.0)
    assert label.dd_before_2x == pytest.approx(-0.2)             # 80 against the entry peak of 100
    assert label.dd_max == pytest.approx(-0.25)                  # 150 against the 200 peak
    assert label.wick_min == pytest.approx(0.70 / 1.0 - 1.0)     # the 70 low against the 100 entry
    assert label.flash_max == pytest.approx(70.0 / 100.0 - 1.0)  # against the previous close
    assert label.final_return == pytest.approx(0.5) and not label.delisted and label.days == 365
    assert bc.label_at(series, ORIGIN, bc.LONG_LABEL_DAYS, data_end_day=ORIGIN + 400) is None   # beyond the data


def test_label_delisting_exits_at_the_last_close_and_holes_are_unknown():
    closes = [100.0] * 100 + [110.0] * 100                       # data stops 100 days after the origin
    series = _series("AUSDT", ORIGIN - 100, closes)
    label = bc.label_at(series, ORIGIN, bc.LABEL_DAYS, data_end_day=DATA_END)
    assert label is not None and label.delisted and label.days == 100
    assert label.final_return == pytest.approx(0.1) and not label.hit
    with_hole = [100.0] * 100 + [110.0] * 50 + [None, None] + [120.0] * 300
    series = _series("AUSDT", ORIGIN - 100, with_hole)
    assert bc.label_at(series, ORIGIN, bc.LABEL_DAYS, data_end_day=DATA_END) is None
    one_day = [100.0] * 100 + [110.0] * 50 + [None] + [120.0] * 300
    series = _series("AUSDT", ORIGIN - 100, one_day)
    label = bc.label_at(series, ORIGIN, bc.LABEL_DAYS, data_end_day=DATA_END)
    assert label is not None and label.final_return == pytest.approx(0.2)
    no_entry = _series("AUSDT", ORIGIN + 1, [100.0] * 400)
    assert bc.label_at(no_entry, ORIGIN, bc.LABEL_DAYS, data_end_day=DATA_END) is None


# ---------------------------------------------------------------------------
# Membership and features
# ---------------------------------------------------------------------------


def test_membership_needs_history_a_last_bar_and_volume_and_keeps_the_top_150():
    universe = {}
    universe["SHORT"] = _flat("SHORT", ORIGIN - 60, 60)                      # 60 days: too young
    universe["STALE"] = _flat("STALE", ORIGIN - 200, 190)                    # last bar 10 days before the origin
    universe["THIN"] = _flat("THIN", ORIGIN - 200, 200, qv=5e5)              # below the 1 M$ floor
    for k in range(160):
        universe[f"C{k:03d}"] = _flat(f"C{k:03d}", ORIGIN - 200, 200, qv=2e6 + 1e3 * k)
    members = bc.members_at(universe, ORIGIN)
    names = [s for s, _ in members]
    assert len(members) == 150 and names[0] == "C159" and "C009" not in names and "C010" in names
    assert not {"SHORT", "STALE", "THIN"} & set(names)


def test_features_read_only_the_history_and_match_their_definitions():
    closes = [100.0 + k for k in range(400)]                                  # 100 .. 499, one per day
    series = _series("AUSDT", ORIGIN - 400, closes, lows={399: 300.0})
    feats = bc.coin_features(series, ORIGIN)
    last, back90, back365 = 499.0, 499.0 - 90, 499.0 - 365
    assert feats["R90"] == pytest.approx(last / back90 - 1)
    assert feats["R365"] == pytest.approx(last / back365 - 1)
    assert feats["DIST_HIGH365"] == pytest.approx(0.0)                        # the last close is the high
    assert feats["DIST_LOW730"] == pytest.approx(last / 100.0 - 1)            # the whole history (400 days)
    assert feats["AGE"] == 400 and feats["VOL_TREND"] == pytest.approx(1.0)
    assert feats["WICK90"] == pytest.approx(300.0 / last - 1)
    assert feats["FLASH90"] == pytest.approx(300.0 / 498.0 - 1)
    assert feats["SIGMA90"] is not None and 0 < feats["SIGMA90"] < 0.5
    tampered = _series("AUSDT", ORIGIN - 400, closes + [5.0] * 50, lows={399: 300.0, 420: 1.0})
    assert bc.coin_features(tampered, ORIGIN) == feats                        # future bars change nothing


def _with_gaps(closes, missing):
    return bc.series_from_rows("AUSDT", [(ORIGIN - len(closes) + k, c, c * 1.01, c * 0.99, c, 1e7)
                                         for k, c in enumerate(closes) if k not in missing])


def test_a_hole_inside_a_feature_window_makes_every_feature_unknown_a_single_day_does_not():
    closes = [100.0] * 200
    feats = bc.coin_features(_with_gaps(closes, {150, 151}), ORIGIN)        # a two-day hole 50 days back
    for name in ("R90", "SIGMA90", "WICK90", "FLASH90", "VOL_TREND"):
        assert feats[name] is None, name
    assert bc.median_volume(_with_gaps(closes, {180, 181}), ORIGIN) is None  # a hole in the last 30 days
    assert bc.eligible(_with_gaps(closes, {180, 181}), ORIGIN)               # history exists ...
    assert bc.members_at({"AUSDT": _with_gaps(closes, {180, 181})}, ORIGIN) == []   # ... but no known volume
    feats = bc.coin_features(_with_gaps(closes, {150}), ORIGIN)
    assert feats["R90"] == pytest.approx(0.0) and feats["SIGMA90"] == pytest.approx(0.0)
    assert feats["VOL_TREND"] == pytest.approx(1.0) and feats["FLASH90"] == pytest.approx(-0.01)


def test_single_missing_day_allowances_follow_the_window_class():
    assert (bc._allowed_missing(30), bc._allowed_missing(91), bc._allowed_missing(366), bc._allowed_missing(730)) \
        == (2, 2, 5, 10)
    closes = [100.0] * 400                                                    # index k = day ORIGIN - 400 + k
    five = {60, 100, 140, 180, 220}                                           # isolated days inside [34, 400)
    assert bc.coin_features(_with_gaps(closes, five), ORIGIN)["R365"] == pytest.approx(0.0)
    six = five | {260}
    assert bc.coin_features(_with_gaps(closes, six), ORIGIN)["R365"] is None
    three_in_90 = {320, 340, 360}
    assert bc.coin_features(_with_gaps(closes, three_in_90), ORIGIN)["R90"] is None
    assert bc.coin_features(_with_gaps(closes, {320, 340}), ORIGIN)["R90"] == pytest.approx(0.0)


def test_universe_exclusions_cover_identity_rules_and_known_migrations():
    bases = {"BTC", "ETH", "MATIC", "POL", "AAVE", "LEND", "USDC", "BTCUP"}
    assert bc.universe_exclusion("ETHUSDT", bases) is None
    assert bc.universe_exclusion("USDCUSDT", bases) == "STABLE"
    assert bc.universe_exclusion("BTCUPUSDT", bases) == "LEVERAGED"
    assert bc.universe_exclusion("POLUSDT", bases) == "MIGRATION" and bc.universe_exclusion("MATICUSDT", bases) == "MIGRATION"
    assert bc.universe_exclusion("AAVEUSDT", bases) == "MIGRATION" and bc.universe_exclusion("LENDUSDT", bases) == "MIGRATION"


def test_a_short_listing_fails_the_build():
    from trading.data.binance_perp import DataQualityError

    bc.check_listing(400, True, 200)
    with pytest.raises(DataQualityError):
        bc.check_listing(50, True, 200)
    with pytest.raises(DataQualityError):
        bc.check_listing(400, False, 200)
    with pytest.raises(DataQualityError):
        bc.check_listing(400, True, 20)


def test_buckets_follow_the_fixed_thresholds():
    r90 = bc.BUCKETS["R90"]
    assert r90.of(-0.31) == 0 and r90.of(-0.30) == 1 and r90.of(0.30) == 1 and r90.of(0.31) == 2
    assert r90.of(None) is None and r90.of(math.nan) is None
    rank = bc.BUCKETS["VOL_RANK"]
    assert rank.of(1) == 2 and rank.of(30) == 2 and rank.of(31) == 1 and rank.of(90) == 1 and rank.of(91) == 0
    assert bc.BUCKETS["WICK90"].of(-0.5) == 0 and bc.BUCKETS["WICK90"].of(-0.1) == 2
    assert bc.BUCKETS["FUND90"].of(-0.01) == 0 and bc.BUCKETS["FUND90"].of(0.2) == 2


def test_btc_weekly_channel_state():
    weekday = (ORIGIN + 3) % 7
    assert weekday == 2                                                       # 2021-09-01 is a Wednesday
    monday = ORIGIN - weekday
    closes = {d: 100.0 for d in range(ORIGIN - 120, ORIGIN + 5)}

    def btc(last_sunday_close):
        values = dict(closes)
        values[monday - 1] = last_sunday_close
        return bc.series_from_rows("BTCUSDT", [(d, v, v * 1.01, v * 0.99, v, 1e9) for d, v in values.items()])

    assert bc.btc_trend(btc(120.0), ORIGIN) == "UP"
    assert bc.btc_trend(btc(80.0), ORIGIN) == "DOWN"
    assert bc.btc_trend(btc(100.0), ORIGIN) == "SIDEWAYS"
    short = bc.series_from_rows("BTCUSDT", [(d, 100.0, 101.0, 99.0, 100.0, 1e9) for d in range(ORIGIN - 30, ORIGIN)])
    assert bc.btc_trend(short, ORIGIN) is None


def test_funding_stablecoin_fng_and_macro_helpers():
    settlements = [((ORIGIN - 90) * bc.DAY + 8 * 3600 * k, 0.0001) for k in range(270)]
    assert bc.funding_90(settlements, ORIGIN) == pytest.approx(0.0001 * 3 * 365)
    assert bc.funding_90(settlements[:100], ORIGIN) is None and bc.funding_90(None, ORIGIN) is None
    stable = {ORIGIN - 1: 110.0, ORIGIN - 91: 100.0}
    assert bc.stable_change(stable, ORIGIN) == pytest.approx(0.10)
    assert bc.stable_change({ORIGIN - 1: 110.0}, ORIGIN) is None
    fng = {d: 50.0 for d in range(ORIGIN - 30, ORIGIN)}
    assert bc.fng_mean(fng, ORIGIN) == 50.0 and bc.fng_mean({ORIGIN - 1: 50.0}, ORIGIN) is None
    monthly = {"2021-08": {"us_10y_real": "-1.0", "net_liquidity_busd": "6000"},
               "2021-05": {"us_10y_real": "-0.8", "net_liquidity_busd": "5000"}}
    daily = {bc.iso(ORIGIN): {"vix": "16.5", "nasdaq": "15000"}, bc.iso(ORIGIN - 90): {"vix": "18", "nasdaq": "12000"}}
    macro = bc.macro_features(monthly, daily, ORIGIN)
    assert macro["REAL_10Y"] == -1.0 and macro["REAL_10Y_90"] == pytest.approx(-0.2)
    assert macro["NET_LIQ_90"] == pytest.approx(0.2) and macro["VIX"] == 16.5
    assert macro["NASDAQ_90"] == pytest.approx(0.25)
    assert bc.macro_features({}, {}, ORIGIN) == {k: None for k in bc.MACRO_INDICATORS}


# ---------------------------------------------------------------------------
# Within-cohort statistics and the candidate rule
# ---------------------------------------------------------------------------


def _cohort(origin, *, top_hits, bottom_hits, n=20, indicator="X", seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for bucket, hits in ((2, top_hits), (0, bottom_hits), (1, 0.5)):
        for k in range(n):
            hit = bool(rng.random() < hits)
            rows.append({"symbol": f"S{bucket}{k}", "features": {}, "perp": {}, "buckets": {indicator: bucket},
                         "label": {"hit": hit, "multiple": 2.0 if hit else 1.2, "t2x": 30 if hit else None,
                                   "dd_before_2x": -0.2, "dd_max": -0.3, "wick_min": -0.35, "flash_max": -0.1,
                                   "final_return": 0.5, "delisted": False, "days": 365, "horizon": 365,
                                   "entry": 1.0},
                         "label24": None, "perp_label": None})
    return bc.Cohort(origin, "annual", rows, {}, {})


def test_within_cohort_lift_pooling_and_the_candidate_rule():
    cohorts = [_cohort(bc.day_of(y, 9), top_hits=0.9, bottom_hits=0.1, seed=y) for y in bc.DISCOVERY_YEARS]
    stats = bc.indicator_stats(cohorts, "X")
    assert stats["known"] == 4 and stats["signs_agree"] and stats["lift"] > 0.5
    assert stats["ci"][0] > 0.3 and bc.is_candidate(stats)
    assert bc.stratified_bootstrap(cohorts, "X") == bc.stratified_bootstrap(cohorts, "X")    # seeded
    small = _cohort(bc.day_of(2018, 9), top_hits=0.9, bottom_hits=0.1, n=5)
    assert bc.cohort_lift(small.rows, "X")["lift"] is None                                   # < 10 per bucket
    mixed = cohorts[:3] + [_cohort(bc.day_of(2021, 9), top_hits=0.1, bottom_hits=0.9, seed=7)]
    assert not bc.is_candidate(bc.indicator_stats(mixed, "X"))                               # signs disagree
    weak = [_cohort(bc.day_of(y, 9), top_hits=0.55, bottom_hits=0.45, n=15, seed=y) for y in bc.DISCOVERY_YEARS]
    assert not bc.is_candidate(bc.indicator_stats(weak, "X"))
    assert bc.select_candidates({"X": stats}) == []                                           # X is not eligible
    assert bc.select_candidates({"RS90": stats})[0]["indicator"] == "RS90"
    assert bc.select_candidates({"FUND90": stats}) == []                                      # perp layer never


def test_random_buckets_rarely_pass_and_the_marker_is_removed():
    cohorts = [_cohort(bc.day_of(y, 9), top_hits=0.5, bottom_hits=0.5, n=30, seed=y) for y in bc.DISCOVERY_YEARS]
    control = bc.random_control(cohorts, reps=20, seed=1)
    assert control["reps"] == 20 and control["passes"] <= 3
    assert all("_RANDOM" not in r["buckets"] for c in cohorts for r in c.rows)


def test_verdicts():
    discovery = {"lift": 0.3}
    good = {"per_cohort": {str(k): {"lift": 0.2} for k in range(4)}, "lift": 0.25, "ci": [0.05, 0.4],
            "unknown_share": 0.0}
    assert bc.verdict(discovery, good, family_alpha=0.025) == "CONFIRMED"
    assert bc.verdict(discovery, {**good, "ci": [-0.1, 0.4]}, family_alpha=0.025) == "NO_EFFECT"
    reversed_ = {**good, "per_cohort": {str(k): {"lift": -0.2} for k in range(4)}, "lift": -0.25, "ci": [-0.4, -0.05]}
    assert bc.verdict(discovery, reversed_, family_alpha=0.025) == "REVERSED"
    assert bc.verdict(discovery, {**good, "unknown_share": 0.2}, family_alpha=0.025) == "INCOMPLETE_DATA"
    two = {**good, "per_cohort": {"a": {"lift": 0.2}, "b": {"lift": 0.2}, "c": {"lift": None}}}
    assert bc.verdict(discovery, two, family_alpha=0.025) == "INCOMPLETE_DATA"


# ---------------------------------------------------------------------------
# End to end on a synthetic universe, the seal and registration
# ---------------------------------------------------------------------------


def _synthetic_dataset(n_symbols=40, seed=0):
    rng = np.random.default_rng(seed)
    start = bc.day_of(2017, 8, 17)
    days = DATA_END - start
    spot = {}
    for k in range(n_symbols + 1):
        symbol = "BTCUSDT" if k == 0 else f"C{k:03d}USDT"
        drift = 0.0 if k == 0 else rng.normal(0.0, 0.0008)
        steps = rng.normal(drift, 0.04, days)
        closes = 100.0 * np.exp(np.cumsum(steps))
        qv = 5e6 + 1e6 * k
        spot[symbol] = bc.series_from_rows(symbol, [(start + d, closes[d - 1] if d else closes[0], closes[d] * 1.03,
                                                     closes[d] * 0.95, closes[d], qv) for d in range(days)])
    return bc.Dataset(spot=spot, data_end_day=DATA_END)


def test_discovery_runs_on_a_synthetic_universe_and_never_reads_sealed_cohorts(tmp_path):
    data = _synthetic_dataset()
    report = bc.run_discovery(data, reps=3, descriptive=False)
    assert report["cohorts"] == ["2018-09-01", "2019-09-01", "2020-09-01", "2021-09-01"]
    assert all(bc.day_of(int(c[:4]), 9) < bc.SEAL_DAY for c in report["cohorts"])
    assert set(report["indicators"]) == set(bc.COIN_INDICATORS + bc.PERP_INDICATORS)
    assert report["can_authorize_trade"] is False and "random_control" in report
    for base in report["base_rates"].values():
        assert base["members"] == 41 and base["labelled"] == 41
    for card in report["period_cards"]:
        assert card["market"]["BTC_TREND"] in {"UP", "DOWN", "SIDEWAYS"} and card["market"]["UNIVERSE_SIZE"] == 41
    assert set(report["sigma_wick_table"]) == {"0", "1", "2"}
    assert bc.validate_discovery_report(json.loads(json.dumps(report))) == []                 # nothing separates
    registry = tmp_path / "registry.jsonl"
    with pytest.raises(SystemExit):
        bc.run_confirmation(data, report, registry)                                            # no candidate: sealed
    with pytest.raises(SystemExit):
        bc.register_report(registry, report)
    hand_picked = json.loads(json.dumps(report))
    hand_picked["candidates"] = [{"indicator": "RS90", "direction": "UP", "lift": 0.5, "ci": [0.2, 0.8]}]
    with pytest.raises(SystemExit):
        bc.validate_discovery_report(hand_picked)                                              # not its own rule's output
    with pytest.raises(SystemExit):
        bc.validate_discovery_report({**report, "stage": "confirmation"})
    with pytest.raises(SystemExit):
        bc.validate_discovery_report({**report, "code_fingerprint": "0" * 16})


def _pattern_dataset(n_each=15):
    """Winners rise 50% in the 90 days before every 1 September and double after it; losers fall 40% and never do."""

    start, end = bc.day_of(2017, 8, 17), DATA_END

    def shape(t, winner):                                # t = days since the last 1 September
        if winner:
            return 1.5 + 1.8 * t / 180 if t < 180 else (3.3 - 2.3 * (t - 180) / 95 if t < 275 else 1.0 + 0.5 * (t - 275) / 90)
        return 0.6 + 0.4 * t / 275 if t < 275 else 1.0 - 0.4 * (t - 275) / 90

    def price(day, winner, base):
        from datetime import date
        y = date.fromtimestamp(day * bc.DAY).year
        origin = bc.day_of(y, 9, 1)
        if day < origin:
            origin = bc.day_of(y - 1, 9, 1)
        return base * shape(day - origin, winner)

    spot = {"BTCUSDT": bc.series_from_rows("BTCUSDT", [(d, 100.0, 101.0, 99.0, 100.0, 1e9) for d in range(start, end)])}
    for k in range(2 * n_each):
        winner, symbol = k < n_each, f"{'W' if k < n_each else 'L'}{k:02d}USDT"
        rows = []
        previous = price(start, winner, 10.0 + k)
        for d in range(start, end):
            close = price(d, winner, 10.0 + k)
            rows.append((d, previous, max(previous, close) * 1.01, min(previous, close) * 0.99, close, 5e6))
            previous = close
        spot[symbol] = bc.series_from_rows(symbol, rows)
    return bc.Dataset(spot=spot, data_end_day=end)


def test_registration_binds_the_discovery_and_confirmation_reproduces_it(tmp_path):
    data = _pattern_dataset()
    report = json.loads(json.dumps(bc.run_discovery(data, reps=2, descriptive=False)))
    names = {c["indicator"] for c in report["candidates"]}
    assert {"R90", "RS90"} <= names and all(c["direction"] == "UP" for c in report["candidates"])
    assert report["indicators"]["R90"]["lift"] == pytest.approx(1.0) and report["indicators"]["R90"]["known"] == 4
    registry = tmp_path / "registry.jsonl"
    with pytest.raises(SystemExit):
        bc.run_confirmation(data, report, registry)                                            # not registered yet
    tid = bc.register_report(registry, report, now=1_800_000_000)
    assert tid == bc.trial_id(report["candidates"], bc.report_digest(report))
    confirmation = bc.run_confirmation(data, report, registry)
    assert confirmation["trial_id"] == tid
    assert confirmation["cohorts"] == ["2022-09-01", "2023-09-01", "2024-09-01", "2025-09-01"]
    assert confirmation["results"]["R90"]["verdict"] == "CONFIRMED"
    assert confirmation["family_alpha"] == pytest.approx(0.05 / len(report["candidates"]))
    edited = json.loads(json.dumps(report))
    edited["indicators"]["AGE"]["lift"] = 0.9                                                  # inconsistent edit
    edited["indicators"]["AGE"].update(known=4, signs_agree=True, ci=[0.5, 1.0])
    with pytest.raises(SystemExit):
        bc.run_confirmation(data, edited, registry)                                            # its own rule now differs
    consistent = json.loads(json.dumps(edited))
    consistent["candidates"] = bc.select_candidates(consistent["indicators"])                  # made self-consistent
    with pytest.raises(SystemExit):
        bc.run_confirmation(data, consistent, registry)                                        # digest not registered
    bc.register_report(registry, consistent, now=1_800_000_100)
    with pytest.raises(SystemExit):
        bc.run_confirmation(data, consistent, registry)                                        # does not reproduce


def test_monthly_cohorts_are_descriptive_and_stop_at_the_seal():
    data = _synthetic_dataset(n_symbols=12, seed=3)
    monthly = bc.descriptive_cohorts(data, before=bc.SEAL_DAY)
    assert monthly[0].name == "2017-11-01" and monthly[-1].origin < bc.SEAL_DAY
    assert all(c.kind == "monthly" for c in monthly)


def test_load_checks_the_manifest_hashes(tmp_path):
    data_dir = tmp_path / "data"
    rows = [(ORIGIN - 200 + k, 100.0, 101.0, 99.0, 100.0, 2e6) for k in range(300)]
    bc._write_rows(rows, ("day", "open", "high", "low", "close", "quote_volume"), data_dir / "spot" / "AUSDT.csv")
    bc._write_rows([(ORIGIN - 1, 1.0e11)], ("day", "total_usd"), data_dir / "stablecoins.csv")
    bc._write_rows([(ORIGIN - 1, 40.0)], ("day", "value"), data_dir / "fng.csv")
    manifest = {"schema": bc.SCHEMA, "data_end": "2026-09", "spot": {"AUSDT": {"rows": 300}}, "perp": {},
                "files": {name: bc._sha256(data_dir / name) for name in ("spot/AUSDT.csv", "stablecoins.csv", "fng.csv")}}
    (data_dir / "manifest.json").write_text(json.dumps(manifest), "utf-8")
    data = bc.load(data_dir, atlas_dir=tmp_path / "no-atlas")
    assert set(data.spot) == {"AUSDT"} and data.data_end_day == DATA_END and data.fng[ORIGIN - 1] == 40.0
    (data_dir / "spot" / "AUSDT.csv").write_text("day,open,high,low,close,quote_volume\n1,1,1,1,1,1\n", "utf-8")
    with pytest.raises(ValueError):
        bc.load(data_dir, atlas_dir=tmp_path / "no-atlas")
