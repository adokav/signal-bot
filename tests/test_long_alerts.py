from __future__ import annotations

import math

import pytest

import bot
from acce_unified import long_alerts as la

NOW = 1_790_000_000 - 1_790_000_000 % 3_600 + 600     # ten minutes into an hour
H, M15 = 3_600, 900


def _kline(t, o, h, low, c):
    return [t * 1000, str(o), str(h), str(low), str(c), "1", (t + H) * 1000 - 1, "100000"]


def _hours(n, *, low_at=None, low=None, base=10.0, width=0.2, end=NOW - NOW % H):
    """n closed 1h candles ending at the last full hour, plus the forming one."""

    rows = []
    for i in range(n + 1):
        t = end - (n - i) * H
        lo = base - width
        if low_at is not None and i == low_at:
            lo = low
        rows.append(_kline(t, base, base + width, lo, base))
    return rows


# ---------------------------------------------------------------------------
# Stop levels
# ---------------------------------------------------------------------------


def test_stop_plan_uses_closed_candles_only_and_the_documented_rule():
    rows = _hours(30, low_at=25, low=9.5)             # structural low inside the last 12 closed hours
    forming = rows[-1]
    forming[3] = "1.0"                                 # a crash in the forming candle must be ignored
    plan = la.compute_stop_plan(rows, 10.0, now=NOW)
    assert plan.technical_invalidation == pytest.approx(9.5)
    atr = 0.4 + (0.3 / 14)                             # 0.4 range, one candle has a 0.7 range
    assert plan.atr_pct == pytest.approx(atr / 10.0 * 100, rel=1e-6)
    expected = min(9.5 - 0.5 * atr, 10.0 - 1.5 * atr)
    assert plan.hard_stop == pytest.approx(expected)
    assert plan.stop_pct == pytest.approx((10.0 - expected) / 10.0 * 100)
    assert plan.position_pct == pytest.approx(1.0 / plan.stop_pct * 100)
    assert plan.last_candle_open == NOW - NOW % H - H


def test_stop_is_at_least_one_and_a_half_atr_below_the_price():
    plan = la.compute_stop_plan(_hours(30), 10.0, now=NOW)     # low only 0.2 below
    atr = 0.4
    assert plan.hard_stop == pytest.approx(10.0 - 1.5 * atr)
    assert plan.warnings == ()


def test_stop_plan_warnings_and_fail_closed_cases():
    broken = la.compute_stop_plan(_hours(30), 9.0, now=NOW)    # price below the 12h low
    assert any("yapı zaten bozulmuş" in w for w in broken.warnings)
    wide = la.compute_stop_plan(_hours(30, width=1.0), 10.0, now=NOW)
    assert any("çok geniş" in w for w in wide.warnings)
    assert la.compute_stop_plan(_hours(10), 10.0, now=NOW) is None          # not enough history
    assert la.compute_stop_plan(_hours(30), 0.0, now=NOW) is None
    assert la.compute_stop_plan(_hours(30), float("nan"), now=NOW) is None
    garbage = [["x"], [1, "a", "b", "c", "d"], _kline(NOW - 5 * H, 10, 9, 11, 10)]    # malformed / impossible OHLC
    assert la.compute_stop_plan(garbage, 10.0, now=NOW) is None


def test_fixed_levels_from_the_tactical_engine_get_the_same_sizing():
    plan = la.plan_from_levels(100.0, 97.0, 96.0, last_candle_open=NOW)
    assert plan.stop_pct == pytest.approx(4.0) and plan.position_pct == pytest.approx(25.0)
    assert plan.atr_pct is None
    assert la.plan_from_levels(100.0, 99.0, 101.0, last_candle_open=NOW) is None   # stop above price
    assert la.plan_from_levels(100.0, 0.0, 96.0, last_candle_open=NOW) is None


# ---------------------------------------------------------------------------
# Radar log and tracking
# ---------------------------------------------------------------------------


