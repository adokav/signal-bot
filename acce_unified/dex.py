"""Research-only DEX phenomenon scoring derived from PhenomenonX."""

from __future__ import annotations

import time
from typing import Any

from .models import RadarCandidate


CHAINS = {"solana", "base"}
BASE_STABLES = {
    "USDT", "USDC", "DAI", "USD1", "USDE", "USDS", "PYUSD", "WETH",
    "ETH", "SOL", "WSOL", "WBTC",
}
ALLOWED_QUOTES = {"SOL", "WSOL", "USDC", "USDT", "WETH", "ETH"}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_dex_pair(
    pair: dict[str, Any],
    *,
    profile: dict[str, Any] | None = None,
    boosted: bool = False,
    takeover: bool = False,
    now_ms: float | None = None,
) -> RadarCandidate | None:
    profile = profile or {}
    now_ms = float(now_ms if now_ms is not None else time.time() * 1000)
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    symbol = str(base.get("symbol") or "").upper()
    address = str(base.get("address") or "")
    chain = str(pair.get("chainId") or "").lower()
    quote_symbol = str(quote.get("symbol") or "").upper()
    if (
        not symbol
        or not address
        or symbol in BASE_STABLES
        or chain not in CHAINS
        or quote_symbol not in ALLOWED_QUOTES
    ):
        return None

    liquidity = _number((pair.get("liquidity") or {}).get("usd"))
    txns = pair.get("txns") or {}
    h1, h6 = txns.get("h1") or {}, txns.get("h6") or {}
    buys_h1, sells_h1 = int(_number(h1.get("buys"))), int(_number(h1.get("sells")))
    buys_h6, sells_h6 = int(_number(h6.get("buys"))), int(_number(h6.get("sells")))
    volume = pair.get("volume") or {}
    changes = pair.get("priceChange") or {}
    vol_h1, vol_h6 = _number(volume.get("h1")), _number(volume.get("h6"))
    chg_h1, chg_h6 = _number(changes.get("h1")), _number(changes.get("h6"))
    age_h = max(0.0, (now_ms - _number(pair.get("pairCreatedAt"), now_ms)) / 3_600_000)
    info = pair.get("info") or {}
    socials = (
        len(info.get("socials") or [])
        + len(info.get("websites") or [])
        + len(profile.get("links") or [])
    )

    score = 0.0
    reasons: list[str] = []
    risks: list[str] = []
    if 1 <= age_h <= 72:
        score += 18
        reasons.append(f"havuz {age_h:.0f}s genç")
    elif age_h <= 336:
        score += 11
    elif age_h <= 1080:
        score += 5
    else:
        risks.append("erkenlik zayıf")
    if liquidity >= 35_000:
        score += min(18, 8 + (liquidity / 100_000) ** 0.25 * 5)
        reasons.append(f"likidite ${liquidity:,.0f}")
    else:
        risks.append("düşük likidite")

    total_h1, total_h6 = buys_h1 + sells_h1, buys_h6 + sells_h6
    buy_ratio = buys_h1 / max(sells_h1, 1)
    txn_accel = total_h1 / max(total_h6 / 6, 1)
    vol_accel = vol_h1 / max(vol_h6 / 6, 1)
    if total_h1 >= 24:
        score += min(15, 5 + total_h1 ** 0.25 * 3)
    if buy_ratio >= 1.25:
        score += min(12, 5 + (buy_ratio - 1.25) * 5)
        reasons.append(f"al/sat {buy_ratio:.1f}x")
    elif buy_ratio < 0.75:
        score -= 8
        risks.append("satış baskısı")
    if txn_accel >= 1.35:
        score += min(12, 5 + (txn_accel - 1.35) * 5)
        reasons.append(f"işlem ivmesi {txn_accel:.1f}x")
    if vol_h1 >= 10_000 and vol_accel >= 1.25:
        score += min(13, 5 + (vol_accel - 1.25) * 4)
        reasons.append(f"hacim ivmesi {vol_accel:.1f}x")
    score += min(8, socials * 2)
    if takeover:
        score += 5
        reasons.append("community takeover")
    if boosted:
        score += 2
        risks.append("ücretli boost olabilir")
    if chg_h1 > 80 or chg_h6 > 250:
        score -= 18
        risks.append("aşırı uzamış")
    elif -10 <= chg_h1 <= 35 and -20 <= chg_h6 <= 120:
        score += 6

    score_i = max(0, min(100, round(score)))
    stage = (
        "SPREADING" if score_i >= 82 and chg_h6 < 180
        else "BUILDING" if score_i >= 68
        else "DISCOVERED" if score_i >= 55
        else "WEAK"
    )
    # DexScreener does not prove mint/freeze authority, holder concentration,
    # honeypot behaviour, tax or LP lock. Missing those checks is a hard gate.
    risks.append("kontrat/holder/LP güvenliği doğrulanmadı")
    return RadarCandidate(
        source="PHENOMENONX_DEX",
        key=f"dex:{chain}:{address}",
        symbol=symbol[:20],
        score=score_i,
        stage=stage,
        role="RESEARCH_ONLY",
        execution_eligible=False,
        reasons=tuple(reasons[:5]),
        risk_flags=tuple(risks[:5]),
        metadata={
            "chain": chain,
            "address": address,
            "url": str(pair.get("url") or profile.get("url") or ""),
            "age_h": round(age_h, 3),
            "liquidity": liquidity,
            "vol_h1": vol_h1,
            "vol_h6": vol_h6,
            "h1_transactions": total_h1,
            "buy_sell_ratio": round(buy_ratio, 4),
            "change_h1": chg_h1,
            "change_h6": chg_h6,
        },
    )


def rank_dex_pairs(
    pairs: list[dict[str, Any]],
    *,
    profiles: dict[tuple[str, str], dict[str, Any]] | None = None,
    boosted: set[tuple[str, str]] | None = None,
    takeovers: set[tuple[str, str]] | None = None,
    top_n: int = 8,
    min_liquidity: float = 35_000.0,
    min_h1_transactions: int = 24,
    max_age_hours: int = 45 * 24,
    now_ms: float | None = None,
) -> list[RadarCandidate]:
    profiles = profiles or {}
    boosted = boosted or set()
    takeovers = takeovers or set()
    candidates: list[RadarCandidate] = []
    for pair in pairs:
        base = pair.get("baseToken") or {}
        key = (str(pair.get("chainId") or "").lower(), str(base.get("address") or ""))
        candidate = score_dex_pair(
            pair,
            profile=profiles.get(key),
            boosted=key in boosted,
            takeover=key in takeovers,
            now_ms=now_ms,
        )
        if candidate is None:
            continue
        meta = candidate.metadata
        if (
            float(meta.get("liquidity", 0.0)) < min_liquidity
            or int(meta.get("h1_transactions", 0)) < min_h1_transactions
            or float(meta.get("age_h", max_age_hours + 1)) > max_age_hours
            or candidate.score < 55
        ):
            continue
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (item.score, float(item.metadata.get("liquidity", 0.0))),
        reverse=True,
    )
    return candidates[: max(1, top_n)]
