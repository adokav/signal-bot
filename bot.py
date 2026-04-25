import os
import time
import json
import threading
import statistics
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from flask import Flask


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

MEXC_SPOT_BASE = "https://api.mexc.com"
MEXC_FUTURES_BASE = "https://contract.mexc.com"

COINS = {
    "BTCUSDT": "CORE",
    "ETHUSDT": "CORE",
    "AVAXUSDT": "CORE",
    "RENDERUSDT": "HIGH_BETA",
    "ONDOUSDT": "HIGH_BETA",
    "POPCATUSDT": "HIGH_BETA",
}

STATE_FILE = "state.json"

SCAN_INTERVAL = 300
COOLDOWN_SECONDS = 900
SUMMARY_INTERVAL_SECONDS = 4 * 60 * 60
HEARTBEAT_INTERVAL_SECONDS = 60 * 60

MIN_SIGNAL_LEVEL = "MEDIUM"

LEVEL_ORDER = {
    "WEAK": 1,
    "MEDIUM": 2,
    "STRONG": 3,
}

# Cooldown'a takılmaması gereken kritik alert tipleri
CRITICAL_ALERTS = {"❌ SİNYAL İPTAL", "🔄 YÖN DEĞİŞTİ"}

# ABD piyasa tatilleri — yıl bazlı tutuldu, yeni yıl geldiğinde eklenmeli
US_MARKET_HOLIDAYS = {
    2026: {
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
        "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
        "2026-11-26", "2026-12-25",
    },
}

US_EARLY_CLOSE = {
    2026: {"2026-11-27"},
}

THRESHOLDS = {
    "CORE": {
        "long": 1.55,
        "short": -1.55,
        "strong": 2.25,
    },
    "HIGH_BETA": {
        "long": 1.95,
        "short": -1.95,
        "strong": 2.70,
    },
}

WEIGHTS = {
    "CORE": {
        "macro": 0.25,
        "market": 0.22,
        "trend": 0.18,
        "momentum": 0.14,
        "volume": 0.05,
        "liquidity": 0.05,
        "basis": 0.11,
    },
    "HIGH_BETA": {
        "macro": 0.15,
        "market": 0.18,
        "trend": 0.18,
        "momentum": 0.23,
        "volume": 0.10,
        "liquidity": 0.06,
        "basis": 0.10,
    },
}

# Macro/market skor eşikleri — tek yerden tune edilebilsin diye dışa alındı
SCORE_PARAMS = {
    "macro": {
        "change_24h_strong": 2.0,
        "ret_4h_strong": 1.0,
        "weight_change_24h": 1.5,
        "weight_ret_4h": 1.5,
    },
    "market": {
        "ret_1h_strong": 0.5,
    },
    "momentum": {
        "ret_5m_strong": 0.15,
        "ret_15m_strong": 0.35,
        "ret_1h_strong": 0.8,
    },
    "volume": {
        "spike": 2.0,
        "high": 1.3,
        "low": 0.6,
    },
    "liquidity": {
        "spread_bad_core": 12,
        "spread_bad_high_beta": 25,
    },
    "veto": {
        "spread_bad_core": 12,
        "spread_bad_high_beta": 25,
        "vol_low_high_beta": 0.7,
        "btc_unclear_1h": 0.25,
        "btc_unclear_4h": 0.40,
    },
}

app = Flask(__name__)


@app.route("/")
def health():
    return "signal-bot is running"


def now_ts():
    return int(time.time())


def tr_now_text():
    tr_time = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Istanbul"))
    return tr_time.strftime("%Y-%m-%d %H:%M:%S TR")


