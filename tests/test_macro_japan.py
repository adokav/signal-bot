from datetime import date, datetime, timezone

import pytest

from macro_japan import BojCallRateBackfill, MofJgbBackfill


NOW = datetime(2026, 8, 25, 4, 45, tzinfo=timezone.utc)
SAMPLE = """Interest Rate,,,,,,,,,,,,,,,(Unit : %)\nDate,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y\n2026/8/14,1.100,1.200,1.300,1.400,1.500,1.600,1.700,1.800,1.900,2.000,2.100,2.200,2.300,2.400,2.500\n2026/8/17,1.110,1.210,1.310,1.410,1.510,1.610,1.710,1.810,1.910,2.010,2.110,2.210,2.310,2.410,2.510\n2026/8/18,1.120,1.220,1.320,1.420,1.520,1.620,1.720,1.820,1.920,2.020,2.120,2.220,2.320,2.420,2.520\n"""


class _Response:
    def __init__(self, *, payload=None, text="", status_error=None):
        self._payload = payload
        self.text = text
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        return self._payload


class _Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_mof_snapshot_uses_actual_ingestion_not_historical_release_time():
    rows = MofJgbBackfill.parse_csv(
        SAMPLE,
        start=date(2026, 8, 14),
        end=date(2026, 8, 17),
        ingested_at=NOW,
    )
    first = [r for r in rows if r.series == "jgb_10y" and r.observation_time.startswith("2026-08-14")][0]
    assert first.available_time == NOW.isoformat()
    assert first.ingested_time == NOW.isoformat()
    assert first.value == 2.0
    assert first.provider == "mof_jgb_unversioned_snapshot"


def test_mof_current_snapshot_can_include_final_historical_row_without_backdating():
    rows = MofJgbBackfill.parse_csv(
        SAMPLE,
        start=date(2026, 8, 18),
        end=date(2026, 8, 18),
        ingested_at=NOW,
    )
    assert {r.series for r in rows} == {"jgb_2y", "jgb_10y", "jgb_30y"}
    assert all(r.available_time == NOW.isoformat() for r in rows)


def test_mof_rejects_missing_required_column():
    broken = "Interest Rate\nDate,2Y,10Y\n2026/8/14,1.2,2.0\n"
    with pytest.raises(RuntimeError, match="30Y"):
        MofJgbBackfill.parse_csv(broken, start=date(2026,8,14), end=date(2026,8,14), ingested_at=NOW)


def test_boj_snapshot_success_normalizes_dates_and_uses_real_ingestion_time():
    payload = {
        "STATUS": 200,
        "data": [
            {"DATE": "20260814", "VALUE": "0.50", "UNIT": "percent"},
            {"DATE": "20260817", "VALUE": "0.55", "UNIT": "percent"},
        ],
    }
    session = _Session(_Response(payload=payload))
    rows = BojCallRateBackfill(session=session, now=lambda: NOW).fetch_core(
        start=date(2026,8,14), end=date(2026,8,17)
    )
    assert [r.value for r in rows] == [0.50, 0.55]
    assert rows[0].observation_time == "2026-08-14T00:00:00+00:00"
    assert all(r.available_time == NOW.isoformat() and r.ingested_time == NOW.isoformat() for r in rows)
    assert all(r.provider == "boj_call_unversioned_snapshot" for r in rows)


def test_boj_snapshot_rejects_provider_status_failure():
    session = _Session(_Response(payload={"STATUS": 500, "data": []}))
    with pytest.raises(RuntimeError, match="STATUS=500"):
        BojCallRateBackfill(session=session, now=lambda: NOW).fetch_core(
            start=date(2026,8,14), end=date(2026,8,17)
        )


def test_boj_snapshot_rejects_nonfinite_payload():
    session = _Session(_Response(payload={"STATUS": 200, "data": [{"DATE":"20260814","VALUE":"nan"}]}))
    with pytest.raises(RuntimeError, match="non-finite BOJ observation value"):
        BojCallRateBackfill(session=session, now=lambda: NOW).fetch_core(
            start=date(2026,8,14), end=date(2026,8,17)
        )


def test_boj_snapshot_rejects_partially_malformed_payload_instead_of_accepting_subset():
    payload = {
        "STATUS": 200,
        "data": [
            {"DATE": "20260814", "VALUE": "0.50", "UNIT": "percent"},
            {"DATE": "20260817", "VALUE": "not-a-number", "UNIT": "percent"},
        ],
    }
    session = _Session(_Response(payload=payload))
    with pytest.raises(RuntimeError, match="malformed BOJ observation value"):
        BojCallRateBackfill(session=session, now=lambda: NOW).fetch_core(
            start=date(2026,8,14), end=date(2026,8,17)
        )


def test_boj_snapshot_rejects_date_bearing_row_missing_value():
    payload = {
        "STATUS": 200,
        "data": [
            {"DATE": "20260814", "VALUE": "0.50", "UNIT": "percent"},
            {"DATE": "20260817", "UNIT": "percent"},
        ],
    }
    session = _Session(_Response(payload=payload))
    with pytest.raises(RuntimeError, match="missing VALUE"):
        BojCallRateBackfill(session=session, now=lambda: NOW).fetch_core(
            start=date(2026,8,14), end=date(2026,8,17)
        )


def test_boj_snapshot_rejects_future_observation_relative_to_ingestion():
    early = datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc)
    session = _Session(_Response(payload={"STATUS":200,"data":[{"DATE":"20260817","VALUE":"0.55"}]}))
    with pytest.raises(RuntimeError, match="future observation"):
        BojCallRateBackfill(session=session, now=lambda: early).fetch_core(
            start=date(2026,8,17), end=date(2026,8,17)
        )
