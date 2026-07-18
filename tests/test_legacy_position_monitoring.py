from __future__ import annotations

import bot


class _FakeState:
    def __init__(self, trades):
        self._trades = trades

    def get_trades(self):
        return self._trades


def test_removed_symbol_with_open_position_stays_in_monitoring_set():
    state = _FakeState({
        "open_removed": {
            "symbol": "AVAXUSDT",
            "group": "HIGH_BETA",
            "result": None,
        },
        "closed_removed": {
            "symbol": "WIFUSDT",
            "group": "MEME",
            "result": "WIN",
        },
        "open_approved": {
            "symbol": "BTCUSDT",
            "group": "CORE",
            "result": None,
        },
        "invalid_state_symbol": {
            "symbol": "../../NOT-A-PAIR",
            "group": "CORE",
            "result": None,
        },
    })

    assert bot._legacy_open_position_groups(state) == {
        "AVAXUSDT": "HIGH_BETA",
    }


def test_removed_symbol_group_fails_closed_to_high_beta():
    state = _FakeState({
        "legacy": {
            "symbol": "FLOKIUSDT",
            "group": "UNKNOWN_OLD_GROUP",
            "result": None,
        },
    })

    assert bot._legacy_open_position_groups(state) == {
        "FLOKIUSDT": "HIGH_BETA",
    }


def test_monitoring_only_path_never_builds_or_queues_a_new_trade(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("monitoring-only symbol attempted a new entry")

    monkeypatch.setattr(bot, "build_trade_plan", unexpected)
    monkeypatch.setattr(bot, "queue_trade_for_approval", unexpected)
    monkeypatch.setattr(bot, "open_trade", unexpected)
    result = {
        "symbol": "AVAXUSDT",
        "signal": "LONG",
        "actionable": True,
    }

    bot._maybe_start_tracked_trade(
        result,
        _FakeState({}),
        allow_new_entries=False,
    )

    assert result["entry_universe"] == {
        "allowed": False,
        "reason": "LEGACY_POSITION_MONITORING_ONLY",
    }


def test_direct_entry_gate_rejects_symbol_outside_approved_universe(monkeypatch):
    monkeypatch.setattr(bot, "TRADE_TRACKING_ENABLED", True)
    allowed, reason = bot.can_open_new_trade(
        {
            "symbol": "AVAXUSDT",
            "signal": "LONG",
            "actionable": True,
        },
        _FakeState({}),
    )

    assert allowed is False
    assert "onaylı işlem evreninde değil" in reason
