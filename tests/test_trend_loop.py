from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import pytest

from acce_unified import trend_loop as tl

DAY, HOUR, QUARTER = tl.DAY, tl.HOUR, tl.QUARTER
T0 = int(datetime(2026, 5, 1, tzinfo=timezone.utc).timestamp())        # first day of the fake history


def _row(t: int, o: float, h: float, lo: float, c: float, qv: float = 1000.0) -> list:
    return [t * 1000, str(o), str(h), str(lo), str(c), "1", (t + 1) * 1000, str(qv)]


# ---------------------------------------------------------------------------
# Parsing and daily bars
# ---------------------------------------------------------------------------


def test_klines_keep_one_open_bar_apart_and_refuse_malformed_payloads():
    rows = [_row(T0 + k * HOUR, 10, 11, 9, 10) for k in range(3)]
    closed, open_bar = tl.parse_klines(rows, interval=HOUR, now=T0 + 2 * HOUR + 60)
    assert [b.open_time for b in closed] == [T0, T0 + HOUR] and open_bar.open_time == T0 + 2 * HOUR
    for bad in ([_row(T0, 10, 9, 9, 10)],                               # high below the close
                [_row(T0, 10, 11, 9, float("nan"))],
                [_row(T0 + HOUR, 10, 11, 9, 10), _row(T0, 10, 11, 9, 10)],
                [_row(T0 + 5 * HOUR, 10, 11, 9, 10)],                    # from the future
                [_row(T0 + 1800, 10, 11, 9, 10)]):                      # not on the hour
        with pytest.raises(tl.TrendLoopDataError):
            tl.parse_klines(bad, interval=HOUR, now=T0 + 2 * HOUR)
    with pytest.raises(tl.TrendLoopDataError, match="more than one open"):
        tl.parse_klines([_row(T0, 10, 11, 9, 10), _row(T0, 10, 11, 9, 10)], interval=HOUR, now=T0 + 60)
    closed, _ = tl.parse_klines([_row(T0 + 8 * HOUR, 10, 11, 9, 10)], interval=DAY, now=T0 + 3 * DAY, aligned=False)
    assert closed[0].open_time == T0 + 8 * HOUR                         # venue day boundary allowed when asked


def test_a_day_without_its_last_hour_has_no_close():
    hours = [tl.Bar(T0 + k * HOUR, 10, 11, 9, 10 + k / 100, 5) for k in range(48) if k != 47]
    days = tl.daily_bars(hours)
    assert set(days) == {T0}                                            # day 2 lost its 23:00 close
    assert days[T0].close == pytest.approx(10.23) and days[T0].quote_volume == 120


def test_a_day_missing_any_hour_is_unknown_not_a_partial_day():
    hours = [tl.Bar(T0 + k * HOUR, 10, 11 if k != 5 else 30, 9, 10, 5) for k in range(48) if k != 5]
    assert set(tl.daily_bars(hours)) == {T0 + DAY}                     # day 1 lost 05:00 (and its high)


# ---------------------------------------------------------------------------
# The rule equals the research implementation
# ---------------------------------------------------------------------------


def _random_days(n: int, seed: int) -> dict[int, tl.DayBar]:
    import random

    rng = random.Random(seed)
    price, out = 100.0, {}
    for k in range(n):
        price *= math.exp(rng.gauss(0.002, 0.04))
        high, low = price * (1 + abs(rng.gauss(0, 0.02))), price * (1 - abs(rng.gauss(0, 0.02)))
        out[T0 + k * DAY] = tl.DayBar(T0 + k * DAY, high, low, price, 1e6)
    return out


