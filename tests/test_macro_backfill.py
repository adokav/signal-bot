from datetime import datetime, timezone

import pytest

from macro_backfill import BinanceHourlyBackfill, FredRevisionBackfill, conservative_date_availability


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _PagedFredSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append(dict(params))
        if params["offset"] == 0:
            return _Response({
                "output_type": 3,
                "count": 2,
                "observations": [
                    {"realtime_start": "2020-01-02", "realtime_end": "9999-12-31", "date": "2020-01-01", "value": "1.50"},
                ],
            })
        return _Response({
            "output_type": 3,
            "count": 2,
            "observations": [
                {"realtime_start": "2020-01-03", "realtime_end": "9999-12-31", "date": "2020-01-01", "value": "1.55"},
            ],
        })


def test_conservative_vintage_availability_is_next_utc_day():
    assert conservative_date_availability("2020-01-02").isoformat() == "2020-01-03T00:00:00+00:00"


def test_fred_revision_backfill_paginates_and_keeps_revisions():
    session = _PagedFredSession()
    rows = FredRevisionBackfill(api_key="x" * 32, session=session).fetch_series(
        "us_10y", start="2020-01-01", end="2020-01-10", page_limit=1
    )
    assert len(rows) == 2
    assert rows[0].provider == "fred_alfred_reconstruction"
    assert rows[0].available_time == "2020-01-03T00:00:00+00:00"
    assert rows[1].available_time == "2020-01-04T00:00:00+00:00"
    assert rows[0].ingested_time == rows[0].available_time
    assert session.calls[0]["output_type"] == 3
    assert session.calls[0]["observation_end"] == "2020-01-10"


def test_fred_revision_rejects_observation_after_vintage():
    class Session:
        def get(self, url, params, timeout):
            return _Response({
                "output_type": 3,
                "count": 1,
                "observations": [
                    {"realtime_start": "2020-01-02", "realtime_end": "9999-12-31", "date": "2020-01-03", "value": "1.5"},
                ],
            })

    with pytest.raises(RuntimeError):
        FredRevisionBackfill(api_key="x" * 32, session=Session()).fetch_series(
            "us_10y", start="2020-01-01", end="2020-01-10"
        )


def test_binance_parser_uses_close_time_and_validates_ohlc():
    close_ms = int(datetime(2020, 1, 1, 0, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
    rows = BinanceHourlyBackfill.parse_rows([
        [0, "100", "110", "90", "105", "1", close_ms, "0", 1, "0", "0", "0"]
    ])
    assert len(rows) == 1
    assert rows[0].close == 105.0
    assert rows[0].timestamp.startswith("2020-01-01T00:59:59")

    with pytest.raises(ValueError):
        BinanceHourlyBackfill.parse_rows([
            [0, "100", "99", "90", "105", "1", close_ms, "0", 1, "0", "0", "0"]
        ])
