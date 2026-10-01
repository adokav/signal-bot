from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pandas")

from trading.backtest import carry_audit as ca
from trading.backtest import carry_replay as cr
from tests.test_carry_replay import _market


def test_audit_finds_a_broken_hedge_and_the_worst_period():
    perp, spot, funding = _market(3)
    # from period 110 the "perp" tracks a different asset: price doubles while spot does not
    for col in ("open", "high", "low", "close"):
        perp["S1USDT"][col] = np.where(np.arange(len(perp["S1USDT"][col])) >= 110,
                                       perp["S1USDT"][col] * 2, perp["S1USDT"][col])
    market = cr.build_market(perp, spot, funding)
    report = ca.audit(market, top=5)
    assert report["worst"][0]["symbol"] == "S1USDT"
    assert report["worst"][0]["perp_ret_pct"] > 90
    breaks = {b["symbol"]: b for b in report["ratio_breaks"]}
    assert "S1USDT" in breaks and "S0USDT" not in breaks
    assert report["can_authorize_trade"] is False


def test_audit_reports_extreme_funding():
    perp, spot, funding = _market(2, funding_rate=lambda k, s: -0.01 if (s == 0 and k == 120) else 0.0001)
    report = ca.audit(cr.build_market(perp, spot, funding))
    assert report["funding_extremes"]["count"] == 1 and report["funding_extremes"]["negative"] == 1
    assert report["funding_extremes"]["worst"][0]["symbol"] == "S0USDT"