def get_session_context():
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))

    year = now_et.year
    date_key = now_et.strftime("%Y-%m-%d")
    weekday = now_et.weekday()
    minutes = now_et.hour * 60 + now_et.minute

    regular_open = 9 * 60 + 30
    regular_close = 16 * 60
    early_close = 13 * 60

    holidays = US_MARKET_HOLIDAYS.get(year, set())
    early_closes = US_EARLY_CLOSE.get(year, set())

    # Tatil listesi yok ise uyar — yıl bazlı listenin güncellenmesi gerekiyor
    if year not in US_MARKET_HOLIDAYS:
        print(f"UYARI: {year} yılı için ABD tatil listesi tanımlı değil.")

    if weekday >= 5:
        return {
            "session": "WEEKEND",
            "macro_multiplier": 0.20,
            "micro_multiplier": 1.20,
            "note": "Hafta sonu: VIX/tahvil gibi ABD piyasa verileri bayat kabul edildi.",
        }

    if date_key in holidays:
        return {
            "session": "US_HOLIDAY",
            "macro_multiplier": 0.20,
            "micro_multiplier": 1.20,
            "note": "ABD piyasa tatili: makro/risk verilerinin etkisi azaltıldı.",
        }

    close_time = early_close if date_key in early_closes else regular_close

    if regular_open <= minutes < close_time:
        return {
            "session": "US_OPEN",
            "macro_multiplier": 1.00,
            "micro_multiplier": 1.00,
            "note": "ABD piyasası açık: makro/risk verileri normal ağırlıkta.",
        }

    if 4 * 60 <= minutes < regular_open or close_time <= minutes < 20 * 60:
        return {
            "session": "US_EXTENDED",
            "macro_multiplier": 0.50,
            "micro_multiplier": 1.10,
            "note": "ABD pre/after-market: makro etkisi kısmen azaltıldı.",
        }

    return {
        "session": "US_CLOSED",
        "macro_multiplier": 0.35,
        "micro_multiplier": 1.15,
        "note": "ABD piyasası kapalı: kripto içi sinyallerin ağırlığı artırıldı.",
    }


def send_message(text):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}
    try:
        r = requests.post(url, data=data, timeout=15)
        print("Telegram:", r.status_code, r.text[:200])
    except Exception as e:
        print("Telegram gönderim hatası:", e)


def request_json(url, params=None, retries=3, base_delay=1.5):
    """Geçici 5xx/timeout hatalarına karşı exponential backoff'lu retry."""
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(base_delay * (2 ** i))
    raise last_err


def to_futures_symbol(symbol):
    return symbol.replace("USDT", "_USDT")


def get_klines(symbol, interval="5m", limit=100):
    url = f"{MEXC_SPOT_BASE}/api/v3/klines"
    return request_json(url, {"symbol": symbol, "interval": interval, "limit": limit})


def get_ticker(symbol):
    url = f"{MEXC_SPOT_BASE}/api/v3/ticker/24hr"
    return request_json(url, {"symbol": symbol})


def get_spot_price(symbol):
    url = f"{MEXC_SPOT_BASE}/api/v3/ticker/price"
    data = request_json(url, {"symbol": symbol})
    return float(data["price"])


def get_book(symbol):
    url = f"{MEXC_SPOT_BASE}/api/v3/ticker/bookTicker"
    return request_json(url, {"symbol": symbol})


def get_futures_fair_price(symbol):
    futures_symbol = to_futures_symbol(symbol)
    url = f"{MEXC_FUTURES_BASE}/api/v1/contract/fair_price/{futures_symbol}"
    data = request_json(url)

    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], dict):
            if "fairPrice" in data["data"]:
                return float(data["data"]["fairPrice"])
            if "price" in data["data"]:
                return float(data["data"]["price"])
        if "fairPrice" in data:
            return float(data["fairPrice"])
        if "price" in data:
            return float(data["price"])

    return None


def pct(a, b):
    if b == 0:
        return 0
    return (a - b) / b * 100


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def ema(values, period):
    """SMA-seed'li doğru EMA. Yetersiz veride basit ortalamaya düşer."""
    if not values:
        return 0
    if len(values) < period:
        return sum(values) / len(values)

    sma = sum(values[:period]) / period
    k = 2 / (period + 1)
    result = sma

    for price in values[period:]:
        result = price * k + result * (1 - k)

    return result


def bar(value, max_abs, width=4):
    if max_abs <= 0:
        return "⬜" * width + "│" + "⬜" * width

    ratio = clamp(value / max_abs, -1, 1)
    filled = int(abs(ratio) * width)

    if ratio > 0:
        return "⬜" * width + "│" + "🟩" * filled + "⬜" * (width - filled)

    if ratio < 0:
        return "⬜" * (width - filled) + "🟥" * filled + "│" + "⬜" * width

    return "⬜" * width + "│" + "⬜" * width


def format_price(price):
    """Fiyatın büyüklüğüne göre dinamik decimal."""
    if price is None:
        return "N/A"
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.6f}"
    return f"{price:.8f}"


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"symbols": {}, "meta": {}}
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        # Eski format uyumluluğu
        if "symbols" not in data:
            symbols = {k: v for k, v in data.items() if not k.startswith("_")}
            meta = {
                "last_summary_ts": data.get("_last_summary_ts", 0),
                "last_heartbeat_ts": data.get("_last_heartbeat_ts", 0),
            }
            return {"symbols": symbols, "meta": meta}
        return data
    except Exception:
        return {"symbols": {}, "meta": {}}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print("State kaydedilemedi:", e)


