from datetime import datetime, timezone
from pathlib import Path

import pytest

from macro_data import JsonlMacroArchive, MacroObservation


def _obs(**overrides):
    values = dict(
        provider="fred",
        series="us_10y",
        source_id="DGS10",
        observation_time="2026-08-19T00:00:00+00:00",
        available_time="2026-08-20T12:00:00+00:00",
        ingested_time="2026-08-20T12:00:01+00:00",
        value=4.25,
    )
    values.update(overrides)
    return MacroObservation(**values)


def test_rejects_future_availability():
    with pytest.raises(ValueError):
        _obs(
            available_time="2026-08-20T12:00:02+00:00",
            ingested_time="2026-08-20T12:00:01+00:00",
        ).validate()


def test_rejects_observation_after_availability():
    with pytest.raises(ValueError):
        _obs(
            observation_time="2026-08-20T12:00:02+00:00",
            available_time="2026-08-20T12:00:01+00:00",
        ).validate()


def test_archive_is_append_only(tmp_path: Path):
    archive = JsonlMacroArchive(tmp_path / "macro.jsonl")
    archive.append([_obs(value=4.2)])
    archive.append([_obs(value=4.3)])
    lines = (tmp_path / "macro.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert '"value": 4.2' in lines[0]
    assert '"value": 4.3' in lines[1]