def test_levels_match_the_research_trade_loop_day_by_day():
    np = pytest.importorskip("numpy")
    pytest.importorskip("pandas")
    from trading.backtest import trade_loop as research

    days = _random_days(120, seed=7)
    del days[T0 + 70 * DAY]                                             # one unknown day, as NaN in the research
    n = 120
    arr = lambda f: np.array([[getattr(days[T0 + k * DAY], f) if T0 + k * DAY in days else np.nan
                               for k in range(n)]])
    daily = research.Daily(T0, arr("high"), arr("low"), arr("close"))
    ind = research.indicators(daily)
    loop = research.LOOPS[0]
    assert loop.name == "D1_20_10"
    signals = research.entry_signals(loop, daily, ind)
    checked = 0
    for d in range(n):
        live = tl.levels_at(days, T0 + d * DAY)
        atr, hi, lo = ind.atr[0, d], ind.high_close[20][0, d], ind.low_close[10][0, d]
        if not all(math.isfinite(x) for x in (atr, hi, lo, daily.close[0, d])):
            assert live is None
            continue
        checked += 1
        assert live.high20 == pytest.approx(hi) and live.low10 == pytest.approx(lo)
        assert live.atr20 == pytest.approx(atr)
        vol = ind.vol30[0, d]
        assert (live.vol30 is None) == (not math.isfinite(vol))
        if live.vol30 is not None:
            assert live.vol30 == pytest.approx(vol)
        assert tl.enters(live) == bool(signals[0, d])
        assert tl.exits(live) == research.exit_signal(loop, daily, ind, 0, d, math.nan)
        stop, _ = research.stop_levels(loop, ind, 0, d, live.close)
        plan = tl.entry_plan(live, live.close, 12)
        assert plan is None or plan["stop"] == pytest.approx(stop)
    assert checked > 60


def test_live_constants_and_lists_match_the_research():
    from trading.backtest import majors_signals as ms
    from trading.backtest import trade_loop as research
    from trading.data import binance_history_identity as hid

    pytest.importorskip("numpy")
    from trading.data import majors_data

    loop, vol = research.LOOPS[0], research.D1_VOL
    assert (tl.ENTRY_DAYS, tl.EXIT_DAYS, tl.ATR_DAYS, tl.VOL_DAYS) == (loop.entry_days, loop.exit_days,
                                                                       research.ATR_DAYS, research.VOL_DAYS)
    assert tl.VOL_TARGET == vol.vol_target
    assert (tl.TOP_OTHERS, tl.VOLUME_DAYS, tl.MIN_VOLUME_DAYS, tl.SEASON_DAYS) == (
        ms.TOP_OTHERS, ms.VOLUME_DAYS, ms.MIN_VOLUME_DAYS, ms.SEASON_DAYS)
    assert tl.FIXED == ms.FIXED and tl.MEMES == majors_data.MEMES
    assert tl.NOT_ORDINARY == (hid.HISTORY_STABLE | hid.HISTORY_COMMODITY | hid.HISTORY_LEVERAGED
                               | hid.HISTORY_PEGGED)
    assert tl.TRIAL == research.trial_id()


def test_volatility_is_optional_but_the_entry_window_is_not():
    days = _random_days(40, seed=3)
    d = T0 + 35 * DAY
    assert tl.levels_at(days, d).vol30 is not None
    gap = dict(days)
    del gap[T0 + 10 * DAY]                                              # 25 days before: only the volatility needs it
    assert tl.levels_at(gap, d) is not None and tl.levels_at(gap, d).vol30 is None
    del gap[T0 + 20 * DAY]                                              # inside the 21-day window
    assert tl.levels_at(gap, d) is None


def test_entry_plan_needs_a_range_and_sizes_the_slot():
    lv = tl.Levels(T0, close=110.0, high20=100.0, low10=95.0, atr20=2.0, vol30=1.0)
    plan = tl.entry_plan(lv, 111.0, 12)
    assert plan["stop"] == pytest.approx(105.0) and plan["slot_pct"] == pytest.approx(100 / 12)
    assert plan["vol_slot_pct"] == pytest.approx(100 / 12 * 0.5)
    assert tl.entry_plan(tl.Levels(T0, 110.0, 100.0, 95.0, 0.0, 1.0), 111.0, 12) is None     # no range: no stop
    assert tl.entry_plan(tl.Levels(T0, 110.0, 100.0, 95.0, 2.0, None), 111.0, 12)["vol_slot_pct"] is None


