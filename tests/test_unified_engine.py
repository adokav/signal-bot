from __future__ import annotations

from acce_unified import UnifiedConfig, UnifiedRadarEngine, attach_snapshot_to_results
from acce_unified.config import DEFAULT_TRADE_UNIVERSE
from acce_unified.models import CexTicker, MexcListing


class FakeCex:
    def fetch_tickers(self):
        return [CexTicker("BTCUSDT", 100_000, 5.0, 2_000_000_000)]


class BrokenListing:
    def fetch_listings(self):
        raise TimeoutError("fixture timeout")


class WeakListing:
    def fetch_listings(self):
        return [
            MexcListing(
                symbol="NOVA",
                pair="NOVAUSDT",
                title="MEXC Will List Nova (NOVA)",
                rank=20,
                spot_status="NOT_OPEN",
                last_price=0.0,
                change_pct=0.0,
                quote_volume=0.0,
                volume_acceleration=0.0,
            )
        ]


def test_provider_failure_is_isolated_and_core_metadata_is_non_authoritative():
    cfg = UnifiedConfig(cex_enabled=True, listing_enabled=True, cex_top_n=3)
    engine = UnifiedRadarEngine(
        cfg,
        DEFAULT_TRADE_UNIVERSE,
        cex_provider=FakeCex(),
        listing_provider=BrokenListing(),
    )
    snapshot = engine.scan_once(now=1_700_000_000)
    assert snapshot.cex_candidates
    assert snapshot.listing_candidates == ()
    assert snapshot.errors == ("LISTING:TimeoutError",)

    results = [{"symbol": "BTCUSDT", "signal": "LONG", "actionable": True, "score": 1.5}]
    before = dict(results[0])
    attach_snapshot_to_results(results, snapshot)
    assert results[0]["signal"] == before["signal"]
    assert results[0]["actionable"] is before["actionable"]
    assert results[0]["score"] == before["score"]
    assert results[0]["unified_radar"]["can_change_trade_decision"] is False


def test_snapshot_serialization_has_global_execution_invariant():
    cfg = UnifiedConfig(cex_enabled=True, listing_enabled=False)
    snapshot = UnifiedRadarEngine(
        cfg,
        DEFAULT_TRADE_UNIVERSE,
        cex_provider=FakeCex(),
        listing_provider=BrokenListing(),
    ).scan_once(now=1_700_000_000)
    payload = snapshot.to_dict()
    assert payload["can_authorize_trade"] is False
    assert payload["mode"] == "SHADOW"
    assert "listing_candidates" in payload
    assert "listing_filtered_candidates" in payload
    assert "dex_candidates" not in payload


def test_engine_keeps_below_threshold_listing_for_watch_flow():
    cfg = UnifiedConfig(cex_enabled=False, listing_enabled=True)
    snapshot = UnifiedRadarEngine(
        cfg,
        DEFAULT_TRADE_UNIVERSE,
        cex_provider=FakeCex(),
        listing_provider=WeakListing(),
    ).scan_once(now=1_700_000_000)

    assert snapshot.listing_candidates == ()
    assert len(snapshot.listing_filtered_candidates) == 1
    candidate = snapshot.listing_filtered_candidates[0]
    assert candidate.symbol == "NOVAUSDT"
    assert candidate.metadata["filter_reasons"]
    assert candidate.execution_eligible is False
