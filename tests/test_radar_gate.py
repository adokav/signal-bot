from __future__ import annotations

from acce_unified.radar_gate import liquid_long_gate, status_line, tactical_gate

NOW = 1_800_000_000


def _plan(**overrides):
    plan = {
        "entry_low": 100.0, "entry_high": 101.0, "technical_invalidation": 98.0,
        "hard_stop": 97.0, "target_1": 106.0, "target_2": 110.0,
    }
    plan.update(overrides)
    return plan


def test_fresh_valid_plan_is_watch_never_higher():
    decision = tactical_gate(
        {"symbol": "BTCUSDT", "decision_at": NOW - 60, "structure_4h": "BULLISH", "plan": _plan()},
        now=NOW, max_age_seconds=900,
    )
    assert decision.status == "WATCH"
    assert decision.no_trade and decision.can_authorize_trade is False
    by_key = {c.key: c.status.value for c in decision.checks}
    assert by_key["execution_healthy"] == "FAIL"
    assert by_key["oos_expectancy_positive"] == "UNKNOWN"


def test_bearish_no_plan_and_broken_geometry_are_rejected():
    bearish = tactical_gate(
        {"symbol": "ETHUSDT", "decision_at": NOW, "structure_4h": "BEARISH", "reasons": ["4H_STRUCTURE_BEARISH"]},
        now=NOW, max_age_seconds=900,
    )
    assert bearish.status == "REJECT"
    broken = tactical_gate(
        {"symbol": "BTCUSDT", "decision_at": NOW, "structure_4h": "BULLISH",
         "plan": _plan(hard_stop=99.0)},
        now=NOW, max_age_seconds=900,
    )
    assert broken.status == "REJECT"
    assert "stop teknik geçersizlik" in status_line(broken)


def test_unreadable_timestamp_is_not_treated_as_fresh():
    decision = tactical_gate(
        {"symbol": "BTCUSDT", "decision_at": None, "structure_4h": "BULLISH", "plan": _plan()},
        now=NOW, max_age_seconds=900,
    )
    assert decision.status == "WATCH"
    assert any(c.key == "data_fresh" and c.status.value == "UNKNOWN" for c in decision.checks)
    future = tactical_gate(
        {"symbol": "BTCUSDT", "decision_at": NOW + 600, "structure_4h": "BULLISH", "plan": _plan()},
        now=NOW, max_age_seconds=900,
    )
    assert future.status == "REJECT"


def test_liquid_long_missing_spread_and_neutral_regime_stay_unknown(tmp_path):
    from acce_unified.radar_gate import load_liquid_evidence

    no_evidence = load_liquid_evidence(tmp_path / "missing.json")
    decision = liquid_long_gate(
        {"symbol": "SOLUSDT", "metadata": {}}, market_regime="NEUTRAL",
        generated_at=NOW - 5, now=NOW, max_age_seconds=360, evidence=no_evidence,
    )
    assert decision.status == "WATCH"
    statuses = {c.key: c.status.value for c in decision.checks}
    assert statuses["liquid_enough"] == "UNKNOWN"
    assert statuses["regime_compatible"] == "UNKNOWN"
    assert statuses["stop_at_invalidation"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# Tactical replay evidence (docs/TACTICAL_REPLAY_REPORT.md)
# ---------------------------------------------------------------------------

import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

import bot
from acce_unified.forward_ledger import record_from_assessment
from acce_unified.radar_gate import (
    REPLAY_EVIDENCE_FILE,
    default_replay_evidence,
    evidence_line,
    evidence_status_text,
    family_disqualified,
    live_vs_replay_text,
    load_replay_evidence,
)

FP = "f" * 16


def _evidence_file(tmp_path, mutate=None, fingerprint=FP):
    payload = json.loads(REPLAY_EVIDENCE_FILE.read_text("utf-8"))
    payload["engine_fingerprint"] = fingerprint
    if mutate is not None:
        mutate(payload)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), "utf-8")
    return path


def _setup_item(setup, *, decided_at=NOW - 60):
    return {"symbol": "BTCUSDT", "decision_at": decided_at, "structure_4h": "BULLISH",
            "setup": setup, "state": "READY", "plan": _plan()}


