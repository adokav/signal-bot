"""MEXC-gated wrapper for the Robinhood Chain pool radar.

GeckoTerminal remains the Robinhood Chain discovery source. This layer fails
closed unless the token symbol has an active MEXC USDT Spot market, sufficient
MEXC quote volume, and a plausible DEX/MEXC price match. It never places orders
or consumes Telegram ``getUpdates``.
"""

from __future__ import annotations

import html
import logging
import os
import threading
import time
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

from robinhood_radar import (
    CHAIN_ID,
    Candidate,
    GeckoClient,
    Pool,
    RadarConfig,
    State,
    _num,
    _usd,
    score_pool,
)

log = logging.getLogger(__name__)
MEXC_BASE = "https://api.mexc.com"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if not raw else raw.strip().lower() in {"1", "true", "yes", "on", "evet"}


def _env_float(name: str, default: float, low: float) -> float:
    return max(low, _num(os.getenv(name), default))


def _mexc_online(value: Any, default: bool = True) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().upper() in {"1", "ENABLED", "ONLINE", "TRADING"}


@dataclass(frozen=True)
class MexcFilterConfig:
    required: bool = True
    base_url: str = MEXC_BASE
    min_quote_volume: float = 100_000.0
    max_price_gap_pct: float = 60.0

    @classmethod
    def from_env(cls) -> "MexcFilterConfig":
        return cls(
            required=_bool("ROBINHOOD_RADAR_REQUIRE_MEXC", True),
            base_url=os.getenv("ROBINHOOD_RADAR_MEXC_BASE_URL", MEXC_BASE).rstrip("/"),
            min_quote_volume=_env_float(
                "ROBINHOOD_RADAR_MIN_MEXC_QUOTE_VOLUME_USD", 100_000, 0
            ),
            max_price_gap_pct=_env_float(
                "ROBINHOOD_RADAR_MAX_MEXC_PRICE_GAP_PCT", 60, 5
            ),
        )


@dataclass(frozen=True)
class MexcMarket:
    pair: str
    base: str
    last_price: float
    change_h24: float
    quote_volume: float
    bid: float = 0.0
    ask: float = 0.0


@dataclass(frozen=True)
class MexcCandidate:
    candidate: Candidate
    mexc_pair: str
    mexc_price: float
    mexc_change_h24: float
    mexc_quote_volume: float
    mexc_price_gap_pct: float
    score: int
    stage: str
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    execution_eligible: bool = False

    @property
    def token(self) -> str:
        return self.candidate.token

    @property
    def symbol(self) -> str:
        return self.candidate.symbol

    @property
    def pool(self) -> str:
        return self.candidate.pool

    @property
    def liquidity(self) -> float:
        return self.candidate.liquidity

    @property
    def volume_h1(self) -> float:
        return self.candidate.volume_h1

    def to_dict(self) -> dict[str, Any]:
        payload = self.candidate.to_dict()
        payload.update(
            {
                "mexc_pair": self.mexc_pair,
                "mexc_price": self.mexc_price,
                "mexc_change_h24": self.mexc_change_h24,
                "mexc_quote_volume": self.mexc_quote_volume,
                "mexc_price_gap_pct": self.mexc_price_gap_pct,
                "score": self.score,
                "stage": self.stage,
                "reasons": list(self.reasons),
                "risks": list(self.risks),
                "execution_eligible": False,
            }
        )
        return payload


