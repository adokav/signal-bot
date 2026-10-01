"""Live use of the confirmed signal-quality result (docs/SIGNAL_QUALITY.md): labels only, fail closed."""

from __future__ import annotations

import json

import pytest

import bot
from acce_unified import long_alerts as la
from acce_unified import radar_gate as g

NOW = 1_790_000_000


def _label(atr=0.7, change=3.0, median=1.0, evidence=None):
    return g.likit_quality_label(atr15_pct=atr, change_24h=change, universe_median=median,
                                 evidence=evidence or g.load_signal_quality_evidence())


def _m15(now, closes):
    """Binance-style 15m rows ending with a forming candle; ``closes`` are the closed ones, oldest first."""

    last_open = now - now % 900 - 900
    first = last_open - (len(closes) - 1) * 900
    rows = [[(first + k * 900) * 1000, str(c), str(c * 1.001), str(c * 0.999), str(c), "1",
             (first + (k + 1) * 900) * 1000 - 1, "1"] for k, c in enumerate(closes)]
    forming = last_open + 900
    rows.append([forming * 1000, "999", "999", "999", "999", "1", (forming + 900) * 1000 - 1, "1"])
    return rows


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


def test_live_rule_is_pinned_by_the_evidence(tmp_path):
    payload = json.loads(g.SIGNAL_QUALITY_EVIDENCE_FILE.read_text("utf-8"))
    assert payload["live_rule_fingerprint"] == g.f1_live_fingerprint()
    changed = g.load_signal_quality_evidence(_evidence(tmp_path), current_live_fingerprint="0" * 16)
    assert changed.status == "STALE" and "canlı F1" in changed.detail


def test_fingerprint_and_rule_are_the_registered_ones():
    pytest.importorskip("numpy")
    from trading.backtest import signal_quality as sq

    assert g.SIGNAL_QUALITY_FINGERPRINT_FILES == sq.FINGERPRINT_FILES
    assert g.SIGNAL_QUALITY_ALERT_LOGIC == sq.LONG_ALERT_LOGIC
    assert g.SIGNAL_QUALITY_ALERT_CONSTANTS == sq.LONG_ALERT_CONSTANTS
    assert g.signal_quality_fingerprint() == sq.logic_fingerprint()
    assert (g.F1_ATR15_MAX, g.F1_REL24_MAX) == (sq.LIKIT_ATR15_MAX, sq.LIKIT_REL24_MAX)
    assert g.F1_MIN_KEPT == sq.CONFIRM_MIN_N and g.F1_CHANGE_BARS == sq.DAY_BARS
    assert g.F1_INTERVAL == sq.BAR
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


def _positive_pass(payload, *, n=500, stress=0.1):
    test = payload["tests"][g.F1_TEST]
    test["verdict"] = "PASS"
    test["kept_net_pct"] = {"n": n, "mean": 0.5, "ci": [0.1, 0.9], "h1": 0.4, "h2": 0.6}
    test["kept_excess_pct"] = {"n": n, "mean": 0.6, "ci": [0.2, 1.0], "h1": 0.5, "h2": 0.7}
    test["kept_stress_net_pct"] = {"n": n, "mean": stress, "ci": [-0.3, 0.5], "h1": 0.1, "h2": 0.1}


def test_pass_needs_every_pre_registered_condition(tmp_path):
    ok = g.load_signal_quality_evidence(_evidence(tmp_path, _positive_pass))
    assert ok.status == "OK" and ok.tests[g.F1_TEST].verdict == "PASS"
    few = _evidence(tmp_path, lambda p: _positive_pass(p, n=99))
    assert g.load_signal_quality_evidence(few).status == "INVALID"
    costly = _evidence(tmp_path, lambda p: _positive_pass(p, stress=-0.05))
    assert g.load_signal_quality_evidence(costly).status == "INVALID"


def test_f1_label_marks_only_the_dropped_alerts():
    calm = _label()
    assert calm.status == "PASSED" and calm.text == "geçti" and "kâr ettiği gösterilmedi" in calm.detail
    assert _label(atr=1.0, change=6.0).status == "PASSED"   # inclusive edges
    volatile = _label(atr=1.4)
    assert volatile.status == "AVOID" and volatile.text == "KAÇIN" and "oynak" in volatile.detail
    chasing = _label(change=9.0, median=1.0)
    assert chasing.status == "AVOID" and "kovalıyor" in chasing.detail and "+8.0 puan" in chasing.detail


