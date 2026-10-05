from __future__ import annotations

from acce_unified.listing_fundamentals import enrich_price_extremes
from bot import _fundamental_lines


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


# The MEXC new-listing view was replaced by the CoinMarketCap security screen (docs/NEW_COINS.md,
# tests/test_new_coins.py). The supply and ATH/ATL lines it used still serve the tactical view.


def test_supply_and_price_extremes_are_shown_from_ready_fundamentals():
    report = "\n".join(_fundamental_lines(_candidate("AUSDT", 70)["metadata"]["fundamentals"]))
    assert "Arz: dolaşan 25.00M · toplam 100.00M · max 120.00M" in report
    assert "ATH $2.50 (%-60.0) · ATL $0.05 (%+1900.0)" in report


def test_pending_provider_does_not_invent_supply_or_price_extremes():
    report = "\n".join(_fundamental_lines(_candidate("WAITUSDT", 88, ready=False)["metadata"]["fundamentals"]))
    assert "Arz ve ATH/ATL: PROVIDER_COOLDOWN" in report
    assert "dolaşan 0" not in report
