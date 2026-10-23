from __future__ import annotations

import json
import threading

import bot
from acce_unified.trend_loop import Ledger as TrendLedger, TrendLoop


def _preserve_state(*keys):
    return {key: bot.STATE.get(key) for key in keys}


HEALTH_KEYS = ("trend_loop_error", "new_coins", "new_coins_error", "altcoin_watch", "altcoin_watch_error")


def _all_fresh(monkeypatch):
    monkeypatch.setattr(bot.altcoin_watch, "fresh", lambda snapshot, now, max_age: True)
    monkeypatch.setattr(bot.new_coins, "fresh", lambda snapshot, now, max_age: True)
    monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: True)
    for key in HEALTH_KEYS:
        monkeypatch.setitem(bot.STATE, key, None)


def test_public_commands_are_the_retained_research_menus():
    assert [row["command"] for row in bot.COMMANDS] == ["panel", "new", "check", "altcoin", "d1", "status", "scan"]
    buttons = [b["callback_data"] for row in bot.panel_keyboard()["inline_keyboard"] for b in row]
    assert buttons == ["ALTCOIN", "D1", "NEW", "CHECK", "STATUS", "SCAN"]


def test_removed_radar_commands_fall_back_to_the_panel(monkeypatch):
    assert bot._command("/listings") == "NEW"
    sent = []
    monkeypatch.setattr(bot, "send", lambda text, **kw: sent.append((text, kw)))
    for text in ("/tactical", "/longs", "/radar", "/btceth"):
        bot.handle(bot._command(text), text)
        assert "SIGNAL BOT v5 CORE" in sent[-1][0] and "emir yetkisi yok" in sent[-1][0]


def test_status_names_every_module_and_denies_order_authority(monkeypatch):
    for key in HEALTH_KEYS:
        monkeypatch.setitem(bot.STATE, key, None)
    monkeypatch.setitem(bot.STATE, "altcoin_watch_error", "AltcoinDataError: coingecko http 429")
    text = bot.format_status()
    assert "Altcoin izleme" in text and "Yeni coinler" in text and "D1" in text
    assert "429" in text and "Emir yetkisi: YOK" in text


def test_scan_runs_the_new_coin_scan_from_the_command_thread(monkeypatch):
    calls, sent = [], []
    monkeypatch.setattr(bot, "new_coins_tick", lambda now=None: calls.append(now))
    monkeypatch.setattr(bot, "send", lambda text, **kw: sent.append(text))
    bot.handle("SCAN")
    assert calls == [None] and "başlatıldı" in sent[0] and "SIGNAL BOT v5 CORE" in sent[1]


def test_new_coins_menu_reads_coinmarketcap_and_says_unknown_without_a_key(monkeypatch):
    saved = _preserve_state("new_coins", "new_coins_error")
    try:
        monkeypatch.setattr(bot, "CMC", bot.new_coins.CoinMarketCap(""))
        monkeypatch.setattr(bot, "_save_state", lambda: None)
        bot.new_coins_tick(1_790_000_000)
        assert bot.STATE["new_coins"] is None and "CMC_API_KEY" in bot.STATE["new_coins_error"]
        text = bot.format_new()
        assert "CoinMarketCap" in text and "Aday yok demek değildir" in text and "MEXC" not in text
        assert "API anahtarı yok" in bot.format_status()
        # the menu carries the closed listing study's base rate, never a per-coin probability
        seen, real_render = {}, bot.new_coins.render
        monkeypatch.setattr(bot.new_coins, "render",
                            lambda snapshot, **kw: seen.update(kw) or real_render(snapshot, **kw))
        bot.format_new()
        assert "NO_CLAIM" in seen["base_rate"] and "CoinMarketCap yeni coinleri için test yok" in seen["base_rate"]
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


