"""
Backtest Engine v2 — Signal Bot

v2 additions over v1:
- Optional MEXC futures kline fetch for historical basis.
- Candle-range based liquidity/spread proxy because historical order book is unavailable.
- Optional historical news proxy via CSV/JSONL event file.
- TP1/TP2/TP3/STOP lifecycle and equity curve outputs.

Examples:
    python backtest_engine_v2.py --days 30
    python backtest_engine_v2.py --symbols BTCUSDT ETHUSDT SOLUSDT LDOUSDT --days 60
    python backtest_engine_v2.py --days 90 --news-events-file news_events.csv

news_events.csv columns:
    start_time,end_time,risk,category,note
    2026-04-01T12:00:00Z,2026-04-01T18:00:00Z,-2,WAR,geopolitical risk
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

MEXC_SPOT_BASE = "https://api.mexc.com"
MEXC_FUTURES_BASE = "https://contract.mexc.com"

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "AVAXUSDT", "SOLUSDT", "LINKUSDT",
    "RENDERUSDT", "ONDOUSDT", "LDOUSDT", "POPCATUSDT",
]

COINS = {
    "BTCUSDT": "CORE", "ETHUSDT": "CORE", "AVAXUSDT": "CORE",
    "SOLUSDT": "CORE", "LINKUSDT": "CORE",
    "RENDERUSDT": "HIGH_BETA", "ONDOUSDT": "HIGH_BETA",
    "LDOUSDT": "HIGH_BETA", "POPCATUSDT": "HIGH_BETA",
}

THRESHOLDS = {
    "CORE": {"long": 1.55, "short": -1.55, "strong": 2.25},
    "HIGH_BETA": {"long": 1.95, "short": -1.95, "strong": 2.70},
}

WEIGHTS = {
    "CORE": {"macro": 0.25, "market": 0.22, "trend": 0.18, "momentum": 0.14, "volume": 0.05, "liquidity": 0.05, "basis": 0.11},
    "HIGH_BETA": {"macro": 0.15, "market": 0.18, "trend": 0.18, "momentum": 0.23, "volume": 0.10, "liquidity": 0.06, "basis": 0.10},
}

SCORE_PARAMS = {
    "macro": {"change_24h_strong": 2.0, "ret_4h_strong": 1.0, "weight_change_24h": 1.5, "weight_ret_4h": 1.5},
    "market": {"ret_1h_strong": 0.5},
    "momentum": {"ret_5m_strong": 0.15, "ret_15m_strong": 0.35, "ret_1h_strong": 0.8},
    "volume": {"spike": 2.0, "high": 1.3, "low": 0.6},
    "veto": {"vol_low_high_beta": 0.7, "btc_unclear_1h": 0.25, "btc_unclear_4h": 0.40},
}

TRADE_PLAN_CONFIG = {
    "CORE": {"stop_pct": 0.020, "tp1_r": 1.0, "tp2_r": 2.0, "tp3_r": 3.0},
    "HIGH_BETA": {"stop_pct": 0.035, "tp1_r": 1.0, "tp2_r": 2.0, "tp3_r": 3.0},
}

ACCOUNT_SIZE_USD = float(os.getenv("ACCOUNT_SIZE_USD", "100"))
BASE_RISK_PCT = float(os.getenv("RISK_PCT_PER_TRADE", "0.01"))

KLINE_LIMIT_PER_CALL = 1000
MS = {"5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000}
FUTURES_INTERVAL_MAP = {"5m": "Min5", "15m": "Min15"}

# Since historical order book is unavailable, v2 uses candle range as liquidity proxy.
SPREAD_PROXY_LIMITS = {"CORE": 80.0, "HIGH_BETA": 160.0}


def utc_ms(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def pct(a: float, b: float) -> float:
    return 0.0 if b == 0 else (a - b) / b * 100.0


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)
    sma = sum(values[:period]) / period
    k = 2.0 / (period + 1)
    result = sma
    for price in values[period:]:
        result = price * k + result * (1 - k)
    return result


def safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))
    return values[idx]


def request_json(url: str, params: dict | None = None, retries: int = 3) -> Any:
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (2 ** attempt))
    raise last_err


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[list]:
    out: list[list] = []
    cursor = start_ms
    step = MS[interval] * KLINE_LIMIT_PER_CALL
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": min(end_ms, cursor + step), "limit": KLINE_LIMIT_PER_CALL}
        data = request_json(f"{MEXC_SPOT_BASE}/api/v3/klines", params=params)
        if not data:
            cursor += step
            continue
        out.extend(data)
        last_open = int(data[-1][0])
        next_cursor = last_open + MS[interval]
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(0.08)
    dedup = {int(k[0]): k for k in out}
    return [dedup[t] for t in sorted(dedup)]


def to_futures_symbol(symbol: str) -> str:
    return symbol.replace("USDT", "_USDT")


def fetch_futures_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    futures_symbol = to_futures_symbol(symbol)
    interval_name = FUTURES_INTERVAL_MAP.get(interval, interval)
    out: list[dict] = []
    cursor_s = start_ms // 1000
    end_s = end_ms // 1000
    step_s = (MS[interval] // 1000) * KLINE_LIMIT_PER_CALL
    while cursor_s < end_s:
        url = f"{MEXC_FUTURES_BASE}/api/v1/contract/kline/{futures_symbol}"
        params = {"interval": interval_name, "start": cursor_s, "end": min(end_s, cursor_s + step_s)}
        try:
            data = request_json(url, params=params, retries=2)
        except Exception:
            return []
        payload = data.get("data") if isinstance(data, dict) else data
        if not payload:
            cursor_s += step_s
            continue
        rows: list[dict] = []
        if isinstance(payload, dict) and "time" in payload:
            times = payload.get("time", [])
            opens = payload.get("open", [])
            highs = payload.get("high", [])
            lows = payload.get("low", [])
            closes = payload.get("close", [])
            vols = payload.get("vol", payload.get("volume", []))
            for i, t in enumerate(times):
                try:
                    rows.append({"open_time": int(t) * 1000, "open": float(opens[i]), "high": float(highs[i]), "low": float(lows[i]), "close": float(closes[i]), "volume": float(vols[i]) if i < len(vols) else 0.0})
                except Exception:
                    continue
        elif isinstance(payload, list):
            for k in payload:
                try:
                    ts = int(k[0])
                    if ts < 10_000_000_000:
                        ts *= 1000
                    rows.append({"open_time": ts, "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]) if len(k) > 5 else 0.0})
                except Exception:
                    continue
        if rows:
            out.extend(rows)
            last_ts_s = rows[-1]["open_time"] // 1000
            cursor_s = max(cursor_s + 1, last_ts_s + MS[interval] // 1000)
        else:
            cursor_s += step_s
        time.sleep(0.08)
    dedup = {r["open_time"]: r for r in out}
    return [dedup[t] for t in sorted(dedup)]


def kline_to_rows(raw: list[list]) -> list[dict]:
    return [{"open_time": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]), "close_time": int(k[6]) if len(k) > 6 else int(k[0])} for k in raw]


def parse_event_time(x) -> int:
    if x is None or x == "":
        return 0
    if isinstance(x, (int, float)):
        return int(x if x > 10_000_000_000 else x * 1000)
    text = str(x).strip()
    if text.isdigit():
        val = int(text)
        return val if val > 10_000_000_000 else val * 1000
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def load_news_events(path: str | None) -> list[dict]:
    if not path:
        return []
    fp = Path(path)
    if not fp.exists():
        print(f"UYARI: news event dosyası bulunamadı: {path}")
        return []
    events: list[dict] = []
    if fp.suffix.lower() == ".csv":
        with open(fp, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    events.append({"start_ms": parse_event_time(row.get("start_time")), "end_ms": parse_event_time(row.get("end_time")), "risk": float(row.get("risk", 0)), "category": row.get("category", "NEWS"), "note": row.get("note", "")})
                except Exception:
                    continue
    else:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    events.append({"start_ms": parse_event_time(row.get("start_time")), "end_ms": parse_event_time(row.get("end_time")), "risk": float(row.get("risk", row.get("news_risk_score", 0))), "category": row.get("category", "NEWS"), "note": row.get("note", "")})
                except Exception:
                    continue
    return sorted(events, key=lambda e: e["start_ms"])


def news_context_at(events: list[dict], ts: int) -> dict:
    active = [e for e in events if e["start_ms"] <= ts <= e["end_ms"]]
    if not active:
        return {"news_risk_score": 0.0, "category": "NONE", "note": "historical news proxy yok"}
    top = sorted(active, key=lambda e: abs(e["risk"]), reverse=True)[0]
    return {"news_risk_score": float(top["risk"]), "category": top.get("category", "NEWS"), "note": top.get("note", "historical news proxy")}


def find_last_index_le(rows: list[dict], ts: int) -> int:
    lo, hi, ans = 0, len(rows) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if rows[mid]["open_time"] <= ts:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def build_features_at(rows_5m: list[dict], rows_15m: list[dict], idx_5m: int, futures_5m: Optional[list[dict]] = None) -> Optional[dict]:
    if idx_5m < 60:
        return None
    ts = rows_5m[idx_5m]["open_time"]
    idx_15m = find_last_index_le(rows_15m, ts)
    if idx_15m < 50:
        return None
    window_5 = rows_5m[max(0, idx_5m - 99): idx_5m + 1]
    window_15 = rows_15m[max(0, idx_15m - 99): idx_15m + 1]
    closes_5 = [r["close"] for r in window_5]
    closes_15 = [r["close"] for r in window_15]
    volumes_5 = [r["volume"] for r in window_5]
    if len(closes_5) < 13 or len(closes_15) < 17:
        return None
    recent_vols = volumes_5[-31:-1]
    median_vol = statistics.median(recent_vols) if recent_vols else 0.0
    vol_ratio = volumes_5[-1] / median_vol if median_vol > 0 else 1.0
    idx_24h = max(0, idx_5m - 288)
    change_24h = pct(rows_5m[idx_5m]["close"], rows_5m[idx_24h]["close"])
    futures_price = None
    basis_pct = None
    if futures_5m:
        fidx = find_last_index_le(futures_5m, ts)
        if fidx >= 0:
            futures_price = futures_5m[fidx]["close"]
            basis_pct = pct(futures_price, closes_5[-1])
    candle_range_bps = (rows_5m[idx_5m]["high"] - rows_5m[idx_5m]["low"]) / closes_5[-1] * 10000 if closes_5[-1] else 999.0
    return {
        "last": closes_5[-1], "spot_price": closes_5[-1], "futures_price": futures_price, "basis_pct": basis_pct,
        "ema9": ema(closes_5, 9), "ema21": ema(closes_5, 21), "ema50": ema(closes_5, 50),
        "ema9_15": ema(closes_15, 9), "ema21_15": ema(closes_15, 21), "ema50_15": ema(closes_15, 50),
        "ret_5m": pct(closes_5[-1], closes_5[-2]), "ret_15m": pct(closes_5[-1], closes_5[-4]),
        "ret_1h": pct(closes_5[-1], closes_5[-12]), "ret_4h": pct(closes_15[-1], closes_15[-16]),
        "vol_ratio": vol_ratio, "spread_bps": candle_range_bps, "change_24h": change_24h,
        "_bar_high": rows_5m[idx_5m]["high"], "_bar_low": rows_5m[idx_5m]["low"], "_ts": ts,
    }


def score_macro(btc: dict) -> float:
    p = SCORE_PARAMS["macro"]
    score = 0.0
    if btc["change_24h"] > p["change_24h_strong"]: score += p["weight_change_24h"]
    elif btc["change_24h"] < -p["change_24h_strong"]: score -= p["weight_change_24h"]
    if btc["ret_4h"] > p["ret_4h_strong"]: score += p["weight_ret_4h"]
    elif btc["ret_4h"] < -p["ret_4h_strong"]: score -= p["weight_ret_4h"]
    return clamp(score, -3, 3)


def score_market(btc: dict, eth: dict) -> float:
    threshold = SCORE_PARAMS["market"]["ret_1h_strong"]
    score = 0.0
    if btc["ret_1h"] > threshold: score += 1
    elif btc["ret_1h"] < -threshold: score -= 1
    if eth["ret_1h"] > threshold: score += 1
    elif eth["ret_1h"] < -threshold: score -= 1
    return clamp(score, -2, 2)


def score_trend(f: dict) -> float:
    score = 0.0
    if f["ema9"] > f["ema21"] > f["ema50"]: score += 1.2
    elif f["ema9"] < f["ema21"] < f["ema50"]: score -= 1.2
    if f["ema9_15"] > f["ema21_15"] > f["ema50_15"]: score += 0.8
    elif f["ema9_15"] < f["ema21_15"] < f["ema50_15"]: score -= 0.8
    return clamp(score, -2, 2)


def score_momentum(f: dict) -> float:
    p = SCORE_PARAMS["momentum"]
    score = 0.0
    if f["ret_5m"] > p["ret_5m_strong"]: score += 0.4
    elif f["ret_5m"] < -p["ret_5m_strong"]: score -= 0.4
    if f["ret_15m"] > p["ret_15m_strong"]: score += 0.6
    elif f["ret_15m"] < -p["ret_15m_strong"]: score -= 0.6
    if f["ret_1h"] > p["ret_1h_strong"]: score += 1.0
    elif f["ret_1h"] < -p["ret_1h_strong"]: score -= 1.0
    return clamp(score, -2, 2)


def score_volume(f: dict) -> float:
    p = SCORE_PARAMS["volume"]
    if f["vol_ratio"] >= p["spike"]: return 2
    if f["vol_ratio"] >= p["high"]: return 1
    if f["vol_ratio"] < p["low"]: return -1
    return 0


def score_liquidity(f: dict, group: str) -> float:
    spread_proxy = f.get("spread_bps", 0.0)
    bad_limit = SPREAD_PROXY_LIMITS[group]
    if spread_proxy > bad_limit: return -2
    if spread_proxy > bad_limit / 2: return -1
    return 0


def score_basis(basis_pct: Optional[float], f: dict) -> float:
    if basis_pct is None: return 0
    score = 0.0
    if 0.05 <= basis_pct <= 0.35 and f["ret_1h"] > 0: score += 1.2
    elif basis_pct > 0.60: score -= 1.0
    elif basis_pct < -0.05 and f["ret_1h"] < 0: score -= 1.2
    elif basis_pct < -0.60: score += 0.5
    if basis_pct > 0 and f["ret_1h"] < -0.5: score -= 0.5
    if basis_pct < 0 and f["ret_1h"] > 0.5: score += 0.5
    return clamp(score, -2, 2)


def veto_signal(symbol: str, f: dict, btc: dict) -> Optional[str]:
    group = COINS[symbol]
    p = SCORE_PARAMS["veto"]
    if group == "HIGH_BETA" and f["vol_ratio"] < p["vol_low_high_beta"]: return "Hacim zayıf"
    if f.get("spread_bps", 0.0) > SPREAD_PROXY_LIMITS[group]: return "Likidite proxy kötü"
    if symbol not in ("BTCUSDT", "ETHUSDT"):
        btc_unclear = abs(btc["ret_1h"]) < p["btc_unclear_1h"] and abs(btc["ret_4h"]) < p["btc_unclear_4h"]
        if btc_unclear: return "BTC yönü belirsiz"
    return None


def classify_level(score_abs: float, group: str) -> str:
    if score_abs >= THRESHOLDS[group]["strong"]: return "STRONG"
    if score_abs >= THRESHOLDS[group]["long"]: return "MEDIUM"
    return "WEAK"


def normalize_weights_if_basis_missing(weights: dict, features: dict) -> dict:
    if features["basis_pct"] is not None: return dict(weights)
    active = {k: v for k, v in weights.items() if k != "basis"}
    total_w = sum(active.values())
    redistributed = {k: v / total_w for k, v in active.items()}
    redistributed["basis"] = 0
    return redistributed


def apply_news_modulation(total_score: float, news_risk: float) -> tuple[Optional[float], str]:
    abs_risk = abs(news_risk)
    same = (total_score > 0 and news_risk > 0) or (total_score < 0 and news_risk < 0)
    opposite = (total_score > 0 and news_risk < 0) or (total_score < 0 and news_risk > 0)
    if abs_risk >= 2.0 and opposite: return None, "news_veto"
    if abs_risk >= 1.0 and opposite: return total_score * 0.6, "news_dampened"
    if abs_risk >= 1.0 and same: return total_score * 1.1, "news_boosted"
    return total_score, "news_neutral"


def weighted_signal(symbol: str, features: dict, btc: dict, eth: dict, news_ctx: Optional[dict] = None) -> dict:
    group = COINS[symbol]
    weights = normalize_weights_if_basis_missing(WEIGHTS[group], features)
    veto = veto_signal(symbol, features, btc)
    raw = {
        "macro": score_macro(btc), "market": score_market(btc, eth), "trend": score_trend(features),
        "momentum": score_momentum(features), "volume": score_volume(features),
        "liquidity": score_liquidity(features, group), "basis": score_basis(features["basis_pct"], features),
    }
    pre_news_total = sum(raw[k] * weights[k] for k in raw)
    news_ctx = news_ctx or {"news_risk_score": 0.0, "category": "NONE"}
    mod_total, news_action = apply_news_modulation(pre_news_total, float(news_ctx.get("news_risk_score", 0.0)))
    total = pre_news_total if mod_total is None else mod_total
    max_total = weights["macro"] * 3 + weights["market"] * 2 + weights["trend"] * 2 + weights["momentum"] * 2 + weights["volume"] * 2 + weights["liquidity"] * 2 + weights["basis"] * 2
    if mod_total is None:
        signal, level = "NO_TRADE", "WEAK"
        veto = veto or f"Haber riski ({news_ctx.get('category', 'NEWS')})"
    elif veto:
        signal, level = "NO_TRADE", "WEAK"
    elif total >= THRESHOLDS[group]["long"]:
        signal, level = "LONG", classify_level(abs(total), group)
    elif total <= THRESHOLDS[group]["short"]:
        signal, level = "SHORT", classify_level(abs(total), group)
    else:
        signal, level = "NO_TRADE", "WEAK"
    confidence = round(abs(total) / max_total * 100, 1) if signal in ("LONG", "SHORT") and max_total > 0 else 0.0
    return {"symbol": symbol, "group": group, "signal": signal, "level": level, "score": round(total, 4), "pre_news_score": round(pre_news_total, 4), "max_score": round(max_total, 4), "confidence": confidence, "raw": raw, "features": features, "veto": veto, "news_action": news_action, "news_context": news_ctx}


@dataclass
class VirtualTrade:
    symbol: str
    direction: str
    entry_ts: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    tp3: float
    group: str
    score: float
    confidence: float
    level: str
    news_action: str = "news_neutral"
    news_category: str = "NONE"
    basis_pct: Optional[float] = None
    spread_proxy_bps: float = 0.0
    status: str = "OPEN"
    size_pct_open: float = 100.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    breakeven_active: bool = False
    realized_pnl_pct: float = 0.0
    close_ts: Optional[int] = None
    exit_price: Optional[float] = None
    bars_held: int = 0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def build_trade(result: dict) -> Optional[VirtualTrade]:
    signal = result["signal"]
    if signal not in ("LONG", "SHORT"):
        return None
    f = result["features"]
    group = result["group"]
    cfg = TRADE_PLAN_CONFIG[group]
    entry = f["spot_price"]
    stop_pct = cfg["stop_pct"]
    risk_unit = entry * stop_pct
    if signal == "LONG":
        stop, tp1, tp2, tp3 = entry - risk_unit, entry + risk_unit * cfg["tp1_r"], entry + risk_unit * cfg["tp2_r"], entry + risk_unit * cfg["tp3_r"]
    else:
        stop, tp1, tp2, tp3 = entry + risk_unit, entry - risk_unit * cfg["tp1_r"], entry - risk_unit * cfg["tp2_r"], entry - risk_unit * cfg["tp3_r"]
    news = result.get("news_context", {})
    return VirtualTrade(symbol=result["symbol"], direction=signal, entry_ts=f["_ts"], entry=entry, stop=stop, tp1=tp1, tp2=tp2, tp3=tp3, group=group, score=result["score"], confidence=result["confidence"], level=result["level"], news_action=result.get("news_action", "news_neutral"), news_category=news.get("category", "NONE"), basis_pct=f.get("basis_pct"), spread_proxy_bps=f.get("spread_bps", 0.0))


def update_trade(trade: VirtualTrade, high: float, low: float, close: float, ts: int) -> Optional[str]:
    if trade.status != "OPEN": return None
    trade.bars_held += 1
    if trade.direction == "LONG":
        trade.mfe_pct = max(trade.mfe_pct, pct(high, trade.entry)); trade.mae_pct = min(trade.mae_pct, pct(low, trade.entry))
        if low <= trade.stop:
            trade.realized_pnl_pct += pct(trade.stop, trade.entry) * (trade.size_pct_open / 100); trade.size_pct_open = 0; trade.status = "STOP"; trade.close_ts = ts; trade.exit_price = trade.stop; return "STOP"
        if not trade.tp1_hit and high >= trade.tp1:
            trade.tp1_hit = True; trade.realized_pnl_pct += pct(trade.tp1, trade.entry) * 0.50; trade.size_pct_open -= 50; trade.stop = trade.entry; trade.breakeven_active = True
        if not trade.tp2_hit and high >= trade.tp2:
            trade.tp2_hit = True; trade.realized_pnl_pct += pct(trade.tp2, trade.entry) * 0.25; trade.size_pct_open -= 25
        if not trade.tp3_hit and high >= trade.tp3:
            trade.tp3_hit = True; trade.realized_pnl_pct += pct(trade.tp3, trade.entry) * (trade.size_pct_open / 100); trade.size_pct_open = 0; trade.status = "TP3"; trade.close_ts = ts; trade.exit_price = trade.tp3; return "TP3"
    else:
        trade.mfe_pct = max(trade.mfe_pct, pct(trade.entry, low)); trade.mae_pct = min(trade.mae_pct, pct(trade.entry, high))
        if high >= trade.stop:
            trade.realized_pnl_pct += pct(trade.entry, trade.stop) * (trade.size_pct_open / 100); trade.size_pct_open = 0; trade.status = "STOP"; trade.close_ts = ts; trade.exit_price = trade.stop; return "STOP"
        if not trade.tp1_hit and low <= trade.tp1:
            trade.tp1_hit = True; trade.realized_pnl_pct += pct(trade.entry, trade.tp1) * 0.50; trade.size_pct_open -= 50; trade.stop = trade.entry; trade.breakeven_active = True
        if not trade.tp2_hit and low <= trade.tp2:
            trade.tp2_hit = True; trade.realized_pnl_pct += pct(trade.entry, trade.tp2) * 0.25; trade.size_pct_open -= 25
        if not trade.tp3_hit and low <= trade.tp3:
            trade.tp3_hit = True; trade.realized_pnl_pct += pct(trade.entry, trade.tp3) * (trade.size_pct_open / 100); trade.size_pct_open = 0; trade.status = "TP3"; trade.close_ts = ts; trade.exit_price = trade.tp3; return "TP3"
    return None


def should_open_trade(open_trades: list[VirtualTrade], result: dict, max_open: int, one_per_symbol: bool) -> bool:
    if result["signal"] not in ("LONG", "SHORT"): return False
    if result["level"] not in ("MEDIUM", "STRONG"): return False
    if len([t for t in open_trades if t.status == "OPEN"]) >= max_open: return False
    if one_per_symbol and any(t.status == "OPEN" and t.symbol == result["symbol"] for t in open_trades): return False
    return True


def aggregate_breakdown(closed: list[VirtualTrade], key_fn) -> dict:
    out: dict[str, dict] = {}
    for t in closed:
        key = str(key_fn(t))
        d = out.setdefault(key, {"count": 0, "pnl_pct_sum": 0.0, "wins": 0, "tp3": 0, "stop": 0})
        d["count"] += 1; d["pnl_pct_sum"] += t.realized_pnl_pct; d["wins"] += 1 if t.realized_pnl_pct > 0 else 0; d["tp3"] += 1 if t.status == "TP3" else 0; d["stop"] += 1 if t.status == "STOP" else 0
    for d in out.values():
        d["avg_pnl_pct"] = round(d["pnl_pct_sum"] / d["count"], 4); d["win_rate"] = round(d["wins"] / d["count"] * 100, 2); d["tp3_rate"] = round(d["tp3"] / d["count"] * 100, 2); d["stop_rate"] = round(d["stop"] / d["count"] * 100, 2)
    return out


def compute_report(trades: list[VirtualTrade], equity_curve: list[dict], args: argparse.Namespace) -> dict:
    closed = [t for t in trades if t.status != "OPEN"]
    if not closed:
        return {"summary": {"closed_trades": 0, "note": "Kapanmış trade yok."}, "config": vars(args)}
    pnl = [t.realized_pnl_pct for t in closed]; wins = [p for p in pnl if p > 0]; losses = [p for p in pnl if p <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses)); profit_factor = gross_win / gross_loss if gross_loss > 0 else None
    equities = [e["equity"] for e in equity_curve] or [ACCOUNT_SIZE_USD]
    peak, max_dd = equities[0], 0.0
    for eq in equities:
        peak = max(peak, eq); dd = (eq - peak) / peak * 100 if peak else 0.0; max_dd = min(max_dd, dd)
    return {
        "summary": {"closed_trades": len(closed), "open_trades_left": len([t for t in trades if t.status == "OPEN"]), "win_rate": round(len(wins) / len(closed) * 100, 2), "avg_pnl_pct": round(safe_mean(pnl), 4), "median_pnl_pct": round(quantile(pnl, 0.5), 4), "profit_factor": round(profit_factor, 4) if profit_factor is not None else None, "tp3_rate": round(len([t for t in closed if t.status == "TP3"]) / len(closed) * 100, 2), "stop_rate": round(len([t for t in closed if t.status == "STOP"]) / len(closed) * 100, 2), "avg_mfe_pct": round(safe_mean([t.mfe_pct for t in closed]), 4), "avg_mae_pct": round(safe_mean([t.mae_pct for t in closed]), 4), "ending_equity": round(equities[-1], 2), "return_pct": round((equities[-1] / ACCOUNT_SIZE_USD - 1) * 100, 2), "max_drawdown_pct": round(max_dd, 2)},
        "by_symbol": aggregate_breakdown(closed, lambda t: t.symbol), "by_group": aggregate_breakdown(closed, lambda t: t.group), "by_news_action": aggregate_breakdown(closed, lambda t: t.news_action), "by_news_category": aggregate_breakdown(closed, lambda t: t.news_category),
        "config": vars(args),
        "limitations": ["Historical news sadece --news-events-file verilirse kullanılır; aksi halde news_risk_score=0.", "Futures basis MEXC contract kline endpointi alınabilirse kullanılır; alınamazsa basis_pct=None.", "Order-book spread yerine candle range tabanlı spread/liquidity proxy kullanılır.", "Bar içinde hem stop hem TP görülürse conservative olarak stop önce kabul edilir."],
    }


def run_backtest(args: argparse.Namespace) -> dict:
    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    end = datetime.now(timezone.utc); start = end - timedelta(days=args.days); start_ms, end_ms = utc_ms(start), utc_ms(end)
    symbols = args.symbols or DEFAULT_SYMBOLS; required = sorted(set(symbols + ["BTCUSDT", "ETHUSDT"]))
    print(f"Backtest v2 data çekiliyor: {start.date()} → {end.date()} | symbols={required}")
    data_5m: dict[str, list[dict]] = {}; data_15m: dict[str, list[dict]] = {}; futures_5m: dict[str, list[dict]] = {}; news_events = load_news_events(args.news_events_file)
    for sym in required:
        print(f"  {sym} 5m..."); data_5m[sym] = kline_to_rows(fetch_klines(sym, "5m", start_ms, end_ms))
        print(f"  {sym} 15m..."); data_15m[sym] = kline_to_rows(fetch_klines(sym, "15m", start_ms, end_ms))
        if args.enable_futures_basis:
            print(f"  {sym} futures 5m basis..."); futures_5m[sym] = fetch_futures_klines(sym, "5m", start_ms, end_ms)
            if not futures_5m[sym]: print(f"  UYARI: {sym} futures basis verisi alınamadı; basis None devam.")
        if len(data_5m[sym]) < 100 or len(data_15m[sym]) < 50: print(f"  UYARI: {sym} veri az: 5m={len(data_5m[sym])}, 15m={len(data_15m[sym])}")
    btc_rows = data_5m["BTCUSDT"]; trades: list[VirtualTrade] = []; equity = ACCOUNT_SIZE_USD; equity_curve = [{"ts": start_ms, "equity": equity}]; last_signal_by_symbol: dict[str, str] = {}
    for i in range(80, len(btc_rows)):
        ts = btc_rows[i]["open_time"]
        btc_idx = find_last_index_le(data_5m["BTCUSDT"], ts); eth_idx = find_last_index_le(data_5m["ETHUSDT"], ts)
        btc_feat = build_features_at(data_5m["BTCUSDT"], data_15m["BTCUSDT"], btc_idx, futures_5m.get("BTCUSDT")); eth_feat = build_features_at(data_5m["ETHUSDT"], data_15m["ETHUSDT"], eth_idx, futures_5m.get("ETHUSDT"))
        if not btc_feat or not eth_feat: continue
        for t in trades:
            if t.status != "OPEN": continue
            sym_idx = find_last_index_le(data_5m[t.symbol], ts)
            if sym_idx < 0: continue
            bar = data_5m[t.symbol][sym_idx]; event = update_trade(t, bar["high"], bar["low"], bar["close"], ts)
            if event:
                stop_pct = TRADE_PLAN_CONFIG[t.group]["stop_pct"]; risk_amount = equity * args.risk_pct; notional = risk_amount / stop_pct if stop_pct > 0 else 0; pnl_usd = notional * (t.realized_pnl_pct / 100.0); equity += pnl_usd; equity_curve.append({"ts": ts, "equity": round(equity, 6), "event": event, "symbol": t.symbol})
        open_trades = [t for t in trades if t.status == "OPEN"]
        for sym in symbols:
            idx = find_last_index_le(data_5m[sym], ts)
            if idx < 0: continue
            feat = build_features_at(data_5m[sym], data_15m[sym], idx, futures_5m.get(sym))
            if not feat: continue
            news_ctx = news_context_at(news_events, ts)
            result = weighted_signal(sym, feat, btc_feat, eth_feat, news_ctx)
            prev_sig = last_signal_by_symbol.get(sym, "NO_TRADE"); current_sig = result["signal"]; signal_changed_to_trade = prev_sig == "NO_TRADE" and current_sig in ("LONG", "SHORT"); last_signal_by_symbol[sym] = current_sig
            if not signal_changed_to_trade: continue
            if not should_open_trade(open_trades, result, args.max_open_trades, args.one_per_symbol): continue
            trade = build_trade(result)
            if trade: trades.append(trade); open_trades.append(trade)
    report = compute_report(trades, equity_curve, args)
    trades_csv = out_dir / "backtest_trades.csv"
    with open(trades_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(VirtualTrade.__dataclass_fields__.keys())); writer.writeheader(); [writer.writerow(t.to_dict()) for t in trades]
    eq_csv = out_dir / "equity_curve.csv"
    with open(eq_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["ts", "time", "equity", "event", "symbol"]); writer.writeheader()
        for e in equity_curve: writer.writerow({"ts": e.get("ts"), "time": ms_to_iso(e.get("ts")), "equity": e.get("equity"), "event": e.get("event", ""), "symbol": e.get("symbol", "")})
    report_path = out_dir / "backtest_report.json"
    with open(report_path, "w", encoding="utf-8") as f: json.dump(report, f, indent=2, ensure_ascii=False)
    print("\n=== BACKTEST V2 SUMMARY ==="); [print(f"{k}: {v}") for k, v in report["summary"].items()]
    print(f"\nRapor: {report_path}\nTrades: {trades_csv}\nEquity: {eq_csv}")
    return report


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Signal Bot Backtest Engine v2")
    p.add_argument("--symbols", nargs="*", default=None, help="Backtest symbols. Default: all configured symbols.")
    p.add_argument("--days", type=int, default=30, help="Lookback days.")
    p.add_argument("--output-dir", default="backtest_results", help="Output folder.")
    p.add_argument("--max-open-trades", type=int, default=2, help="Max simultaneous virtual trades.")
    p.add_argument("--risk-pct", type=float, default=BASE_RISK_PCT, help="Risk percent per trade, e.g. 0.01.")
    p.add_argument("--one-per-symbol", action="store_true", default=True, help="Allow max one open trade per symbol.")
    p.add_argument("--enable-futures-basis", action="store_true", default=True, help="Fetch MEXC futures klines and use historical basis if available.")
    p.add_argument("--news-events-file", default=None, help="Optional historical news proxy CSV/JSONL file.")
    return p.parse_args()


if __name__ == "__main__":
    run_backtest(parse_args())
