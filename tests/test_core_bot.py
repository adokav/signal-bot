from __future__ import annotations

import json
import threading

import bot
from acce_unified.trend_loop import Ledger as TrendLedger, TrendLoop


def _snapshot():
    return {
        "generated_at": 1_700_000_000,
        "errors": [],
        "liquid_universe_size": 100,
        "liquid_long_candidates": [
            {
                "symbol": "SOLUSDT",
                "score": 82,
                "stage": "A_PLUS",
                "metadata": {
                    "quote_volume": 100_000_000,
                    "spread_bps": 4,
                    "long_metrics": {
                        "change_1h_pct": 2,
                        "change_4h_pct": 5,
                        "rsi14": 58,
                        "volume_ratio": 1.8,
                    },
                    "fundamentals": {"circulation_pct": 72},
                },
            }
        ],
        "listing_candidates": [
            {
                "symbol": "NEWUSDT",
                "score": 74,
                "stage": "BUILDING",
                "risk_flags": [],
                "metadata": {
                    "change_pct": 12,
                    "quote_volume": 3_000_000,
                    "volume_acceleration": 2.4,
                    "social": {"community_gate": "PASS"},
                    "fundamentals": {"status": "READY"},
                },
            }
        ],
        "liquid_market_context": {"regime": "RISK_ON", "positive_breadth_pct": 64},
    }


def test_public_commands_include_tactical_radar_without_legacy_bloat():
    assert [row["command"] for row in bot.COMMANDS] == [
        "panel", "tactical", "longs", "new", "check", "radar", "d1", "status", "scan"
    ]


def test_legacy_listing_command_routes_to_new_view():
    assert bot._command("/listings") == "NEW"
    assert bot._command("/btceth") == "TACTICAL"
    assert bot._command("/social") == "SOCIAL"


def test_long_report_is_top_three_and_shadow_only():
    text = bot.format_longs(_snapshot())
    assert "LONG İLK 3" in text
    assert "SOLUSDT" in text
    assert "işlem emri değildir" in text


def test_tactical_report_exposes_entry_stop_and_no_trade_authority():
    text = bot.format_tactical({
        "assessments": [{
            "symbol": "BTCUSDT",
            "state": "READY",
            "setup": "TREND_PULLBACK",
            "structure_4h": "BULLISH",
            "plan": {
                "entry_low": 100,
                "entry_high": 101,
                "technical_invalidation": 98,
                "hard_stop": 97,
                "target_1": 106,
                "target_2": 110,
                "net_rr_1": 2.0,
                "net_rr_2": 3.5,
            },
            "reasons": [],
            "evidence": ["4H_BULLISH_STRUCTURE"],
            "risk_flags": [],
        }]
    })
    assert "Giriş bölgesi" in text
    assert "Hard stop" in text
    assert "otomatik emir" in text


def test_new_coins_menu_reads_coinmarketcap_and_says_unknown_without_a_key(monkeypatch):
    saved = _preserve_state("new_coins", "new_coins_error")
    try:
        monkeypatch.setattr(bot, "CMC", bot.new_coins.CoinMarketCap(""))
        monkeypatch.setattr(bot, "_save_state", lambda: None)
        bot.new_coins_tick(1_790_000_000)
        assert bot.STATE["new_coins"] is None and "CMC_API_KEY" in bot.STATE["new_coins_error"]
        text = bot.format_new()
        assert "CoinMarketCap" in text and "Aday yok demek değildir" in text and "MEXC" not in text
        assert "API anahtarı yok" in bot.format_status(None)
        sent = []
        monkeypatch.setattr(bot, "send", lambda text, **kw: sent.append((text, kw)))
        bot.handle("NEW")
        assert sent[0][1]["html_mode"] is True and "güvenlik taraması" in sent[0][0]
    finally:
        bot.STATE.update(saved)