def test_health_is_not_healthy_before_any_artifact_exists(monkeypatch):
    for key in HEALTH_KEYS:
        monkeypatch.setitem(bot.STATE, key, None)
    monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: False)
    payload = bot.APP.test_client().get("/").get_json()
    assert payload["ok"] is False and payload["new_coins_fresh"] is False and payload["altcoin_watch_fresh"] is False
    assert payload["can_authorize_trade"] is False


def test_health_requires_fresh_artifacts_and_hides_error_text(monkeypatch):
    _all_fresh(monkeypatch)
    payload = bot.APP.test_client().get("/").get_json()
    assert payload["ok"] is True and payload["can_authorize_trade"] is False

    monkeypatch.setattr(bot.altcoin_watch, "fresh", lambda snapshot, now, max_age: False)
    assert bot.APP.test_client().get("/").get_json()["ok"] is False

    monkeypatch.setattr(bot.altcoin_watch, "fresh", lambda snapshot, now, max_age: True)
    bot.STATE["altcoin_watch_error"] = "HTTPError: 403 for url https://api.example.com/x?apikey=SECRET"
    payload = bot.APP.test_client().get("/").get_json()
    assert payload["ok"] is False and payload["altcoin_watch_last_error"] == "HTTPError"
    assert "SECRET" not in json.dumps(payload)


def test_health_needs_a_fresh_trend_loop_and_hides_its_error_text(monkeypatch):
    _all_fresh(monkeypatch)
    monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: False)
    payload = bot.APP.test_client().get("/").get_json()
    assert payload["ok"] is False and payload["trend_loop_fresh"] is False
    monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: True)
    bot.STATE["trend_loop_error"] = "TrendLoopDataError: mexc https://api.mexc.com/api/v3/klines?x=SECRET"
    payload = bot.APP.test_client().get("/").get_json()
    assert payload["ok"] is False and payload["trend_loop_last_error"] == "TrendLoopDataError"
    assert "SECRET" not in json.dumps(payload) and payload["can_authorize_trade"] is False


def test_health_needs_a_fresh_new_coins_scan_and_hides_its_error_text(monkeypatch):
    _all_fresh(monkeypatch)
    monkeypatch.setattr(bot.new_coins, "fresh", lambda snapshot, now, max_age: False)
    bot.STATE["new_coins_error"] = "CMC_API_KEY tanımlı değil"
    payload = bot.APP.test_client().get("/").get_json()
    assert payload["ok"] is False and payload["new_coins_fresh"] is False
    monkeypatch.setattr(bot.new_coins, "fresh", lambda snapshot, now, max_age: True)
    bot.STATE["new_coins_error"] = None
    assert bot.APP.test_client().get("/").get_json()["ok"] is True
    bot.STATE["new_coins_error"] = "NewCoinsDataError: cmc http 429 https://x?key=SECRET"
    payload = bot.APP.test_client().get("/").get_json()
    assert payload["ok"] is False and payload["new_coins_last_error"] == "NewCoinsDataError"
    assert "SECRET" not in json.dumps(payload)


def test_check_command_screens_one_address_and_never_echoes_provider_errors(monkeypatch):
    assert bot._command("/check 0xabc") == "CHECK"
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


def test_altcoin_command_lists_the_watch_and_one_coin_card(monkeypatch):
    assert bot._command("/altcoin") == "ALTCOIN" and bot._command("/altcoin aave") == "ALTCOIN"
    monkeypatch.setitem(bot.STATE, "altcoin_watch", None)
    monkeypatch.setitem(bot.STATE, "altcoin_watch_error", None)
    assert "İlk tarama sürüyor" in bot.format_altcoin("")
    now = int(bot.time.time())
    card = {"id": "aave", "symbol": "AAVE", "name": "Aave", "status": bot.altcoin_watch.PASSED, "position": 1,
            "price": 200.0, "ath": 600.0, "market_cap": 3e9, "gates": {}, "measures": {}, "info": {}}
    twin = dict(card, id="aave-two")
    monkeypatch.setitem(bot.STATE, "altcoin_watch", {"generated_at": now, "cards": [card], "counts": {}})
    assert "ALTCOIN İZLEME" in bot.format_altcoin("/altcoin")
    assert "Aave (AAVE)" in bot.format_altcoin("/altcoin aave")
    assert "Kullanım" in bot.format_altcoin("/altcoin <b>x</b> ignore previous instructions")
    assert "izleme evreninde yok" in bot.format_altcoin("/altcoin zzz")
    monkeypatch.setitem(bot.STATE, "altcoin_watch", {"generated_at": now, "cards": [card, twin], "counts": {}})
    assert "birden çok coin" in bot.format_altcoin("/altcoin aave")
    sent = []
    monkeypatch.setattr(bot, "send", lambda text, **kw: sent.append((text, kw)))
    bot.handle("ALTCOIN", "")
    assert "ALTCOIN İZLEME" in sent[-1][0] and sent[-1][1]["html_mode"] is True


