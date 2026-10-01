from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("pandas")

from trading.backtest import liquid_replay as lr
from trading.backtest import trade_loop as tl

T0 = 1_704_067_200            # 2024-01-01T00:00:00Z
DAY_S = 86_400
BAR = 900
D1 = tl.LOOPS[0]              # D1_20_10


def _rows(prices, *, start=T0, wick=0.01, opens=None, lows=None):
    """96 flat 15m bars per day at the day's price, ±wick intraday; overrides keyed by (day, bar)."""

    rows = []
    for d, p in enumerate(prices):
        if p is None:
            continue
        for k in range(96):
            t = start + (d * 96 + k) * BAR
            o = (opens or {}).get((d, k), p)
            lo = (lows or {}).get((d, k), p * (1 - wick))
            rows.append((t, t + BAR - 1, o, max(o, p) * (1 + wick), min(o, lo), p, 1e6))
    return rows


def _market(series):
    return lr.build_market({s: lr.rows_to_columns(r) for s, r in series.items()})


def _breakout_path(tail):
    """30 flat days at 100, a breakout to 101 on day 30, a climb to 110, then ``tail``."""

    return [100.0] * 30 + [101.0] + [102.0 + k for k in range(9)] + list(tail)


def _one(prices, **kw):
    market = _market({"AUSDT": _rows(prices, **kw)})
    daily = tl.daily_bars(market)
    return market, daily, tl.indicators(daily), tl.wm.continuity_breaks(market)


def test_daily_candles_come_from_closed_15m_candles():
    market, daily, _, _ = _one([100.0, 105.0], lows={(1, 7): 90.0})
    assert daily.close[0].tolist() == [100.0, 105.0]
    assert daily.low[0, 1] == 90.0 and daily.high[0, 1] == pytest.approx(105 * 1.01)
    off = _market({"AUSDT": _rows([100.0, 101.0], start=T0 + BAR)})
    with pytest.raises(ValueError):
        tl.daily_bars(off)


def test_entry_is_decided_at_the_close_and_traded_at_the_next_open():
    prices = _breakout_path([110.0] * 5)
    market, daily, ind, breaks = _one(prices)
    signals = tl.entry_signals(D1, daily, ind)
    assert not signals[0, 29] and signals[0, 30]                    # 101 > the previous 20 closes (100)
    trade = tl.simulate(market, daily, ind, D1, 0, 30, breaks)
    assert trade.decided_at == T0 + 31 * DAY_S
    assert trade.entry_day == 31 and trade.entry_price == 102.0    # day 31's open, not day 30's close (101)
    assert trade.stop == pytest.approx(102.0 - 3 * ind.atr[0, 30])


def test_future_prices_do_not_change_an_entry_decision():
    base = _breakout_path([110.0] * 5)
    _, daily, ind, _ = _one(base)
    _, daily2, ind2, _ = _one(base[:31] + [50.0] * (len(base) - 31))
    assert tl.entry_signals(D1, daily, ind)[0, 30] == tl.entry_signals(D1, daily2, ind2)[0, 30]


def test_the_channel_exit_fires_on_a_close_below_the_10_day_low_and_trades_next_open():
    fall = [108.0, 106.0, 104.0, 102.0, 100.0, 99.0, 98.0, 97.0]
    prices = _breakout_path(fall)
    market, daily, ind, breaks = _one(prices, wick=0.002)
    trade = tl.simulate(market, daily, ind, D1, 0, 30, breaks)
    exit_day = next(e for e in range(32, len(prices))
                    if prices[e] < min(prices[e - 10:e]))
    assert trade.exit_reason == "signal" and trade.status == "RESOLVED"
    assert trade.exit_day == exit_day + 1 and trade.exit_price == prices[exit_day + 1]


def test_the_disaster_stop_fills_at_the_stop_or_at_a_gapped_open():
    prices = _breakout_path([110.0] * 5)
    market, daily, ind, breaks = _one(prices, lows={(33, 40): 50.0})
    trade = tl.simulate(market, daily, ind, D1, 0, 30, breaks)
    assert trade.exit_reason == "stop" and trade.exit_day == 33 and trade.exit_price == pytest.approx(trade.stop)
    market, daily, ind, breaks = _one(prices, opens={(33, 40): 60.0}, lows={(33, 40): 60.0})
    trade = tl.simulate(market, daily, ind, D1, 0, 30, breaks)
    assert trade.exit_reason == "stop" and trade.exit_price == 60.0          # gapped through: the open, not the stop
    assert trade.r < -1.0


