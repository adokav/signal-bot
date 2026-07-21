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
    assert bot._resolve_telegram_command("💧 Likit 100 Long") == "LIQUID_LONG"
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

    assert [button["text"] for button in buttons[:7]] == [
        "💧 Likit 100 · Long İlk 5 (0)",
        "🆕 Yeni · Güçlü 1", "🟡 Yeni · İzleme 2",
        "🧬 Temel 0", "📣 Sosyal 0", "⭐ Takibim 1", "🧭 Piyasa",
    ]
    assert buttons[-1]["callback_data"] == "DASHBOARD"


def test_botfather_menu_exposes_listing_and_approval_centers():
    commands = {item["command"] for item in bot.BOT_COMMANDS}
    assert {
        "listings", "filtered", "watch", "watch_add", "watch_remove",
        "radar", "social", "fundamentals", "approvals", "positions", "longs",
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
    state.meta["unified_radar_snapshot"] = {
        "listing_filtered_candidates": [{
            "symbol": "NOVAUSDT",
            "score": 48,
            "stage": "WATCH",
            "metadata": {},
        }]
    }
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


def test_old_liquid_coin_cannot_be_injected_into_new_listing_watchlist(monkeypatch):
    state = _MenuState()
    state.meta["unified_radar_snapshot"] = {
        "liquid_long_candidates": [{"symbol": "AVAXUSDT", "metadata": {}}]
    }
    monkeypatch.setattr(bot, "_get_unified_runtime", lambda: None)

    ok, message = bot.add_mexc_watch("AVAX", state)

    assert ok is False
    assert "Yeni Listeleme" in message
    assert bot._mexc_watchlist(state) == {}


def test_liquid_long_card_exposes_score_horizons_and_all_supply_fields():
    state = _MenuState()
    candidate = {
        "source": "LIQUID_100_LONG_RADAR",
        "symbol": "AVAXUSDT",
        "score": 82,
        "stage": "A_PLUS",
        "metadata": {
            "liquidity_rank": 12,
            "quote_volume": 50_000_000,
            "change_pct": 4,
            "technical_score": 86,
            "supply_score": 80,
            "spread_bps": 4,
            "technical_components": {
                "trend": 25,
                "momentum": 18,
                "participation": 14,
                "liquidity": 13,
                "risk_fomo": 16,
            },
            "long_metrics": {
                "change_1h_pct": 1,
                "change_4h_pct": 3,
                "ema20": 26,
                "ema50": 25,
                "ema20_distance_pct": 2,
                "rsi14": 58,
                "atr_pct": 2,
                "volume_ratio": 1.8,
            },
            "market_context": {
                "regime": "RISK_ON",
                "positive_breadth_pct": 65,
                "btc_change_pct": 2,
            },
            "fundamentals": {
                "status": "READY",
                "circulating_supply": 400_000_000,
                "total_supply": 700_000_000,
                "max_supply": 720_000_000,
                "circulation_pct": 55.6,
                "market_cap_to_fdv_pct": 55.6,
            },
        },
    }
    state.meta["unified_radar_snapshot"] = {
        "liquid_long_candidates": [candidate]
    }

    text = bot._liquid_long_detail_text("AVAX", state)
    keyboard = bot._liquid_long_keyboard(state.meta["unified_radar_snapshot"])

    assert "1s %+1.0" in text and "4s %+3.0" in text
    assert "Dolaşan arz: 400.00M" in text
    assert "Toplam arz: 700.00M" in text
    assert "Azami arz: 720.00M" in text
    assert keyboard["inline_keyboard"][0][0]["callback_data"] == "LIQUID_DETAIL AVAXUSDT"


def test_filtered_candidates_open_detail_instead_of_exposing_actions_in_list():
    state = _MenuState()
    snapshot = {
        "listing_filtered_candidates": [
            {"symbol": "NOVAUSDT", "score": 48, "stage": "WEAK", "metadata": {}},
        ]
    }

    keyboard = bot._filtered_watch_keyboard(snapshot, state)

    assert keyboard["inline_keyboard"][0] == [{
        "text": "⚪ NOVA · Fırsat 48 · Temel ?",
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
            "text": "🔥 NOVA · Fırsat 72 · Temel ?",
            "callback_data": "CANDIDATE NOVAUSDT L",
        }],
        [{
            "text": "⭐ ALPHA · Fırsat 80 · Temel ?",
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


def test_social_radar_exposes_viral_authenticity_and_sources_without_trade_actions():
    state = _MenuState()
    candidate = {
        "symbol": "NOVAUSDT",
        "score": 72,
        "stage": "BUILDING",
        "metadata": {
            "social": {
                "status": "READY",
                "stage": "EMERGING",
                "community_gate": "PASS",
                "viral_potential": 78,
                "attention_score": 86,
                "sentiment_score": 34,
                "manipulation_risk": 12,
                "crowding_risk": 18,
                "mentions_window": 42,
                "unique_authors_window": 31,
                "window_hours": 6,
                "bullish_pct": 74,
                "bearish_pct": 26,
                "neutral_count": 4,
                "coverage_pct": 90,
                "community_coverage_pct": 60,
                "news_coverage_pct": 30,
                "community_platforms": ["X"],
                "platforms": ["NEWS", "X"],
                "reasons": ["1s mention ivmesi 4.0x", "2 bağımsız kanal"],
                "sources": [{"title": "Nova launch", "url": "https://example.com/nova"}],
            },
        },
    }
    snapshot = {
        "listing_candidates": [candidate],
        "listing_filtered_candidates": [],
        "social_candidates": [candidate],
    }
    state.meta["unified_radar_snapshot"] = snapshot

    radar_text = bot._format_social_radar(snapshot)
    detail_text = bot._social_detail_text("NOVA", state)
    keyboard = bot._social_detail_keyboard("NOVA", state)
    callbacks = [
        button.get("callback_data", "")
        for row in keyboard["inline_keyboard"]
        for button in row
    ]

    assert "NOVA — 78/100" in radar_text
    assert "✅ PASS" in radar_text
    assert "Manipülasyon 12" in radar_text
    assert "Fenomen potansiyeli: 78/100" in detail_text
    assert "Boğa/Ayı/Nötr: %74/%26/4 görüş" in detail_text
    assert keyboard["inline_keyboard"][0][0]["url"] == "https://example.com/nova"
    assert not any("BUY" in value or "SELL" in value or "LIVE" in value for value in callbacks)


def test_fundamental_radar_exposes_supply_dilution_and_source_without_trade_actions():
    state = _MenuState()
    candidate = {
        "symbol": "NOVAUSDT",
        "score": 72,
        "stage": "BUILDING",
        "metadata": {
            "fundamentals": {
                "status": "READY",
                "stage": "HEALTHY",
                "fundamental_score": 68,
                "identity_confidence": 96,
                "coverage_pct": 86,
                "name": "Nova Protocol",
                "market_cap_rank": 700,
                "market_cap_usd": 20_000_000,
                "fully_diluted_valuation_usd": 40_000_000,
                "total_volume_usd": 4_000_000,
                "volume_to_market_cap_pct": 20,
                "circulation_pct": 50,
                "market_cap_to_fdv_pct": 50,
                "circulating_supply": 50_000_000,
                "total_supply": 100_000_000,
                "max_supply": 100_000_000,
                "ath_change_pct": -35,
                "reasons": ["dolaşım/FDV oranı %50"],
                "risk_flags": ["token açılım takvimi ayrıca doğrulanmalı"],
                "source_url": "https://www.coingecko.com/en/coins/nova-protocol",
            },
        },
    }
    snapshot = {
        "listing_candidates": [candidate],
        "listing_filtered_candidates": [],
        "fundamental_candidates": [candidate],
    }
    state.meta["unified_radar_snapshot"] = snapshot

    radar_text = bot._format_fundamental_radar(snapshot)
    detail_text = bot._fundamental_detail_text("NOVA", state)
    keyboard = bot._fundamental_detail_keyboard("NOVA", state)
    list_keyboard = bot._passed_watch_keyboard(snapshot, state)
    callbacks = [
        button.get("callback_data", "")
        for row in keyboard["inline_keyboard"]
        for button in row
    ]

    assert "NOVA — 68/100" in radar_text
    assert "Dolaşım %50.0" in radar_text
    assert "Piyasa değeri: $20.00M" in detail_text
    assert "Piyasa değeri / FDV: %50.0" in detail_text
    assert "Fırsat 72 · Temel 68" in list_keyboard["inline_keyboard"][0][0]["text"]
    assert keyboard["inline_keyboard"][0][0]["url"].endswith("nova-protocol")
    assert not any("BUY" in value or "SELL" in value or "LIVE" in value for value in callbacks)


def test_all_generated_callback_payloads_fit_telegram_limit():
    state = _MenuState()
    pair = f"{'X' * 40}USDT"
    snapshot = {
        "listing_filtered_candidates": [{
            "symbol": pair,
            "score": 10,
            "stage": "WEAK",
            "metadata": {"fundamentals": {"status": "DATA_PENDING"}},
        }],
        "fundamental_candidates": [{
            "symbol": pair,
            "score": 10,
            "stage": "WEAK",
            "metadata": {
                "fundamentals": {"status": "READY", "fundamental_score": 50},
            },
        }],
    }
    state.meta["unified_radar_snapshot"] = snapshot
    keyboards = [
        bot._filtered_watch_keyboard(snapshot, state),
        bot._candidate_detail_keyboard(pair, state, origin="F99"),
        bot._fundamental_radar_keyboard(snapshot, state),
        bot._fundamental_detail_keyboard(pair, state, origin="F99"),
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
    fundamental_detail = bot._fundamental_detail_keyboard(
        "COIN5USDT", state, origin="F1"
    )
    social_detail = bot._social_detail_keyboard("COIN5USDT", state, origin="F1")

    assert first["inline_keyboard"][5][-1]["callback_data"] == "FILTERED_LISTINGS 1"
    assert second["inline_keyboard"][0][0]["callback_data"] == "CANDIDATE COIN5USDT F1"
    assert detail["inline_keyboard"][-2][0]["callback_data"] == "FILTERED_LISTINGS 1"
    assert fundamental_detail["inline_keyboard"][0][0]["callback_data"] == "CANDIDATE COIN5USDT F1"
    assert social_detail["inline_keyboard"][0][0]["callback_data"] == "CANDIDATE COIN5USDT F1"


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
                "metadata": {
                    "filter_status": "FILTERED",
                    "social": {
                        "status": "INSUFFICIENT_DATA",
                        "stage": "INSUFFICIENT_DATA",
                        "community_gate": "WAIT",
                    },
                },
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
                "social": {
                    "status": "READY",
                    "stage": "EMERGING",
                    "community_gate": "PASS",
                    "viral_potential": 68,
                    "window_hours": 6,
                    "mentions_window": 8,
                    "unique_authors_window": 6,
                    "bullish_pct": 75,
                    "bearish_pct": 25,
                },
            },
        }],
        "listing_filtered_candidates": [],
    }

    bot._sync_mexc_watchlist_state(snapshot, state)
    bot._sync_mexc_watchlist_state(snapshot, state)

    assert len(sent) == 1
    assert "TOPLULUK TEYİTLİ" in sent[0][0]
    assert "6 saat görüş/yazar: 8/6" in sent[0][0]
    assert sent[0][1]["keyboard"] is False


