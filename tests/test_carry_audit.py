from __future__ import annotations

import json

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


def test_cli_writes_json_with_extreme_funding(monkeypatch, tmp_path, capsys):
    """Regression: counting numpy booleans gave np.int64, which json.dumps rejected after the build."""

    perp, spot, funding = _market(2, funding_rate=lambda k, s: -0.01 if (s == 0 and k == 120) else 0.0001)
    market = cr.build_market(perp, spot, funding)
    monkeypatch.setattr(cr, "load_market", lambda data_dir: (market, {}))
    out = tmp_path / "audit.json"
    assert ca._cli(["--data-dir", str(tmp_path), "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["funding_extremes"]["negative"] == 1 and report["can_authorize_trade"] is False
    assert "funding prints" in capsys.readouterr().out


def test_zero_volume_perp_candles_are_reported_as_a_dead_hedge():
    """A settled perp keeps a frozen, zero-volume price while spot collapses: STATIC books the loss."""

    perp, spot, funding = _market(3, funding_rate=lambda k, s: -0.0001 if s == 1 else 0.0001)
    frozen = perp["S1USDT"]["open"][110]
    for col in ("open", "high", "low", "close"):
        perp["S1USDT"][col][110:] = frozen
    perp["S1USDT"]["quote_volume"][110:] = 0.0
    for col in ("open", "high", "low", "close"):
        spot["S1USDT"][col][111:] = spot["S1USDT"][col][111:] * 0.1
    report = ca.audit(cr.build_market(perp, spot, funding))
    zv = report["zero_volume"]
    assert zv["symbols"] == ["S1USDT"] and zv["candles"] > 0
    static = zv["STATIC_CARRY"]
    assert static["contribution_pct"] < -4 and static["worst"][0]["symbol"] == "S1USDT"
    assert static["without_pct"] == pytest.approx(static["book_total_pct"] - static["contribution_pct"], abs=0.02)
    assert zv["SIGNED_CARRY"]["periods"] == 0   # negative funding: SIGNED never held S1
