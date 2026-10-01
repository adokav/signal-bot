"""Live use of the confirmed signal-quality result (docs/SIGNAL_QUALITY.md): labels only, fail closed."""

from __future__ import annotations

import json

import pytest

import bot
from acce_unified import long_alerts as la
from acce_unified import radar_gate as g

NOW = 1_790_000_000


def _meta(*, atr=0.7, change=3.0, median=1.0, size=100, status="READY"):
    return {"change_pct": change, "long_metrics": {"status": status, "atr_pct": atr},
            "market_context": {"median_change_pct": median, "universe_size": size}}


def _evidence(tmp_path, mutate=None):
    payload = json.loads(g.SIGNAL_QUALITY_EVIDENCE_FILE.read_text("utf-8"))
    if mutate:
        mutate(payload)
    path = tmp_path / "sq.json"
    path.write_text(json.dumps(payload))
    return path


def test_committed_evidence_matches_the_code_it_was_measured_on():
    evidence = g.load_signal_quality_evidence()
    assert evidence.status == "OK" and evidence.trial_id == g.SIGNAL_QUALITY_TRIAL
    assert evidence.tests[g.F1_TEST].verdict == "KAYBI_AZALTIR"
    assert evidence.tests[g.F2_TEST].verdict == "NO_EFFECT"


def test_fingerprint_and_rule_are_the_registered_ones():
    pytest.importorskip("numpy")
    from trading.backtest import signal_quality as sq

    assert g.SIGNAL_QUALITY_FINGERPRINT_FILES == sq.FINGERPRINT_FILES
    assert g.SIGNAL_QUALITY_ALERT_LOGIC == sq.LONG_ALERT_LOGIC
    assert g.SIGNAL_QUALITY_ALERT_CONSTANTS == sq.LONG_ALERT_CONSTANTS
    assert g.signal_quality_fingerprint() == sq.logic_fingerprint()
    assert (g.F1_ATR15_MAX, g.F1_REL24_MAX) == (sq.LIKIT_ATR15_MAX, sq.LIKIT_REL24_MAX)
    assert g.SIGNAL_QUALITY_TRIAL == sq.filter_trial_id()


def test_evidence_fails_closed(tmp_path):
    assert g.load_signal_quality_evidence(tmp_path / "none.json").status == "MISSING"
    stale = g.load_signal_quality_evidence(_evidence(tmp_path), current_fingerprint="0" * 16)
    assert stale.status == "STALE" and not stale.tests
    other = _evidence(tmp_path, lambda p: p.update(trial_id="a2a9af1b364c9ac7"))
    assert g.load_signal_quality_evidence(other).status == "INVALID"

    def claim_pass(payload):
        payload["tests"][g.F1_TEST]["verdict"] = "PASS"       # kept net interval includes zero: not a PASS
    assert g.load_signal_quality_evidence(_evidence(tmp_path, claim_pass)).status == "INVALID"

    def broken_counts(payload):
        payload["tests"][g.F1_TEST]["kept"] = 1
    assert g.load_signal_quality_evidence(_evidence(tmp_path, broken_counts)).status == "INVALID"

    def authority(payload):
        payload["can_authorize_trade"] = True
    assert g.load_signal_quality_evidence(_evidence(tmp_path, authority)).status == "INVALID"


def test_f1_label_marks_only_the_dropped_alerts():
    evidence = g.load_signal_quality_evidence()
    calm = g.likit_quality_label(_meta(), evidence)
    assert calm.status == "PASSED" and calm.text == "geçti" and "kâr ettiği gösterilmedi" in calm.detail
    assert g.likit_quality_label(_meta(atr=1.0, change=6.0), evidence).status == "PASSED"   # inclusive edges
    volatile = g.likit_quality_label(_meta(atr=1.4), evidence)
    assert volatile.status == "AVOID" and volatile.text == "KAÇIN" and "oynak" in volatile.detail
    chasing = g.likit_quality_label(_meta(change=9.0, median=1.0), evidence)
    assert chasing.status == "AVOID" and "kovalıyor" in chasing.detail and "+8.0 puan" in chasing.detail


