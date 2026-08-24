"""Production service entrypoint.

Starts raw macro first-seen collection and, when explicitly enabled, a one-shot
historical research backfill as isolated daemon threads before handing control
to Signal Bot's existing main function. Neither macro thread has trading
authority.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
import time

import bot
from flask import jsonify
from macro_backfill_runtime import HistoricalBackfillConfig, MacroHistoricalBackfillJob
from macro_runtime import MacroCollector


log = logging.getLogger("signal-bot-macro-runtime")
MACRO_ENABLED = os.getenv("MACRO_DATA_COLLECTION_ENABLED", "1") == "1"
MACRO_INTERVAL_SECONDS = max(900, int(os.getenv("MACRO_DATA_COLLECTION_INTERVAL_SECONDS", "21600")))
MACRO_ARCHIVE_PATH = Path(os.getenv("MACRO_OBSERVATION_ARCHIVE_PATH", "/data/macro_observations.jsonl"))
MACRO_STATUS_PATH = Path(os.getenv("MACRO_DATA_STATUS_PATH", "/data/macro_status.json"))
HISTORICAL_BACKFILL_ENABLED = os.getenv("MACRO_HISTORICAL_BACKFILL_ENABLED", "0") == "1"


def _write_status(payload: dict) -> None:
    try:
        MACRO_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MACRO_STATUS_PATH.with_suffix(MACRO_STATUS_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(MACRO_STATUS_PATH)
    except Exception:
        log.warning("Macro status file could not be written", exc_info=True)


def _safe_read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _line_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return None


def macro_research_health_payload() -> dict:
    """Sanitized observability only; never exposes API keys or creates a signal."""
    config = HistoricalBackfillConfig.from_env()
    live_status = _safe_read_json(MACRO_STATUS_PATH)
    backfill_status = _safe_read_json(config.status_path)
    evidence = _safe_read_json(config.evidence_path)
    evidence_rows = evidence.get("rows") if isinstance(evidence, dict) else None
    if not isinstance(evidence_rows, list):
        evidence_rows = []

    state = str((backfill_status or {}).get("state") or "NOT_STARTED")
    return {
        "ok": state in {"COMPLETED", "SKIPPED_ALREADY_COMPLETE"},
        "scientific_boundary": "EVIDENCE_ONLY_NO_SCORE_NO_REGIME_NO_TRADING_AUTHORITY",
        "live_first_seen": {
            "status": live_status,
            "archive_rows": _line_count(MACRO_ARCHIVE_PATH),
        },
        "historical_research": {
            "enabled": HISTORICAL_BACKFILL_ENABLED,
            "state": state,
            "status": backfill_status,
            "reconstructed_macro_rows": _line_count(config.reconstructed_macro_path),
            "btc_hourly_bar_rows": _line_count(config.btc_path),
            "replay_rows": _line_count(config.replay_path),
            "evidence_summary_rows": len(evidence_rows),
            "evidence_generated_at": (evidence or {}).get("generated_at") if isinstance(evidence, dict) else None,
        },
        "can_authorize_trade": False,
    }


@bot.APP.get("/macro-research")
def macro_research_health():
    return jsonify(macro_research_health_payload())


def macro_collection_loop() -> None:
    collector = MacroCollector(MACRO_ARCHIVE_PATH)
    while True:
        try:
            status = collector.collect_once()
            _write_status(status.to_dict())
            if status.errors:
                log.warning(
                    "Macro collection partial: appended=%s fetched=%s errors=%s",
                    status.appended,
                    len(status.fetched_series),
                    "; ".join(status.errors),
                )
            else:
                log.info(
                    "Macro collection complete: appended=%s fetched=%s unchanged=%s",
                    status.appended,
                    len(status.fetched_series),
                    len(status.unchanged_series),
                )
        except Exception as exc:
            log.exception("Macro collection cycle failed")
            _write_status({"ok": False, "fatal_error": f"{type(exc).__name__}: {exc}"})
        time.sleep(MACRO_INTERVAL_SECONDS)


def macro_historical_backfill_once() -> None:
    """Run research reconstruction once; any failure is isolated from the bot."""
    try:
        config = HistoricalBackfillConfig.from_env()
        job = MacroHistoricalBackfillJob(config, live_archive=MACRO_ARCHIVE_PATH)
        status = job.run_once()
        if status.state == "COMPLETED":
            log.info(
                "Macro historical research complete: macro=%s btc_bars=%s replay=%s complete=%s evidence=%s",
                status.macro_observations,
                status.btc_bars,
                status.replay_rows,
                status.complete_factor_rows,
                status.evidence_rows,
            )
        else:
            log.info("Macro historical research: %s", status.state)
    except Exception:
        log.exception("Macro historical research job failed; live bot remains unaffected")


def main() -> None:
    if MACRO_ENABLED:
        threading.Thread(target=macro_collection_loop, name="macro-data-collector", daemon=True).start()
    else:
        log.warning("Macro data collection is disabled")

    if HISTORICAL_BACKFILL_ENABLED:
        threading.Thread(
            target=macro_historical_backfill_once,
            name="macro-historical-backfill",
            daemon=True,
        ).start()
    else:
        log.info("Macro historical backfill is disabled")

    bot.main()


if __name__ == "__main__":
    main()
