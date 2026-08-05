from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from acce_unified.config import UnifiedConfig
from acce_unified.models import CexTicker
from acce_unified.observation_archive import (
    LiquidObservationArchive,
    LiquidScanObservation,
    ObservationArchivingCexProvider,
)


UTC = timezone.utc


def _ticker(
    symbol: str,
    volume: float,
    *,
    change: float = 2.0,
    spread_bps: float = 10.0,
) -> CexTicker:
    price = 10.0
    half = spread_bps / 20_000.0
    return CexTicker(
        symbol=symbol,
        last_price=price,
        change_pct=change,
        quote_volume=volume,
        venue="MEXC",
        bid_price=price * (1.0 - half),
        ask_price=price * (1.0 + half),
        high_price=11.0,
        low_price=9.0,
    )


def _ready_metrics(**overrides):
    payload = {
        "status": "READY",
        "last_close": 10.0,
        "ema20": 9.8,
        "ema50": 9.5,
        "ema20_slope_pct": 0.3,
        "ema20_distance_pct": 2.0,
        "change_1h_pct": 1.0,
        "change_4h_pct": 3.0,
        "rsi14": 58.0,
        "atr_pct": 2.0,
        "volume_ratio": 2.0,
        "range_position_pct": 72.0,
        "drawdown_from_high_pct": -5.0,
        "can_authorize_trade": False,
    }
    payload.update(overrides)
    return payload


class _Clock:
    def __init__(self, *values: datetime):
        self.values = iter(values)

    def __call__(self) -> datetime:
        return next(self.values)


class _Provider:
    def __init__(self, tickers, metrics, *, fail_metrics: bool = False):
        self.tickers = list(tickers)
        self.metrics = dict(metrics)
        self.fail_metrics = fail_metrics
        self.requested = []

    def fetch_tickers(self):
        return list(self.tickers)

    def fetch_long_metrics(self, symbols):
        self.requested = list(symbols)
        if self.fail_metrics:
            raise RuntimeError("kline outage")
        return {
            symbol: dict(self.metrics[symbol])
            for symbol in self.requested
            if symbol in self.metrics
        }


def _config() -> UnifiedConfig:
    return UnifiedConfig(
        cex_enabled=True,
        listing_enabled=False,
        fundamental_enabled=False,
        social_enabled=False,
        liquid_universe_size=3,
        liquid_min_quote_volume=0,
        liquid_min_long_score=64,
    )


def test_archive_keeps_complete_universe_rejections_and_explicit_missingness():
    t0 = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    archive = LiquidObservationArchive()
    provider = _Provider(
        [
            _ticker("AAAUSDT", 30_000_000),
            _ticker("BBBUSDT", 20_000_000),
            _ticker("CCCUSDT", 10_000_000),
        ],
        {
            "AAAUSDT": _ready_metrics(),
            "BBBUSDT": _ready_metrics(rsi14=80.0),
        },
    )
    wrapped = ObservationArchivingCexProvider(
        provider,
        archive,
        _config(),
        {"AAAUSDT": "CORE"},
        clock=_Clock(t0, t0 + timedelta(seconds=2), t0 + timedelta(seconds=3)),
        id_factory=lambda: "scan-1",
    )

    wrapped.fetch_tickers()
    wrapped.fetch_long_metrics(["AAAUSDT", "BBBUSDT"])

    scan = archive.get_scan("scan-1")
    rows = archive.list_scan("scan-1")
    assert scan is not None
    assert scan["universe_size"] == 3
    assert [row["symbol"] for row in rows] == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    assert [row["liquidity_rank"] for row in rows] == [1, 2, 3]

    aaa, bbb, ccc = rows
    assert aaa["metrics_status"] == "READY"
    assert aaa["technical_score"] is not None
    assert aaa["in_trade_universe"] == 1
    assert bbb["evaluation_status"] == "BLOCKED"
    assert bbb["technical_score"] is None
    assert any("RSI" in value for value in json.loads(bbb["blockers_json"]))
    assert ccc["metrics_status"] == "NOT_ENRICHED"
    assert ccc["metrics_json"] is None
    assert ccc["technical_score"] is None
    assert ccc["enrichment_requested"] == 0