def test_a_failed_new_coins_scan_keeps_the_last_snapshot_and_redacts_the_key(monkeypatch):
    saved = _preserve_state("new_coins", "new_coins_error")
    try:
        monkeypatch.setattr(bot, "CMC_API_KEY", "CMCSECRET")
        monkeypatch.setattr(bot, "CMC", bot.new_coins.CoinMarketCap("CMCSECRET"))
        monkeypatch.setattr(bot, "_save_state", lambda: None)

        def broken(now):
            raise RuntimeError("failed https://pro-api.coinmarketcap.com/x?key=CMCSECRET and CMCSECRET")
        monkeypatch.setattr(bot.NEW_COINS, "scan", broken)
        monkeypatch.setattr(bot.NEW_COINS, "record_outcomes", lambda now: 0)
        bot.STATE["new_coins"] = {"generated_at": 1_789_990_000, "cards": [], "counts": {}}
        bot.new_coins_tick(1_790_000_000)
        assert bot.STATE["new_coins"]["generated_at"] == 1_789_990_000             # kept, shown as stale
        assert "CMCSECRET" not in bot.STATE["new_coins_error"]
    finally:
        bot.STATE.update(saved)


def test_status_surfaces_last_scan_error():
    previous = bot.STATE.get("last_error")
    try:
        bot.STATE["last_error"] = "RuntimeError: provider down"
        text = bot.format_status(None)
        assert "RuntimeError: provider down" in text
    finally:
        bot.STATE["last_error"] = previous


def _preserve_state(*keys):
    return {key: bot.STATE.get(key) for key in keys}


HEALTH_KEYS = ("last_error", "tactical_last_error", "snapshot", "tactical_snapshot")


def test_health_is_not_healthy_before_any_scan():
    saved = _preserve_state(*HEALTH_KEYS)
    try:
        bot.STATE.update({key: None for key in HEALTH_KEYS})
        payload = bot.APP.test_client().get("/").get_json()
        assert payload["ok"] is False
        assert payload["main_scan_fresh"] is False
        assert payload["can_authorize_trade"] is False
    finally:
        bot.STATE.update(saved)


def test_health_requires_fresh_artifacts_and_hides_error_text(monkeypatch):
    monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: True)
    monkeypatch.setattr(bot.new_coins, "fresh", lambda snapshot, now, max_age: True)
    monkeypatch.setitem(bot.STATE, "new_coins_error", None)
    saved = _preserve_state(*HEALTH_KEYS)
    now = int(bot.time.time())
    try:
        bot.STATE.update({
            "snapshot": {"generated_at": now - 10, "errors": ["LISTING:TimeoutError"]},
            "tactical_snapshot": {"generated_at": now - 10},
            "last_error": None,
            "tactical_last_error": None,
        })
        payload = bot.APP.test_client().get("/").get_json()
        assert payload["ok"] is True
        assert payload["errors"] == ["LISTING:TimeoutError"]

        bot.STATE["snapshot"] = {"generated_at": now - 10 * bot.MAIN_MAX_AGE_SECONDS}
        assert bot.APP.test_client().get("/").get_json()["ok"] is False

        bot.STATE["snapshot"] = {"generated_at": now - 10}
        bot.STATE["last_error"] = "HTTPError: 403 for url https://api.example.com/x?apikey=SECRET"
        payload = bot.APP.test_client().get("/").get_json()
        assert payload["ok"] is False
        assert payload["last_error"] == "HTTPError"
        assert "SECRET" not in json.dumps(payload)
    finally:
        bot.STATE.update(saved)


