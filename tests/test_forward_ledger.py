from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bot
from acce_unified.forward_ledger import (
    MAX_HOLD_SECONDS,
    ForwardLedger,
    LedgerIntegrityError,
    advance,
    record_from_assessment,
    status_text,
    summarize,
    update_records,
)
from acce_unified.tactical_long import Candle
from acce_unified.tactical_long_data import TacticalTimeframe

T0 = 1_800_000_000  # multiple of 300


def _item(symbol="BTCUSDT", decided_at=T0, state="READY", **plan_overrides):
    plan = {
        "entry_low": 99.0, "entry_high": 100.0, "technical_invalidation": 98.0,
        "hard_stop": 97.0, "target_1": 104.0, "target_2": 108.0,
        "estimated_round_trip_cost_pct": 0.1, "expires_at": decided_at + 3600,
    }
    plan.update(plan_overrides)
    return {"symbol": symbol, "decision_at": decided_at, "state": state,
            "setup": "TREND_PULLBACK", "structure_4h": "BULLISH", "plan": plan}


def _m5(start, bars):
    """bars: list of (open, high, low, close)."""
    out = []
    for i, (o, h, l, c) in enumerate(bars):
        open_time = start + i * 300
        out.append(Candle(open_time=open_time, close_time=open_time + 299,
                          available_at=open_time + 299, open=o, high=h, low=l, close=c, volume=1.0))
    return out


def test_record_only_for_ready_or_triggered_plans_with_valid_geometry():
    assert record_from_assessment(_item()) is not None
    assert record_from_assessment(_item(state="OBSERVE")) is None
    assert record_from_assessment({**_item(), "plan": None}) is None
    assert record_from_assessment(_item(hard_stop=98.5)) is None
    assert record_from_assessment(_item()).can_authorize_trade is False


def test_fill_then_target_is_a_win_with_net_r():
    record = record_from_assessment(_item())
    candles = _m5(T0, [(101, 101.5, 99.8, 100.5), (100.5, 102, 100.2, 101.8), (101.8, 104.5, 101.5, 104.2)])
    result = advance(record, candles)
    assert result.status == "WIN_T1"
    assert result.fill_price == pytest.approx(100.0)
    assert result.exit_price == pytest.approx(104.0)
    risk = (100.0 - 97.0) / 100.0 * 100 + 0.1
    assert result.r_multiple == pytest.approx((4.0 - 0.1) / risk)


def test_same_candle_stop_and_target_counts_as_loss():
    record = record_from_assessment(_item())
    candles = _m5(T0, [(100.5, 100.6, 99.9, 100.2), (100.2, 104.5, 96.5, 101.0)])
    assert advance(record, candles).status == "LOSS_STOP"


def test_target_on_the_fill_candle_is_not_counted():
    record = record_from_assessment(_item())
    result = advance(record, _m5(T0, [(101.0, 105.0, 99.9, 104.8)]))
    assert result.status == "OPEN"
    assert result.fill_price == pytest.approx(100.0)


def test_candles_before_the_decision_are_ignored():
    record = record_from_assessment(_item(decided_at=T0 + 600))
    # A dip to the stop and a spike to target both happen before the decision.
    candles = _m5(T0, [(101, 105, 96, 101), (101, 101.5, 100.5, 101), (101, 101.2, 100.8, 101)])
    result = advance(record, candles)
    assert result.status == "OPEN"
    assert result.fill_price is None