class MexcClient:
    """Public MEXC Spot adapter with no fallback to another exchange."""

    def __init__(
        self,
        filter_config: MexcFilterConfig,
        *,
        timeout: int = 15,
        session: requests.Session | None = None,
    ):
        self.config = filter_config
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get(self, path: str) -> Any:
        response = self.session.get(
            f"{self.config.base_url}{path}", timeout=self.timeout
        )
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        if "json" not in content_type and response.text.lstrip().startswith("<"):
            raise RuntimeError("MEXC Spot API HTML block page")
        return response.json()

    def markets(self) -> dict[str, MexcMarket]:
        exchange_payload = self._get("/api/v3/exchangeInfo") or {}
        ticker_payload = self._get("/api/v3/ticker/24hr") or []
        exchange_rows = (
            exchange_payload.get("symbols")
            if isinstance(exchange_payload, dict)
            else []
        )
        ticker_rows = (
            [ticker_payload] if isinstance(ticker_payload, dict) else ticker_payload
        )
        tickers = {
            str(row.get("symbol") or "").upper(): row
            for row in ticker_rows or []
            if isinstance(row, dict)
        }
        result: dict[str, MexcMarket] = {}
        for row in exchange_rows or []:
            if not isinstance(row, dict):
                continue
            pair = str(row.get("symbol") or "").upper()
            base = (
                str(row.get("baseAsset") or "").upper()
                or pair.removesuffix("USDT")
            )
            quote = str(row.get("quoteAsset") or "").upper()
            if not pair.endswith("USDT") or (quote and quote != "USDT"):
                continue
            if not _mexc_online(row.get("status"), True):
                continue
            ticker = tickers.get(pair) or {}
            last = _num(ticker.get("lastPrice"))
            if last <= 0:
                continue
            open_price = _num(ticker.get("openPrice"))
            change = (
                (last / open_price - 1.0) * 100.0
                if open_price > 0
                else _num(ticker.get("priceChangePercent")) * 100.0
            )
            result[base] = MexcMarket(
                pair=pair,
                base=base,
                last_price=last,
                change_h24=change,
                quote_volume=_num(ticker.get("quoteVolume")),
                bid=_num(ticker.get("bidPrice")),
                ask=_num(ticker.get("askPrice")),
            )
        if not result:
            raise RuntimeError("MEXC active USDT Spot market list is empty")
        return result


def _stage(score: int, blocked: bool) -> str:
    if blocked:
        return "BLOCKED"
    if score >= 78:
        return "HOT"
    if score >= 64:
        return "BUILDING"
    if score >= 52:
        return "EARLY"
    return "WATCH"


def score_mexc_pool(
    pool: Pool,
    market: MexcMarket,
    radar_config: RadarConfig,
    filter_config: MexcFilterConfig,
    now: int | None = None,
) -> MexcCandidate:
    # Compatible with the original radar and any later version that adds its
    # own MEXC gate. This wrapper remains the authoritative MEXC confirmation.
    field_names = {field.name for field in fields(radar_config)}
    base_config = (
        replace(radar_config, require_mexc=False)
        if "require_mexc" in field_names
        else radar_config
    )
    base = score_pool(pool, base_config, now)
    score = float(base.score)
    reasons = list(base.reasons)
    risks = list(base.risks)
    blocked = base.stage == "BLOCKED"

    if market.quote_volume < filter_config.min_quote_volume:
        blocked = True
        risks.append(f"MEXC 24s hacmi eşik altında ({_usd(market.quote_volume)})")
    elif market.quote_volume >= 20_000_000:
        score += 12
        reasons.insert(0, "MEXC hacmi çok güçlü")
    elif market.quote_volume >= 5_000_000:
        score += 10
        reasons.insert(0, "MEXC hacmi güçlü")
    elif market.quote_volume >= 1_000_000:
        score += 8
        reasons.insert(0, "MEXC hacmi yeterli")
    else:
        score += 4
        reasons.insert(0, "MEXC'te işlem görüyor")

    gap = 0.0
    if pool.price > 0 and market.last_price > 0:
        gap = abs(pool.price / market.last_price - 1.0) * 100.0
        if gap <= 10:
            score += 8
            reasons.insert(1, "DEX/MEXC fiyatı uyumlu")
        elif gap <= 25:
            score += 4
            reasons.insert(1, "DEX/MEXC fiyatı yakın")
        elif gap > filter_config.max_price_gap_pct:
            blocked = True
            risks.append(f"sembol/fiyat eşleşmesi şüpheli (%{gap:.0f} fark)")
        else:
            score -= 4
            risks.append(f"DEX/MEXC fiyat farkı %{gap:.0f}")
    else:
        blocked = True
        risks.append("DEX/MEXC fiyat teyidi eksik")

    score_int = int(round(max(0, min(100, score))))
    return MexcCandidate(
        candidate=base,
        mexc_pair=market.pair,
        mexc_price=market.last_price,
        mexc_change_h24=market.change_h24,
        mexc_quote_volume=market.quote_volume,
        mexc_price_gap_pct=round(gap, 2),
        score=score_int,
        stage=_stage(score_int, blocked),
        reasons=tuple(reasons[:8]),
        risks=tuple(risks[:8]),
        execution_eligible=False,
    )