def test_a_pair_specific_gap_inside_a_trade_is_unknown_but_an_exchange_halt_is_not():
    prices = _breakout_path([110.0] * 8)
    rows = _rows(prices)
    hole = {T0 + (34 * 96 + k) * BAR for k in range(10, 14)}                  # one hour missing on day 34
    gapped = [r for r in rows if r[0] not in hole]
    market = _market({"AUSDT": gapped})
    daily = tl.daily_bars(market)
    ind, breaks = tl.indicators(daily), tl.wm.continuity_breaks(market)
    assert tl.simulate(market, daily, ind, D1, 0, 30, breaks).status == "UNKNOWN"   # no halt map: fail closed
    other = {f"B{k}USDT": [r for r in _rows(prices) if r[0] not in hole] for k in range(5)}
    market = _market({"AUSDT": gapped, **other})                              # every pair is missing: a halt
    daily = tl.daily_bars(market)
    ind, breaks = tl.indicators(daily), tl.wm.continuity_breaks(market)
    halts = tl.halt_bars(market)
    assert halts[34 * 96 + 11] and not halts[34 * 96 + 20]
    assert tl.simulate(market, daily, ind, D1, 0, 30, breaks, halts).status == "RESOLVED"
    alone = _market({"AUSDT": gapped, **{f"B{k}USDT": _rows(prices) for k in range(5)}})
    daily = tl.daily_bars(alone)
    trade = tl.simulate(alone, daily, tl.indicators(daily), D1, 0, 30, tl.wm.continuity_breaks(alone),
                        tl.halt_bars(alone))
    assert trade.status == "UNKNOWN"                                          # only this pair is missing


def test_entry_signals_that_cannot_trade_are_counted_not_dropped():
    prices = _breakout_path([110.0] * 5)
    rows = [r for r in _rows(prices) if r[0] != T0 + 31 * 96 * BAR]          # the execution open is missing
    market = _market({"AUSDT": rows})
    daily = tl.daily_bars(market)
    ind, breaks = tl.indicators(daily), tl.wm.continuity_breaks(market)
    assert tl.simulate(market, daily, ind, D1, 0, 30, breaks) == tl.Skip("no_open")
    trades, skipped = tl.run_loop(market, daily, ind, D1, {0: [(30, T0)]}, breaks)
    assert trades == [] and skipped == {"no_open": 1}
    stats = tl.completeness(trades, daily, market, {0: [(30, T0)]}, {T0: ["BTCUSDT", "ETHUSDT"]}, skipped)
    assert stats["unknown_trade_share"] == 1.0 and "unknown trades 100.0%" in tl.incomplete(stats)


def test_a_token_swap_during_the_trade_is_unknown_not_a_return():
    prices = _breakout_path([110.0] * 3) + [None, None, None] + [110_000.0] * 5 + [50_000.0] * 12
    market, daily, ind, breaks = _one(prices)
    trade = tl.simulate(market, daily, ind, D1, 0, 30, breaks)
    assert trade.status == "UNKNOWN"


def test_the_loop_repeats_and_the_random_control_is_reproducible():
    second = [96.0] * 25 + [99.0] + [100.0 + k for k in range(6)] + [90.0] * 12
    prices = _breakout_path([108.0, 104.0, 100.0, 96.0]) + second
    market = _market({"AUSDT": _rows(prices, wick=0.002)})
    daily = tl.daily_bars(market)
    ind, breaks = tl.indicators(daily), tl.wm.continuity_breaks(market)
    days = {0: [(d, T0) for d in range(25, daily.n_days)]}
    rows, _ = tl.run_loop(market, daily, ind, D1, days, breaks, seed=7)
    assert len(rows) == 2 and rows[1]["entry_day"] > rows[0]["exit_day"]          # one position at a time, then again
    assert all(r["random_draws"] <= tl.RANDOM_DRAWS for r in rows)
    again, _ = tl.run_loop(market, daily, ind, D1, days, breaks, seed=7)
    assert [r["random_mean_net_pct"] for r in rows] == [r["random_mean_net_pct"] for r in again]


