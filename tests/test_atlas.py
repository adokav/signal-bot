from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

pytest.importorskip("pandas")

from trading.backtest import liquid_replay as lr
from trading.research import atlas


def test_fred_csv_parsing_skips_missing_values_in_both_header_styles():
    old = "DATE,DGS10\n2024-01-02,3.95\n2024-01-03,.\n2024-01-04,4.0\n"
    new = "observation_date,DGS10\n2024-01-02,3.95\n2024-01-03,\n"
    assert atlas.parse_fred_csv(old) == [(date(2024, 1, 2), 3.95), (date(2024, 1, 4), 4.0)]
    assert atlas.parse_fred_csv(new) == [(date(2024, 1, 2), 3.95)]
    with pytest.raises(ValueError):
        atlas.parse_fred_csv("")


def test_a_stale_value_is_missing_not_carried():
    rows = [(date(2024, 1, 10), 1.0), (date(2024, 3, 1), 2.0)]
    assert atlas.value_at(rows, date(2024, 1, 31)) == 1.0
    assert atlas.value_at(rows, date(2024, 2, 29)) is None                 # 50 days old
    assert atlas.value_at(rows, date(2023, 12, 31)) is None


def test_the_atlas_never_reaches_into_the_sealed_window():
    months = atlas.month_ends()
    assert months[0] == date(2017, 9, 30) and months[-1] == date(2024, 8, 31)
    assert all(m < date(2024, 9, 1) for m in months)


def test_macro_month_rows_net_liquidity_units_and_fed_moves():
    series = {
        "fed_assets_musd": [(date(2024, 1, 31), 7_600_000.0), (date(2024, 2, 28), 7_500_000.0)],
        "tga_musd": [(date(2024, 1, 31), 800_000.0), (date(2024, 2, 28), 750_000.0)],   # WTREGEN is in $mn
        "rrp_busd": [(date(2024, 1, 31), 600.0), (date(2024, 2, 29), 500.0)],
        "us_2y": [(date(2024, 1, 31), 4.3), (date(2024, 2, 29), 4.6)],
        "us_10y": [(date(2024, 1, 31), 4.0), (date(2024, 2, 29), 4.2)],
        "fed_upper": [(date(2023, 12, 31), 5.5), (date(2024, 1, 31), 5.5), (date(2024, 2, 15), 5.25),
                      (date(2024, 2, 29), 5.25)],
    }
    rows = atlas.macro_monthly(series, [date(2024, 1, 31), date(2024, 2, 29)])
    assert rows[0]["net_liquidity_busd"] == pytest.approx(7600 - 800 - 600)    # $bn: WALCL, WTREGEN in $mn
    assert rows[1]["net_liquidity_busd_chg"] == pytest.approx((7500 - 750 - 500) - 6200)
    assert rows[1]["curve_10y_2y"] == pytest.approx(-0.4)
    assert rows[0]["fed_move_bp"] == 0 and rows[1]["fed_move_bp"] == -25
    oil = atlas.macro_monthly({"brent": [(date(2024, 1, 31), 80.0), (date(2024, 2, 29), 88.0)],
                               "us_10y_real": [(date(2024, 1, 31), 1.8), (date(2024, 2, 29), 2.0)]},
                              [date(2024, 1, 31), date(2024, 2, 29)])
    assert oil[1]["brent_chg"] == pytest.approx(10.0)                       # prices change in %
    assert oil[1]["us_10y_real_chg"] == pytest.approx(0.2)                   # rates change in points
    assert {"DCOILBRENTEU", "DEXCHUS"} <= set(atlas.FRED_SERIES.values())
    assert "Fed -25 bp" in atlas.calendar_notes("2024-02", rows[1])
    assert "Bitcoin halving" in atlas.calendar_notes("2024-04", None)
    assert "(sonradan bilinen)" in atlas.calendar_notes("2022-11", None)


def _rows(prices, start, *, gap=None):
    out = []
    for d, p in enumerate(prices):
        if gap and gap[0] <= d < gap[1]:
            continue
        for k in range(96):
            t = start + (d * 96 + k) * 900
            out.append((t, t + 899, p, p * 1.01, p * 0.99, p, 1e6 if k else 5e6))
    return out


