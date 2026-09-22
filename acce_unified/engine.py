"""Orchestration layer: scan, isolate failures, annotate without authority."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from typing import Any

from .cex import rank_cex_tickers
from .config import UnifiedConfig
from .fundamentals import FundamentalMetricsProvider
from .liquid_long import (
    build_market_context,
    rank_liquid_longs,
    score_technical_long,
    select_enrichment_universe,
    select_liquid_universe,
    supply_gate_ready,
)
from .listings import partition_mexc_listings
from .models import MexcListing, RadarSnapshot
from .providers import MexcNewListingProvider, MexcPublicProvider
from .social import SocialIntelligenceProvider


log = logging.getLogger(__name__)


class UnifiedRadarEngine:
    def __init__(
        self,
        config: UnifiedConfig,
        trade_universe: dict[str, str],
        *,
        cex_provider: Any | None = None,
        listing_provider: Any | None = None,
        social_provider: Any | None = None,
        fundamental_provider: Any | None = None,
    ):
        self.config = config
        self.trade_universe = dict(trade_universe)
        self.cex_provider = cex_provider or MexcPublicProvider(
            timeout=config.request_timeout_seconds
        )
        self.listing_provider = listing_provider or MexcNewListingProvider(
            timeout=config.request_timeout_seconds,
            seen_file=config.listing_seen_file,
            candidate_file=config.listing_candidate_file,
            candidate_ttl_hours=config.listing_candidate_ttl_hours,
            max_candidates=config.listing_max_candidates,
            confirmation_scans=config.listing_confirmation_scans,
        )
        self.social_provider = social_provider or SocialIntelligenceProvider(
            x_bearer_token=os.getenv("X_BEARER_TOKEN", ""),
            reddit_client_id=os.getenv("REDDIT_CLIENT_ID", ""),
            reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
            reddit_user_agent=os.getenv(
                "REDDIT_USER_AGENT",
                "server:adokav-signal-bot:v4.8 (by /u/adokav)",
            ),
            reddit_enabled=config.social_reddit_enabled,
            gdelt_enabled=config.social_gdelt_enabled,
            timeout=config.request_timeout_seconds,
            cache_ttl_seconds=config.social_cache_ttl_seconds,
            max_assets=config.social_max_assets,
            window_hours=config.social_window_hours,
            min_mentions=config.social_min_mentions,
            min_unique_authors=config.social_min_unique_authors,
            min_community_platforms=config.social_min_community_platforms,
            min_sentiment=config.social_min_sentiment,
        )
        self.fundamental_provider = fundamental_provider or FundamentalMetricsProvider(
            demo_api_key=os.getenv("COINGECKO_DEMO_API_KEY", ""),
            pro_api_key=os.getenv("COINGECKO_PRO_API_KEY", ""),
            timeout=config.request_timeout_seconds,
            cache_ttl_seconds=config.fundamental_cache_ttl_seconds,
            max_assets=config.fundamental_max_assets,
        )

    def set_watched_pairs(self, pairs: list[str] | tuple[str, ...] | set[str]) -> None:
        setter = getattr(self.listing_provider, "set_watched_pairs", None)
        if callable(setter):
            setter(pairs)

    def scan_once(self, *, now: int | None = None) -> RadarSnapshot:
        timestamp = int(now if now is not None else time.time())
        cex_candidates = []
        liquid_long_candidates = []
        liquid_universe_size = 0
        liquid_enriched_size = 0
        liquid_supply_ready_size = 0
        liquid_market_context: dict[str, Any] = {}
        listing_candidates = []
        listing_filtered_candidates = []
        social_candidates = []
        fundamental_candidates = []
        errors: list[str] = []
        if self.config.cex_enabled:
            try:
                tickers = self.cex_provider.fetch_tickers()
                cex_candidates = rank_cex_tickers(
                    tickers,
                    trade_universe=self.trade_universe,
                    top_n=self.config.cex_top_n,
                    min_quote_volume=self.config.cex_min_quote_volume,
                )
                try:
                    metrics_fetcher = getattr(
                        self.cex_provider, "fetch_long_metrics", None
                    )
                    if not callable(metrics_fetcher):
                        raise NotImplementedError("provider has no long metrics")
                    liquid_universe = select_liquid_universe(
                        tickers,
                        size=self.config.liquid_universe_size,
                        min_quote_volume=self.config.liquid_min_quote_volume,
                        required_venue="MEXC",
                    )
                    liquid_universe_size = len(liquid_universe)
                    if not liquid_universe:
                        raise RuntimeError("MEXC_LIQUID_UNIVERSE_UNAVAILABLE")
                    market_context = build_market_context(liquid_universe)
                    liquid_market_context = dict(market_context)
                    enrichment_rows = select_enrichment_universe(
                        liquid_universe,
                        size=self.config.liquid_enrichment_size,
                        max_drawdown_pct=self.config.liquid_max_24h_drawdown_pct,
                        max_gain_pct=self.config.liquid_max_24h_gain_pct,
                        max_spread_bps=self.config.liquid_max_spread_bps,
                    )
                    metrics_by_symbol = metrics_fetcher(
                        [item.symbol for item in enrichment_rows]
                    )
                    liquid_enriched_size = sum(
                        (metrics_by_symbol.get(item.symbol) or {}).get("status")
                        == "READY"
                        for item in enrichment_rows
                    )

                    liquidity_ranks = {
                        item.symbol: index
                        for index, item in enumerate(liquid_universe, 1)
                    }
                    technically_ready = []
                    for item in enrichment_rows:
                        technical = score_technical_long(
                            item,
                            metrics_by_symbol.get(item.symbol),
                            liquidity_rank=liquidity_ranks.get(item.symbol, 100),
                            max_drawdown_pct=(
                                self.config.liquid_max_24h_drawdown_pct
                            ),
                            max_gain_pct=self.config.liquid_max_24h_gain_pct,
                            max_spread_bps=self.config.liquid_max_spread_bps,
                        )
                        if technical.get("status") == "READY":
                            technically_ready.append((item, technical))
                    technically_ready.sort(
                        key=lambda row: (
                            int(row[1].get("score") or 0),
                            row[0].quote_volume,
                        ),
                        reverse=True,
                    )
                    fundamental_rows = [
                        MexcListing(
                            symbol=item.symbol[:-4],
                            pair=item.symbol,
                            title=(
                                f"MEXC Likit 100: {item.symbol[:-4]} "
                                f"({item.symbol[:-4]})"
                            ),
                            rank=liquidity_ranks.get(item.symbol, 100),
                            spot_status="OPEN",
                            last_price=item.last_price,
                            change_pct=item.change_pct,
                            quote_volume=item.quote_volume,
                            volume_acceleration=float(
                                (metrics_by_symbol.get(item.symbol) or {}).get(
                                    "volume_ratio"
                                )
                                or 0.0
                            ),
                            discovery_source="LIQUID_100",
                        )
                        for item, _ in technically_ready[
                            : self.config.liquid_fundamental_size
                        ]
                    ]
                    fundamentals_by_symbol: dict[str, dict[str, Any]] = {}
                    if self.config.fundamental_enabled and fundamental_rows:
                        raw_fundamentals = self.fundamental_provider.fetch_many(
                            fundamental_rows
                        )
                        fundamentals_by_symbol = {
                            item.pair: dict(raw_fundamentals.get(item.pair) or {})
                            for item in fundamental_rows
                        }
                    liquid_supply_ready_size = sum(
                        supply_gate_ready(value)
                        for value in fundamentals_by_symbol.values()
                    )
                    liquid_long_candidates = rank_liquid_longs(
                        liquid_universe,
                        metrics_by_symbol,
                        fundamentals_by_symbol,
                        market_context=market_context,
                        trade_universe=self.trade_universe,
                        top_n=self.config.liquid_long_top_n,
                        min_score=self.config.liquid_min_long_score,
                        max_drawdown_pct=(
                            self.config.liquid_max_24h_drawdown_pct
                        ),
                        max_gain_pct=self.config.liquid_max_24h_gain_pct,
                        max_spread_bps=self.config.liquid_max_spread_bps,
                        require_supply_data=self.config.liquid_require_supply_data,
                    )
                except NotImplementedError:
                    pass
                except Exception as exc:
                    errors.append(f"LIQUID_LONG:{type(exc).__name__}")
                    log.warning("MEXC Likit-100 Long radar failed: %s", exc)
            except Exception as exc:
                errors.append(f"CEX:{type(exc).__name__}")
                log.warning("Unified CEX radar failed: %s", exc)
        if self.config.listing_enabled:
            try:
                listings = self.listing_provider.fetch_listings()
                if self.config.fundamental_enabled:
                    try:
                        fundamental_by_pair = self.fundamental_provider.fetch_many(listings)
                        listings = [
                            replace(
                                item,
                                fundamental_data=fundamental_by_pair.get(item.pair, {}),
                            )
                            for item in listings
                        ]
                    except Exception as exc:
                        errors.append(f"FUNDAMENTALS:{type(exc).__name__}")
                        log.warning("Fundamental metrics radar failed: %s", exc)
                if self.config.social_enabled:
                    try:
                        social_by_pair = self.social_provider.fetch_many(listings)
                        listings = [
                            replace(
                                item,
                                social_data=social_by_pair.get(item.pair, {}),
                            )
                            for item in listings
                        ]
                    except Exception as exc:
                        errors.append(f"SOCIAL:{type(exc).__name__}")
                        log.warning("Social intelligence radar failed: %s", exc)
                listing_candidates, listing_filtered_candidates = partition_mexc_listings(
                    listings,
                    trade_universe=self.trade_universe,
                    top_n=self.config.listing_top_n,
                    min_score=self.config.listing_min_score,
                    max_drawdown_pct=self.config.listing_max_drawdown_pct,
                    require_open_spot=self.config.listing_require_open_spot,
                    require_supply_data=self.config.listing_require_supply_data,
                )
                social_candidates = sorted(
                    (
                        item
                        for item in (*listing_candidates, *listing_filtered_candidates)
                        if (
                            int(
                                (item.metadata.get("social") or {}).get(
                                    "mentions_window"
                                )
                                or 0
                            )
                            > 0
                            or (item.metadata.get("social") or {}).get("status")
                            == "READY"
                        )
                    ),
                    key=lambda item: (
                        (item.metadata.get("social") or {}).get("community_gate")
                        == "PASS",
                        int((item.metadata.get("social") or {}).get("viral_potential") or 0),
                        int((item.metadata.get("social") or {}).get("attention_score") or 0),
                    ),
                    reverse=True,
                )
                fundamental_candidates = sorted(
                    (
                        item
                        for item in (*listing_candidates, *listing_filtered_candidates)
                        if (item.metadata.get("fundamentals") or {}).get("status") == "READY"
                    ),
                    key=lambda item: (
                        int((item.metadata.get("fundamentals") or {}).get("fundamental_score") or 0),
                        int((item.metadata.get("fundamentals") or {}).get("coverage_pct") or 0),
                        item.score,
                    ),
                    reverse=True,
                )
            except Exception as exc:
                errors.append(f"LISTING:{type(exc).__name__}")
                log.warning("PhenomenonX MEXC listing radar failed: %s", exc)
        return RadarSnapshot(
            generated_at=timestamp,
            mode=self.config.mode,
            cex_candidates=tuple(cex_candidates),
            liquid_long_candidates=tuple(liquid_long_candidates),
            liquid_universe_size=liquid_universe_size,
            liquid_enriched_size=liquid_enriched_size,
            liquid_supply_ready_size=liquid_supply_ready_size,
            liquid_market_context=liquid_market_context,
            listing_candidates=tuple(listing_candidates),
            listing_filtered_candidates=tuple(listing_filtered_candidates),
            social_candidates=tuple(social_candidates),
            fundamental_candidates=tuple(fundamental_candidates),
            errors=tuple(errors),
        )