def test_portfolio_charges_costs_and_compares_with_buy_and_hold():
    prices = _breakout_path([110.0] * 2 + [108.0, 106.0, 104.0, 102.0, 100.0, 99.0, 98.0, 97.0])
    market = _market({"AUSDT": _rows(prices, wick=0.002)})
    daily = tl.daily_bars(market)
    ind, breaks = tl.indicators(daily), tl.wm.continuity_breaks(market)
    days = {0: [(d, T0) for d in range(25, daily.n_days)]}
    rows, _ = tl.run_loop(market, daily, ind, D1, days, breaks)
    assert len(rows) == 1
    window = (T0 + DAY_S, T0 + daily.n_days * DAY_S)
    book = tl.portfolio(market, daily, rows, {T0: ["AUSDT"]}, window, breaks, half_at=T0 + 40 * DAY_S)
    assert book["loop"]["total_return_pct"] == pytest.approx(rows[0]["net_pct"], abs=0.05)
    assert 0 < book["time_in_market"] < 1 and book["capped_days"] == 0


def test_positions_kept_after_a_universe_rotation_never_lever_the_portfolio():
    prices = _breakout_path([110.0 + k for k in range(20)])
    market = _market({"AUSDT": _rows(prices, wick=0.002), "BUSDT": _rows(prices, wick=0.002)})
    daily = tl.daily_bars(market)
    ind, breaks = tl.indicators(daily), tl.wm.continuity_breaks(market)
    month2 = T0 + 33 * DAY_S
    rows_a, _ = tl.run_loop(market, daily, ind, D1, {0: [(30, T0)]}, breaks)     # A entered in a 1-coin month
    rows_b, _ = tl.run_loop(market, daily, ind, D1, {1: [(34, month2)]}, breaks)  # B in a later 1-coin month
    monthly = {T0: ["AUSDT"], month2: ["BUSDT"]}
    window = (T0 + DAY_S, T0 + daily.n_days * DAY_S)
    book = tl.portfolio(market, daily, rows_a + rows_b, monthly, window, breaks, half_at=T0 + 40 * DAY_S)
    assert book["capped_days"] > 0 and book["time_in_market"] <= 1.0 + 1e-9
    assert book["buy_and_hold"]["total_return_pct"] == pytest.approx((prices[-1] / prices[0] - 1) * 100, abs=0.5)


# ---------------------------------------------------------------------------
# Decision rules (fixed before results)
# ---------------------------------------------------------------------------


def _s(mean, ci, h1, h2, n=500):
    return {"n": n, "mean": mean, "ci": ci, "h1": h1, "h2": h2}


GOOD, FLAT = _s(1.0, (0.2, 1.8), 0.8, 1.2), _s(0.0, (-0.5, 0.5), 0.2, -0.2)


def _book(sharpe=1.0, base=0.8, dd=-30.0, base_dd=-70.0, h=(1.0, 1.0), base_h=(0.8, 0.8)):
    return {"loop": {"sharpe": sharpe, "max_drawdown_pct": dd, "sharpe_h1": h[0], "sharpe_h2": h[1]},
            "buy_and_hold": {"sharpe": base, "max_drawdown_pct": base_dd, "sharpe_h1": base_h[0], "sharpe_h2": base_h[1]}}


def _result(loop="D1_20_10", family="D1", net=GOOD, diff=GOOD, stress=GOOD, book=None, resolved=400):
    return {"loop": loop, "family": family, "resolved": resolved, "net_pct": net, "minus_random_pct": diff,
            "stress_net_pct": stress, "portfolio": book or _book()}


def test_candidate_rule_and_selection():
    results = [
        _result(),
        _result("D1_55_20", diff=_s(1.0, (0.5, 1.5), 0.9, 1.1)),
        _result("D2_EMA50", "D2", diff=_s(1.0, (0.1, 1.9), 0.9, 1.1)),
        _result("D2_DIP", "D2", book=_book(sharpe=0.5)),                        # worse Sharpe than buy-and-hold
        _result("X", "D3", resolved=149),
    ]
    assert [tl.is_candidate(r) for r in results] == [True, True, True, False, False]
    assert [c["loop"] for c in tl.select_candidates(results)] == ["D1_55_20", "D2_EMA50"]
    assert tl.select_candidates([_result(diff=FLAT)]) == []


