from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from macro_data import (
    BojAdapter,
    FredAdapter,
    JsonlMacroArchive,
    MacroObservation,
    TreasuryBuybackAdapter,
    coverage_report,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.last_params = None

    def get(self, _url, *, params=None, timeout=None):
        self.last_params = params
        return _Response(self.payload)


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


def test_rejects_non_finite_macro_value():
    with pytest.raises(ValueError):
        _obs(value=float("nan")).validate()
    with pytest.raises(ValueError):
        _obs(value=float("inf")).validate()


def test_archive_is_append_only_and_readable(tmp_path: Path):
    archive = JsonlMacroArchive(tmp_path / "macro.jsonl")
    archive.append([_obs(value=4.2)])
    archive.append([_obs(value=4.3)])
    lines = (tmp_path / "macro.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert [row.value for row in archive.read_all()] == [4.2, 4.3]


def test_fred_query_has_explicit_observation_end_boundary():
    session = _Session(
        {
            "observations": [
                {
                    "date": "2026-08-19",
                    "value": "4.25",
                    "realtime_start": "2026-08-20",
                    "realtime_end": "2026-08-20",
                }
            ]
        }
    )
    FredAdapter(api_key="test", session=session).fetch_latest(
        "us_10y", as_of=datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
    )
    assert session.last_params["realtime_start"] == "2026-08-20"
    assert session.last_params["realtime_end"] == "2026-08-20"
    assert session.last_params["observation_end"] == "2026-08-20"


def test_fred_rejects_future_dated_provider_row_even_if_returned():
    session = _Session(
        {
            "observations": [
                {
                    "date": "2026-08-21",
                    "value": "4.25",
                    "realtime_start": "2026-08-20",
                    "realtime_end": "2026-08-20",
                }
            ]
        }
    )
    with pytest.raises(RuntimeError):
        FredAdapter(api_key="test", session=session).fetch_latest(
            "us_10y", as_of=datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
        )


def test_boj_parser_extracts_nested_value_rows():
    payload = {
        "RESULTSET": [
            {
                "UNIT": "%",
                "VALUES": [
                    {"TIME_PERIOD": "20260819", "VALUE": "0.512"},
                    {"TIME_PERIOD": "20260820", "VALUE": "0.518"},
                ],
            }
        ]
    }
    assert BojAdapter._find_value_rows(payload) == [
        ("20260819", 0.512, "%"),
        ("20260820", 0.518, "%"),
    ]


def test_boj_date_normalization_is_deterministic():
    assert BojAdapter._boj_date_to_iso("20260820") == "2026-08-20T00:00:00+00:00"
    assert BojAdapter._boj_date_to_iso("202608") == "2026-08-01T00:00:00+00:00"
    with pytest.raises(RuntimeError):
        BojAdapter._boj_date_to_iso("20")


def test_boj_as_of_filters_later_rows_from_same_month():
    session = _Session(
        {
            "RESULTSET": [
                {
                    "UNIT": "%",
                    "VALUES": [
                        {"TIME_PERIOD": "20260819", "VALUE": "0.512"},
                        {"TIME_PERIOD": "20260825", "VALUE": "0.700"},
                    ],
                }
            ]
        }
    )
    row = BojAdapter(session=session).fetch_latest(
        "boj_call_rate", as_of=datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
    )
    assert row.observation_time.startswith("2026-08-19")
    assert row.value == 0.512


def test_coverage_missing_series_is_not_healthy():
    report = coverage_report(
        [_obs(series="us_10y")],
        required_series=("us_10y", "us_30y"),
        as_of=datetime(2026, 8, 20, 12, 1, tzinfo=timezone.utc),
    )
    assert report.healthy is False
    assert report.missing_series == ("us_30y",)


def test_coverage_future_available_row_is_invisible_at_historical_cut():
    report = coverage_report(
        [
            _obs(
                series="us_10y",
                available_time="2026-08-20T13:00:00+00:00",
                ingested_time="2026-08-20T13:00:01+00:00",
            )
        ],
        required_series=("us_10y",),
        as_of=datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc),
    )
    assert report.healthy is False
    assert report.missing_series == ("us_10y",)


def test_coverage_stale_series_is_not_healthy():
    report = coverage_report(
        [_obs(series="us_10y")],
        required_series=("us_10y",),
        as_of=datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc),
        max_age={"us_10y": timedelta(hours=1)},
    )
    assert report.healthy is False
    assert report.stale_series == ("us_10y",)


def test_coverage_complete_fresh_set_is_healthy():
    report = coverage_report(
        [
            _obs(series="us_10y"),
            _obs(series="us_30y", source_id="DGS30", value=4.8),
        ],
        required_series=("us_10y", "us_30y"),
        as_of=datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc),
        max_age={"us_10y": timedelta(hours=1), "us_30y": timedelta(hours=1)},
    )
    assert report.healthy is True


def _treasury_xml():
    return """<BuybackSchedule><Operation><AnnouncementDate>08/18/2026</AnnouncementDate><OperationDate>09/09/2026</OperationDate><SettlementDate>09/10/2026</SettlementDate><OperationType>Liquidity Support</OperationType><SecurityTypeMaturityRange>Nominal Coupons 10Y to 20Y</SecurityTypeMaturityRange><MaximumPurchaseAmount>$4 billion</MaximumPurchaseAmount></Operation><Operation><AnnouncementDate>08/18/2026</AnnouncementDate><OperationDate>09/10/2026</OperationDate><SettlementDate>09/11/2026</SettlementDate><OperationType>Cash Management</OperationType><SecurityTypeMaturityRange>Nominal Coupons 1Mo to 2Y</SecurityTypeMaturityRange><MaximumPurchaseAmount>$12.5 billion</MaximumPurchaseAmount></Operation></BuybackSchedule>"""


def test_treasury_schedule_parser_preserves_capacity_not_realized_purchase():
    rows = TreasuryBuybackAdapter.parse_xml(
        _treasury_xml(),
        source_url="https://home.treasury.gov/example.xml",
        ingested_at=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
    )
    assert len(rows) == 2
    assert rows[0].max_purchase_usd == 4_000_000_000
    assert rows[0].operation_type == "Liquidity Support"
    assert rows[0].operation_time.startswith("2026-09-09")


def test_treasury_schedule_rejects_future_announcement_relative_to_ingestion():
    xml = _treasury_xml().replace("08/18/2026", "08/25/2026")
    with pytest.raises(ValueError):
        TreasuryBuybackAdapter.parse_xml(
            xml,
            source_url="https://home.treasury.gov/example.xml",
            ingested_at=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
        )


def test_treasury_parser_rejects_spoofed_source_even_when_called_directly():
    with pytest.raises(ValueError):
        TreasuryBuybackAdapter.parse_xml(
            _treasury_xml(),
            source_url="https://example.com/treasury.xml",
            ingested_at=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
        )


def test_treasury_adapter_rejects_nonofficial_schedule_host():
    with pytest.raises(ValueError):
        TreasuryBuybackAdapter("https://example.com/buybacks.xml")


def test_treasury_parser_fails_closed_on_unknown_payload():
    with pytest.raises(RuntimeError):
        TreasuryBuybackAdapter.parse_xml(
            "<root><message>no schedule</message></root>",
            source_url="https://home.treasury.gov/example.xml",
            ingested_at=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
        )
