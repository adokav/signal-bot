from __future__ import annotations

import bot


def test_persistent_keyboard_is_human_readable_and_risk_first():
    assert bot.KEYBOARD_ROWS[0] == ("🧠 Durum", "📂 Pozisyonlar")
    assert bot.KEYBOARD_ROWS[1] == ("🆕 Yeni Liste", "🧭 Fırsat Radarı")
    labels = [button["text"] for row in bot.MAIN_KEYBOARD["keyboard"] for button in row]
    assert all(not label.startswith("/") for label in labels)
    assert bot.MAIN_KEYBOARD["is_persistent"] is True


def test_keyboard_labels_and_slash_commands_share_one_router():
    assert bot._resolve_telegram_command("🆕 Yeni Liste") == "LISTINGS"
    assert bot._resolve_telegram_command("✅ Onay Merkezi") == "APPROVALS"
    assert bot._resolve_telegram_command("/trade_accept TRD-1") == "TRADE_ACCEPT"


def test_botfather_menu_exposes_listing_and_approval_centers():
    commands = {item["command"] for item in bot.BOT_COMMANDS}
    assert {"listings", "radar", "approvals", "positions"}.issubset(commands)