def _entry(now=NOW, *, stop=9.5, price=10.0, source="LIKIT100", symbol="XUSDT"):
    plan = la.plan_from_levels(price, stop + 0.1, stop, last_candle_open=now)
    return la.open_entry(source=source, symbol=symbol, now=now, gate_status="REJECT",
                         detail="Durum: REJECT — geçmiş test NEGATIVE", plan=plan, entry_price=price)


def _m15(start, closes, *, lows=None, opens=None):
    rows = []
    for i, c in enumerate(closes):
        t = start + i * M15
        o = (opens or closes)[i]
        lo = (lows or [min(o, c) - 0.01] * len(closes))[i]
        rows.append([t * 1000, str(o), str(max(o, c) + 0.01), str(lo), str(c), "1", (t + M15) * 1000 - 1, "1"])
    return rows


def test_one_open_entry_per_symbol_and_a_cooldown():
    log = [_entry()]
    assert not la.can_open(log, "LIKIT100", "XUSDT", now=NOW + 60)
    assert la.can_open(log, "TAKTIK", "XUSDT", now=NOW + 60)
    closed = dict(log[0], status=la.STOPPED)
    assert not la.can_open([closed], "LIKIT100", "XUSDT", now=NOW + 11 * H)
    assert la.can_open([closed], "LIKIT100", "XUSDT", now=NOW + 13 * H)


def test_stop_is_reached_only_by_candles_after_the_alert():
    entry = _entry()
    start = NOW - NOW % M15
    before = _m15(start - 2 * M15, [10.0, 10.0], lows=[9.0, 9.0])     # dips before the alert do not count
    after = _m15(start + M15, [10.1, 9.8, 9.6], lows=[10.0, 9.7, 9.4])
    tracked, event = la.track_entry(entry, before + after, now=start + 5 * M15)
    assert event == la.STOPPED
    assert tracked["exit_price"] == pytest.approx(9.5) and tracked["result_pct"] == pytest.approx(-5.0)
    assert tracked["closed_at"] == start + 4 * M15


def test_a_gap_below_the_stop_exits_at_the_open_and_forming_candles_wait():
    entry = _entry()
    start = NOW - NOW % M15 + M15
    gap = _m15(start, [9.2], opens=[9.3], lows=[9.1])
    tracked, event = la.track_entry(entry, gap, now=start + M15)
    assert event == la.STOPPED and tracked["exit_price"] == pytest.approx(9.3)
    untouched, event = la.track_entry(entry, gap, now=start + M15 - 1)              # still forming
    assert event is None and untouched["status"] == la.OPEN


def test_entries_expire_after_72_hours_with_the_last_close():
    entry = _entry()
    start = NOW - NOW % M15 + M15
    n = (la.TRACK_HOURS * H) // M15
    rows = _m15(start, [10.0 + 0.001 * i for i in range(n + 4)])
    tracked, event = la.track_entry(entry, rows, now=NOW + la.TRACK_HOURS * H + M15)
    assert event == la.EXPIRED and tracked["closed_at"] == NOW + la.TRACK_HOURS * H
    last_inside = max(i for i in range(n + 4) if start + i * M15 + M15 <= NOW + la.TRACK_HOURS * H)
    assert tracked["exit_price"] == pytest.approx(10.0 + 0.001 * last_inside)
    stale, event = la.track_entry(entry, [], now=NOW + (la.TRACK_HOURS + la.STALE_GRACE_HOURS) * H)
    assert event == la.EXPIRED and "eksik" in stale["note"]
    waiting, event = la.track_entry(entry, [], now=NOW + (la.TRACK_HOURS + 1) * H)
    assert event is None


def test_tracking_waits_for_the_first_candle_after_the_alert_to_close():
    entry = _entry()                                   # NOW is 10 minutes into a 15m slot
    first_open = NOW - NOW % M15 + M15
    assert la.next_check_due(entry) == first_open + M15 + 30
    tracked, _ = la.track_entry(entry, _m15(first_open, [10.0]), now=first_open + M15 + 30)
    assert la.next_check_due(tracked) == first_open + 2 * M15 + 30


