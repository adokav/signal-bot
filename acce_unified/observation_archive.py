"""Append-only point-in-time observations for the MEXC Liquid-100 universe.

The archive records the whole decision surface, including rejected and
unevaluated symbols. It is deliberately separated from ranking and execution:
archive failures are isolated and can never authorize, suppress or alter a
trade decision.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from .config import UnifiedConfig
from .engine import UnifiedRadarEngine as CoreUnifiedRadarEngine
from .liquid_long import (
    build_market_context,
    score_technical_long,
    select_liquid_universe,
    ticker_spread_bps,
)
from .models import CexTicker
from .providers import MexcPublicProvider


log = logging.getLogger(__name__)
UTCClock = Callable[[], datetime]
IdFactory = Callable[[], str]


@dataclass(frozen=True)
class LiquidScanObservation:
    scan_id: str
    event_time: datetime
    available_time: datetime
    ingested_time: datetime
    venue: str
    raw_ticker_count: int
    universe_size: int
    market_context: Mapping[str, Any]
    configuration: Mapping[str, Any]
    schema_version: str = "liquid-observation-v1"

    def __post_init__(self) -> None:
        for name in ("event_time", "available_time", "ingested_time"):
            _require_aware(getattr(self, name), name)
        if not self.scan_id.strip():
            raise ValueError("scan_id is required")
        if self.event_time > self.available_time:
            raise ValueError("event_time cannot be after available_time")
        if self.available_time > self.ingested_time:
            raise ValueError("available_time cannot be after ingested_time")
        if self.raw_ticker_count < 0 or self.universe_size < 0:
            raise ValueError("scan counts cannot be negative")

    def to_record(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "event_time": _iso(self.event_time),
            "available_time": _iso(self.available_time),
            "ingested_time": _iso(self.ingested_time),
            "venue": self.venue,
            "raw_ticker_count": self.raw_ticker_count,
            "universe_size": self.universe_size,
            "market_context_json": _json(dict(self.market_context)),
            "configuration_json": _json(dict(self.configuration)),
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class LiquidSymbolObservation:
    scan_id: str
    symbol: str
    liquidity_rank: int
    last_price: float
    change_pct: float
    quote_volume: float
    bid_price: float | None
    ask_price: float | None
    spread_bps: float | None
    high_price: float | None
    low_price: float | None
    in_trade_universe: bool
    enrichment_requested: bool
    metrics_status: str
    evaluation_status: str
    technical_score: int | None
    technical_passed: bool
    blockers: tuple[str, ...]
    metrics: Mapping[str, Any] | None
    technical: Mapping[str, Any]
    ticker: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.scan_id.strip() or not self.symbol.strip():
            raise ValueError("scan_id and symbol are required")
        if self.liquidity_rank <= 0:
            raise ValueError("liquidity_rank must be positive")
        if self.last_price <= 0 or self.quote_volume < 0:
            raise ValueError("invalid ticker values")
        if self.technical_score is not None and not 0 <= self.technical_score <= 100:
            raise ValueError("technical_score must be between 0 and 100")
        if self.technical_passed and self.technical_score is None:
            raise ValueError("technical_passed requires a real technical_score")

    def to_record(self) -> dict[str, Any]:
        metrics = dict(self.metrics) if self.metrics is not None else None
        return {
            "scan_id": self.scan_id,
            "symbol": self.symbol,
            "liquidity_rank": self.liquidity_rank,
            "last_price": self.last_price,
            "change_pct": self.change_pct,
            "quote_volume": self.quote_volume,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "spread_bps": self.spread_bps,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "in_trade_universe": int(self.in_trade_universe),
            "enrichment_requested": int(self.enrichment_requested),
            "metrics_status": self.metrics_status,
            "evaluation_status": self.evaluation_status,
            "technical_score": self.technical_score,
            "technical_passed": int(self.technical_passed),
            "change_1h_pct": _metric(metrics, "change_1h_pct"),
            "change_4h_pct": _metric(metrics, "change_4h_pct"),
            "rsi14": _metric(metrics, "rsi14"),
            "atr_pct": _metric(metrics, "atr_pct"),
            "ema20_distance_pct": _metric(metrics, "ema20_distance_pct"),
            "ema20_slope_pct": _metric(metrics, "ema20_slope_pct"),
            "volume_ratio": _metric(metrics, "volume_ratio"),
            "range_position_pct": _metric(metrics, "range_position_pct"),
            "drawdown_from_high_pct": _metric(metrics, "drawdown_from_high_pct"),
            "blockers_json": _json(list(self.blockers)),
            "metrics_json": _json(metrics) if metrics is not None else None,
            "technical_json": _json(dict(self.technical)),
            "ticker_json": _json(dict(self.ticker)),
        }


class LiquidObservationArchive:
    """Append-only SQLite store for point-in-time Liquid-100 observations."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        path_text = str(path)
        if path_text != ":memory:":
            Path(path_text).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path_text, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def _migrate(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS liquid_scans (
                    scan_id TEXT PRIMARY KEY,
                    event_time TEXT NOT NULL,
                    available_time TEXT NOT NULL,
                    ingested_time TEXT NOT NULL,
                    venue TEXT NOT NULL,
                    raw_ticker_count INTEGER NOT NULL,
                    universe_size INTEGER NOT NULL,
                    market_context_json TEXT NOT NULL,
                    configuration_json TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_liquid_scans_available
                    ON liquid_scans(available_time, scan_id);
                CREATE TABLE IF NOT EXISTS liquid_symbol_observations (
                    scan_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    liquidity_rank INTEGER NOT NULL,
                    last_price REAL NOT NULL,
                    change_pct REAL NOT NULL,
                    quote_volume REAL NOT NULL,
                    bid_price REAL,
                    ask_price REAL,
                    spread_bps REAL,
                    high_price REAL,
                    low_price REAL,
                    in_trade_universe INTEGER NOT NULL,
                    enrichment_requested INTEGER NOT NULL,
                    metrics_status TEXT NOT NULL,
                    evaluation_status TEXT NOT NULL,
                    technical_score INTEGER,
                    technical_passed INTEGER NOT NULL,
                    change_1h_pct REAL,
                    change_4h_pct REAL,
                    rsi14 REAL,
                    atr_pct REAL,
                    ema20_distance_pct REAL,
                    ema20_slope_pct REAL,
                    volume_ratio REAL,
                    range_position_pct REAL,
                    drawdown_from_high_pct REAL,
                    blockers_json TEXT NOT NULL,
                    metrics_json TEXT,
                    technical_json TEXT NOT NULL,
                    ticker_json TEXT NOT NULL,
                    PRIMARY KEY(scan_id, symbol),
                    FOREIGN KEY(scan_id) REFERENCES liquid_scans(scan_id)
                );
                CREATE INDEX IF NOT EXISTS idx_liquid_symbols_symbol_time
                    ON liquid_symbol_observations(symbol, scan_id);
                CREATE INDEX IF NOT EXISTS idx_liquid_symbols_rank
                    ON liquid_symbol_observations(scan_id, liquidity_rank, symbol);
                CREATE TRIGGER IF NOT EXISTS liquid_scans_no_update
                BEFORE UPDATE ON liquid_scans BEGIN
                    SELECT RAISE(ABORT, 'liquid observation archive is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS liquid_scans_no_delete
                BEFORE DELETE ON liquid_scans BEGIN
                    SELECT RAISE(ABORT, 'liquid observation archive is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS liquid_symbols_no_update
                BEFORE UPDATE ON liquid_symbol_observations BEGIN
                    SELECT RAISE(ABORT, 'liquid observation archive is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS liquid_symbols_no_delete
                BEFORE DELETE ON liquid_symbol_observations BEGIN
                    SELECT RAISE(ABORT, 'liquid observation archive is append-only');
                END;
                """
            )
            self.connection.commit()

    def append_scan(
        self,
        scan: LiquidScanObservation,
        observations: Iterable[LiquidSymbolObservation],
    ) -> None:
        rows = list(observations)
        if len(rows) != scan.universe_size:
            raise ValueError("observation count must equal scan universe_size")
        if any(row.scan_id != scan.scan_id for row in rows):
            raise ValueError("all observations must belong to the scan")
        symbols = [row.symbol for row in rows]
        if len(symbols) != len(set(symbols)):
            raise ValueError("duplicate symbols inside one scan are forbidden")
        ordered = sorted(rows, key=lambda row: (row.liquidity_rank, row.symbol))
        with self._lock, self.connection:
            self._insert("liquid_scans", scan.to_record())
            for row in ordered:
                self._insert("liquid_symbol_observations", row.to_record())

    def get_scan(self, scan_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM liquid_scans WHERE scan_id = ?", (scan_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list_scan(self, scan_id: str) -> list[Mapping[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM liquid_symbol_observations WHERE scan_id = ? "
                "ORDER BY liquidity_rank, symbol",
                (scan_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_scan_as_of(self, as_of: datetime) -> Mapping[str, Any] | None:
        _require_aware(as_of, "as_of")
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM liquid_scans WHERE available_time <= ? "
                "ORDER BY available_time DESC, scan_id DESC LIMIT 1",
                (_iso(as_of),),
            ).fetchone()
        return dict(row) if row is not None else None

    def latest_observations_as_of(self, as_of: datetime) -> list[Mapping[str, Any]]:
        scan = self.latest_scan_as_of(as_of)
        return self.list_scan(str(scan["scan_id"])) if scan is not None else []

    def count_scans(self) -> int:
        with self._lock:
            row = self.connection.execute("SELECT COUNT(*) AS count FROM liquid_scans").fetchone()
        return int(row["count"] if row is not None else 0)

    def _insert(self, table: str, record: Mapping[str, Any]) -> None:
        columns = tuple(record)
        placeholders = ", ".join("?" for _ in columns)
        self.connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(record[column] for column in columns),
        )


@dataclass(frozen=True)
class _PendingScan:
    scan_id: str
    event_time: datetime
    tickers: tuple[CexTicker, ...]
    universe: tuple[CexTicker, ...]
    market_context: Mapping[str, Any]


class ObservationArchivingCexProvider:
    """Provider decorator that archives facts without changing returned data."""

    def __init__(
        self,
        provider: Any,
        archive: LiquidObservationArchive,
        config: UnifiedConfig,
        trade_universe: Mapping[str, str],
        *,
        clock: UTCClock | None = None,
        id_factory: IdFactory | None = None,
    ) -> None:
        self.provider = provider
        self.archive = archive
        self.config = config
        self.trade_universe = {str(key).upper(): value for key, value in trade_universe.items()}
        self.clock = clock or _utcnow
        self.id_factory = id_factory or (lambda: uuid4().hex)
        self._pending: _PendingScan | None = None
        self._lock = threading.RLock()

    def fetch_tickers(self) -> list[CexTicker]:
        tickers = list(self.provider.fetch_tickers())
        event_time = _coerce_clock(self.clock())
        universe = select_liquid_universe(
            tickers,
            size=self.config.liquid_universe_size,
            min_quote_volume=self.config.liquid_min_quote_volume,
            required_venue="MEXC",
        )
        pending = _PendingScan(
            scan_id=self.id_factory(),
            event_time=event_time,
            tickers=tuple(tickers),
            universe=tuple(universe),
            market_context=build_market_context(universe),
        )
        previous: _PendingScan | None
        with self._lock:
            previous = self._pending
            self._pending = pending
        if previous is not None:
            self._finalize(
                previous,
                metrics_by_symbol={},
                requested_symbols=set(),
                terminal_status="METRICS_NOT_REQUESTED",
            )
        return tickers

    def fetch_long_metrics(self, symbols: Any) -> dict[str, dict[str, Any]]:
        requested = {
            str(symbol or "").strip().upper()
            for symbol in (symbols or [])
            if str(symbol or "").strip()
        }
        try:
            result = self.provider.fetch_long_metrics(symbols)
        except Exception:
            pending = self._take_pending()
            if pending is not None:
                self._finalize(
                    pending,
                    metrics_by_symbol={},
                    requested_symbols=requested,
                    terminal_status="PROVIDER_ERROR",
                )
            raise
        pending = self._take_pending()
        if pending is not None:
            self._finalize(
                pending,
                metrics_by_symbol=result,
                requested_symbols=requested,
                terminal_status=None,
            )
        return result

    def _take_pending(self) -> _PendingScan | None:
        with self._lock:
            pending = self._pending
            self._pending = None
            return pending

    def _finalize(
        self,
        pending: _PendingScan,
        *,
        metrics_by_symbol: Mapping[str, Mapping[str, Any]],
        requested_symbols: set[str],
        terminal_status: str | None,
    ) -> None:
        available_time = max(pending.event_time, _coerce_clock(self.clock()))
        ingested_time = max(available_time, _coerce_clock(self.clock()))
        venue = pending.universe[0].venue if pending.universe else "MEXC"
        scan = LiquidScanObservation(
            scan_id=pending.scan_id,
            event_time=pending.event_time,
            available_time=available_time,
            ingested_time=ingested_time,
            venue=venue,
            raw_ticker_count=len(pending.tickers),
            universe_size=len(pending.universe),
            market_context=dict(pending.market_context),
            configuration={
                "liquid_universe_size": self.config.liquid_universe_size,
                "liquid_min_quote_volume": self.config.liquid_min_quote_volume,
                "liquid_max_24h_drawdown_pct": self.config.liquid_max_24h_drawdown_pct,
                "liquid_max_24h_gain_pct": self.config.liquid_max_24h_gain_pct,
                "liquid_max_spread_bps": self.config.liquid_max_spread_bps,
                "liquid_min_long_score": self.config.liquid_min_long_score,
                "technical_policy_version": "liquid-long-rules-v1",
            },
        )
        observations: list[LiquidSymbolObservation] = []
        for rank, ticker in enumerate(pending.universe, 1):
            symbol = ticker.symbol.upper()
            requested = symbol in requested_symbols
            raw_metrics = metrics_by_symbol.get(symbol)
            if not requested:
                metrics: Mapping[str, Any] | None = None
                metrics_status = "NOT_ENRICHED"
                technical: Mapping[str, Any] = {
                    "status": "NOT_EVALUATED",
                    "score": None,
                    "blockers": ["kapalı mum zenginleştirmesi istenmedi"],
                }
            elif raw_metrics is None:
                metrics = None
                metrics_status = terminal_status or "MISSING_RESPONSE"
                technical = {
                    "status": "BLOCKED",
                    "score": None,
                    "blockers": [f"mum metriği kullanılamıyor: {metrics_status}"],
                }
            else:
                metrics = dict(raw_metrics)
                metrics_status = str(metrics.get("status") or "UNKNOWN").upper()
                technical = score_technical_long(
                    ticker,
                    dict(metrics),
                    liquidity_rank=rank,
                    max_drawdown_pct=self.config.liquid_max_24h_drawdown_pct,
                    max_gain_pct=self.config.liquid_max_24h_gain_pct,
                    max_spread_bps=self.config.liquid_max_spread_bps,
                )
            evaluation_status = str(technical.get("status") or "UNKNOWN").upper()
            score_value = technical.get("score")
            technical_score = (
                int(score_value)
                if evaluation_status == "READY" and score_value is not None
                else None
            )
            blockers = tuple(str(value) for value in (technical.get("blockers") or ()))
            observations.append(
                LiquidSymbolObservation(
                    scan_id=pending.scan_id,
                    symbol=symbol,
                    liquidity_rank=rank,
                    last_price=float(ticker.last_price),
                    change_pct=float(ticker.change_pct),
                    quote_volume=float(ticker.quote_volume),
                    bid_price=_positive_or_none(ticker.bid_price),
                    ask_price=_positive_or_none(ticker.ask_price),
                    spread_bps=ticker_spread_bps(ticker),
                    high_price=_positive_or_none(ticker.high_price),
                    low_price=_positive_or_none(ticker.low_price),
                    in_trade_universe=symbol in self.trade_universe,
                    enrichment_requested=requested,
                    metrics_status=metrics_status,
                    evaluation_status=evaluation_status,
                    technical_score=technical_score,
                    technical_passed=bool(
                        technical_score is not None
                        and technical_score >= self.config.liquid_min_long_score
                    ),
                    blockers=blockers,
                    metrics=metrics,
                    technical=dict(technical),
                    ticker=asdict(ticker),
                )
            )
        try:
            self.archive.append_scan(scan, observations)
        except Exception:
            log.exception(
                "Liquid-100 observation archive failed; radar result is unchanged"
            )


class ObservationArchivingRadarEngine(CoreUnifiedRadarEngine):
    """Drop-in engine that enables the archive only when explicitly configured."""

    def __init__(
        self,
        config: UnifiedConfig,
        trade_universe: dict[str, str],
        *,
        cex_provider: Any | None = None,
        listing_provider: Any | None = None,
        social_provider: Any | None = None,
        fundamental_provider: Any | None = None,
    ) -> None:
        archive: LiquidObservationArchive | None = None
        provider = cex_provider
        if provider is None:
            provider = MexcPublicProvider(timeout=config.request_timeout_seconds)
            if _env_enabled("LIQUID_OBSERVATION_ARCHIVE_ENABLED", False):
                path = os.getenv(
                    "LIQUID_OBSERVATION_ARCHIVE_PATH",
                    "/data/liquid100_observations.sqlite3",
                ).strip() or "/data/liquid100_observations.sqlite3"
                try:
                    archive = LiquidObservationArchive(path)
                    provider = ObservationArchivingCexProvider(
                        provider,
                        archive,
                        config,
                        trade_universe,
                    )
                except Exception:
                    archive = None
                    log.exception(
                        "Liquid-100 observation archive could not start; radar continues"
                    )
        self.liquid_observation_archive = archive
        super().__init__(
            config,
            trade_universe,
            cex_provider=provider,
            listing_provider=listing_provider,
            social_provider=social_provider,
            fundamental_provider=fundamental_provider,
        )

    def close_observation_archive(self) -> None:
        if self.liquid_observation_archive is not None:
            self.liquid_observation_archive.close()
            self.liquid_observation_archive = None


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "evet"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_clock(value: datetime) -> datetime:
    _require_aware(value, "clock value")
    return value.astimezone(timezone.utc)


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _positive_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _metric(metrics: Mapping[str, Any] | None, name: str) -> float | None:
    if not metrics or str(metrics.get("status") or "").upper() != "READY":
        return None
    try:
        return float(metrics[name]) if metrics.get(name) is not None else None
    except (TypeError, ValueError):
        return None
