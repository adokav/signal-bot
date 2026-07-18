"""Read-only public market-data adapters for the unified radar."""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .listings import extract_listing_titles, extract_symbols_from_title
from .models import CexTicker, MexcListing


log = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _boolish(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


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
                        change_pct=float(row.get("priceChangePercent") or 0.0),
                        quote_volume=float(row.get("quoteVolume") or 0.0),
                        venue=venue,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out


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
        max_candidates: int = 20,
    ):
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.announcement_endpoints = (
            announcement_endpoints or self.DEFAULT_ANNOUNCEMENT_ENDPOINTS
        )
        self.seen_file = Path(seen_file)
        self.max_candidates = max(1, max_candidates)
        self.session = _session("SignalBot-PhenomenonX-MEXC/3.0")

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
        """Use only completed hourly candles; the last row may still be open."""
        try:
            rows = self._get_json(
                "/api/v3/klines",
                {"symbol": pair, "interval": "60m", "limit": 8},
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
            baseline = sum(baseline_rows) / max(len(baseline_rows), 1)
            return latest / max(baseline, 1.0)
        except Exception:
            log.warning("MEXC listing kline alınamadı: %s", pair, exc_info=True)
            return 0.0

    def _new_pairs_from_snapshot(self, current: set[str]) -> list[str]:
        try:
            previous = (
                set(json.loads(self.seen_file.read_text("utf-8")))
                if self.seen_file.exists()
                else set()
            )
        except Exception:
            previous = set()

        try:
            self.seen_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.seen_file.with_name(f".{self.seen_file.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(sorted(current)), encoding="utf-8")
            os.replace(tmp, self.seen_file)
        except Exception:
            log.warning("MEXC sembol hafızası yazılamadı", exc_info=True)

        if not previous:
            return []
        return sorted(current - previous)

    def fetch_listings(self) -> list[MexcListing]:
        titles = self.fetch_listing_titles()
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
        additions = self._new_pairs_from_snapshot(set(exchange)) if exchange else []

        discovered: list[tuple[str, str, int, str]] = []
        seen_symbols: set[str] = set()
        for rank, title in enumerate(titles, 1):
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

        if titles and not discovered:
            raise RuntimeError("MEXC listing başlığı bulundu fakat sembol ayrıştırılamadı")
        if not discovered and not exchange:
            raise RuntimeError("MEXC duyuru ve Spot API kaynakları birlikte kullanılamıyor")

        observations: list[MexcListing] = []
        for symbol, title, rank, source in discovered[: self.max_candidates]:
            pair = f"{symbol}USDT"
            info = exchange.get(pair) or {}
            tick = ticker.get(pair) or {}
            if info:
                spot_status = (
                    "OPEN"
                    if _boolish(info.get("isSpotTradingAllowed"), True)
                    else "NOT_OPEN"
                )
            else:
                spot_status = "NOT_OPEN" if exchange else "UNKNOWN"
            observations.append(
                MexcListing(
                    symbol=symbol,
                    pair=pair,
                    title=title,
                    rank=rank,
                    spot_status=spot_status,
                    last_price=_safe_float(tick.get("lastPrice")),
                    change_pct=_safe_float(tick.get("priceChangePercent")),
                    quote_volume=_safe_float(tick.get("quoteVolume")),
                    volume_acceleration=0.0,
                    discovery_source=source,
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