def score_basis(basis_pct, f):
    if basis_pct is None:
        return 0

    score = 0

    if 0.05 <= basis_pct <= 0.35 and f["ret_1h"] > 0:
        score += 1.2
    elif basis_pct > 0.60:
        score -= 1.0
    elif basis_pct < -0.05 and f["ret_1h"] < 0:
        score -= 1.2
    elif basis_pct < -0.60:
        score += 0.5

    if basis_pct > 0 and f["ret_1h"] < -0.5:
        score -= 0.5

    if basis_pct < 0 and f["ret_1h"] > 0.5:
        score += 0.5

    return clamp(score, -2, 2)


def get_features(symbol):
    k5 = get_klines(symbol, "5m", 100)
    k15 = get_klines(symbol, "15m", 100)
    ticker = get_ticker(symbol)
    book = get_book(symbol)

    closes_5 = [float(x[4]) for x in k5]
    closes_15 = [float(x[4]) for x in k15]
    volumes_5 = [float(x[5]) for x in k5]

    if len(closes_5) < 13 or len(closes_15) < 17:
        raise ValueError(f"{symbol} için yetersiz kline verisi (5m={len(closes_5)}, 15m={len(closes_15)})")

    last = closes_5[-1]

    ema9 = ema(closes_5, 9)
    ema21 = ema(closes_5, 21)
    ema50 = ema(closes_5, 50)

    ema9_15 = ema(closes_15, 9)
    ema21_15 = ema(closes_15, 21)
    ema50_15 = ema(closes_15, 50)

    ret_5m = pct(closes_5[-1], closes_5[-2])
    ret_15m = pct(closes_5[-1], closes_5[-4])
    ret_1h = pct(closes_5[-1], closes_5[-12])
    ret_4h = pct(closes_15[-1], closes_15[-16])

    # Spike'lara dayanıklı olsun diye median kullanılıyor (son bar dahil değil)
    recent_vols = volumes_5[-30:-1]
    if recent_vols:
        median_vol = statistics.median(recent_vols)
        vol_ratio = volumes_5[-1] / median_vol if median_vol > 0 else 1
    else:
        vol_ratio = 1

    bid = float(book["bidPrice"])
    ask = float(book["askPrice"])
    mid = (bid + ask) / 2
    spread_bps = ((ask - bid) / mid) * 10000 if mid else 999

    change_24h = float(ticker.get("priceChangePercent", 0))
    spot_price = get_spot_price(symbol)

    futures_price = None
    basis_pct = None

    try:
        futures_price = get_futures_fair_price(symbol)
        if futures_price:
            basis_pct = pct(futures_price, spot_price)
    except Exception as e:
        print(f"{symbol} futures basis alınamadı:", e)

    return {
        "last": last,
        "spot_price": spot_price,
        "futures_price": futures_price,
        "basis_pct": basis_pct,
        "ema9": ema9,
        "ema21": ema21,
        "ema50": ema50,
        "ema9_15": ema9_15,
        "ema21_15": ema21_15,
        "ema50_15": ema50_15,
        "ret_5m": ret_5m,
        "ret_15m": ret_15m,
        "ret_1h": ret_1h,
        "ret_4h": ret_4h,
        "vol_ratio": vol_ratio,
        "spread_bps": spread_bps,
        "change_24h": change_24h,
    }


def score_macro(btc):
    p = SCORE_PARAMS["macro"]
    score = 0

    if btc["change_24h"] > p["change_24h_strong"]:
        score += p["weight_change_24h"]
    elif btc["change_24h"] < -p["change_24h_strong"]:
        score -= p["weight_change_24h"]

    if btc["ret_4h"] > p["ret_4h_strong"]:
        score += p["weight_ret_4h"]
    elif btc["ret_4h"] < -p["ret_4h_strong"]:
        score -= p["weight_ret_4h"]

    return clamp(score, -3, 3)


def score_market(btc, eth):
    threshold = SCORE_PARAMS["market"]["ret_1h_strong"]
    score = 0

    if btc["ret_1h"] > threshold:
        score += 1
    elif btc["ret_1h"] < -threshold:
        score -= 1

    if eth["ret_1h"] > threshold:
        score += 1
    elif eth["ret_1h"] < -threshold:
        score -= 1

    return clamp(score, -2, 2)


