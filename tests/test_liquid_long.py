from __future__ import annotations

from acce_unified import (
    DEFAULT_TRADE_UNIVERSE,
    UnifiedConfig,
    UnifiedRadarEngine,
    format_liquid_long_report,
)
from acce_unified.liquid_long import (
    build_market_context,
    calculate_long_metrics,
    rank_liquid_longs,
    select_liquid_universe,
)
from acce_unified.models import CexTicker


def ready_metrics() -> dict:
    return {
        "status": "READY",
        "last_close": 110.0,
        "ema20": 105.0,
        "ema50": 100.0,
        "ema20_slope_pct": 0.5,
        "ema20_distance_pct": 2.0,
        "change_1h_pct": 1.2,
        "change_4h_pct": 4.0,
        "rsi14": 58.0,
        "atr_pct": 2.0,
        "volume_ratio": 2.0,
        "range_position_pct": 72.0,
        "drawdown_from_high_pct": -5.0,
        "can_authorize_trade": False,
    }


def ready_fundamentals() -> dict:
    return {
        "status": "READY",
        "stage": "STRONG",
        "fundamental_score": 88,
        "circulating_supply": 800_000_000,
        "total_supply": 1_000_000_000,
        "max_supply": 1_000_000_000,
        "circulation_pct": 80.0,
        "market_cap_to_fdv_pct": 80.0,
        "volume_to_market_cap_pct": 10.0,
        "market_cap_usd": 2_000_000_000,
        "fully_diluted_valuation_usd": 2_500_000_000,
    }


def ticker(
    symbol: str,
    *,
    change: float = 8.0,
    volume: float = 100_000_000,
) -> CexTicker:
    return CexTicker(
        symbol=symbol,
        last_price=110.0,
        change_pct=change,
        quote_volume=volume,
        venue="MEXC",
        bid_price=109.99,
        ask_price=110.01,
    )


def test_closed_candle_metrics_are_calculated_from_15m_rows():
    rows = []
    for index in range(97):
        close = 100 + index * 0.1 + (0.15 if index % 3 else -0.1)
        rows.append([
            index,
            close - 0.1,
            close + 0.3,
            close - 0.3,
            close,
            100 + index,
            index + 1,
            10_000 + index * 100,
        ])

    metrics = calculate_long_metrics(rows)

    assert metrics["status"] == "READY"
    assert metrics["completed_candles"] == 96
    assert metrics["ema20"] > metrics["ema50"]
    assert metrics["can_authorize_trade"] is False


def test_liquid_universe_is_mexc_only_and_excludes_stables_and_leverage():
    rows = [
        ticker("BTCUSDT", volume=500_000_000),
        ticker("ETHUSDT", volume=400_000_000),
        ticker("USDCUSDT", volume=900_000_000),
        ticker("BTC3LUSDT", volume=800_000_000),
        CexTicker("SOLUSDT", 1, 1, 700_000_000, venue="BINANCE"),
    ]

    selected = select_liquid_universe(
        rows,
        size=100,
        required_venue="MEXC",
    )

    assert [row.symbol for row in selected] == ["BTCUSDT", "ETHUSDT"]


def test_large_daily_loser_is_hard_blocked_even_with_perfect_other_metrics():
    rows = [ticker("BTCUSDT", change=1), ticker("DROPUSDT", change=-12)]
    result = rank_liquid_longs(
        rows,
        {row.symbol: ready_metrics() for row in rows},
        {row.symbol: ready_fundamentals() for row in rows},
        market_context=build_market_context(rows),
        trade_universe=DEFAULT_TRADE_UNIVERSE,
        min_score=1,
    )

    assert "DROPUSDT" not in {row.symbol for row in result}


def test_missing_supply_cannot_enter_long_top_five():
    row = ticker("BTCUSDT", change=1)
    missing_supply = {**ready_fundamentals(), "circulating_supply": None}

    result = rank_liquid_longs(
        [row],
        {row.symbol: ready_metrics()},
        {row.symbol: missing_supply},
        market_context={
            "regime": "RISK_ON",
            "regime_score": 100,
            "positive_breadth_pct": 70,
            "btc_change_pct": 1,
        },
        trade_universe=DEFAULT_TRADE_UNIVERSE,
        min_score=1,
    )

    assert result == []


def test_missing_spread_cannot_receive_a_false_liquidity_advantage():
    row = CexTicker(
        symbol="BTCUSDT",
        last_price=110,
        change_pct=1,
        quote_volume=900_000_000,
        venue="MEXC",
    )

    result = rank_liquid_longs(
        [row],
        {row.symbol: ready_metrics()},
        {row.symbol: ready_fundamentals()},
        market_context={"regime": "RISK_ON", "regime_score": 100},
        trade_universe=DEFAULT_TRADE_UNIVERSE,
        min_score=1,
    )

    assert result == []


def test_ranking_is_bounded_to_five_and_carries_raw_supply():
    rows = [ticker(f"COIN{index}USDT", volume=200_000_000 - index) for index in range(8)]
    context = {
        "regime": "RISK_ON",
        "regime_score": 100,
        "positive_breadth_pct": 75,
        "btc_change_pct": 1,
    }

    result = rank_liquid_longs(
        rows,
        {row.symbol: ready_metrics() for row in rows},
        {row.symbol: ready_fundamentals() for row in rows},
        market_context=context,
        trade_universe=DEFAULT_TRADE_UNIVERSE,
        top_n=5,
        min_score=1,
    )

    assert len(result) == 5
    assert result[0].metadata["fundamentals"]["circulating_supply"] == 800_000_000
    assert all(row.execution_eligible is False for row in result)


class FakeLiquidProvider:
    def fetch_tickers(self):
        return [
            ticker("BTCUSDT", change=1, volume=900_000_000),
            ticker("ETHUSDT", change=4, volume=800_000_000),
            ticker("SOLUSDT", change=6, volume=700_000_000),
            ticker("LINKUSDT", change=5, volume=600_000_000),
            ticker("AVAXUSDT", change=7, volume=500_000_000),
            ticker("XRPUSDT", change=3, volume=400_000_000),
            ticker("DROPUSDT", change=-15, volume=300_000_000),
        ]

    def fetch_long_metrics(self, symbols):
        return {symbol: ready_metrics() for symbol in symbols}


class FakeFundamentalProvider:
    def fetch_many(self, listings):
        return {item.pair: ready_fundamentals() for item in listings}


def test_engine_builds_separate_mexc_liquid_top_five():
    config = UnifiedConfig(
        listing_enabled=False,
        social_enabled=False,
        liquid_min_long_score=1,
        liquid_long_top_n=5,
    )
    snapshot = UnifiedRadarEngine(
        config,
        DEFAULT_TRADE_UNIVERSE,
        cex_provider=FakeLiquidProvider(),
        fundamental_provider=FakeFundamentalProvider(),
    ).scan_once(now=1_700_000_000)

    assert snapshot.liquid_universe_size == 7
    assert snapshot.liquid_enriched_size == 6
    assert snapshot.liquid_supply_ready_size == 6
    assert snapshot.liquid_market_context["regime"] == "RISK_ON"
    assert len(snapshot.liquid_long_candidates) == 5
    assert "DROPUSDT" not in {row.symbol for row in snapshot.liquid_long_candidates}
    report = format_liquid_long_report(snapshot)
    assert "MEXC LİKİT 100" in report
    assert "dolaşan 800.00M" in report
    assert "otomatik emir oluşturmaz" in report
