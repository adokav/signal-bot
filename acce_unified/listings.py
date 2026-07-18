"""PhenomenonX MEXC new-listing scoring.

The listing radar is discovery-only.  It can surface a token and explain why
it deserves attention, but it cannot add that token to the executable capital
universe or authorize an order.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable

from .models import MexcListing, RadarCandidate


STABLES = {
    "USDT", "USDC", "DAI", "USD1", "USDE", "USDS", "PYUSD", "EURC",
    "TUSD", "FDUSD",
}
NON_SYMBOLS = {
    "MEXC", "NEW", "SPOT", "FUTURES", "FUTURE", "INNOVATION", "ZONE",
    "KICKSTARTER", "THE", "WILL", "LIST", "LISTING", "UTC", "GMT",
}
LEVERAGED_SUFFIXES = ("3L", "3S", "4L", "4S", "5L", "5S", "BULL", "BEAR")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def extract_listing_titles(page_text: str, *, limit: int = 80) -> list[str]:
    """Extract likely listing headlines from MEXC announcement HTML/JSON."""
    raw_titles: list[str] = []
    patterns = (
        r'"title"\s*:\s*"((?:\\.|[^"\\])*)"',
        r"<h[1-4][^>]*>(.*?)</h[1-4]>",
        r'<a[^>]+href="[^"]*announcements/article/[^"]+"[^>]*>(.*?)</a>',
    )
    listing_words = re.compile(
        r"\b(?:will\s+list|to\s+list|lists?|listing)\b|now\s+live|first\s+in\s+market",
        flags=re.I,
    )
    for pattern in patterns:
        for match in re.findall(pattern, page_text or "", flags=re.I | re.S):
            value = re.sub(r"<[^>]+>", " ", match)
            try:
                value = json.loads(f'"{value}"')
            except Exception:
                value = value.replace("\\u0026", "&").replace("\\/", "/")
            value = html.unescape(re.sub(r"\s+", " ", value)).strip()
            if value and listing_words.search(value):
                raw_titles.append(value)

    unique: list[str] = []
    seen: set[str] = set()
    for title in raw_titles:
        key = title.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(title)
    return unique[: max(1, limit)]


def extract_symbols_from_title(title: str) -> list[str]:
    """Extract base symbols while rejecting stable and leveraged products."""
    upper = str(title or "").upper()
    found = re.findall(r"\(([A-Z0-9]{2,20})\)", upper)
    found.extend(re.findall(r"\b([A-Z0-9]{2,20})USDT\b", upper))
    found.extend(re.findall(r"FIRST\s+IN\s+MARKET\s*:\s*([A-Z0-9]{2,20})\b", upper))
    if not found:
        found.extend(re.findall(r"\b(?:WILL\s+LIST|TO\s+LIST|LISTS?)\s+([A-Z0-9]{2,20})\b", upper))

    result: list[str] = []
    for symbol in found:
        if (
            symbol in STABLES
            or symbol in NON_SYMBOLS
            or symbol.endswith(LEVERAGED_SUFFIXES)
        ):
            continue
        if symbol not in result:
            result.append(symbol)
    return result


def score_mexc_listing(
    item: MexcListing,
    *,
    trade_universe: dict[str, str],
) -> RadarCandidate:
    """Score a confirmed/recent MEXC listing without granting trade authority."""
    pair = item.pair.upper()
    # A rank-1 official announcement must be visible even before Spot opens or
    # when a hosted region cannot reach api.mexc.com. Market confirmation then
    # promotes it from WATCH instead of being required for initial discovery.
    score = 32.0 + max(0.0, 22.0 - max(0, item.rank) * 1.2)
    reasons: list[str] = []
    risks: list[str] = []

    if item.discovery_source == "EXCHANGE_DIFF":
        reasons.append("MEXC Spot API'de yeni görüldü")
    else:
        reasons.append("MEXC New Listings akışında")

    spot_status = str(item.spot_status or "UNKNOWN").upper()
    if spot_status == "OPEN":
        score += 15
        reasons.append("spot işlem açık")
    elif spot_status == "NOT_OPEN":
        risks.append("spot henüz açık değil / yalnız futures olabilir")
    else:
        risks.append("MEXC Spot API teyidi erişilemedi; işlem durumu bilinmiyor")

    if item.quote_volume >= 10_000_000:
        score += 18
        reasons.append("24s hacim > $10M")
    elif item.quote_volume >= 2_000_000:
        score += 13
        reasons.append("24s hacim > $2M")
    elif item.quote_volume >= 500_000:
        score += 7
    elif spot_status == "OPEN":
        risks.append("hacim henüz zayıf")

    if item.volume_acceleration >= 2.0:
        score += 15
        reasons.append(f"tamamlanmış saat hacim ivmesi {item.volume_acceleration:.1f}x")
    elif item.volume_acceleration >= 1.25:
        score += 9
        reasons.append(f"hacim ivmesi {item.volume_acceleration:.1f}x")

    # Missing ticker data is represented by zeroes, so price behaviour may
    # contribute only after a real market price has been confirmed.
    if item.last_price > 0:
        if -12 <= item.change_pct <= 35:
            score += 8
        elif item.change_pct > 80:
            score -= 18
            risks.append("ilk pump aşırı uzamış; kovalama yasak")
        elif item.change_pct < -35:
            score -= 12
            risks.append("listeleme sonrası ağır satış")

    score_i = int(round(_clamp(score, 0, 100)))
    if item.change_pct > 80:
        stage = "CROWDED"
    elif score_i >= 82 and item.change_pct <= 60:
        stage = "HOT"
    elif score_i >= 68:
        stage = "BUILDING"
    elif score_i >= 52:
        stage = "WATCH"
    else:
        stage = "WEAK"

    role = "CORE_CONFLUENCE" if pair in trade_universe else "DISCOVERY_ONLY"
    if role == "CORE_CONFLUENCE":
        reasons.append("onaylı sermaye evreninde")
    else:
        risks.append("sermaye evreni dışında; otomatik işlem yasak")

    return RadarCandidate(
        source="PHENOMENONX_MEXC_NEW_LISTING",
        key=f"listing:{pair}",
        symbol=pair,
        score=score_i,
        stage=stage,
        role=role,
        execution_eligible=False,
        reasons=tuple(reasons[:5]),
        risk_flags=tuple(risks[:4]),
        metadata={
            "base_symbol": item.symbol,
            "title": item.title,
            "announcement_rank": item.rank,
            "spot_status": spot_status,
            "spot_enabled": spot_status == "OPEN",
            "last_price": item.last_price,
            "change_pct": item.change_pct,
            "quote_volume": item.quote_volume,
            "volume_acceleration": item.volume_acceleration,
            "discovery_source": item.discovery_source,
        },
    )


def rank_mexc_listings(
    listings: Iterable[MexcListing],
    *,
    trade_universe: dict[str, str],
    top_n: int = 5,
    min_score: int = 52,
) -> list[RadarCandidate]:
    candidates = [
        score_mexc_listing(item, trade_universe=trade_universe)
        for item in listings
    ]
    eligible = [
        item for item in candidates
        if item.score >= min_score and item.stage != "CROWDED"
    ]
    eligible.sort(
        key=lambda item: (
            item.score,
            float(item.metadata.get("quote_volume", 0.0)),
            -int(item.metadata.get("announcement_rank", 0)),
        ),
        reverse=True,
    )
    return eligible[: max(1, top_n)]
