from __future__ import annotations

import bot


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
        "listing_filtered_candidates": [
            {
                "symbol": "OLDUSDT",
                "score": 99,
                "stage": "FILTERED",
                "risk_flags": ["eski market"],
                "metadata": {"quote_volume": 1_000_000_000},
            }
        ],
        "liquid_market_context": {"regime": "RISK_ON", "positive_breadth_pct": 64},
    }


def test_public_commands_include_tactical_radar_without_legacy_bloat():
    assert [row["command"] for row in bot.COMMANDS] == [
        "panel", "tactical", "longs", "new", "status", "scan"
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


def test_new_listing_report_contains_only_accepted_enriched_rows():
    text = bot.format_new(_snapshot())
    assert "DOĞRULANMIŞ ADAYLAR" in text
    assert "NEWUSDT" in text
    assert "OLDUSDT" not in text
    assert "Sosyal kapı PASS" in text


def test_status_surfaces_last_scan_error():
    previous = bot.STATE.get("last_error")
    try:
        bot.STATE["last_error"] = "RuntimeError: provider down"
        text = bot.format_status(None)
        assert "RuntimeError: provider down" in text
    finally:
        bot.STATE["last_error"] = previous


def test_health_declares_no_trade_authority():
    previous = bot.STATE.get("last_error")
    tactical_previous = bot.STATE.get("tactical_last_error")
    try:
        bot.STATE["last_error"] = None
        bot.STATE["tactical_last_error"] = None
        payload = bot.APP.test_client().get("/").get_json()
        assert payload["ok"] is True
        assert payload["can_authorize_trade"] is False
        assert "last_error" in payload
        assert "tactical_last_error" in payload
    finally:
        bot.STATE["last_error"] = previous
        bot.STATE["tactical_last_error"] = tactical_previous
