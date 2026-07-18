from __future__ import annotations

import bot


class _MenuState:
    def __init__(self):
        self.meta = {}

    def get_meta(self, key, default=None):
        return self.meta.get(key, default)

    def set_meta(self, key, value):
        self.meta[key] = value


def test_persistent_keyboard_is_human_readable_and_risk_first():
    assert bot.KEYBOARD_ROWS == (
        ("📊 Durum", "📂 Pozisyon"),
        ("🆕 MEXC", "🧭 Radar"),
        ("✅ Onaylar", "☰ Diğer"),
    )
    assert bot.MORE_KEYBOARD_ROWS[-1] == ("⬅️ Ana Menü",)
    labels = [button["text"] for row in bot.MAIN_KEYBOARD["keyboard"] for button in row]
    assert all(not label.startswith("/") for label in labels)
    assert bot.MAIN_KEYBOARD["is_persistent"] is True
    assert len(labels) == 6


def test_keyboard_labels_and_slash_commands_share_one_router():
    assert bot._resolve_telegram_command("🆕 MEXC") == "LISTINGS"
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
        "STATUS", "POSITIONS", "LISTINGS", "RADAR", "APPROVALS", "MORE",
        "REGIME", "EQUITY", "REPORT", "HELP", "MAIN_MENU",
    }
    rows = (*bot.KEYBOARD_ROWS, *bot.MORE_KEYBOARD_ROWS)
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
    assert {"listings", "radar", "approvals", "positions"}.issubset(commands)