def test_monthly_market_rows_from_daily_candles():
    start = 1_704_067_200                                   # 2024-01-01
    days = 91                                               # January to March
    rng = np.random.default_rng(3)
    series = {}
    for k, name in enumerate(["BTCUSDT"] + [f"C{j}USDT" for j in range(9)]):
        steps = rng.normal(0.001 * (k + 1), 0.02, days)
        series[name] = _rows(list(100 * np.exp(np.cumsum(steps))), start)
    flat = [100.0] * 45 + [100_000.0] * 46                  # a 1000x swap after a 3-day hole
    series["SWAPUSDT"] = _rows(flat, start, gap=(42, 45))
    market = lr.build_market({s: lr.rows_to_columns(r) for s, r in series.items()})
    panel = atlas.build_panel([market], start=start, end=start + days * 86_400)
    members = list(series)
    rows = atlas.market_monthly(panel, {"2024-01": members, "2024-02": members, "2024-03": members})
    feb = rows[1]
    btc = panel.close[panel.index_of["BTCUSDT"]]
    assert feb["btc_ret_pct"] == pytest.approx((btc[59] / btc[30] - 1) * 100)
    assert feb["members"] == 11 and feb["basket_ret_pct"] is not None
    assert all(abs(r["basket_ret_pct"] or 0) < 100 for r in rows)            # the swap is never a return
    assert 0.0 <= feb["breadth_above_ema50"] <= 1.0 or feb["breadth_above_ema50"] is None
    assert feb["btc_volume_share"] == pytest.approx(1 / 11, rel=0.05)
    assert -1.0 <= feb["avg_correlation"] <= 1.0
    assert rows[0]["btc_ret_pct"] is None                                      # no previous month-end close


def test_phases_group_consecutive_trend_states():
    rows = [{"month": f"2024-0{k}", "btc_trend": s, "btc_ret_pct": 10.0, "calendar": ""}
            for k, s in enumerate(["YUKSELIS", "YUKSELIS", "DUSUS"], start=1)]
    out = atlas.phases(rows)
    assert [(p["state"], p["months"]) for p in out] == [("YUKSELIS", 2), ("DUSUS", 1)]
    assert out[0]["btc_ret_pct"] == pytest.approx(21.0)


def _market(series):
    return lr.build_market({s: lr.rows_to_columns(r) for s, r in series.items()})


def test_an_exchange_wide_halt_is_not_a_break_but_a_pair_hole_is():
    start = 1_517_443_200                                   # 2018-02-01
    halt = (7, 9)                                           # every pair stops for two days (2018-02-08..09)
    series = {"BTCUSDT": _rows([100.0] * 20, start, gap=halt), "ETHUSDT": _rows([10.0] * 20, start, gap=halt),
              "SWAPUSDT": _rows([1.0] * 20, start, gap=(12, 15))}
    market = _market(series)
    breaks = atlas.atlas_breaks(market)
    names = {market.symbols[s] for s in breaks}
    assert names == {"SWAPUSDT"}                            # the shared halt is the exchange, not a new token
    panel = atlas.build_panel([market], start=start, end=start + 20 * 86_400)
    assert not panel.resumed[panel.index_of["BTCUSDT"]].any()
    assert panel.resumed[panel.index_of["SWAPUSDT"], 15]
    no_btc = _market({"ETHUSDT": series["ETHUSDT"]})
    assert set(atlas.atlas_breaks(no_btc)) == {0}           # without BTC nothing can be told apart: fail closed


