"""Characterization tests for bot.build_trade_plan.

Tek senaryo ile baslar (LONG core, all gates open). Test gecerse golden
snapshot kaydeder; sonraki kosullarda ayni cikti gerekir. Refactor onayinin
olcusu: bu testler kirmizi olmadan refactor edilir.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import bot

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_DIR.mkdir(parents=True, exist_ok=True)


def _serialize(obj):
    """JSON-serializable hale getir; rounding ile deterministik yap."""
    if isinstance(obj, float):
        return round(obj, 8)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in sorted(obj.items())}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


def _golden_assert(name: str, output):
    path = GOLDEN_DIR / f"{name}.json"
    serialized = _serialize(output)
    if not path.exists():
        path.write_text(json.dumps(serialized, indent=2, sort_keys=True))
        pytest.skip(f"golden {name}.json yaratıldı; tekrar çalıştırıp assert et")
    expected = json.loads(path.read_text())
    assert serialized == expected, (
        f"{name}: build_trade_plan çıktısı golden ile eşleşmedi.\n"
        f"Beklenen: {json.dumps(expected, indent=2, sort_keys=True)[:500]}\n"
        f"Alinan : {json.dumps(serialized, indent=2, sort_keys=True)[:500]}"
    )


def _base_long_core_result() -> dict:
    """LONG, CORE, tum gate'ler acik, RISK_ON_TREND_UP rejim, A-grade kalite."""
    return {
        "symbol": "BTCUSDT",
        "group": "CORE",
        "signal": "LONG",
        "level": "MEDIUM",
        "score": 1.80,
        "pre_news_score": 1.80,
        "max_score": 2.50,
        "confidence": 72.0,
        "veto": None,
        "news_action": "news_neutral",
        "basis_missing": False,
        "funding_missing": False,
        "raw": {
            "macro": 1.0, "market": 0.5, "mtf": 1.5, "trend": 1.0,
            "momentum": 0.8, "volume": 1.0, "liquidity": 0.0,
            "basis": 0.5, "funding": 0.2,
        },
        "features": {
            "last": 100_000.0,
            "spot_price": 100_000.0,
            "futures_price": 100_100.0,
            "basis_pct": 0.10,
            "funding_rate": 0.005,
            "ema9": 99_000.0,
            "ema21": 98_500.0,
            "ema50": 97_000.0,
            "ema9_15": 99_000.0,
            "ema21_15": 98_500.0,
            "ema50_15": 97_000.0,
            "ema9_1h": 99_000.0,
            "ema21_1h": 98_000.0,
            "ema50_1h": 96_000.0,
            "ema9_4h": 95_000.0,
            "ema21_4h": 92_000.0,
            "ema50_4h": 90_000.0,
            "ret_1h_tf": 0.5,
            "ret_4h_tf": 1.0,
            "ret_24h_tf": 2.5,
            "ret_5m": 0.05,
            "ret_15m": 0.20,
            "ret_1h": 0.50,
            "ret_4h": 1.20,
            "vol_ratio": 1.2,
            "spread_bps": 1.5,
            "change_24h": 2.0,
        },
        "trade_quality": {
            "tradable": True,
            "grade": "A",
            "score": 72.0,
            "reasons": ["mtf aynı yönde destekliyor", "Rejim: RISK_ON_TREND_UP / bias: LONG_ONLY"],
        },
        "portfolio_check": {"allowed": True, "reason": "Portföy riski uygun."},
        "regime_commander": {
            "allowed": True,
            "reason": "Rejim uygun.",
            "strategy": {
                "direction_bias": "LONG_ONLY",
                "allow_long": True,
                "allow_short": False,
                "high_beta_allowed": True,
                "risk_multiplier": 1.25,
                "min_quality": "A",
                "quality_bonus": 8,
                "tp_style": "trend_runner",
                "note": "Risk-on yukarı trend.",
            },
        },
        "entry_engine": {"status": "READY", "reason": "ok", "checks": []},
        "regime": {
            "regime": "RISK_ON_TREND_UP",
            "score": 2,
            "direction_bias": "LONG_ONLY",
            "risk_multiplier": 1.25,
            "min_quality": "A",
            "high_beta_allowed": True,
            "allow_long": True,
            "allow_short": False,
            "tp_style": "trend_runner",
            "strategy_note": "Risk-on yukarı trend.",
            "note": "BTC/ETH ust trend.",
            "metrics": {"btc_mtf": 1.5, "eth_mtf": 1.0, "btc_4h": 1.2, "btc_24h": 3.0, "eth_24h": 4.0, "news_risk": 0.0, "btc_funding": 0.005},
        },
        "session_context": {
            "session": "US_OPEN",
            "macro_multiplier": 1.0,
            "micro_multiplier": 1.0,
            "news_multiplier": 1.0,
            "note": "ABD piyasası açık.",
        },
        "news_context": {
            "news_risk_score": 0,
            "category": "NONE",
            "headline": None,
            "source": None,
            "provider": None,
            "url": None,
            "match_count": 0,
            "note": "Önemli haber etkisi yok.",
        },
    }


