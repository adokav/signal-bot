from datetime import datetime, timezone

import pytest

from treasury_flows import (
    TreasuryAuctionAdapter,
    TreasuryBuybackResultsAdapter,
    gross_issuance_total,
)


def test_buyback_results_parser_requires_results_status():
    xml = """
    <buyback>
      <operationStatus>Released</operationStatus>
      <operationDate>2026-08-20</operationDate>
      <totalParAmountOffered>1000000000</totalParAmountOffered>
      <totalParAmountAccepted>500000000</totalParAmountAccepted>
    </buyback>
    """
    with pytest.raises(RuntimeError):
        TreasuryBuybackResultsAdapter.parse_xml(
            xml,
            source_url="https://www.treasurydirect.gov/xml/example.xml",
            ingested_at=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        )


def test_buyback_results_parser_preserves_realized_amounts():
    xml = """
    <buyback>
      <operationStatus>Results</operationStatus>
      <operationStartDTM>2026-08-20T13:40:00-04:00</operationStartDTM>
      <settlementDate>2026-08-21</settlementDate>
      <operationType>Liquidity Support</operationType>
      <securityType>Nominal Coupons</securityType>
      <maturityBucket>10Y to 20Y</maturityBucket>
      <maxParAmountToBeRedeemed>4000000000</maxParAmountToBeRedeemed>
      <numberIssuesEligible>12</numberIssuesEligible>
      <numberIssuesAccepted>8</numberIssuesAccepted>
      <totalParAmountOffered>5600000000</totalParAmountOffered>
      <totalParAmountAccepted>3900000000</totalParAmountAccepted>
    </buyback>
    """
    row = TreasuryBuybackResultsAdapter.parse_xml(
        xml,
        source_url="https://www.treasurydirect.gov/xml/example.xml",
        ingested_at=datetime(2026, 8, 20, 18, 10, tzinfo=timezone.utc),
    )
    assert row.total_par_offered_usd == 5_600_000_000
    assert row.total_par_accepted_usd == 3_900_000_000
    assert row.max_par_redeemable_usd == 4_000_000_000
    assert row.issues_accepted == 8
    assert row.operation_time == "2026-08-20T17:40:00+00:00"


def test_buyback_results_rejects_impossible_acceptance():
    xml = """
    <buyback>
      <operationStatus>Results</operationStatus>
      <operationDate>2026-08-20</operationDate>
      <maxParAmountToBeRedeemed>4000000000</maxParAmountToBeRedeemed>
      <totalParAmountOffered>3000000000</totalParAmountOffered>
      <totalParAmountAccepted>3500000000</totalParAmountAccepted>
    </buyback>
    """
    with pytest.raises(ValueError):
        TreasuryBuybackResultsAdapter.parse_xml(
            xml,
            source_url="https://www.treasurydirect.gov/xml/example.xml",
            ingested_at=datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc),
        )


def test_auction_adapter_parses_gross_issuance_without_calling_it_net():
    payload = {
        "data": [
            {
                "record_date": "2026-08-20",
                "cusip": "912345678",
                "security_type": "Bill",
                "security_term": "13-Week",
                "auction_date": "2026-08-20",
                "issue_date": "2026-08-25",
                "reopening": "No",
                "offering_amt": "85000000000",
                "total_accepted": "85000000000",
            },
            {
                "record_date": "2026-08-20",
                "cusip": "912345679",
                "security_type": "Note",
                "security_term": "2-Year",
                "auction_date": "2026-08-20",
                "issue_date": "2026-08-25",
                "reopening": "Yes",
                "offering_amt": "69000000000",
                "total_accepted": "69000000000",
            },
        ]
    }
    rows = TreasuryAuctionAdapter.parse_rows(
        payload,
        ingested_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
    )
    assert len(rows) == 2
    assert gross_issuance_total(rows) == 154_000_000_000
    assert rows[0].security_type == "Bill"


def test_auction_adapter_fails_closed_on_empty_or_invalid_payload():
    with pytest.raises(RuntimeError):
        TreasuryAuctionAdapter.parse_rows(
            {"data": []},
            ingested_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
    with pytest.raises(RuntimeError):
        TreasuryAuctionAdapter.parse_rows(
            {"data": [{"cusip": "bad"}]},
            ingested_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
