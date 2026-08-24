import json

import run_service


def test_macro_research_health_never_grants_trade_authority(tmp_path, monkeypatch):
    root = tmp_path / "research"
    root.mkdir(parents=True)
    (root / "backfill_status.json").write_text(json.dumps({
        "state": "COMPLETED",
        "replay_rows": 100,
        "evidence_rows": 3,
    }), "utf-8")
    (root / "macro_evidence.json").write_text(json.dumps({
        "generated_at": "2026-08-25T00:00:00+00:00",
        "rows": [{"factor": "us_10y_d1_bp"}, {"factor": "tga_pct_d1"}],
    }), "utf-8")
    (root / "macro_alfred_reconstructed.jsonl").write_text("{}\n{}\n", "utf-8")
    (root / "btcusdt_1h_bars.jsonl").write_text("{}\n", "utf-8")
    (root / "macro_btc_daily_replay.jsonl").write_text("{}\n{}\n{}\n", "utf-8")
    live = tmp_path / "macro_observations.jsonl"
    live.write_text("{}\n", "utf-8")
    live_status = tmp_path / "macro_status.json"
    live_status.write_text(json.dumps({"ok": True}), "utf-8")

    monkeypatch.setenv("MACRO_HISTORICAL_ROOT", str(root))
    monkeypatch.setattr(run_service, "MACRO_ARCHIVE_PATH", live)
    monkeypatch.setattr(run_service, "MACRO_STATUS_PATH", live_status)
    monkeypatch.setattr(run_service, "HISTORICAL_BACKFILL_ENABLED", True)

    payload = run_service.macro_research_health_payload()
    assert payload["ok"] is True
    assert payload["can_authorize_trade"] is False
    assert payload["historical_research"]["state"] == "COMPLETED"
    assert payload["historical_research"]["evidence_summary_rows"] == 2
    assert payload["historical_research"]["replay_rows"] == 3
    assert payload["live_first_seen"]["archive_rows"] == 1
    assert "API" not in json.dumps(payload).upper()


def test_macro_research_endpoint_is_fail_visible_not_false_healthy(tmp_path, monkeypatch):
    root = tmp_path / "research"
    root.mkdir(parents=True)
    (root / "backfill_status.json").write_text(json.dumps({
        "state": "FAILED",
        "error": "RuntimeError: provider down",
    }), "utf-8")
    monkeypatch.setenv("MACRO_HISTORICAL_ROOT", str(root))
    monkeypatch.setattr(run_service, "MACRO_ARCHIVE_PATH", tmp_path / "missing-live.jsonl")
    monkeypatch.setattr(run_service, "MACRO_STATUS_PATH", tmp_path / "missing-status.json")

    response = run_service.bot.APP.test_client().get("/macro-research")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["historical_research"]["state"] == "FAILED"
    assert payload["can_authorize_trade"] is False