def test_long_core_happy_path():
    result = _base_long_core_result()
    plan = bot.build_trade_plan(result)
    assert plan is not None, "happy-path LONG core plan = None geldi; bir gate yanlış engelliyor"
    _golden_assert("long_core_happy_path", plan)


def test_short_blocked_by_acce_long_only_default():
    """Default config'de (ACCE_TRADE_BRAIN_ENABLED=1 + ACCE_LONG_ONLY=1) SHORT
    sinyali her zaman None doner. SHORT icin build_trade_plan'in entry zone
    / stop / TP hesap dali (30+ satir) bu default'ta cagrilmiyor -- olu kod
    suphesi. Refactor'da ya kaldirilmali ya ACCE flag'leri silinmeli.
    """
    result = _base_long_core_result()
    result["signal"] = "SHORT"
    plan = bot.build_trade_plan(result)
    assert plan is None, "SHORT plan üretildi -- ACCE flag erken kesisini kaybetmis olabilir"


def test_short_with_acce_disabled(monkeypatch):
    """ACCE flag'leri kapatildiginda SHORT plan dali calismali."""
    monkeypatch.setattr(bot, "ACCE_TRADE_BRAIN_ENABLED", False)
    monkeypatch.setattr(bot, "ACCE_LONG_ONLY", False)
    result = _base_long_core_result()
    result["signal"] = "SHORT"
    # SHORT icin EMA'lari spot'un USTUNDE: saglikli pullback yukari
    result["features"]["ema9"] = 101_000.0
    result["features"]["ema21"] = 101_500.0
    result["features"]["ema50"] = 102_500.0
    result["regime"]["regime"] = "RISK_OFF_TREND_DOWN"
    result["regime"]["direction_bias"] = "SHORT_ONLY_OR_CASH"
    result["regime"]["allow_long"] = False
    result["regime"]["allow_short"] = True
    result["regime"]["tp_style"] = "defensive"
    result["regime_commander"]["strategy"]["allow_long"] = False
    result["regime_commander"]["strategy"]["allow_short"] = True
    result["regime_commander"]["strategy"]["direction_bias"] = "SHORT_ONLY_OR_CASH"
    result["regime_commander"]["strategy"]["tp_style"] = "defensive"
    plan = bot.build_trade_plan(result)
    assert plan is not None, "SHORT plan = None geldi (ACCE kapatilmasina ragmen)"
    assert plan["direction"] == "SHORT"
    _golden_assert("short_core_acce_disabled", plan)


def test_long_blocked_by_trade_quality():
    result = _base_long_core_result()
    result["trade_quality"]["tradable"] = False
    result["trade_quality"]["grade"] = "C"
    plan = bot.build_trade_plan(result)
    assert plan is None, "trade_quality.tradable=False olmasina ragmen plan uretti"


def test_long_basis_missing():
    """basis_pct=None oldugunda apply_regime_first_scoring / build_trade_plan
    hala calismali; weights basis siz redistribute ediliyor."""
    result = _base_long_core_result()
    result["features"]["basis_pct"] = None
    result["features"]["futures_price"] = None
    result["basis_missing"] = True
    plan = bot.build_trade_plan(result)
    assert plan is not None, "basis missing'de plan kaybolduysa weights yanlis"
    _golden_assert("long_core_basis_missing", plan)
