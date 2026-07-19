from __future__ import annotations

from acce_unified.config import DEFAULT_TRADE_UNIVERSE
from acce_unified.listings import (
    extract_listing_titles,
    extract_symbols_from_title,
    partition_mexc_listings,
    rank_mexc_listings,
    score_mexc_listing,
)
from acce_unified.models import MexcListing


def _listing(**overrides) -> MexcListing:
    base = {
        "symbol": "NOVA",
        "pair": "NOVAUSDT",
        "title": "MEXC Will List Nova Protocol (NOVA)",
        "rank": 1,
        "spot_status": "OPEN",
        "last_price": 0.25,
        "change_pct": 18.0,
        "quote_volume": 15_000_000.0,
        "volume_acceleration": 2.5,
        "discovery_source": "ANNOUNCEMENT",
    }
    base.update(overrides)
    return MexcListing(**base)


def test_announcement_parser_extracts_listing_and_symbol():
    page = (
        '<script>{"title":"MEXC Will List Nova Protocol (NOVA)"}</script>'
        '<h2>General Platform Maintenance</h2>'
    )
    titles = extract_listing_titles(page)
    assert titles == ["MEXC Will List Nova Protocol (NOVA)"]
    assert extract_symbols_from_title(titles[0]) == ["NOVA"]


def test_symbol_parser_handles_mexc_first_in_market_headline():
    title = "First in Market: JIMOTHY Now Live on MEXC Meme+"
    assert extract_symbols_from_title(title) == ["JIMOTHY"]


def test_symbol_parser_rejects_stable_and_leveraged_products():
    assert extract_symbols_from_title("MEXC Will List USD Coin (USDC)") == []
    assert extract_symbols_from_title("MEXC Will List Bitcoin 3L (BTC3L)") == []
    assert extract_symbols_from_title("MEXC Will List Nova (NOVA) at 12:00 (UTC)") == ["NOVA"]


def test_listing_candidate_is_discovery_only_and_never_authoritative():
    candidate = score_mexc_listing(
        _listing(),
        trade_universe=DEFAULT_TRADE_UNIVERSE,
    )
    assert candidate.source == "PHENOMENONX_MEXC_NEW_LISTING"
    assert candidate.stage == "HOT"
    assert candidate.role == "DISCOVERY_ONLY"
    assert candidate.execution_eligible is False
    assert any("otomatik işlem yasak" in flag for flag in candidate.risk_flags)


def test_extended_first_pump_is_crowded_and_excluded_from_ranked_top():
    crowded = _listing(change_pct=120.0, quote_volume=80_000_000.0)
    assert score_mexc_listing(
        crowded,
        trade_universe=DEFAULT_TRADE_UNIVERSE,
    ).stage == "CROWDED"
    assert rank_mexc_listings(
        [crowded],
        trade_universe=DEFAULT_TRADE_UNIVERSE,
    ) == []


def test_exchange_diff_candidate_keeps_mexc_specific_evidence():
    candidate = score_mexc_listing(
        _listing(discovery_source="EXCHANGE_DIFF", rank=0),
        trade_universe=DEFAULT_TRADE_UNIVERSE,
    )
    assert "MEXC Spot API'de yeni görüldü" in candidate.reasons


def test_official_announcement_remains_watch_when_spot_api_is_unreachable():
    candidate = score_mexc_listing(
        _listing(
            spot_status="UNKNOWN",
            last_price=0.0,
            change_pct=0.0,
            quote_volume=0.0,
            volume_acceleration=0.0,
        ),
        trade_universe=DEFAULT_TRADE_UNIVERSE,
    )
    assert candidate.score == 53
    assert candidate.stage == "WATCH"
    assert candidate.execution_eligible is False
    assert any("Spot API teyidi erişilemedi" in flag for flag in candidate.risk_flags)


def test_filtered_candidates_are_retained_with_plain_language_reasons():
    weak = _listing(
        rank=20,
        spot_status="NOT_OPEN",
        last_price=0.0,
        quote_volume=0.0,
        volume_acceleration=0.0,
    )
    crowded = _listing(change_pct=120.0, quote_volume=80_000_000.0)

    passed, filtered = partition_mexc_listings(
        [weak, crowded],
        trade_universe=DEFAULT_TRADE_UNIVERSE,
        min_score=52,
    )

    assert passed == []
    assert {item.stage for item in filtered} == {"WEAK", "CROWDED"}
    assert all(item.metadata["filter_status"] == "FILTERED" for item in filtered)
    assert any(
        "puan eksik" in reason
        for item in filtered
        for reason in item.metadata["filter_reasons"]
    )


def test_tight_spread_adds_early_liquidity_evidence():
    candidate = score_mexc_listing(
        _listing(spread_bps=22.0),
        trade_universe=DEFAULT_TRADE_UNIVERSE,
    )

    assert any("makası dar" in reason for reason in candidate.reasons)
