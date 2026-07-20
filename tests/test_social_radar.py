from __future__ import annotations

from acce_unified import UnifiedConfig, UnifiedRadarEngine
from acce_unified.config import DEFAULT_TRADE_UNIVERSE
from acce_unified.models import MexcListing
from acce_unified.social import score_social_snapshot


def _listing() -> MexcListing:
    return MexcListing(
        symbol="NOVA",
        pair="NOVAUSDT",
        title="MEXC Will List Nova (NOVA)",
        rank=1,
        spot_status="OPEN",
        last_price=0.42,
        change_pct=4.0,
        quote_volume=3_000_000,
        volume_acceleration=2.2,
    )


class _ListingProvider:
    def fetch_listings(self):
        return [_listing()]


class _SocialProvider:
    def fetch_many(self, listings):
        assert [item.pair for item in listings] == ["NOVAUSDT"]
        return {
            "NOVAUSDT": score_social_snapshot(
                {
                    "mentions_1h": 48,
                    "previous_mentions_1h": 8,
                    "unique_authors_1h": 36,
                    "previous_unique_authors_1h": 7,
                    "engagements_1h": 900,
                    "credible_authors_1h": 8,
                    "duplicate_ratio": 0.05,
                    "new_account_ratio": 0.03,
                    "author_concentration": 0.08,
                    "active_buckets": 5,
                    "sentiment_score": 42,
                    "platforms": ["X", "NEWS"],
                    "coverage_pct": 90,
                    "sources": [{"title": "Nova launch", "url": "https://example.com/nova"}],
                },
                price_change_pct=4,
                volume_acceleration=2.2,
            )
        }


def test_missing_social_data_is_pending_not_negative():
    signal = score_social_snapshot(None)

    assert signal["stage"] == "DATA_PENDING"
    assert signal["coverage_pct"] == 0
    assert signal["can_authorize_trade"] is False


def test_duplicate_burst_is_marked_suspicious_even_when_mentions_rise():
    signal = score_social_snapshot(
        {
            "mentions_1h": 100,
            "previous_mentions_1h": 5,
            "unique_authors_1h": 12,
            "previous_unique_authors_1h": 4,
            "engagements_1h": 500,
            "duplicate_ratio": 0.9,
            "new_account_ratio": 0.7,
            "author_concentration": 0.8,
            "platforms": ["X"],
            "coverage_pct": 70,
        }
    )

    assert signal["stage"] == "SUSPICIOUS"
    assert signal["manipulation_risk"] >= 60
    assert signal["viral_potential"] < signal["attention_score"]


def test_engine_keeps_social_ranking_separate_from_market_score():
    config = UnifiedConfig(
        cex_enabled=False,
        listing_enabled=True,
        social_enabled=True,
    )
    snapshot = UnifiedRadarEngine(
        config,
        DEFAULT_TRADE_UNIVERSE,
        listing_provider=_ListingProvider(),
        social_provider=_SocialProvider(),
    ).scan_once(now=1_700_000_000)

    assert len(snapshot.social_candidates) == 1
    candidate = snapshot.social_candidates[0]
    assert candidate.symbol == "NOVAUSDT"
    assert candidate.metadata["social"]["stage"] in {"EMERGING", "CONFIRMED"}
    assert candidate.metadata["social"]["can_authorize_trade"] is False
    assert candidate.execution_eligible is False
