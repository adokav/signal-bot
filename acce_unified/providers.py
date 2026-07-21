"""Read-only public market-data adapters for the unified radar."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .liquid_long import calculate_long_metrics
from .listings import extract_listing_titles, extract_symbols_from_title, is_spot_listing_title
from .models import CexTicker, MexcListing


log = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mexc_change_pct(row: dict[str, Any]) -> float:
    """Convert MEXC's decimal change ratio to a true percentage.

    The Spot v3 payload uses values such as ``0.0779`` for a 7.79% move.
    Recomputing from open/last when available also guards against field-format
    drift without applying the Binance convention to MEXC rows.
    """
    last_price = _safe_float(row.get("lastPrice"))
    open_price = _safe_float(row.get("openPrice"))
    if last_price > 0 and open_price > 0:
        return (last_price / open_price - 1.0) * 100.0
    return _safe_float(row.get("priceChangePercent")) * 100.0


def _mexc_market_is_online(value: Any, default: bool = True) -> bool:
    """Interpret MEXC's market status without conflating it with API access.

    MEXC documents ``1`` as online, ``2`` as paused, and ``3`` as offline.
    Some exchangeInfo responses have also used the textual ``ENABLED`` form.
    A missing status keeps the provider backward compatible, while an unknown
    explicit value is treated conservatively as not open.
    """
    if value is None or not str(value).strip():
        return default
    return str(value).strip().upper() in {"1", "ENABLED", "ONLINE", "TRADING"}


def _session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8))
    return session


class MexcPublicProvider:
    """MEXC-first ticker source with public market-data fallbacks.

    Some cloud regions return a 200 HTML block page for MEXC. Binance mirrors
    keep the broad *market* radar alive, but rows retain their venue label so a
    fallback result can never masquerade as a confirmed MEXC listing.
    """

    DEFAULT_ENDPOINTS = (
        ("MEXC", "https://api.mexc.com/api/v3/ticker/24hr"),
        ("BINANCE", "https://data-api.binance.vision/api/v3/ticker/24hr"),
        ("BINANCE", "https://api-gcp.binance.com/api/v3/ticker/24hr"),
        ("BINANCE", "https://api.binance.com/api/v3/ticker/24hr"),
    )

    def __init__(
        self,
        *,
        timeout: int = 15,
        base_url: str = "https://api.mexc.com",
        endpoints: tuple[tuple[str, str], ...] | None = None,
    ):
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.endpoints = endpoints or self.DEFAULT_ENDPOINTS
        if base_url.rstrip("/") != "https://api.mexc.com" and endpoints is None:
            self.endpoints = (("MEXC", f"{self.base_url}/api/v3/ticker/24hr"),)
        self.session = _session("SignalBot-ACCE-Unified/2.0")
        self._active_market_base_url = self.base_url
        self._long_cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._long_lock = threading.RLock()

    def fetch_tickers(self) -> list[CexTicker]:
        failures: list[str] = []
        data = None
        venue = "UNKNOWN"
        for candidate_venue, url in self.endpoints:
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                content_type = str(response.headers.get("content-type") or "").lower()
                if "json" not in content_type and response.text.lstrip().startswith("<"):
                    raise RuntimeError("HTML block page")
                payload = response.json()
                if not isinstance(payload, list):
                    raise RuntimeError("ticker yanıtı liste değil")
                data = payload
                venue = candidate_venue
                self._active_market_base_url = url.split("/api/v3/", 1)[0].rstrip("/")
                break
            except Exception as exc:
                failures.append(f"{candidate_venue}:{type(exc).__name__}")
                log.warning("CEX ticker endpoint başarısız (%s): %s", url, exc)
        if data is None:
            raise RuntimeError("Tüm CEX ticker endpointleri başarısız: " + ", ".join(failures))
        out: list[CexTicker] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                out.append(
                    CexTicker(
                        symbol=str(row["symbol"]).upper(),
                        last_price=float(row["lastPrice"]),
                        change_pct=(
                            _mexc_change_pct(row)
                            if venue == "MEXC"
                            else float(row.get("priceChangePercent") or 0.0)
                        ),
                        quote_volume=float(row.get("quoteVolume") or 0.0),
                        venue=venue,
                        bid_price=float(row.get("bidPrice") or 0.0),
                        ask_price=float(row.get("askPrice") or 0.0),
                        high_price=float(row.get("highPrice") or 0.0),
                        low_price=float(row.get("lowPrice") or 0.0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def _fetch_long_metrics_one(self, symbol: str) -> dict[str, Any]:
        response = self.session.get(
            f"{self._active_market_base_url}/api/v3/klines",
            params={"symbol": symbol, "interval": "15m", "limit": 97},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("kline yanıtı liste değil")
        return calculate_long_metrics(payload)

    def fetch_long_metrics(
        self,
        symbols: Any,
    ) -> dict[str, dict[str, Any]]:
        """Enrich a bounded shortlist; never fan out across all 100 pairs."""
        ordered = list(dict.fromkeys(
            str(symbol or "").strip().upper() for symbol in (symbols or []) if symbol
        ))
        now = int(time.time())
        candle_bucket = now // (15 * 60)
        result: dict[str, dict[str, Any]] = {}
        pending: list[str] = []
        with self._long_lock:
            for symbol in ordered:
                cached = self._long_cache.get(symbol)
                if cached and cached[0] == candle_bucket:
                    result[symbol] = dict(cached[1])
                else:
                    pending.append(symbol)
        if pending:
            with ThreadPoolExecutor(max_workers=min(10, len(pending))) as executor:
                future_to_symbol = {
                    executor.submit(self._fetch_long_metrics_one, symbol): symbol
                    for symbol in pending
                }
                for future in as_completed(future_to_symbol):
                    symbol = future_to_symbol[future]
                    try:
                        metrics = future.result()
                    except Exception as exc:
                        log.warning("Likit-100 kline alınamadı (%s): %s", symbol, exc)
                        metrics = {
                            "status": "PROVIDER_UNAVAILABLE",
                            "can_authorize_trade": False,
                        }
                    result[symbol] = metrics
                    with self._long_lock:
                        self._long_cache[symbol] = (candle_bucket, dict(metrics))
        return result


class MexcNewListingProvider:
    """PhenomenonX adapter for official MEXC listing discovery.

    Official announcement mirrors are attempted first. When hosted IP ranges
    are blocked, a persisted ``exchangeInfo`` symbol snapshot detects newly
    appearing USDT pairs without borrowing data from another exchange.
    """

    DEFAULT_ANNOUNCEMENT_ENDPOINTS = (
        "https://www.mexc.com/announcements/new-listings",
        "https://www.mexc.co/announcements",
        "https://www.mexc.fm/announcements",
    )

    def __init__(
        self,
        *,
        timeout: int = 15,
        base_url: str = "https://api.mexc.com",
        announcement_endpoints: tuple[str, ...] | None = None,
        seen_file: str = "mexc_seen_symbols.json",
        candidate_file: str | None = None,
        candidate_ttl_hours: int = 72,
        max_candidates: int = 20,
        confirmation_scans: int = 2,
        announcement_seen_file: str | None = None,
    ):
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.announcement_endpoints = (
            announcement_endpoints or self.DEFAULT_ANNOUNCEMENT_ENDPOINTS
        )
        self.seen_file = Path(seen_file)
        self.candidate_file = (
            Path(candidate_file)
            if candidate_file
            else self.seen_file.with_name("mexc_listing_candidates.json")
        )
        self.candidate_ttl_seconds = max(1, candidate_ttl_hours) * 60 * 60
        self.max_candidates = max(1, max_candidates)
        self.confirmation_scans = max(2, min(4, int(confirmation_scans)))
        self.announcement_seen_file = (
            Path(announcement_seen_file)
            if announcement_seen_file
            else self.seen_file.with_name("mexc_seen_spot_announcements.json")
        )
        self.session = _session("SignalBot-PhenomenonX-MEXC/3.0")
        self._watch_lock = threading.RLock()
        self._watched_pairs: set[str] = set()

    def set_watched_pairs(self, pairs: Any) -> None:
        normalized = {
            str(pair or "").strip().upper().replace("/", "")
            for pair in (pairs or [])
        }
        with self._watch_lock:
            self._watched_pairs = {
                pair for pair in normalized
                if pair.endswith("USDT") and 6 <= len(pair) <= 44
            }

    def _watched_pairs_snapshot(self) -> set[str]:
        with self._watch_lock:
            return set(self._watched_pairs)

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.session.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        if "json" not in content_type and response.text.lstrip().startswith("<"):
            raise RuntimeError("MEXC Spot API HTML block page")
        return response.json()

    def fetch_listing_titles(self) -> list[str]:
        failures: list[str] = []
        for url in self.announcement_endpoints:
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                titles = extract_listing_titles(response.text)
                if titles:
                    return titles
                failures.append(f"{url}:empty")
            except Exception as exc:
                failures.append(f"{url}:{type(exc).__name__}")
        if failures:
            log.warning(
                "MEXC announcement akışı okunamadı; Spot API farkı kullanılacak: %s",
                ", ".join(failures),
            )
        return []

    def exchange_info(self) -> dict[str, dict[str, Any]]:
        payload = self._get_json("/api/v3/exchangeInfo") or {}
        symbols = payload.get("symbols") if isinstance(payload, dict) else []
        result: dict[str, dict[str, Any]] = {}
        for item in symbols or []:
            if not isinstance(item, dict):
                continue
            pair = str(item.get("symbol") or "").upper()
            if pair.endswith("USDT"):
                result[pair] = item
        if not result:
            raise RuntimeError("MEXC exchangeInfo geçerli USDT paritesi üretmedi")
        return result

    def ticker_map(self) -> dict[str, dict[str, Any]]:
        payload = self._get_json("/api/v3/ticker/24hr") or []
        rows = [payload] if isinstance(payload, dict) else payload
        return {
            str(item.get("symbol") or "").upper(): item
            for item in rows
            if isinstance(item, dict)
        }

    def volume_acceleration(self, pair: str) -> float:
        """Detect early volume expansion from completed five-minute candles."""
        try:
            rows = self._get_json(
                "/api/v3/klines",
                {"symbol": pair, "interval": "5m", "limit": 13},
            ) or []
            if len(rows) < 5:
                return 0.0
            volumes = [
                _safe_float(row[7] if len(row) > 7 else row[5])
                for row in rows
            ]
            completed = volumes[:-1]
            latest = completed[-1]
            baseline_rows = completed[:-1]
            baseline = statistics.median(baseline_rows) if baseline_rows else 0.0
            return latest / max(baseline, 1.0)
        except Exception:
            log.warning("MEXC listing kline alınamadı: %s", pair, exc_info=True)
            return 0.0

    @staticmethod
    def _announcement_fingerprint(title: str) -> str:
        normalized = re.sub(r"\W+", " ", str(title or "").lower()).strip()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _new_spot_announcement_titles(self, titles: list[str]) -> list[str]:
        eligible = [title for title in titles if is_spot_listing_title(title)]
        fingerprints = {
            self._announcement_fingerprint(title): title for title in eligible
        }
        initialized = False
        try:
            payload = json.loads(self.announcement_seen_file.read_text("utf-8"))
            if isinstance(payload, dict):
                previous = set(payload.get("fingerprints") or [])
                initialized = payload.get("initialized") is True
            elif isinstance(payload, list):
                previous = set(payload)
                initialized = bool(payload)
            else:
                previous = set()
        except Exception:
            previous = set()
        try:
            self.announcement_seen_file.parent.mkdir(parents=True, exist_ok=True)
            combined = sorted((previous | set(fingerprints)))[-2000:]
            tmp = self.announcement_seen_file.with_name(
                f".{self.announcement_seen_file.name}.{os.getpid()}.tmp"
            )
            tmp.write_text(json.dumps({
                "version": 1,
                "initialized": True,
                "fingerprints": combined,
            }, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.announcement_seen_file)
        except Exception:
            log.warning("MEXC duyuru hafızası yazılamadı", exc_info=True)
        if not initialized:
            # A fresh deploy baselines the page instead of relabelling months-old
            # headlines as current opportunities.
            return []
        return [title for key, title in fingerprints.items() if key not in previous]

    def _new_pairs_from_snapshot(
        self,
        current: set[str],
        online: set[str],
        *,
        now: int,
    ) -> list[str]:
        legacy_snapshot = False
        try:
            payload = json.loads(self.seen_file.read_text("utf-8"))
            if isinstance(payload, list):
                legacy_snapshot = True
                previous = {str(value).upper() for value in payload}
                pending: dict[str, dict[str, int]] = {}
            elif isinstance(payload, dict):
                previous = {
                    str(value).upper() for value in (payload.get("symbols") or [])
                }
                pending = {
                    str(pair).upper(): dict(row)
                    for pair, row in (payload.get("pending") or {}).items()
                    if isinstance(row, dict)
                }
            else:
                previous, pending = set(), {}
        except Exception:
            previous, pending = set(), {}

        confirmed: list[str] = []
        if not previous or legacy_snapshot:
            # Lists written by the old provider contained only online markets;
            # unioning the full exchangeInfo set prevents AVAX-like pause/reopen
            # events from masquerading as a new listing after an upgrade.
            previous |= current
            pending = {}
        else:
            unseen = current - previous
            recovery_limit = max(10, int(max(len(current), 1) * 0.02))
            if len(unseen) > recovery_limit:
                log.warning(
                    "MEXC snapshot %d toplu ekleme gösterdi; olay üretmeden yeniden bazlandı",
                    len(unseen),
                )
                previous |= current
                pending = {}
            else:
                pending = {
                    pair: row for pair, row in pending.items() if pair in current
                }
                for pair in unseen:
                    row = pending.get(pair) or {
                        "first_seen_at": now,
                        "observations": 0,
                    }
                    row["observations"] = int(row.get("observations") or 0) + 1
                    row["last_seen_at"] = now
                    pending[pair] = row
                for pair, row in list(pending.items()):
                    if (
                        pair in online
                        and int(row.get("observations") or 0) >= self.confirmation_scans
                    ):
                        confirmed.append(pair)
                        previous.add(pair)
                        pending.pop(pair, None)

        try:
            self.seen_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.seen_file.with_name(f".{self.seen_file.name}.{os.getpid()}.tmp")
            tmp.write_text(
                json.dumps({
                    "version": 2,
                    "symbols": sorted(previous),
                    "pending": pending,
                    "updated_at": now,
                }, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, self.seen_file)
        except Exception:
            log.warning("MEXC sembol hafızası yazılamadı", exc_info=True)

        return sorted(confirmed)

    def _load_candidate_catalog(self) -> dict[str, dict[str, Any]]:
        try:
            payload = json.loads(self.candidate_file.read_text("utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        catalog: dict[str, dict[str, Any]] = {}
        for pair, row in payload.items():
            normalized = str(pair or "").upper()
            if (
                normalized.endswith("USDT")
                and isinstance(row, dict)
                and isinstance(row.get("first_seen_at"), int)
            ):
                if (
                    int(row.get("discovery_version") or 0) >= 2
                    and row.get("listing_event_verified") is True
                ):
                    catalog[normalized] = row
        return catalog

    def _save_candidate_catalog(self, catalog: dict[str, dict[str, Any]]) -> None:
        try:
            self.candidate_file.parent.mkdir(parents=True, exist_ok=True)
            # Bound disk state while retaining enough history to prevent old
            # announcement headlines from becoming "new" again after expiry.
            ordered = sorted(
                catalog.items(),
                key=lambda item: int(item[1].get("first_seen_at") or 0),
                reverse=True,
            )[:500]
            tmp = self.candidate_file.with_name(
                f".{self.candidate_file.name}.{os.getpid()}.tmp"
            )
            tmp.write_text(
                json.dumps(dict(ordered), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, self.candidate_file)
        except Exception:
            log.warning("MEXC aday hafızası yazılamadı", exc_info=True)

    def _candidate_records(
        self,
        discovered: list[tuple[str, str, int, str]],
        *,
        now: int,
    ) -> list[dict[str, Any]]:
        catalog = self._load_candidate_catalog()
        discovered_pairs: list[str] = []
        for symbol, title, rank, source in discovered:
            pair = f"{symbol}USDT"
            previous = catalog.get(pair) or {}
            first_seen = int(previous.get("first_seen_at") or now)
            catalog[pair] = {
                "symbol": symbol,
                "pair": pair,
                "title": title or previous.get("title") or pair,
                "rank": int(rank),
                "source": source or previous.get("source") or "ANNOUNCEMENT",
                "first_seen_at": first_seen,
                "last_seen_at": now,
                "discovery_version": 2,
                "listing_event_verified": True,
            }
            if pair not in discovered_pairs:
                discovered_pairs.append(pair)
        self._save_candidate_catalog(catalog)

        watched = self._watched_pairs_snapshot()
        cutoff = now - self.candidate_ttl_seconds
        active_pairs = {
            pair for pair, row in catalog.items()
            if int(row.get("first_seen_at") or 0) >= cutoff
        }
        verified_watched = watched & set(catalog)
        ordered_pairs = [
            *sorted(verified_watched),
            *(pair for pair in discovered_pairs if pair in active_pairs and pair not in watched),
            *(
                pair for pair, _ in sorted(
                    catalog.items(),
                    key=lambda item: int(item[1].get("first_seen_at") or 0),
                    reverse=True,
                )
                if pair in active_pairs
                and pair not in watched
                and pair not in discovered_pairs
            ),
        ]

        records: list[dict[str, Any]] = []
        auto_count = 0
        for pair in ordered_pairs:
            manual = pair in verified_watched
            if not manual and auto_count >= self.max_candidates:
                continue
            row = dict(catalog.get(pair) or {})
            if not row:
                continue
            row["manually_watched"] = manual
            records.append(row)
            if not manual:
                auto_count += 1
        return records

    def fetch_listings(self) -> list[MexcListing]:
        scan_started_at = int(time.time())
        titles = self.fetch_listing_titles()
        fresh_titles = self._new_spot_announcement_titles(titles)
        try:
            exchange = self.exchange_info()
        except Exception as exc:
            exchange = {}
            log.warning("MEXC Spot exchangeInfo teyidi alınamadı: %s", exc)
        try:
            ticker = self.ticker_map()
        except Exception as exc:
            ticker = {}
            log.warning("MEXC Spot ticker teyidi alınamadı: %s", exc)
        # Persist only pairs whose MEXC market is online. ``isSpotTradingAllowed``
        # describes API-order permission and can remain false for an already
        # listed market, so it must not drive listing discovery. A symbol may
        # appear in exchangeInfo while its market is paused/offline; excluding
        # it preserves the later market-status -> online transition.
        all_spot_pairs = set(exchange)
        open_spot_pairs = {
            pair
            for pair, info in exchange.items()
            if _mexc_market_is_online(info.get("status"), True)
        }
        additions = (
            self._new_pairs_from_snapshot(
                all_spot_pairs,
                open_spot_pairs,
                now=scan_started_at,
            )
            if exchange
            else []
        )

        discovered: list[tuple[str, str, int, str]] = []
        seen_symbols: set[str] = set()
        for rank, title in enumerate(fresh_titles, 1):
            for symbol in extract_symbols_from_title(title):
                if symbol not in seen_symbols:
                    seen_symbols.add(symbol)
                    discovered.append((symbol, title, rank, "ANNOUNCEMENT"))

        # Announcement headlines and the authoritative exchangeInfo diff are
        # complementary. Batch listing headlines sometimes keep symbols only
        # in the article body, so a parseable headline must not suppress a new
        # Spot pair found in the same scan.
        exchange_discoveries: list[tuple[str, str, int, str]] = []
        for pair in additions:
            if not pair.endswith("USDT") or len(pair) <= 4:
                continue
            symbol = pair[:-4]
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            exchange_discoveries.append(
                (
                    symbol,
                    f"MEXC Spot API'de yeni görülen {pair}",
                    0,
                    "EXCHANGE_DIFF",
                )
            )
        # A newly appeared Spot pair is more time-sensitive than older titles
        # still present on the announcement page. Put unseen API additions
        # first so max_candidates trimming cannot discard the fallback signal.
        discovered = [*exchange_discoveries, *discovered]

        if fresh_titles and not discovered:
            raise RuntimeError("Yeni MEXC Spot başlığı bulundu fakat sembol ayrıştırılamadı")
        records = self._candidate_records(discovered, now=scan_started_at)
        if not records and not exchange and not titles:
            raise RuntimeError("MEXC duyuru ve Spot API kaynakları birlikte kullanılamıyor")

        observations: list[MexcListing] = []
        for record in records:
            symbol = str(record.get("symbol") or "").upper()
            pair = str(record.get("pair") or f"{symbol}USDT").upper()
            title = str(record.get("title") or pair)
            rank = int(record.get("rank") or 0)
            source = str(record.get("source") or "ANNOUNCEMENT")
            info = exchange.get(pair) or {}
            tick = ticker.get(pair) or {}
            if info:
                spot_status = (
                    "OPEN"
                    if _mexc_market_is_online(info.get("status"), True)
                    else "NOT_OPEN"
                )
            else:
                spot_status = "NOT_OPEN" if exchange else "UNKNOWN"
            bid = _safe_float(tick.get("bidPrice"))
            ask = _safe_float(tick.get("askPrice"))
            midpoint = (bid + ask) / 2 if bid > 0 and ask >= bid else 0.0
            spread_bps = ((ask - bid) / midpoint * 10_000) if midpoint > 0 else 0.0
            observations.append(
                MexcListing(
                    symbol=symbol,
                    pair=pair,
                    title=title,
                    rank=rank,
                    spot_status=spot_status,
                    last_price=_safe_float(tick.get("lastPrice")),
                    change_pct=_mexc_change_pct(tick),
                    quote_volume=_safe_float(tick.get("quoteVolume")),
                    volume_acceleration=0.0,
                    discovery_source=source,
                    first_seen_at=int(record.get("first_seen_at") or scan_started_at),
                    spread_bps=spread_bps,
                    manually_watched=bool(record.get("manually_watched")),
                )
            )

        eligible_indexes = [
            index for index, item in enumerate(observations)
            if item.spot_status == "OPEN"
        ]
        if eligible_indexes:
            with ThreadPoolExecutor(max_workers=min(4, len(eligible_indexes))) as executor:
                future_to_index = {
                    executor.submit(self.volume_acceleration, observations[index].pair): index
                    for index in eligible_indexes
                }
                for future in as_completed(future_to_index):
                    index = future_to_index[future]
                    try:
                        acceleration = float(future.result())
                    except Exception:
                        acceleration = 0.0
                    observations[index] = replace(
                        observations[index],
                        volume_acceleration=acceleration,
                    )
        return observations
