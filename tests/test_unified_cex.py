from __future__ import annotations

from acce_unified.cex import opportunity_proxy, rank_cex_tickers
from acce_unified.config import DEFAULT_TRADE_UNIVERSE, build_trade_universe
from acce_unified.models import CexTicker


def test_default_trade_universe_is_the_reviewed_nine_assets():
    assert set(build_trade_universe("")) == {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "ONDOUSDT",
        "RENDERUSDT", "PYTHUSDT", "BONKUSDT", "POPCATUSDT",
    }


def test_cex_ranking_filters_stables_and_leveraged_tokens():
    rows = [
        CexTicker("BTCUSDT", 100_000, 4.0, 2_000_000_000),
        CexTicker("BTCUPUSDT", 10, 7.0, 50_000_000),
        CexTicker("USDCUSDT", 1, 0.01, 500_000_000),
        CexTicker("NEWUSDT", 2, 8.0, 12_000_000),
    ]
    ranked = rank_cex_tickers(rows, trade_universe=DEFAULT_TRADE_UNIVERSE, top_n=10)
    symbols = [item.symbol for item in ranked]
    assert "USDCUSDT" not in symbols
    assert "BTCUPUSDT" not in symbols
    assert {"BTCUSDT", "NEWUSDT"}.issubset(symbols)


def test_cex_candidates_cannot_authorize_trades():
    rows = [
        CexTicker("SOLUSDT", 150, 9.0, 800_000_000),
        CexTicker("NEWUSDT", 2, 10.0, 80_000_000),
    ]
    ranked = rank_cex_tickers(rows, trade_universe=DEFAULT_TRADE_UNIVERSE, top_n=10)
    by_symbol = {item.symbol: item for item in ranked}
    assert by_symbol["SOLUSDT"].role == "CORE_CONFLUENCE"
    assert by_symbol["NEWUSDT"].role == "DISCOVERY_ONLY"
    assert all(item.execution_eligible is False for item in ranked)
    assert any("otomatik işlem yasak" in flag for flag in by_symbol["NEWUSDT"].risk_flags)


def test_opportunity_proxy_penalises_late_pump():
    volume = 50_000_000
    assert opportunity_proxy(12.0, volume) > opportunity_proxy(65.0, volume)
