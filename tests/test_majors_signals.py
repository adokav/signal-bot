from __future__ import annotations

import math
from datetime import date

import numpy as np
import pytest

pytest.importorskip("pandas")

from trading.backtest import liquid_replay as lr
from trading.backtest import majors_signals as ms
from trading.data import majors_data as md

T0 = 1_704_067_200            # 2024-01-01T00:00:00Z
H = 3600
D = 86_400


# ---------------------------------------------------------------------------
# Parsing (majors_data)
# ---------------------------------------------------------------------------


def _kline(open_s, *, o=10.0, h=11.0, low=9.0, c=10.5, quote=1000.0, taker=600.0, interval=H):
    return ",".join(str(x) for x in (open_s * 1000, o, h, low, c, 1.0, (open_s + interval) * 1000 - 1,
                                     quote, 5, 0.5, taker, 0))


def test_taker_klines_keep_closed_valid_rows_and_reject_the_rest():
    text = "\n".join([
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore",
        _kline(T0),
        _kline(T0 + H, taker=1200.0),                  # taker buy above total volume
        _kline(T0 + 2 * H, h=9.5),                     # high below close
        _kline(T0 + 3 * H, c=float("nan")),
        _kline(T0 + 4 * H, interval=2 * H),            # not a 1h candle
        _kline(T0 + 5 * H),                            # closes after decision_at: still forming
    ])
    rows, rejected = md.parse_taker_klines(text, decision_at=T0 + 5 * H)
    assert rows == [(T0, 10.5, 1000.0, 600.0)]
    assert rejected == 4


def test_metrics_keep_snapshots_created_by_the_decision_time():
    text = "\n".join([
        "create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio",
        '2024-01-01 00:00:00,BTCUSDT,100.5,1.0,""',
        "2024-01-01 00:05:00,BTCUSDT,0,1.0,",           # non-positive OI
        "2024-01-01 00:10:00,BTCUSDT,nan,1.0,",
        "2024-01-01 00:15:00,BTCUSDT,101.0,1.0,",        # after decision_at
    ])
    rows, rejected = md.parse_metrics(text, decision_at=T0 + 600)
    assert rows == [(T0, 100.5)] and rejected == 2
    with pytest.raises(md.carry.DataQualityError):
        md.parse_metrics("time,oi\n1,2", decision_at=T0)


def test_a_daily_oi_file_is_authoritative_for_its_own_day_only():
    rows = [(T0 - 300, 1.0), (T0, 2.0), (T0 + D - 300, 3.0), (T0 + D, 4.0)]
    inside, outside = md.own_day(rows, date(2024, 1, 1))
    assert inside == [(T0, 2.0), (T0 + D - 300, 3.0)] and outside == 2


def test_conflicting_oi_inside_a_day_still_fails_the_build(monkeypatch):
    files = {date(2024, 1, 1): "create_time,sum_open_interest\n2024-01-01 00:05:00,5\n",
             date(2024, 1, 2): "create_time,sum_open_interest\n2024-01-02 00:00:00,7\n2024-01-01 00:05:00,6\n"}
    monkeypatch.setattr(md.carry, "_csv", lambda session, url, what: files[date.fromisoformat(url[-14:-4])])
    rows, report = md.fetch_oi(None, "BTCUSDT", files, decision_at=T0 + 3 * D)
    assert rows == [(T0 + 300, 5.0), (T0 + D, 7.0)] and report["outside_day_rows"] == 1
    files[date(2024, 1, 1)] += "2024-01-01 00:05:00,9\n"            # the day's own file contradicts itself
    with pytest.raises(md.carry.DataQualityError):
        md.fetch_oi(None, "BTCUSDT", files, decision_at=T0 + 3 * D)


def test_download_plan_covers_lookbacks_and_starts_oi_at_its_coverage():
    feb, mar = 1_706_745_600, 1_709_251_200          # 2024-02-01, 2024-03-01
    months, days = md.plan_downloads({feb: ["BTCUSDT"]}, oi_from=feb + 10 * D, month_end={feb: mar})
    assert months == {"BTCUSDT": {"2024-01", "2024-02"}}
    assert min(days["BTCUSDT"]) == date(2024, 2, 9) and max(days["BTCUSDT"]) == date(2024, 2, 28)