def test_committed_evidence_matches_the_running_engine():
    """Tripwire: changing the engine, ledger or replay code makes the evidence stale.

    Re-run the replay (a new, pre-registered trial) and update
    research/evidence/tactical_replay.json before relying on it again.
    """

    evidence = default_replay_evidence()
    assert evidence.status == "OK", evidence.detail
    assert evidence.trial_id == "cff97d5d6f5b5c5d"


def test_negative_family_is_rejected_with_its_evidence(tmp_path):
    evidence = load_replay_evidence(_evidence_file(tmp_path), current_fingerprint=FP)
    decision = tactical_gate(_setup_item("BREAKOUT_RETEST"), now=NOW, max_age_seconds=900, evidence=evidence)
    assert decision.status == "REJECT" and decision.no_trade and decision.can_authorize_trade is False
    by_key = {c.key: c.status.value for c in decision.checks}
    assert by_key["oos_expectancy_positive"] == "FAIL"
    assert by_key["net_ev_positive"] == "FAIL"
    assert by_key["probabilities_calibrated"] == "UNKNOWN"  # a family base rate is not a calibrated probability
    line = status_line(decision)
    assert "REJECT" in line and "geçmiş test NEGATIVE" in line and "kanıt yok" not in line
    assert family_disqualified("BREAKOUT_RETEST", evidence)
    assert "NEGATIVE" in evidence_line("BREAKOUT_RETEST", evidence)


def test_insufficient_and_unknown_families_stay_watch(tmp_path):
    evidence = load_replay_evidence(_evidence_file(tmp_path), current_fingerprint=FP)
    thin = tactical_gate(_setup_item("RANGE_RECLAIM"), now=NOW, max_age_seconds=900, evidence=evidence)
    assert thin.status == "WATCH"
    assert any("yetersiz" in c.detail for c in thin.checks if c.key == "oos_expectancy_positive")
    new_family = tactical_gate(_setup_item("SOMETHING_NEW"), now=NOW, max_age_seconds=900, evidence=evidence)
    assert new_family.status == "WATCH"
    assert not family_disqualified("RANGE_RECLAIM", evidence)
    assert evidence_line("SOMETHING_NEW", evidence).startswith("Geçmiş test: YOK")


def test_evidence_for_another_engine_is_not_applied(tmp_path):
    evidence = load_replay_evidence(_evidence_file(tmp_path), current_fingerprint="0" * 16)
    assert evidence.status == "STALE"
    decision = tactical_gate(_setup_item("BREAKOUT_RETEST"), now=NOW, max_age_seconds=900, evidence=evidence)
    assert decision.status == "WATCH"
    assert not family_disqualified("BREAKOUT_RETEST", evidence)
    assert "uygulanamıyor" in evidence_status_text(evidence)


@pytest.mark.parametrize("mutate", [
    lambda p: p["families"]["BREAKOUT_RETEST"].update(mean_r_ci_family=[-0.3, 0.1]),  # verdict contradicts numbers
    lambda p: p["families"]["BREAKOUT_RETEST"].update(verdict="GREAT"),
    lambda p: p["families"]["BREAKOUT_RETEST"].update(mean_r=float("nan")),
    lambda p: p["families"]["BREAKOUT_RETEST"].update(resolved=True),
    lambda p: p["families"]["BREAKOUT_RETEST"].update(hit_rate_ci=[0.3, 0.2]),
    lambda p: p["families"]["RANGE_RECLAIM"].update(verdict="PASS_CANDIDATE"),  # 26 outcomes, CI spans zero
    lambda p: p.update(can_authorize_trade=True),
    lambda p: p.update(schema="something-else"),
    lambda p: p.pop("families"),
])
def test_corrupt_or_inconsistent_evidence_fails_closed(tmp_path, mutate):
    evidence = load_replay_evidence(_evidence_file(tmp_path, mutate), current_fingerprint=FP)
    assert evidence.status == "INVALID"
    decision = tactical_gate(_setup_item("BREAKOUT_RETEST"), now=NOW, max_age_seconds=900, evidence=evidence)
    assert decision.status == "WATCH"  # unknown, never guessed into PASS or FAIL


def test_missing_or_unreadable_evidence_fails_closed(tmp_path):
    assert load_replay_evidence(tmp_path / "nope.json", current_fingerprint=FP).status == "MISSING"
    (tmp_path / "bad.json").write_text("{not json")
    assert load_replay_evidence(tmp_path / "bad.json", current_fingerprint=FP).status == "INVALID"


