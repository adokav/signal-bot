"""Strict MEXC data boundary for the BTC/ETH tactical Long radar.

Unlike the broad market radar, this adapter never substitutes another venue.
Entry, stop and execution-quality decisions must use the intended MEXC market.
The adapter is read-only and deliberately fails closed on malformed, future or
stale observations. A provider may still return its current open kline even
when an endTime boundary is supplied; that row is deterministically discarded
at this ingestion boundary rather than being allowed into signal logic.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Mapping

import requests

from .tactical_long import Candle, DataQualityError, visible_closed_candles


class TacticalTimeframe(str, Enum):
    M5 = "5m"
    M15 = "15m"
    H1 = "60m"
    H4 = "4h"
    D1 = "1d"

    @property
    def seconds(self) -> int:
        return {
            TacticalTimeframe.M5: 300,
            TacticalTimeframe.M15: 900,
            TacticalTimeframe.H1: 3600,
            TacticalTimeframe.H4: 14_400,
            TacticalTimeframe.D1: 86_400,
        }[self]


REQUIRED_TIMEFRAMES = (
    TacticalTimeframe.M5,
    TacticalTimeframe.M15,
    TacticalTimeframe.H1,
    TacticalTimeframe.H4,
    TacticalTimeframe.D1,
)
TACTICAL_SYMBOLS = ("BTCUSDT", "ETHUSDT")
CONTEXT_SYMBOLS = ("ETHBTC",)


@dataclass(frozen=True)
class Quote:
    symbol: str
    observed_at: int
    available_at: int
    bid: float
    ask: float

    def __post_init__(self) -> None:
        symbol = self.symbol.upper()
        if symbol not in {*TACTICAL_SYMBOLS, *CONTEXT_SYMBOLS}:
            raise DataQualityError("unsupported tactical quote symbol")
        object.__setattr__(self, "symbol", symbol)
        if self.observed_at <= 0 or self.available_at < self.observed_at:
            raise DataQualityError("invalid quote timestamps")
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise DataQualityError("invalid bid/ask geometry")

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.midpoint * 10_000.0


@dataclass(frozen=True)
class TacticalMarketSnapshot:
    decision_at: int
    evidence_ingested_at: int
    candles: Mapping[str, Mapping[TacticalTimeframe, tuple[Candle, ...]]]
    quotes: Mapping[str, Quote]
    source: str = "MEXC_SPOT_V3"

    def __post_init__(self) -> None:
        if self.decision_at <= 0:
            raise DataQualityError("invalid snapshot decision timestamp")
        if self.evidence_ingested_at <= 0:
            raise DataQualityError("invalid snapshot evidence ingestion timestamp")
        required_symbols = {*TACTICAL_SYMBOLS, *CONTEXT_SYMBOLS}
        if set(self.candles) != required_symbols or set(self.quotes) != required_symbols:
            raise DataQualityError("snapshot must contain BTCUSDT, ETHUSDT and ETHBTC")
        latest_provider_availability = 0
        for symbol in required_symbols:
            frames = self.candles[symbol]
            if set(frames) != set(REQUIRED_TIMEFRAMES):
                raise DataQualityError(f"incomplete timeframe set for {symbol}")
            quote = self.quotes[symbol]
            if quote.available_at > self.decision_at:
                raise DataQualityError("future quote in snapshot")
            latest_provider_availability = max(latest_provider_availability, quote.available_at)
            for rows in frames.values():
                for row in rows:
                    if not row.visible_at(self.decision_at):
                        raise DataQualityError("future or open candle supplied")
                    latest_provider_availability = max(
                        latest_provider_availability,
                        row.available_at,
                    )
        if self.evidence_ingested_at < latest_provider_availability:
            raise DataQualityError("evidence ingestion cannot predate provider availability")


class MexcTacticalMarketData:
    """Fetch complete multi-timeframe BTC/ETH context from MEXC only."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.mexc.com",
        timeout_seconds: int = 15,
        candle_limit: int = 240,
        session: requests.Session | None = None,
    ) -> None:
        # One provider row is intentionally reserved for MEXC's permitted
        # forming-candle spillover. A limit of 61 therefore guarantees at least
        # 60 retained closed rows, which the medium-horizon volatility policy
        # requires after the open-row discard path.
        if candle_limit < 61 or candle_limit > 1000:
            raise ValueError("candle_limit must be between 61 and 1000")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.candle_limit = int(candle_limit)
        self.session = session or requests.Session()

    def _get_json(self, path: str, params: Mapping[str, Any]) -> Any:
        response = self.session.get(
            f"{self.base_url}{path}",
            params=dict(params),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def last_closed_end_time_ms(
        *, timeframe: TacticalTimeframe, decision_at: int
    ) -> int:
        """Return the final millisecond of the latest fully closed UTC bucket."""
        if decision_at <= 0:
            raise DataQualityError("invalid decision timestamp")
        bucket_start = (decision_at // timeframe.seconds) * timeframe.seconds
        return bucket_start * 1000 - 1

    @staticmethod
    def parse_kline_rows(
        rows: Any,
        *,
        timeframe: TacticalTimeframe,
        decision_at: int,
    ) -> tuple[Candle, ...]:
        if not isinstance(rows, list) or not rows:
            raise DataQualityError("MEXC kline payload is empty or malformed")
        parsed: list[Candle] = []
        discarded_open_rows = 0
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                raise DataQualityError("malformed MEXC kline row")
            try:
                open_time = int(row[0]) // 1000
                close_time = int(row[6]) // 1000
                candle = Candle(
                    open_time=open_time,
                    close_time=close_time,
                    available_at=close_time,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            except (TypeError, ValueError, IndexError) as exc:
                raise DataQualityError("invalid MEXC kline values") from exc

            # MEXC's REST kline response can include the current in-progress
            # bucket. It is provider transport noise, not valid research data.
            # Discard only rows that are provably still open at decision_at;
            # never alter their timestamps or promote them to closed candles.
            if not candle.visible_at(decision_at):
                discarded_open_rows += 1
                continue
            parsed.append(candle)

        if not parsed:
            raise DataQualityError("no closed candles in MEXC kline payload")
        if discarded_open_rows > 1:
            raise DataQualityError("multiple future or open candles supplied")

        max_staleness = timeframe.seconds * 2
        return visible_closed_candles(
            parsed,
            decision_at=decision_at,
            max_staleness_seconds=max_staleness,
        )

    def fetch_candles(
        self,
        symbol: str,
        timeframe: TacticalTimeframe,
        *,
        decision_at: int,
    ) -> tuple[Candle, ...]:
        symbol = symbol.upper()
        if symbol not in {*TACTICAL_SYMBOLS, *CONTEXT_SYMBOLS}:
            raise ValueError("unsupported tactical symbol")
        payload = self._get_json(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": timeframe.value,
                "limit": self.candle_limit,
                "endTime": self.last_closed_end_time_ms(
                    timeframe=timeframe,
                    decision_at=decision_at,
                ),
            },
        )
        return self.parse_kline_rows(
            payload,
            timeframe=timeframe,
            decision_at=decision_at,
        )

    def fetch_quote(self, symbol: str, *, decision_at: int) -> Quote:
        symbol = symbol.upper()
        payload = self._get_json("/api/v3/ticker/bookTicker", {"symbol": symbol})
        if not isinstance(payload, dict):
            raise DataQualityError("MEXC bookTicker payload is malformed")
        try:
            returned_symbol = str(payload.get("symbol") or symbol).upper()
            bid = float(payload["bidPrice"])
            ask = float(payload["askPrice"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataQualityError("invalid MEXC bookTicker values") from exc
        if returned_symbol != symbol:
            raise DataQualityError("MEXC bookTicker symbol mismatch")
        return Quote(
            symbol=symbol,
            observed_at=decision_at,
            available_at=decision_at,
            bid=bid,
            ask=ask,
        )

    def snapshot(self, *, decision_at: int | None = None) -> TacticalMarketSnapshot:
        explicit_cut = decision_at is not None
        query_cut = int(decision_at if explicit_cut else time.time())
        symbols = (*TACTICAL_SYMBOLS, *CONTEXT_SYMBOLS)
        candle_map: dict[str, dict[TacticalTimeframe, tuple[Candle, ...]]] = {}
        quote_map: dict[str, Quote] = {}
        for symbol in symbols:
            candle_map[symbol] = {
                timeframe: self.fetch_candles(
                    symbol,
                    timeframe,
                    decision_at=query_cut,
                )
                for timeframe in REQUIRED_TIMEFRAMES
            }
            quote_map[symbol] = self.fetch_quote(symbol, decision_at=query_cut)

        evidence_ingested_at = max(query_cut, int(time.time())) if not explicit_cut else int(time.time())
        # A live scan decides only after its complete evidence batch has been
        # collected. An explicit historical cut is preserved as requested, but
        # its actual ingestion timestamp remains current; medium-horizon replay
        # policy will therefore reject a newly downloaded historical revision
        # rather than backdating it into the old cut.
        final_decision_at = evidence_ingested_at if not explicit_cut else query_cut
        return TacticalMarketSnapshot(
            decision_at=final_decision_at,
            evidence_ingested_at=evidence_ingested_at,
            candles=candle_map,
            quotes=quote_map,
        )