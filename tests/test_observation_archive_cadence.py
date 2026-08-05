from __future__ import annotations

from datetime import datetime, timedelta, timezone

from acce_unified.config import UnifiedConfig
from acce_unified.models import CexTicker
from acce_unified.observation_archive import LiquidObservationArchive
from acce_unified.observation_runtime import CadencedObservationArchivingCexProvider


class _Sequence:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


class _Provider:
    def __init__(self):
        self.ticker_calls = 0
        self.metric_calls = 0

    def fetch_tickers(self):
        self.ticker_calls += 1
        return [
            CexTicker(
                symbol="AAAUSDT",
                last_price=10.0,
                change_pct=2.0,
                quote_volume=10_000_000,
                venue="MEXC",
                bid_price=9.99,
                ask_price=10.01,
                high_price=11.0,
                low_price=9.0,
            )
        ]

    def fetch_long_metrics(self, symbols):
        self.metric_calls += 1
        return {
            "AAAUSDT": {
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
        }


def test_cadence_archives_only_due_scans_but_returns_fresh_data_each_time():
    t0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
    base = _Provider()
    archive = LiquidObservationArchive()
    provider = CadencedObservationArchivingCexProvider(
        base,
        archive,
        UnifiedConfig(
            listing_enabled=False,
            social_enabled=False,
            fundamental_enabled=False,
            liquid_universe_size=1,
            liquid_min_quote_volume=0,
        ),
        {},
        interval_seconds=60,
        schedule_clock=_Sequence(0.0, 10.0, 61.0),
        clock=_Sequence(
            t0,
            t0 + timedelta(seconds=1),
            t0 + timedelta(seconds=2),
            t0 + timedelta(seconds=61),
            t0 + timedelta(seconds=62),
            t0 + timedelta(seconds=63),
        ),
        id_factory=_Sequence("scan-1", "scan-2"),
    )

    for _ in range(3):
        assert provider.fetch_tickers()[0].symbol == "AAAUSDT"
        assert provider.fetch_long_metrics(["AAAUSDT"])["AAAUSDT"]["status"] == "READY"

    assert base.ticker_calls == 3
    assert base.metric_calls == 3
    assert archive.count_scans() == 2
    assert archive.get_scan("scan-1") is not None
    assert archive.get_scan("scan-2") is not None
