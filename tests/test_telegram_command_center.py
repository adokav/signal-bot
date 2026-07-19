from __future__ import annotations

import bot


class _MenuState:
    def __init__(self):
        self.meta = {}

    def get_meta(self, key, default=None):
        return self.meta.get(key, default)

    def set_meta(self, key, value):
        self.meta[key] = value

    def save(self):
        return None

    def snapshot(self):
        return {"meta": self.meta, "trades": {}}


def test_persistent_keyboard_has_only_four_stable_sections():
    assert bot.KEYBOARD_ROWS == (
        ("🏠 Panel", "🔎 Fırsatlar"),
        ("📂 Pozisyonlar", "🛡️ Güvenlik"),
    )
    labels = [button["text"] for row in bot.MAIN_KEYBOARD["keyboard"] for button in row]
    assert all(not label.startswith("/") for label in labels)
    assert bot.MAIN_KEYBOARD["is_persistent"] is True
    assert len(labels) == 4


def test_keyboard_labels_and_slash_commands_share_one_router():
    assert bot._resolve_telegram_command("🏠 Panel") == "DASHBOARD"
    assert bot._resolve_telegram_command("🔎 Fırsatlar") == "MEXC_MENU"
    assert bot._resolve_telegram_command("🛡️ Güvenlik") == "SAFETY"
    assert bot._resolve_telegram_command("🔥 Güçlü Adaylar") == "LISTINGS"
    assert bot._resolve_telegram_command("🟡 İzleme Havuzu") == "FILTERED_LISTINGS"
    assert bot._resolve_telegram_command("🆕 MEXC") == "LISTINGS"
    assert bot._resolve_telegram_command("🚀 MEXC Fırsat") == "MEXC_MENU"
    assert bot._resolve_telegram_command("🟡 Filtredekiler") == "FILTERED_LISTINGS"
    assert bot._resolve_telegram_command("👀 Takip") == "WATCHLIST"
    assert bot._resolve_telegram_command("✅ Onaylar") == "APPROVALS"
    assert bot._resolve_telegram_command("☰ Diğer") == "MORE"
    assert bot._resolve_telegram_command("⬅ Ana Menü") == "MAIN_MENU"
    assert bot._resolve_telegram_command("/trade_accept TRD-1") == "TRADE_ACCEPT"


def test_old_keyboard_labels_remain_compatible_after_deploy():
    assert bot._resolve_telegram_command("🧠 Durum") == "STATUS"
    assert bot._resolve_telegram_command("📂 Pozisyonlar") == "POSITIONS"
    assert bot._resolve_telegram_command("🆕 Yeni Liste") == "LISTINGS"
    assert bot._resolve_telegram_command("✅ Onay Merkezi") == "APPROVALS"


def test_every_visible_button_routes_to_a_supported_action():
    supported = {"DASHBOARD", "MEXC_MENU", "POSITIONS", "SAFETY"}
    rows = bot.KEYBOARD_ROWS
    resolved = {
        bot._resolve_telegram_command(label)
        for row in rows
        for label in row
    }
    assert resolved == supported


def test_command_listener_is_interactive_by_default():
    assert bot.TELEGRAM_COMMAND_POLL_INTERVAL_SECONDS <= 5


def test_command_failure_returns_feedback_instead_of_silently_dropping(monkeypatch):
    sent = []

    def fail(_state_mgr):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(bot, "_process_telegram_commands_locked", fail)
    monkeypatch.setattr(bot, "send_message", lambda text, **_kwargs: sent.append(text) or True)

    bot.process_telegram_commands(object())

    assert sent and "geçici bir hata" in sent[0]


def test_opportunity_button_keeps_stable_keyboard_and_opens_inline_center(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "TOKEN", "fixture-token")
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    monkeypatch.setattr(bot, "now_ts", lambda: 100)
    monkeypatch.setattr(
        bot,
        "_telegram_get_updates",
        lambda _state: [{"message": {"chat": {"id": 123}, "text": "🔎 Fırsatlar"}}],
    )
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda text, **kwargs: sent.append((text, kwargs.get("reply_markup"))) or True,
    )

    bot._process_telegram_commands_locked(_MenuState())
    assert "inline_keyboard" in sent[-1][1]
    assert bot.KEYBOARD_ROWS == (
        ("🏠 Panel", "🔎 Fırsatlar"),
        ("📂 Pozisyonlar", "🛡️ Güvenlik"),
    )


