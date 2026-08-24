from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from macro_backfill_runtime import HistoricalBackfillConfig, MacroHistoricalBackfillJob
from macro_data import MacroObservation
from macro_replay import PriceBar


NOW = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)


def _obs(series: str, day: int, value: float) -> MacroObservation:
    ts = datetime(2020, 1, day, tzinfo=timezone.utc)
    available = ts + timedelta(days=1)
    return MacroObservation(
        provider="fred_alfred_reconstruction",
        series=series,
        source_id=series,
        observation_time=ts.isoformat(),
        available_time=available.isoformat(),
        ingested_time=available.isoformat(),
        value=value,
    )


class _Fred:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def fetch_core(self, *, start, end):
        self.calls += 1
        if self.fail:
            raise RuntimeError("fred unavailable")
        rows = []
        # Enough history for all core factor transforms. Values are simple but
        # valid; this test exercises orchestration, not predictive research.
        for series in ("us_2y", "us_10y", "us_30y", "us_10y_real", "fed_assets", "rrp", "tga"):
            for day in range(1, 8):
                rows.append(_obs(series, day, float(day) + 1.0))
        return tuple(rows)


class _Btc:
    def __init__(self):
        self.calls = 0

    def fetch(self, *, start, end):
        self.calls += 1
        bars = []
        current = start + timedelta(hours=1)
        price = 100.0
        while current <= end:
            bars.append(PriceBar(current.isoformat(), price, price + 2, price - 2, price + 1))
            price += 1
            current += timedelta(hours=1)
        return tuple(bars)


def _config(tmp_path: Path) -> HistoricalBackfillConfig:
    return HistoricalBackfillConfig(
        start_date=date(2020, 1, 7),
        end_lag_days=(NOW.date() - date(2020, 1, 9)).days,
        root=tmp_path / "research",
        min_train_rows=1,
        test_rows=1,
    )


def test_runtime_never_allows_live_archive_collision(tmp_path: Path):
    config = HistoricalBackfillConfig(date(2020, 1, 1), 8, tmp_path)
    with pytest.raises(ValueError, match="collides"):
        MacroHistoricalBackfillJob(config, live_archive=config.reconstructed_macro_path, fred=_Fred(), btc=_Btc())


def test_failed_job_writes_failure_status_but_no_completion_marker(tmp_path: Path):
    config = _config(tmp_path)
    job = MacroHistoricalBackfillJob(
        config,
        live_archive=tmp_path / "macro_observations.jsonl",
        fred=_Fred(fail=True),
        btc=_Btc(),
        now=lambda: NOW,
    )
    with pytest.raises(RuntimeError, match="fred unavailable"):
        job.run_once()
    status = json.loads(config.status_path.read_text("utf-8"))
    assert status["state"] == "FAILED"
    assert not config.marker_path.exists()


def test_completion_marker_prevents_duplicate_rebuild(tmp_path: Path, monkeypatch):
    config = _config(tmp_path)
    fred, btc = _Fred(), _Btc()

    # Evidence creation requires meaningful history; isolate orchestration here.
    monkeypatch.setattr(
        "macro_backfill_runtime.build_evidence_report",
        lambda replay, factors, min_train_rows, test_rows: (),
    )
    job = MacroHistoricalBackfillJob(
        config,
        live_archive=tmp_path / "macro_observations.jsonl",
        fred=fred,
        btc=btc,
        now=lambda: NOW,
    )
    first = job.run_once()
    assert first.state == "COMPLETED"
    assert config.marker_path.exists()
    assert fred.calls == 1 and btc.calls == 1

    second = job.run_once()
    assert second.state == "SKIPPED_ALREADY_COMPLETE"
    assert fred.calls == 1 and btc.calls == 1


def test_force_rebuild_ignores_existing_marker(tmp_path: Path, monkeypatch):
    config = _config(tmp_path)
    fred, btc = _Fred(), _Btc()
    monkeypatch.setattr(
        "macro_backfill_runtime.build_evidence_report",
        lambda replay, factors, min_train_rows, test_rows: (),
    )
    job = MacroHistoricalBackfillJob(
        config,
        live_archive=tmp_path / "macro_observations.jsonl",
        fred=fred,
        btc=btc,
        now=lambda: NOW,
    )
    job.run_once()
    job.run_once(force=True)
    assert fred.calls == 2 and btc.calls == 2
