"""Read-only public market-data adapters for the unified radar."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import CexTicker


log = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
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
    keep the *radar* alive, but the scorer marks those rows as non-MEXC data so
    an out-of-universe candidate can never be assumed listed on MEXC.
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
        self.session = _session("SignalBot-ACCE-Unified/1.0")

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


class DexScreenerProvider:
    API = "https://api.dexscreener.com"
    CHAINS = ("solana", "base")

    def __init__(self, *, timeout: int = 15, max_tokens_per_chain: int = 90):
        self.timeout = timeout
        self.max_tokens_per_chain = max(1, max_tokens_per_chain)
        self.session = _session("SignalBot-ACCE-DexRadar/1.0")

    def _get(self, path: str) -> Any:
        response = self.session.get(f"{self.API}{path}", timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def fetch_pairs(
        self,
    ) -> tuple[
        list[dict[str, Any]],
        dict[tuple[str, str], dict[str, Any]],
        set[tuple[str, str]],
        set[tuple[str, str]],
    ]:
        profiles_raw = self._get("/token-profiles/latest/v1") or []
        boosts_raw = self._get("/token-boosts/latest/v1") or []
        takeovers_raw = self._get("/community-takeovers/latest/v1") or []
        profiles: dict[tuple[str, str], dict[str, Any]] = {}
        boosted: set[tuple[str, str]] = set()
        takeovers: set[tuple[str, str]] = set()

        for item in profiles_raw:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("chainId") or "").lower(), str(item.get("tokenAddress") or ""))
            if key[0] in self.CHAINS and key[1]:
                profiles[key] = item
        for item, target in ((row, boosted) for row in boosts_raw):
            if not isinstance(item, dict):
                continue
            key = (str(item.get("chainId") or "").lower(), str(item.get("tokenAddress") or ""))
            if key[0] in self.CHAINS and key[1]:
                target.add(key)
                profiles.setdefault(key, item)
        for item in takeovers_raw:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("chainId") or "").lower(), str(item.get("tokenAddress") or ""))
            if key[0] in self.CHAINS and key[1]:
                takeovers.add(key)
                profiles.setdefault(key, item)

        pairs: list[dict[str, Any]] = []
        for chain in self.CHAINS:
            addresses = [address for (item_chain, address) in profiles if item_chain == chain]
            addresses = addresses[: self.max_tokens_per_chain]
            for start in range(0, len(addresses), 30):
                batch = ",".join(addresses[start : start + 30])
                if not batch:
                    continue
                rows = self._get(f"/tokens/v1/{chain}/{batch}") or []
                best: dict[str, dict[str, Any]] = {}
                for pair in rows:
                    if not isinstance(pair, dict):
                        continue
                    address = str((pair.get("baseToken") or {}).get("address") or "")
                    liquidity = _safe_float((pair.get("liquidity") or {}).get("usd"))
                    previous = best.get(address)
                    previous_liquidity = _safe_float((previous.get("liquidity") or {}).get("usd")) if previous else -1.0
                    if address and liquidity > previous_liquidity:
                        best[address] = pair
                pairs.extend(best.values())
                time.sleep(0.08)
        return pairs, profiles, boosted, takeovers