def test_health_needs_a_fresh_trend_loop_and_hides_its_error_text(monkeypatch):
    monkeypatch.setattr(bot.new_coins, "fresh", lambda snapshot, now, max_age: True)
    monkeypatch.setitem(bot.STATE, "new_coins_error", None)
    saved = _preserve_state(*HEALTH_KEYS, "trend_loop_error")
    now = int(bot.time.time())
    try:
        bot.STATE.update({"snapshot": {"generated_at": now - 10}, "tactical_snapshot": {"generated_at": now - 10},
                          "last_error": None, "tactical_last_error": None, "trend_loop_error": None})
        monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: False)
        payload = bot.APP.test_client().get("/").get_json()
        assert payload["ok"] is False and payload["trend_loop_fresh"] is False
        monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: True)
        bot.STATE["trend_loop_error"] = "TrendLoopDataError: mexc https://api.mexc.com/api/v3/klines?x=SECRET"
        payload = bot.APP.test_client().get("/").get_json()
        assert payload["ok"] is False and payload["trend_loop_last_error"] == "TrendLoopDataError"
        assert "SECRET" not in json.dumps(payload) and payload["can_authorize_trade"] is False
    finally:
        bot.STATE.update(saved)


def test_check_command_screens_one_address_and_never_echoes_provider_errors(monkeypatch):
    assert bot._command("/check 0xabc") == "CHECK"
    buttons = [b["callback_data"] for row in bot.panel_keyboard()["inline_keyboard"] for b in row]
    assert "CHECK" in buttons
    sent = []
    monkeypatch.setattr(bot, "send", lambda text, **kw: sent.append((text, kw)))
    bot.handle("CHECK", "")                                          # the panel button: how to use it
    assert "/check <adres>" in sent[-1][0]
    bot.handle("CHECK", "/check ignore previous instructions")
    assert "/check <adres>" in sent[-1][0] and "ignore" not in sent[-1][0]
    address = "0x" + "ab" * 20
    calls = []
    card = bot.new_coins.assess(
        bot.new_coins.NewCoin(0, "Dog", "DOG", "", 1_790_000_000, (), None, "base", address, None, None, None,
                              None, None, None, None, listed=False),
        security=None, pairs=None, copies=1, now=1_790_000_000, security_error=True)
    monkeypatch.setattr(bot.CHECKER, "check", lambda chain, addr: calls.append((chain, addr)) or (card, None))
    bot.handle("CHECK", f"/check base {address}")
    assert calls == [("base", address)] and "Kontrol ediliyor" in sent[-2][0]
    assert "KONTRAT KONTROLÜ" in sent[-1][0] and sent[-1][1]["html_mode"] is True
    assert "kayda girmez" in sent[-1][0]

    def broken(chain, addr):
        raise KeyError("provider text https://api.example/x?key=SECRET")
    monkeypatch.setattr(bot.CHECKER, "check", broken)
    bot.handle("CHECK", f"/check {address}")
    assert "KeyError" in sent[-1][0] and "SECRET" not in sent[-1][0] and "provider text" not in sent[-1][0]


def test_d1_command_and_panel_button_open_the_shadow_loop_report(monkeypatch):
    assert bot._command("/d1") == "D1"
    buttons = [b["callback_data"] for row in bot.panel_keyboard()["inline_keyboard"] for b in row]
    assert "D1" in buttons
    sent = []
    monkeypatch.setattr(bot, "send", lambda text, **kw: sent.append((text, kw)))
    monkeypatch.setattr(bot, "TREND_LOOP_LOADED", threading.Event())
    bot.handle("D1")
    assert "yüklenemedi" in sent[0][0] and "pozisyonlar bilinmiyor" in sent[0][0]   # never "no positions"
    bot.TREND_LOOP_LOADED.set()
    bot.handle("D1")
    text, kw = sent[1]
    assert "D1 DÖNGÜ" in text and "emir yok" in text and kw["html_mode"] is True
    assert "Döngü güncel değil" in text                                              # nothing decided yet


