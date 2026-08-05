from __future__ import annotations

from dataclasses import replace

from acce_unified.config import UnifiedConfig
from acce_unified.listing_integrity import (
    _versioned_state_path,
    candidate_integrity_reasons,
    sanitize_listing_snapshot,
)
from acce_unified.models import RadarCandidate, RadarSnapshot


def _candidate(
    symbol: str,
    *,
    now: int,
    first_seen_at: int | None = None,
    discovery_source: str = "ANNOUNCEMENT",
    title: str | None = None,
    manually_watched: bool = False,
    supply_ready: bool = True,
    filter_status: str = "PASSED",
) -> RadarCandidate:
    base = symbol.removesuffix("USDT")
    fundamentals = (
        {
            "status": "READY",
            "stage": "HEALTHY",
            "circulating_supply": 10_000_000,
            "total_supply": 100_000_000,
            "max_supply": 100_000_000,
        }
        if supply_ready
        else {"status": "AMBIGUOUS"}
    )
    return RadarCandidate(
        source="PHENOMENONX_MEXC_NEW_LISTING",
        key=f"listing:{symbol}",
        symbol=symbol,
        score=90,
        stage="HOT",
        role="DISCOVERY_ONLY",
        metadata={
            "base_symbol": base,
            "title": title or f"MEXC Will List Project ({base}) in Innovation Zone",
            "discovery_source": discovery_source,
            "first_seen_at": first_seen_at if first_seen_at is not None else now - 600,
            "manually_watched": manually_watched,
            "spot_status": "OPEN",
            "quote_volume": 1_000_000,
            "fundamentals": fundamentals,
            "filter_status": filter_status,
        },
    )


def _snapshot(now: int, *, passed=(), filtered=()) -> RadarSnapshot:
    return RadarSnapshot(
        generated_at=now,
        mode="SHADOW",
        listing_candidates=tuple(passed),
        listing_filtered_candidates=tuple(filtered),
    )


def test_valid_recent_explicit_announcement_is_kept():
    now = 1_800_000_000
    candidate = _candidate("NEWUSDT", now=now)
    result = sanitize_listing_snapshot(_snapshot(now, passed=[candidate]), UnifiedConfig())
    assert [item.symbol for item in result.listing_candidates] == ["NEWUSDT"]


def test_filtered_rows_are_never_refilled_into_public_top_five():
    now = 1_800_000_000
    rejected_btc = _candidate(
        "BTCUSDT",
        now=now,
        supply_ready=False,
        filter_status="FILTERED",
        title="MEXC market update mentions Bitcoin (BTC)",
    )
    result = sanitize_listing_snapshot(
        _snapshot(now, filtered=[rejected_btc]),
        UnifiedConfig(),
    )
    assert result.listing_candidates == ()
    assert result.listing_filtered_candidates == ()


def test_old_market_and_vague_btc_mention_fail_closed():
    now = 1_800_000_000
    btc = _candidate(
        "BTCUSDT",
        now=now,
        first_seen_at=now - 10 * 24 * 60 * 60,
        title="MEXC campaign: deposit Bitcoin (BTC) and win rewards",
    )
    reasons = candidate_integrity_reasons(
        btc,
        now=now,
        max_age_seconds=72 * 60 * 60,
        require_open_spot=True,
        require_supply_data=True,
    )
    assert "OUTSIDE_RECENT_LISTING_WINDOW" in reasons
    assert "ANNOUNCEMENT_CONTEXT_NOT_VERIFIED" in reasons


def test_manual_watch_cannot_masquerade_as_new_listing():
    now = 1_800_000_000
    candidate = _candidate(
        "ARBUSDT",
        now=now,
        discovery_source="MANUAL_WATCH",
        manually_watched=True,
    )
    reasons = candidate_integrity_reasons(
        candidate,
        now=now,
        max_age_seconds=72 * 60 * 60,
        require_open_spot=True,
        require_supply_data=True,
    )
    assert "UNVERIFIED_DISCOVERY_SOURCE" in reasons
    assert "MANUAL_WATCH_IS_NOT_LISTING_EVENT" in reasons


def test_exchange_diff_is_allowed_only_inside_recent_window():
    now = 1_800_000_000
    candidate = _candidate(
        "FRESHUSDT",
        now=now,
        discovery_source="EXCHANGE_DIFF",
        title="MEXC Spot API'de yeni görülen FRESHUSDT",
    )
    reasons = candidate_integrity_reasons(
        candidate,
        now=now,
        max_age_seconds=72 * 60 * 60,
        require_open_spot=True,
        require_supply_data=True,
    )
    assert reasons == ()


def test_state_files_are_versioned_to_drop_polluted_catalogs():
    assert _versioned_state_path("/data/mexc_seen_symbols.json") == "/data/mexc_seen_symbols_v3.json"
    assert _versioned_state_path("/data/mexc_listing_candidates.json") == "/data/mexc_listing_candidates_v3.json"
    assert _versioned_state_path("/data/mexc_seen_symbols_v3.json") == "/data/mexc_seen_symbols_v3.json"
