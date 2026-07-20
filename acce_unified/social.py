"""Community-opinion intelligence for MEXC discovery candidates.

News coverage and community opinion are deliberately different evidence types.
GDELT can add context and source links, but only authenticated community
providers (X and/or Reddit) can satisfy the community validation gate.  The
gate may suppress a Telegram opportunity alert; it can never authorize an
order or add an asset to the executable universe.
"""

from __future__ import annotations

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
    "accumulate", "adoption", "airdrop", "breakout", "bullish", "buying",
    "community", "gem", "launch", "legit", "mainnet", "partnership",
    "promising", "release", "roadmap", "shipping", "strong", "undervalued",
    "upgrade", "utility", "viral", "alım", "biriktir", "güçlü", "lansman",
    "ortaklık", "potansiyel", "topluluk",
}
_NEGATIVE_WORDS = {
    "attack", "avoid", "bearish", "bot", "delist", "dump", "exploit",
    "fake", "hack", "honeypot", "inflation", "insider", "lawsuit", "ponzi",
    "rug", "rugpull", "scam", "sell", "selloff", "unlock", "vulnerability",
    "açılım", "dolandırıcılık", "manipülasyon", "sahte", "satış", "şüpheli",
    "uzak",
}
_THEME_WORDS = {
    "CATALYST": {"airdrop", "launch", "listing", "mainnet", "partnership", "release", "upgrade"},
    "UTILITY": {"adoption", "product", "protocol", "roadmap", "shipping", "utility"},
    "COMMUNITY": {"community", "holders", "organic", "topluluk", "viral"},
    "DILUTION": {"airdrop", "inflation", "supply", "unlock", "vesting", "açılım"},
    "SECURITY": {"attack", "exploit", "hack", "honeypot", "rug", "scam"},
    "SPECULATION": {"breakout", "buying", "dump", "gem", "moon", "pump", "sell"},
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _words(value: Any) -> set[str]:
    return set(re.findall(r"[^\W\d_]+", str(value or "").lower(), flags=re.UNICODE))


def _sentiment_from_texts(texts: Iterable[str]) -> tuple[int, int, int]:
    positive = negative = 0
    for text in texts:
        words = _words(text)
        positive += len(words & _POSITIVE_WORDS)
        negative += len(words & _NEGATIVE_WORDS)
    total = positive + negative
    score = int(round(100 * (positive - negative) / total)) if total else 0
    return score, positive, negative


def _opinion_breakdown(texts: Iterable[str]) -> dict[str, Any]:
    bullish = bearish = neutral = 0
    themes: Counter[str] = Counter()
    for text in texts:
        words = _words(text)
        positive = len(words & _POSITIVE_WORDS)
        negative = len(words & _NEGATIVE_WORDS)
        if positive > negative:
            bullish += 1
        elif negative > positive:
            bearish += 1
        else:
            neutral += 1
        for name, vocabulary in _THEME_WORDS.items():
            hits = len(words & vocabulary)
            if hits:
                themes[name] += hits
    total = bullish + bearish + neutral
    directional = bullish + bearish
    return {
        "opinion_count": total,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "bullish_pct": int(round(100 * bullish / directional)) if directional else 0,
        "bearish_pct": int(round(100 * bearish / directional)) if directional else 0,
        "themes": [name for name, _ in themes.most_common(3)],
    }


def _non_ready_social(
    *,
    status: str,
    reason: str,
    data: dict[str, Any],
    mentions: int,
    unique_authors: int,
    community_gate: str,
    sentiment: int | None = None,
) -> dict[str, Any]:
    community_platforms = sorted({
        str(value).upper() for value in data.get("community_platforms", []) if value
    })
    configured = sorted({
        str(value).upper()
        for value in data.get("configured_community_platforms", [])
        if value
    })
    reached = sorted({
        str(value).upper()
        for value in data.get("reached_community_platforms", [])
        if value
    })
    breakdown = _opinion_breakdown(data.get("community_texts") or [])
    return {
        "status": status,
        "stage": "INSUFFICIENT_DATA" if status == "INSUFFICIENT_DATA" else "DATA_PENDING",
        "community_gate": community_gate,
        "gate_reasons": [reason],
        "attention_score": None,
        "viral_potential": None,
        "sentiment_score": sentiment,
        "manipulation_risk": None,
        "crowding_risk": None,
        "coverage_pct": int(_clamp(_number(data.get("coverage_pct")))),
        "community_coverage_pct": int(
            _clamp(_number(data.get("community_coverage_pct")))
        ),
        "news_coverage_pct": int(_clamp(_number(data.get("news_coverage_pct")))),
        "mentions_window": mentions,
        "unique_authors_window": unique_authors,
        "window_hours": int(max(1, _number(data.get("window_hours"), 6))),
        "community_platforms": community_platforms,
        "configured_community_platforms": configured,
        "reached_community_platforms": reached,
        "platforms": sorted(set(community_platforms) | set(data.get("news_platforms") or [])),
        "news_count": int(max(0, _number(data.get("news_count")))),
        "reasons": [reason],
        "sources": list(data.get("sources") or [])[:6],
        "provider_errors": list(data.get("provider_errors") or [])[:3],
        "narrative": None,
        **breakdown,
        "observed_at": int(data.get("observed_at") or time.time()),
        "can_authorize_trade": False,
    }


def score_social_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    price_change_pct: float = 0.0,
    volume_acceleration: float = 0.0,
    min_mentions: int = 4,
    min_unique_authors: int = 3,
    min_community_platforms: int = 1,
    min_sentiment: int = -10,
) -> dict[str, Any]:
    """Score community evidence and return an explicit alert gate.

    GDELT/news data is contextual only.  It cannot turn missing community
    evidence into ``QUIET`` or satisfy ``community_gate=PASS``.
    """
    data = dict(snapshot or {})
    window_hours = int(max(1, _number(data.get("window_hours"), 6)))
    community_texts = [str(value) for value in data.get("community_texts", []) if value]
    current_authors = [str(value) for value in data.get("current_authors", []) if value]
    previous_authors = [str(value) for value in data.get("previous_authors", []) if value]
    mentions = int(max(
        0,
        _number(data.get("mentions_window"), _number(data.get("mentions_1h"))),
        len(community_texts),
        len(current_authors),
    ))
    previous_mentions = max(
        0.0,
        _number(data.get("previous_mentions_window"), _number(data.get("previous_mentions_1h"))),
    )
    unique_authors = int(max(
        0,
        _number(data.get("unique_authors_window"), _number(data.get("unique_authors_1h"))),
        len(set(current_authors)),
    ))
    previous_unique_authors = max(
        0.0,
        _number(
            data.get("previous_unique_authors_window"),
            _number(data.get("previous_unique_authors_1h")),
        ),
        float(len(set(previous_authors))),
    )
    community_platforms = sorted({
        str(value).upper() for value in data.get("community_platforms", []) if value
    })
    legacy_platforms = {
        str(value).upper() for value in data.get("platforms", []) if value
    }
    if not community_platforms:
        community_platforms = sorted(legacy_platforms & {"X", "REDDIT", "TELEGRAM"})
    configured = sorted({
        str(value).upper()
        for value in data.get("configured_community_platforms", [])
        if value
    })
    reached = sorted({
        str(value).upper()
        for value in data.get("reached_community_platforms", [])
        if value
    })
    if community_platforms and not configured:
        configured = list(community_platforms)
    if community_platforms and not reached:
        reached = list(community_platforms)
    data.update({
        "window_hours": window_hours,
        "community_texts": community_texts,
        "community_platforms": community_platforms,
        "configured_community_platforms": configured,
        "reached_community_platforms": reached,
    })

    supplied_sentiment = data.get("sentiment_score")
    sentiment = (
        int(round(_clamp(_number(supplied_sentiment), -100.0, 100.0)))
        if supplied_sentiment is not None
        else _sentiment_from_texts(community_texts)[0]
    )
    if not configured and not community_platforms:
        return _non_ready_social(
            status="COMMUNITY_SOURCE_MISSING",
            reason="X/Reddit topluluk kaynağı bağlı değil",
            data=data,
            mentions=mentions,
            unique_authors=unique_authors,
            community_gate="UNAVAILABLE",
            sentiment=None,
        )
    if not reached and not community_platforms:
        return _non_ready_social(
            status="PROVIDER_UNAVAILABLE",
            reason="bağlı topluluk sağlayıcılarına ulaşılamadı",
            data=data,
            mentions=mentions,
            unique_authors=unique_authors,
            community_gate="UNAVAILABLE",
            sentiment=None,
        )
    if (
        mentions < max(1, int(min_mentions))
        or unique_authors < max(1, int(min_unique_authors))
        or len(community_platforms) < max(1, int(min_community_platforms))
    ):
        return _non_ready_social(
            status="INSUFFICIENT_DATA",
            reason=(
                f"topluluk kanıtı yetersiz: {mentions}/{max(1, int(min_mentions))} görüş, "
                f"{unique_authors}/{max(1, int(min_unique_authors))} yazar"
            ),
            data=data,
            mentions=mentions,
            unique_authors=unique_authors,
            community_gate="WAIT",
            sentiment=sentiment,
        )

    engagements = max(0.0, _number(data.get("engagements_window"), _number(data.get("engagements_1h"))))
    credible_authors = max(
        0.0,
        _number(data.get("credible_authors_window"), _number(data.get("credible_authors_1h"))),
    )
    duplicate_ratio = _clamp(
        _number(
            data.get("duplicate_ratio"),
            _number(data.get("duplicate_count")) / max(mentions, 1),
        ),
        0.0,
        1.0,
    )
    new_account_ratio = _clamp(
        _number(
            data.get("new_account_ratio"),
            _number(data.get("new_account_count")) / max(unique_authors, 1),
        ),
        0.0,
        1.0,
    )
    author_concentration = _clamp(
        _number(
            data.get("author_concentration"),
            max(Counter(current_authors).values(), default=0) / max(mentions, 1),
        ),
        0.0,
        1.0,
    )
    active_buckets = max(0.0, _number(data.get("active_buckets")))
    news_count = max(0.0, _number(data.get("news_count")))
    community_coverage = int(_clamp(_number(
        data.get("community_coverage_pct"),
        _number(data.get("coverage_pct")),
    )))
    news_coverage = int(_clamp(_number(data.get("news_coverage_pct"))))
    coverage = min(100, community_coverage + news_coverage)

    mention_velocity = (mentions + 1.0) / (previous_mentions + 1.0)
    author_velocity = (unique_authors + 1.0) / (previous_unique_authors + 1.0)
    engagement_per_post = engagements / max(mentions, 1.0)
    unique_ratio = unique_authors / max(mentions, 1.0)
    credible_ratio = credible_authors / max(unique_authors, 1.0)
    attention = (
        _clamp((mention_velocity - 1.0) * 22.0, 0.0, 25.0)
        + _clamp((author_velocity - 1.0) * 18.0, 0.0, 20.0)
        + _clamp(engagement_per_post / 8.0 * 15.0, 0.0, 15.0)
        + _clamp(len(community_platforms) / 2.0 * 10.0, 0.0, 10.0)
        + _clamp(credible_ratio * 20.0, 0.0, 10.0)
        + _clamp(unique_ratio * 10.0, 0.0, 10.0)
        + _clamp(active_buckets / max(window_hours, 1) * 10.0, 0.0, 10.0)
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

    if manipulation >= 60:
        stage = "SUSPICIOUS"
    elif sentiment <= -35 and attention >= 40:
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

    if stage in {"SUSPICIOUS", "NEGATIVE_EVENT", "CROWDED"}:
        community_gate = "BLOCK"
        gate_reasons = [f"topluluk aşaması {stage.lower()}"]
    elif sentiment < int(min_sentiment):
        community_gate = "BLOCK"
        gate_reasons = [f"topluluk duyarlılığı eşik altında ({sentiment:+d})"]
    elif stage in {"SEED", "EMERGING", "CONFIRMED"}:
        community_gate = "PASS"
        gate_reasons = ["organik topluluk ilgisi asgari kanıtı geçti"]
    else:
        community_gate = "WAIT"
        gate_reasons = ["topluluk mevcut fakat henüz ivme teyidi yok"]

    reasons: list[str] = []
    if mention_velocity >= 2:
        reasons.append(f"{window_hours} saat görüş ivmesi {mention_velocity:.1f}x")
    if author_velocity >= 1.5:
        reasons.append(f"benzersiz yazar ivmesi {author_velocity:.1f}x")
    if len(community_platforms) >= 2:
        reasons.append(f"{len(community_platforms)} bağımsız topluluk kanalı")
    if news_count:
        reasons.append(f"{int(news_count)} bağlamsal haber")
    if manipulation >= 40:
        reasons.append("eşzamanlı/kopya paylaşım riski")
    if crowding >= 40:
        reasons.append("ilgi fiyat hareketini geç takip ediyor olabilir")
    if not reasons:
        reasons.append("topluluk taban çizgisi oluşturuldu")

    breakdown = _opinion_breakdown(community_texts)
    return {
        "status": "READY",
        "stage": stage,
        "community_gate": community_gate,
        "gate_reasons": gate_reasons,
        "attention_score": int(round(attention)),
        "viral_potential": int(round(viral)),
        "sentiment_score": sentiment,
        "manipulation_risk": int(round(manipulation)),
        "crowding_risk": int(round(crowding)),
        "coverage_pct": coverage,
        "community_coverage_pct": community_coverage,
        "news_coverage_pct": news_coverage,
        "mentions_window": mentions,
        "previous_mentions_window": int(previous_mentions),
        "mention_velocity": round(mention_velocity, 2),
        "unique_authors_window": unique_authors,
        "previous_unique_authors_window": int(previous_unique_authors),
        "author_velocity": round(author_velocity, 2),
        "window_hours": window_hours,
        "community_platforms": community_platforms,
        "configured_community_platforms": configured,
        "reached_community_platforms": reached,
        "platforms": sorted(set(community_platforms) | set(data.get("news_platforms") or [])),
        "news_count": int(news_count),
        "reasons": reasons[:4],
        "sources": list(data.get("sources") or [])[:8],
        "provider_errors": list(data.get("provider_errors") or [])[:3],
        "narrative": ", ".join(breakdown.get("themes") or []) or None,
        **breakdown,
        "observed_at": int(data.get("observed_at") or time.time()),
        "can_authorize_trade": False,
    }


class SocialIntelligenceProvider:
    """Optional X + Reddit community collector with GDELT news context."""

    X_RECENT_SEARCH = "https://api.x.com/2/tweets/search/recent"
    GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"
    REDDIT_TOKEN = "https://www.reddit.com/api/v1/access_token"
    REDDIT_SEARCH = "https://oauth.reddit.com/search"

    def __init__(
        self,
        *,
        x_bearer_token: str = "",
        reddit_client_id: str = "",
        reddit_client_secret: str = "",
        reddit_user_agent: str = "server:signal-bot:v4.7 (community radar)",
        reddit_enabled: bool = True,
        gdelt_enabled: bool = True,
        timeout: int = 12,
        cache_ttl_seconds: int = 10 * 60,
        max_assets: int = 6,
        window_hours: int = 6,
        min_mentions: int = 4,
        min_unique_authors: int = 3,
        min_community_platforms: int = 1,
        min_sentiment: int = -10,
    ):
        self.x_bearer_token = str(x_bearer_token or "").strip()
        self.reddit_client_id = str(reddit_client_id or "").strip()
        self.reddit_client_secret = str(reddit_client_secret or "").strip()
        self.reddit_enabled = bool(reddit_enabled)
        self.gdelt_enabled = bool(gdelt_enabled)
        self.timeout = max(3, int(timeout))
        self.cache_ttl_seconds = max(60, int(cache_ttl_seconds))
        self.max_assets = max(1, int(max_assets))
        self.window_hours = max(1, min(24, int(window_hours)))
        self.min_mentions = max(1, int(min_mentions))
        self.min_unique_authors = max(1, int(min_unique_authors))
        self.min_community_platforms = max(1, int(min_community_platforms))
        self.min_sentiment = max(-100, min(100, int(min_sentiment)))
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": str(reddit_user_agent or "SignalBot/4.7")})
        self._cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._reddit_access_token = ""
        self._reddit_token_expires_at = 0
        self._reddit_lock = threading.RLock()
        self._gdelt_lock = threading.RLock()
        self._last_gdelt_request_at = 0.0
        self._lock = threading.RLock()

    @staticmethod
    def _normalise_text(value: Any) -> str:
        text = re.sub(r"https?://\S+", "", str(value or "").lower())
        return re.sub(r"\W+", " ", text).strip()

    @staticmethod
    def _symbol(item: MexcListing) -> str:
        return re.sub(r"[^A-Z0-9]", "", item.symbol.upper())

    @classmethod
    def _text_relevant(cls, text: Any, item: MexcListing) -> bool:
        value = str(text or "")
        symbol = cls._symbol(item)
        if re.search(rf"\${re.escape(symbol)}\b", value, flags=re.I):
            return True
        if not re.search(rf"\b{re.escape(symbol)}\b", value, flags=re.I):
            return False
        if len(symbol) >= 5:
            return True
        crypto_context = re.search(
            r"\b(?:crypto|coin|token|blockchain|mexc|listing|airdrop|defi|web3)\b",
            value,
            flags=re.I,
        )
        return bool(crypto_context)

    @classmethod
    def _x_query(cls, item: MexcListing) -> str:
        symbol = cls._symbol(item)
        return f'(${symbol} OR "{symbol} token" OR "{symbol} coin") -is:retweet'

    def _fetch_x(self, item: MexcListing) -> dict[str, Any]:
        response = self.session.get(
            self.X_RECENT_SEARCH,
            headers={"Authorization": f"Bearer {self.x_bearer_token}"},
            params={
                "query": self._x_query(item),
                "max_results": 100,
                "tweet.fields": "created_at,public_metrics,author_id,entities",
                "expansions": "author_id",
                "user.fields": "created_at,public_metrics,verified",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        posts = [
            row for row in (payload.get("data") or [])
            if isinstance(row, dict) and self._text_relevant(row.get("text"), item)
        ]
        users = {
            str(row.get("id")): row
            for row in (payload.get("includes") or {}).get("users", [])
            if isinstance(row, dict)
        }
        now = datetime.now(timezone.utc)
        current_posts: list[dict] = []
        previous_posts: list[dict] = []
        active_buckets: set[int] = set()
        window_seconds = self.window_hours * 3600
        for row in posts:
            try:
                created = datetime.fromisoformat(
                    str(row.get("created_at")).replace("Z", "+00:00")
                )
                age_s = max(0.0, (now - created).total_seconds())
            except (TypeError, ValueError):
                continue
            if age_s <= window_seconds:
                current_posts.append(row)
                active_buckets.add(int(age_s // 3600))
            elif age_s <= 2 * window_seconds:
                previous_posts.append(row)

        current_authors = [f"X:{row.get('author_id')}" for row in current_posts]
        previous_authors = [f"X:{row.get('author_id')}" for row in previous_posts]
        texts = [str(row.get("text") or "") for row in current_posts]
        normalised = [self._normalise_text(text) for text in texts]
        duplicate_count = sum(
            count - 1 for count in Counter(normalised).values() if count > 1
        )
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
        for author_id in {str(row.get("author_id") or "") for row in current_posts}:
            user = users.get(author_id) or {}
            followers = _number((user.get("public_metrics") or {}).get("followers_count"))
            if bool(user.get("verified")) or followers >= 10_000:
                credible += 1
            try:
                created = datetime.fromisoformat(
                    str(user.get("created_at")).replace("Z", "+00:00")
                )
                if (now - created).days <= 30:
                    new_accounts += 1
            except (TypeError, ValueError):
                pass
        return {
            "reached_community_platforms": ["X"],
            "community_platforms": ["X"] if current_posts else [],
            "mentions_window": len(current_posts),
            "previous_mentions_window": len(previous_posts),
            "current_authors": current_authors,
            "previous_authors": previous_authors,
            "community_texts": texts,
            "engagements_window": engagements,
            "credible_authors_window": credible,
            "duplicate_count": duplicate_count,
            "new_account_count": new_accounts,
            "active_bucket_ids": [f"X:{bucket}" for bucket in active_buckets],
            "sources": [
                {
                    "title": text[:100],
                    "url": f"https://x.com/i/web/status/{row.get('id')}",
                    "provider": "X",
                    "source_type": "COMMUNITY",
                }
                for row, text in zip(current_posts[:4], texts[:4])
            ],
            "community_coverage_pct": 55 if current_posts else 15,
        }

    def _reddit_token(self) -> str:
        now = int(time.time())
        with self._reddit_lock:
            if self._reddit_access_token and now < self._reddit_token_expires_at - 60:
                return self._reddit_access_token
            response = self.session.post(
                self.REDDIT_TOKEN,
                auth=(self.reddit_client_id, self.reddit_client_secret),
                data={"grant_type": "client_credentials"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json() if response.content else {}
            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise RuntimeError("Reddit OAuth access_token üretmedi")
            self._reddit_access_token = token
            self._reddit_token_expires_at = now + int(payload.get("expires_in") or 3600)
            return token

    def _fetch_reddit(self, item: MexcListing) -> dict[str, Any]:
        token = self._reddit_token()
        symbol = self._symbol(item)
        response = self.session.get(
            self.REDDIT_SEARCH,
            headers={"Authorization": f"bearer {token}"},
            params={
                "q": f'("{symbol}" OR "${symbol}") (crypto OR token OR coin)',
                "sort": "new",
                "t": "day",
                "limit": 100,
                "type": "link",
                "raw_json": 1,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        children = ((payload.get("data") or {}).get("children") or [])
        posts: list[dict[str, Any]] = []
        for child in children:
            row = child.get("data") if isinstance(child, dict) else None
            if not isinstance(row, dict):
                continue
            text = f"{row.get('title') or ''}\n{row.get('selftext') or ''}".strip()
            if self._text_relevant(text, item):
                row = dict(row)
                row["_text"] = text
                posts.append(row)
        now = time.time()
        window_seconds = self.window_hours * 3600
        current_posts = [
            row for row in posts
            if 0 <= now - _number(row.get("created_utc"), now) <= window_seconds
        ]
        previous_posts = [
            row for row in posts
            if window_seconds < now - _number(row.get("created_utc"), now) <= 2 * window_seconds
        ]
        current_authors = [f"REDDIT:{row.get('author')}" for row in current_posts]
        previous_authors = [f"REDDIT:{row.get('author')}" for row in previous_posts]
        texts = [str(row.get("_text") or "") for row in current_posts]
        normalised = [self._normalise_text(text) for text in texts]
        duplicate_count = sum(
            count - 1 for count in Counter(normalised).values() if count > 1
        )
        credible_authors = {
            str(row.get("author") or "")
            for row in current_posts
            if _number(row.get("score")) >= 10 or _number(row.get("num_comments")) >= 5
        }
        active_buckets = {
            int((now - _number(row.get("created_utc"), now)) // 3600)
            for row in current_posts
        }
        return {
            "reached_community_platforms": ["REDDIT"],
            "community_platforms": ["REDDIT"] if current_posts else [],
            "mentions_window": len(current_posts),
            "previous_mentions_window": len(previous_posts),
            "current_authors": current_authors,
            "previous_authors": previous_authors,
            "community_texts": texts,
            "engagements_window": sum(
                max(0, _number(row.get("score")))
                + 2 * max(0, _number(row.get("num_comments")))
                for row in current_posts
            ),
            "credible_authors_window": len(credible_authors),
            "duplicate_count": duplicate_count,
            "active_bucket_ids": [f"REDDIT:{bucket}" for bucket in active_buckets],
            "sources": [
                {
                    "title": str(row.get("title") or "")[:100],
                    "url": f"https://www.reddit.com{row.get('permalink')}",
                    "provider": "REDDIT",
                    "source_type": "COMMUNITY",
                }
                for row in current_posts[:4]
                if str(row.get("permalink") or "").startswith("/")
            ],
            "community_coverage_pct": 35 if current_posts else 10,
        }

    def _fetch_gdelt(self, item: MexcListing) -> dict[str, Any]:
        symbol = self._symbol(item)
        with self._gdelt_lock:
            wait = 1.25 - (time.monotonic() - self._last_gdelt_request_at)
            if wait > 0:
                time.sleep(wait)
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
            self._last_gdelt_request_at = time.monotonic()
        response.raise_for_status()
        payload = response.json() if response.content else {}
        articles = [row for row in payload.get("articles", []) if isinstance(row, dict)]
        return {
            "news_count": len(articles),
            "news_texts": [str(row.get("title") or "") for row in articles if row.get("title")],
            "news_platforms": ["NEWS"] if articles else [],
            "sources": [
                {
                    "title": str(row.get("title") or "")[:100],
                    "url": row.get("url"),
                    "provider": "GDELT",
                    "source_type": "NEWS",
                }
                for row in articles[:4]
                if row.get("url")
            ],
            "news_coverage_pct": 25 if articles else 5,
        }

    @staticmethod
    def _merge(parts: Iterable[dict[str, Any]]) -> dict[str, Any]:
        list_keys = {
            "community_platforms", "configured_community_platforms",
            "reached_community_platforms", "news_platforms", "sources",
            "community_texts", "news_texts", "current_authors",
            "previous_authors", "active_bucket_ids", "provider_errors",
        }
        sum_keys = {
            "mentions_window", "previous_mentions_window", "engagements_window",
            "credible_authors_window", "duplicate_count", "new_account_count",
            "news_count", "community_coverage_pct", "news_coverage_pct",
        }
        result: dict[str, Any] = {key: [] for key in list_keys}
        for key in sum_keys:
            result[key] = 0.0
        for part in parts:
            if not part:
                continue
            for key, value in part.items():
                if key in list_keys:
                    result[key].extend(value or [])
                elif key in sum_keys:
                    result[key] = _number(result.get(key)) + _number(value)
                elif value is not None:
                    result[key] = value
        for key in {
            "community_platforms", "configured_community_platforms",
            "reached_community_platforms", "news_platforms", "active_bucket_ids",
        }:
            result[key] = sorted(set(result[key]))
        result["community_coverage_pct"] = min(
            100, int(result["community_coverage_pct"])
        )
        result["news_coverage_pct"] = min(100, int(result["news_coverage_pct"]))
        result["coverage_pct"] = min(
            100,
            result["community_coverage_pct"] + result["news_coverage_pct"],
        )
        result["active_buckets"] = len(set(result["active_bucket_ids"]))
        result["unique_authors_window"] = len(set(result["current_authors"]))
        result["previous_unique_authors_window"] = len(
            set(result["previous_authors"])
        )
        normalised = [
            SocialIntelligenceProvider._normalise_text(value)
            for value in result["community_texts"]
            if value
        ]
        result["duplicate_count"] = max(
            result["duplicate_count"],
            sum(count - 1 for count in Counter(normalised).values() if count > 1),
        )
        result["observed_at"] = int(time.time())
        return result

    def _fetch_one(self, item: MexcListing) -> dict[str, Any]:
        configured: list[str] = []
        if self.x_bearer_token:
            configured.append("X")
        if self.reddit_enabled and self.reddit_client_id and self.reddit_client_secret:
            configured.append("REDDIT")
        parts: list[dict[str, Any]] = [{
            "configured_community_platforms": configured,
            "window_hours": self.window_hours,
        }]
        if self.x_bearer_token:
            try:
                parts.append(self._fetch_x(item))
            except Exception as exc:
                log.warning("X topluluk taraması başarısız (%s): %s", item.pair, exc)
                parts.append({"provider_errors": [f"X:{type(exc).__name__}"]})
        if "REDDIT" in configured:
            try:
                parts.append(self._fetch_reddit(item))
            except Exception as exc:
                log.warning("Reddit topluluk taraması başarısız (%s): %s", item.pair, exc)
                parts.append({"provider_errors": [f"REDDIT:{type(exc).__name__}"]})
        if self.gdelt_enabled:
            try:
                parts.append(self._fetch_gdelt(item))
            except Exception as exc:
                log.warning("GDELT haber taraması başarısız (%s): %s", item.pair, exc)
                parts.append({"provider_errors": [f"GDELT:{type(exc).__name__}"]})
        return score_social_snapshot(
            self._merge(parts),
            price_change_pct=item.change_pct,
            volume_acceleration=item.volume_acceleration,
            min_mentions=self.min_mentions,
            min_unique_authors=self.min_unique_authors,
            min_community_platforms=self.min_community_platforms,
            min_sentiment=self.min_sentiment,
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
            with ThreadPoolExecutor(max_workers=min(2, len(pending))) as executor:
                futures = {executor.submit(self._fetch_one, item): item for item in pending}
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        signal = future.result()
                    except Exception as exc:
                        log.warning("Topluluk radarı başarısız (%s): %s", item.pair, exc)
                        signal = score_social_snapshot(None)
                    result[item.pair] = signal
                    with self._lock:
                        self._cache[item.pair] = (now, dict(signal))
        return result