def test_inline_centers_use_dynamic_counts_and_clear_navigation():
    state = _MenuState()
    state.meta["mexc_manual_watchlist"] = {"NOVAUSDT": {"added_at": 1}}
    snapshot = {
        "listing_candidates": [{"symbol": "HOTUSDT"}],
        "listing_filtered_candidates": [{"symbol": "WAITUSDT"}, {"symbol": "NEWUSDT"}],
    }

    keyboard = bot._mexc_center_keyboard(snapshot, state)
    buttons = [button for row in keyboard["inline_keyboard"] for button in row]

    assert [button["text"] for button in buttons[:4]] == [
        "🔥 Güçlü 1", "🟡 İzleme 2", "⭐ Takibim 1", "🧭 Piyasa",
    ]
    assert buttons[-1]["callback_data"] == "DASHBOARD"


def test_botfather_menu_exposes_listing_and_approval_centers():
    commands = {item["command"] for item in bot.BOT_COMMANDS}
    assert {
        "listings", "filtered", "watch", "watch_add", "watch_remove",
        "radar", "approvals", "positions",
    }.issubset(commands)


def test_callback_button_is_routed_like_a_command(monkeypatch):
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    update = {
        "callback_query": {
            "id": "cb-1",
            "data": "WATCH_ADD NOVAUSDT",
            "message": {"chat": {"id": 123}},
        }
    }

    assert bot._extract_command_text(update) == ("123", "WATCH_ADD NOVAUSDT")


def test_inline_navigation_edits_existing_message_instead_of_spamming_chat(monkeypatch):
    calls = []

    class _Response:
        status_code = 200
        text = "OK"

    class _Session:
        def post(self, url, data=None, timeout=15):
            calls.append((url, data, timeout))
            return _Response()

    monkeypatch.setattr(bot, "TOKEN", "fixture-token")
    monkeypatch.setattr(bot, "_http_session", lambda: _Session())
    update = {
        "callback_query": {
            "message": {
                "message_id": 77,
                "chat": {"id": 123},
            }
        }
    }

    edited = bot._edit_callback_message(
        update,
        "Yeni ekran",
        reply_markup=bot._dashboard_keyboard(),
    )

    assert edited is True
    assert calls[0][0].endswith("/editMessageText")
    assert calls[0][1]["message_id"] == 77
    assert "inline_keyboard" in calls[0][1]["reply_markup"]


def test_safety_center_has_no_one_tap_live_or_auto_enable_action():
    callbacks = [
        button["callback_data"]
        for row in bot._safety_center_keyboard()["inline_keyboard"]
        for button in row
    ]

    assert {"AUTOMATION_INFO", "APPROVALS", "STATUS", "DASHBOARD"} == set(callbacks)
    assert not any("ENABLE" in value or "LIVE" in value for value in callbacks)


def test_watchlist_add_and_remove_are_persistent_and_non_executable(monkeypatch):
    state = _MenuState()
    monkeypatch.setattr(bot, "_get_unified_runtime", lambda: None)
    monkeypatch.setattr(bot, "now_ts", lambda: 1_700_000_000)

    ok, added = bot.add_mexc_watch("nova", state)

    assert ok is True
    assert "emir yetkisi vermez" in added
    assert set(bot._mexc_watchlist(state)) == {"NOVAUSDT"}

    ok, removed = bot.remove_mexc_watch("NOVAUSDT", state)

    assert ok is True
    assert "çıkarıldı" in removed
    assert bot._mexc_watchlist(state) == {}


def test_filtered_candidates_open_detail_instead_of_exposing_actions_in_list():
    state = _MenuState()
    snapshot = {
        "listing_filtered_candidates": [
            {"symbol": "NOVAUSDT", "score": 48, "stage": "WEAK", "metadata": {}},
        ]
    }

    keyboard = bot._filtered_watch_keyboard(snapshot, state)

    assert keyboard["inline_keyboard"][0] == [{
        "text": "⚪ NOVA · 48/100",
        "callback_data": "CANDIDATE NOVAUSDT F",
    }]
    assert keyboard["inline_keyboard"][-1][0]["callback_data"] == "DASHBOARD"


def test_passed_candidates_show_score_and_watch_marker_before_detail():
    state = _MenuState()
    snapshot = {
        "listing_candidates": [
            {"symbol": "NOVAUSDT", "score": 72, "stage": "HOT", "metadata": {}},
            {"symbol": "ALPHAUSDT", "score": 80, "stage": "BUILDING", "metadata": {}},
        ]
    }
    state.meta["mexc_manual_watchlist"] = {
        "ALPHAUSDT": {"added_at": 1},
    }

    keyboard = bot._passed_watch_keyboard(snapshot, state)

    assert keyboard["inline_keyboard"][:2] == [
        [{
            "text": "🔥 NOVA · 72/100",
            "callback_data": "CANDIDATE NOVAUSDT L",
        }],
        [{
            "text": "⭐ ALPHA · 80/100",
            "callback_data": "CANDIDATE ALPHAUSDT L",
        }],
    ]


