"""Independent market-fundamental intelligence for MEXC listing candidates.

The provider deliberately keeps identity resolution and data availability
explicit.  A symbol collision is never resolved by silently selecting the
largest token, and a missing provider response is never scored as poor
fundamentals.  Fundamental output may prioritise research, but cannot approve
or place an order.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict
from typing import Any, Iterable

import requests

from .models import MexcListing


log = logging.getLogger(__name__)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _signed_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _pending(status: str = "DATA_PENDING", note: str | None = None) -> dict[str, Any]:
    return {
        "status": status,
        "stage": "DATA_PENDING",
        "fundamental_score": None,
        "identity_confidence": 0,
        "coverage_pct": 0,
        "provider": "COINGECKO",
        "circulating_supply": None,
        "total_supply": None,
        "max_supply": None,
        "supply_data_complete": False,
        "supply_disclosure": "UNVERIFIED",
        "reasons": [note or "temel veri henüz doğrulanmadı"],
        "risk_flags": [],
        "can_authorize_trade": False,
    }


def score_fundamental_snapshot(
    row: dict[str, Any] | None,
    *,
    mexc_quote_volume: float = 0.0,
    identity_confidence: int = 0,
    matched_by: str = "UNRESOLVED",
) -> dict[str, Any]:
    """Score transparent market fundamentals without changing market rank.

    This is a research-quality score, not a valuation model.  Size, dilution,
    turnover and supply disclosure are kept visible so the score remains
    explainable and can later be cohort-backtested.
    """
    if not row:
        return _pending()

    market_cap = _number(row.get("market_cap"))
    fdv = _number(row.get("fully_diluted_valuation"))
    total_volume = _number(row.get("total_volume"))
    circulating = _number(row.get("circulating_supply"))
    total_supply = _number(row.get("total_supply"))
    max_supply = _number(row.get("max_supply"))
    market_cap_rank = _number(row.get("market_cap_rank"))
    ath_change = _signed_number(row.get("ath_change_percentage"))

    supply_ceiling = max(
        value for value in (total_supply, max_supply) if value is not None
    ) if total_supply is not None or max_supply is not None else None
    circulation_pct = (
        _clamp(circulating / supply_ceiling * 100.0)
        if circulating is not None and supply_ceiling is not None
        else None
    )
    mcap_to_fdv_pct = (
        _clamp(market_cap / fdv * 100.0)
        if market_cap is not None and fdv is not None
        else None
    )
    dilution_basis_pct = min(
        value for value in (circulation_pct, mcap_to_fdv_pct) if value is not None
    ) if circulation_pct is not None or mcap_to_fdv_pct is not None else None
    volume_to_mcap_pct = (
        total_volume / market_cap * 100.0
        if total_volume is not None and market_cap is not None
        else None
    )
    mexc_volume_share_pct = (
        max(0.0, float(mexc_quote_volume)) / total_volume * 100.0
        if total_volume is not None and mexc_quote_volume > 0
        else None
    )

    covered = (
        market_cap,
        fdv,
        total_volume,
        circulating,
        supply_ceiling,
        market_cap_rank,
        ath_change,
    )
    coverage_pct = int(round(sum(value is not None for value in covered) / len(covered) * 100))

    coverage_points = 20.0 * coverage_pct / 100.0
    if market_cap is None:
        size_points = 0.0
    elif market_cap < 1_000_000:
        size_points = 3.0
    elif market_cap < 5_000_000:
        size_points = 7.0
    elif market_cap < 25_000_000:
        size_points = 12.0
    elif market_cap < 250_000_000:
        size_points = 17.0
    else:
        size_points = 20.0

    if dilution_basis_pct is None:
        dilution_points = 0.0
    elif dilution_basis_pct >= 75:
        dilution_points = 30.0
    elif dilution_basis_pct >= 50:
        dilution_points = 24.0
    elif dilution_basis_pct >= 25:
        dilution_points = 14.0
    else:
        dilution_points = 5.0

    if volume_to_mcap_pct is None:
        turnover_points = 0.0
    elif 3 <= volume_to_mcap_pct <= 80:
        turnover_points = 20.0
    elif 1 <= volume_to_mcap_pct <= 150:
        turnover_points = 14.0
    elif volume_to_mcap_pct <= 300:
        turnover_points = 8.0
    else:
        turnover_points = 3.0

    supply_points = 10.0 if circulating and supply_ceiling else 5.0 if supply_ceiling else 0.0
    score = int(round(_clamp(
        coverage_points + size_points + dilution_points + turnover_points + supply_points
    )))

    risks: list[str] = []
    reasons: list[str] = []
    if market_cap is not None:
        reasons.append("piyasa değeri doğrulandı")
        if market_cap < 1_000_000:
            risks.append("mikro piyasa değeri (< $1M)")
    else:
        risks.append("piyasa değeri açıklanmamış")
    if dilution_basis_pct is not None:
        reasons.append(f"dolaşım/FDV oranı %{dilution_basis_pct:.0f}")
        if dilution_basis_pct < 25:
            risks.append("yüksek seyrelme veya token açılım riski")
    else:
        risks.append("FDV/dolaşım arzı doğrulanamadı")
    if volume_to_mcap_pct is not None:
        reasons.append(f"global 24s hacim/PD %{volume_to_mcap_pct:.1f}")
        if volume_to_mcap_pct > 300:
            risks.append("olağandışı yüksek devir; spekülasyon riski")
    if mexc_volume_share_pct is not None and mexc_volume_share_pct > 80:
        risks.append("hacim MEXC üzerinde yoğunlaşmış olabilir")
    if supply_ceiling is not None:
        reasons.append("arz üst sınırı/verisi mevcut")

    forced_high_risk = (
        (dilution_basis_pct is not None and dilution_basis_pct < 15)
        or (volume_to_mcap_pct is not None and volume_to_mcap_pct > 300)
    )
    if forced_high_risk or score < 45:
        stage = "HIGH_RISK"
    elif score >= 75:
        stage = "STRONG"
    elif score >= 60:
        stage = "HEALTHY"
    else:
        stage = "SPECULATIVE"

    coin_id = str(row.get("id") or "").strip()
    supply_data_complete = bool(
        circulating is not None
        and (total_supply is not None or max_supply is not None)
    )
    if supply_data_complete:
        supply_disclosure = "VERIFIED"
    elif circulating is not None:
        supply_disclosure = "PARTIAL"
    else:
        supply_disclosure = "UNVERIFIED"
    return {
        "status": "READY",
        "stage": stage,
        "fundamental_score": score,
        "identity_confidence": int(_clamp(identity_confidence)),
        "matched_by": matched_by,
        "coverage_pct": coverage_pct,
        "provider": "COINGECKO",
        "provider_id": coin_id or None,
        "name": row.get("name"),
        "market_cap_usd": market_cap,
        "fully_diluted_valuation_usd": fdv,
        "total_volume_usd": total_volume,
        "market_cap_rank": int(market_cap_rank) if market_cap_rank else None,
        "circulating_supply": circulating,
        "total_supply": total_supply,
        "max_supply": max_supply,
        "supply_data_complete": supply_data_complete,
        "supply_disclosure": supply_disclosure,
        "circulation_pct": round(circulation_pct, 2) if circulation_pct is not None else None,
        "market_cap_to_fdv_pct": round(mcap_to_fdv_pct, 2) if mcap_to_fdv_pct is not None else None,
        "volume_to_market_cap_pct": round(volume_to_mcap_pct, 2) if volume_to_mcap_pct is not None else None,
        "mexc_volume_share_pct": (
            round(mexc_volume_share_pct, 2)
            if mexc_volume_share_pct is not None
            else None
        ),
        "ath_change_pct": ath_change,
        "price_change_24h_pct": _signed_number(row.get("price_change_percentage_24h")),
        "source_url": f"https://www.coingecko.com/en/coins/{coin_id}" if coin_id else None,
        "reasons": reasons[:4],
        "risk_flags": risks[:4],
        "observed_at": int(time.time()),
        "can_authorize_trade": False,
    }


class FundamentalRateLimitError(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__(f"CoinGecko rate limit; {retry_after}s cooldown")
        self.retry_after = retry_after


class FundamentalMetricsProvider:
    """One-batch CoinGecko market-fundamental collector with cooldown."""

    def __init__(
        self,
        *,
        demo_api_key: str = "",
        pro_api_key: str = "",
        timeout: int = 15,
        cache_ttl_seconds: int = 30 * 60,
        max_assets: int = 100,
    ):
        self.timeout = max(3, int(timeout))
        self.cache_ttl_seconds = max(300, int(cache_ttl_seconds))
        self.max_assets = min(120, max(1, int(max_assets)))
        self.base_url = (
            "https://pro-api.coingecko.com/api/v3"
            if str(pro_api_key or "").strip()
            else "https://api.coingecko.com/api/v3"
        )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "SignalBot-FundamentalRadar/1.0"})
        if str(pro_api_key or "").strip():
            self.session.headers["x-cg-pro-api-key"] = str(pro_api_key).strip()
        elif str(demo_api_key or "").strip():
            self.session.headers["x-cg-demo-api-key"] = str(demo_api_key).strip()
        self._cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._next_retry_at = 0
        self._lock = threading.RLock()

    @staticmethod
    def _normalise(value: Any) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))

    def _resolve_identity(
        self,
        item: MexcListing,
        rows: list[dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, int, str]:
        exact = [
            row for row in rows
            if str(row.get("symbol") or "").upper() == item.symbol.upper()
        ]
        if not exact:
            return None, 0, "NOT_FOUND"
        if len(exact) == 1:
            return exact[0], 84, "UNIQUE_SYMBOL"
        title = self._normalise(item.title)
        title_matches: list[tuple[int, dict[str, Any]]] = []
        for row in exact:
            name = self._normalise(row.get("name"))
            coin_id = self._normalise(row.get("id"))
            if (len(name) >= 3 and name in title) or (len(coin_id) >= 4 and coin_id in title):
                title_matches.append((max(len(name), len(coin_id)), row))
        if title_matches:
            best_length = max(length for length, _ in title_matches)
            best = [row for length, row in title_matches if length == best_length]
            if len(best) == 1:
                return best[0], 96, "TITLE_AND_SYMBOL"
        return None, 0, "AMBIGUOUS"

    def _fetch_rows(self, symbols: list[str]) -> list[dict[str, Any]]:
        response = self.session.get(
            f"{self.base_url}/coins/markets",
            params={
                "vs_currency": "usd",
                "symbols": ",".join(symbol.lower() for symbol in symbols),
                "include_tokens": "all",
                "order": "market_cap_desc",
                "per_page": 250,
                "page": 1,
                "sparkline": "false",
            },
            timeout=self.timeout,
        )
        if response.status_code == 429:
            try:
                retry_after = int(float(response.headers.get("Retry-After") or 900))
            except (TypeError, ValueError):
                retry_after = 900
            raise FundamentalRateLimitError(max(60, retry_after))
        response.raise_for_status()
        payload = response.json() if response.content else []
        if not isinstance(payload, list):
            raise RuntimeError("CoinGecko market yanıtı liste değil")
        return [row for row in payload if isinstance(row, dict)]

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
                signal = score_fundamental_snapshot(
                    matched,
                    mexc_quote_volume=item.quote_volume,
                    identity_confidence=confidence,
                    matched_by=matched_by,
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