def test_no_plan_items_ignore_the_evidence(tmp_path):
    evidence = load_replay_evidence(_evidence_file(tmp_path), current_fingerprint=FP)
    item = {"symbol": "BTCUSDT", "decision_at": NOW, "structure_4h": "BULLISH",
            "setup": "BREAKOUT_RETEST", "reasons": ["INSUFFICIENT_REWARD_RISK"]}
    decision = tactical_gate(item, now=NOW, max_age_seconds=900, evidence=evidence)
    assert {c.key: c.status.value for c in decision.checks}["oos_expectancy_positive"] == "UNKNOWN"


def test_live_vs_replay_is_descriptive(tmp_path):
    evidence = load_replay_evidence(_evidence_file(tmp_path), current_fingerprint=FP)
    assert "henüz" in live_vs_replay_text([], evidence)
    base = record_from_assessment({**_setup_item("BREAKOUT_RETEST"), "plan": {
        **_plan(), "estimated_round_trip_cost_pct": 0.1, "expires_at": NOW + 3600}})
    rows = [replace(base, record_id="a", status="LOSS_STOP", r_multiple=-1.0),
            replace(base, record_id="b", status="WIN_T1", r_multiple=1.5)]
    text = live_vs_replay_text(rows, evidence)
    assert "BREAKOUT_RETEST canlı +0.25R (n=2) vs replay -0.28R" in text
    assert "anlamlı değil" in text


# ---------------------------------------------------------------------------
# Bot alerts follow the gate
# ---------------------------------------------------------------------------


def _scan(monkeypatch, tmp_path, setup, *, rejected_alerts=False):
    from acce_unified.forward_ledger import ForwardLedger
    from acce_unified.tactical_long_data import TacticalTimeframe

    item = {**_setup_item(setup, decided_at=int(time.time()) - 5),
            "plan": {**_plan(), "estimated_round_trip_cost_pct": 0.1, "expires_at": int(time.time()) + 3600}}
    report = {"generated_at": item["decision_at"], "assessments": [item], "errors": [], "can_authorize_trade": False}
    frames = {tf: () for tf in TacticalTimeframe}
    market = SimpleNamespace(candles={"BTCUSDT": dict(frames), "ETHUSDT": dict(frames)})
    sent: list[str] = []
    monkeypatch.setattr(bot, "FORWARD_LEDGER", ForwardLedger(tmp_path / "ledger.json"))
    monkeypatch.setattr(bot.TACTICAL_DATA, "snapshot", lambda: market)
    monkeypatch.setattr(bot.TACTICAL_ENGINE, "analyze", lambda m: SimpleNamespace(to_dict=lambda: report))
    monkeypatch.setattr(bot, "send", lambda text, **kwargs: sent.append(text))
    monkeypatch.setattr(bot, "TACTICAL_ALERTS", True)
    monkeypatch.setattr(bot, "TACTICAL_REJECTED_ALERTS", rejected_alerts)
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    for key in ("tactical_snapshot", "tactical_last_error", "forward_ledger_status",
                "forward_vs_replay", "forward_ledger_error"):
        monkeypatch.setitem(bot.STATE, key, bot.STATE.get(key))
    monkeypatch.setitem(bot.STATE, "tactical_last_states", {})
    bot.tactical_scan_once()
    return sent, bot.FORWARD_LEDGER.load()


def test_rejected_setup_is_recorded_but_not_pushed(monkeypatch, tmp_path):
    sent, records = _scan(monkeypatch, tmp_path, "BREAKOUT_RETEST")
    assert sent == []
    assert len(records) == 1 and records[0].setup == "BREAKOUT_RETEST"


def test_rejected_alerts_can_be_re_enabled_and_say_reject(monkeypatch, tmp_path):
    sent, _ = _scan(monkeypatch, tmp_path, "BREAKOUT_RETEST", rejected_alerts=True)
    assert len(sent) == 1 and sent[0].startswith("⛔") and "REJECT" in sent[0]
    assert "Kapı nedeni" in sent[0] and "geçmiş test NEGATIVE" in sent[0]