def score_trend(f):
    score = 0

    if f["ema9"] > f["ema21"] > f["ema50"]:
        score += 1.2
    elif f["ema9"] < f["ema21"] < f["ema50"]:
        score -= 1.2

    if f["ema9_15"] > f["ema21_15"] > f["ema50_15"]:
        score += 0.8
    elif f["ema9_15"] < f["ema21_15"] < f["ema50_15"]:
        score -= 0.8

    return clamp(score, -2, 2)


def score_momentum(f):
    p = SCORE_PARAMS["momentum"]
    score = 0

    if f["ret_5m"] > p["ret_5m_strong"]:
        score += 0.4
    elif f["ret_5m"] < -p["ret_5m_strong"]:
        score -= 0.4

    if f["ret_15m"] > p["ret_15m_strong"]:
        score += 0.6
    elif f["ret_15m"] < -p["ret_15m_strong"]:
        score -= 0.6

    if f["ret_1h"] > p["ret_1h_strong"]:
        score += 1.0
    elif f["ret_1h"] < -p["ret_1h_strong"]:
        score -= 1.0

    return clamp(score, -2, 2)


def score_volume(f):
    p = SCORE_PARAMS["volume"]
    if f["vol_ratio"] >= p["spike"]:
        return 2
    if f["vol_ratio"] >= p["high"]:
        return 1
    if f["vol_ratio"] < p["low"]:
        return -1
    return 0


def score_liquidity(f, group):
    spread = f["spread_bps"]
    p = SCORE_PARAMS["liquidity"]
    bad_limit = p["spread_bad_core"] if group == "CORE" else p["spread_bad_high_beta"]

    if spread > bad_limit:
        return -2
    if spread > bad_limit / 2:
        return -1
    return 0


def veto_signal(symbol, f, btc, eth):
    group = COINS[symbol]
    p = SCORE_PARAMS["veto"]

    if group == "CORE" and f["spread_bps"] > p["spread_bad_core"]:
        return "Spread yüksek"

    if group == "HIGH_BETA" and f["spread_bps"] > p["spread_bad_high_beta"]:
        return "Spread yüksek"

    if group == "HIGH_BETA" and f["vol_ratio"] < p["vol_low_high_beta"]:
        return "Hacim zayıf"

    if symbol not in ["BTCUSDT", "ETHUSDT"]:
        btc_unclear = (
            abs(btc["ret_1h"]) < p["btc_unclear_1h"]
            and abs(btc["ret_4h"]) < p["btc_unclear_4h"]
        )
        if btc_unclear:
            return "BTC yönü belirsiz"

    return None


def classify_level(score_abs, group):
    strong = THRESHOLDS[group]["strong"]
    medium = THRESHOLDS[group]["long"]

    if score_abs >= strong:
        return "STRONG"
    if score_abs >= medium:
        return "MEDIUM"
    return "WEAK"


def normalize_weights_if_basis_missing(weights, features):
    """Basis verisi yoksa ağırlığını diğer kategorilere yay."""
    if features["basis_pct"] is not None:
        return weights

    active = {k: v for k, v in weights.items() if k != "basis"}
    total_w = sum(active.values())
    if total_w == 0:
        return weights

    redistributed = {k: v / total_w for k, v in active.items()}
    redistributed["basis"] = 0
    return redistributed


def weighted_signal(symbol, features, btc, eth, session_context):
    group = COINS[symbol]
    base_weights = WEIGHTS[group]
    weights = normalize_weights_if_basis_missing(base_weights, features)

    veto = veto_signal(symbol, features, btc, eth)

    macro_multiplier = session_context["macro_multiplier"]
    micro_multiplier = session_context["micro_multiplier"]

    raw = {
        "macro": score_macro(btc) * macro_multiplier,
        "market": score_market(btc, eth) * micro_multiplier,
        "trend": score_trend(features) * micro_multiplier,
        "momentum": score_momentum(features) * micro_multiplier,
        "volume": score_volume(features) * micro_multiplier,
        "liquidity": score_liquidity(features, group),
        "basis": score_basis(features["basis_pct"], features) * micro_multiplier,
    }

    total = sum(raw[k] * weights[k] for k in raw)

    max_total = (
        weights["macro"] * 3
        + weights["market"] * 2
        + weights["trend"] * 2
        + weights["momentum"] * 2
        + weights["volume"] * 2
        + weights["liquidity"] * 2
        + weights["basis"] * 2
    )

    confidence = round(abs(total) / max_total * 100, 1) if max_total > 0 else 0

    if veto:
        signal = "NO_TRADE"
        level = "WEAK"
    elif total >= THRESHOLDS[group]["long"]:
        signal = "LONG"
        level = classify_level(abs(total), group)
    elif total <= THRESHOLDS[group]["short"]:
        signal = "SHORT"
        level = classify_level(abs(total), group)
    else:
        signal = "NO_TRADE"
        level = "WEAK"

    return {
        "symbol": symbol,
        "group": group,
        "signal": signal,
        "level": level,
        "score": round(total, 3),
        "max_score": round(max_total, 3),
        "confidence": confidence,
        "raw": raw,
        "features": features,
        "veto": veto,
        "session_context": session_context,
        "basis_missing": features["basis_pct"] is None,
    }


