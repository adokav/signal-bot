from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from acce_unified.long_alerts import LongAlertKind, LongAlertTracker, format_long_alert


NOW = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)


def test_new_candidate_emits_entered_and_is_watched(tmp_path):
    tracker = LongAlertTracker(tmp_path / "long-watch.json")

    events = tracker.evaluate(
        observed_at=NOW,
        evaluated_symbols={"BTCUSDT", "ETHUSDT"},
        qualified_scores={"BTCUSDT": 74.5},
    )

    assert len(events) == 1
    assert events[0].kind is LongAlertKind.ENTERED
    assert events[0].symbol == "BTCUSDT"
    assert tracker.watched_symbols() == ("BTCUSDT",)
    assert "işlem emri değildir" in format_long_alert(events[0])


def test_unchanged_candidate_does_not_repeat_alert(tmp_path):
    tracker = LongAlertTracker(tmp_path / "long-watch.json")
    tracker.evaluate(
        observed_at=NOW,
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={"BTCUSDT": 74.5},
    )

    events = tracker.evaluate(
        observed_at=NOW + timedelta(minutes=2),
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={"BTCUSDT": 76.0},
    )

    assert events == ()


def test_watched_candidate_emits_broken_once_with_reasons(tmp_path):
    tracker = LongAlertTracker(tmp_path / "long-watch.json")
    tracker.evaluate(
        observed_at=NOW,
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={"BTCUSDT": 74.5},
    )

    events = tracker.evaluate(
        observed_at=NOW + timedelta(minutes=2),
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={},
        rejection_reasons={"BTCUSDT": ("EMA_STRUCTURE_BROKEN", "MOMENTUM_NEGATIVE")},
    )

    assert len(events) == 1
    assert events[0].kind is LongAlertKind.BROKEN
    assert events[0].reason_codes == ("EMA_STRUCTURE_BROKEN", "MOMENTUM_NEGATIVE")
    assert tracker.watched_symbols() == ()
    assert "FORMASYONU BOZULDU" in format_long_alert(events[0])

    duplicate = tracker.evaluate(
        observed_at=NOW + timedelta(minutes=4),
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={},
    )
    assert duplicate == ()


def test_provider_gap_does_not_create_false_break(tmp_path):
    tracker = LongAlertTracker(tmp_path / "long-watch.json")
    tracker.evaluate(
        observed_at=NOW,
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={"BTCUSDT": 74.5},
    )

    events = tracker.evaluate(
        observed_at=NOW + timedelta(minutes=2),
        evaluated_symbols=set(),
        qualified_scores={},
    )

    assert events == ()
    assert tracker.watched_symbols() == ("BTCUSDT",)


def test_requalification_after_break_emits_new_entered(tmp_path):
    path = tmp_path / "long-watch.json"
    tracker = LongAlertTracker(path)
    tracker.evaluate(
        observed_at=NOW,
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={"BTCUSDT": 74.5},
    )
    tracker.evaluate(
        observed_at=NOW + timedelta(minutes=2),
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={},
    )

    restarted = LongAlertTracker(path)
    events = restarted.evaluate(
        observed_at=NOW + timedelta(minutes=4),
        evaluated_symbols={"BTCUSDT"},
        qualified_scores={"BTCUSDT": 78.0},
    )

    assert len(events) == 1
    assert events[0].kind is LongAlertKind.ENTERED


def test_qualified_symbol_must_be_in_evaluated_set(tmp_path):
    tracker = LongAlertTracker(tmp_path / "long-watch.json")
    with pytest.raises(ValueError, match="was not evaluated"):
        tracker.evaluate(
            observed_at=NOW,
            evaluated_symbols=set(),
            qualified_scores={"BTCUSDT": 74.5},
        )