def test_the_daily_close_is_the_last_known_bar_and_an_empty_day_stays_unknown():
    start = 1_704_067_200

    def bar(day, k, price):
        t = start + (day * 96 + k) * 900
        return (t, t + 899, price, price, price, price, 1e6)

    rows = [bar(0, k, 100.0) for k in range(96)]
    rows.append(bar(1, 50, 105.0))                          # day 1: a single bar at 12:30
    rows += [bar(2, k, 120.0) for k in range(94)] + [bar(2, 94, 119.0)]   # day 2: no 23:45 bar
    rows += [bar(4, k, 130.0) for k in range(96)]           # day 3: nothing at all
    panel = atlas.build_panel([_market({"BTCUSDT": rows})], start=start, end=start + 5 * 86_400)
    assert panel.close[0, 0] == 100.0
    assert panel.close[0, 1] == 105.0                       # the day's last known bar, not NaN
    assert panel.close[0, 2] == 119.0                       # 23:30 closes a day whose 23:45 bar is missing
    assert math.isnan(panel.close[0, 3]) and math.isnan(panel.quote_volume[0, 3])


def test_a_gap_at_the_dataset_junction_is_a_resume_unless_btc_shares_it():
    start = 1_704_067_200
    split = start + 10 * 86_400
    old = _market({"BTCUSDT": _rows([100.0] * 10, start), "AUSDT": _rows([1.0] * 10, start, gap=(7, 10)),
                   "BUSDT": _rows([2.0] * 10, start)})
    new = _market({"BTCUSDT": _rows([100.0] * 10, split), "AUSDT": _rows([1000.0] * 10, split),
                   "BUSDT": _rows([2.0] * 10, split)})
    panel = atlas.build_panel([old, new], start=start, end=start + 20 * 86_400)
    assert panel.resumed[panel.index_of["AUSDT"], 10]       # a 3-day hole across the junction: maybe a new token
    assert not panel.resumed[panel.index_of["BUSDT"]].any()
    assert not panel.resumed[panel.index_of["BTCUSDT"]].any()


def test_the_trend_average_restarts_after_a_resume_and_needs_enough_known_closes():
    close = np.array([[100.0] * 60 + [1.0] * 60])
    resumed = np.zeros_like(close, dtype=bool)
    resumed[0, 60] = True
    ema = atlas._ema(close, resumed, 50)
    assert np.isnan(ema[0, :49]).all() and ema[0, 59] == pytest.approx(100.0)
    assert np.isnan(ema[0, 60:109]).all()                   # nothing from the old token is carried
    assert ema[0, 119] == pytest.approx(1.0)
    sparse = close.copy()
    sparse[0, 70:80] = np.nan                               # 10 of the last 50 unknown: below 90%
    assert np.isnan(atlas._ema(sparse, resumed, 50)[0, 119])


def _dataset(root, name, window, data_end, *, files=True, downloaded=None):
    import json

    d = root / name
    (d / "15m").mkdir(parents=True)
    symbols = {"BTCUSDT": {}, "ETHUSDT": {}}
    (d / "manifest.json").write_text(json.dumps({
        "schema": "liquid-universe/v1", "window": list(window), "data_end": data_end, "symbols": symbols,
        "candidates": len(symbols), "downloaded": len(symbols) if downloaded is None else downloaded}))
    for s in symbols if files else ():
        (d / "15m" / f"{s}.parquet").write_bytes(b"")
    return d


def test_dataset_verification_refuses_partial_gapped_or_sealed_data(tmp_path):
    a = _dataset(tmp_path, "a", ("2017-09", "2020-09"), 1_601_510_399)
    b = _dataset(tmp_path, "b", ("2020-10", "2024-08"), atlas.ATLAS_END - 1)
    assert len(atlas.verify_datasets([a, b])) == 2
    with pytest.raises(SystemExit, match="contiguous"):
        atlas.verify_datasets([a, _dataset(tmp_path, "c", ("2020-11", "2024-08"), atlas.ATLAS_END - 1)])
    with pytest.raises(SystemExit, match="sealed"):
        atlas.verify_datasets([a, _dataset(tmp_path, "d", ("2020-10", "2026-08"), 1_788_220_799)])
    with pytest.raises(SystemExit, match="start in 2017-09"):
        atlas.verify_datasets([b])
    with pytest.raises(SystemExit, match="downloaded"):
        atlas.verify_datasets([a, _dataset(tmp_path, "e", ("2020-10", "2024-08"), atlas.ATLAS_END - 1, downloaded=1)])
    with pytest.raises(SystemExit, match="missing"):
        atlas.verify_datasets([a, _dataset(tmp_path, "f", ("2020-10", "2024-08"), atlas.ATLAS_END - 1, files=False)])
    with pytest.raises(SystemExit, match="no manifest"):
        atlas.verify_datasets([a, tmp_path / "none"])