def test_missing_inputs_are_unknown_and_unconfirmed_evidence_gives_no_label(tmp_path):
    for kwargs in ({"atr": None}, {"change": None}, {"median": None}, {"atr": float("nan")}):
        assert _label(**kwargs).status == "UNKNOWN"
    assert g.atr15_from_metadata({"long_metrics": {"status": "INSUFFICIENT_KLINES", "atr_pct": 0.5}}) is None
    assert g.atr15_from_metadata({"long_metrics": {"status": "READY", "atr_pct": 0.5}}) == 0.5
    stale = g.load_signal_quality_evidence(_evidence(tmp_path), current_fingerprint="0" * 16)
    assert _label(atr=3.0, evidence=stale).status == "NOT_APPLIED"

    def no_effect(payload):
        payload["tests"][g.F1_TEST]["verdict"] = "NO_EFFECT"
        payload["tests"][g.F1_TEST]["kept_minus_dropped_net_pct"]["ci"] = [-0.1, 1.0]
    neutral = g.load_signal_quality_evidence(_evidence(tmp_path, no_effect))
    assert neutral.status == "OK" and _label(atr=3.0, evidence=neutral).status == "NOT_APPLIED"


def test_closed_candle_change_matches_the_research_feature():
    closes = [10.0] + [10.0] * 95 + [11.0]                      # 97 closed candles: 96 apart
    assert g.closed_change_24h(_m15(NOW, closes), now=NOW) == pytest.approx(10.0)   # forming 999 ignored
    assert g.closed_change_24h(_m15(NOW, closes[1:]), now=NOW) is None              # only 95 apart
    holed = _m15(NOW, closes)
    del holed[0]                                                 # the candle 24h back is missing
    assert g.closed_change_24h(holed, now=NOW) is None
    assert g.closed_change_24h(_m15(NOW, closes), now=NOW + 3600) is None           # stale feed
    assert g.universe_median_change({f"S{k}": float(k) for k in range(49)}) is None
    values = {f"S{k}": float(k) for k in range(51)} | {"X": None}
    assert g.universe_median_change(values) == 25.0


def test_status_line_reports_both_tests():
    text = g.evidence_status_text()
    assert "Sinyal kalitesi — F1 sakin/kovalamayan: KAYBI_AZALTIR · F2 baz: NO_EFFECT" in text


# ---------------------------------------------------------------------------
# Bot wiring
# ---------------------------------------------------------------------------


class _Klines:
    """15m: SOL +12% over 24h on closed candles, the rest +1%; 1h: flat candles for the stop plan."""

    def __init__(self):
        self.calls = []

    def fetch_klines(self, symbol, interval_seconds, limit):
        self.calls.append((symbol, interval_seconds))
        if interval_seconds == 900:
            end = 11.2 if symbol == "SOLUSDT" else 10.1
            return _m15(NOW, [10.0] * 96 + [end])
        start = NOW - NOW % 3600 - 30 * 3600
        return [[(start + k * 3600) * 1000, "10", "10.2", "9.8", "10", "1", (start + (k + 1) * 3600) * 1000 - 1, "1"]
                for k in range(30)]


class _Tickers:
    def fetch_tickers(self):
        from acce_unified.models import CexTicker

        names = ["SOLUSDT"] + [f"C{k:02d}USDT" for k in range(59)]
        return [CexTicker(symbol=n, last_price=10.0, change_pct=2.0, quote_volume=5e6 + k, venue="MEXC",
                          bid_price=9.999, ask_price=10.001) for k, n in enumerate(names)]


def test_alert_panel_and_radar_log_use_closed_candles_not_the_ticker(monkeypatch):
    sent = []
    klines = _Klines()
    monkeypatch.setattr(bot, "send", lambda text, keyboard=None, html_mode=False: sent.append(text))
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    monkeypatch.setitem(bot.STATE, "radar_log", [])
    monkeypatch.setattr(bot, "LIQUID_LONG_ALERTS", True)
    monkeypatch.setattr(bot, "LIQUID_AVOID_ALERTS", True)    # this test reads the KAÇIN alert text
    monkeypatch.setattr(bot, "KLINES", klines)
    monkeypatch.setattr(bot.ENGINE, "cex_provider", _Tickers())
    monkeypatch.setattr(bot, "F1_CACHE", {})
    monkeypatch.setattr(bot.time, "time", lambda: NOW)
    meta = {"change_pct": 2.0,           # the ticker says calm; the closed candles say +12% vs a +1% median
            "long_metrics": {"status": "READY", "atr_pct": 0.6},
            "market_context": {"median_change_pct": 2.0, "universe_size": 60}}
    snapshot = {
        "generated_at": NOW, "liquid_market_context": {"regime": "RISK_ON"},
        "liquid_long_candidates": [{"symbol": "SOLUSDT", "score": 70, "metadata": dict(meta, last_price=10.0,
                                                                                         spread_bps=3)}],
    }
    bot._liquid_radar(snapshot)
    assert "Kalite (F1) KAÇIN" in sent[0] and "kovalıyor" in sent[0]
    entry = bot.STATE["radar_log"][0]
    assert entry["quality"] == "AVOID" and entry["quality_inputs"]["rel_24h"] == pytest.approx(11.0)
    fetched = sum(1 for _, interval in klines.calls if interval == 900)
    bot.format_longs(snapshot)                          # same 15m bar: the cached closed changes are reused
    assert sum(1 for _, interval in klines.calls if interval == 900) == fetched
    assert entry["can_authorize_trade"] is False
    assert "Kalite filtresi (F1): KAÇIN" in bot.format_longs(snapshot)
    stopped = dict(entry, status=la.STOPPED, result_pct=-4.0, exit_price=9.6, closed_at=NOW + 3600)
    calm = dict(entry, symbol="ADAUSDT", quality="PASSED", status=la.EXPIRED, result_pct=1.0, closed_at=NOW + 3600)
    radar = la.format_radar([stopped, calm], now=NOW + 7200)
    assert "F1 kaçın" in radar and "F1 geçti" in radar
    assert "F1 etiketine göre: geçti 1 kayıt ort. %+1.0 · kaçın 1 kayıt ort. %-4.0" in radar


