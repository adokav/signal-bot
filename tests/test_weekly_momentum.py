from __future__ import annotations

import math

import numpy as np
import pytest

pytest.importorskip("pandas")

from trading.backtest import liquid_replay as lr
from trading.backtest import weekly_momentum as wm

MONDAY = 1_704_067_200          # 2024-01-01T00:00:00Z, a Monday
WEEKS = 10
BARS = WEEKS * wm.WEEK_BARS


def _rows(growth, *, start=MONDAY, bars=BARS, price=10.0):
    rows, p = [], price
    for i in range(bars):
        o, c = p, p * math.exp(growth)
        rows.append((start + i * 900, start + i * 900 + 899, o, max(o, c), min(o, c), c, 1e6))
        p = c
    return rows


def _market(overrides=None, n=6):
    rows = {f"C{k}USDT": _rows(0.00002 * k) for k in range(n)}   # C5 rises fastest, every week
    rows.update(overrides or {})
    return lr.build_market({s: lr.rows_to_columns(r) for s, r in rows.items()})


@pytest.fixture(autouse=True)
def small_universe(monkeypatch):
    monkeypatch.setattr(wm, "MIN_RANKED", 3)


def test_monday_steps_close_exactly_at_monday_midnight():
    market = _market()
    steps = wm.monday_steps(market, (0, 2_000_000_000))
    assert len(steps) == WEEKS                      # bars open on Monday; their closes land on the next ten Mondays
    assert all((int(market.grid_open[i]) + 900 - MONDAY) % wm.WEEK == 0 for i in steps)
    assert steps[1] - steps[0] == wm.WEEK_BARS


def test_prices_freeze_on_delisting_but_holes_stay_unknown():
    full = _rows(0.0)
    delisted = _market({"C0USDT": full[:3000]})
    s = delisted.index_of["C0USDT"]
    delisted.coverage_end[s] = BARS - 1
    assert wm.price_at(delisted, s, 5000) == pytest.approx(full[2999][5])
    holed = _market({"C0USDT": full[:3000] + full[4000:]})
    assert wm.price_at(holed, holed.index_of["C0USDT"], 3500) is None
    edge = _market({"C0USDT": full[:3000]})       # data ends at the download edge, not a delisting
    assert wm.price_at(edge, edge.index_of["C0USDT"], 5000) is None


def test_a_token_swap_gap_is_a_break_not_a_return():
    """COCOS 2021-01 / SUN 2021-06: trading halts for days, then resumes at a price 1000x off."""

    flat = _rows(0.0)
    halt, resume = 2 * wm.WEEK_BARS + 100, 2 * wm.WEEK_BARS + 100 + 4 * 96
    swapped = [(o, c, op * 1000, hi * 1000, lo * 1000, cl * 1000, v) for o, c, op, hi, lo, cl, v in flat[resume:]]
    market = _market({"C5USDT": flat[:halt] + swapped})
    s = market.index_of["C5USDT"]
    breaks = wm.continuity_breaks(market)
    assert list(breaks) == [s] and int(breaks[s][0]) == resume
    steps = wm.monday_steps(market, (0, 2_000_000_000))
    universes = {i: list(range(len(market.symbols))) for i in steps}
    rows, _ = wm.weekly_series(market, steps, universes, lookback_w=1, hold_w=4)
    assert all(abs(r.portfolio_pct) < 50 and abs(r.benchmark_pct) < 50 for r in rows)
    assert sum(r.unknown_member_weeks for r in rows) > 0              # counted, never a 1000x return
    after = next(i for i in steps if i > resume)
    assert s not in [x for _, x in wm.rank(market, universes[after], after, lookback_w=1)]
    later = next(i for i in steps if i - wm.WEEK_BARS > resume)
    assert s in [x for _, x in wm.rank(market, universes[later], later, lookback_w=1)]


def test_a_maintenance_hole_of_a_few_hours_is_not_a_break():
    full = _rows(0.0)
    market = _market({"C0USDT": full[:1000] + full[1020:]})          # 5 hours missing, same prices after
    assert wm.continuity_breaks(market) == {}
    steps = wm.monday_steps(market, (0, 2_000_000_000))
    universes = {i: list(range(len(market.symbols))) for i in steps}
    rows, _ = wm.weekly_series(market, steps, universes, lookback_w=1, hold_w=1)
    assert sum(r.unknown_member_weeks for r in rows) == 0