def test_missing_inputs_are_unknown_and_unconfirmed_evidence_gives_no_label(tmp_path):
    evidence = g.load_signal_quality_evidence()
    for meta in (_meta(atr=None), _meta(status="INSUFFICIENT_KLINES"), _meta(size=40), _meta(median=None), {}):
        assert g.likit_quality_label(meta, evidence).status == "UNKNOWN"
    stale = g.load_signal_quality_evidence(_evidence(tmp_path), current_fingerprint="0" * 16)
    assert g.likit_quality_label(_meta(atr=3.0), stale).status == "NOT_APPLIED"

    def no_effect(payload):
        payload["tests"][g.F1_TEST]["verdict"] = "NO_EFFECT"
        payload["tests"][g.F1_TEST]["kept_minus_dropped_net_pct"]["ci"] = [-0.1, 1.0]
    neutral = g.load_signal_quality_evidence(_evidence(tmp_path, no_effect))
    assert neutral.status == "OK" and g.likit_quality_label(_meta(atr=3.0), neutral).status == "NOT_APPLIED"


def test_status_line_reports_both_tests():
    text = g.evidence_status_text()
    assert "Sinyal kalitesi — F1 sakin/kovalamayan: KAYBI_AZALTIR · F2 baz: NO_EFFECT" in text


# ---------------------------------------------------------------------------
# Bot wiring
# ---------------------------------------------------------------------------


class _Klines:
    def fetch_klines(self, symbol, interval_seconds, limit):
        start = NOW - NOW % 3600 - 30 * 3600
        return [[(start + k * 3600) * 1000, "10", "10.2", "9.8", "10", "1", (start + (k + 1) * 3600) * 1000 - 1, "1"]
                for k in range(30)]


def test_alert_panel_and_radar_log_carry_the_label(monkeypatch):
    sent = []
    monkeypatch.setattr(bot, "send", lambda text, keyboard=None, html_mode=False: sent.append(text))
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    monkeypatch.setitem(bot.STATE, "radar_log", [])
    monkeypatch.setattr(bot, "LIQUID_LONG_ALERTS", True)
    monkeypatch.setattr(bot, "KLINES", _Klines())
    monkeypatch.setattr(bot.time, "time", lambda: NOW)
    snapshot = {
        "generated_at": NOW, "liquid_market_context": {"regime": "RISK_ON"},
        "liquid_long_candidates": [{"symbol": "SOLUSDT", "score": 70, "metadata": dict(
            _meta(atr=2.5, change=12.0, median=1.0), last_price=10.0, spread_bps=3)}],
    }
    bot._liquid_radar(snapshot)
    assert "Kalite (F1) KAÇIN" in sent[0] and "Kalite filtresi: oynak" in sent[0]
    entry = bot.STATE["radar_log"][0]
    assert entry["quality"] == "AVOID" and entry["quality_inputs"]["rel_24h"] == pytest.approx(11.0)
    assert entry["can_authorize_trade"] is False
    assert "Kalite filtresi (F1): KAÇIN" in bot.format_longs(snapshot)
    stopped = dict(entry, status=la.STOPPED, result_pct=-4.0, exit_price=9.6, closed_at=NOW + 3600)
    calm = dict(entry, symbol="ADAUSDT", quality="PASSED", status=la.EXPIRED, result_pct=1.0, closed_at=NOW + 3600)
    radar = la.format_radar([stopped, calm], now=NOW + 7200)
    assert "F1 kaçın" in radar and "F1 geçti" in radar
    assert "F1 etiketine göre: geçti 1 kayıt ort. %+1.0 · kaçın 1 kayıt ort. %-4.0" in radar