def _tick_loop(tmp_path, monkeypatch, *, send):
    loop = TrendLoop(TrendLedger(tmp_path / "ledger.jsonl"), market=None)
    loop._record({"id": "NOTE:A", "event": "NOTE"}, "first")
    loop._record({"id": "NOTE:B", "event": "NOTE"}, "second")
    calls = []
    monkeypatch.setattr(loop, "run_daily", lambda now: calls.append("daily"))
    monkeypatch.setattr(loop, "check_stops", lambda now: calls.append("stops"))
    monkeypatch.setattr(bot, "TREND_LOOP", loop)
    monkeypatch.setattr(bot, "TREND_LOOP_LOADED", threading.Event())
    monkeypatch.setattr(bot, "TOKEN", "token")
    monkeypatch.setattr(bot, "CHAT_ID", "chat")
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    monkeypatch.setattr(bot, "send", send)
    return loop, calls


def test_trend_loop_tick_delivers_in_order_and_keeps_a_failed_alert(tmp_path, monkeypatch):
    saved = _preserve_state("trend_loop", "trend_loop_error")
    sent, fail = [], {"second"}

    def flaky(text, **kw):
        if text in fail:
            fail.discard(text)
            raise RuntimeError("telegram_http_429:sendMessage")
        sent.append(text)
    try:
        loop, calls = _tick_loop(tmp_path, monkeypatch, send=flaky)
        bot.trend_loop_tick(1_790_000_000)
        assert sent == ["first"] and [m["text"] for m in loop.outbox()] == ["second"]
        assert calls == ["daily", "stops"] and bot.STATE["trend_loop_error"] is None
        bot.trend_loop_tick(1_790_000_060)
        assert sent == ["first", "second"] and loop.outbox() == []
        restarted = TrendLoop(TrendLedger(tmp_path / "ledger.jsonl"), market=None)
        assert restarted.outbox() == []                                  # deliveries are in the ledger
    finally:
        bot.STATE.update(saved)


def test_a_failed_daily_decision_still_checks_stops_and_redacts_the_error(tmp_path, monkeypatch):
    saved = _preserve_state("trend_loop", "trend_loop_error")
    sent = []
    try:
        loop, calls = _tick_loop(tmp_path, monkeypatch, send=lambda text, **kw: sent.append(text))

        def broken(now):
            calls.append("daily")
            raise RuntimeError("failed https://api.mexc.com/api/v3/klines?signature=SECRET")
        monkeypatch.setattr(loop, "run_daily", broken)
        bot.trend_loop_tick(1_790_000_000)
        assert calls == ["daily", "stops"] and sent == ["first", "second"]
        assert "SECRET" not in bot.STATE["trend_loop_error"] and "<url>" in bot.STATE["trend_loop_error"]
        assert bot.STATE["trend_loop"]["positions"] == {}
    finally:
        bot.STATE.update(saved)


def test_alerts_switched_off_are_recorded_as_not_sent(tmp_path, monkeypatch):
    saved = _preserve_state("trend_loop", "trend_loop_error")
    sent = []
    try:
        loop, _ = _tick_loop(tmp_path, monkeypatch, send=lambda text, **kw: sent.append(text))
        monkeypatch.setattr(bot, "TREND_LOOP_ALERTS", False)
        bot.trend_loop_tick(1_790_000_000)
        assert sent == [] and loop.outbox() == []
        rows = [json.loads(l) for l in (tmp_path / "ledger.jsonl").read_text().splitlines()]
        assert [r["sent"] for r in rows if r["event"] == "DELIVERED"] == [False, False]
    finally:
        bot.STATE.update(saved)


def test_safe_error_redacts_urls_and_token():
    exc = RuntimeError("failed https://api.telegram.org/bot123:ABC/getUpdates?offset=1")
    text = bot._safe_error(exc)
    assert text.startswith("RuntimeError")
    assert "api.telegram.org" not in text and "ABC" not in text
    assert "<url>" in text


