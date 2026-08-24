from datetime import datetime, timezone
from pathlib import Path

from macro_data import MacroObservation
from macro_runtime import MacroCollector


NOW = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)


def _obs(provider, series, value, source_id):
    return MacroObservation(
        provider=provider,
        series=series,
        observation_time="2026-08-24T00:00:00+00:00",
        available_time="2026-08-25T00:00:00+00:00",
        ingested_time="2026-08-25T00:00:00+00:00",
        value=value,
        source_id=source_id,
    )


class FakeFred:
    def fetch_latest(self, logical_series, *, as_of=None):
        if logical_series == "rrp":
            raise RuntimeError("provider hiccup")
        return _obs("fred", logical_series, 1.0, logical_series.upper())


class FakeBoj:
    def fetch_latest(self, logical_series, *, as_of=None):
        return _obs("boj", logical_series, 0.5, "FM01:STRDCLUCON")


def test_collector_isolates_one_provider_series_failure(tmp_path: Path):
    collector = MacroCollector(
        tmp_path / "macro.jsonl",
        fred=FakeFred(),
        boj=FakeBoj(),
        now=lambda: NOW,
    )
    status = collector.collect_once()
    assert status.appended == 7
    assert any(item.startswith("fred:rrp:RuntimeError") for item in status.errors)
    assert "boj_call_rate" in status.fetched_series
    assert status.ok is False


def test_collector_deduplicates_identical_first_seen_observations(tmp_path: Path):
    collector = MacroCollector(
        tmp_path / "macro.jsonl",
        fred=FakeFred(),
        boj=FakeBoj(),
        now=lambda: NOW,
    )
    first = collector.collect_once()
    second = collector.collect_once()
    assert first.appended == 7
    assert second.appended == 0
    assert len(second.unchanged_series) == 7
    assert len(collector.archive.read_all()) == 7


def test_status_dict_exposes_errors_without_claiming_success(tmp_path: Path):
    collector = MacroCollector(
        tmp_path / "macro.jsonl",
        fred=FakeFred(),
        boj=FakeBoj(),
        now=lambda: NOW,
    )
    payload = collector.collect_once().to_dict()
    assert payload["ok"] is False
    assert payload["appended"] == 7
    assert payload["errors"]