def rank_mexc_pools(
    pools: Iterable[Pool],
    markets: dict[str, MexcMarket],
    radar_config: RadarConfig,
    filter_config: MexcFilterConfig,
    now: int | None = None,
) -> tuple[list[MexcCandidate], int]:
    best: dict[str, MexcCandidate] = {}
    excluded = 0
    for pool in pools:
        market = markets.get(pool.symbol.upper())
        if market is None:
            excluded += 1
            continue
        item = score_mexc_pool(pool, market, radar_config, filter_config, now)
        old = best.get(item.token)
        if old is None or (
            item.score,
            item.mexc_quote_volume,
            item.liquidity,
            item.volume_h1,
        ) > (
            old.score,
            old.mexc_quote_volume,
            old.liquidity,
            old.volume_h1,
        ):
            best[item.token] = item
    ranked = sorted(
        best.values(),
        key=lambda item: (
            item.stage != "BLOCKED",
            item.score,
            item.mexc_quote_volume,
            item.liquidity,
            item.volume_h1,
        ),
        reverse=True,
    )
    return ranked[: radar_config.top_n], excluded


def format_mexc_alert(
    items: Iterable[MexcCandidate],
    radar_config: RadarConfig,
    now: int | None = None,
    startup: bool = False,
) -> str:
    items = list(items)
    stamp = datetime.fromtimestamp(
        now or time.time(), tz=timezone.utc
    ).astimezone().strftime("%d.%m %H:%M")
    title = (
        "🟢 Robinhood × MEXC Radar devrede"
        if startup
        else "🏹 Robinhood × MEXC fırsat alarmı"
    )
    lines = [f"<b>{title}</b>", f"Tarama: {stamp}"]
    if not items:
        lines.append("Robinhood Chain ve MEXC Spot kesişiminde uygun aday yok.")
    for index, item in enumerate(items[: radar_config.top_n], 1):
        source = item.candidate
        mexc_url = f"https://www.mexc.com/exchange/{source.symbol}_USDT"
        pool_url = (
            f"https://www.geckoterminal.com/{radar_config.network_id}/pools/"
            f"{source.pool}"
        )
        token_url = f"{radar_config.explorer_url}/token/{source.token}"
        lines += [
            "",
            f"<b>{index}. {html.escape(item.mexc_pair)} · {item.stage} · {item.score}/100</b>",
            f"MEXC fiyat ${item.mexc_price:.8g} · 24s hacim {_usd(item.mexc_quote_volume)} · 24s %{item.mexc_change_h24:+.1f}",
            f"Chain likidite {_usd(source.liquidity)} · 1s hacim {_usd(source.volume_h1)} · Al/Sat {source.buys_h1}/{source.sells_h1}",
            f"Yaş {source.age_hours:.1f}s · Chain 1s %{source.change_h1:+.1f} · fiyat farkı %{item.mexc_price_gap_pct:.1f}",
            "Güç: " + html.escape(", ".join(item.reasons[:3]) or "veri oluşuyor"),
        ]
        if item.risks:
            lines.append("Risk: " + html.escape(", ".join(item.risks[:2])))
        lines.append(
            f'<a href="{html.escape(mexc_url)}">MEXC</a> · '
            f'<a href="{html.escape(pool_url)}">Havuz</a> · '
            f'<a href="{html.escape(token_url)}">Kontrat</a>'
        )
    lines += [
        "",
        "⚠️ <b>Gölge radar:</b> yalnız aktif MEXC USDT Spot sembolleri gösterilir; işlem açmaz.",
        "Sembol eşleşmesi fiyat farkıyla kontrol edilir ancak kontrat kimliği garanti edilmez.",
    ]
    return "\n".join(lines)[:4090]