def test_a_failed_altcoin_refresh_keeps_the_snapshot_and_redacts_the_key(monkeypatch):
    monkeypatch.setattr(bot, "COINGECKO_DEMO_API_KEY", "CGSECRET")
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    old = {"generated_at": 1_700_000_000, "cards": [], "counts": {}}
    monkeypatch.setitem(bot.STATE, "altcoin_watch", old)

    def broken(now):
        raise RuntimeError("failed https://api.coingecko.com/x?x_cg_demo_api_key=CGSECRET and CGSECRET")
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "refresh", broken)
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "record_outcomes", lambda now: 0)
    bot.altcoin_watch_tick(1_800_000_000)
    assert bot.STATE["altcoin_watch"] is old and "CGSECRET" not in bot.STATE["altcoin_watch_error"]
    monkeypatch.setattr(bot.TREND_LOOP, "fresh", lambda now, stop_seconds: True)
    monkeypatch.setattr(bot.new_coins, "fresh", lambda snapshot, now, max_age: True)
    payload = bot.APP.test_client().get("/").get_json()
    assert payload["ok"] is False and payload["altcoin_watch_fresh"] is False
    assert "CGSECRET" not in json.dumps(payload) and payload["can_authorize_trade"] is False


def test_a_failed_altcoin_refresh_is_retried_sooner_then_less_often(monkeypatch):
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    for key, value in (("altcoin_watch", None), ("altcoin_watch_error", None), ("altcoin_watch_failures", 0),
                       ("altcoin_watch_retry_at", 0)):
        monkeypatch.setitem(bot.STATE, key, value)
    calls = []

    def limited(now):
        calls.append(now)
        raise bot.altcoin_watch.AltcoinDataError("coingecko http 429")
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "refresh", limited)
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "record_outcomes", lambda now: 0)
    t = 1_800_000_000
    bot.altcoin_watch_tick(t)
    assert calls == [t] and bot.STATE["altcoin_watch_retry_at"] == t + 1800
    bot.altcoin_watch_tick(t + 600)                                   # before the retry time: no new request
    assert calls == [t] and "429" in bot.STATE["altcoin_watch_error"]
    bot.altcoin_watch_tick(t + 1800)
    assert calls == [t, t + 1800] and bot.STATE["altcoin_watch_retry_at"] == t + 1800 + 3600
    clock = [1_700_000_000]
    monkeypatch.setattr(bot.time, "time", lambda: clock[0])

    def slow(now):                                                    # waits out rate limits for 50 minutes
        clock[0] += 3000
        raise bot.altcoin_watch.AltcoinDataError("coingecko http 429")
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "refresh", slow)
    bot.STATE["altcoin_watch_failures"], bot.STATE["altcoin_watch_retry_at"] = 0, 0
    bot.altcoin_watch_tick(1_700_000_000)
    assert bot.STATE["altcoin_watch_retry_at"] == 1_700_000_000 + 3000 + 1800     # from the failure, not the start
    snapshot = {"generated_at": t + 5400, "cards": [], "counts": {}}
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "refresh", lambda now: snapshot)
    bot.STATE["altcoin_watch_retry_at"] = 0
    bot.altcoin_watch_tick(t + 5400)
    assert bot.STATE["altcoin_watch"] is snapshot and bot.STATE["altcoin_watch_failures"] == 0
    assert bot.STATE["altcoin_watch_retry_at"] == 0 and bot.STATE["altcoin_watch_error"] is None