def test_only_unrevised_macro_series_are_downloaded():
    assert not atlas.REVISED_SERIES & set(atlas.FRED_SERIES.values())     # M2, broad dollar, CPI... stay out
    assert "M2SL" in atlas.REVISED_SERIES and "DTWEXBGS" in atlas.REVISED_SERIES


def test_listing_age_runs_across_the_dataset_boundary():
    from trading.backtest import majors_signals as ms

    jan, apr = 1_704_067_200, 1_711_929_600                 # 2024-01-01, 2024-04-01
    old_days, new_days = (apr - jan) // 86_400, 122         # Jan..Mar, then Apr..Jul
    new_listed = 70                                         # NEWUSDT lists on 2024-03-11, 21 days before the boundary
    old = _market({"BTCUSDT": _rows([100.0] * old_days, jan), "ETHUSDT": _rows([10.0] * old_days, jan),
                   "NEWUSDT": _rows([1.0] * old_days, jan, gap=(0, new_listed))})
    new_series = {s: _rows([p] * new_days, apr) for s, p in
                  (("BTCUSDT", 100.0), ("ETHUSDT", 10.0), ("NEWUSDT", 1.0), ("LATEUSDT", 5.0))}
    new = _market(new_series)
    (_, _), (first, breaks) = atlas.market_history([old, new])
    end = apr + new_days * 86_400
    months = atlas.market_universes(new, end, first_trade=first, breaks=breaks)
    assert "NEWUSDT" not in months["2024-05"] and "NEWUSDT" not in months["2024-06"]   # under 90 days old
    assert "NEWUSDT" in months["2024-07"]                   # 2024-03-11 + 90 days = 2024-06-09
    assert "LATEUSDT" not in months["2024-06"]              # first seen at the boundary: counted from there
    assert "LATEUSDT" in months["2024-07"]
    # control: ranking the new dataset on its own would have seasoned NEWUSDT at once
    alone = ms.monthly_universe(new, 1_714_521_600, perp_of={}, funding_times={},
                                first_trade=ms.first_trade_times(new), breaks={}, require_perp=False)
    assert "NEWUSDT" in alone


def test_a_failed_member_is_sold_at_its_last_close_never_dropped():
    start = 1_704_067_200
    days = 60
    close = np.full((4, days), 100.0)
    close[1, 40:] = np.nan
    close[1, 35:40] = 1.0                                   # collapses, then stops trading for good on day 40
    close[2, 45:] = 5000.0
    close[2, 42:45] = np.nan                                # a swap: old token last trades on day 41
    close[2, 38:42] = 80.0
    close[3, 59] = np.nan                                   # no month-end close, and nothing after: stopped
    resumed = np.zeros_like(close, dtype=bool)
    resumed[2, 45] = True
    panel = atlas.Panel(["A", "B", "C", "D"], start, close, close.copy(), resumed)
    a, b = 31, 59                                           # February
    assert atlas.member_return(panel, 0, a, b) == pytest.approx(0.0)
    assert atlas.member_return(panel, 1, a, b) == pytest.approx(-99.0)     # not missing
    assert atlas.member_return(panel, 2, a, b) == pytest.approx(-20.0)     # the old token's last close
    assert atlas.member_return(panel, 3, a, b) == pytest.approx(0.0)       # last close on day 58
    gap = close.copy()
    gap[0, 59] = np.nan
    gap = np.concatenate([gap, np.full((4, 1), 100.0)], axis=1)            # trades again on day 60, no break
    held = atlas.Panel(["A", "B", "C", "D"], start, gap, gap.copy(), np.zeros_like(gap, dtype=bool))
    assert atlas.member_return(held, 0, a, b) is None                      # a hole, not an exit: unknown


