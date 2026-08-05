from __future__ import annotations

from acce_unified.listing_fundamentals import enrich_price_extremes
from bot import format_new


def _candidate(symbol: str, score: int, *, ready: bool = True) -> dict:
    fundamentals = (
        {
            "status": "READY",
            "circulating_supply": 25_000_000,
            "total_supply": 100_000_000,
            "max_supply": 120_000_000,
            "ath_price_usd": 2.5,
            "ath_change_pct": -60.0,
            "atl_price_usd": 0.05,
            "atl_change_pct": 1900.0,
        }
        if ready
        else {"status": "PROVIDER_COOLDOWN"}
    )
    return {
        "symbol": symbol,
        "score": score,
        "stage": "BUILDING",
        "risk_flags": [],
        "metadata": {
            "change_pct": 5.0,
            "quote_volume": 500_000,
            "volume_acceleration": 1.5,
            "social": {"status": "UNAVAILABLE"},
            "fundamentals": fundamentals,
        },
    }


def test_price_extremes_are_preserved_from_provider_row():
    signal = enrich_price_extremes(
        {"status": "READY"},
        {
            "ath": 3.2,
            "ath_change_percentage": -68.5,
            "ath_date": "2025-01-01T00:00:00.000Z",
            "atl": 0.04,
            "atl_change_percentage": 2420.0,
            "atl_date": "2024-01-01T00:00:00.000Z",
        },
    )
    assert signal["ath_price_usd"] == 3.2
    assert signal["ath_change_pct"] == -68.5
    assert signal["atl_price_usd"] == 0.04
    assert signal["atl_change_pct"] == 2420.0


def test_new_listing_report_returns_highest_scoring_five_with_supply_and_extremes():
    snapshot = {
        "listing_candidates": [
            _candidate("AUSDT", 70),
            _candidate("BUSDT", 95),
            _candidate("CUSDT", 80),
        ],
        "listing_filtered_candidates": [
            _candidate("DUSDT", 90),
            _candidate("EUSDT", 85),
            _candidate("FUSDT", 75),
        ],
    }
    report = format_new(snapshot)

    assert "EN YÜKSEK SKORLU 5" in report
    assert report.index("BUSDT") < report.index("DUSDT") < report.index("EUSDT")
    assert "FUSDT" in report
    assert "AUSDT" not in report
    assert "Arz: dolaşan 25.00M · toplam 100.00M · max 120.00M" in report
    assert "ATH $2.5 (%-60.0) · ATL $0.05 (%+1900.0)" in report


def test_pending_provider_does_not_invent_supply_or_price_extremes():
    report = format_new(
        {
            "listing_candidates": [_candidate("WAITUSDT", 88, ready=False)],
            "listing_filtered_candidates": [],
        }
    )
    assert "Arz ve ATH/ATL: PROVIDER_COOLDOWN" in report
    assert "dolaşan 0" not in report