def test_confirmation_verdicts():
    assert tl.verdict(_result(), []) == "PASS"
    assert tl.verdict(_result(diff=FLAT), []) == "ZAMANLAMA_YOK"
    assert tl.verdict(_result(net=FLAT, diff=FLAT), []) == "RISK_AZALTIR"
    assert tl.verdict(_result(net=FLAT, diff=FLAT, book=_book(dd=-50.0)), []) == "NO_EFFECT"   # DD not halved
    assert tl.verdict(_result(net=FLAT, diff=FLAT, book=_book(h=(1.0, 0.5))), []) == "NO_EFFECT"
    assert tl.verdict(_result(stress=_s(-0.1, None, 0, 0)), []) == "ZAMANLAMA_YOK"
    assert tl.verdict(_result(), ["unknown trades 6.0%"]) == "INCOMPLETE_DATA"


def test_completeness_fails_closed():
    assert tl.incomplete({"trades": 0, "unknown_trade_share": 1.0, "unknown_day_share": 1.0,
                          "months_missing_fixed": 0})
    ok = {"trades": 10, "unknown_trade_share": 0.0, "unknown_day_share": 0.0, "months_missing_fixed": 0}
    assert tl.incomplete(ok) == []
    assert tl.incomplete({**ok, "months_missing_fixed": 1}) == ["1 months without BTC and ETH"]


def test_confirmation_stays_sealed_without_a_registered_loop(tmp_path):
    if tl.REGISTERED:
        pytest.skip("a loop is registered; the tripwire test covers it")
    with pytest.raises(SystemExit):
        tl.require_registration(tmp_path / "registry.jsonl")
    for argv in (["build-confirm", "--out", str(tmp_path / "data")],
                 ["confirm", "--spot-dir", str(tmp_path), "--out", str(tmp_path / "o")]):
        with pytest.raises(SystemExit):
            tl._cli(argv + ["--trial-registry", str(tmp_path / "none.jsonl")])
    assert not (tmp_path / "data").exists() and not (tmp_path / "o").exists()


# ---------------------------------------------------------------------------
# Pre-history test (docs/TRADE_LOOP_STUDY.md)
# ---------------------------------------------------------------------------


def test_volatility_target_cuts_the_slot_of_a_volatile_coin_and_never_raises_it():
    calm = _breakout_path([110.0] * 2 + [108.0, 106.0, 104.0, 102.0, 100.0, 99.0, 98.0, 97.0])
    market = _market({"AUSDT": _rows(calm, wick=0.002)})
    daily = tl.daily_bars(market)
    ind, breaks = tl.indicators(daily), tl.wm.continuity_breaks(market)
    rows, _ = tl.run_loop(market, daily, ind, D1, {0: [(d, T0) for d in range(25, daily.n_days)]}, breaks)
    window = (T0 + DAY_S, T0 + daily.n_days * DAY_S)
    plain = tl.portfolio(market, daily, rows, {T0: ["AUSDT"]}, window, breaks, half_at=T0 + 40 * DAY_S)
    wild = [dict(r, vol_30d=1.0) for r in rows]                 # 100% annualised: half the slot
    tamed = tl.portfolio(market, daily, wild, {T0: ["AUSDT"]}, window, breaks, half_at=T0 + 40 * DAY_S,
                         vol_target=0.5)
    assert tamed["time_in_market"] == pytest.approx(plain["time_in_market"] / 2)
    quiet = [dict(r, vol_30d=0.1) for r in rows]                # calmer than the target: still one full slot
    same = tl.portfolio(market, daily, quiet, {T0: ["AUSDT"]}, window, breaks, half_at=T0 + 40 * DAY_S,
                        vol_target=0.5)
    assert same["time_in_market"] == pytest.approx(plain["time_in_market"])
    unknown = tl.portfolio(market, daily, [dict(r, vol_30d=None) for r in rows], {T0: ["AUSDT"]}, window, breaks,
                           half_at=T0 + 40 * DAY_S, vol_target=0.5)
    assert unknown["unsized_trades"] == len(rows) and unknown["time_in_market"] == 0