def test_watch_setup_is_still_pushed(monkeypatch, tmp_path):
    sent, _ = _scan(monkeypatch, tmp_path, "RANGE_RECLAIM")
    assert len(sent) == 1 and sent[0].startswith("👀") and "WATCH" in sent[0]
    assert "INSUFFICIENT" in sent[0] and "Sinyal bazında kalibre olasılık: YOK" in sent[0]


# ---------------------------------------------------------------------------
# Liquid-100 replay evidence (docs/LIQUID_REPLAY_REPORT.md)
# ---------------------------------------------------------------------------

from acce_unified.radar_gate import (
    LIQUID_EVIDENCE_FILE,
    LIQUID_FINGERPRINT_FILES,
    code_fingerprint,
    default_liquid_evidence,
    liquid_evidence_line,
    load_liquid_evidence,
)


def _liquid_file(tmp_path, mutate=None, fingerprint=FP):
    payload = json.loads(LIQUID_EVIDENCE_FILE.read_text("utf-8"))
    payload["engine_fingerprint"] = fingerprint
    if mutate is not None:
        mutate(payload)
    path = tmp_path / "liquid.json"
    path.write_text(json.dumps(payload), "utf-8")
    return path


def _liquid_item():
    return {"symbol": "SOLUSDT", "metadata": {"spread_bps": 3.0}}


def test_committed_liquid_evidence_matches_the_running_code():
    """Tripwire: changing the radar, the universe builder or the replay makes the evidence stale."""

    from trading.backtest import liquid_replay

    assert LIQUID_FINGERPRINT_FILES == liquid_replay.FINGERPRINT_FILES
    assert code_fingerprint(LIQUID_FINGERPRINT_FILES) == liquid_replay.engine_fingerprint()
    evidence = default_liquid_evidence()
    assert evidence.status == "OK", evidence.detail
    assert evidence.trial_id == "69f6387eaf32ed4f"


def test_negative_top3_list_is_rejected_with_its_evidence(tmp_path):
    evidence = load_liquid_evidence(_liquid_file(tmp_path), current_fingerprint=FP)
    decision = liquid_long_gate(_liquid_item(), market_regime="RISK_ON", generated_at=NOW - 5,
                                now=NOW, max_age_seconds=360, evidence=evidence)
    assert decision.status == "REJECT" and decision.can_authorize_trade is False
    by_key = {c.key: c.status.value for c in decision.checks}
    assert by_key["oos_expectancy_positive"] == "FAIL" and by_key["net_ev_positive"] == "FAIL"
    line = status_line(decision)
    assert "geçmiş test NEGATIVE" in line and "24 saat" in line
    assert "NEGATIVE" in liquid_evidence_line(evidence)


def test_mixed_or_insufficient_liquid_evidence_is_not_a_verdict(tmp_path):
    def mixed(p):
        p["groups"]["TOP3@4h"].update(verdict="INSUFFICIENT", n=100)

    evidence = load_liquid_evidence(_liquid_file(tmp_path, mixed), current_fingerprint=FP)
    decision = liquid_long_gate(_liquid_item(), market_regime="RISK_ON", generated_at=NOW - 5,
                                now=NOW, max_age_seconds=360, evidence=evidence)
    assert decision.status == "WATCH"


def test_liquid_evidence_for_other_code_is_not_applied(tmp_path):
    evidence = load_liquid_evidence(_liquid_file(tmp_path), current_fingerprint="0" * 16)
    assert evidence.status == "STALE"
    decision = liquid_long_gate(_liquid_item(), market_regime="RISK_ON", generated_at=NOW - 5,
                                now=NOW, max_age_seconds=360, evidence=evidence)
    assert decision.status == "WATCH"
    assert liquid_evidence_line(evidence).startswith("Geçmiş test: YOK")


@pytest.mark.parametrize("mutate", [
    lambda p: p["groups"]["TOP3@24h"].update(excess_ci_family=[-0.4, 0.1]),
    lambda p: p["groups"]["TOP3@24h"].update(verdict="GOOD"),
    lambda p: p["groups"]["TOP3@24h"].update(mean_excess_pct=float("inf")),
    lambda p: p["groups"]["TOP3@24h"].update(n=-1),
    lambda p: p["groups"]["TOP3@4h"].update(verdict="PASS_CANDIDATE"),  # negative numbers
    lambda p: p.update(can_authorize_trade=True),
    lambda p: p.pop("groups"),
])
def test_corrupt_liquid_evidence_fails_closed(tmp_path, mutate):
    evidence = load_liquid_evidence(_liquid_file(tmp_path, mutate), current_fingerprint=FP)
    assert evidence.status == "INVALID"
    decision = liquid_long_gate(_liquid_item(), market_regime="RISK_ON", generated_at=NOW - 5,
                                now=NOW, max_age_seconds=360, evidence=evidence)
    assert decision.status == "WATCH"