def test_watched_candidate_does_not_alert_without_community_confirmation(monkeypatch):
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
                "social": {
                    "status": "COMMUNITY_SOURCE_MISSING",
                    "stage": "DATA_PENDING",
                    "community_gate": "UNAVAILABLE",
                    "viral_potential": None,
                },
            },
        }],
        "listing_filtered_candidates": [],
    }

    bot._sync_mexc_watchlist_state(snapshot, state)

    assert sent == []
    entry = state.meta["mexc_manual_watchlist"]["NOVAUSDT"]
    assert entry.get("last_alert_signature") is None


def test_community_reconfirmation_respects_cooldown_then_can_alert_again(monkeypatch):
    state = _MenuState()
    state.meta["mexc_manual_watchlist"] = {
        "NOVAUSDT": {
            "added_at": 1,
            "last_alert_at": 1_700_000_000,
            "last_alert_signature": "old",
            "last_candidate": {
                "symbol": "NOVAUSDT",
                "score": 70,
                "stage": "BUILDING",
                "metadata": {
                    "filter_status": "PASSED",
                    "social": {"community_gate": "WAIT"},
                },
            },
        }
    }
    sent = []
    monkeypatch.setattr(bot, "now_ts", lambda: 1_700_030_000)
    monkeypatch.setattr(
        bot,
        "send_message",
        lambda text, **kwargs: sent.append(text) or True,
    )

    def snapshot(generated_at, gate):
        return {
            "generated_at": generated_at,
            "listing_candidates": [{
                "symbol": "NOVAUSDT",
                "score": 72,
                "stage": "BUILDING",
                "metadata": {
                    "filter_status": "PASSED",
                    "change_pct": 4.0,
                    "volume_acceleration": 1.8,
                    "social": {
                        "status": "READY" if gate == "PASS" else "INSUFFICIENT_DATA",
                        "stage": "EMERGING" if gate == "PASS" else "INSUFFICIENT_DATA",
                        "community_gate": gate,
                        "viral_potential": 64 if gate == "PASS" else None,
                        "mentions_window": 7,
                        "unique_authors_window": 5,
                    },
                },
            }],
            "listing_filtered_candidates": [],
        }

    # Reconfirmation inside six hours is deliberately muted.
    bot._sync_mexc_watchlist_state(snapshot(1_700_000_100, "PASS"), state)
    assert sent == []
    bot._sync_mexc_watchlist_state(snapshot(1_700_000_200, "WAIT"), state)

    # A fresh WAIT -> PASS transition after cooldown is eligible again.
    bot._sync_mexc_watchlist_state(snapshot(1_700_022_000, "PASS"), state)
    assert len(sent) == 1
    assert "TOPLULUK TEYİTLİ" in sent[0]