def test_entries_without_a_plan_are_not_tracked_and_trim_keeps_open_ones():
    no_plan = la.open_entry(source="LIKIT100", symbol="ZUSDT", now=NOW, gate_status="REJECT", detail="x",
                            plan=None, entry_price=1.0)
    assert no_plan["status"] == la.EXPIRED and la.track_entry(no_plan, [], now=NOW + H)[1] is None
    rows = [dict(_entry(NOW - i * H, symbol=f"S{i}USDT"), status=la.STOPPED) for i in range(250)]
    rows.append(_entry(NOW - 300 * H, symbol="OLDOPENUSDT"))
    kept = la.trim(rows, limit=200)
    assert len(kept) == 200 and any(e["symbol"] == "OLDOPENUSDT" for e in kept)


def test_texts_show_levels_status_and_no_authority():
    entry = _entry()
    text = la.alert_text(entry, icon="⛔", title="LONG SİNYALİ", evidence="Geçmiş test: NEGATIVE")
    for needle in ("XUSDT", "Hard stop", "Pozisyon", "REJECT (zararda)", "Emir yetkisi yok", "<pre>"):
        assert needle in text
    stopped = dict(entry, status=la.STOPPED, exit_price=9.5, result_pct=-5.0, closed_at=NOW + H)
    assert "-5.0" in la.stop_alert_text(stopped)
    radar = la.format_radar([entry, stopped], now=NOW + H)
    assert "RADAR KAYDI" in radar and "açık" in radar and "stop %-5.0" in radar and "stop 9.5000" in radar
    assert "1 kapanmış kayıt" in la.summary_line([stopped], now=NOW + H)


# ---------------------------------------------------------------------------
# Bot wiring
# ---------------------------------------------------------------------------


class FakeKlines:
    def __init__(self, hourly, quarter=None):
        self.hourly, self.quarter, self.calls = hourly, quarter or [], []

    def fetch_klines(self, symbol, interval_seconds, limit):
        self.calls.append((symbol, interval_seconds, limit))
        return self.hourly if interval_seconds == H else self.quarter


@pytest.fixture
def wired(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "send", lambda text, keyboard=None, html_mode=False: sent.append(text))
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    monkeypatch.setitem(bot.STATE, "radar_log", [])
    monkeypatch.setattr(bot, "LIQUID_LONG_ALERTS", True)
    return sent


def _snapshot(symbol="SOLUSDT", price=10.0):
    return {
        "generated_at": NOW,
        "liquid_market_context": {"regime": "RISK_ON"},
        "liquid_long_candidates": [{"symbol": symbol, "score": 70, "metadata": {"last_price": price, "spread_bps": 3}}],
    }


def test_a_new_top3_coin_raises_one_alert_with_stop_levels(monkeypatch, wired):
    monkeypatch.setattr(bot.time, "time", lambda: NOW)
    monkeypatch.setattr(bot, "KLINES", FakeKlines(_hours(30, low_at=25, low=9.5)))
    bot._liquid_radar(_snapshot())
    assert len(wired) == 1
    alert = wired[0]
    assert "LONG SİNYALİ · Likit-100 #1" in alert and "SOLUSDT" in alert and "Hard stop" in alert
    assert "REJECT" in alert and "Emir yetkisi yok" in alert
    log = bot.STATE["radar_log"]
    assert len(log) == 1 and log[0]["plan"]["hard_stop"] < 10.0 and log[0]["can_authorize_trade"] is False
    bot._liquid_radar(_snapshot())                       # same coin next scan: no second alert
    assert len(wired) == 1 and len(bot.STATE["radar_log"]) == 1


