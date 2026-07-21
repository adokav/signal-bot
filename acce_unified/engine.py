"""Orchestration layer: scan, isolate failures, annotate without authority."""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
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


class UnifiedRadarRuntime:
    """Non-blocking periodic runner.

    Network discovery never occupies the main trade loop. A failed scan leaves
    the last good snapshot intact and can never change an existing trade.
    """

    def __init__(self, engine: UnifiedRadarEngine):
        self.engine = engine
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="unified-radar")
        self._future: Future[RadarSnapshot] | None = None
        self._latest: RadarSnapshot | None = None
        self._next_due = 0.0
        self._lock = threading.RLock()

    def tick(self, *, now: float | None = None, force: bool = False) -> RadarSnapshot | None:
        current = float(now if now is not None else time.time())
        with self._lock:
            if self._future is not None and self._future.done():
                try:
                    completed = self._future.result()
                    # A total provider outage must not erase the last known-good view.
                    if (
                        self._latest is None
                        or not completed.errors
                        or completed.cex_candidates
                        or completed.liquid_long_candidates
                        or completed.listing_candidates
                        or completed.listing_filtered_candidates
                    ):
                        self._latest = completed
                except Exception as exc:  # defensive; scan_once isolates provider failures
                    log.warning("Unified radar future failed: %s", exc)
                finally:
                    self._future = None
                    self._next_due = current + self.engine.config.scan_interval_seconds
            if (
                self.engine.config.enabled
                and self._future is None
                and (force or current >= self._next_due)
            ):
                self._future = self._executor.submit(self.engine.scan_once, now=int(current))
            return self._latest

    def latest(self) -> RadarSnapshot | None:
        with self._lock:
            return self._latest

    def set_watched_pairs(self, pairs: list[str] | tuple[str, ...] | set[str]) -> None:
        self.engine.set_watched_pairs(pairs)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def attach_snapshot_to_results(results: list[dict], snapshot: RadarSnapshot | None) -> None:
    """Attach confluence metadata without mutating signal/actionable fields."""

    if snapshot is None:
        return
    by_symbol = {candidate.symbol: candidate for candidate in snapshot.cex_candidates}
    liquid_longs_by_symbol = {
        candidate.symbol: candidate for candidate in snapshot.liquid_long_candidates
    }
    listings_by_symbol = {
        candidate.symbol: candidate
        for candidate in (
            *snapshot.listing_filtered_candidates,
            *snapshot.listing_candidates,
        )
    }
    for result in results:
        symbol = str(result.get("symbol") or "").upper()
        candidate = by_symbol.get(symbol)
        result["unified_radar"] = {
            "mode": snapshot.mode,
            "can_change_trade_decision": False,
            "candidate": candidate.to_dict() if candidate else None,
            "liquid_100_long": (
                liquid_longs_by_symbol[symbol].to_dict()
                if symbol in liquid_longs_by_symbol
                else None
            ),
            "new_listing": (
                listings_by_symbol[symbol].to_dict()
                if symbol in listings_by_symbol
                else None
            ),
        }


def _candidate_line(item: dict | None) -> str:
    if not item:
        return "yok"
    return f"{item.get('symbol', '?')} {int(item.get('score', 0))}/100 ({item.get('stage', '-')})"


def format_snapshot_brief(snapshot: RadarSnapshot | dict | None) -> str:
    if snapshot is None:
        return "Unified Radar: ilk gölge tarama bekleniyor."
    data = snapshot.to_dict() if isinstance(snapshot, RadarSnapshot) else snapshot
    cex = (data.get("cex_candidates") or [None])[0]
    liquid_long = (data.get("liquid_long_candidates") or [None])[0]
    listing = (data.get("listing_candidates") or [None])[0]
    filtered_count = len(data.get("listing_filtered_candidates") or [])
    fundamental_count = len(data.get("fundamental_candidates") or [])
    errors = data.get("errors") or []
    error_text = f" | hata: {', '.join(errors)}" if errors else ""
    return (
        f"Unified Radar [{data.get('mode', 'SHADOW')}]: "
        f"PriceMonitorX {_candidate_line(cex)} | "
        f"Likit100 Long {_candidate_line(liquid_long)} | "
        f"PhenomenonX/MEXC Yeni {_candidate_line(listing)}"
        f" | filtrede {filtered_count}"
        f" | temel veri {fundamental_count}"
        f" | trade yetkisi: YOK{error_text}"
    )


