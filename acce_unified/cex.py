"""CEX opportunity discovery derived from PriceMonitorX.

Liquidity is a safety floor rather than the ranking objective. The scorer
rewards early, liquid movement and penalises already-extended pumps.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

from .models import CexTicker, RadarCandidate


STABLE_EXACT = {
    "USDT", "USDC", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "GUSD",
    "USD1", "USDE", "USDS", "USDD", "USDX", "PYUSD", "RLUSD", "EURC",
    "EURI", "AEUR", "FRAX", "LUSD", "SUSD", "EUR", "GBP", "TRY",
}
STABLE_RE = re.compile(r"^(?:USD[A-Z0-9]*|[A-Z0-9]*USD|EUR[A-Z0-9]*|[A-Z0-9]*EUR)$")
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_stable_or_synthetic(base: str) -> bool:
    value = base.upper()
    return value in STABLE_EXACT or bool(STABLE_RE.match(value))


def is_leveraged_token(base: str, all_bases: set[str]) -> bool:
    for suffix in LEVERAGED_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix):
            if base[: -len(suffix)] in all_bases:
                return True
    return False


def opportunity_proxy(change_pct: float, quote_volume: float) -> float:
    """Cheap first-pass score retained from PriceMonitorX v2."""

    if quote_volume <= 0:
        return -1e9
    liquidity = _clamp(math.log10(max(quote_volume, 1.0)) - 5.5, 0.0, 4.0)
    if -8.0 <= change_pct < 0.0:
        momentum = 0.4 + (change_pct + 8.0) / 20.0
    elif 0.0 <= change_pct <= 18.0:
        momentum = 1.0 + change_pct / 9.0
    elif 18.0 < change_pct <= 35.0:
        momentum = 3.0 - (change_pct - 18.0) / 17.0
    elif change_pct > 35.0:
        momentum = max(-2.0, 1.0 - (change_pct - 35.0) / 15.0)
    else:
        momentum = -1.5
    return liquidity + momentum


def rank_cex_tickers(
    tickers: Iterable[CexTicker],
    *,
    trade_universe: dict[str, str],
    quote_asset: str = "USDT",
    top_n: int = 12,
    min_quote_volume: float = 500_000.0,
) -> list[RadarCandidate]:
    rows = list(tickers)
    quote = quote_asset.upper()
    all_bases = {
        item.symbol[: -len(quote)]
        for item in rows
        if item.symbol.endswith(quote) and len(item.symbol) > len(quote)
    }
    ranked: list[tuple[float, RadarCandidate]] = []
    for item in rows:
        symbol = item.symbol.upper()
        if not symbol.endswith(quote) or len(symbol) <= len(quote):
            continue
        base = symbol[: -len(quote)]
        if (
            is_stable_or_synthetic(base)
            or is_leveraged_token(base, all_bases)
            or item.quote_volume < min_quote_volume
            or item.last_price <= 0
        ):
            continue

        raw_score = opportunity_proxy(item.change_pct, item.quote_volume)
        score = int(round(_clamp(38.0 + raw_score * 9.0, 0.0, 100.0)))
        risks: list[str] = []
        reasons = [f"24s hacim ${item.quote_volume:,.0f}", f"24s değişim %{item.change_pct:+.1f}"]
        if item.change_pct > 35.0:
            risks.append("aşırı uzamış hareket; kovalama yasak")
        elif item.change_pct < -8.0:
            risks.append("sert düşüş; dönüş teyidi yok")
        if item.quote_volume < max(min_quote_volume * 3.0, 2_000_000.0):
            risks.append("ince likidite katmanı")

        if score >= 82:
            stage = "PRIORITY_WATCH"
        elif score >= 65:
            stage = "BUILDING"
        else:
            stage = "DISCOVERED"
        role = "CORE_CONFLUENCE" if symbol in trade_universe else "DISCOVERY_ONLY"
        if role == "CORE_CONFLUENCE":
            reasons.append("onaylı sermaye evreninde")
        else:
            risks.append("sermaye evreni dışında; otomatik işlem yasak")
        venue = str(item.venue or "UNKNOWN").upper()
        if venue != "MEXC":
            risks.append("yedek piyasa verisi; MEXC listelemesi doğrulanmadı")

        candidate = RadarCandidate(
            source="PRICEMONITORX_CEX",
            key=f"cex:{symbol}",
            symbol=symbol,
            score=score,
            stage=stage,
            role=role,
            execution_eligible=False,
            reasons=tuple(reasons[:4]),
            risk_flags=tuple(risks[:4]),
            metadata={
                "last_price": item.last_price,
                "change_pct": item.change_pct,
                "quote_volume": item.quote_volume,
                "venue": venue,
                "raw_opportunity_score": round(raw_score, 6),
            },
        )
        ranked.append((raw_score, candidate))

    ranked.sort(
        key=lambda row: (row[0], float(row[1].metadata.get("quote_volume", 0.0))),
        reverse=True,
    )
    return [candidate for _, candidate in ranked[: max(1, top_n)]]
