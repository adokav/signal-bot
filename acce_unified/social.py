"""Social-attention intelligence for MEXC discovery candidates.

Attention is deliberately kept separate from market and fundamental quality.
The module can raise observation priority, explain why an asset is trending,
and identify suspicious/crowded attention, but it cannot authorize an order.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from .models import MexcListing


log = logging.getLogger(__name__)

_POSITIVE_WORDS = {
    "adoption", "airdrop", "breakout", "bullish", "launch", "mainnet",
    "partnership", "release", "shipping", "upgrade", "viral",
}
_NEGATIVE_WORDS = {
    "attack", "delist", "dump", "exploit", "hack", "lawsuit", "rug",
    "scam", "selloff", "vulnerability",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _sentiment_from_texts(texts: Iterable[str]) -> tuple[int, int, int]:
    positive = negative = 0
    for text in texts:
        words = set(re.findall(r"[a-z]+", str(text or "").lower()))
        positive += len(words & _POSITIVE_WORDS)
        negative += len(words & _NEGATIVE_WORDS)
    total = positive + negative
    score = int(round(100 * (positive - negative) / total)) if total else 0
    return score, positive, negative


def score_social_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    price_change_pct: float = 0.0,
    volume_acceleration: float = 0.0,
) -> dict[str, Any]:
    """Return independent attention, authenticity and crowding signals.

    Missing social data stays explicitly ``DATA_PENDING`` instead of becoming
    a zero-quality verdict. Thresholds are policy defaults intended for later
    cohort backtesting, not trade-entry rules.
    """
    data = dict(snapshot or {})
    mentions = max(0.0, _number(data.get("mentions_1h")))
    previous_mentions = max(0.0, _number(data.get("previous_mentions_1h")))
    unique_authors = max(0.0, _number(data.get("unique_authors_1h")))
    previous_authors = max(0.0, _number(data.get("previous_unique_authors_1h")))
    engagements = max(0.0, _number(data.get("engagements_1h")))
    credible_authors = max(0.0, _number(data.get("credible_authors_1h")))
    duplicate_ratio = _clamp(_number(data.get("duplicate_ratio")), 0.0, 1.0)
    new_account_ratio = _clamp(_number(data.get("new_account_ratio")), 0.0, 1.0)
    author_concentration = _clamp(
        _number(data.get("author_concentration")), 0.0, 1.0
    )
    platforms = sorted({str(value).upper() for value in data.get("platforms", []) if value})
    news_count = max(0.0, _number(data.get("news_count")))
    active_buckets = max(0.0, _number(data.get("active_buckets")))
    coverage = int(_clamp(_number(data.get("coverage_pct"))))

    if coverage <= 0 and not (mentions or news_count or platforms):
        return {
            "status": "DATA_PENDING",
            "stage": "DATA_PENDING",
            "attention_score": 0,
            "viral_potential": 0,
            "sentiment_score": 0,
            "manipulation_risk": 0,
            "crowding_risk": 0,
            "coverage_pct": 0,
            "platforms": [],
            "reasons": ["sosyal veri sağlayıcısı henüz teyit üretmedi"],
            "sources": [],
            "narrative": None,
            "can_authorize_trade": False,
        }

    mention_velocity = (mentions + 1.0) / (previous_mentions + 1.0)
    author_velocity = (unique_authors + 1.0) / (previous_authors + 1.0)
    engagement_per_post = engagements / max(mentions, 1.0)
    unique_ratio = unique_authors / max(mentions, 1.0)
    credible_ratio = credible_authors / max(unique_authors, 1.0)

    attention = (
        _clamp((mention_velocity - 1.0) * 22.0, 0.0, 25.0)
        + _clamp((author_velocity - 1.0) * 18.0, 0.0, 20.0)
        + _clamp(engagement_per_post / 8.0 * 15.0, 0.0, 15.0)
        + _clamp(len(platforms) / 3.0 * 10.0, 0.0, 10.0)
        + _clamp(credible_ratio * 20.0, 0.0, 10.0)
        + _clamp(unique_ratio * 10.0, 0.0, 10.0)
        + _clamp(active_buckets / 6.0 * 10.0, 0.0, 10.0)
    )
    manipulation = _clamp(
        duplicate_ratio * 55.0
        + new_account_ratio * 25.0
        + author_concentration * 20.0
    )
    crowding = _clamp(
        max(0.0, _number(price_change_pct) - 15.0) * 0.9
        + max(0.0, attention - 85.0) * 0.7
        + max(0.0, _number(volume_acceleration) - 5.0) * 4.0
    )
    viral = _clamp(attention - manipulation * 0.45 - crowding * 0.25)

    supplied_sentiment = data.get("sentiment_score")
    sentiment = int(round(_clamp(_number(supplied_sentiment), -100.0, 100.0)))
    if supplied_sentiment is None:
        sentiment, _, _ = _sentiment_from_texts(data.get("texts") or [])

    if manipulation >= 60:
        stage = "SUSPICIOUS"
    elif sentiment <= -35 and attention >= 50:
        stage = "NEGATIVE_EVENT"
    elif crowding >= 60:
        stage = "CROWDED"
    elif viral >= 70 and _number(volume_acceleration) >= 1.5:
        stage = "CONFIRMED"
    elif viral >= 55:
        stage = "EMERGING"
    elif viral >= 30:
        stage = "SEED"
    else:
        stage = "QUIET"

    reasons: list[str] = []
    if mention_velocity >= 2:
        reasons.append(f"1s mention ivmesi {mention_velocity:.1f}x")
    if author_velocity >= 1.5:
        reasons.append(f"benzersiz yazar ivmesi {author_velocity:.1f}x")
    if len(platforms) >= 2:
        reasons.append(f"{len(platforms)} bağımsız kanal")
    if news_count:
        reasons.append(f"{int(news_count)} güncel haber")
    if manipulation >= 40:
        reasons.append("eşzamanlı/kopya paylaşım riski")
    if crowding >= 40:
        reasons.append("ilgi fiyat hareketini geç takip ediyor olabilir")
    if not reasons:
        reasons.append("erken sosyal taban çizgisi oluşturuluyor")

    return {
        "status": "READY",
        "stage": stage,
        "attention_score": int(round(attention)),
        "viral_potential": int(round(viral)),
        "sentiment_score": sentiment,
        "manipulation_risk": int(round(manipulation)),
        "crowding_risk": int(round(crowding)),
        "coverage_pct": coverage,
        "mentions_1h": int(mentions),
        "mention_velocity": round(mention_velocity, 2),
        "unique_authors_1h": int(unique_authors),
        "author_velocity": round(author_velocity, 2),
        "platforms": platforms,
        "reasons": reasons[:4],
        "sources": list(data.get("sources") or [])[:6],
        "narrative": data.get("narrative"),
        "observed_at": int(data.get("observed_at") or time.time()),
        "can_authorize_trade": False,
    }


class SocialIntelligenceProvider:
    """Optional X plus keyless GDELT collector with bounded cached scans."""

    X_RECENT_SEARCH = "https://api.x.com/2/tweets/search/recent"
    GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(
        self,
        *,
        x_bearer_token: str = "",
        gdelt_enabled: bool = True,
        timeout: int = 12,
        cache_ttl_seconds: int = 10 * 60,
        max_assets: int = 6,
    ):
        self.x_bearer_token = str(x_bearer_token or "").strip()
        self.gdelt_enabled = bool(gdelt_enabled)
        self.timeout = max(3, int(timeout))
        self.cache_ttl_seconds = max(60, int(cache_ttl_seconds))
        self.max_assets = max(1, int(max_assets))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "SignalBot-SocialRadar/1.0"})
        self._cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _normalise_text(value: Any) -> str:
        text = re.sub(r"https?://\S+", "", str(value or "").lower())
        return re.sub(r"\W+", " ", text).strip()

    @staticmethod
    def _query(item: MexcListing) -> str:
        symbol = re.sub(r"[^A-Z0-9]", "", item.symbol.upper())
        return f'(${symbol} OR "{symbol} token" OR "{symbol} coin") -is:retweet'

    def _fetch_x(self, item: MexcListing) -> dict[str, Any]:
        if not self.x_bearer_token:
            return {}
        response = self.session.get(
            self.X_RECENT_SEARCH,
            headers={"Authorization": f"Bearer {self.x_bearer_token}"},
            params={
                "query": self._query(item),
                "max_results": 100,
                "tweet.fields": "created_at,public_metrics,author_id,entities",
                "expansions": "author_id",
                "user.fields": "created_at,public_metrics,verified",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        posts = payload.get("data") or []
        users = {
            str(row.get("id")): row
            for row in (payload.get("includes") or {}).get("users", [])
            if isinstance(row, dict)
        }
        now = datetime.now(timezone.utc)
        current_posts: list[dict] = []
        previous_posts: list[dict] = []
        active_buckets: set[int] = set()
        for row in posts:
            if not isinstance(row, dict):
                continue
            try:
                created = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
                age_s = max(0.0, (now - created).total_seconds())
            except (TypeError, ValueError):
                age_s = 0.0
            if age_s <= 3600:
                current_posts.append(row)
            elif age_s <= 7200:
                previous_posts.append(row)
            if age_s <= 6 * 3600:
                active_buckets.add(int(age_s // 3600))

        current_authors = [str(row.get("author_id") or "") for row in current_posts]
        previous_authors = [str(row.get("author_id") or "") for row in previous_posts]
        texts = [str(row.get("text") or "") for row in current_posts]
        normalised = [self._normalise_text(text) for text in texts]
        duplicate_count = sum(count - 1 for count in Counter(normalised).values() if count > 1)
        author_counts = Counter(current_authors)
        engagements = 0.0
        for row in current_posts:
            metrics = row.get("public_metrics") or {}
            engagements += (
                _number(metrics.get("like_count"))
                + 3 * _number(metrics.get("retweet_count"))
                + 2 * _number(metrics.get("reply_count"))
                + 2 * _number(metrics.get("quote_count"))
            )
        new_accounts = credible = 0
        for author_id in set(current_authors):
            user = users.get(author_id) or {}
            followers = _number((user.get("public_metrics") or {}).get("followers_count"))
            if bool(user.get("verified")) or followers >= 10_000:
                credible += 1
            try:
                created = datetime.fromisoformat(str(user.get("created_at")).replace("Z", "+00:00"))
                if (now - created).days <= 30:
                    new_accounts += 1
            except (TypeError, ValueError):
                pass
        sentiment, _, _ = _sentiment_from_texts(texts)
        hashtags = Counter(
            str(tag.get("tag") or "").lower()
            for row in current_posts
            for tag in (row.get("entities") or {}).get("hashtags", [])
            if tag.get("tag")
        )
        narrative = ", ".join(tag for tag, _ in hashtags.most_common(3)) or None
        unique_current = len(set(current_authors))
        return {
            "mentions_1h": len(current_posts),
            "previous_mentions_1h": len(previous_posts),
            "unique_authors_1h": unique_current,
            "previous_unique_authors_1h": len(set(previous_authors)),
            "engagements_1h": engagements,
            "credible_authors_1h": credible,
            "duplicate_ratio": duplicate_count / max(len(current_posts), 1),
            "new_account_ratio": new_accounts / max(unique_current, 1),
            "author_concentration": max(author_counts.values(), default=0) / max(len(current_posts), 1),
            "active_buckets": len(active_buckets),
            "sentiment_score": sentiment,
            "texts": texts,
            "platforms": ["X"] if posts else [],
            "sources": [
                {"title": text[:100], "url": f"https://x.com/i/web/status/{row.get('id')}", "provider": "X"}
                for row, text in zip(current_posts[:4], texts[:4])
            ],
            "narrative": narrative,
            "coverage_pct": 70 if posts else 25,
        }

    def _fetch_gdelt(self, item: MexcListing) -> dict[str, Any]:
        if not self.gdelt_enabled:
            return {}
        symbol = re.sub(r"[^A-Z0-9]", "", item.symbol.upper())
        response = self.session.get(
            self.GDELT_DOC,
            params={
                "query": f'"{symbol}" (crypto OR token OR blockchain)',
                "mode": "ArtList",
                "format": "json",
                "maxrecords": 25,
                "timespan": "24h",
                "sort": "DateDesc",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        articles = [row for row in payload.get("articles", []) if isinstance(row, dict)]
        titles = [str(row.get("title") or "") for row in articles if row.get("title")]
        return {
            "news_count": len(articles),
            "texts": titles,
            "platforms": ["NEWS"] if articles else [],
            "sources": [
                {"title": str(row.get("title") or "")[:100], "url": row.get("url"), "provider": "GDELT"}
                for row in articles[:4]
                if row.get("url")
            ],
            "coverage_pct": 30 if articles else 10,
        }

    @staticmethod
    def _merge(parts: Iterable[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "platforms": [], "sources": [], "texts": [], "coverage_pct": 0,
        }
        for part in parts:
            if not part:
                continue
            for key, value in part.items():
                if key in {"platforms", "sources", "texts"}:
                    result[key].extend(value or [])
                elif key == "coverage_pct":
                    result[key] = min(100, int(result[key]) + int(value or 0))
                elif key in result and isinstance(value, (int, float)):
                    result[key] = _number(result[key]) + _number(value)
                elif value is not None:
                    result[key] = value
        result["platforms"] = sorted(set(result["platforms"]))
        result["observed_at"] = int(time.time())
        return result

    def _fetch_one(self, item: MexcListing) -> dict[str, Any]:
        try:
            x_data = self._fetch_x(item)
        except Exception as exc:
            log.warning("X sosyal tarama başarısız (%s): %s", item.pair, exc)
            x_data = {}
        try:
            news_data = self._fetch_gdelt(item)
        except Exception as exc:
            log.warning("GDELT coin taraması başarısız (%s): %s", item.pair, exc)
            news_data = {}
        return score_social_snapshot(
            self._merge((x_data, news_data)),
            price_change_pct=item.change_pct,
            volume_acceleration=item.volume_acceleration,
        )

    def fetch_many(self, listings: Iterable[MexcListing]) -> dict[str, dict[str, Any]]:
        ordered = sorted(
            listings,
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
        if pending:
            with ThreadPoolExecutor(max_workers=min(4, len(pending))) as executor:
                futures = {executor.submit(self._fetch_one, item): item for item in pending}
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        signal = future.result()
                    except Exception as exc:
                        log.warning("Sosyal radar başarısız (%s): %s", item.pair, exc)
                        signal = score_social_snapshot(None)
                    result[item.pair] = signal
                    with self._lock:
                        self._cache[item.pair] = (now, dict(signal))
        return result