def format_signal(result, title):
    f = result["features"]
    raw = result["raw"]
    session = result["session_context"]

    basis_text = "N/A"
    futures_text = "N/A"

    if f["basis_pct"] is not None:
        basis_text = f"{f['basis_pct']:+.3f}%"

    if f["futures_price"] is not None:
        futures_text = format_price(f["futures_price"])

    lines = [
        title,
        f"{result['symbol']} → {result['signal']} / {result['level']}",
        f"Skor: {result['score']} / {result['max_score']} {bar(result['score'], result['max_score'])}",
        f"Güven: %{result['confidence']}",
        f"Seans: {session['session']}",
        f"Makro Çarpan: x{session['macro_multiplier']}",
        f"Mikro Çarpan: x{session['micro_multiplier']}",
        f"Not: {session['note']}",
        "",
        "Alt Skorlar:",
        f"Macro: {raw['macro']:+.2f} / 3 {bar(raw['macro'], 3)}",
        f"Market: {raw['market']:+.2f} / 2 {bar(raw['market'], 2)}",
        f"Trend: {raw['trend']:+.2f} / 2 {bar(raw['trend'], 2)}",
        f"Momentum: {raw['momentum']:+.2f} / 2 {bar(raw['momentum'], 2)}",
        f"Volume: {raw['volume']:+.2f} / 2 {bar(raw['volume'], 2)}",
        f"Liquidity: {raw['liquidity']:+.2f} / 2 {bar(raw['liquidity'], 2)}",
        f"Basis: {raw['basis']:+.2f} / 2 {bar(raw['basis'], 2)}",
        "",
        f"Spot: {format_price(f['spot_price'])}",
        f"Futures Fair: {futures_text}",
        f"Basis: {basis_text}",
        f"5m: %{f['ret_5m']:.2f} | 15m: %{f['ret_15m']:.2f} | 1h: %{f['ret_1h']:.2f} | 4h: %{f['ret_4h']:.2f}",
        f"Hacim Oranı: {f['vol_ratio']:.2f}x | Spread: {f['spread_bps']:.2f} bps",
    ]

    if result.get("basis_missing"):
        lines.append("Not: Basis verisi yok, ağırlıklar yeniden dağıtıldı.")

    if result["veto"]:
        lines.append(f"Veto: {result['veto']}")

    lines.append(f"Zaman: {tr_now_text()}")

    return "\n".join(lines)


def should_notify_signal(result):
    if result["signal"] == "NO_TRADE":
        return False

    return LEVEL_ORDER[result["level"]] >= LEVEL_ORDER[MIN_SIGNAL_LEVEL]


def decide_alert(old, new):
    if not should_notify_signal(new):
        if old and old.get("signal") in ["LONG", "SHORT"] and new["signal"] == "NO_TRADE":
            return "❌ SİNYAL İPTAL"
        return None

    if not old:
        return "🚀 YENİ SİNYAL"

    old_signal = old.get("signal")
    new_signal = new["signal"]

    if old_signal == "NO_TRADE" and new_signal in ["LONG", "SHORT"]:
        return "🚀 YENİ SİNYAL"

    if old_signal in ["LONG", "SHORT"] and new_signal == "NO_TRADE":
        return "❌ SİNYAL İPTAL"

    if old_signal != new_signal and old_signal != "NO_TRADE" and new_signal != "NO_TRADE":
        return "🔄 YÖN DEĞİŞTİ"

    if old_signal == new_signal and new_signal != "NO_TRADE":
        old_conf = float(old.get("confidence", 0))
        old_score = float(old.get("score", 0))

        if old_conf - new["confidence"] >= 20:
            return "⚠️ SİNYAL ZAYIFLADI"

        if abs(old_score) - abs(new["score"]) >= 1.0:
            return "⚠️ SKOR ZAYIFLADI"

    return None


