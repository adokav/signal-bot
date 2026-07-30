"""CoinGecko listing fundamentals with explicit ATH/ATL context.

This adapter extends the existing point-in-time fundamental provider without
changing scoring semantics.  ATH/ATL values are display and research evidence;
they never authorize an order.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Iterable

from .fundamentals import (
    FundamentalMetricsProvider,
    FundamentalRateLimitError,
    _pending,
    _signed_number,
    score_fundamental_snapshot,
)
from .models import MexcListing


log = logging.getLogger(__name__)


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def enrich_price_extremes(signal: dict[str, Any], row: dict[str, Any] | None) -> dict[str, Any]:
    """Attach provider-native ATH/ATL facts while keeping missingness explicit."""
    result = dict(signal)
    row = row or {}
    result.update(
        {
            "ath_price_usd": _positive_number(row.get("ath")),
            "ath_change_pct": _signed_number(row.get("ath_change_percentage")),
            "ath_date": row.get("ath_date") or None,
            "atl_price_usd": _positive_number(row.get("atl")),
            "atl_change_pct": _signed_number(row.get("atl_change_percentage")),
            "atl_date": row.get("atl_date") or None,
        }
    )
    return result


class ListingFundamentalMetricsProvider(FundamentalMetricsProvider):
    """Fundamental provider that preserves ATH/ATL fields from `/coins/markets`."""

    def fetch_many(self, listings: Iterable[MexcListing]) -> dict[str, dict[str, Any]]:
        ordered: list[MexcListing] = []
        seen: set[str] = set()
        for item in listings:
            if item.pair not in seen:
                seen.add(item.pair)
                ordered.append(item)
        ordered = sorted(
            ordered,
            key=lambda item: (item.manually_watched, item.spot_status == "OPEN", -item.rank),
            reverse=True,
        )[: self.max_assets]

        now = int(time.time())
        result: dict[str, dict[str, Any]] = {}
        pending: list[MexcListing] = []
        with self._lock:
            for item in ordered:
                cached = self._cache.get(item.pair)
                if cached and now - cached[0] < self.cache_ttl_seconds:
                    result[item.pair] = dict(cached[1])
                else:
                    pending.append(item)
            retry_at = self._next_retry_at

        if not pending:
            return result
        if now < retry_at:
            wait_minutes = max(1, (retry_at - now + 59) // 60)
            for item in pending:
                result[item.pair] = _pending(
                    "PROVIDER_COOLDOWN",
                    f"CoinGecko hız limiti; yaklaşık {wait_minutes} dk sonra yenilenecek",
                )
            return result

        symbols = sorted({item.symbol.upper() for item in pending})
        try:
            rows = self._fetch_rows(symbols)
        except FundamentalRateLimitError as exc:
            with self._lock:
                self._next_retry_at = now + exc.retry_after
            log.warning("CoinGecko temel radar hız limiti: %s", exc)
            for item in pending:
                result[item.pair] = _pending("PROVIDER_COOLDOWN", str(exc))
            return result
        except Exception as exc:
            with self._lock:
                self._next_retry_at = now + 5 * 60
            log.warning("CoinGecko temel radar başarısız: %s", exc)
            for item in pending:
                result[item.pair] = _pending(
                    "PROVIDER_UNAVAILABLE", "temel veri sağlayıcısına ulaşılamadı"
                )
            return result

        by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_symbol[str(row.get("symbol") or "").upper()].append(row)

        for item in pending:
            matched, confidence, matched_by = self._resolve_identity(
                item, by_symbol.get(item.symbol.upper(), [])
            )
            if matched_by == "NOT_FOUND":
                signal = _pending("NOT_FOUND", "CoinGecko üzerinde eşleşen varlık bulunamadı")
            elif matched_by == "AMBIGUOUS":
                signal = _pending(
                    "AMBIGUOUS", "aynı sembollü birden fazla proje var; kimlik doğrulanamadı"
                )
            else:
                signal = enrich_price_extremes(
                    score_fundamental_snapshot(
                        matched,
                        mexc_quote_volume=item.quote_volume,
                        identity_confidence=confidence,
                        matched_by=matched_by,
                    ),
                    matched,
                )
            result[item.pair] = signal
            with self._lock:
                cached_at = now
                if signal.get("status") != "READY":
                    cached_at = now - self.cache_ttl_seconds + min(
                        5 * 60, self.cache_ttl_seconds
                    )
                self._cache[item.pair] = (cached_at, dict(signal))
        return result