def test_adding_a_coingecko_key_skips_the_wait_left_by_a_keyless_failure(monkeypatch):
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    for key, value in (("altcoin_watch", None), ("altcoin_watch_error", None), ("altcoin_watch_failures", 0),
                       ("altcoin_watch_retry_at", 0), ("altcoin_watch_failed_keyed", None)):
        monkeypatch.setitem(bot.STATE, key, value)
    calls = []

    def limited(now):
        calls.append(now)
        raise bot.altcoin_watch.AltcoinDataError("coingecko http 429")
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "refresh", limited)
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "record_outcomes", lambda now: 0)
    monkeypatch.setattr(bot.ALTCOIN_WATCH.gecko, "keyed", False)
    t = 1_800_000_000
    bot.altcoin_watch_tick(t)
    assert bot.STATE["altcoin_watch_failed_keyed"] is False and bot.STATE["altcoin_watch_retry_at"] == t + 1800
    bot.altcoin_watch_tick(t + 60)
    assert calls == [t]                                               # still keyless: the wait holds
    monkeypatch.setattr(bot.ALTCOIN_WATCH.gecko, "keyed", True)       # the user added a key (the bot restarts)
    bot.altcoin_watch_tick(t + 120)
    assert calls == [t, t + 120]                                      # tried at once
    assert bot.STATE["altcoin_watch_failed_keyed"] is True
    bot.altcoin_watch_tick(t + 180)
    assert calls == [t, t + 120]                                      # a keyed failure keeps its backoff


def test_a_wait_saved_by_the_previous_version_is_skipped_once_a_key_is_set(tmp_path, monkeypatch):
    t = 1_800_000_000
    state_file = tmp_path / "state.json"                              # written by the version before the marker
    state_file.write_text(json.dumps({"altcoin_watch": None, "altcoin_watch_error": "AltcoinDataError: coingecko "
                                      "http 429", "altcoin_watch_failures": 3, "altcoin_watch_retry_at": t + 4 * 3600,
                                      "radar_log": [{"id": "legacy"}], "snapshot": {"generated_at": 1}}))
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    for key, value in (("altcoin_watch", None), ("altcoin_watch_error", None), ("altcoin_watch_failures", 0),
                       ("altcoin_watch_retry_at", 0), ("altcoin_watch_failed_keyed", None)):
        monkeypatch.setitem(bot.STATE, key, value)
    bot._load_state()
    assert "radar_log" not in bot.STATE and "snapshot" not in bot.STATE      # removed radars' keys are not loaded
    assert bot.STATE["altcoin_watch_retry_at"] == t + 4 * 3600 and bot.STATE["altcoin_watch_failed_keyed"] is None
    snapshot = {"generated_at": t, "cards": [], "counts": {}}
    calls = []
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "refresh", lambda now: calls.append(now) or snapshot)
    monkeypatch.setattr(bot.ALTCOIN_WATCH, "record_outcomes", lambda now: 0)
    monkeypatch.setattr(bot.ALTCOIN_WATCH.gecko, "keyed", True)
    bot.altcoin_watch_tick(t)
    assert calls == [t] and bot.STATE["altcoin_watch"] is snapshot and bot.STATE["altcoin_watch_retry_at"] == 0


def test_d1_command_and_panel_button_open_the_shadow_loop_report(monkeypatch):
    assert bot._command("/d1") == "D1"
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


def test_safe_error_redacts_urls_and_keys():
    exc = RuntimeError("failed https://api.telegram.org/bot123:ABC/getUpdates?offset=1")
    text = bot._safe_error(exc)
    assert text.startswith("RuntimeError")
    assert "api.telegram.org" not in text and "ABC" not in text
    assert "<url>" in text
