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
from dataclasses import replace

from .fundamentals import score_fundamental_snapshot
from .models import MexcListing, RadarCandidate
from .social import score_social_snapshot


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
    elif item.discovery_source == "MANUAL_WATCH":
        reasons.append("manuel MEXC takibi")
    else:
        reasons.append("MEXC New Listings akışında")
    if item.manually_watched and item.discovery_source != "MANUAL_WATCH":
        reasons.append("manuel takip listesinde")

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
        reasons.append(f"tamamlanmış 5dk hacim ivmesi {item.volume_acceleration:.1f}x")
    elif item.volume_acceleration >= 1.25:
        score += 9
        reasons.append(f"5dk hacim ivmesi {item.volume_acceleration:.1f}x")

    if 0 < item.spread_bps <= 35:
        score += 6
        reasons.append(f"alış-satış makası dar ({item.spread_bps:.0f} bps)")
    elif item.spread_bps > 150:
        score -= 12
        risks.append(f"alış-satış makası çok geniş ({item.spread_bps:.0f} bps)")
    elif item.spread_bps > 80:
        score -= 6
        risks.append(f"alış-satış makası geniş ({item.spread_bps:.0f} bps)")

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

    social = (
        dict(item.social_data)
        if item.social_data
        else score_social_snapshot(None)
    )
    if social.get("stage") in {"SUSPICIOUS", "NEGATIVE_EVENT"}:
        risks.append(
            "sosyal ilgi riskli: "
            + str(social.get("stage") or "UNKNOWN").lower()
        )

    fundamentals = (
        dict(item.fundamental_data)
        if item.fundamental_data
        else score_fundamental_snapshot(None)
    )
    if fundamentals.get("status") == "READY" and fundamentals.get("stage") == "HIGH_RISK":
        risks.append("temel metriklerde yüksek risk")

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
            "spread_bps": item.spread_bps,
            "discovery_source": item.discovery_source,
            "first_seen_at": item.first_seen_at,
            "manually_watched": item.manually_watched,
            "social": social,
            "fundamentals": fundamentals,
        },
    )


def partition_mexc_listings(
    listings: Iterable[MexcListing],
    *,
    trade_universe: dict[str, str],
    top_n: int = 5,
    min_score: int = 52,
) -> tuple[list[RadarCandidate], list[RadarCandidate]]:
    """Return both threshold-pass and rejected candidates with explanations."""
    passed: list[RadarCandidate] = []
    filtered: list[RadarCandidate] = []
    for raw_candidate in (
        score_mexc_listing(item, trade_universe=trade_universe)
        for item in listings
    ):
        filter_reasons: list[str] = []
        if raw_candidate.stage == "CROWDED":
            filter_reasons.append("ilk hareket aşırı uzamış")
        if raw_candidate.score < min_score:
            filter_reasons.append(
                f"puan eşiğine {min_score - raw_candidate.score} puan eksik"
            )
        accepted = not filter_reasons
        metadata = dict(raw_candidate.metadata)
        metadata.update(
            {
                "filter_status": "PASSED" if accepted else "FILTERED",
                "filter_reasons": filter_reasons,
                "score_threshold": min_score,
                "score_gap": max(0, min_score - raw_candidate.score),
            }
        )
        candidate = replace(raw_candidate, metadata=metadata)
        (passed if accepted else filtered).append(candidate)

    def sort_key(item: RadarCandidate) -> tuple[float, float, int]:
        return (
            float(item.score),
            float(item.metadata.get("quote_volume", 0.0)),
            -int(item.metadata.get("announcement_rank", 0)),
        )

    passed.sort(key=sort_key, reverse=True)
    filtered.sort(key=sort_key, reverse=True)
    return passed[: max(1, top_n)], filtered


def rank_mexc_listings(
    listings: Iterable[MexcListing],
    *,
    trade_universe: dict[str, str],
    top_n: int = 5,
    min_score: int = 52,
) -> list[RadarCandidate]:
    passed, _ = partition_mexc_listings(
        listings,
        trade_universe=trade_universe,
        top_n=top_n,
        min_score=min_score,
    )
    return passed