def should_cooldown(old, alert_type):
    """Kritik alertler (iptal, yön değişimi) cooldown'a takılmaz."""
    if alert_type in CRITICAL_ALERTS:
        return False
    if not old:
        return False
    last_alert = old.get("last_alert_ts", 0)
    return now_ts() - last_alert < COOLDOWN_SECONDS


def make_summary(results, session_context):
    longs = [f"{r['symbol']}({r['level']})" for r in results if r["signal"] == "LONG"]
    shorts = [f"{r['symbol']}({r['level']})" for r in results if r["signal"] == "SHORT"]
    neutral = [r["symbol"] for r in results if r["signal"] == "NO_TRADE"]

    return (
        "📊 DURUM ÖZETİ\n\n"
        f"Seans: {session_context['session']}\n"
        f"Makro Çarpan: x{session_context['macro_multiplier']}\n"
        f"Mikro Çarpan: x{session_context['micro_multiplier']}\n"
        f"Not: {session_context['note']}\n\n"
        f"LONG: {', '.join(longs) if longs else '-'}\n"
        f"SHORT: {', '.join(shorts) if shorts else '-'}\n"
        f"NO_TRADE: {', '.join(neutral) if neutral else '-'}\n"
        f"Zaman: {tr_now_text()}"
    )


def bot_loop():
    print("BOT BAŞLADI")
    send_message("BOT BAŞLADI 🚀 Session + basis katmanları aktif.")

    state = load_state()

    # İlk açılışta hemen özet/heartbeat atmaması için zamanları şimdiye çek
    if not state["meta"].get("last_summary_ts"):
        state["meta"]["last_summary_ts"] = now_ts()
    if not state["meta"].get("last_heartbeat_ts"):
        state["meta"]["last_heartbeat_ts"] = now_ts()

    while True:
        try:
            session_context = get_session_context()

            # BTC ve ETH temel referans — alınamazsa tüm tur anlamsız
            try:
                btc = get_features("BTCUSDT")
                eth = get_features("ETHUSDT")
            except Exception as e:
                print("BTC/ETH features alınamadı, tur atlanıyor:", e)
                time.sleep(60)
                continue

            results = []
            features_cache = {"BTCUSDT": btc, "ETHUSDT": eth}

            for symbol in COINS:
                try:
                    if symbol in features_cache:
                        features = features_cache[symbol]
                    else:
                        features = get_features(symbol)

                    result = weighted_signal(symbol, features, btc, eth, session_context)
                    results.append(result)

                    old = state["symbols"].get(symbol)
                    alert_type = decide_alert(old, result)

                    if alert_type and not should_cooldown(old, alert_type):
                        send_message(format_signal(result, alert_type))
                        last_alert_ts = now_ts()
                    else:
                        last_alert_ts = old.get("last_alert_ts", 0) if old else 0

                    state["symbols"][symbol] = {
                        "signal": result["signal"],
                        "level": result["level"],
                        "score": result["score"],
                        "confidence": result["confidence"],
                        "last_alert_ts": last_alert_ts,
                    }

                except Exception as e:
                    print(f"{symbol} bu turda atlandı:", e)
                    continue

            last_summary = state["meta"].get("last_summary_ts", 0)
            if now_ts() - last_summary >= SUMMARY_INTERVAL_SECONDS and results:
                send_message(make_summary(results, session_context))
                state["meta"]["last_summary_ts"] = now_ts()

            last_heartbeat = state["meta"].get("last_heartbeat_ts", 0)
            if now_ts() - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                send_message(
                    f"✅ Bot aktif\n"
                    f"Son kontrol: {tr_now_text()}\n"
                    f"Seans: {session_context['session']}\n"
                    f"Makro Çarpan: x{session_context['macro_multiplier']}\n"
                    f"Mikro Çarpan: x{session_context['micro_multiplier']}"
                )
                state["meta"]["last_heartbeat_ts"] = now_ts()

            save_state(state)
            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print("ANA HATA:", e)
            try:
                send_message(f"⚠️ Bot hata aldı:\n{e}\nZaman: {tr_now_text()}")
            except Exception:
                pass
            time.sleep(60)


if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