def test_candidate_detail_separates_observation_from_future_execution():
    state = _MenuState()
    state.meta["unified_radar_snapshot"] = {
        "listing_candidates": [{
            "symbol": "NOVAUSDT",
            "score": 76,
            "stage": "HOT",
            "metadata": {
                "filter_status": "PASSED",
                "volume_acceleration": 2.5,
                "change_pct": 9.0,
                "spread_bps": 24,
                "quote_volume": 3_000_000,
                "last_price": 0.42,
            },
        }]
    }

    text = bot._mexc_candidate_detail_text("NOVA", state)
    keyboard = bot._candidate_detail_keyboard("NOVA", state, origin="LISTINGS")
    callbacks = [
        button["callback_data"]
        for row in keyboard["inline_keyboard"]
        for button in row
    ]

    assert "NOVA ADAY DETAYI" in text
    assert "76/100" in text and "24 bps" in text
    assert "emir oluşturmaz" in text
    assert callbacks[0] == "WATCH_ADD NOVAUSDT L"
    assert not any("BUY" in value or "SELL" in value or "LIVE" in value for value in callbacks)


def test_all_generated_callback_payloads_fit_telegram_limit():
    state = _MenuState()
    pair = f"{'X' * 40}USDT"
    snapshot = {
        "listing_filtered_candidates": [{
            "symbol": pair,
            "score": 10,
            "stage": "WEAK",
            "metadata": {},
        }]
    }
    keyboards = [
        bot._filtered_watch_keyboard(snapshot, state),
        bot._candidate_detail_keyboard(pair, state, origin="FILTERED_LISTINGS"),
        bot._mexc_center_keyboard(snapshot, state),
        bot._dashboard_keyboard(),
        bot._safety_center_keyboard(),
    ]

    payloads = [
        button["callback_data"]
        for keyboard in keyboards
        for row in keyboard["inline_keyboard"]
        for button in row
    ]

    assert all(len(value.encode("utf-8")) <= 64 for value in payloads)


def test_candidate_lists_are_paginated_and_detail_returns_to_same_page():
    state = _MenuState()
    snapshot = {
        "listing_filtered_candidates": [
            {
                "symbol": f"COIN{index}USDT",
                "score": 40 + index,
                "stage": "WEAK",
                "metadata": {},
            }
            for index in range(7)
        ]
    }

    first = bot._filtered_watch_keyboard(snapshot, state, page=0)
    second = bot._filtered_watch_keyboard(snapshot, state, page=1)
    detail = bot._candidate_detail_keyboard("COIN5USDT", state, origin="F1")

    assert first["inline_keyboard"][5][-1]["callback_data"] == "FILTERED_LISTINGS 1"
    assert second["inline_keyboard"][0][0]["callback_data"] == "CANDIDATE COIN5USDT F1"
    assert detail["inline_keyboard"][-2][0]["callback_data"] == "FILTERED_LISTINGS 1"


def test_watch_status_reports_current_passed_candidate_without_readding():
    state = _MenuState()
    state.meta["mexc_manual_watchlist"] = {
        "NOVAUSDT": {"added_at": 1},
    }
    state.meta["unified_radar_snapshot"] = {
        "listing_candidates": [{
            "symbol": "NOVAUSDT",
            "score": 76,
            "stage": "HOT",
            "metadata": {
                "filter_status": "PASSED",
                "volume_acceleration": 2.5,
                "change_pct": 9.0,
            },
        }]
    }

    message = bot._mexc_watch_status("NOVA", state)

    assert "NOVAUSDT — 76/100" in message
    assert "Eşiği geçti" in message
    assert "Takip alarmı aktiftir" in message


def test_watched_candidate_alerts_once_when_it_clears_the_filter(monkeypatch):
    state = _MenuState()
    state.meta["mexc_manual_watchlist"] = {
        "NOVAUSDT": {
            "added_at": 1,
            "last_candidate": {
                "symbol": "NOVAUSDT",
                "score": 48,
                "stage": "WEAK",
                "metadata": {"filter_status": "FILTERED"},
            },
        }
    }
    sent = []
    monkeypatch.setattr(bot, "now_ts", lambda: 1_700_000_100)
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda text, **kwargs: sent.append((text, kwargs)) or True,
    )
    snapshot = {
        "generated_at": 1_700_000_100,
        "listing_candidates": [{
            "symbol": "NOVAUSDT",
            "score": 72,
            "stage": "BUILDING",
            "metadata": {
                "filter_status": "PASSED",
                "change_pct": 8.0,
                "volume_acceleration": 2.2,
            },
        }],
        "listing_filtered_candidates": [],
    }

    bot._sync_mexc_watchlist_state(snapshot, state)
    bot._sync_mexc_watchlist_state(snapshot, state)

    assert len(sent) == 1
    assert "ISINIYOR" in sent[0][0]
    assert sent[0][1]["keyboard"] is False
