"""Orchestration layer: scan, isolate failures, annotate without authority."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .cex import rank_cex_tickers
from .config import UnifiedConfig
from .listings import rank_mexc_listings
from .models import RadarSnapshot
from .providers import MexcNewListingProvider, MexcPublicProvider


log = logging.getLogger(__name__)


class UnifiedRadarEngine:
    def __init__(
        self,
        config: UnifiedConfig,
        trade_universe: dict[str, str],
        *,
        cex_provider: Any | None = None,
        listing_provider: Any | None = None,
    ):
        self.config = config
        self.trade_universe = dict(trade_universe)
        self.cex_provider = cex_provider or MexcPublicProvider(
            timeout=config.request_timeout_seconds
        )
        self.listing_provider = listing_provider or MexcNewListingProvider(
            timeout=config.request_timeout_seconds,
            seen_file=config.listing_seen_file,
            max_candidates=config.listing_max_candidates,
        )

    def scan_once(self, *, now: int | None = None) -> RadarSnapshot:
        timestamp = int(now if now is not None else time.time())
        cex_candidates = []
        listing_candidates = []
        errors: list[str] = []
        if self.config.cex_enabled:
            try:
                cex_candidates = rank_cex_tickers(
                    self.cex_provider.fetch_tickers(),
                    trade_universe=self.trade_universe,
                    top_n=self.config.cex_top_n,
                    min_quote_volume=self.config.cex_min_quote_volume,
                )
            except Exception as exc:
                errors.append(f"CEX:{type(exc).__name__}")
                log.warning("Unified CEX radar failed: %s", exc)
        if self.config.listing_enabled:
            try:
                listing_candidates = rank_mexc_listings(
                    self.listing_provider.fetch_listings(),
                    trade_universe=self.trade_universe,
                    top_n=self.config.listing_top_n,
                    min_score=self.config.listing_min_score,
                )
            except Exception as exc:
                errors.append(f"LISTING:{type(exc).__name__}")
                log.warning("PhenomenonX MEXC listing radar failed: %s", exc)
        return RadarSnapshot(
            generated_at=timestamp,
            mode=self.config.mode,
            cex_candidates=tuple(cex_candidates),
            listing_candidates=tuple(listing_candidates),
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
                        or completed.listing_candidates
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

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def attach_snapshot_to_results(results: list[dict], snapshot: RadarSnapshot | None) -> None:
    """Attach confluence metadata without mutating signal/actionable fields."""

    if snapshot is None:
        return
    by_symbol = {candidate.symbol: candidate for candidate in snapshot.cex_candidates}
    listings_by_symbol = {
        candidate.symbol: candidate for candidate in snapshot.listing_candidates
    }
    for result in results:
        symbol = str(result.get("symbol") or "").upper()
        candidate = by_symbol.get(symbol)
        result["unified_radar"] = {
            "mode": snapshot.mode,
            "can_change_trade_decision": False,
            "candidate": candidate.to_dict() if candidate else None,
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
    listing = (data.get("listing_candidates") or [None])[0]
    errors = data.get("errors") or []
    error_text = f" | hata: {', '.join(errors)}" if errors else ""
    return (
        f"Unified Radar [{data.get('mode', 'SHADOW')}]: "
        f"PriceMonitorX {_candidate_line(cex)} | "
        f"PhenomenonX/MEXC Yeni {_candidate_line(listing)}"
        f" | trade yetkisi: YOK{error_text}"
    )


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


def format_listing_report(
    snapshot: RadarSnapshot | dict | None,
    *,
    limit: int = 3,
) -> str:
    """Human-readable Telegram report for PhenomenonX MEXC candidates."""
    if snapshot is None:
        return "🆕 PHENOMENONX — MEXC NEW LISTING\n\nİlk tarama henüz tamamlanmadı."
    data = snapshot.to_dict() if isinstance(snapshot, RadarSnapshot) else snapshot
    rows = data.get("listing_candidates") or []
    if not rows:
        errors = [str(item) for item in (data.get("errors") or []) if str(item).startswith("LISTING:")]
        suffix = f"\nKaynak durumu: {', '.join(errors)}" if errors else ""
        return (
            "🆕 PHENOMENONX — MEXC NEW LISTING\n\n"
            "Şu an eşiği geçen yeni listeleme adayı yok."
            f"{suffix}"
        )

    lines = [
        "🆕 PHENOMENONX — MEXC NEW LISTING",
        "Resmî duyuru + MEXC Spot teyidi | emir yetkisi yok",
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
                f"{index}. {item.get('symbol', '?')} — {int(item.get('score', 0))}/100 · {item.get('stage', '-')}",
                f"   {status} | 24s hacim {_money(metadata.get('quote_volume'))} | değişim %{change:+.1f}",
                f"   Hacim ivmesi {acceleration:.1f}x | fiyat {price:g}",
            ]
        )
        if reasons:
            lines.append("   ✅ " + " · ".join(str(value) for value in reasons[:3]))
        if risks:
            lines.append("   ⚠️ " + " · ".join(str(value) for value in risks[:3]))
        title = str(metadata.get("title") or "").strip()
        if title:
            lines.append(f"   Duyuru: {title[:160]}")
        lines.append("")
    lines.append("Erken keşif radarıdır; yeni coin otomatik olarak işlem evrenine alınmaz.")
    return "\n".join(lines)
