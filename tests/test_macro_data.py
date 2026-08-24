from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from macro_data import (
    BojAdapter,
    JsonlMacroArchive,
    MacroObservation,
    TreasuryBuybackAdapter,
    TreasuryBuybackResultAdapter,
    coverage_report,
)


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


def test_archive_is_append_only_and_readable(tmp_path: Path):
    archive = JsonlMacroArchive(tmp_path / "macro.jsonl")
    archive.append([_obs(value=4.2)])
    archive.append([_obs(value=4.3)])
    lines = (tmp_path / "macro.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert '"value": 4.2' in lines[0]
    assert '"value": 4.3' in lines[1]
    assert [row.value for row in archive.read_all()] == [4.2, 4.3]


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
    rows = BojAdapter._find_value_rows(payload)
    assert rows == [("20260819", 0.512, "%"), ("20260820", 0.518, "%")]


def test_boj_date_normalization_is_deterministic():
    assert BojAdapter._boj_date_to_iso("20260820") == "2026-08-20T00:00:00+00:00"
    assert BojAdapter._boj_date_to_iso("202608") == "2026-08-01T00:00:00+00:00"
    with pytest.raises(RuntimeError):
        BojAdapter._boj_date_to_iso("20")


def test_coverage_missing_series_is_not_healthy():
    report = coverage_report(
        [_obs(series="us_10y")],
        required_series=("us_10y", "us_30y"),
        as_of=datetime(2026, 8, 20, 12, 1, tzinfo=timezone.utc),
    )
    assert report.healthy is False
    assert report.missing_series == ("us_30y",)


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
    assert report.missing_series == ()
    assert report.stale_series == ()


def test_treasury_schedule_capacity_is_not_realized_result():
    xml = """
    <Schedule>
      <Operation>
        <AnnouncementDate>08/19/2026</AnnouncementDate>
        <OperationDate>09/09/2026</OperationDate>
        <SettlementDate>09/10/2026</SettlementDate>
        <OperationType>Liquidity Support</OperationType>
        <SecurityTypeMaturityRange>20-Year to 30-Year</SecurityTypeMaturityRange>
        <MaximumPurchaseAmount>$4 billion</MaximumPurchaseAmount>
      </Operation>
    </Schedule>
    """
    rows = TreasuryBuybackAdapter.parse_xml(
        xml,
        source_url="https://home.treasury.gov/example.xml",
        ingested_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    assert rows[0].max_purchase_usd == 4_000_000_000
    assert not hasattr(rows[0], "total_accepted_usd")


def test_treasury_realized_result_parses_accepted_and_offered():
    xml = """
    <BuybackResults>
      <OperationDate>04/22/2026</OperationDate>
      <SettlementDate>04/23/2026</SettlementDate>
      <OperationType>Cash Management</OperationType>
      <MaturityBucket>05/31/2026 - 04/15/2028</MaturityBucket>
      <MaximumParAmountToBeRedeemed>$15,000,000,000</MaximumParAmountToBeRedeemed>
      <TotalParAmountOffered>$37,841,000,000</TotalParAmountOffered>
      <TotalParAmountAccepted>$15,000,000,000</TotalParAmountAccepted>
      <NumberOfIssuesEligible>49</NumberOfIssuesEligible>
      <NumberOfIssuesAccepted>26</NumberOfIssuesAccepted>
    </BuybackResults>
    """
    result = TreasuryBuybackResultAdapter.parse_xml(
        xml,
        source_url="https://www.treasurydirect.gov/example.xml",
        ingested_at=datetime(2026, 4, 22, 18, 0, tzinfo=timezone.utc),
    )
    assert result.total_offered_usd == 37_841_000_000
    assert result.total_accepted_usd == 15_000_000_000
    assert result.max_purchase_usd == 15_000_000_000
    assert result.issues_eligible == 49
    assert result.issues_accepted == 26


def test_treasury_result_rejects_accepted_above_offered():
    xml = """
    <BuybackResults>
      <OperationDate>04/22/2026</OperationDate>
      <SettlementDate>04/23/2026</SettlementDate>
      <MaximumParAmountToBeRedeemed>$20,000,000,000</MaximumParAmountToBeRedeemed>
      <TotalParAmountOffered>$10,000,000,000</TotalParAmountOffered>
      <TotalParAmountAccepted>$11,000,000,000</TotalParAmountAccepted>
    </BuybackResults>
    """
    with pytest.raises(ValueError):
        TreasuryBuybackResultAdapter.parse_xml(
            xml,
            source_url="https://treasurydirect.gov/example.xml",
            ingested_at=datetime(2026, 4, 22, 18, 0, tzinfo=timezone.utc),
        )


def test_treasury_adapters_reject_untrusted_sources():
    with pytest.raises(ValueError):
        TreasuryBuybackAdapter("https://example.com/schedule.xml")
    with pytest.raises(ValueError):
        TreasuryBuybackResultAdapter("https://example.com/result.xml")