def test_unfilled_plan_expires_and_long_hold_time_exits():
    record = record_from_assessment(_item())
    never_dips = _m5(T0, [(101, 102, 100.5, 101)] * 14)
    assert advance(record, never_dips).status == "NOT_FILLED"
    filled_then_flat = _m5(T0, [(100.2, 100.4, 99.8, 100.1)] + [(100.1, 101, 99.5, 100.3)] * (MAX_HOLD_SECONDS // 300 + 1))
    result = advance(record, filled_then_flat)
    assert result.status == "TIME_EXIT"
    assert result.exit_price == pytest.approx(100.3)


def test_gaps_are_unresolvable_not_guessed():
    record = record_from_assessment(_item())
    late = _m5(T0 + 3000, [(100.2, 104.5, 99.9, 104)])
    assert advance(record, late).status == "UNRESOLVABLE"
    holey = _m5(T0, [(101, 101.5, 99.8, 100.5)]) + _m5(T0 + 900, [(100.5, 104.5, 100.2, 104.2)])
    assert advance(record, holey).status == "UNRESOLVABLE"


def test_update_is_incremental_and_one_open_record_per_symbol():
    first = update_records([], new_assessments=[_item()], m5_by_symbol={})
    assert len(first) == 1
    again = update_records(first, new_assessments=[_item(decided_at=T0 + 300)], m5_by_symbol={})
    assert len(again) == 1  # BTC already has an open record
    both = update_records(again, new_assessments=[_item(symbol="ETHUSDT")], m5_by_symbol={})
    assert {r.symbol for r in both} == {"BTCUSDT", "ETHUSDT"}
    step1 = update_records(both, new_assessments=[], m5_by_symbol={"BTCUSDT": _m5(T0, [(101, 101.5, 99.8, 100.5)])})
    btc = next(r for r in step1 if r.symbol == "BTCUSDT")
    assert btc.status == "OPEN" and btc.fill_price == pytest.approx(100.0)
    step2 = update_records(step1, new_assessments=[], m5_by_symbol={
        "BTCUSDT": _m5(T0, [(101, 101.5, 99.8, 100.5), (100.5, 104.5, 100.2, 104.2)]),
    })
    assert next(r for r in step2 if r.symbol == "BTCUSDT").status == "WIN_T1"


def test_ledger_roundtrip_and_integrity(tmp_path):
    ledger = ForwardLedger(tmp_path / "ledger.json")
    assert ledger.load() == []
    records = update_records([], new_assessments=[_item()], m5_by_symbol={})
    ledger.save(records)
    assert ledger.load() == records
    payload = json.loads(ledger.path.read_text())
    payload["records"][0]["hard_stop"] = 96.0  # retroactive edit of a decision field
    ledger.path.write_text(json.dumps(payload))
    with pytest.raises(LedgerIntegrityError, match="altered"):
        ledger.load()
    ledger.path.write_text("{not json")
    with pytest.raises(LedgerIntegrityError):
        ledger.load()


def test_summary_reports_wilson_interval_and_sample_quality():
    base = record_from_assessment(_item())
    rows = [
        replace(base, record_id=f"w{i}", status="WIN_T1", r_multiple=1.2) for i in range(6)
    ] + [
        replace(base, record_id=f"l{i}", status="LOSS_STOP", r_multiple=-1.0) for i in range(4)
    ] + [replace(base, record_id="nf", status="NOT_FILLED")]
    overall = summarize(rows)[0]
    assert overall.resolved == 10 and overall.wins == 6 and overall.not_filled == 1
    assert overall.hit_rate_low < 0.6 < overall.hit_rate_high
    assert overall.sample_quality == "INSUFFICIENT"
    text = status_text(rows)
    assert "10 çözümlendi" in text and "INSUFFICIENT" in text
    assert "henüz çözümlenen yok" in status_text([base])


def _fake_market(m5):
    frames = {tf: () for tf in TacticalTimeframe}
    return SimpleNamespace(candles={
        "BTCUSDT": {**frames, TacticalTimeframe.M5: tuple(m5)},
        "ETHUSDT": {**frames, TacticalTimeframe.M5: ()},
    })


def test_bot_records_new_setups_and_never_overwrites_a_corrupt_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "FORWARD_LEDGER", ForwardLedger(tmp_path / "ledger.json"))
    saved = {k: bot.STATE.get(k) for k in ("forward_ledger_status", "forward_ledger_error")}
    try:
        bot._update_forward_ledger(_fake_market([]), [_item()])
        assert len(bot.FORWARD_LEDGER.load()) == 1
        assert "henüz çözümlenen yok" in bot.STATE["forward_ledger_status"]
        assert bot.STATE["forward_ledger_error"] is None

        bot.FORWARD_LEDGER.path.write_text("{corrupt")
        bot._update_forward_ledger(_fake_market([]), [_item(symbol="ETHUSDT")])
        assert bot.FORWARD_LEDGER.path.read_text() == "{corrupt"
        assert bot.STATE["forward_ledger_error"].startswith("LedgerIntegrityError")
        assert "Kayıt hatası" in bot.format_status(None)
    finally:
        bot.STATE.update(saved)


def test_tactical_scan_feeds_the_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "FORWARD_LEDGER", ForwardLedger(tmp_path / "ledger.json"))
    monkeypatch.setattr(bot.TACTICAL_DATA, "snapshot", lambda: _fake_market([]))
    report = {"generated_at": T0, "assessments": [_item()], "errors": [], "can_authorize_trade": False}
    monkeypatch.setattr(bot.TACTICAL_ENGINE, "analyze", lambda market: SimpleNamespace(to_dict=lambda: report))
    saved = {k: bot.STATE.get(k) for k in ("tactical_last_states", "tactical_snapshot", "forward_ledger_status", "forward_ledger_error")}
    try:
        bot.STATE["tactical_last_states"] = {}
        bot.tactical_scan_once(emit_alerts=False)
        records = bot.FORWARD_LEDGER.load()
        assert len(records) == 1 and records[0].symbol == "BTCUSDT"
        bot.tactical_scan_once(emit_alerts=False)  # same state: no new record
        assert len(bot.FORWARD_LEDGER.load()) == 1
    finally:
        bot.STATE.update(saved)