def test_stop_fills_at_the_stop_or_at_a_gapped_open():
    bars = [tl.Bar(T0, 100, 101, 99, 100, 1), tl.Bar(T0 + QUARTER, 100, 100, 94, 95, 1),
            tl.Bar(T0 + 2 * QUARTER, 90, 91, 89, 90, 1)]
    assert tl.stop_fill(95.0, bars)[1] == 95.0
    assert tl.stop_fill(92.0, bars[2:])[1] == 90.0                      # opened below the stop
    assert tl.stop_fill(80.0, bars) is None


def test_universe_is_btc_eth_top_ten_and_the_largest_meme():
    volumes = {f"C{k}USDT": 100.0 - k for k in range(14)}
    volumes.update({"BTCUSDT": 1.0, "ETHUSDT": 2.0, "DOGEUSDT": 0.5, "NEWUSDT": 500.0})
    seasoned = {s: True for s in volumes}
    seasoned["NEWUSDT"] = False
    members = tl.rank_universe(volumes, seasoned)
    assert members[:2] == ["BTCUSDT", "ETHUSDT"] and "NEWUSDT" not in members
    assert members[2:12] == [f"C{k}USDT" for k in range(10)] and members[-1] == "DOGEUSDT"
    assert not tl.ordinary("USDC", ["USDC", "BTC"]) and not tl.ordinary("WBTC", ["WBTC", "BTC"])
    assert not tl.ordinary("BTC3L", ["BTC3L", "BTC"]) and tl.ordinary("SOL", ["SOL"])


# ---------------------------------------------------------------------------
# The loop on a fake MEXC
# ---------------------------------------------------------------------------


class FakeMexc:
    """Flat intraday prices per day (±0.5% high/low); ``dips`` lowers single 15m bars."""

    def __init__(self, paths: dict[str, list[float]], volumes: dict[str, float], *, listed: dict[str, int] | None = None):
        self.paths, self.volumes, self.listed = paths, volumes, listed or {}
        self.now = T0
        self.dips: dict[tuple[str, int], float] = {}
        self.fail: set[str] = set()

    def price(self, symbol: str, t: int) -> float | None:
        k = (t - T0) // DAY
        path = self.paths[symbol]
        return path[k] if 0 <= k < len(path) else None

    def klines(self, symbol, interval, *, end=None, start=None, limit=1000):
        if symbol in self.fail:
            raise tl.TrendLoopDataError("mexc http 500")
        hi = min(self.now, end) if end is not None else self.now          # like MEXC: bars opening before hi
        lo = max(start if start is not None else T0, self.listed.get(symbol, T0))
        lo += (-lo) % interval
        rows = []
        for t in range(lo, hi, interval):
            p = self.price(symbol, t)
            if p is not None:
                low = self.dips.get((symbol, t), p * 0.995)
                rows.append(_row(t, p, p * 1.005, min(low, p), p, self.volumes.get(symbol, 1.0) / 24))
        return rows[:limit] if start is not None else rows[-limit:]   # from start, or the latest before end

    def tickers(self):
        return [{"symbol": s, "quoteVolume": str(v)} for s, v in self.volumes.items()]


def _flat_then(n: int, base: float, changes: dict[int, float]) -> list[float]:
    out, price = [], base
    for k in range(n):
        price = changes.get(k, price)
        out.append(price)
    return out


def _setup(tmp_path, *, breakout_day: int, n: int = 160):
    paths = {s: [100.0] * n for s in ("BTCUSDT", "ETHUSDT", *(f"C{k}USDT" for k in range(11)), "DOGEUSDT")}
    paths["C0USDT"] = _flat_then(n, 100.0, {breakout_day: 110.0, breakout_day + 1: 111.0})
    volumes = {s: 1e9 - i for i, s in enumerate(paths)}
    market = FakeMexc(paths, volumes)
    loop = tl.TrendLoop(tl.Ledger(tmp_path / "ledger.jsonl"), market=market, workers=2)
    return loop, market


