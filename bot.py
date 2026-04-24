import os
import time
import json
import threading
from datetime import datetime, timezone

import requests
from flask import Flask

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
MEXC_BASE = "https://api.mexc.com"

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

WEIGHTS = {
    "CORE": {
        "macro": 0.35,
        "market": 0.25,
        "momentum": 0.15,
        "volume": 0.10,
        "liquidity": 0.10,
        "derivatives": 0.05,
    },
    "HIGH_BETA": {
        "macro": 0.20,
        "market": 0.20,
        "momentum": 0.25,
        "volume": 0.15,
        "liquidity": 0.10,
        "derivatives": 0.10,
    },
}

THRESHOLDS = {
    "CORE": 1.20,
    "HIGH_BETA": 1.60,
}

app = Flask(__name__)


@app.route("/")
def health():
    return "signal-bot is running"


def now_ts():
    return int(time.time())


def utc_now_text():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def send_message(text):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}
    r = requests.post(url, data=data, timeout=15)
    print("Telegram:", r.status_code, r.text[:200])


def request_json(url, params=None):
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def get_klines(symbol, interval="5m", limit=60):
    url = f"{MEXC_BASE}/api/v3/klines"
    return request_json(url, {"symbol": symbol, "interval": interval, "limit": limit})


def get_ticker(symbol):
    url = f"{MEXC_BASE}/api/v3/ticker/24hr"
    return request_json(url, {"symbol": symbol})


def get_book(symbol):
    url = f"{MEXC_BASE}/api/v3/ticker/bookTicker"
    return request_json(url, {"symbol": symbol})


def pct(a, b):
    if b == 0:
        return 0
    return (a - b) / b * 100


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def bar(value, max_abs, width=6):
    if max_abs <= 0:
        return "⬜" * width + "│" + "⬜" * width

    ratio = clamp(value / max_abs, -1, 1)
    filled = int(abs(ratio) * width)

    if ratio > 0:
        return "⬜" * width + "│" + "🟩" * filled + "⬜" * (width - filled)
    if ratio < 0:
        return "⬜" * (width - filled) + "🟥" * filled + "│" + "⬜" * width
    return "⬜" * width + "│" + "⬜" * width


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def feature_from_symbol(symbol):
    k5 = get_klines(symbol, "5m", 60)
    k15 = get_klines(symbol, "15m", 60)
    ticker = get_ticker(symbol)
    book = get_book(symbol)

    closes_5 = [float(x[4]) for x in k5]
    closes_15 = [float(x[4]) for x in k15]
    volumes_5 = [float(x[5]) for x in k5]

    last = closes_5[-1]
    ret_5m = pct(closes_5[-1], closes_5[-2])
    ret_15m = pct(closes_5[-1], closes_5[-4])
    ret_1h = pct(closes_5[-1], closes_5[-12])
    ret_4h = pct(closes_15[-1], closes_15[-16])

    avg_vol = sum(volumes_5[-30:-1]) / max(1, len(volumes_5[-30:-1]))
    vol_ratio = volumes_5[-1] / avg_vol if avg_vol else 1

    bid = float(book["bidPrice"])
    ask = float(book["askPrice"])
    mid = (bid + ask) / 2
    spread_bps = ((ask - bid) / mid) * 10000 if mid else 999

    change_24h = float(ticker.get("priceChangePercent", 0))

    return {
        "last": last,
        "ret_5m": ret_5m,
        "ret_15m": ret_15m,
        "ret_1h": ret_1h,
        "ret_4h": ret_4h,
        "vol_ratio": vol_ratio,
        "spread_bps": spread_bps,
        "change_24h": change_24h,
    }


def score_macro(btc_features):
    score = 0
    if btc_features["change_24h"] > 2:
        score += 2
    elif btc_features["change_24h"] < -2:
        score -= 2

    if btc_features["ret_4h"] > 1:
        score += 1
    elif btc_features["ret_4h"] < -1:
        score -= 1

    return clamp(score, -3, 3)


def score_market(btc, eth):
    score = 0
    if btc["ret_1h"] > 0.4:
        score += 1
    elif btc["ret_1h"] < -0.4:
        score -= 1

    if eth["ret_1h"] > 0.4:
        score += 1
    elif eth["ret_1h"] < -0.4:
        score -= 1

    return clamp(score, -2, 2)


def score_momentum(f):
    score = 0
    if f["ret_5m"] > 0.15:
        score += 0.5
    elif f["ret_5m"] < -0.15:
        score -= 0.5

    if f["ret_15m"] > 0.35:
        score += 0.7
    elif f["ret_15m"] < -0.35:
        score -= 0.7

    if f["ret_1h"] > 0.8:
        score += 0.8
    elif f["ret_1h"] < -0.8:
        score -= 0.8

    return clamp(score, -2, 2)


def score_volume(f):
    if f["vol_ratio"] >= 2.0:
        return 2
    if f["vol_ratio"] >= 1.3:
        return 1
    if f["vol_ratio"] < 0.6:
        return -1
    return 0


def score_liquidity(f, group):
    spread = f["spread_bps"]
    bad_limit = 15 if group == "CORE" else 30

    if spread > bad_limit:
        return -2
    if spread > bad_limit / 2:
        return -1
    return 0


