from __future__ import annotations

from acce_unified import UnifiedConfig, UnifiedRadarEngine, attach_snapshot_to_results
from acce_unified.config import DEFAULT_TRADE_UNIVERSE
from acce_unified.models import CexTicker


class FakeCex:
    def fetch_tickers(self):
        return [CexTicker("BTCUSDT", 100_000, 5.0, 2_000_000_000)]


class BrokenDex:
    def fetch_pairs(self):
        raise TimeoutError("fixture timeout")


def test_provider_failure_is_isolated_and_core_metadata_is_non_authoritative():
    cfg = UnifiedConfig(cex_enabled=True, dex_enabled=True, cex_top_n=3)
    engine = UnifiedRadarEngine(
        cfg,
        DEFAULT_TRADE_UNIVERSE,
        cex_provider=FakeCex(),
        dex_provider=BrokenDex(),
    )
    snapshot = engine.scan_once(now=1_700_000_000)
    assert snapshot.cex_candidates
    assert snapshot.dex_candidates == ()
    assert snapshot.errors == ("DEX:TimeoutError",)

    results = [{"symbol": "BTCUSDT", "signal": "LONG", "actionable": True, "score": 1.5}]
    before = dict(results[0])
    attach_snapshot_to_results(results, snapshot)
    assert results[0]["signal"] == before["signal"]
    assert results[0]["actionable"] is before["actionable"]
    assert results[0]["score"] == before["score"]
    assert results[0]["unified_radar"]["can_change_trade_decision"] is False


def test_snapshot_serialization_has_global_execution_invariant():
    cfg = UnifiedConfig(cex_enabled=True, dex_enabled=False)
    snapshot = UnifiedRadarEngine(
        cfg,
        DEFAULT_TRADE_UNIVERSE,
        cex_provider=FakeCex(),
        dex_provider=BrokenDex(),
    ).scan_once(now=1_700_000_000)
    payload = snapshot.to_dict()
    assert payload["can_authorize_trade"] is False
    assert payload["mode"] == "SHADOW"