def test_the_30_day_volatility_uses_closes_up_to_the_decision_only():
    prices = [100.0 * (1.02 if k % 2 else 0.98) for k in range(60)]
    _, daily, ind, _ = _one(prices)
    later = prices[:40] + [p * 3 for p in prices[40:]]
    _, daily2, ind2, _ = _one(later)
    assert ind.vol30[0, 39] == pytest.approx(ind2.vol30[0, 39]) and ind.vol30[0, 39] > 0


def test_sharpe_difference_ci_brackets_a_known_gap():
    rng = np.random.default_rng(1)
    base = rng.normal(0.0, 0.02, 800)
    better = base + 0.004
    lo, hi = tl.sharpe_difference_ci(better, base, alpha=0.05)
    assert 0 < lo < hi
    assert tl.sharpe_difference_ci(better[:50], base[:50], alpha=0.05) is None


def test_the_spot_only_universe_drops_the_perpetual_condition_only():
    from trading.backtest import majors_signals as ms

    series = {f"C{k:02d}USDT": _rows([100.0] * 130) for k in range(13)}
    series.update(BTCUSDT=_rows([100.0] * 130), ETHUSDT=_rows([100.0] * 130))
    market = _market(series)
    month = T0 + 120 * DAY_S
    kw = dict(first_trade=ms.first_trade_times(market), breaks=ms.wm.continuity_breaks(market))
    assert ms.monthly_universe(market, month, perp_of={}, funding_times={}, **kw) == []
    spot = ms.monthly_universe(market, month, perp_of={}, funding_times={}, require_perp=False, **kw)
    assert spot[:2] == ["BTCUSDT", "ETHUSDT"] and len(spot) == 12


def test_the_pre_history_test_refuses_to_run_unregistered(tmp_path):
    with pytest.raises(SystemExit):
        tl._cli(["prehistory", "--spot-dir", str(tmp_path), "--out", str(tmp_path / "o"),
                 "--trial-registry", str(tmp_path / "none.jsonl")])
    assert not (tmp_path / "o").exists()
    assert tl.prehistory_trial_params()["chosen_after_discovery"] is True


def test_unsized_trades_count_as_unknown_in_the_completeness_gate():
    rows = [{"status": "RESOLVED"}] * 19
    stats = tl.completeness(rows, None, None, {}, {T0: ["BTCUSDT", "ETHUSDT"]}, {}, unsized=2)
    assert stats["unknown_trade_share"] == pytest.approx(2 / 19)
    assert "unknown trades 10.5%" in tl.incomplete({**stats, "unknown_day_share": 0.0})


def test_the_dataset_manifest_must_be_the_complete_expected_build(tmp_path):
    import json

    (tmp_path / "15m").mkdir()
    for s in ("BTCUSDT", "ETHUSDT"):
        (tmp_path / "15m" / f"{s}.parquet").write_bytes(b"x")
    good = {"schema": "liquid-universe/v1", "data_end": 1601510399, "window": ["2017-09", "2020-09"],
            "candidates": 2, "downloaded": 2, "symbols": {"BTCUSDT": {}, "ETHUSDT": {}}}
    (tmp_path / "manifest.json").write_text(json.dumps(good))
    assert tl.verify_universe_manifest(tmp_path, end=1601510400, window=tl.PREHISTORY_DATA)["candidates"] == 2
    assert tl.data_window(tl.sq.DISCOVERY_END, tl.sq.DISCOVERY_MONTHS) == ("2020-10", "2024-08")
    assert tl.data_window(tl.sq.CONFIRMATION_END, tl.sq.CONFIRMATION_MONTHS) == ("2024-06", "2026-08")
    for bad in ({"downloaded": 1}, {"data_end": 1601510000}, {"window": ["2018-01", "2020-09"]},
                {"schema": "other"}, {"symbols": {"BTCUSDT": {}, "XRPUSDT": {}}}):
        (tmp_path / "manifest.json").write_text(json.dumps({**good, **bad}))
        with pytest.raises(SystemExit):
            tl.verify_universe_manifest(tmp_path, end=1601510400, window=tl.PREHISTORY_DATA)
