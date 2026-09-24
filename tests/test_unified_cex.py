from __future__ import annotations

from acce_unified.cex import opportunity_proxy
from acce_unified.config import build_trade_universe


def test_default_trade_universe_is_the_reviewed_nine_assets():
    assert set(build_trade_universe("")) == {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "ONDOUSDT",
        "RENDERUSDT", "PYTHUSDT", "BONKUSDT", "POPCATUSDT",
    }


def test_opportunity_proxy_penalises_late_pump():
    volume = 50_000_000
    assert opportunity_proxy(12.0, volume) > opportunity_proxy(65.0, volume)