# ---------------------------------------------------------------------------
# New-listing replay evidence (docs/LISTING_REPLAY_REPORT.md): a base rate on the panel, no gate
# ---------------------------------------------------------------------------

from acce_unified.radar_gate import (
    LISTING_EVIDENCE_FILE,
    LISTING_FINGERPRINT_FILES,
    default_listing_evidence,
    listing_evidence_line,
    load_listing_evidence,
)


def _listing_file(tmp_path, mutate=None, fingerprint=FP):
    payload = json.loads(LISTING_EVIDENCE_FILE.read_text("utf-8"))
    payload["engine_fingerprint"] = fingerprint
    if mutate is not None:
        mutate(payload)
    path = tmp_path / "listing.json"
    path.write_text(json.dumps(payload), "utf-8")
    return path


def test_committed_listing_evidence_matches_the_running_code():
    """Tripwire: changing the identity layer, the dataset builder or the study makes the base rate stale."""

    pytest.importorskip("numpy")
    from trading.backtest import listing_replay

    assert LISTING_FINGERPRINT_FILES == listing_replay.FINGERPRINT_FILES
    assert code_fingerprint(LISTING_FINGERPRINT_FILES) == listing_replay.engine_fingerprint()
    evidence = default_listing_evidence()
    assert evidence.status == "OK", evidence.detail
    assert evidence.trial_id == "2381d2ac70c38a52" and len(evidence.groups) == 6


def test_listing_panel_line_states_the_base_rate_and_its_limits(tmp_path):
    line = listing_evidence_line(load_listing_evidence(_listing_file(tmp_path), current_fingerprint=FP))
    assert "medyan %-20.3" in line and "BTC'yi geçen %24" in line and "NO_CLAIM" in line
    assert "birkaç büyük kazanana" in line          # mean far above the median
    assert "MEXC listelemeleri için test yok" in line


def test_listing_status_summarises_all_six_tests(tmp_path):
    listing = load_listing_evidence(_listing_file(tmp_path), current_fingerprint=FP)
    assert "Yeni listeleme replay — NO_CLAIM 6/6" in evidence_status_text(listing_evidence=listing)


def test_listing_evidence_for_other_code_is_not_shown(tmp_path):
    evidence = load_listing_evidence(_listing_file(tmp_path), current_fingerprint="0" * 16)
    assert evidence.status == "STALE"
    assert listing_evidence_line(evidence).startswith("Geçmiş test: YOK")
    assert "uygulanamıyor" in evidence_status_text(listing_evidence=evidence)
    missing = load_listing_evidence(tmp_path / "nope.json", current_fingerprint=FP)
    assert missing.status == "MISSING" and listing_evidence_line(missing).startswith("Geçmiş test: YOK")


@pytest.mark.parametrize("mutate", [
    lambda p: p["groups"]["24h@30d"].update(verdict="AVOID_CONFIRMED"),      # interval reaches zero
    lambda p: p["groups"]["24h@30d"].update(verdict="POSITIVE_SURPRISE"),    # intervals not above zero
    lambda p: p["groups"]["24h@30d"].update(verdict="INSUFFICIENT"),         # n >= 100
    lambda p: p["groups"]["24h@30d"].update(n=50),                            # NO_CLAIM needs 100
    lambda p: p["groups"]["24h@30d"].update(share_beating_btc=1.4),
    lambda p: p["groups"]["24h@30d"].update(median_gross_pct=float("nan")),
    lambda p: p["groups"]["24h@30d"].update(verdict="SAFE"),
    lambda p: p.update(can_authorize_trade=True),
    lambda p: p.pop("window"),
])
def test_corrupt_listing_evidence_fails_closed(tmp_path, mutate):
    evidence = load_listing_evidence(_listing_file(tmp_path, mutate), current_fingerprint=FP)
    assert evidence.status == "INVALID"
    assert listing_evidence_line(evidence).startswith("Geçmiş test: YOK")
