from datetime import datetime, timezone

import pytest

from macro_treasury import (
    TreasuryAuctionIssuanceAdapter,
    TreasuryBuybackResultsAdapter,
    treasury_supply_window,
)


def test_buyback_results_parser_requires_official_source_and_preserves_realized_amount():
    xml = """<BuybackResults>
      <OperationDate>2026-08-20</OperationDate>
      <SettlementDate>2026-08-21</SettlementDate>
      <OperationType>Liquidity Support</OperationType>
      <MaturityBucket>Nominal Coupons 10Y to 20Y</MaturityBucket>
      <TotalParAmountOffered>91000000</TotalParAmountOffered>
      <TotalParAmountAccepted>24000000</TotalParAmountAccepted>
    </BuybackResults>"""
    row = TreasuryBuybackResultsAdapter.parse_xml(
        xml,
        source_url="https://www.treasurydirect.gov/example/results.xml",
        ingested_at=datetime(2026, 8, 20, 20, tzinfo=timezone.utc),
    )
    assert row.total_par_offered_usd == 91_000_000
    assert row.total_par_accepted_usd == 24_000_000
    assert row.operation_type == "Liquidity Support"


def test_buyback_results_rejects_accepted_above_offered():
    xml = """<BuybackResults><OperationDate>2026-08-20</OperationDate>
      <TotalParAmountOffered>100</TotalParAmountOffered>
      <TotalParAmountAccepted>101</TotalParAmountAccepted></BuybackResults>"""
    with pytest.raises(ValueError):
        TreasuryBuybackResultsAdapter.parse_xml(
            xml,
            source_url="https://treasurydirect.gov/example/results.xml",
            ingested_at=datetime(2026, 8, 20, 20, tzinfo=timezone.utc),
        )


def test_buyback_results_reject_operation_after_ingestion():
    xml = """<BuybackResults><OperationDate>2026-08-21</OperationDate>
      <TotalParAmountOffered>100</TotalParAmountOffered>
      <TotalParAmountAccepted>90</TotalParAmountAccepted></BuybackResults>"""
    with pytest.raises(ValueError):
        TreasuryBuybackResultsAdapter.parse_xml(
            xml,
            source_url="https://treasurydirect.gov/example/results.xml",
            ingested_at=datetime(2026, 8, 20, 20, tzinfo=timezone.utc),
        )


def test_buyback_adapter_rejects_unapproved_host():
    with pytest.raises(ValueError):
        TreasuryBuybackResultsAdapter("https://example.com/results.xml")


def _auction_payload():
    return {
        "data": [
            {
                "record_date": "2026-08-20",
                "cusip": "912TEST01",
                "security_type": "Bill",
                "security_term": "13-Week",
                "auction_date": "2026-08-20",
                "issue_date": "2026-08-25",
                "total_accepted": "80000000000",
            },
            {
                "record_date": "2026-08-20",
                "cusip": "912TEST02",
                "security_type": "Note",
                "security_term": "10-Year",
                "auction_date": "2026-08-19",
                "issue_date": "2026-08-24",
                "total_accepted": "42000000000",
            },
        ]
    }


def test_fiscaldata_auction_parser_preserves_gross_issuance():
    rows = TreasuryAuctionIssuanceAdapter.parse_rows(
        _auction_payload(),
        ingested_at=datetime(2026, 8, 20, 20, tzinfo=timezone.utc),
    )
    assert len(rows) == 2
    assert rows[0].total_accepted_usd == 80_000_000_000
    assert rows[1].security_term == "10-Year"


def test_fiscaldata_auction_parser_fails_closed_on_future_record_date():
    payload = _auction_payload()
    payload["data"][0]["record_date"] = "2026-08-21"
    with pytest.raises(ValueError):
        TreasuryAuctionIssuanceAdapter.parse_rows(
            payload,
            ingested_at=datetime(2026, 8, 20, 20, tzinfo=timezone.utc),
        )


def test_supply_window_is_accounting_not_score():
    issuance = TreasuryAuctionIssuanceAdapter.parse_rows(
        _auction_payload(),
        ingested_at=datetime(2026, 8, 20, 20, tzinfo=timezone.utc),
    )
    buyback_xml = """<BuybackResults><OperationDate>2026-08-20</OperationDate>
      <TotalParAmountOffered>5000000000</TotalParAmountOffered>
      <TotalParAmountAccepted>4000000000</TotalParAmountAccepted></BuybackResults>"""
    buyback = TreasuryBuybackResultsAdapter.parse_xml(
        buyback_xml,
        source_url="https://www.treasurydirect.gov/example/results.xml",
        ingested_at=datetime(2026, 8, 20, 20, tzinfo=timezone.utc),
    )
    window = treasury_supply_window(issuance, [buyback])
    assert window.gross_issuance_usd == 122_000_000_000
    assert window.realized_buybacks_usd == 4_000_000_000
    assert window.net_market_supply_usd == 118_000_000_000