def _decide(loop, market, day_index: int, minutes: int = 3) -> int | None:
    market.now = T0 + (day_index + 1) * DAY + minutes * 60
    return loop.run_daily(market.now)


def _events(tmp_path):
    path = tmp_path / "ledger.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


def test_breakout_enters_once_with_its_stop_and_a_message(tmp_path):
    b = 130                                                             # 2026-09-08: September universe exists
    loop, market = _setup(tmp_path, breakout_day=b)
    assert _decide(loop, market, b - 1) == T0 + (b - 1) * DAY           # builds the September universe
    assert loop.state["universe"]["members"][:2] == ["BTCUSDT", "ETHUSDT"]
    assert "DOGEUSDT" in loop.state["universe"]["members"]
    _decide(loop, market, b)
    pos = loop.state["positions"]["C0USDT"]
    assert pos["entry_price"] == 111.0 and pos["close"] == 110.0 and pos["high20"] == 100.0
    assert pos["stop"] == pytest.approx(111.0 - 3 * pos["atr20"]) and pos["stop"] < 111.0
    out = loop.outbox()
    assert len(out) == 1 and "GİRİŞ" in out[0]["text"] and "C0USDT" in out[0]["text"]
    assert "emir yok" in out[0]["text"] and "RİSK_AZALTIR" in out[0]["text"]
    assert _decide(loop, market, b) is None                             # already decided: nothing twice
    assert [e["event"] for e in _events(tmp_path)].count("ENTRY") == 1
    assert all(e["can_authorize_trade"] is False and e["trial"] == tl.TRIAL for e in _events(tmp_path))