def test_missing_klines_still_log_the_signal_but_say_no_stop(monkeypatch, wired):
    class Broken:
        def fetch_klines(self, *args):
            raise RuntimeError("https://api.mexc.com/?secret=1 down")

    monkeypatch.setattr(bot.time, "time", lambda: NOW)
    monkeypatch.setattr(bot, "KLINES", Broken())
    bot._liquid_radar(_snapshot())
    assert "Stop hesaplanamadı" in wired[0] and "secret" not in wired[0]
    assert bot.STATE["radar_log"][0]["plan"] is None


def test_a_stop_hit_sends_a_stop_alert(monkeypatch, wired):
    entry = _entry(symbol="SOLUSDT")
    bot.STATE["radar_log"] = [entry]
    start = NOW - NOW % M15 + M15
    monkeypatch.setattr(bot, "KLINES", FakeKlines([], _m15(start, [9.4], lows=[9.3], opens=[9.6])))
    monkeypatch.setattr(bot.time, "time", lambda: start + 2 * M15)
    bot._liquid_radar({"liquid_long_candidates": []})
    assert any("STOP · SOLUSDT" in m for m in wired)
    assert bot.STATE["radar_log"][0]["status"] == la.STOPPED


def test_tactical_setups_enter_the_radar_log_with_engine_levels(monkeypatch):
    monkeypatch.setitem(bot.STATE, "radar_log", [])

    class Candle:
        open_time, close = NOW - 300, 100.0

    class Market:
        candles = {"BTCUSDT": {bot.TacticalTimeframe.M5: (Candle(),)}}

    class Decision:
        status = "REJECT"

    monkeypatch.setattr(bot, "status_line", lambda decision: "Durum: REJECT — geçmiş test NEGATIVE")
    item = {"symbol": "BTCUSDT", "setup": "TREND_PULLBACK",
            "plan": {"technical_invalidation": 97.0, "hard_stop": 96.0}}
    plan = bot._tactical_radar_entry(Market(), item, Decision(), NOW)
    assert plan.position_pct == pytest.approx(25.0)
    entry = bot.STATE["radar_log"][0]
    assert entry["source"] == "TAKTIK" and entry["setup"] == "TREND_PULLBACK" and entry["plan"]["hard_stop"] == 96.0


def test_radar_command_and_default_alert_policy():
    assert bot._command("/radar") == "RADAR"
    assert any(row["command"] == "radar" for row in bot.COMMANDS)
    assert bot.TACTICAL_REJECTED_ALERTS is True and bot.LIQUID_LONG_ALERTS is True


# ---------------------------------------------------------------------------
# Supply and ATH / ATL facts on the radar
# ---------------------------------------------------------------------------

READY_FACTS = {
    "status": "READY", "circulating_supply": 25_000_000, "total_supply": 100_000_000, "max_supply": None,
    "circulation_pct": 25.0, "ath_price_usd": 2.5, "ath_change_pct": -60.0, "ath_date": "2024-03-14T10:00:00.000Z",
    "atl_price_usd": 0.05, "atl_change_pct": 1900.0, "atl_date": "2023-01-01T00:00:00.000Z",
}


def test_fundamental_lines_show_supply_and_extremes_or_name_what_is_missing():
    lines = bot._fundamental_lines(READY_FACTS, indent="")
    assert lines[0] == "Arz: dolaşan 25.00M · toplam 100.00M · max açıklanmamış/sınırsız · dolaşımda %25.0"
    assert lines[1] == "ATH $2.50 (%-60.0) · ATL $0.05 (%+1900.0)"
    assert lines[2] == "Tarih: ATH 2024-03-14 · ATL 2023-01-01"
    assert bot._fundamental_lines({"status": "PROVIDER_COOLDOWN"}) == ["   Arz ve ATH/ATL: PROVIDER_COOLDOWN"]
    assert bot._fundamental_lines(None) == ["   Arz ve ATH/ATL: DATA_PENDING"]