def format_liquid_long_report(
    snapshot: RadarSnapshot | dict | None,
    *,
    limit: int = 5,
) -> str:
    """Telegram-ready view of the MEXC top-liquidity long shortlist."""
    if snapshot is None:
        return "💧 MEXC LİKİT 100 — LONG İLK 5\n\nİlk tarama henüz tamamlanmadı."
    data = snapshot.to_dict() if isinstance(snapshot, RadarSnapshot) else snapshot
    rows = data.get("liquid_long_candidates") or []
    universe_size = int(data.get("liquid_universe_size") or 0)
    enriched_size = int(data.get("liquid_enriched_size") or 0)
    supply_ready_size = int(data.get("liquid_supply_ready_size") or 0)
    market_context = data.get("liquid_market_context") or {}
    if not rows:
        return (
            "💧 MEXC LİKİT 100 — LONG İLK 5\n\n"
            f"MEXC hacim evreni: {universe_size}/100 · teknik: {enriched_size} · "
            f"arz: {supply_ready_size}\n"
            f"Rejim: {market_context.get('regime') or '?'} · "
            f"pozitif genişlik %{float(market_context.get('positive_breadth_pct') or 0):.1f}\n"
            "Şu an düşüş, trend, momentum, oynaklık ve doğrulanmış arz "
            "kapılarının tamamını geçen Long adayı yok."
        )

    stage_labels = {
        "A_PLUS": "🏆 A+",
        "STRONG_LONG": "🟢 Güçlü Long",
        "WATCH_LONG": "🟡 Long İzle",
    }
    lines = [
        "💧 MEXC LİKİT 100 — LONG İLK 5",
        f"Hacim evreni {universe_size}/100 · teknik {enriched_size} · "
        f"arz doğrulanan {supply_ready_size}",
        "Sıralama: trend + momentum + katılım + likidite + arz + rejim",
        "",
    ]
    for index, item in enumerate(rows[: max(1, limit)], 1):
        metadata = item.get("metadata") or {}
        metrics = metadata.get("long_metrics") or {}
        context = metadata.get("market_context") or {}
        fundamentals = metadata.get("fundamentals") or {}
        lines.extend([
            f"{index}. {str(item.get('symbol') or '?').removesuffix('USDT')} — "
            f"{int(item.get('score') or 0)}/100 · "
            f"{stage_labels.get(str(item.get('stage') or ''), item.get('stage') or '-')}",
            f"   Likidite #{int(metadata.get('liquidity_rank') or 0)} | "
            f"Teknik {int(metadata.get('technical_score') or 0)} | "
            f"Arz {int(metadata.get('supply_score') or 0)} | "
            f"Rejim {context.get('regime') or '?'}",
            f"   24s %{float(metadata.get('change_pct') or 0):+.1f} | "
            f"1s %{float(metrics.get('change_1h_pct') or 0):+.1f} | "
            f"4s %{float(metrics.get('change_4h_pct') or 0):+.1f} | "
            f"RSI {float(metrics.get('rsi14') or 0):.0f}",
            f"   Hacim ivmesi {float(metrics.get('volume_ratio') or 0):.1f}x | "
            f"ATR %{float(metrics.get('atr_pct') or 0):.1f} | "
            f"MEXC 24s {_money(metadata.get('quote_volume'))}",
            f"   Arz: dolaşan {_quantity(fundamentals.get('circulating_supply'))} | "
            f"toplam {_quantity(fundamentals.get('total_supply'))} | "
            f"max {_quantity(fundamentals.get('max_supply'), missing='açıklanmamış/sınırsız')}",
            "",
        ])
    lines.append("Araştırma sıralamasıdır; otomatik emir oluşturmaz.")
    return "\n".join(lines)