def test_ranking_uses_only_closed_history_and_needs_enough_names():
    market = _market()
    steps = wm.monday_steps(market, (0, 2_000_000_000))
    members = list(range(len(market.symbols)))
    ranked = wm.rank(market, members, steps[2], lookback_w=1)
    assert [market.symbols[s] for _, s in ranked][:2] == ["C5USDT", "C4USDT"]
    assert wm.rank(market, members, steps[0], lookback_w=4) is None      # no 4-week history yet
    top, bottom = wm.top_bottom(ranked)
    assert len(top) == 1 and market.symbols[top[0]] == "C5USDT" and market.symbols[bottom[0]] == "C0USDT"


def test_persistent_winners_beat_the_basket_and_costs_are_charged_per_cohort():
    market = _market()
    steps = wm.monday_steps(market, (0, 2_000_000_000))
    universes = {i: list(range(len(market.symbols))) for i in steps}
    rows1, skipped = wm.weekly_series(market, steps, universes, lookback_w=1, hold_w=1)
    assert skipped == 1                             # the first Monday has no week of history to rank on
    assert rows1 and all(r.portfolio_pct > r.benchmark_pct for r in rows1)
    assert all(r.cost_pct == pytest.approx(wm.COST_PCT) for r in rows1)
    rows4, _ = wm.weekly_series(market, steps, universes, lookback_w=1, hold_w=4)
    assert rows4[-1].cohorts == 4 and rows4[-1].cost_pct == pytest.approx(wm.COST_PCT / 4)
    assert all(r.unknown_member_weeks == 0 for r in rows4)


def test_future_prices_do_not_change_who_is_bought():
    market = _market()
    steps = wm.monday_steps(market, (0, 2_000_000_000))
    members = list(range(len(market.symbols)))
    before = wm.rank(market, members, steps[3], lookback_w=2)
    market.close[:, steps[3] + 1:] *= np.linspace(0.5, 2.0, len(market.symbols))[:, None]
    assert wm.rank(market, members, steps[3], lookback_w=2) == before


def test_verdicts_follow_the_pre_registered_rules():
    def s(mean, ci, h1, h2, weeks=104):
        return {"weeks": weeks, "mean": mean, "ci": ci, "h1": h1, "h2": h2}

    good, bad = s(0.5, (0.1, 0.9), 0.4, 0.6), s(-0.5, (-0.9, -0.1), -0.4, -0.6)
    flat = s(0.0, (-0.4, 0.4), 0.1, -0.1)
    ok = dict(unknown_share=0.0, skipped_share=0.0)
    assert wm.verdict(good, good, s(0.1, None, 0, 0), **ok) == "PASS"
    assert wm.verdict(flat, good, s(-0.1, None, 0, 0), **ok) == "SEPETI_YENER"
    assert wm.verdict(good, good, s(-0.1, None, 0, 0), **ok) == "SEPETI_YENER"     # stress cost kills PASS
    assert wm.verdict(flat, bad, flat, **ok) == "TERS_DONUS"
    assert wm.verdict(flat, flat, flat, **ok) == "NO_EFFECT"
    assert wm.verdict(good, good, good, unknown_share=0.03, skipped_share=0.0) == "INCOMPLETE_DATA"
    assert wm.verdict(good, good, good, unknown_share=0.0, skipped_share=0.06) == "INCOMPLETE_DATA"
    assert wm.verdict(s(0.5, (0.1, 0.9), 0.4, 0.6, weeks=79), good, good, **ok) == "SEPETI_YENER"


def test_block_ci_needs_enough_weeks_and_covers_the_mean():
    assert wm.block_ci([1.0] * 7, alpha=0.05) is None
    lo, hi = wm.block_ci([float(k % 5) for k in range(100)], alpha=0.05)
    assert lo < 2.0 < hi


def test_confirmation_stays_sealed_without_a_registered_variant(tmp_path):
    """Discovery found no effect, so nothing is registered: no confirmation data is fetched or read."""

    assert wm.REGISTERED_VARIANTS == ()
    with pytest.raises(SystemExit):
        wm.require_registration(tmp_path / "registry.jsonl")
    for mode in (["build-confirm"], ["confirm", "--data-dir", str(tmp_path)]):
        with pytest.raises(SystemExit):
            wm._cli(mode + ["--out", str(tmp_path / "out"), "--trial-registry", str(tmp_path / "none.jsonl")])
    assert not (tmp_path / "out").exists()
