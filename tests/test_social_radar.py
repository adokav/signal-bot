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
    assert signal["status"] == "COMMUNITY_SOURCE_MISSING"
    assert signal["community_gate"] == "UNAVAILABLE"
    assert signal["coverage_pct"] == 0
    assert signal["viral_potential"] is None
    assert signal["can_authorize_trade"] is False


def test_news_only_is_context_not_a_community_opinion_score():
    signal = score_social_snapshot(
        {
            "news_count": 8,
            "news_platforms": ["NEWS"],
            "news_coverage_pct": 25,
            "coverage_pct": 25,
            "sources": [{"provider": "GDELT", "source_type": "NEWS"}],
        }
    )

    assert signal["status"] == "COMMUNITY_SOURCE_MISSING"
    assert signal["community_gate"] == "UNAVAILABLE"
    assert signal["viral_potential"] is None
    assert signal["news_count"] == 8


def test_sparse_community_evidence_waits_instead_of_becoming_quiet():
    signal = score_social_snapshot(
        {
            "configured_community_platforms": ["X"],
            "reached_community_platforms": ["X"],
            "community_platforms": ["X"],
            "mentions_window": 2,
            "unique_authors_window": 2,
            "community_texts": ["NOVA looks promising", "NOVA roadmap"],
        }
    )

    assert signal["status"] == "INSUFFICIENT_DATA"
    assert signal["stage"] == "INSUFFICIENT_DATA"
    assert signal["community_gate"] == "WAIT"
    assert signal["viral_potential"] is None


def test_organic_positive_opinions_can_pass_the_alert_gate():
    signal = score_social_snapshot(
        {
            "configured_community_platforms": ["X"],
            "reached_community_platforms": ["X"],
            "community_platforms": ["X"],
            "mentions_window": 12,
            "previous_mentions_window": 1,
            "unique_authors_window": 10,
            "previous_unique_authors_window": 1,
            "engagements_window": 300,
            "credible_authors_window": 3,
            "active_buckets": 5,
            "duplicate_ratio": 0.0,
            "new_account_ratio": 0.0,
            "author_concentration": 0.1,
            "community_texts": [
                "NOVA has strong utility and community",
                "NOVA roadmap and launch look promising",
                "NOVA mainnet adoption is growing",
                "NOVA partnership looks bullish",
            ],
        },
        volume_acceleration=2.0,
    )

    assert signal["status"] == "READY"
    assert signal["community_gate"] == "PASS"
    assert signal["viral_potential"] >= 55
    assert signal["bullish_count"] == 4


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
    assert signal["community_gate"] == "BLOCK"
    assert signal["manipulation_risk"] >= 60
    assert signal["viral_potential"] < signal["attention_score"]


def test_engine_keeps_social_ranking_separate_from_market_score():
    config = UnifiedConfig(
        cex_enabled=False,
        listing_enabled=True,
        social_enabled=True,
        fundamental_enabled=False,
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
    assert candidate.metadata["social"]["community_gate"] == "PASS"
    assert candidate.metadata["social"]["can_authorize_trade"] is False
    assert candidate.execution_eligible is False
