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
    rows = tl.run_loop(market, daily, ind, D1, days, breaks, seed=7)
    assert len(rows) == 2 and rows[1]["entry_day"] > rows[0]["exit_day"]          # one position at a time, then again
    assert all(r["random_draws"] <= tl.RANDOM_DRAWS for r in rows)
    again = tl.run_loop(market, daily, ind, D1, days, breaks, seed=7)
    assert [r["random_mean_net_pct"] for r in rows] == [r["random_mean_net_pct"] for r in again]


def test_portfolio_charges_costs_and_compares_with_buy_and_hold():
    prices = _breakout_path([110.0] * 2 + [108.0, 106.0, 104.0, 102.0, 100.0, 99.0, 98.0, 97.0])
    market = _market({"AUSDT": _rows(prices, wick=0.002)})
    daily = tl.daily_bars(market)
    ind, breaks = tl.indicators(daily), tl.wm.continuity_breaks(market)
    days = {0: [(d, T0) for d in range(25, daily.n_days)]}
    rows = tl.run_loop(market, daily, ind, D1, days, breaks)
    assert len(rows) == 1
    window = (T0 + DAY_S, T0 + daily.n_days * DAY_S)
    book = tl.portfolio(market, daily, rows, {T0: ["AUSDT"]}, window, breaks, half_at=T0 + 40 * DAY_S)
    assert book["loop"]["total_return_pct"] == pytest.approx(rows[0]["net_pct"], abs=0.05)
    assert 0 < book["time_in_market"] < 1
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