def _fake_downloads(monkeypatch, taker_value=600.0):
    monkeypatch.setattr(md, "fetch_taker", lambda session, perp, months, decision_at: (
        [(T0, 10.0, 1000.0, taker_value)], {"missing_months": [], "rejected_rows": 0}))
    monkeypatch.setattr(md, "fetch_oi", lambda session, perp, days, decision_at: (
        [(T0, 5.0)], {"missing_days": 0, "rejected_rows": 0, "outside_day_rows": 0}))


def test_perp_data_is_one_verifiable_generation(tmp_path, monkeypatch):
    feb, mar = 1_706_745_600, 1_709_251_200
    args = ({feb: ["BTCUSDT"]}, {feb: mar}, {"BTCUSDT": "BTCUSDT"}, tmp_path)
    _fake_downloads(monkeypatch)
    md.build_perp_data(*args, decision_at=mar, oi_from=feb, session=object())
    assert md.verify_generation(tmp_path, decision_at=mar)["universes"] == {str(feb): ["BTCUSDT"]}
    with pytest.raises(md.carry.DataQualityError):
        md.verify_generation(tmp_path, decision_at=mar + 1)                 # built for another cut-off
    kept = (tmp_path / md.TAKER_FILE).read_bytes()
    _fake_downloads(monkeypatch, taker_value=700.0)
    md.build_perp_data(*args, decision_at=mar, oi_from=feb, session=object())
    (tmp_path / md.OI_FILE).write_bytes((tmp_path / md.OI_FILE).read_bytes() + b"\n")  # an OI file from elsewhere
    with pytest.raises(md.carry.DataQualityError):
        md.verify_generation(tmp_path, decision_at=mar)
    (tmp_path / md.MANIFEST_FILE).unlink()                                   # interrupted before the manifest
    (tmp_path / md.TAKER_FILE).write_bytes(kept)
    with pytest.raises(md.carry.DataQualityError):
        md.verify_generation(tmp_path, decision_at=mar)


# ---------------------------------------------------------------------------
# Features: point in time
# ---------------------------------------------------------------------------


def test_funding_uses_only_payments_settled_before_the_decision():
    t = T0 + 10 * D
    times8 = [t - k * 8 * H for k in range(0, 12)][::-1]
    rates = [0.0001] * len(times8)
    rates[-1] = 0.05                                       # paid exactly at t: not visible yet
    assert ms.funding_feature(times8, rates, t) == pytest.approx(0.0001)
    times4 = [t - k * 4 * H for k in range(1, 25)][::-1]
    assert ms.funding_feature(times4, [0.0001] * 24, t) == pytest.approx(0.0002)   # per-8h equivalent
    holed = [x for x in times8 if not (t - 40 * H <= x <= t - 24 * H)]
    assert ms.funding_feature(holed, [0.0001] * len(holed), t) is None                 # > 8h without a payment
    assert ms.funding_group(None) is None and ms.funding_group(-1e-6) == "F_NEGATIVE"
    assert ms.funding_group(0.0003) == "F_HIGH" and ms.funding_group(0.0001) == "F_BASE"