def _money(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"${number / 1_000:.0f}K"
    return f"${number:.0f}"


def _quantity(value: Any, *, missing: str = "açıklanmamış") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return missing
    if number >= 1_000_000_000_000:
        return f"{number / 1_000_000_000_000:.2f}T"
    if number >= 1_000_000_000:
        return f"{number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"{number / 1_000:.2f}K"
    return f"{number:g}"


def _stage_label(stage: Any) -> str:
    return {
        "HOT": "🔥 Güçlü",
        "BUILDING": "📈 Isınıyor",
        "WATCH": "👀 İzle",
        "WEAK": "⚪ Zayıf",
        "CROWDED": "⛔ Geç kalınmış",
    }.get(str(stage or "").upper(), str(stage or "-"))


def _fundamental_report_line(metadata: dict[str, Any]) -> str:
    fundamentals = metadata.get("fundamentals") or {}
    if fundamentals.get("status") != "READY":
        status = str(fundamentals.get("status") or "DATA_PENDING").upper()
        label = {
            "AMBIGUOUS": "kimlik belirsiz",
            "NOT_FOUND": "veri bulunamadı",
            "PROVIDER_COOLDOWN": "sağlayıcı beklemede",
            "PROVIDER_UNAVAILABLE": "sağlayıcı erişilemiyor",
        }.get(status, "veri bekleniyor")
        return f"   🧬 Temel: {label}"
    circulation = fundamentals.get("circulation_pct")
    circulation_text = (
        f"%{float(circulation):.0f}" if circulation is not None else "?"
    )
    return (
        f"   🧬 Temel {int(fundamentals.get('fundamental_score') or 0)}/100 | "
        f"PD {_money(fundamentals.get('market_cap_usd'))} | "
        f"FDV {_money(fundamentals.get('fully_diluted_valuation_usd'))} | "
        f"dolaşım {circulation_text}\n"
        f"   Arz: dolaşan {_quantity(fundamentals.get('circulating_supply'))} | "
        f"toplam {_quantity(fundamentals.get('total_supply'))} | "
        f"max {_quantity(fundamentals.get('max_supply'), missing='açıklanmamış/sınırsız')}"
    )


def format_listing_report(
    snapshot: RadarSnapshot | dict | None,
    *,
    limit: int = 3,
) -> str:
    """Human-readable Telegram report for PhenomenonX MEXC candidates."""
    if snapshot is None:
        return "🆕 YENİ LİSTELEME — GÜÇLÜ ADAYLAR\n\nİlk tarama henüz tamamlanmadı."
    data = snapshot.to_dict() if isinstance(snapshot, RadarSnapshot) else snapshot
    rows = data.get("listing_candidates") or []
    if not rows:
        errors = [str(item) for item in (data.get("errors") or []) if str(item).startswith("LISTING:")]
        suffix = f"\nKaynak durumu: {', '.join(errors)}" if errors else ""
        return (
            "🆕 YENİ LİSTELEME — GÜÇLÜ ADAYLAR\n\n"
            "Şu an güçlü aday seviyesine ulaşan yeni listeleme yok."
            f"{suffix}"
        )

    lines = [
        "🆕 YENİ LİSTELEME FIRSAT PENCERESİ",
        "Yeni olay + MEXC Spot teyidi + arz doğrulaması | emir yetkisi yok",
        "",
    ]
    for index, item in enumerate(rows[: max(1, limit)], 1):
        metadata = item.get("metadata") or {}
        reasons = item.get("reasons") or []
        risks = item.get("risk_flags") or []
        spot_status = str(metadata.get("spot_status") or "UNKNOWN").upper()
        status = {
            "OPEN": "Spot açık",
            "NOT_OPEN": "Spot bekleniyor",
            "UNKNOWN": "Spot teyidi yok",
        }.get(spot_status, "Spot teyidi yok")
        try:
            change = float(metadata.get("change_pct") or 0)
            acceleration = float(metadata.get("volume_acceleration") or 0)
            price = float(metadata.get("last_price") or 0)
        except (TypeError, ValueError):
            change, acceleration, price = 0.0, 0.0, 0.0
        lines.extend(
            [
                f"{index}. {item.get('symbol', '?')} — {int(item.get('score', 0))}/100 · {_stage_label(item.get('stage'))}",
                f"   {status} | 24s hacim {_money(metadata.get('quote_volume'))} | değişim %{change:+.1f}",
                f"   5dk hacim ivmesi {acceleration:.1f}x | fiyat {price:g}",
            ]
        )
        lines.append(_fundamental_report_line(metadata))
        if reasons:
            lines.append("   ✅ " + " · ".join(str(value) for value in reasons[:3]))
        if risks:
            lines.append("   ⚠️ " + " · ".join(str(value) for value in risks[:3]))
        title = str(metadata.get("title") or "").strip()
        if title:
            lines.append(f"   Duyuru: {title[:160]}")
        lines.append("")
    lines.append("Bir aday düğmesine dokunarak detay ve takip işlemlerini açabilirsin.")
    return "\n".join(lines)


def format_filtered_listing_report(
    snapshot: RadarSnapshot | dict | None,
    *,
    limit: int = 5,
) -> str:
    """Explain candidates hidden by the quality/chase filter."""
    if snapshot is None:
        return "🟡 YENİ LİSTELEME — İZLEME\n\nİlk tarama henüz tamamlanmadı."
    data = snapshot.to_dict() if isinstance(snapshot, RadarSnapshot) else snapshot
    rows = data.get("listing_filtered_candidates") or []
    if not rows:
        return "🟡 YENİ LİSTELEME — İZLEME\n\nŞu an izleme havuzunda aday yok."

    lines = [
        "🟡 YENİ LİSTELEME — İZLEME HAVUZU",
        "Henüz güçlü değil; gerekçesiyle izlenir ve yeniden puanlanır.",
        "",
    ]
    for index, item in enumerate(rows[: max(1, limit)], 1):
        metadata = item.get("metadata") or {}
        filter_reasons = metadata.get("filter_reasons") or []
        risks = item.get("risk_flags") or []
        lines.extend(
            [
                f"{index}. {item.get('symbol', '?')} — {int(item.get('score', 0))}/100 · {_stage_label(item.get('stage'))}",
                "   Filtre: " + " · ".join(str(value) for value in filter_reasons[:2]),
                f"   24s hacim {_money(metadata.get('quote_volume'))} | 5dk ivme {float(metadata.get('volume_acceleration') or 0):.1f}x",
            ]
        )
        lines.append(_fundamental_report_line(metadata))
        if risks:
            lines.append("   ⚠️ " + " · ".join(str(value) for value in risks[:2]))
        lines.append("")
    lines.append("Bir aday düğmesine dokunarak detay ve kalıcı takip işlemlerini açabilirsin.")
    return "\n".join(lines)