def score_derivatives_proxy(f):
    score = 0
    if f["change_24h"] > 3 and f["ret_1h"] > 0:
        score += 1
    elif f["change_24h"] < -3 and f["ret_1h"] < 0:
        score -= 1

    if abs(f["change_24h"]) > 8:
        score -= 0.5

    return clamp(score, -2, 2)


def weighted_signal(symbol, features, btc, eth):
    group = COINS[symbol]
    weights = WEIGHTS[group]

    raw = {
        "macro": score_macro(btc),
        "market": score_market(btc, eth),
        "momentum": score_momentum(features),
        "volume": score_volume(features),
        "liquidity": score_liquidity(features, group),
        "derivatives": score_derivatives_proxy(features),
    }

    total = sum(raw[k] * weights[k] for k in raw)

    max_total = (
        weights["macro"] * 3
        + weights["market"] * 2
        + weights["momentum"] * 2
        + weights["volume"] * 2
        + weights["liquidity"] * 2
        + weights["derivatives"] * 2
    )

    confidence = round(abs(total) / max_total * 100, 1)

    threshold = THRESHOLDS[group]

    if total >= threshold:
        signal = "LONG"
    elif total <= -threshold:
        signal = "SHORT"
    else:
        signal = "NO_TRADE"

    return {
        "symbol": symbol,
        "group": group,
        "signal": signal,
        "score": round(total, 3),
        "max_score": round(max_total, 3),
        "confidence": confidence,
        "raw": raw,
        "features": features,
    }


def format_signal(result, title):
    f = result["features"]
    raw = result["raw"]

    lines = [
        title,
        f"{result['symbol']} → {result['signal']}",
        f"Skor: {result['score']} / {result['max_score']} {bar(result['score'], result['max_score'])}",
        f"Güven: %{result['confidence']}",
        "",
        "Alt Skorlar:",
        f"Macro: {raw['macro']:+.2f} / 3 {bar(raw['macro'], 3)}",
        f"Market: {raw['market']:+.2f} / 2 {bar(raw['market'], 2)}",
        f"Momentum: {raw['momentum']:+.2f} / 2 {bar(raw['momentum'], 2)}",
        f"Volume: {raw['volume']:+.2f} / 2 {bar(raw['volume'], 2)}",
        f"Liquidity: {raw['liquidity']:+.2f} / 2 {bar(raw['liquidity'], 2)}",
        f"DerivProxy: {raw['derivatives']:+.2f} / 2 {bar(raw['derivatives'], 2)}",
        "",
        f"Fiyat: {f['last']}",
        f"5m: %{f['ret_5m']:.2f} | 15m: %{f['ret_15m']:.2f} | 1h: %{f['ret_1h']:.2f}",
        f"Hacim Oranı: {f['vol_ratio']:.2f}x | Spread: {f['spread_bps']:.2f} bps",
        f"Zaman: {utc_now_text()}",
    ]
    return "\n".join(lines)


def decide_alert(old, new):
    if not old:
        if new["signal"] != "NO_TRADE":
            return "🚀 YENİ SİNYAL"
        return None

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


def should_cooldown(old):
    if not old:
        return False
    last_alert = old.get("last_alert_ts", 0)
    return now_ts() - last_alert < COOLDOWN_SECONDS


def make_summary(results):
    longs = [r["symbol"] for r in results if r["signal"] == "LONG"]
    shorts = [r["symbol"] for r in results if r["signal"] == "SHORT"]
    neutral = [r["symbol"] for r in results if r["signal"] == "NO_TRADE"]

    return (
        "📊 DURUM ÖZETİ\n\n"
        f"LONG: {', '.join(longs) if longs else '-'}\n"
        f"SHORT: {', '.join(shorts) if shorts else '-'}\n"
        f"NO_TRADE: {', '.join(neutral) if neutral else '-'}\n"
        f"Zaman: {utc_now_text()}"
    )


def bot_loop():
    print("BOT BAŞLADI")
    send_message("BOT BAŞLADI 🚀 Gerçek sinyal motoru aktif.")

    state = load_state()

    while True:
        try:
            results = []
            btc = feature_from_symbol("BTCUSDT")
            eth = feature_from_symbol("ETHUSDT")

            for symbol in COINS:
                features = btc if symbol == "BTCUSDT" else eth if symbol == "ETHUSDT" else feature_from_symbol(symbol)
                result = weighted_signal(symbol, features, btc, eth)
                results.append(result)

                old = state.get(symbol)
                alert_type = decide_alert(old, result)

                if alert_type and not should_cooldown(old):
                    send_message(format_signal(result, alert_type))
                    result["last_alert_ts"] = now_ts()
                else:
                    result["last_alert_ts"] = old.get("last_alert_ts", 0) if old else 0

                state[symbol] = {
                    "signal": result["signal"],
                    "score": result["score"],
                    "confidence": result["confidence"],
                    "last_alert_ts": result["last_alert_ts"],
                }

            last_summary = state.get("_last_summary_ts", 0)
            if now_ts() - last_summary >= SUMMARY_INTERVAL_SECONDS:
                send_message(make_summary(results))
                state["_last_summary_ts"] = now_ts()

            save_state(state)
            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print("ANA HATA:", e)
            time.sleep(60)


if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