def test_provider_failure_is_archived_and_does_not_fabricate_zero_features():
    t0 = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    archive = LiquidObservationArchive()
    provider = _Provider(
        [_ticker("AAAUSDT", 30_000_000), _ticker("BBBUSDT", 20_000_000)],
        {},
        fail_metrics=True,
    )
    wrapped = ObservationArchivingCexProvider(
        provider,
        archive,
        _config(),
        {},
        clock=_Clock(t0, t0 + timedelta(seconds=1), t0 + timedelta(seconds=2)),
        id_factory=lambda: "scan-outage",
    )

    wrapped.fetch_tickers()
    with pytest.raises(RuntimeError, match="kline outage"):
        wrapped.fetch_long_metrics(["AAAUSDT", "BBBUSDT"])

    rows = archive.list_scan("scan-outage")
    assert len(rows) == 2
    assert {row["metrics_status"] for row in rows} == {"PROVIDER_ERROR"}
    assert all(row["technical_score"] is None for row in rows)
    assert all(row["rsi14"] is None for row in rows)
    assert all(row["metrics_json"] is None for row in rows)


def test_scan_timestamps_are_ordered_and_as_of_query_cannot_see_future_scan():
    t0 = datetime(2026, 8, 1, 2, 0, tzinfo=UTC)
    archive = LiquidObservationArchive()
    provider = _Provider(
        [_ticker("AAAUSDT", 30_000_000)],
        {"AAAUSDT": _ready_metrics()},
    )
    wrapped = ObservationArchivingCexProvider(
        provider,
        archive,
        _config(),
        {},
        clock=_Clock(t0, t0 + timedelta(seconds=4), t0 + timedelta(seconds=5)),
        id_factory=lambda: "scan-time",
    )

    wrapped.fetch_tickers()
    wrapped.fetch_long_metrics(["AAAUSDT"])

    scan = archive.get_scan("scan-time")
    assert scan is not None
    assert scan["event_time"] <= scan["available_time"] <= scan["ingested_time"]
    assert archive.latest_scan_as_of(t0 + timedelta(seconds=3)) is None
    visible = archive.latest_scan_as_of(t0 + timedelta(seconds=4))
    assert visible is not None and visible["scan_id"] == "scan-time"
    observations = archive.latest_observations_as_of(t0 + timedelta(seconds=4))
    assert observations[0]["symbol"] == "AAAUSDT"


def test_archive_rejects_duplicate_scan_and_database_mutation():
    t0 = datetime(2026, 8, 1, 3, 0, tzinfo=UTC)
    archive = LiquidObservationArchive()
    provider = _Provider(
        [_ticker("AAAUSDT", 30_000_000)],
        {"AAAUSDT": _ready_metrics()},
    )
    wrapped = ObservationArchivingCexProvider(
        provider,
        archive,
        _config(),
        {},
        clock=_Clock(t0, t0, t0),
        id_factory=lambda: "immutable-scan",
    )
    wrapped.fetch_tickers()
    wrapped.fetch_long_metrics(["AAAUSDT"])

    scan_row = archive.get_scan("immutable-scan")
    symbol_rows = archive.list_scan("immutable-scan")
    assert scan_row is not None and len(symbol_rows) == 1

    duplicate_scan = LiquidScanObservation(
        scan_id="immutable-scan",
        event_time=t0,
        available_time=t0,
        ingested_time=t0,
        venue="MEXC",
        raw_ticker_count=0,
        universe_size=0,
        market_context={},
        configuration={},
    )
    with pytest.raises(sqlite3.IntegrityError):
        archive.append_scan(duplicate_scan, [])
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        archive.connection.execute(
            "UPDATE liquid_scans SET venue = 'OTHER' WHERE scan_id = 'immutable-scan'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        archive.connection.execute(
            "DELETE FROM liquid_symbol_observations WHERE scan_id = 'immutable-scan'"
        )


def test_scan_contract_rejects_time_travel():
    t0 = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="event_time"):
        LiquidScanObservation(
            scan_id="future",
            event_time=t0 + timedelta(seconds=1),
            available_time=t0,
            ingested_time=t0 + timedelta(seconds=2),
            venue="MEXC",
            raw_ticker_count=0,
            universe_size=0,
            market_context={},
            configuration={},
        )