def _flow(days=40, share=0.5, last_day_share=None, start=T0):
    times = np.arange(start, start + days * D, H, dtype=np.int64)
    quote = np.full(len(times), 100.0)
    taker = np.full(len(times), 100.0 * share)
    taker += np.where((times // D) % 2 == 0, 2.0, -2.0)            # day-to-day variation
    if last_day_share is not None:
        taker[-24:] = 100.0 * last_day_share
    return times, quote, taker


def test_flow_z_needs_24_closed_candles_and_ignores_the_future():
    times, quote, taker = _flow(last_day_share=0.9)
    t = int(times[-1]) + H
    flow = ms.Flow(times, quote, taker)
    assert flow.share(t) == pytest.approx(0.9)
    assert flow.z(t) > 5 and ms.flow_group(flow.z(t)) == "T_BUYING"
    earlier = ms.Flow(times, quote, taker).z(t - D)
    with_future = ms.Flow(*(np.concatenate((a, b)) for a, b in zip(
        (times, quote, taker), (times[-24:] + D, quote[-24:], taker[-24:] * 0.0))))
    assert with_future.z(t - D) == pytest.approx(earlier)        # later candles do not move an earlier z
    gap = np.ones(len(times), bool)
    gap[-5] = False
    assert ms.Flow(times[gap], quote[gap], taker[gap]).share(t) is None
    assert ms.Flow(*_flow(days=10)).z(T0 + 10 * D) is None       # fewer than 20 baseline days


def test_oi_snapshot_lags_five_minutes_and_expires_after_an_hour():
    times = np.array([T0 - 600, T0 - 300, T0], dtype=np.int64)
    values = np.array([1.0, 2.0, 3.0])
    assert ms.oi_snapshot(times, values, T0) == 2.0                 # the 00:00 snapshot is too fresh
    assert ms.oi_snapshot(times, values, T0 + 2 * H) is None        # stale
    assert ms.oi_group(0.1, 0.02) == "O_LONGS_BUILDING" and ms.oi_group(-0.1, 0.02) == "O_SHORT_COVERING"
    assert ms.oi_group(0.1, -0.02) == "O_SHORTS_BUILDING" and ms.oi_group(-0.1, -0.02) == "O_LONG_LIQUIDATION"
    assert ms.oi_group(None, 0.02) is None and ms.oi_group(0.0, 0.02) is None


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------


def _rows(start, bars, *, price=10.0, qv=1e6, growth=0.0):
    out, p = [], price
    for k in range(bars):
        o, c = p, p * math.exp(growth)
        out.append((start + k * 900, start + k * 900 + 899, o, max(o, c), min(o, c), c, qv))
        p = c
    return out


MONTH = T0 + 120 * D
BARS = 125 * 96


def _universe_market(extra=None):
    volumes = {"BTCUSDT": 5e6, "ETHUSDT": 4e6, **{f"A{k:02d}USDT": 3e6 - k * 1e5 for k in range(11)},
               "PEPEUSDT": 1e5, "NOPERPUSDT": 9e6}
    series = {s: _rows(T0, BARS, qv=v) for s, v in volumes.items()}
    series["NEWUSDT"] = _rows(T0 + 100 * D, BARS - 100 * 96, qv=9e6)       # listed 20 days before the month
    series.update(extra or {})
    market = lr.build_market({s: lr.rows_to_columns(r) for s, r in series.items()})
    perp_of = {s: s for s in series if s != "NOPERPUSDT"}
    funding_times = {s: np.arange(T0, MONTH, 8 * H, dtype=np.int64) for s in perp_of}
    return market, perp_of, funding_times


def _members(market, perp_of, funding_times):
    return ms.monthly_universe(market, MONTH, perp_of=perp_of, funding_times=funding_times,
                               first_trade=ms.first_trade_times(market), breaks=ms.wm.continuity_breaks(market))


def test_universe_is_btc_eth_top_ten_seasoned_with_a_perp_and_one_meme():
    market, perp_of, funding_times = _universe_market()
    members = _members(market, perp_of, funding_times)
    assert members[:2] == ["BTCUSDT", "ETHUSDT"]
    assert members[2:12] == [f"A{k:02d}USDT" for k in range(10)]           # A10 is 11th: out
    assert members[12:] == ["PEPEUSDT"]                                     # no meme in the top 10
    assert "NOPERPUSDT" not in members and "NEWUSDT" not in members


def test_universe_ignores_volume_after_the_month_start():
    late_pump = _rows(T0, BARS, qv=1e3)
    late_pump = [r[:6] + ((9e9,) if r[0] >= MONTH else (1e3,)) for r in late_pump]
    market, perp_of, funding_times = _universe_market({"A10USDT": late_pump})
    members = _members(market, perp_of, funding_times)
    assert "A10USDT" not in members and "PEPEUSDT" in members               # its pump comes after the start
    market, perp_of, funding_times = _universe_market()
    young = dict(funding_times, A00USDT=np.arange(MONTH - 10 * D, MONTH, 8 * H, dtype=np.int64))
    assert "A00USDT" not in _members(market, perp_of, young)                # perp listed 10 days ago
    dead = dict(funding_times, A00USDT=np.arange(T0, MONTH - 5 * D, 8 * H, dtype=np.int64))
    assert "A00USDT" not in _members(market, perp_of, dead)                 # perp stopped paying 5 days ago
    assert "A10USDT" in _members(market, perp_of, dead)


def test_a_token_swap_relists_the_pair_for_seasoning():
    """LUNA 2.0 reused LUNAUSDT after a long halt: it is a new listing, not a seasoned major."""

    full = _rows(T0, BARS, qv=8e6)
    resume = 80 * 96                                                       # 40 days before the month start
    swapped = full[: 70 * 96] + [r[:2] + tuple(x * 1000 for x in r[2:6]) + r[6:] for r in full[resume:]]
    market, perp_of, funding_times = _universe_market({"A00USDT": swapped})
    assert "A00USDT" not in _members(market, perp_of, funding_times)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


def test_outcomes_start_at_the_next_open_and_a_swap_gap_is_unknown():
    flat = _rows(T0, 20 * 96)
    i = 5 * 96 - 1                                     # decision at T0 + 5 days
    jump = flat[: i + 1] + [r[:2] + tuple(x * 2 for x in r[2:6]) + r[6:] for r in flat[i + 1:]]
    halt, resume = i + 10, i + 10 + 2 * 96
    swapped = flat[:halt] + [r[:2] + tuple(x * 1000 for x in r[2:6]) + r[6:] for r in flat[resume:]]
    series = {"JUMPUSDT": jump, "SWAPUSDT": swapped, **{f"F{k}USDT": flat for k in range(8)}}
    market = lr.build_market({s: lr.rows_to_columns(r) for s, r in series.items()})
    index = [market.index_of[s] for s in series]
    returns, basket = ms.outcomes(market, index, i, 24, ms.wm.continuity_breaks(market))
    assert returns[0] == pytest.approx(0.0)            # bought at the open after the gap-up, not before it
    assert math.isnan(returns[1])                      # never a 1000x return
    assert basket == pytest.approx(0.0)
    _, thin = ms.outcomes(market, index[:5], i, 24, ms.wm.continuity_breaks(market))
    assert thin is None                                # fewer than BASKET_MIN known members


def test_a_pair_resuming_on_the_entry_bar_is_not_an_outcome():
    flat = _rows(T0, 20 * 96)
    i = 5 * 96 - 1
    resumed = flat[: i - 2 * 96] + [r[:2] + tuple(x * 1000 for x in r[2:6]) + r[6:] for r in flat[i + 1:]]
    series = {"BACKUSDT": resumed, **{f"F{k}USDT": flat for k in range(8)}}
    market = lr.build_market({s: lr.rows_to_columns(r) for s, r in series.items()})
    breaks = ms.wm.continuity_breaks(market)
    assert int(breaks[market.index_of["BACKUSDT"]][0]) == i + 1          # trading resumes at the entry bar
    returns, _ = ms.outcomes(market, [market.index_of[s] for s in series], i, 24, breaks)
    assert math.isnan(returns[0])


# ---------------------------------------------------------------------------
# Candidate selection and verdicts (rules fixed before results)
# ---------------------------------------------------------------------------


def _s(mean, ci, h1, h2, n=1000):
    return {"n": n, "mean": mean, "ci": ci, "h1": h1, "h2": h2}


GOOD, BAD, FLAT = _s(0.3, (0.1, 0.5), 0.2, 0.4), _s(-0.3, (-0.5, -0.1), -0.2, -0.4), _s(0.0, (-0.2, 0.2), 0.1, -0.1)


def _result(family, group, h, *, net=GOOD, exc=GOOD, rest=FLAT, stress=GOOD):
    return {"family": family, "group": group, "hours": h, "net": net, "excess": exc, "stress_net": stress,
            "minus_rest_excess": rest}


def test_candidate_rule_one_per_family_at_most_two():
    results = [
        _result("F", "F_HIGH", 72, net=FLAT, exc=FLAT, rest=_s(-0.5, (-0.9, -0.2), -0.4, -0.6)),
        _result("F", "F_NEGATIVE", 24),
        _result("T", "T_BUYING", 24, exc=_s(0.4, (0.25, 0.6), 0.3, 0.5)),
        _result("O", "O_LONG_LIQUIDATION", 72, exc=_s(0.2, (0.05, 0.4), 0.1, 0.3)),
        _result("O", "O_SHORTS_BUILDING", 24, net=_s(0.3, (0.1, 0.5), 0.2, 0.4, n=299)),   # too few
        _result("T", "T_SELLING", 72, exc=_s(0.3, (0.1, 0.5), -0.1, 0.6)),                 # a half fails
    ]
    assert [ms.candidate_kind(r) for r in results] == ["AVOID", "LONG", "LONG", "LONG", None, None]
    chosen = ms.select_candidates(results)
    assert [(c["family"], c["group"], c["kind"]) for c in chosen] == [("T", "T_BUYING", "LONG"),     # bound 0.25
                                                                     ("F", "F_HIGH", "AVOID")]    # bound 0.20
    assert ms.select_candidates([_result("F", "F_BASE", 24, exc=FLAT)]) == []


def test_confirmation_verdicts_follow_the_pre_registered_rules():
    assert ms.confirm_verdict(_result("T", "T_BUYING", 24), "LONG", []) == "PASS"
    assert ms.confirm_verdict(_result("T", "T_BUYING", 24, stress=_s(-0.1, None, 0, 0)), "LONG", []) == "SEPETI_YENER"
    assert ms.confirm_verdict(_result("T", "T_BUYING", 24, net=FLAT), "LONG", []) == "SEPETI_YENER"
    assert ms.confirm_verdict(_result("T", "T_BUYING", 24, net=FLAT, exc=BAD), "LONG", []) == "TERS"
    assert ms.confirm_verdict(_result("T", "T_BUYING", 24, net=FLAT, exc=FLAT), "LONG", []) == "NO_EFFECT"
    assert ms.confirm_verdict(_result("T", "T_BUYING", 24, net=_s(0.3, (0.1, 0.5), 0.2, 0.4, n=199)),
                              "LONG", []) == "SEPETI_YENER"
    assert ms.confirm_verdict(_result("F", "F_HIGH", 72, rest=BAD), "AVOID", []) == "KAYBI_AZALTIR"
    assert ms.confirm_verdict(_result("F", "F_HIGH", 72, rest=FLAT), "AVOID", []) == "NO_EFFECT"
    assert ms.confirm_verdict(_result("T", "T_BUYING", 24), "LONG", ["unknown T 6%"]) == "INCOMPLETE_DATA"


def test_completeness_fails_closed():
    empty = ms.completeness([], {})
    assert ms.incomplete(empty, "F", 24)                                   # nothing measured is not "complete"
    row = {"F": None, "T": "T_NEUTRAL", "O": None, "in_O": False, "gross_24": 1.0, "basket_24": 0.5,
           "gross_72": None, "basket_72": None}
    stats = ms.completeness([row], {T0: ["BTCUSDT", "ETHUSDT"] + [f"A{k:02d}USDT" for k in range(10)]})
    assert "unknown F 100.0%" in ms.incomplete(stats, "F", 24)
    assert ms.incomplete(stats, "T", 24) == []
    assert any(p.startswith("missing outcome") for p in ms.incomplete(stats, "T", 72))
    assert ms.completeness([row], {T0: ["BTCUSDT"] * 11})["short_month_share"] == 1.0
    no_eth = {T0: ["BTCUSDT"] + [f"A{k:02d}USDT" for k in range(12)]}            # 13 names, ETH missing
    stats = ms.completeness([row], no_eth)
    assert stats["short_month_share"] == 0.0 and "1 months without BTC and ETH" in ms.incomplete(stats, "T", 24)


# ---------------------------------------------------------------------------
# Seal
# ---------------------------------------------------------------------------


def test_confirmation_stays_sealed_without_a_registered_candidate(tmp_path):
    if ms.REGISTERED:
        pytest.skip("a candidate is registered; the tripwire test covers it")
    with pytest.raises(SystemExit):
        ms.require_registration(tmp_path / "registry.jsonl")
    for argv in (["build-confirm", "--out", str(tmp_path / "data")],
                 ["confirm", "--spot-dir", str(tmp_path), "--perp-dir", str(tmp_path), "--out", str(tmp_path / "o")]):
        with pytest.raises(SystemExit):
            ms._cli(argv + ["--trial-registry", str(tmp_path / "none.jsonl")])
    assert not (tmp_path / "data").exists() and not (tmp_path / "o").exists()