class MexcRadarService:
    def __init__(
        self,
        radar_config: RadarConfig | None = None,
        filter_config: MexcFilterConfig | None = None,
        *,
        gecko_client: GeckoClient | None = None,
        mexc_client: MexcClient | None = None,
        state: State | None = None,
    ):
        self.radar_config = radar_config or RadarConfig.from_env()
        self.filter_config = filter_config or MexcFilterConfig.from_env()
        self.gecko = gecko_client or GeckoClient(self.radar_config)
        self.mexc = mexc_client or MexcClient(
            self.filter_config,
            timeout=self.radar_config.timeout,
            session=self.gecko.session,
        )
        self.state = state or State(self.radar_config.state_file)
        self.rpc_chain_id: int | None = None
        self.rpc_checked = False

    def scan_once(self, now: int | None = None) -> dict[str, Any]:
        now = int(now or time.time())
        errors: list[str] = []
        if not self.rpc_checked:
            self.rpc_chain_id = self.gecko.chain_id()
            self.rpc_checked = True
            if self.rpc_chain_id not in (None, CHAIN_ID):
                errors.append(f"RPC_CHAIN_ID_MISMATCH:{self.rpc_chain_id}")

        try:
            pools = self.gecko.pools()
        except Exception as exc:
            pools = []
            errors.append(f"GECKOTERMINAL:{type(exc).__name__}")
            log.warning("Robinhood pool scan failed: %s", exc)

        candidates: list[MexcCandidate] = []
        excluded = 0
        if pools:
            try:
                markets = self.mexc.markets()
                candidates, excluded = rank_mexc_pools(
                    pools, markets, self.radar_config, self.filter_config, now
                )
            except Exception as exc:
                errors.append(f"MEXC_SPOT:{type(exc).__name__}")
                log.warning("MEXC filter failed; radar failed closed: %s", exc)
                if not self.filter_config.required:
                    markets = {
                        pool.symbol: MexcMarket(
                            pair=f"{pool.symbol}USDT",
                            base=pool.symbol,
                            last_price=pool.price,
                            change_h24=0,
                            quote_volume=0,
                        )
                        for pool in pools
                    }
                    candidates, excluded = rank_mexc_pools(
                        pools, markets, self.radar_config, self.filter_config, now
                    )

        first = self.state.first_scan
        alerts = self.state.alerts(candidates, self.radar_config, now)
        matched_count = max(0, len(pools) - excluded)
        self.state.data.update(
            {
                "version": 2,
                "network_id": self.radar_config.network_id,
                "last_errors": errors,
                "raw_pool_count": len(pools),
                "mexc_matched_count": matched_count,
                "excluded_not_on_mexc": excluded,
            }
        )
        self.state.save()
        if alerts:
            self.send(format_mexc_alert(alerts, self.radar_config, now))
        elif first and self.radar_config.startup_report:
            self.send(
                format_mexc_alert(
                    [item for item in candidates if item.stage != "BLOCKED"][:5],
                    self.radar_config,
                    now,
                    True,
                )
            )
        return {
            "generated_at": now,
            "network_id": self.radar_config.network_id,
            "rpc_chain_id": self.rpc_chain_id,
            "raw_pool_count": len(pools),
            "mexc_matched_count": matched_count,
            "excluded_not_on_mexc": excluded,
            "candidates": [item.to_dict() for item in candidates],
            "alerts": [item.to_dict() for item in alerts],
            "errors": errors,
            "can_authorize_trade": False,
        }

    def send(self, text: str) -> bool:
        if not self.radar_config.token or not self.radar_config.chat_id:
            return False
        try:
            response = self.gecko.session.post(
                f"https://api.telegram.org/bot{self.radar_config.token}/sendMessage",
                json={
                    "chat_id": self.radar_config.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=self.radar_config.timeout,
            )
            response.raise_for_status()
            return True
        except Exception as exc:
            log.warning("Robinhood x MEXC Telegram alert failed: %s", exc)
            return False

    def run(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        log.info(
            "Robinhood x MEXC radar started | interval=%ss min_score=%s min_volume=%s",
            self.radar_config.interval,
            self.radar_config.min_alert_score,
            self.filter_config.min_quote_volume,
        )
        while not stop.is_set():
            try:
                self.scan_once()
            except Exception as exc:
                log.exception("Robinhood x MEXC radar unexpected failure: %s", exc)
            stop.wait(self.radar_config.interval)


_LOCK = threading.RLock()
_THREAD: threading.Thread | None = None


def start_background_radar(
    radar_config: RadarConfig | None = None,
    filter_config: MexcFilterConfig | None = None,
) -> threading.Thread | None:
    global _THREAD
    radar_config = radar_config or RadarConfig.from_env()
    filter_config = filter_config or MexcFilterConfig.from_env()
    if not radar_config.enabled or not radar_config.token or not radar_config.chat_id:
        return None
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return _THREAD
        _THREAD = threading.Thread(
            target=MexcRadarService(radar_config, filter_config).run,
            name="robinhood-mexc-radar",
            daemon=True,
        )
        _THREAD.start()
        return _THREAD