def test_an_intraday_dip_through_the_stop_closes_the_position(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    stop = loop.state["positions"]["C0USDT"]["stop"]
    dip_at = T0 + (b + 2) * DAY + 9 * HOUR + 2 * QUARTER
    market.dips[("C0USDT", dip_at)] = stop * 0.98
    market.now = dip_at + 600                                            # the dip bar is still open: its low counts
    loop.check_stops(market.now)
    assert "C0USDT" not in loop.state["positions"]
    stop_event = [e for e in _events(tmp_path) if e["event"] == "STOP"][0]
    assert stop_event["exit_price"] == pytest.approx(stop) and stop_event["result_pct"] < 0
    assert any("ACİL STOP" in m["text"] for m in loop.outbox())


def test_a_close_below_the_ten_day_low_exits_at_the_next_open(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    path = market.paths["C0USDT"]
    for k in range(b + 2, b + 12):
        path[k] = 112.0                                                 # ten closes at 112: the 10-day low is 112
    path[b + 12] = 111.5                                                # below it, above the stop
    path[b + 13] = 111.0
    for d in range(b - 1, b + 12):
        _decide(loop, market, d)
    assert "C0USDT" in loop.state["positions"]
    assert loop.state["positions"]["C0USDT"]["exit_level"] == 111.0     # window b+1..b+10 still holds 111
    _decide(loop, market, b + 12)
    assert "C0USDT" not in loop.state["positions"]
    exit_event = [e for e in _events(tmp_path) if e["event"] == "EXIT"][0]
    assert exit_event["close"] == 111.5 and exit_event["level"] == 112.0 and exit_event["exit_price"] == 111.0
    assert loop.state["reentry_from"]["C0USDT"] == T0 + (b + 13) * DAY
    assert any("ÇIKIŞ" in m["text"] for m in loop.outbox())


def test_missing_data_is_unknown_never_a_signal(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    _decide(loop, market, b - 1)
    market.fail.add("C0USDT")
    assert _decide(loop, market, b) is None                             # inside the retry window: not final
    assert "C0USDT" in loop.state["unknown"] and "C0USDT" not in loop.state["positions"]
    assert _decide(loop, market, b, minutes=20) == T0 + b * DAY         # final after 15 minutes
    assert [e for e in _events(tmp_path) if e["event"] == "UNKNOWN"][0]["symbol"] == "C0USDT"
    assert not [e for e in _events(tmp_path) if e["event"] == "ENTRY"]


def test_universe_without_btc_or_eth_refuses_and_backs_off(tmp_path):
    loop, market = _setup(tmp_path, breakout_day=150)
    market.fail.add("BTCUSDT")
    with pytest.raises(tl.TrendLoopDataError, match="BTC and ETH"):
        _decide(loop, market, 129)
    assert loop.state["universe"] is None and loop.state["last_decision_day"] is None
    assert _decide(loop, market, 129, minutes=5) is None                # backing off, no request storm


def test_days_without_a_decision_mark_open_positions(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    _decide(loop, market, b + 3)                                        # the bot was down for two decisions
    assert loop.state["positions"]["C0USDT"]["tracking_gap"] is True
    assert [e for e in _events(tmp_path) if e["event"] == "DECISION_GAP"]


def test_state_round_trips_and_the_ledger_never_repeats_an_event(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    saved = json.loads(json.dumps(loop.export()))
    again = tl.TrendLoop(tl.Ledger(tmp_path / "ledger.jsonl"), market=market, workers=2)
    again.load(saved)
    again.state["last_decision_day"] = None                             # a restart that lost the decision mark
    _decide(again, market, b)
    assert [e["event"] for e in _events(tmp_path)].count("ENTRY") == 1
    message = again.outbox()[0]
    again.delivered(message["id"], at=market.now)
    assert again.outbox() == []


def test_reports_escape_and_carry_the_evidence(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    text = loop.positions_text(market.now)
    assert "C0USDT" in text and "kapanış &lt;" in text and "RİSK_AZALTIR" in text and "emir yok" in text
    assert "D1 döngü: 1 açık gölge pozisyon" in loop.status_line()


# ---------------------------------------------------------------------------
# Durability: the ledger is the record; restarts never lose a position or an alert
# ---------------------------------------------------------------------------


def test_ledger_repairs_a_torn_last_line_and_refuses_a_malformed_one(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"id": "A", "event": "UNIVERSE"}\n{"id": "B", "ev')              # crash mid-write
    ledger = tl.Ledger(path)
    assert ledger.ids() == {"A"} and path.read_text() == '{"id": "A", "event": "UNIVERSE"}\n'
    assert ledger.append({"id": "B", "event": "UNIVERSE"}) is True
    assert [json.loads(l)["id"] for l in path.read_text().splitlines()] == ["A", "B"]
    path.write_text('{"id": "A"}\nnot json\n{"id": "C"}\n')
    with pytest.raises(ValueError):
        tl.Ledger(path).ids()
    path.write_text('{"event": "ENTRY"}\n')
    with pytest.raises(tl.TrendLoopDataError):
        tl.Ledger(path).ids()


def test_an_alert_stays_pending_until_its_delivery_is_recorded(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    restarted = tl.TrendLoop(tl.Ledger(tmp_path / "ledger.jsonl"), market=market, workers=2)
    restarted.load(loop.export())
    pending = restarted.outbox()
    assert [m["id"] for m in pending] == [f"ENTRY:C0USDT:{tl._iso(T0 + b * DAY)}"]   # not delivered: kept
    restarted.delivered(pending[0]["id"], at=market.now, sent=False)
    again = tl.TrendLoop(tl.Ledger(tmp_path / "ledger.jsonl"), market=market, workers=2)
    again.load(loop.export())
    assert again.outbox() == []
    delivery = [e for e in _events(tmp_path) if e["event"] == "DELIVERED"]
    assert delivery[0]["sent"] is False and delivery[0]["can_authorize_trade"] is False


def test_a_lost_state_file_restores_open_positions_from_the_ledger(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    fresh = tl.TrendLoop(tl.Ledger(tmp_path / "ledger.jsonl"), market=market, workers=2)
    fresh.load(None)                                                    # the bot's state file was lost
    position = fresh.state["positions"]["C0USDT"]
    assert position["entry_price"] == 111.0 and position["tracking_gap"] is True
    assert position["checked_from"] == position["entered_at"]           # stops re-checked from the entry
    assert fresh.state["last_decision_day"] == T0 + b * DAY            # from the DAY record: no re-decision
    assert _decide(fresh, market, b) is None
    assert [e["event"] for e in _events(tmp_path)].count("ENTRY") == 1


def test_a_close_recorded_before_a_crash_is_not_reopened_by_stale_state(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    stale = loop.export()                                               # saved before the stop
    dip_at = T0 + (b + 2) * DAY + 9 * HOUR
    market.dips[("C0USDT", dip_at)] = loop.state["positions"]["C0USDT"]["stop"] * 0.98
    market.now = dip_at + 600
    loop.check_stops(market.now)                                        # STOP in the ledger, then a crash
    restarted = tl.TrendLoop(tl.Ledger(tmp_path / "ledger.jsonl"), market=market, workers=2)
    restarted.load(stale)
    assert "C0USDT" not in restarted.state["positions"]
    assert restarted.state["reentry_from"]["C0USDT"] == T0 + (b + 2) * DAY
    assert any("ACİL STOP" in m["text"] for m in restarted.outbox())


def test_an_unchecked_stop_is_reported_and_the_loop_is_not_fresh(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    assert loop.fresh(T0 + (b + 1) * DAY + 3 * HOUR, stop_seconds=300) is False   # nothing decided yet
    for d in (b - 1, b):
        _decide(loop, market, d)
    market.now = T0 + (b + 1) * DAY + 3 * HOUR
    loop.check_stops(market.now)
    assert loop.state["stop_unknown"] == [] and loop.fresh(market.now, stop_seconds=300) is True
    market.fail.add("C0USDT")
    loop.check_stops(market.now + 300)
    assert loop.state["stop_unknown"] == ["C0USDT"]
    assert loop.fresh(market.now + 300, stop_seconds=300) is False
    assert "Acil stop kontrol edilemedi: C0USDT" in loop.positions_text(market.now)
    assert loop.fresh(market.now + 2 * DAY, stop_seconds=300) is False            # a missed decision is stale
    market.fail.discard("C0USDT")
    market.listed["C0USDT"] = T0 + 1000 * DAY                                       # MEXC answers with no bars
    loop.check_stops(market.now + 600)
    assert loop.state["stop_unknown"] == ["C0USDT"] and "C0USDT" in loop.state["positions"]


def test_a_late_decision_says_its_entry_reference_is_not_a_buy_now_price(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    _decide(loop, market, b - 1)
    _decide(loop, market, b, minutes=5 * 60)                            # the bot came up five hours late
    text = loop.outbox()[0]["text"]
    assert "Geç karar: 5 saat" in text and "şimdi al" in text
    assert loop.state["positions"]["C0USDT"]["entry_price"] == 111.0   # the rule's fill is unchanged
    on_time, market2 = _setup(tmp_path / "b", breakout_day=b)
    _decide(on_time, market2, b - 1)
    _decide(on_time, market2, b)
    assert "Geç karar" not in on_time.outbox()[0]["text"]


# ---------------------------------------------------------------------------
# Review fixes: durable-first state changes, the full stop backlog, the recorded universe
# ---------------------------------------------------------------------------


def test_a_failed_ledger_write_keeps_the_position_open_and_tracked(tmp_path, monkeypatch):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    dip_at = T0 + (b + 2) * DAY + 9 * HOUR
    market.dips[("C0USDT", dip_at)] = loop.state["positions"]["C0USDT"]["stop"] * 0.98
    market.now = dip_at + 600

    def disk_full(event, *, text=None):
        raise OSError("no space left on device")
    monkeypatch.setattr(loop.ledger, "append", disk_full)
    with pytest.raises(OSError):
        loop.check_stops(market.now)
    assert "C0USDT" in loop.state["positions"]                          # still open, still tracked
    monkeypatch.undo()
    loop.check_stops(market.now)
    assert "C0USDT" not in loop.state["positions"]
    assert [e["event"] for e in _events(tmp_path)].count("STOP") == 1


def test_a_failed_entry_write_opens_no_position(tmp_path, monkeypatch):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    _decide(loop, market, b - 1)
    real = loop.ledger.append

    def no_entries(event, *, text=None):
        if event["event"] == "ENTRY":
            raise OSError("disk")
        return real(event, text=text)
    monkeypatch.setattr(loop.ledger, "append", no_entries)
    with pytest.raises(OSError):
        _decide(loop, market, b)
    assert loop.state["positions"] == {} and loop.state["last_decision_day"] == T0 + (b - 1) * DAY


def test_a_stop_backlog_longer_than_one_request_is_read_to_the_end(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    dip_at = T0 + (b + 13) * DAY + 5 * HOUR                             # past the first 1000 15m bars
    market.dips[("C0USDT", dip_at)] = loop.state["positions"]["C0USDT"]["stop"] * 0.98
    market.now = T0 + (b + 15) * DAY
    assert loop.check_stops(market.now) == []
    stop = [e for e in _events(tmp_path) if e["event"] == "STOP"][0]
    assert stop["exited_at"] == dip_at and stop["reported_at"] == market.now
    assert "Gecikme" in loop.outbox()[-1]["text"]


def test_an_unfinished_stop_backlog_is_unknown_and_blocks_the_exit_decision(tmp_path, monkeypatch):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    for d in (b - 1, b):
        _decide(loop, market, d)
    monkeypatch.setattr(tl, "STOP_CHUNKS", 1)
    path = market.paths["C0USDT"]
    for k in range(b + 2, b + 24):
        path[k] = 108.0                                                 # above the stop (~106.6)
    path[b + 24] = 107.5                                                # a close below the 10-day low
    market.now = T0 + (b + 25) * DAY
    assert loop.check_stops(market.now) == ["C0USDT"]                   # 23 days unchecked: two passes needed
    assert loop.state["stop_unknown"] == ["C0USDT"] and not loop.fresh(market.now, stop_seconds=300)
    checked = loop.state["positions"]["C0USDT"]["checked_from"]
    assert checked == T0 + (b + 1) * DAY + 999 * QUARTER                 # progress is kept for the next pass
    loop.state["positions"]["C0USDT"]["checked_from"] = T0 + (b + 1) * DAY   # as if no pass had run
    loop.state["last_decision_day"] = T0 + (b + 23) * DAY
    _decide(loop, market, b + 24, minutes=20)                           # the stop may have come first
    assert "C0USDT" in loop.state["positions"]
    assert [e for e in _events(tmp_path) if e["event"] == "UNKNOWN" and e["symbol"] == "C0USDT"]
    assert not [e for e in _events(tmp_path) if e["event"] == "EXIT"]


def test_a_lost_state_restores_the_recorded_universe_not_a_rebuild(tmp_path):
    b = 130
    loop, market = _setup(tmp_path, breakout_day=b)
    _decide(loop, market, b - 1)
    recorded = loop.state["universe"]["members"]
    market.volumes = {s: float(i) for i, s in enumerate(market.volumes)}  # later volumes would rank differently
    restarted = tl.TrendLoop(tl.Ledger(tmp_path / "ledger.jsonl"), market=market, workers=2)
    restarted.load(None)
    assert restarted.state["universe"]["members"] == recorded
    restarted.state["last_decision_day"] = None
    _decide(restarted, market, b)
    assert restarted.state["universe"]["members"] == recorded