def test_provider_failure_leaves_the_label_unknown(monkeypatch):
    class Down:
        def fetch_tickers(self):
            raise RuntimeError("https://api.mexc.com/?signature=SECRET 503")

    monkeypatch.setattr(bot.ENGINE, "cex_provider", Down())
    monkeypatch.setattr(bot, "KLINES", _Klines())
    monkeypatch.setattr(bot, "F1_CACHE", {})
    label = bot._f1_label("SOLUSDT", {"long_metrics": {"status": "READY", "atr_pct": 0.6}}, NOW)
    assert label.status == "UNKNOWN" and "SECRET" not in label.detail


def _wire(monkeypatch, *, avoid_alerts):
    sent = []
    monkeypatch.setattr(bot, "send", lambda text, keyboard=None, html_mode=False: sent.append(text))
    monkeypatch.setattr(bot, "_save_state", lambda: None)
    monkeypatch.setitem(bot.STATE, "radar_log", [])
    monkeypatch.setattr(bot, "LIQUID_LONG_ALERTS", True)
    monkeypatch.setattr(bot, "LIQUID_AVOID_ALERTS", avoid_alerts)
    monkeypatch.setattr(bot, "KLINES", _Klines())
    monkeypatch.setattr(bot.ENGINE, "cex_provider", _Tickers())
    monkeypatch.setattr(bot, "F1_CACHE", {})
    monkeypatch.setattr(bot.time, "time", lambda: NOW)
    return sent


def _candidate(symbol="SOLUSDT", atr=0.6):
    meta = {"change_pct": 2.0, "long_metrics": {"status": "READY", "atr_pct": atr},
            "market_context": {"median_change_pct": 2.0, "universe_size": 60}, "last_price": 10.0, "spread_bps": 3}
    return {"symbol": symbol, "score": 70, "metadata": meta}


def test_avoid_alerts_are_logged_but_not_pushed_by_default(monkeypatch):
    assert bot.LIQUID_AVOID_ALERTS is False                 # the user's decision is the default
    sent = _wire(monkeypatch, avoid_alerts=False)
    snapshot = {"generated_at": NOW, "liquid_market_context": {"regime": "RISK_ON"},
                "liquid_long_candidates": [_candidate("SOLUSDT"), _candidate("C01USDT")]}
    bot._liquid_radar(snapshot)
    log = {e["symbol"]: e for e in bot.STATE["radar_log"]}
    assert log["SOLUSDT"]["quality"] == "AVOID" and log["SOLUSDT"]["muted"] is True
    assert log["C01USDT"]["quality"] == "PASSED" and log["C01USDT"]["muted"] is False
    assert len(sent) == 1 and "C01USDT" in sent[0] and "SOLUSDT" not in sent[0]

    stopped = dict(log["SOLUSDT"], status=la.OPEN, checked_until=NOW)
    bot.STATE["radar_log"] = [stopped]
    sent.clear()
    stop_hit = la.track_entry(stopped, [[(NOW + 900 - NOW % 900) * 1000, "10", "10", "1", "1"]], now=NOW + 3 * 900)
    assert stop_hit[1] == la.STOPPED
    monkeypatch.setattr(bot, "_track_radar", lambda rows, now: ([stop_hit[0]], [stop_hit[0]]))
    bot._liquid_radar({"liquid_long_candidates": []})
    assert sent == []                                       # its opening alert was never sent: no stop alert
    assert "sessiz" in la.format_radar([stop_hit[0]], now=NOW + 3 * 900)


def test_avoid_alerts_can_be_pushed_again_and_unknown_is_never_muted(monkeypatch):
    sent = _wire(monkeypatch, avoid_alerts=True)
    bot._liquid_radar({"generated_at": NOW, "liquid_market_context": {"regime": "RISK_ON"},
                       "liquid_long_candidates": [_candidate("SOLUSDT")]})
    assert len(sent) == 1 and "KAÇIN" in sent[0] and bot.STATE["radar_log"][0]["muted"] is False

    sent = _wire(monkeypatch, avoid_alerts=False)
    unknown = _candidate("SOLUSDT", atr=None)
    bot._liquid_radar({"generated_at": NOW, "liquid_market_context": {"regime": "RISK_ON"},
                       "liquid_long_candidates": [unknown]})
    assert len(sent) == 1 and "bilinmiyor" in sent[0]       # UNKNOWN is not AVOID: still pushed
