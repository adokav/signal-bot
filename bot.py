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
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

MEXC_SPOT_BASE = "https://api.mexc.com"
MEXC_FUTURES_BASE = "https://contract.mexc.com"

STATE_FILE = "state.json"

SCAN_INTERVAL = 300
COOLDOWN_SECONDS = 900
SUMMARY_INTERVAL_SECONDS = 4 * 60 * 60
HEARTBEAT_INTERVAL_SECONDS = 60 * 60

NEWS_SCAN_INTERVAL_SECONDS = 15 * 60
NEWS_ALERT_COOLDOWN_SECONDS = 30 * 60

MIN_SIGNAL_LEVEL = "MEDIUM"

COINS = {
    "BTCUSDT": "CORE",
    "ETHUSDT": "CORE",
    "AVAXUSDT": "CORE",
    "RENDERUSDT": "HIGH_BETA",
    "ONDOUSDT": "HIGH_BETA",
    "POPCATUSDT": "HIGH_BETA",
}

LEVEL_ORDER = {
    "WEAK": 1,
    "MEDIUM": 2,
    "STRONG": 3,
}

CRITICAL_ALERTS = {
    "❌ SİNYAL İPTAL",
    "🔄 YÖN DEĞİŞTİ",
}

US_MARKET_HOLIDAYS = {
    2026: {
        "2026-01-01",
        "2026-01-19",
        "2026-02-16",
        "2026-04-03",
        "2026-05-25",
        "2026-06-19",
        "2026-07-03",
        "2026-09-07",
        "2026-11-26",
        "2026-12-25",
    }
}

US_EARLY_CLOSE = {
    2026: {
        "2026-11-27",
    }
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
        "macro": 0.23,
        "market": 0.20,
        "trend": 0.17,
        "momentum": 0.13,
        "volume": 0.05,
        "liquidity": 0.05,
        "basis": 0.09,
        "news": 0.08,
    },
    "HIGH_BETA": {
        "macro": 0.13,
        "market": 0.17,
        "trend": 0.17,
        "momentum": 0.21,
        "volume": 0.09,
        "liquidity": 0.05,
        "basis": 0.06,
        "news": 0.12,
    },
}

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

NEWS_CATEGORIES = {
    "WAR": {
        "keywords": [
            "war", "missile", "attack", "strike", "military", "iran",
            "israel", "china", "taiwan", "russia", "ukraine",
            "hormuz", "nuclear", "escalation", "conflict",
        ],
        "risk": -3,
    },
    "TARIFF": {
        "keywords": [
            "trump", "tariff", "trade war", "sanction", "china tariff",
            "import duty", "export control", "retaliation",
        ],
        "risk": -2,
    },
    "FED_MACRO": {
        "keywords": [
            "fed", "powell", "cpi", "pce", "inflation", "payroll",
            "nfp", "unemployment", "rate cut", "rate hike",
            "treasury yields", "jobs report",
        ],
        "risk": -1,
    },
    "CRYPTO_RISK": {
        "keywords": [
            "sec", "etf", "hack", "exploit", "stablecoin", "depeg",
            "binance", "mexc", "liquidation", "exchange outage",
        ],
        "risk": -2,
    },
    "RISK_ON": {
        "keywords": [
            "ceasefire", "deal reached", "tariff pause", "rate cut hopes",
            "etf approval", "peace talks", "diplomatic breakthrough",
        ],
        "risk": 2,
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

    if year not in US_MARKET_HOLIDAYS:
        print(f"UYARI: {year} yılı için ABD tatil listesi tanımlı değil.")

    if weekday >= 5:
        return {
            "session": "WEEKEND",
            "macro_multiplier": 0.20,
            "micro_multiplier": 1.20,
            "note": "Hafta sonu: ABD piyasa verileri bayat kabul edildi.",
        }

    if date_key in holidays:
        return {
            "session": "US_HOLIDAY",
            "macro_multiplier": 0.20,
            "micro_multiplier": 1.20,
            "note": "ABD piyasa tatili: makro/risk etkisi azaltıldı.",
        }

    close_time = early_close if date_key in early_closes else regular_close

    if regular_open <= minutes < close_time:
        return {
            "session": "US_OPEN",
            "macro_multiplier": 1.00,
            "micro_multiplier": 1.00,
            "note": "ABD piyasası açık: makro/risk normal ağırlıkta.",
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
        "note": "ABD piyasası kapalı: kripto içi sinyaller artırıldı.",
    }


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


def default