def test_longs_panel_and_alert_carry_supply_and_extremes(monkeypatch, wired):
    snapshot = _snapshot()
    snapshot["liquid_long_candidates"][0]["metadata"]["fundamentals"] = dict(READY_FACTS)
    panel = bot.format_longs(snapshot)
    assert "Arz: dolaşan 25.00M" in panel and "ATH $2.50" in panel and "Tarih: ATH 2024-03-14" in panel
    monkeypatch.setattr(bot.time, "time", lambda: NOW)
    monkeypatch.setattr(bot, "KLINES", FakeKlines(_hours(30, low_at=25, low=9.5)))
    bot._liquid_radar(snapshot)
    assert "ATL         $0.05 · %+1900.0" in wired[0] and "Max arz     açıklanmamış" in wired[0]
    assert "ATH tarihi  14.03.2024" in wired[0]
    stored = bot.STATE["radar_log"][0]["fundamentals"]
    assert stored["ath_price_usd"] == 2.5 and stored["max_supply"] is None


def test_alert_html_escapes_untrusted_text_and_falls_back_to_plain(monkeypatch):
    entry = dict(_entry(), symbol="X<b>&USDT", gate_status="REJECT")
    text = la.alert_text(entry, icon="⛔", title="LONG", evidence="a < b & c", fact_rows=[("Arz/ATH", "<i>x</i>")])
    assert "X&lt;b&gt;&amp;USDT" in text and "a &lt; b &amp; c" in text and "&lt;i&gt;x&lt;/i&gt;" in text
    block = text.split("<pre>")[1].split("</pre>")[0]
    assert all(len(line) <= 32 for line in bot._plain(block).split("\n"))  # table rows fit a phone

    calls = []

    def api(method, payload):
        calls.append(payload)
        if payload.get("parse_mode") == "HTML":
            raise RuntimeError("telegram_http_400:sendMessage")

    monkeypatch.setattr(bot, "TOKEN", "t")
    monkeypatch.setattr(bot, "CHAT_ID", "c")
    monkeypatch.setattr(bot, "_api", api)
    bot.send(text, html_mode=True)
    assert calls[0]["parse_mode"] == "HTML" and "parse_mode" not in calls[1]
    assert "X<b>&USDT" in calls[1]["text"] and "<pre>" not in calls[1]["text"]

    def down(method, payload):
        raise RuntimeError("telegram_transport_error:ConnectionError")

    monkeypatch.setattr(bot, "_api", down)
    with pytest.raises(RuntimeError):      # transport errors are not markup errors: no silent retry
        bot.send(text, html_mode=True)


def test_small_prices_use_four_significant_digits():
    assert la._num(0.98) == "0.9800" and la._num(0.0000123456) == "0.00001235"
    assert la._num(64120.5) == "64,120" and la._num(2345.678) == "2,345.68" and la._num(1.05) == "1.0500"


def test_btc_eth_facts_use_project_titles_and_fail_quietly(monkeypatch):
    seen = {}

    class Provider:
        def fetch_many(self, rows):
            seen.update({row.pair: row.title for row in rows})
            return {"BTCUSDT": dict(READY_FACTS)}

    monkeypatch.setattr(bot, "FUNDAMENTAL_PROVIDER", Provider())
    facts = bot._tactical_fundamentals(["BTCUSDT", "ETHUSDT", "BTCUSDT"])
    assert seen == {"BTCUSDT": "Bitcoin (BTC)", "ETHUSDT": "Ethereum (ETH)"}
    panel = bot.format_tactical({"assessments": [{"symbol": "BTCUSDT", "state": "NO_LONG"},
                                                 {"symbol": "ETHUSDT", "state": "NO_LONG"}]}, fundamentals=facts)
    assert "ATH $2.50" in panel and "Arz ve ATH/ATL: DATA_PENDING" in panel     # ETH missing is named

    class Broken:
        def fetch_many(self, rows):
            raise RuntimeError("https://api.coingecko.com/?x_cg_demo_api_key=SECRET 500")

    monkeypatch.setattr(bot, "FUNDAMENTAL_PROVIDER", Broken())
    assert bot._tactical_fundamentals(["BTCUSDT"]) == {}
