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


def test_persistent_keyboard_is_human_readable_and_risk_first():
    assert bot.KEYBOARD_ROWS == (
        ("📊 Durum", "📂 Pozisyon"),
        ("🚀 MEXC Fırsat", "👀 Takip"),
        ("🧭 Radar", "✅ Onaylar"),
        ("☰ Diğer",),
    )
    assert bot.MEXC_KEYBOARD_ROWS[-1] == ("⬅️ Ana Menü",)
    assert bot.MORE_KEYBOARD_ROWS[-1] == ("⬅️ Ana Menü",)
    labels = [button["text"] for row in bot.MAIN_KEYBOARD["keyboard"] for button in row]
    assert all(not label.startswith("/") for label in labels)
    assert bot.MAIN_KEYBOARD["is_persistent"] is True
    assert len(labels) == 7


def test_keyboard_labels_and_slash_commands_share_one_router():
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
    supported = {
        "STATUS", "POSITIONS", "MEXC_MENU", "WATCHLIST", "RADAR",
        "APPROVALS", "MORE", "LISTINGS", "FILTERED_LISTINGS",
        "MEXC_REFRESH", "REGIME", "EQUITY", "REPORT", "HELP", "MAIN_MENU",
    }
    rows = (*bot.KEYBOARD_ROWS, *bot.MEXC_KEYBOARD_ROWS, *bot.MORE_KEYBOARD_ROWS)
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


def test_more_and_main_menu_buttons_replace_the_keyboard(monkeypatch):
    sent = []
    current_text = {"value": "☰ Diğer"}
    monkeypatch.setattr(bot, "TOKEN", "fixture-token")
    monkeypatch.setattr(bot, "CHAT_ID", "123")
    monkeypatch.setattr(bot, "now_ts", lambda: 100)
    monkeypatch.setattr(
        bot,
        "_telegram_get_updates",
        lambda _state: [{"message": {"chat": {"id": 123}, "text": current_text["value"]}}],
    )
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda text, **kwargs: sent.append((text, kwargs.get("reply_markup"))) or True,
    )

    bot._process_telegram_commands_locked(_MenuState())
    assert sent[-1][1] == bot.MORE_KEYBOARD

    current_text["value"] = "⬅️ Ana Menü"
    bot._process_telegram_commands_locked(_MenuState())
    assert sent[-1][1] == bot.MAIN_KEYBOARD


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


def test_filtered_candidates_get_one_tap_watch_buttons():
    state = _MenuState()
    snapshot = {
        "listing_filtered_candidates": [
            {"symbol": "NOVAUSDT", "score": 48, "metadata": {}},
        ]
    }

    keyboard = bot._filtered_watch_keyboard(snapshot, state)

    assert keyboard == {
        "inline_keyboard": [[{
            "text": "👀 NOVA takibe al",
            "callback_data": "WATCH_ADD NOVAUSDT",
        }]]
    }


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
