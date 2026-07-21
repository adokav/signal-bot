"""Top-liquidity long-opportunity ranking for the unified radar.

The module deliberately separates a broad liquid universe from new-listing
discovery.  It uses hard downside/FOMO/execution gates before scoring trend,
momentum, participation, risk, market breadth and verified supply.  Output is
research-only and cannot authorize an order.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from typing import Any

from .cex import is_leveraged_token, is_stable_or_synthetic, opportunity_proxy
from .models import CexTicker, RadarCandidate


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    value = values[0]
    for current in values[1:]:
        value = alpha * current + (1.0 - alpha) * value
    return value


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    sample = changes[-period:]
    gains = sum(max(value, 0.0) for value in sample) / period
    losses = sum(max(-value, 0.0) for value in sample) / period
    if losses <= 0:
        return 100.0 if gains > 0 else 50.0
    relative = gains / losses
    return 100.0 - 100.0 / (1.0 + relative)


def calculate_long_metrics(rows: list[Any]) -> dict[str, Any]:
    """Calculate closed-candle metrics from 15-minute Binance-style klines."""
    valid = [row for row in rows if isinstance(row, (list, tuple)) and len(row) >= 8]
    completed = valid[:-1]
    if len(completed) < 55:
        return {"status": "INSUFFICIENT_KLINES", "can_authorize_trade": False}
    closes = [_number(row[4]) for row in completed]
    highs = [_number(row[2]) for row in completed]
    lows = [_number(row[3]) for row in completed]
    quote_volumes = [_number(row[7], _number(row[5])) for row in completed]
    if min(closes) <= 0:
        return {"status": "INVALID_KLINES", "can_authorize_trade": False}

    last = closes[-1]
    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    ema20_previous = _ema(closes[:-4], 20) if len(closes) > 24 else ema20
    change_1h = (last / closes[-5] - 1.0) * 100.0
    change_4h = (last / closes[-17] - 1.0) * 100.0
    change_window = (last / closes[0] - 1.0) * 100.0
    high_window = max(highs)
    low_window = min(lows)
    range_position = (
        (last - low_window) / (high_window - low_window) * 100.0
        if high_window > low_window
        else 50.0
    )
    true_ranges: list[float] = []
    for index in range(1, len(completed)):
        true_ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))
    atr = statistics.mean(true_ranges[-14:]) if len(true_ranges) >= 14 else 0.0
    recent_volume = sum(quote_volumes[-4:])
    baseline_windows = [
        sum(quote_volumes[index:index + 4])
        for index in range(max(0, len(quote_volumes) - 28), len(quote_volumes) - 4, 4)
    ]
    volume_baseline = statistics.median(baseline_windows) if baseline_windows else 0.0
    return {
        "status": "READY",
        "interval": "15m",
        "completed_candles": len(completed),
        "last_close": last,
        "ema20": ema20,
        "ema50": ema50,
        "ema20_slope_pct": (ema20 / max(ema20_previous, 1e-12) - 1.0) * 100.0,
        "ema20_distance_pct": (last / max(ema20, 1e-12) - 1.0) * 100.0,
        "change_1h_pct": change_1h,
        "change_4h_pct": change_4h,
        "change_window_pct": change_window,
        "rsi14": _rsi(closes),
        "atr_pct": atr / last * 100.0,
        "volume_ratio": recent_volume / max(volume_baseline, 1.0),
        "range_position_pct": range_position,
        "drawdown_from_high_pct": (last / max(high_window, 1e-12) - 1.0) * 100.0,
        "can_authorize_trade": False,
    }


def ticker_spread_bps(item: CexTicker) -> float | None:
    if item.bid_price <= 0 or item.ask_price < item.bid_price:
        return None
    midpoint = (item.bid_price + item.ask_price) / 2.0
    return (item.ask_price - item.bid_price) / midpoint * 10_000.0 if midpoint else None


def select_liquid_universe(
    tickers: Iterable[CexTicker],
    *,
    size: int = 100,
    min_quote_volume: float = 1_000_000.0,
    quote_asset: str = "USDT",
    required_venue: str | None = None,
) -> list[CexTicker]:
    rows = list(tickers)
    quote = quote_asset.upper()
    all_bases = {
        item.symbol[: -len(quote)]
        for item in rows
        if item.symbol.endswith(quote) and len(item.symbol) > len(quote)
    }
    eligible = []
    for item in rows:
        symbol = item.symbol.upper()
        if required_venue and item.venue.upper() != required_venue.upper():
            continue
        if not symbol.endswith(quote) or len(symbol) <= len(quote):
            continue
        base = symbol[: -len(quote)]
        if (
            is_stable_or_synthetic(base)
            or is_leveraged_token(base, all_bases)
            or item.last_price <= 0
            or item.quote_volume < min_quote_volume
        ):
            continue
        eligible.append(item)
    eligible.sort(key=lambda item: item.quote_volume, reverse=True)
    return eligible[: max(1, int(size))]


def build_market_context(universe: Iterable[CexTicker]) -> dict[str, Any]:
    rows = list(universe)
    changes = [item.change_pct for item in rows]
    btc = next((item.change_pct for item in rows if item.symbol.upper() == "BTCUSDT"), 0.0)
    breadth = 100.0 * sum(value > 0 for value in changes) / len(changes) if changes else 0.0
    median_change = statistics.median(changes) if changes else 0.0
    if btc <= -5.0 and breadth < 25.0:
        regime = "CAPITULATION"
        score = 0
    elif btc < -2.0 or breadth < 35.0:
        regime = "RISK_OFF"
        score = 25
    elif btc >= 0.0 and breadth >= 55.0:
        regime = "RISK_ON"
        score = 100
    else:
        regime = "NEUTRAL"
        score = 60
    return {
        "regime": regime,
        "regime_score": score,
        "positive_breadth_pct": round(breadth, 1),
        "median_change_pct": round(median_change, 2),
        "btc_change_pct": round(btc, 2),
        "universe_size": len(rows),
    }


def select_enrichment_universe(
    universe: Iterable[CexTicker],
    *,
    size: int = 30,
    max_drawdown_pct: float = -8.0,
    max_gain_pct: float = 25.0,
    max_spread_bps: float = 35.0,
) -> list[CexTicker]:
    candidates: list[tuple[float, CexTicker]] = []
    for item in universe:
        spread = ticker_spread_bps(item)
        if (
            item.change_pct <= max_drawdown_pct
            or item.change_pct >= max_gain_pct
            or (spread is not None and spread > max_spread_bps)
        ):
            continue
        candidates.append((opportunity_proxy(item.change_pct, item.quote_volume), item))
    candidates.sort(key=lambda row: (row[0], row[1].quote_volume), reverse=True)
    return [item for _, item in candidates[: max(1, int(size))]]


def _bell(value: float, target: float, width: float, points: float) -> float:
    return points * max(0.0, 1.0 - abs(value - target) / max(width, 1e-9))


def score_technical_long(
    ticker: CexTicker,
    metrics: dict[str, Any] | None,
    *,
    liquidity_rank: int,
    max_drawdown_pct: float = -8.0,
    max_gain_pct: float = 25.0,
    max_spread_bps: float = 35.0,
) -> dict[str, Any]:
    data = dict(metrics or {})
    blockers: list[str] = []
    if ticker.change_pct <= max_drawdown_pct:
        blockers.append(f"24s düşüş sınırı aşıldı (%{ticker.change_pct:+.1f})")
    if ticker.change_pct >= max_gain_pct:
        blockers.append(f"24s hareket fazla uzamış (%{ticker.change_pct:+.1f})")
    spread = ticker_spread_bps(ticker)
    if spread is None:
        blockers.append("alış-satış makası doğrulanamadı")
    elif spread > max_spread_bps:
        blockers.append(f"makas geniş ({spread:.0f} bps)")
    if data.get("status") != "READY":
        blockers.append("çok-zaman-dilimli mum verisi eksik")
        return {"status": "BLOCKED", "score": 0, "blockers": blockers}

    change_1h = _number(data.get("change_1h_pct"))
    change_4h = _number(data.get("change_4h_pct"))
    rsi = _number(data.get("rsi14"), 50.0)
    atr = _number(data.get("atr_pct"))
    ema_distance = _number(data.get("ema20_distance_pct"))
    ema_slope = _number(data.get("ema20_slope_pct"))
    volume_ratio = _number(data.get("volume_ratio"))
    range_position = _number(data.get("range_position_pct"), 50.0)
    drawdown_high = _number(data.get("drawdown_from_high_pct"))
    last = _number(data.get("last_close"), ticker.last_price)
    ema20 = _number(data.get("ema20"))
    ema50 = _number(data.get("ema50"))
    if last <= ema20 or ema20 <= ema50 or ema_slope <= 0:
        blockers.append("EMA20/EMA50 long trend yapısı teyitli değil")
    if change_1h <= -1.5 or change_4h <= -3.0:
        blockers.append("kısa vadeli momentum aşağı")
    if rsi < 45.0 or rsi > 72.0:
        blockers.append(f"RSI long penceresi dışında ({rsi:.0f})")
    if atr > 6.0:
        blockers.append(f"oynaklık aşırı yüksek (ATR %{atr:.1f})")
    if ema_distance > 6.0:
        blockers.append(f"EMA20'den fazla uzaklaşmış (%{ema_distance:.1f})")
    if blockers:
        return {"status": "BLOCKED", "score": 0, "blockers": blockers}

    trend = (
        8.0
        + 10.0
        + _clamp((ema_slope + 0.05) / 0.8 * 6.0, 0.0, 6.0)
        + _bell(change_4h, 4.0, 7.0, 6.0)
    )
    momentum = (
        _bell(change_1h, 1.2, 3.0, 7.0)
        + _bell(change_4h, 4.0, 8.0, 7.0)
        + _bell(rsi, 58.0, 14.0, 6.0)
    )
    participation = (
        _bell(volume_ratio, 2.0, 2.0, 10.0)
        + _bell(range_position, 72.0, 35.0, 5.0)
    )
    liquidity = 10.0 * (101 - min(max(liquidity_rank, 1), 100)) / 100.0
    if spread is not None and spread <= 5:
        liquidity += 5.0
    elif spread <= 15:
        liquidity += 3.0
    elif spread <= max_spread_bps:
        liquidity += 1.0
    risk = (
        _bell(ticker.change_pct, 8.0, 14.0, 6.0)
        + _bell(atr, 2.0, 2.5, 5.0)
        + _bell(ema_distance, 2.0, 4.0, 5.0)
        + _bell(drawdown_high, -5.0, 8.0, 4.0)
    )
    score = int(round(_clamp(trend + momentum + participation + liquidity + risk)))
    return {
        "status": "READY",
        "score": score,
        "components": {
            "trend": int(round(trend)),
            "momentum": int(round(momentum)),
            "participation": int(round(participation)),
            "liquidity": int(round(liquidity)),
            "risk_fomo": int(round(risk)),
        },
        "blockers": [],
        "spread_bps": round(spread, 2) if spread is not None else None,
    }


def supply_gate_ready(fundamentals: dict[str, Any] | None) -> bool:
    data = fundamentals or {}
    return bool(
        data.get("status") == "READY"
        and data.get("circulating_supply") is not None
        and (
            data.get("total_supply") is not None
            or data.get("max_supply") is not None
        )
    )


def score_supply_quality(fundamentals: dict[str, Any] | None) -> int:
    data = fundamentals or {}
    if not supply_gate_ready(data):
        return 0
    score = 25.0
    circulation = data.get("circulation_pct")
    if circulation is not None:
        value = _number(circulation)
        score += 35 if value >= 70 else 28 if value >= 50 else 18 if value >= 30 else 5
    mcap_fdv = data.get("market_cap_to_fdv_pct")
    if mcap_fdv is None:
        score += 10
    else:
        value = _number(mcap_fdv)
        score += 25 if value >= 70 else 20 if value >= 50 else 10 if value >= 30 else 3
    turnover = data.get("volume_to_market_cap_pct")
    if turnover is None:
        score += 5
    else:
        value = _number(turnover)
        score += 15 if 3 <= value <= 80 else 10 if 1 <= value <= 150 else 4
    return int(round(_clamp(score)))


def rank_liquid_longs(
    universe: Iterable[CexTicker],
    metrics_by_symbol: dict[str, dict[str, Any]],
    fundamentals_by_symbol: dict[str, dict[str, Any]],
    *,
    market_context: dict[str, Any],
    trade_universe: dict[str, str],
    top_n: int = 5,
    min_score: int = 64,
    max_drawdown_pct: float = -8.0,
    max_gain_pct: float = 25.0,
    max_spread_bps: float = 35.0,
    require_supply_data: bool = True,
) -> list[RadarCandidate]:
    rows = list(universe)
    liquidity_ranks = {item.symbol.upper(): index for index, item in enumerate(rows, 1)}
    candidates: list[RadarCandidate] = []
    regime = str(market_context.get("regime") or "UNKNOWN")
    if regime == "CAPITULATION":
        return []
    for ticker in rows:
        symbol = ticker.symbol.upper()
        metrics = metrics_by_symbol.get(symbol) or {}
        technical = score_technical_long(
            ticker,
            metrics,
            liquidity_rank=liquidity_ranks.get(symbol, 100),
            max_drawdown_pct=max_drawdown_pct,
            max_gain_pct=max_gain_pct,
            max_spread_bps=max_spread_bps,
        )
        if technical.get("status") != "READY":
            continue
        fundamentals = fundamentals_by_symbol.get(symbol) or {}
        supply_ready = supply_gate_ready(fundamentals)
        if require_supply_data and not supply_ready:
            continue
        if fundamentals.get("stage") == "HIGH_RISK":
            continue
        supply_score = score_supply_quality(fundamentals)
        regime_score = int(market_context.get("regime_score") or 0)
        final_score = int(round(_clamp(
            0.65 * int(technical.get("score") or 0)
            + 0.25 * supply_score
            + 0.10 * regime_score
        )))
        if final_score < min_score:
            continue
        if final_score >= 82:
            stage = "A_PLUS"
        elif final_score >= 74:
            stage = "STRONG_LONG"
        else:
            stage = "WATCH_LONG"
        role = "CORE_CONFLUENCE" if symbol in trade_universe else "DISCOVERY_ONLY"
        reasons = [
            "EMA20 > EMA50 ve fiyat trend üzerinde",
            f"1s/4s momentum %{_number(metrics.get('change_1h_pct')):+.1f}/%{_number(metrics.get('change_4h_pct')):+.1f}",
            f"hacim katılımı {_number(metrics.get('volume_ratio')):.1f}x",
            f"arz kalitesi {supply_score}/100",
        ]
        risks = []
        if regime != "RISK_ON":
            risks.append(f"piyasa rejimi {regime.lower()}")
        if role != "CORE_CONFLUENCE":
            risks.append("sermaye evreni dışında; otomatik işlem yasak")
        candidates.append(RadarCandidate(
            source="LIQUID_100_LONG_RADAR",
            key=f"liquid-long:{symbol}",
            symbol=symbol,
            score=final_score,
            stage=stage,
            role=role,
            execution_eligible=False,
            reasons=tuple(reasons),
            risk_flags=tuple(risks),
            metadata={
                "liquidity_rank": liquidity_ranks.get(symbol, 100),
                "last_price": ticker.last_price,
                "change_pct": ticker.change_pct,
                "quote_volume": ticker.quote_volume,
                "venue": ticker.venue,
                "spread_bps": technical.get("spread_bps"),
                "technical_score": technical.get("score"),
                "technical_components": technical.get("components") or {},
                "supply_score": supply_score,
                "market_context": dict(market_context),
                "long_metrics": dict(metrics),
                "fundamentals": dict(fundamentals),
                "can_authorize_trade": False,
            },
        ))
    candidates.sort(
        key=lambda item: (
            item.score,
            int(item.metadata.get("technical_score") or 0),
            -int(item.metadata.get("liquidity_rank") or 100),
        ),
        reverse=True,
    )
    return candidates[: max(1, int(top_n))]
