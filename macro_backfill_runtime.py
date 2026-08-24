"""One-shot historical macro research job for the Render service.

The job is intentionally isolated from live trading and from the live first-seen
macro archive. Failure must never prevent the Signal Bot web/Telegram service
from starting. Successful runs are marked by a configuration fingerprint so a
redeploy does not repeatedly download and rebuild the same history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from macro_backfill import BinanceHourlyBackfill, FredRevisionBackfill
from macro_dataset import build_daily_replay_rows, write_replay_jsonl
from macro_evidence import build_evidence_report


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), "utf-8")
    tmp.replace(path)


def _atomic_jsonl_dataclasses(path: Path, rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = tuple(rows)
    with tmp.open("w", encoding="utf-8") as handle:
        for row in data:
            handle.write(json.dumps(asdict(row), sort_keys=True) + "\n")
    tmp.replace(path)
    return len(data)


@dataclass(frozen=True)
class HistoricalBackfillConfig:
    start_date: date
    end_lag_days: int
    root: Path
    min_train_rows: int = 365
    test_rows: int = 90

    @classmethod
    def from_env(cls) -> "HistoricalBackfillConfig":
        start = date.fromisoformat(os.getenv("MACRO_HISTORICAL_START_DATE", "2017-08-18"))
        lag = max(8, int(os.getenv("MACRO_HISTORICAL_END_LAG_DAYS", "8")))
        root = Path(os.getenv("MACRO_HISTORICAL_ROOT", "/data/research/macro"))
        return cls(start_date=start, end_lag_days=lag, root=root)

    def end_date(self, now: datetime) -> date:
        current = now.astimezone(timezone.utc).date()
        end = current - timedelta(days=self.end_lag_days)
        if end < self.start_date:
            raise ValueError("historical end precedes configured start")
        return end

    @property
    def reconstructed_macro_path(self) -> Path:
        return self.root / "macro_alfred_reconstructed.jsonl"

    @property
    def btc_path(self) -> Path:
        return self.root / "btcusdt_1h_bars.jsonl"

    @property
    def replay_path(self) -> Path:
        return self.root / "macro_btc_daily_replay.jsonl"

    @property
    def evidence_path(self) -> Path:
        return self.root / "macro_evidence.json"

    @property
    def status_path(self) -> Path:
        return self.root / "backfill_status.json"

    @property
    def marker_path(self) -> Path:
        return self.root / "backfill_complete.json"

    def validate_paths(self, live_archive: Path) -> None:
        live = live_archive.resolve()
        for path in (
            self.reconstructed_macro_path,
            self.btc_path,
            self.replay_path,
            self.evidence_path,
            self.status_path,
            self.marker_path,
        ):
            if path.resolve() == live:
                raise ValueError("historical research path collides with live macro archive")
        if self.reconstructed_macro_path.name == "macro_observations.jsonl":
            raise ValueError("historical reconstruction cannot use live archive filename")

    def fingerprint(self, end: date) -> str:
        payload = {
            "schema": 1,
            "start": self.start_date.isoformat(),
            "end": end.isoformat(),
            "end_lag_days": self.end_lag_days,
            "min_train_rows": self.min_train_rows,
            "test_rows": self.test_rows,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HistoricalBackfillStatus:
    state: str
    started_at: str
    completed_at: str | None
    start_date: str
    end_date: str
    fingerprint: str
    macro_observations: int = 0
    btc_bars: int = 0
    replay_rows: int = 0
    complete_factor_rows: int = 0
    labeled_rows: int = 0
    evidence_rows: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class MacroHistoricalBackfillJob:
    """Build historical evidence once per date/config fingerprint."""

    def __init__(
        self,
        config: HistoricalBackfillConfig,
        *,
        live_archive: str | Path,
        fred: FredRevisionBackfill | None = None,
        btc: BinanceHourlyBackfill | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.config = config
        self.live_archive = Path(live_archive)
        self.config.validate_paths(self.live_archive)
        self.fred = fred or FredRevisionBackfill()
        self.btc = btc or BinanceHourlyBackfill()
        self.now = now

    def _marker_matches(self, fingerprint: str) -> bool:
        try:
            payload = json.loads(self.config.marker_path.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return False
        return payload.get("fingerprint") == fingerprint and payload.get("state") == "COMPLETED"

    def run_once(self, *, force: bool = False) -> HistoricalBackfillStatus:
        started = self.now().astimezone(timezone.utc)
        end = self.config.end_date(started)
        fingerprint = self.config.fingerprint(end)
        if not force and self._marker_matches(fingerprint):
            status = HistoricalBackfillStatus(
                state="SKIPPED_ALREADY_COMPLETE",
                started_at=started.isoformat(),
                completed_at=started.isoformat(),
                start_date=self.config.start_date.isoformat(),
                end_date=end.isoformat(),
                fingerprint=fingerprint,
            )
            _atomic_json(self.config.status_path, status.to_dict())
            return status

        running = HistoricalBackfillStatus(
            state="RUNNING",
            started_at=started.isoformat(),
            completed_at=None,
            start_date=self.config.start_date.isoformat(),
            end_date=end.isoformat(),
            fingerprint=fingerprint,
        )
        _atomic_json(self.config.status_path, running.to_dict())

        try:
            macro_start = self.config.start_date - timedelta(days=30)
            observations = self.fred.fetch_core(start=macro_start, end=end)
            macro_n = _atomic_jsonl_dataclasses(self.config.reconstructed_macro_path, observations)

            price_start = datetime.combine(
                self.config.start_date - timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
            )
            # end is lagged >=8d, therefore end+8d is not in the future.
            price_end = datetime.combine(end + timedelta(days=8), datetime.max.time(), tzinfo=timezone.utc)
            bars = self.btc.fetch(start=price_start, end=price_end)
            btc_n = _atomic_jsonl_dataclasses(self.config.btc_path, bars)

            replay = build_daily_replay_rows(
                observations,
                bars,
                start=self.config.start_date,
                end=end,
            )
            replay_n = write_replay_jsonl(replay, self.config.replay_path)
            complete_n = sum(row.factor_complete for row in replay)
            labeled_n = sum(bool(row.outcomes) for row in replay)

            factor_names = sorted(
                {name for row in replay for name, value in row.factors.items() if value is not None}
            )
            evidence = build_evidence_report(
                replay,
                factors=factor_names,
                min_train_rows=self.config.min_train_rows,
                test_rows=self.config.test_rows,
            )
            _atomic_json(
                self.config.evidence_path,
                {
                    "generated_at": self.now().astimezone(timezone.utc).isoformat(),
                    "scientific_boundary": "EVIDENCE_ONLY_NO_SCORE_NO_REGIME_NO_TRADING_AUTHORITY",
                    "rows": [row.to_dict() for row in evidence],
                },
            )

            completed = self.now().astimezone(timezone.utc)
            status = HistoricalBackfillStatus(
                state="COMPLETED",
                started_at=started.isoformat(),
                completed_at=completed.isoformat(),
                start_date=self.config.start_date.isoformat(),
                end_date=end.isoformat(),
                fingerprint=fingerprint,
                macro_observations=macro_n,
                btc_bars=btc_n,
                replay_rows=replay_n,
                complete_factor_rows=complete_n,
                labeled_rows=labeled_n,
                evidence_rows=len(evidence),
            )
            _atomic_json(self.config.status_path, status.to_dict())
            # Marker is written last. Any exception before this point leaves no
            # completion marker and the next deploy can safely retry.
            _atomic_json(self.config.marker_path, status.to_dict())
            return status
        except Exception as exc:
            failed = HistoricalBackfillStatus(
                state="FAILED",
                started_at=started.isoformat(),
                completed_at=self.now().astimezone(timezone.utc).isoformat(),
                start_date=self.config.start_date.isoformat(),
                end_date=end.isoformat(),
                fingerprint=fingerprint,
                error=f"{type(exc).__name__}: {exc}",
            )
            _atomic_json(self.config.status_path, failed.to_dict())
            raise