def test_the_basket_is_unknown_when_any_member_return_is_unknown():
    start = 1_704_067_200
    days = 91
    rng = np.random.default_rng(5)
    series = {name: _rows(list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, days)))), start)
              for name in ["BTCUSDT"] + [f"C{j}USDT" for j in range(9)]}
    series["HOLEUSDT"] = _rows([50.0] * days, start, gap=(59, 60))         # no close on 2024-02-29, trades on
    market = _market(series)
    panel = atlas.build_panel([market], start=start, end=start + days * 86_400)
    members = list(series)
    rows = atlas.market_monthly(panel, {"2024-02": members, "2024-03": members})
    feb, mar = rows[1], rows[2]
    assert feb["unknown_returns"] == 1 and feb["basket_ret_pct"] is None and feb["dispersion_pct"] is None
    assert feb["alts_minus_btc_pct"] is None and feb["btc_ret_pct"] is not None
    assert mar["not_trading_at_start"] == 1 and mar["unknown_returns"] == 0  # no close before March: left out
    assert mar["basket_ret_pct"] is not None


def test_a_member_not_trading_at_the_month_start_is_left_out_and_counted():
    start = 1_704_067_200
    days = 91
    rng = np.random.default_rng(7)
    series = {name: _rows(list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, days)))), start)
              for name in ["BTCUSDT"] + [f"C{j}USDT" for j in range(8)]}
    series["GONEUSDT"] = _rows([50.0] * 55, start)                          # last trade on 2024-02-24
    panel = atlas.build_panel([_market(series)], start=start, end=start + days * 86_400)
    members = list(series)
    feb, mar = atlas.market_monthly(panel, {"2024-02": members, "2024-03": members})[1:3]
    assert feb["not_trading_at_start"] == 0 and feb["unknown_returns"] == 0
    assert feb["basket_ret_pct"] is not None                                # GONE sold at its last close
    assert mar["not_trading_at_start"] == 1 and mar["unknown_returns"] == 0
    assert mar["basket_ret_pct"] is not None and mar["members"] == 10


def _full_macro(tmp_path):
    months = atlas.month_ends()
    series = {name: [(m, 1.0 + k) for k, m in enumerate(months)] for name in atlas.FRED_SERIES}
    rows = atlas.macro_monthly(series, months)
    path = tmp_path / "macro.csv"
    atlas.write_macro(rows, path)
    return path, rows


def test_the_macro_table_must_match_this_schema_and_cover_every_month(tmp_path):
    import csv as _csv

    path, rows = _full_macro(tmp_path)
    table = atlas.read_macro(path)
    assert list(table) == [m.strftime("%Y-%m") for m in atlas.month_ends()]
    assert table["2024-08"]["brent"] is not None
    old = tmp_path / "old.csv"                                  # a table from before Brent and USD/CNY
    keys = [k for k in rows[0] if k not in {"brent", "brent_chg", "usd_cny", "usd_cny_chg"}] + ["m2_busd"]
    with old.open("w", newline="", encoding="utf-8") as handle:
        writer = _csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(SystemExit, match="schema"):
        atlas.read_macro(old)
    partial = tmp_path / "partial.csv"
    atlas.write_macro(rows[:-1], partial)                       # 2024-08 missing
    with pytest.raises(SystemExit, match="month by month"):
        atlas.read_macro(partial)


def test_factors_chosen_with_knowledge_of_the_window_get_a_forward_holdout():
    assert atlas.confirmation_from("brent") == "2026-10" and atlas.confirmation_from("usd_cny_chg") == "2026-10"
    assert atlas.confirmation_from("vix") == "2024-09" and atlas.confirmation_from("net_liquidity_busd") == "2024-09"


def test_a_daily_value_is_used_only_after_its_publication_lag():
    vix = [(date(2024, 3, 4), 20.0), (date(2024, 3, 5), 30.0)]            # Monday, Tuesday closes
    lag = atlas.AVAILABLE_LAG_DAYS["vix"]
    assert atlas.known_at(vix, date(2024, 3, 5), lag) == 20.0               # Tuesday 00:00 UTC
    assert atlas.known_at(vix, date(2024, 3, 6), lag) == 30.0
    assert atlas.known_at(vix, date(2024, 3, 4), lag) is None               # not yet closed
    assert atlas.known_at(vix, date(2024, 3, 5) + timedelta(days=lag + 8), lag) is None   # stale, not carried


