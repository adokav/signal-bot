from __future__ import annotations

import bot


def test_higher_timeframe_replay_uses_only_closed_candles():
    # Decision is made when the 10:05 5m bar closes at 10:10.
    hour = 3_600_000
    minute = 60_000
    opens_15m = [9 * hour + 45 * minute, 10 * hour, 10 * hour + 15 * minute]
    decision_open = 10 * hour + 5 * minute
    idx = bot._hr_closed_idx_at(opens_15m, decision_open, "15m")
    assert idx == 0  # 09:45 closed at 10:00; 10:00 is still open until 10:15.


def test_current_five_minute_bar_is_available_at_its_close():
    minute = 60_000
    opens_5m = [0, 5 * minute, 10 * minute]
    assert bot._hr_closed_idx_at(opens_5m, 5 * minute, "5m") == 1


def test_replay_entry_can_be_forced_to_next_bar_open():
    result = {"signal": "LONG", "group": "CORE", "regime": {}, "trade_quality": {}}
    bar = [1_000, "101", "106", "99", "105", "10"]
    pos = bot._open_replay_pos(
        "BTCUSDT",
        result,
        bar,
        1_000,
        2.0,
        {},
        entry_price=101.0,
    )
    assert pos["entry"] == 101.0