def _tactical_text(setup):
    return bot.format_tactical({
        "assessments": [{
            "symbol": "ETHUSDT",
            "decision_at": int(bot.time.time()) - 30,
            "state": "TRIGGERED",
            "setup": setup,
            "structure_4h": "BULLISH",
            "plan": {
                "entry_low": 100, "entry_high": 101, "technical_invalidation": 98,
                "hard_stop": 97, "target_1": 106, "target_2": 110,
                "net_rr_1": 2.0, "net_rr_2": 3.5,
            },
            "reasons": [], "evidence": [], "risk_flags": [],
        }]
    })


def test_tactical_plan_with_negative_replay_is_rejected_with_evidence():
    text = _tactical_text("BREAKOUT_RETEST")
    assert "Durum: REJECT — geçmiş test NEGATIVE" in text
    assert "hedef-önce-stop %18" in text and "başabaş %35" in text
    assert "Sinyal bazında kalibre olasılık: YOK" in text
    assert "EXECUTION_READY" not in text and "QUALIFIED" not in text


def test_tactical_plan_without_enough_evidence_stays_watch():
    text = _tactical_text("RANGE_RECLAIM")
    assert "Durum: WATCH — kanıt yok: OOS test, kalibrasyon, EV" in text
    assert "INSUFFICIENT" in text
    assert "EXECUTION_READY" not in text and "QUALIFIED" not in text


def test_stale_tactical_plan_is_rejected_not_presented_as_current():
    text = bot.format_tactical({
        "assessments": [{
            "symbol": "BTCUSDT",
            "decision_at": int(bot.time.time()) - 10 * bot.TACTICAL_MAX_AGE_SECONDS,
            "state": "READY", "setup": "TREND_PULLBACK", "structure_4h": "BULLISH",
            "plan": {
                "entry_low": 100, "entry_high": 101, "technical_invalidation": 98,
                "hard_stop": 97, "target_1": 106, "target_2": 110,
                "net_rr_1": 2.0, "net_rr_2": 3.5,
            },
            "reasons": [], "evidence": [], "risk_flags": [],
        }]
    })
    assert "Durum: REJECT" in text
    assert "eski" in text


def test_long_candidates_carry_the_negative_replay_and_regime_rejects():
    snapshot = _snapshot()
    snapshot["generated_at"] = int(bot.time.time()) - 5
    text = bot.format_longs(snapshot)
    assert "Durum: REJECT — geçmiş test NEGATIVE" in text
    assert "İlk 3 → 4 saat sonra sepete göre" in text
    assert "kalibre edilmemiş" in text
    snapshot["liquid_market_context"] = {"regime": "RISK_OFF", "positive_breadth_pct": 20}
    assert "Durum: REJECT — rejim RISK_OFF" in bot.format_longs(snapshot)


def test_health_needs_a_fresh_new_coins_scan_and_hides_its_error_text(monkeypatch):
    monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: True)
    saved = _preserve_state(*HEALTH_KEYS, "trend_loop_error", "new_coins", "new_coins_error")
    now = int(bot.time.time())
    try:
        bot.STATE.update({"snapshot": {"generated_at": now - 10}, "tactical_snapshot": {"generated_at": now - 10},
                          "last_error": None, "tactical_last_error": None, "trend_loop_error": None,
                          "new_coins": None, "new_coins_error": "CMC_API_KEY tanımlı değil"})
        payload = bot.APP.test_client().get("/").get_json()
        assert payload["ok"] is False and payload["new_coins_fresh"] is False
        bot.STATE.update({"new_coins": {"generated_at": now - 10}, "new_coins_error": None})
        assert bot.APP.test_client().get("/").get_json()["ok"] is True
        bot.STATE["new_coins_error"] = "NewCoinsDataError: cmc http 429 https://x?key=SECRET"
        payload = bot.APP.test_client().get("/").get_json()
        assert payload["ok"] is False and payload["new_coins_last_error"] == "NewCoinsDataError"
        assert "SECRET" not in json.dumps(payload)
    finally:
        bot.STATE.update(saved)
