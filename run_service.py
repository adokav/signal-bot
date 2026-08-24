"""Production service entrypoint.

Starts the raw macro observation collector as a daemon and then hands control to
Signal Bot's existing main function.  The collector has no trading authority.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import threading
import time

import bot
from macro_runtime import MacroCollector


log = logging.getLogger("signal-bot-macro-runtime")
MACRO_ENABLED = os.getenv("MACRO_DATA_COLLECTION_ENABLED", "1") == "1"
MACRO_INTERVAL_SECONDS = max(900, int(os.getenv("MACRO_DATA_COLLECTION_INTERVAL_SECONDS", "21600")))
MACRO_ARCHIVE_PATH = Path(os.getenv("MACRO_OBSERVATION_ARCHIVE_PATH", "/data/macro_observations.jsonl"))
MACRO_STATUS_PATH = Path(os.getenv("MACRO_DATA_STATUS_PATH", "/data/macro_status.json"))


def _write_status(payload: dict) -> None:
    try:
        MACRO_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = MACRO_STATUS_PATH.with_suffix(MACRO_STATUS_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(MACRO_STATUS_PATH)
    except Exception:
        log.warning("Macro status file could not be written", exc_info=True)


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


def main() -> None:
    if MACRO_ENABLED:
        threading.Thread(target=macro_collection_loop, name="macro-data-collector", daemon=True).start()
    else:
        log.warning("Macro data collection is disabled")
    bot.main()


if __name__ == "__main__":
    main()