def test_the_daily_table_holds_only_exchange_closes_never_corrected_releases():
    assert atlas.DAILY_SERIES == ("vix", "vix3m", "nasdaq", "sp500")
    corrected = {"fed_assets_musd", "tga_musd", "rrp_busd", "us_2y", "us_10y", "us_10y_real", "eur_usd", "usd_cny",
                 "brent"}
    assert not corrected & set(atlas.DAILY_COLUMNS)                         # FRED's current vintage could leak them


def test_the_daily_table_covers_every_day_and_refuses_a_foreign_schema(tmp_path):
    first = date(2017, 9, 1)
    origin = first - timedelta(days=30)                                 # value = days since origin
    series = {name: [(origin + timedelta(days=k), float(k)) for k in range(2700)] for name in atlas.FRED_SERIES}
    rows = atlas.macro_daily(series)
    assert len(rows) == (atlas.ATLAS_END - atlas.ATLAS_START) // 86_400
    assert rows[0]["date"] == "2017-09-01" and rows[-1]["date"] == "2024-08-31"
    for r in rows:                                                      # never a value before its lag
        age = (date.fromisoformat(r["date"]) - origin).days
        assert r["vix"] == age - atlas.AVAILABLE_LAG_DAYS["vix"]
        assert "brent" not in r
    path = tmp_path / "daily.csv"
    atlas.write_macro(rows, path)
    assert atlas.read_daily(path)["2024-08-31"]["vix"] is not None
    atlas.write_macro(rows[:-1], tmp_path / "short.csv")
    with pytest.raises(SystemExit, match="day by day"):
        atlas.read_daily(tmp_path / "short.csv")
    atlas.write_macro([{k: v for k, v in r.items() if k != "vix3m"} for r in rows], tmp_path / "old.csv")
    with pytest.raises(SystemExit, match="schema"):
        atlas.read_daily(tmp_path / "old.csv")
    assert atlas.confirmation_from("vix3m") == "2026-10" and atlas.confirmation_from("vix") == "2024-09"


def test_table_readers_refuse_duplicates_and_non_finite_values(tmp_path):
    first = date(2017, 9, 1)
    series = {name: [(first - timedelta(days=30) + timedelta(days=k), 1.0) for k in range(2700)]
              for name in atlas.FRED_SERIES}
    daily = atlas.macro_daily(series)
    atlas.write_macro(daily + [dict(daily[-1], vix=99.0)], tmp_path / "dup.csv")
    with pytest.raises(SystemExit, match="duplicate date"):
        atlas.read_daily(tmp_path / "dup.csv")
    atlas.write_macro([dict(daily[0], vix=float("nan"))] + daily[1:], tmp_path / "nan.csv")
    with pytest.raises(SystemExit, match="non-finite"):
        atlas.read_daily(tmp_path / "nan.csv")
    path, rows = _full_macro(tmp_path)
    atlas.write_macro(rows + [rows[-1]], tmp_path / "dup_month.csv")
    with pytest.raises(SystemExit, match="duplicate month"):
        atlas.read_macro(tmp_path / "dup_month.csv")
    atlas.write_macro([dict(rows[0], vix=float("inf"))] + rows[1:], tmp_path / "inf.csv")
    with pytest.raises(SystemExit, match="non-finite"):
        atlas.read_macro(tmp_path / "inf.csv")


def test_a_series_in_the_wrong_unit_refuses_the_macro_table():
    assert set(atlas.UNIT_BOUNDS) == set(atlas.FRED_SERIES)
    ok = {"tga_musd": [(date(2024, 1, 31), 750_000.0)], "rrp_busd": [(date(2024, 1, 31), 600.0)]}
    atlas.check_units(ok)
    with pytest.raises(SystemExit, match="tga_musd"):
        atlas.check_units({"tga_musd": [(date(2024, 1, 31), 750.0)]})          # billions passed as millions
    with pytest.raises(SystemExit, match="no plausible range"):
        atlas.check_units({"m2_busd": [(date(2024, 1, 31), 21_000.0)]})
