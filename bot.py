"""
Signal Bot - Integrated Movement + News + Session + Basis.

Production-grade kripto sinyal botu. MEXC uzerinden coin verilerini ceker,
makro/trend/momentum/volume/likidite/basis skorlarini hesaplar, news katmani
ile yon-bagimli modulasyon uygular ve Telegram'a sinyal gonderir.

Mimari:
    - Flask: health endpoint
    - Background thread: ana bot dongusu
    - State: atomik dosya yazimi + thread-safe
    - HTTP: thread-local requests.Session, bounded concurrency, akilli retry
    - News: NewsAPI + GDELT, kategori bazli keyword classification
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import random
import re
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests
from flask import Flask
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

__version__ = "1.2.0"

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("signal-bot")


def env_int(name: str, default: int, *, min_value: Optional[int] = None) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("%s=%r gecersiz, varsayilan kullaniliyor: %s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        log.warning("%s=%s minimumun altinda, varsayilan kullaniliyor: %s", name, value, default)
        return default
    return value


def env_float(name: str, default: float, *, min_value: Optional[float] = None) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("%s=%r gecersiz, varsayilan kullaniliyor: %s", name, raw, default)
        return default
    if min_value is not None and value < min_value:
        log.warning("%s=%s minimumun altinda, varsayilan kullaniliyor: %s", name, value, default)
        return default
    return value


# ============================================================
# ENV VARIABLES
# ============================================================

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
PORT = env_int("PORT", 10000, min_value=1)


def validate_env() -> None:
    """Startup-time env validation.

    STRICT_ENV=1 verilirse kritik degisken eksiginde process baslamaz.
    """
    missing = []
    if not TOKEN:
        missing.append("TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")
    if missing:
        msg = f"Eksik env degiskenleri: {', '.join(missing)} - Telegram mesajlari gonderilemez."
        if os.getenv("STRICT_ENV") == "1":
            log.error(msg)
            raise SystemExit(1)
        log.warning(msg)
    if not NEWS_API_KEY:
        log.info("NEWS_API_KEY eksik - sadece GDELT kullanilacak.")


# ============================================================
# CONSTANTS - BASE URLS
# ============================================================

MEXC_SPOT_BASE = "https://api.mexc.com"
MEXC_FUTURES_BASE = "https://contract.mexc.com"
TELEGRAM_BASE = "https://api.telegram.org"
NEWSAPI_BASE = "https://newsapi.org/v2"
GDELT_BASE = "https://api.gdeltproject.org/api/v2"

# ============================================================
# COIN UNIVERSE
# ============================================================

COINS: dict[str, str] = {
    "BTCUSDT": "CORE",
    "ETHUSDT": "CORE",
    "AVAXUSDT": "CORE",
    "SOLUSDT": "CORE",
    "LINKUSDT": "CORE",
    "RENDERUSDT": "HIGH_BETA",
    "ONDOUSDT": "HIGH_BETA",
    "LDOUSDT": "HIGH_BETA",
    "POPCATUSDT": "HIGH_BETA",
}

# ============================================================
# TIMING CONFIG
# ============================================================

STATE_FILE = os.getenv("STATE_FILE", "state.json")
STATE_VERSION = 3

SCAN_INTERVAL = env_int("SCAN_INTERVAL", 300, min_value=15)
COOLDOWN_SECONDS = env_int("COOLDOWN_SECONDS", 900, min_value=0)
SUMMARY_INTERVAL_SECONDS = env_int("SUMMARY_INTERVAL_SECONDS", 4 * 60 * 60, min_value=60)
HEARTBEAT_INTERVAL_SECONDS = env_int("HEARTBEAT_INTERVAL_SECONDS", 60 * 60, min_value=60)

NEWS_SCAN_INTERVAL_SECONDS = env_int("NEWS_SCAN_INTERVAL_SECONDS", 15 * 60, min_value=60)
NEWS_ALERT_COOLDOWN_SECONDS = env_int("NEWS_ALERT_COOLDOWN_SECONDS", 30 * 60, min_value=0)
NEWS_MAX_AGE_HOURS = env_int("NEWS_MAX_AGE_HOURS", 2, min_value=1)

MOVEMENT_ALERT_COOLDOWN_SECONDS = env_int(
    "MOVEMENT_ALERT_COOLDOWN_SECONDS", 30 * 60, min_value=0
)

SEND_STANDALONE_NEWS_ALERTS = os.getenv("SEND_STANDALONE_NEWS_ALERTS", "0") == "1"
SEND_MOVEMENT_ALERTS = os.getenv("SEND_MOVEMENT_ALERTS", "0") == "1"

ERROR_BACKOFF_SHORT = env_int("ERROR_BACKOFF_SHORT", 60, min_value=1)
ERROR_BACKOFF_LONG = env_int("ERROR_BACKOFF_LONG", 300, min_value=1)

# ============================================================
# FEATURE / KLINE CONFIG
# ============================================================

KLINE_LIMIT = env_int("KLINE_LIMIT", 100, min_value=20)
VOLUME_WINDOW = env_int("VOLUME_WINDOW", 30, min_value=5)
MIN_KLINES_5M = 13
MIN_KLINES_15M = 17

RET_5M_OFFSET = 2
RET_15M_OFFSET = 4
RET_1H_OFFSET = 12
RET_4H_OFFSET = 16

EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 50

# ============================================================
# MOVEMENT ALERT THRESHOLDS
# ============================================================

MOVEMENT_ALERT_THRESHOLDS: dict[str, dict[str, float]] = {
    "CORE": {"ret_15m": 2.0, "ret_1h": 3.0, "volume_ratio": 2.5},
    "HIGH_BETA": {"ret_15m": 3.0, "ret_1h": 5.0, "volume_ratio": 2.0},
}

# ============================================================
# TRADE PLAN CONFIG
# ============================================================

ACCOUNT_SIZE_USD = env_float("ACCOUNT_SIZE_USD", 800.0, min_value=1.0)
RISK_PCT_PER_TRADE = env_float("RISK_PCT_PER_TRADE", 0.01, min_value=0.0001)

TRADE_PLAN_CONFIG: dict[str, dict[str, float]] = {
    "CORE": {"stop_pct": 0.020, "tp1_r": 1.0, "tp2_r": 2.0, "tp3_r": 3.0},
    "HIGH_BETA": {"stop_pct": 0.035, "tp1_r": 1.0, "tp2_r": 2.0, "tp3_r": 3.0},
}

# ============================================================
# SIGNAL CONFIG
# ============================================================

MIN_SIGNAL_LEVEL = os.getenv("MIN_SIGNAL_LEVEL", "MEDIUM").upper()
LEVEL_ORDER = {"WEAK": 1, "MEDIUM": 2, "STRONG": 3}
if MIN_SIGNAL_LEVEL not in LEVEL_ORDER:
    log.warning("MIN_SIGNAL_LEVEL=%r gecersiz, MEDIUM kullaniliyor.", MIN_SIGNAL_LEVEL)
    MIN_SIGNAL_LEVEL = "MEDIUM"

CRITICAL_ALERTS = frozenset({"❌ SİNYAL İPTAL", "🔄 YÖN DEĞİŞTİ", "🛑 NEWS VETO"})

THRESHOLDS: dict[str, dict[str, float]] = {
    "CORE": {"long": 1.55, "short": -1.55, "strong": 2.25},
    "HIGH_BETA": {"long": 1.95, "short": -1.95, "strong": 2.70},
}

WEIGHTS: dict[str, dict[str, float]] = {
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

SPREAD_LIMITS: dict[str, float] = {"CORE": 12, "HIGH_BETA": 25}

SCORE_PARAMS: dict[str, dict[str, float]] = {
    "macro": {
        "change_24h_strong": 2.0,
        "ret_4h_strong": 1.0,
        "weight_change_24h": 1.5,
        "weight_ret_4h": 1.5,
    },
    "market": {"ret_1h_strong": 0.5},
    "momentum": {"ret_5m_strong": 0.15, "ret_15m_strong": 0.35, "ret_1h_strong": 0.8},
    "volume": {"spike": 2.0, "high": 1.3, "low": 0.6},
    "veto": {"vol_low_high_beta": 0.7, "btc_unclear_1h": 0.25, "btc_unclear_4h": 0.40},
}

# ============================================================
# US MARKET CALENDAR
# ============================================================

US_MARKET_HOLIDAYS: dict[int, set[str]] = {
    2025: {
        "2025-01-01",
        "2025-01-20",
        "2025-02-17",
        "2025-04-18",
        "2025-05-26",
        "2025-06-19",
        "2025-07-04",
        "2025-09-01",
        "2025-11-27",
        "2025-12-25",
    },
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
    },
    2027: {
        "2027-01-01",
        "2027-01-18",
        "2027-02-15",
        "2027-03-26",
        "2027-05-31",
        "2027-06-18",
        "2027-07-05",
        "2027-09-06",
        "2027-11-25",
        "2027-12-24",
    },
}

US_EARLY_CLOSE: dict[int, set[str]] = {
    2025: {"2025-11-28", "2025-12-24"},
    2026: {"2026-11-27", "2026-12-24"},
    2027: {"2027-11-26"},
}

# ============================================================
# NEWS CONFIG
# ============================================================

NEWS_CATEGORIES: dict[str, dict[str, Any]] = {
    "WAR": {
        "keywords": [
            "war",
            "missile",
            "attack",
            "strike",
            "military",
            "iran",
            "israel",
            "taiwan",
            "russia",
            "ukraine",
            "hormuz",
            "nuclear",
            "escalation",
            "invasion",
        ],
        "risk": -3,
    },
    "TARIFF": {
        "keywords": [
            "trump tariff",
            "tariff",
            "trade war",
            "sanction",
            "china tariff",
            "import duty",
            "export ban",
        ],
        "risk": -2,
    },
    "FED_MACRO": {
        "keywords": [
            "powell",
            "cpi",
            "pce",
            "inflation",
            "payroll",
            "nfp",
            "unemployment",
            "rate cut",
            "rate hike",
            "fed funds",
            "fomc",
        ],
        "risk": -1,
    },
    "CRYPTO_RISK": {
        "keywords": [
            "stablecoin depeg",
            "exchange hack",
            "crypto hack",
            "exchange outage",
            "liquidation cascade",
            "etf rejection",
            "sec lawsuit",
            "depeg",
            "exploit",
            "binance",
            "coinbase",
            "kraken",
        ],
        "risk": -2,
    },
    "RISK_ON": {
        "keywords": [
            "ceasefire",
            "deal reached",
            "tariff pause",
            "rate cut hopes",
            "etf approval",
            "peace agreement",
            "diplomatic breakthrough",
        ],
        "risk": 2,
    },
}

NEWS_VETO_THRESHOLD = 2.0
NEWS_DAMPEN_THRESHOLD = 1.0
NEWS_DAMPEN_FACTOR = 0.6
NEWS_BOOST_THRESHOLD = 1.0
NEWS_BOOST_FACTOR = 1.1
NEWS_MULTI_SOURCE_BOOST = 3
NEWS_MULTI_SOURCE_FACTOR = 1.2

NEWSAPI_QUERIES_PER_SCAN = 3
NEWSAPI_DAILY_LIMIT = env_int("NEWSAPI_DAILY_LIMIT", 90, min_value=1)

# ============================================================
# HTTP SESSION
# ============================================================

HTTP_MAX_CONCURRENCY = env_int("HTTP_MAX_CONCURRENCY", 24, min_value=1)
_HTTP_SEMAPHORE = threading.BoundedSemaphore(HTTP_MAX_CONCURRENCY)
_HTTP_LOCAL = threading.local()
_HTTP_SESSION_LOCK = threading.RLock()
_HTTP_SESSIONS: list[requests.Session] = []


def _build_session() -> requests.Session:
    """Per-thread requests.Session; connection pool thread icinde yeniden kullanilir."""
    sess = requests.Session()
    retry = Retry(total=0, backoff_factor=0, status_forcelist=[])
    adapter = HTTPAdapter(pool_connections=32, pool_maxsize=64, max_retries=retry)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    sess.headers.update({"User-Agent": f"signal-bot/{__version__}"})
    return sess


def _http_session() -> requests.Session:
    sess = getattr(_HTTP_LOCAL, "session", None)
    if sess is None:
        sess = _build_session()
        _HTTP_LOCAL.session = sess
        with _HTTP_SESSION_LOCK:
            _HTTP_SESSIONS.append(sess)
    return sess


def close_http_sessions() -> None:
    with _HTTP_SESSION_LOCK:
        sessions = list(_HTTP_SESSIONS)
        _HTTP_SESSIONS.clear()
    for sess in sessions:
        try:
            sess.close()
        except Exception:
            pass


# Feature engine icin kalici thread pool.
_FEATURE_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(16, len(COINS) * 5),
    thread_name_prefix="feat",
)
_SYMBOL_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(4, len(COINS)),
    thread_name_prefix="symbol",
)

_STOP_EVENT = threading.Event()

# ============================================================
# FLASK HEALTH SERVER
# ============================================================

app = Flask(__name__)


@app.route("/")
def health() -> str:
    return "signal-bot is running"


@app.route("/healthz")
def healthz() -> tuple[dict, int]:
    try:
        state = _STATE_MGR.snapshot()
        meta = state.get("meta", {})
        now = now_ts()
        last_success = meta.get("last_successful_scan_ts", 0)
        return (
            {
                "status": "ok",
                "version": __version__,
                "last_successful_scan_age_s": now - last_success if last_success else None,
                "last_news_scan_age_s": now - meta.get("last_news_scan_ts", 0)
                if meta.get("last_news_scan_ts")
                else None,
                "consecutive_failures": meta.get("consecutive_failures", 0),
            },
            200,
        )
    except Exception as e:
        return ({"status": "error", "error": str(e)}, 500)


@app.route("/readyz")
def readyz() -> tuple[dict, int]:
    ready = bool(TOKEN and CHAT_ID)
    return (
        {
            "status": "ready" if ready else "degraded",
            "telegram_configured": ready,
            "newsapi_configured": bool(NEWS_API_KEY),
        },
        200 if ready else 503,
    )


# ============================================================
# TIME HELPERS
# ============================================================


def now_ts() -> int:
    return int(time.time())


def tr_now_text() -> str:
    tr_time = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Istanbul"))
    return tr_time.strftime("%Y-%m-%d %H:%M:%S TR")


def sleep_or_stop(seconds: float) -> bool:
    """True donerse stop event geldi demektir."""
    return _STOP_EVENT.wait(max(0, seconds))


# ============================================================
# SESSION CONTEXT
# ============================================================


@dataclass(frozen=True)
class SessionContext:
    session: str
    macro_multiplier: float
    micro_multiplier: float
    news_multiplier: float
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "session": self.session,
            "macro_multiplier": self.macro_multiplier,
            "micro_multiplier": self.micro_multiplier,
            "news_multiplier": self.news_multiplier,
            "note": self.note,
        }


_SESSION_PROFILES = {
    "US_OPEN": SessionContext(
        "US_OPEN",
        1.00,
        1.00,
        1.00,
        "ABD piyasasi acik: makro/risk verileri normal agirlikta.",
    ),
    "US_EXTENDED": SessionContext(
        "US_EXTENDED",
        0.50,
        1.10,
        0.95,
        "ABD pre/after-market: makro etkisi kismen azaltildi.",
    ),
    "US_CLOSED": SessionContext(
        "US_CLOSED",
        0.35,
        1.15,
        0.90,
        "ABD piyasasi kapali: kripto ici sinyallerin agirligi artirildi.",
    ),
    "US_HOLIDAY": SessionContext(
        "US_HOLIDAY",
        0.20,
        1.20,
        0.85,
        "ABD piyasa tatili: makro/risk verilerinin etkisi azaltildi.",
    ),
    "WEEKEND": SessionContext(
        "WEEKEND",
        0.20,
        1.20,
        0.85,
        "Hafta sonu: ABD piyasa verileri bayat kabul edildi.",
    ),
}


def _compute_session_context(minute_key: int) -> SessionContext:
    del minute_key
    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
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
        log.warning("%d yili icin ABD tatil listesi tanimli degil.", year)

    if weekday >= 5:
        return _SESSION_PROFILES["WEEKEND"]
    if date_key in holidays:
        return _SESSION_PROFILES["US_HOLIDAY"]

    close_time = early_close if date_key in early_closes else regular_close
    if regular_open <= minutes < close_time:
        return _SESSION_PROFILES["US_OPEN"]
    if 4 * 60 <= minutes < regular_open or close_time <= minutes < 20 * 60:
        return _SESSION_PROFILES["US_EXTENDED"]
    return _SESSION_PROFILES["US_CLOSED"]


@lru_cache(maxsize=4)
def _cached_session(minute_key: int) -> SessionContext:
    return _compute_session_context(minute_key)


def get_session_context() -> SessionContext:
    return _cached_session(now_ts() // 60)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_MAX_LEN = 4000


def send_message(text: str) -> bool:
    """Telegram mesaj gonder. Basari durumunu dondurur."""
    if not TOKEN or not CHAT_ID:
        log.warning("TOKEN veya CHAT_ID eksik, mesaj gonderilemedi.")
        return False

    if len(text) > TELEGRAM_MAX_LEN:
        text = text[: TELEGRAM_MAX_LEN - 20] + "\n\n[...mesaj kisaltildi]"

    url = f"{TELEGRAM_BASE}/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}

    try:
        with _HTTP_SEMAPHORE:
            r = _http_session().post(url, data=data, timeout=15)
        if r.status_code != 200:
            log.error("Telegram HTTP %s: %s", r.status_code, r.text[:200])
            return False
        log.debug("Telegram OK")
        return True
    except requests.RequestException as e:
        # Exception string'i URL/token icerebilir; sadece tipini logluyoruz.
        log.error("Telegram gonderim hatasi: %s", type(e).__name__)
        return False


# ============================================================
# HTTP HELPERS
# ============================================================


class TransientHTTPError(Exception):
    """Gecici HTTP hatasi - retry edilebilir."""


class PermanentHTTPError(Exception):
    """Kalici HTTP hatasi - retry'a degmez."""


def _retry_wait(base_delay: float, attempt: int, response: Optional[requests.Response] = None) -> float:
    wait = base_delay * (2**attempt)
    if response is not None and response.status_code == 429:
        try:
            wait = max(wait, float(response.headers.get("Retry-After", wait)))
        except (TypeError, ValueError):
            pass
    return wait + random.uniform(0, min(1.0, wait * 0.2))


def request_json(
    url: str,
    params: Optional[dict] = None,
    retries: int = 3,
    base_delay: float = 1.5,
    timeout: float = 15.0,
    headers: Optional[dict] = None,
) -> Any:
    """
    Akilli HTTP retry:
      - 4xx (429 haric): fail-fast
      - 429: Retry-After + exponential backoff
      - 5xx / timeout / connection: exponential backoff
    """
    attempts = max(1, retries)
    last_err: Optional[Exception] = None

    for attempt in range(attempts):
        if _STOP_EVENT.is_set():
            raise TransientHTTPError("Shutdown requested")

        try:
            with _HTTP_SEMAPHORE:
                r = _http_session().get(url, params=params, timeout=timeout, headers=headers)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            if attempt < attempts - 1:
                wait = _retry_wait(base_delay, attempt)
                log.debug("Baglanti hatasi (%s), %.1fs bekleniyor: %s", type(e).__name__, wait, url)
                if sleep_or_stop(wait):
                    raise TransientHTTPError("Shutdown requested") from e
            continue
        except requests.RequestException as e:
            last_err = e
            if attempt < attempts - 1 and sleep_or_stop(_retry_wait(base_delay, attempt)):
                raise TransientHTTPError("Shutdown requested") from e
            continue

        if r.status_code == 429:
            wait = _retry_wait(base_delay, attempt, r)
            log.warning("429 rate limit, %.1fs bekleniyor: %s", wait, url)
            last_err = TransientHTTPError(f"429 Too Many Requests: {url}")
            if attempt < attempts - 1 and sleep_or_stop(wait):
                raise TransientHTTPError("Shutdown requested")
            continue

        if 400 <= r.status_code < 500:
            raise PermanentHTTPError(f"HTTP {r.status_code}: {url} - {r.text[:200]}")

        if r.status_code >= 500:
            last_err = TransientHTTPError(f"HTTP {r.status_code}: {url}")
            if attempt < attempts - 1 and sleep_or_stop(_retry_wait(base_delay, attempt, r)):
                raise TransientHTTPError("Shutdown requested")
            continue

        try:
            return r.json()
        except ValueError as e:
            raise PermanentHTTPError(f"Gecersiz JSON: {url} - {e}") from e

    raise last_err if last_err else TransientHTTPError(f"Bilinmeyen hata: {url}")


# ============================================================
# MEXC HELPERS
# ============================================================


def to_futures_symbol(symbol: str) -> str:
    return symbol.replace("USDT", "_USDT")


def get_klines(symbol: str, interval: str = "5m", limit: int = KLINE_LIMIT) -> list:
    data = request_json(
        f"{MEXC_SPOT_BASE}/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )
    if not isinstance(data, list):
        raise PermanentHTTPError(f"{symbol} kline response list degil")
    return data


def get_ticker(symbol: str) -> dict:
    data = request_json(f"{MEXC_SPOT_BASE}/api/v3/ticker/24hr", {"symbol": symbol})
    if not isinstance(data, dict):
        raise PermanentHTTPError(f"{symbol} ticker response dict degil")
    return data


def get_spot_price(symbol: str) -> float:
    data = request_json(f"{MEXC_SPOT_BASE}/api/v3/ticker/price", {"symbol": symbol})
    if not isinstance(data, dict):
        raise PermanentHTTPError(f"{symbol} price response dict degil")
    return float(data["price"])


def get_book(symbol: str) -> dict:
    data = request_json(f"{MEXC_SPOT_BASE}/api/v3/ticker/bookTicker", {"symbol": symbol})
    if not isinstance(data, dict):
        raise PermanentHTTPError(f"{symbol} book response dict degil")
    return data


def get_futures_fair_price(symbol: str) -> Optional[float]:
    futures_symbol = to_futures_symbol(symbol)
    url = f"{MEXC_FUTURES_BASE}/api/v1/contract/fair_price/{futures_symbol}"

    try:
        data = request_json(url)
    except (TransientHTTPError, PermanentHTTPError) as e:
        log.debug("%s futures fair price alinamadi: %s", symbol, e)
        return None

    if not isinstance(data, dict):
        return None

    candidates = []
    if isinstance(data.get("data"), dict):
        candidates.append(data["data"])
    candidates.append(data)

    for src in candidates:
        for key in ("fairPrice", "price"):
            if key in src:
                try:
                    return float(src[key])
                except (TypeError, ValueError):
                    continue
    return None


# ============================================================
# MATH HELPERS
# ============================================================


def pct(a: float, b: float) -> float:
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


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


def bar(value: float, max_abs: float, width: int = 4) -> str:
    if max_abs <= 0:
        return "⬜" * width + "│" + "⬜" * width

    ratio = clamp(value / max_abs, -1, 1)
    filled = int(abs(ratio) * width)
    if ratio > 0:
        return "⬜" * width + "│" + "🟩" * filled + "⬜" * (width - filled)
    if ratio < 0:
        return "⬜" * (width - filled) + "🟥" * filled + "│" + "⬜" * width
    return "⬜" * width + "│" + "⬜" * width


def format_price(price: Optional[float]) -> str:
    if price is None:
        return "N/A"
    if price == 0:
        return "0"
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.6f}"
    return f"{price:.8f}"


def format_money(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


# ============================================================
# STATE MANAGEMENT
# ============================================================


class StateManager:
    """Thread-safe state yonetimi. Atomik yazim, in-memory cache."""

    def __init__(self, file_path: str):
        self._file_path = file_path
        self._lock = threading.RLock()
        self._state: dict = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self._file_path):
            return self._fresh_state()

        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.error("State okunamadi, sifirlaniyor: %s", e)
            return self._fresh_state()

        if not isinstance(data, dict):
            log.error("State dict degil, sifirlaniyor.")
            return self._fresh_state()
        return self._migrate(data)

    @staticmethod
    def _fresh_state() -> dict:
        return {"version": STATE_VERSION, "symbols": {}, "meta": {}}

    @staticmethod
    def _migrate(data: dict) -> dict:
        version = data.get("version", 1)

        if "symbols" not in data:
            symbols = {k: v for k, v in data.items() if not k.startswith("_")}
            meta = {
                "last_summary_ts": data.get("_last_summary_ts", 0),
                "last_heartbeat_ts": data.get("_last_heartbeat_ts", 0),
                "last_news_scan_ts": data.get("_last_news_scan_ts", 0),
                "last_news_alert_ts": data.get("_last_news_alert_ts", 0),
                "last_news_alert_hash": data.get("_last_news_alert_hash"),
            }
            log.info("State v1 -> v%d migration uygulandi.", STATE_VERSION)
            return {"version": STATE_VERSION, "symbols": symbols, "meta": meta}

        data.setdefault("symbols", {})
        data.setdefault("meta", {})
        if version < 3:
            for symbol_state in data["symbols"].values():
                if isinstance(symbol_state, dict):
                    symbol_state.setdefault("pending_alert_type", None)
                    symbol_state.setdefault("updated_at", 0)
            log.info("State v%s -> v3 migration uygulandi.", version)

        data["version"] = STATE_VERSION
        return data

    def save(self) -> None:
        """Atomik yazim: tmp + fsync + os.replace."""
        with self._lock:
            try:
                parent = os.path.dirname(os.path.abspath(self._file_path))
                os.makedirs(parent, exist_ok=True)
                tmp = f"{self._file_path}.{os.getpid()}.{threading.get_ident()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._state, f, indent=2, sort_keys=True)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self._file_path)
            except OSError as e:
                log.error("State kaydedilemedi: %s", e)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def get_meta(self, key: str, default=None):
        with self._lock:
            return self._state["meta"].get(key, default)

    def set_meta(self, key: str, value) -> None:
        with self._lock:
            self._state["meta"][key] = value

    def get_symbol(self, symbol: str) -> Optional[dict]:
        with self._lock:
            value = self._state["symbols"].get(symbol)
            return copy.deepcopy(value) if value is not None else None

    def update_symbol(self, symbol: str, value: dict) -> None:
        with self._lock:
            self._state["symbols"][symbol] = value


_STATE_MGR = StateManager(STATE_FILE)


# ============================================================
# NEWS LAYER
# ============================================================


def default_news_context() -> dict:
    return {
        "news_risk_score": 0,
        "category": "NONE",
        "headline": None,
        "source": None,
        "provider": None,
        "url": None,
        "match_count": 0,
        "note": "Onemli haber etkisi yok.",
    }


def is_fresh_article(published_at: Optional[str]) -> bool:
    if not published_at:
        return True

    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True

    age = datetime.now(timezone.utc) - dt
    return age <= timedelta(hours=NEWS_MAX_AGE_HOURS)


def _newsapi_quota_used_today() -> int:
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    quota = _STATE_MGR.get_meta("newsapi_quota", {})
    if quota.get("date") != today_key:
        return 0
    return int(quota.get("count", 0))


def _newsapi_quota_increment(by: int) -> None:
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    quota = _STATE_MGR.get_meta("newsapi_quota", {})
    if quota.get("date") != today_key:
        quota = {"date": today_key, "count": 0}
    quota["count"] = int(quota.get("count", 0)) + by
    _STATE_MGR.set_meta("newsapi_quota", quota)


def fetch_newsapi_headlines() -> list[dict]:
    if not NEWS_API_KEY:
        return []

    used_today = _newsapi_quota_used_today()
    if used_today + NEWSAPI_QUERIES_PER_SCAN > NEWSAPI_DAILY_LIMIT:
        log.warning("NewsAPI limit korumasina takildi (%d/%d). Atlaniyor.", used_today, NEWSAPI_DAILY_LIMIT)
        return []

    queries = [
        "Iran OR Israel OR Taiwan OR Russia OR Ukraine OR war OR missile",
        "Fed OR Powell OR CPI OR inflation OR tariff OR Trump OR FOMC",
        "crypto SEC OR ETF OR hack OR stablecoin OR depeg",
    ]

    headlines = []
    success_count = 0

    for q in queries:
        try:
            data = request_json(
                f"{NEWSAPI_BASE}/everything",
                params={"q": q, "language": "en", "sortBy": "publishedAt", "pageSize": 15},
                headers={"X-Api-Key": NEWS_API_KEY},
            )
            success_count += 1
        except (TransientHTTPError, PermanentHTTPError) as e:
            log.warning("NewsAPI query basarisiz (%s...): %s", q[:30], e)
            continue

        for article in data.get("articles", []) if isinstance(data, dict) else []:
            title = article.get("title") or ""
            published_at = article.get("publishedAt")
            if not title or not is_fresh_article(published_at):
                continue
            headlines.append(
                {
                    "title": title,
                    "source": (article.get("source") or {}).get("name", "Unknown"),
                    "url": article.get("url", ""),
                    "published_at": published_at,
                    "provider": "NewsAPI",
                }
            )

    if success_count > 0:
        _newsapi_quota_increment(success_count)
    return headlines


def fetch_gdelt_headlines() -> list[dict]:
    query = "trump tariff iran israel china war missile fed powell crypto sec etf hack stablecoin"

    try:
        data = request_json(
            f"{GDELT_BASE}/doc/doc",
            {
                "query": query,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": 20,
                "sort": "DateDesc",
            },
        )
    except (TransientHTTPError, PermanentHTTPError) as e:
        log.warning("GDELT alinamadi: %s", e)
        return []

    headlines = []
    articles = data.get("articles", []) if isinstance(data, dict) else []
    for article in articles:
        title = article.get("title") or ""
        if not title:
            continue

        seendate = article.get("seendate")
        published_at = None
        if seendate and len(seendate) >= 14:
            published_at = (
                f"{seendate[0:4]}-{seendate[4:6]}-{seendate[6:8]}T"
                f"{seendate[8:10]}:{seendate[10:12]}:{seendate[12:14]}Z"
            )

        if not is_fresh_article(published_at):
            continue

        headlines.append(
            {
                "title": title,
                "source": article.get("sourceCountry", "GDELT"),
                "url": article.get("url", ""),
                "published_at": published_at,
                "provider": "GDELT",
            }
        )

    return headlines


def _compile_keyword_patterns() -> dict:
    compiled = {}
    for category, cfg in NEWS_CATEGORIES.items():
        patterns = []
        for kw in cfg["keywords"]:
            kw_lower = kw.lower().strip()
            if not kw_lower:
                log.warning("Bos keyword atlaniyor (kategori: %s)", category)
                continue
            if " " in kw_lower:
                patterns.append(("substring", kw_lower))
            else:
                patterns.append(("regex", re.compile(rf"\b{re.escape(kw_lower)}\b", re.IGNORECASE)))
        compiled[category] = patterns
    return compiled


_KEYWORD_PATTERNS = _compile_keyword_patterns()


def count_keyword_hits(text: str, category: str) -> int:
    text_lower = text.lower()
    hits = 0
    for kind, pattern in _KEYWORD_PATTERNS[category]:
        if kind == "regex":
            if pattern.search(text_lower):
                hits += 1
        elif pattern in text_lower:
            hits += 1
    return hits


def classify_headline(headline: dict) -> Optional[dict]:
    text = headline["title"]
    best: Optional[tuple[int, float, str, float]] = None

    for category, cfg in NEWS_CATEGORIES.items():
        hits = count_keyword_hits(text, category)
        risk = float(cfg["risk"])
        candidate = (hits, abs(risk), category, risk)
        if hits > 0 and (best is None or candidate[:2] > best[:2]):
            best = candidate

    if best is None:
        return None

    best_hits, _, best_category, best_risk = best
    if best_hits >= 3:
        risk_score = best_risk
    elif best_hits == 2:
        risk_score = best_risk * 0.8
    else:
        risk_score = best_risk * 0.6

    return {
        "category": best_category,
        "risk_score": clamp(risk_score, -3, 3),
        "headline": headline["title"],
        "source": headline["source"],
        "provider": headline["provider"],
        "url": headline["url"],
        "hits": best_hits,
    }


def scan_news() -> dict:
    headlines = []

    try:
        headlines.extend(fetch_newsapi_headlines())
    except Exception as e:
        log.warning("NewsAPI hata: %s", e)

    try:
        headlines.extend(fetch_gdelt_headlines())
    except Exception as e:
        log.warning("GDELT hata: %s", e)

    classified = []
    seen_titles = set()
    for headline in headlines:
        title_key = headline["title"].strip().lower()
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)

        item = classify_headline(headline)
        if item:
            classified.append(item)

    if not classified:
        return default_news_context()

    category_counts: dict[str, int] = {}
    for item in classified:
        category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1

    classified.sort(key=lambda x: (abs(x["risk_score"]), x["hits"]), reverse=True)
    top = classified[0]

    final_risk = top["risk_score"]
    multi_count = category_counts.get(top["category"], 1)
    if multi_count >= NEWS_MULTI_SOURCE_BOOST:
        final_risk = clamp(final_risk * NEWS_MULTI_SOURCE_FACTOR, -3, 3)

    note = "Haber katmani aktif."
    if multi_count >= NEWS_MULTI_SOURCE_BOOST:
        note += f" {multi_count} kaynak {top['category']} kategorisini dogruluyor."

    return {
        "news_risk_score": round(final_risk, 2),
        "category": top["category"],
        "headline": top["headline"],
        "source": top["source"],
        "provider": top["provider"],
        "url": top["url"],
        "match_count": multi_count,
        "note": note,
    }


def headline_hash(headline: Optional[str]) -> Optional[str]:
    if not headline:
        return None
    return hashlib.md5(headline.encode("utf-8")).hexdigest()[:12]


def format_news_alert(news_context: dict) -> str:
    return (
        "📰 NEWS RISK ALERT\n\n"
        f"Kategori: {news_context['category']}\n"
        f"Risk Skoru: {news_context['news_risk_score']} / 3\n"
        f"Dogulayan Kaynak Sayisi: {news_context.get('match_count', 1)}\n"
        f"Provider: {news_context.get('provider', '-')} ({news_context.get('source', '-')})\n\n"
        f"Baslik:\n{news_context.get('headline', '-')}\n\n"
        f"Bot Etkisi:\n"
        f"- |risk| >= {NEWS_VETO_THRESHOLD}: ters yondeki sinyaller VETO edilir\n"
        f"- |risk| >= {NEWS_DAMPEN_THRESHOLD}: ters yondeki skor x{NEWS_DAMPEN_FACTOR} sonumlenir\n"
        f"- Ayni yon: skor x{NEWS_BOOST_FACTOR} guclendirilir\n\n"
        f"Zaman: {tr_now_text()}"
    )


# ============================================================
# FEATURE ENGINE
# ============================================================


def _to_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} float'a cevrilemedi: {value!r}") from e


def get_features(symbol: str) -> dict:
    """Tek coin icin spot endpoint'ler paralel cagrilir; futures non-kritiktir."""
    futures = {
        "k5": _FEATURE_EXECUTOR.submit(get_klines, symbol, "5m", KLINE_LIMIT),
        "k15": _FEATURE_EXECUTOR.submit(get_klines, symbol, "15m", KLINE_LIMIT),
        "ticker": _FEATURE_EXECUTOR.submit(get_ticker, symbol),
        "book": _FEATURE_EXECUTOR.submit(get_book, symbol),
        "spot": _FEATURE_EXECUTOR.submit(get_spot_price, symbol),
    }

    results: dict[str, Any] = {}
    errors: dict[str, Exception] = {}
    for key, fut in futures.items():
        try:
            results[key] = fut.result()
        except Exception as e:
            errors[key] = e

    failed = [key for key in ("k5", "k15", "ticker", "book", "spot") if key in errors]
    if failed:
        raise RuntimeError(f"{symbol}: kritik endpoint hatasi ({', '.join(failed)}): {errors[failed[0]]}")

    k5 = results["k5"]
    k15 = results["k15"]
    ticker = results["ticker"]
    book = results["book"]
    spot_price = results["spot"]

    closes_5 = [_to_float(x[4], f"{symbol}.k5.close") for x in k5]
    closes_15 = [_to_float(x[4], f"{symbol}.k15.close") for x in k15]
    volumes_5 = [_to_float(x[5], f"{symbol}.k5.volume") for x in k5]

    if len(closes_5) < MIN_KLINES_5M or len(closes_15) < MIN_KLINES_15M:
        raise ValueError(
            f"{symbol} icin yetersiz kline verisi "
            f"(5m={len(closes_5)}/{MIN_KLINES_5M}, 15m={len(closes_15)}/{MIN_KLINES_15M})"
        )

    ema9 = ema(closes_5, EMA_FAST)
    ema21 = ema(closes_5, EMA_MID)
    ema50 = ema(closes_5, EMA_SLOW)
    ema9_15 = ema(closes_15, EMA_FAST)
    ema21_15 = ema(closes_15, EMA_MID)
    ema50_15 = ema(closes_15, EMA_SLOW)

    ret_5m = pct(closes_5[-1], closes_5[-RET_5M_OFFSET])
    ret_15m = pct(closes_5[-1], closes_5[-RET_15M_OFFSET])
    ret_1h = pct(closes_5[-1], closes_5[-RET_1H_OFFSET])
    ret_4h = pct(closes_15[-1], closes_15[-RET_4H_OFFSET])

    recent_vols = volumes_5[-(VOLUME_WINDOW + 1) : -1]
    if recent_vols:
        median_vol = statistics.median(recent_vols)
        vol_ratio = volumes_5[-1] / median_vol if median_vol > 0 else 1.0
    else:
        vol_ratio = 1.0

    bid = _to_float(book["bidPrice"], f"{symbol}.bid")
    ask = _to_float(book["askPrice"], f"{symbol}.ask")
    mid = (bid + ask) / 2
    spread_bps = ((ask - bid) / mid) * 10000 if mid else 999.0

    change_24h = _to_float(ticker.get("priceChangePercent", 0), f"{symbol}.change_24h")

    futures_price = None
    basis_pct = None
    try:
        futures_price = get_futures_fair_price(symbol)
        if futures_price:
            basis_pct = pct(futures_price, spot_price)
    except Exception as e:
        log.debug("%s futures basis alinamadi: %s", symbol, e)

    return {
        "last": closes_5[-1],
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


# ============================================================
# SCORING FUNCTIONS
# ============================================================


def score_macro(btc: dict) -> float:
    p = SCORE_PARAMS["macro"]
    score = 0.0
    if btc["change_24h"] > p["change_24h_strong"]:
        score += p["weight_change_24h"]
    elif btc["change_24h"] < -p["change_24h_strong"]:
        score -= p["weight_change_24h"]

    if btc["ret_4h"] > p["ret_4h_strong"]:
        score += p["weight_ret_4h"]
    elif btc["ret_4h"] < -p["ret_4h_strong"]:
        score -= p["weight_ret_4h"]
    return clamp(score, -3, 3)


def score_market(btc: dict, eth: dict) -> float:
    threshold = SCORE_PARAMS["market"]["ret_1h_strong"]
    score = 0.0
    if btc["ret_1h"] > threshold:
        score += 1
    elif btc["ret_1h"] < -threshold:
        score -= 1
    if eth["ret_1h"] > threshold:
        score += 1
    elif eth["ret_1h"] < -threshold:
        score -= 1
    return clamp(score, -2, 2)


def score_trend(f: dict) -> float:
    score = 0.0
    if f["ema9"] > f["ema21"] > f["ema50"]:
        score += 1.2
    elif f["ema9"] < f["ema21"] < f["ema50"]:
        score -= 1.2

    if f["ema9_15"] > f["ema21_15"] > f["ema50_15"]:
        score += 0.8
    elif f["ema9_15"] < f["ema21_15"] < f["ema50_15"]:
        score -= 0.8
    return clamp(score, -2, 2)


def score_momentum(f: dict) -> float:
    p = SCORE_PARAMS["momentum"]
    score = 0.0
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


def score_volume(f: dict) -> float:
    p = SCORE_PARAMS["volume"]
    if f["vol_ratio"] >= p["spike"]:
        return 2
    if f["vol_ratio"] >= p["high"]:
        return 1
    if f["vol_ratio"] < p["low"]:
        return -1
    return 0


def score_liquidity(f: dict, group: str) -> float:
    bad_limit = SPREAD_LIMITS[group]
    spread = f["spread_bps"]
    if spread > bad_limit:
        return -2
    if spread > bad_limit / 2:
        return -1
    return 0


def score_basis(basis_pct: Optional[float], f: dict) -> float:
    if basis_pct is None:
        return 0
    score = 0.0
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


# ============================================================
# SIGNAL ENGINE
# ============================================================


def veto_signal(symbol: str, f: dict, btc: dict, eth: dict) -> Optional[str]:
    del eth
    group = COINS[symbol]
    p = SCORE_PARAMS["veto"]

    if f["spread_bps"] > SPREAD_LIMITS[group]:
        return "Spread yuksek"
    if group == "HIGH_BETA" and f["vol_ratio"] < p["vol_low_high_beta"]:
        return "Hacim zayif"
    if symbol not in ("BTCUSDT", "ETHUSDT"):
        btc_unclear = abs(btc["ret_1h"]) < p["btc_unclear_1h"] and abs(btc["ret_4h"]) < p["btc_unclear_4h"]
        if btc_unclear:
            return "BTC yonu belirsiz"
    return None


def classify_level(score_abs: float, group: str) -> str:
    strong = THRESHOLDS[group]["strong"]
    medium = THRESHOLDS[group]["long"]
    if score_abs >= strong:
        return "STRONG"
    if score_abs >= medium:
        return "MEDIUM"
    return "WEAK"


def normalize_weights_if_basis_missing(weights: dict, features: dict) -> dict:
    if features["basis_pct"] is not None:
        return dict(weights)

    active = {k: v for k, v in weights.items() if k != "basis"}
    total_w = sum(active.values())
    if total_w == 0:
        return dict(weights)

    redistributed = {k: v / total_w for k, v in active.items()}
    redistributed["basis"] = 0
    return redistributed


def apply_news_modulation(total_score: float, news_risk: float, session_news_mult: float) -> tuple[Optional[float], str]:
    effective_risk = news_risk * session_news_mult
    abs_risk = abs(effective_risk)

    same_direction = (total_score > 0 and effective_risk > 0) or (total_score < 0 and effective_risk < 0)
    opposite_direction = (total_score > 0 and effective_risk < 0) or (total_score < 0 and effective_risk > 0)

    if abs_risk >= NEWS_VETO_THRESHOLD and opposite_direction:
        return None, "news_veto"
    if abs_risk >= NEWS_DAMPEN_THRESHOLD and opposite_direction:
        return total_score * NEWS_DAMPEN_FACTOR, "news_dampened"
    if abs_risk >= NEWS_BOOST_THRESHOLD and same_direction:
        return total_score * NEWS_BOOST_FACTOR, "news_boosted"
    return total_score, "news_neutral"


def weighted_signal(
    symbol: str,
    features: dict,
    btc: dict,
    eth: dict,
    session_context: SessionContext,
    news_context: dict,
) -> dict:
    group = COINS[symbol]
    weights = normalize_weights_if_basis_missing(WEIGHTS[group], features)
    veto = veto_signal(symbol, features, btc, eth)

    macro_mult = session_context.macro_multiplier
    micro_mult = session_context.micro_multiplier
    news_mult = session_context.news_multiplier

    raw = {
        "macro": score_macro(btc) * macro_mult,
        "market": score_market(btc, eth) * micro_mult,
        "trend": score_trend(features) * micro_mult,
        "momentum": score_momentum(features) * micro_mult,
        "volume": score_volume(features) * micro_mult,
        "liquidity": score_liquidity(features, group),
        "basis": score_basis(features["basis_pct"], features) * micro_mult,
    }

    pre_news_total = sum(raw[k] * weights[k] for k in raw)
    news_risk = float(news_context.get("news_risk_score", 0))
    modulated_total, news_action = apply_news_modulation(pre_news_total, news_risk, news_mult)

    max_components = {
        "macro": 3 * abs(macro_mult),
        "market": 2 * abs(micro_mult),
        "trend": 2 * abs(micro_mult),
        "momentum": 2 * abs(micro_mult),
        "volume": 2 * abs(micro_mult),
        "liquidity": 2,
        "basis": 2 * abs(micro_mult),
    }
    max_total = sum(weights[k] * max_components[k] for k in raw)

    if modulated_total is None:
        signal = "NO_TRADE"
        level = "WEAK"
        total = pre_news_total
        veto = veto or f"Haber riski ({news_context['category']})"
    else:
        total = modulated_total
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

    if signal in ("LONG", "SHORT") and max_total > 0:
        confidence = round(clamp(abs(total) / max_total * 100, 0, 100), 1)
    else:
        confidence = 0.0

    return {
        "symbol": symbol,
        "group": group,
        "signal": signal,
        "level": level,
        "score": round(total, 3),
        "pre_news_score": round(pre_news_total, 3),
        "max_score": round(max_total, 3),
        "confidence": confidence,
        "raw": raw,
        "features": features,
        "veto": veto,
        "news_action": news_action,
        "session_context": session_context.as_dict(),
        "news_context": news_context,
        "basis_missing": features["basis_pct"] is None,
    }


# ============================================================
# TRADE PLAN ENGINE
# ============================================================


def build_trade_plan(result: dict) -> Optional[dict]:
    signal = result.get("signal")
    if signal not in ("LONG", "SHORT"):
        return None

    group = result["group"]
    f = result["features"]
    cfg = TRADE_PLAN_CONFIG[group]

    spot = float(f["spot_price"])
    ema9 = float(f["ema9"])
    ema21 = float(f["ema21"])
    stop_pct = float(cfg["stop_pct"])

    if spot <= 0:
        log.warning("%s build_trade_plan: spot=%s, plan uretilemiyor", result["symbol"], spot)
        return None

    reference_entry = spot
    ema_low = min(ema9, ema21)
    ema_high = max(ema9, ema21)

    if signal == "LONG":
        if ema_high < spot and ema_low > 0:
            zone_low = ema_low
            zone_high = ema_high
        else:
            zone_high = spot * (1 - stop_pct / 4)
            zone_low = spot * (1 - stop_pct / 2)

        stop_price = reference_entry * (1 - stop_pct)
        risk_per_unit = reference_entry - stop_price
        tp1 = reference_entry + risk_per_unit * cfg["tp1_r"]
        tp2 = reference_entry + risk_per_unit * cfg["tp2_r"]
        tp3 = reference_entry + risk_per_unit * cfg["tp3_r"]
    else:
        if ema_low > spot:
            zone_low = ema_low
            zone_high = ema_high
        else:
            zone_low = spot * (1 + stop_pct / 4)
            zone_high = spot * (1 + stop_pct / 2)

        stop_price = reference_entry * (1 + stop_pct)
        risk_per_unit = stop_price - reference_entry
        tp1 = reference_entry - risk_per_unit * cfg["tp1_r"]
        tp2 = reference_entry - risk_per_unit * cfg["tp2_r"]
        tp3 = reference_entry - risk_per_unit * cfg["tp3_r"]

    risk_amount = ACCOUNT_SIZE_USD * RISK_PCT_PER_TRADE
    position_notional = risk_amount / stop_pct if stop_pct > 0 else 0
    quantity = position_notional / reference_entry if reference_entry > 0 else 0

    return {
        "direction": signal,
        "account_size": ACCOUNT_SIZE_USD,
        "risk_pct": RISK_PCT_PER_TRADE,
        "risk_amount": risk_amount,
        "reference_entry": reference_entry,
        "entry_zone_low": zone_low,
        "entry_zone_high": zone_high,
        "stop_price": stop_price,
        "stop_pct": stop_pct,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr_ratio": cfg["tp2_r"],
        "position_notional": position_notional,
        "quantity": quantity,
    }


def format_trade_plan_block(plan: Optional[dict]) -> list[str]:
    if not plan:
        return []

    return [
        "",
        "📌 Trade Plan (emir gondermez)",
        f"Yon: {plan['direction']}",
        f"Referans Entry: {format_price(plan['reference_entry'])}",
        f"Tercihli Entry Bolgesi: {format_price(plan['entry_zone_low'])} - {format_price(plan['entry_zone_high'])}",
        f"Stop: {format_price(plan['stop_price'])} (%{plan['stop_pct'] * 100:.2f})",
        f"TP1 (1R): {format_price(plan['tp1'])}",
        f"TP2 (2R): {format_price(plan['tp2'])}",
        f"TP3 / Runner (3R): {format_price(plan['tp3'])}",
        f"Hesap: {format_money(plan['account_size'])}",
        f"Islem Riski: %{plan['risk_pct'] * 100:.2f} = {format_money(plan['risk_amount'])}",
        f"Onerilen Notional: {format_money(plan['position_notional'])}",
        f"Yaklasik Miktar: {plan['quantity']:.6f}",
        "Not: Sinyal geldi diye anlik piyasa emri sart degildir; entry bolgesine pullback beklemek daha sagliklidir.",
    ]


# ============================================================
# MESSAGE FORMATTERS
# ============================================================


def format_signal(result: dict, title: str) -> str:
    f = result["features"]
    raw = result["raw"]
    session = result["session_context"]
    news = result["news_context"]

    basis_text = f"{f['basis_pct']:+.3f}%" if f["basis_pct"] is not None else "N/A"
    futures_text = format_price(f["futures_price"]) if f["futures_price"] is not None else "N/A"

    lines = [
        title,
        f"{result['symbol']} -> {result['signal']} / {result['level']}",
        f"Skor: {result['score']} / {result['max_score']} {bar(result['score'], result['max_score'])}",
        f"News-oncesi Skor: {result['pre_news_score']}",
        f"News Aksiyonu: {result['news_action']}",
        f"Guven: %{result['confidence']}",
        f"Seans: {session['session']}",
        f"Makro Carpan: x{session['macro_multiplier']}",
        f"Mikro Carpan: x{session['micro_multiplier']}",
        f"News Carpani: x{session['news_multiplier']}",
        f"Not: {session['note']}",
        f"Haber Kategorisi: {news['category']}",
        f"Haber Risk Skoru: {news['news_risk_score']} / 3",
        f"Haber Notu: {news['note']}",
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
        f"5m: %{f['ret_5m']:.2f} | 15m: %{f['ret_15m']:.2f} | "
        f"1h: %{f['ret_1h']:.2f} | 4h: %{f['ret_4h']:.2f}",
        f"Hacim Orani: {f['vol_ratio']:.2f}x | Spread: {f['spread_bps']:.2f} bps",
    ]

    lines.extend(format_trade_plan_block(build_trade_plan(result)))

    if result.get("basis_missing"):
        lines.append("Not: Basis verisi yok, agirliklar yeniden dagitildi.")
    if result["veto"]:
        lines.append(f"Veto: {result['veto']}")

    lines.append(f"Zaman: {tr_now_text()}")
    return "\n".join(lines)


def make_summary(results: list[dict], session_context: SessionContext, news_context: dict) -> str:
    longs = [f"{r['symbol']}({r['level']})" for r in results if r["signal"] == "LONG"]
    shorts = [f"{r['symbol']}({r['level']})" for r in results if r["signal"] == "SHORT"]
    neutral = [r["symbol"] for r in results if r["signal"] == "NO_TRADE"]

    headline = news_context.get("headline") or "-"
    url = news_context.get("url") or ""
    headline_line = headline + (f"\n{url}" if url else "")
    sd = session_context.as_dict()

    return (
        "📊 DURUM OZETI\n\n"
        f"Seans: {sd['session']}\n"
        f"Makro Carpan: x{sd['macro_multiplier']}\n"
        f"Mikro Carpan: x{sd['micro_multiplier']}\n"
        f"News Carpani: x{sd['news_multiplier']}\n"
        f"Not: {sd['note']}\n\n"
        f"Haber Kategorisi: {news_context['category']}\n"
        f"Haber Risk Skoru: {news_context['news_risk_score']} / 3\n"
        f"Haber Basligi: {headline_line}\n\n"
        f"LONG: {', '.join(longs) if longs else '-'}\n"
        f"SHORT: {', '.join(shorts) if shorts else '-'}\n"
        f"NO_TRADE: {', '.join(neutral) if neutral else '-'}\n"
        f"Zaman: {tr_now_text()}"
    )


# ============================================================
# ALERT LOGIC
# ============================================================


def should_notify_signal(result: dict) -> bool:
    if result["signal"] == "NO_TRADE":
        return False
    return LEVEL_ORDER[result["level"]] >= LEVEL_ORDER[MIN_SIGNAL_LEVEL]


def decide_alert(old: Optional[dict], new: dict) -> Optional[str]:
    if new.get("news_action") == "news_veto" and old and old.get("signal") in ("LONG", "SHORT"):
        return "🛑 NEWS VETO"

    if not should_notify_signal(new):
        if old and old.get("signal") in ("LONG", "SHORT") and new["signal"] == "NO_TRADE":
            return "❌ SİNYAL İPTAL"
        return None

    if not old:
        return "🚀 YENİ SİNYAL"

    old_signal = old.get("signal")
    new_signal = new["signal"]
    if old_signal == "NO_TRADE" and new_signal in ("LONG", "SHORT"):
        return "🚀 YENİ SİNYAL"
    if old_signal in ("LONG", "SHORT") and new_signal == "NO_TRADE":
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


def should_cooldown(old: Optional[dict], alert_type: str) -> bool:
    if alert_type in CRITICAL_ALERTS:
        return False
    if not old:
        return False
    return now_ts() - old.get("last_alert_ts", 0) < COOLDOWN_SECONDS


def pending_alert_still_relevant(alert_type: Optional[str], new: dict) -> bool:
    if not alert_type:
        return False
    if alert_type == "🛑 NEWS VETO":
        return new.get("news_action") == "news_veto"
    if alert_type in ("🚀 YENİ SİNYAL", "🔄 YÖN DEĞİŞTİ"):
        return should_notify_signal(new)
    if alert_type == "❌ SİNYAL İPTAL":
        return new["signal"] == "NO_TRADE"
    if alert_type.startswith("⚠️"):
        return new["signal"] in ("LONG", "SHORT")
    return False


def detect_movement_alert(symbol: str, result: dict) -> Optional[list[str]]:
    group = COINS[symbol]
    f = result["features"]
    thresholds = MOVEMENT_ALERT_THRESHOLDS[group]

    reasons = []
    if abs(f["ret_15m"]) >= thresholds["ret_15m"]:
        reasons.append(f"15dk degisim: %{f['ret_15m']:+.2f}")
    if abs(f["ret_1h"]) >= thresholds["ret_1h"]:
        reasons.append(f"1s degisim: %{f['ret_1h']:+.2f}")
    if f["vol_ratio"] >= thresholds["volume_ratio"]:
        reasons.append(f"Hacim orani: {f['vol_ratio']:.2f}x")
    return reasons if reasons else None


def format_movement_alert(symbol: str, result: dict, reasons: list[str]) -> str:
    f = result["features"]
    return (
        "⚡ COIN HAREKET ALARMI\n\n"
        f"Coin: {symbol}\n"
        f"Sinyal Durumu: {result['signal']} / {result['level']}\n"
        f"Skor: {result['score']} / {result['max_score']}\n"
        f"News Aksiyonu: {result.get('news_action', '-')}\n\n"
        f"Hareket Sebebi:\n"
        + "\n".join(f"- {reason}" for reason in reasons)
        + "\n\n"
        f"Fiyat: {format_price(f['spot_price'])}\n"
        f"5m: %{f['ret_5m']:+.2f} | 15m: %{f['ret_15m']:+.2f} | "
        f"1h: %{f['ret_1h']:+.2f} | 4h: %{f['ret_4h']:+.2f}\n"
        f"Hacim Orani: {f['vol_ratio']:.2f}x\n"
        f"Spread: {f['spread_bps']:.2f} bps\n\n"
        f"Not: Bu bir LONG/SHORT sinyali degildir. Bot sadece olagan disi coin hareketi tespit etti.\n"
        f"Zaman: {tr_now_text()}"
    )


def should_movement_alert_cooldown(state_mgr: StateManager, symbol: str) -> bool:
    last_ts = state_mgr.get_meta(f"last_movement_alert_ts_{symbol}", 0)
    return now_ts() - last_ts < MOVEMENT_ALERT_COOLDOWN_SECONDS


def mark_movement_alert_sent(state_mgr: StateManager, symbol: str) -> None:
    state_mgr.set_meta(f"last_movement_alert_ts_{symbol}", now_ts())


# ============================================================
# BOT LOOP
# ============================================================


def _process_news_cycle(state_mgr: StateManager, current_news: dict) -> dict:
    last_news_scan = state_mgr.get_meta("last_news_scan_ts", 0)
    if now_ts() - last_news_scan < NEWS_SCAN_INTERVAL_SECONDS:
        return current_news

    try:
        new_news = scan_news()
    except Exception as e:
        log.error("News scan hata: %s", e)
        new_news = default_news_context()

    state_mgr.set_meta("last_news_scan_ts", now_ts())

    if SEND_STANDALONE_NEWS_ALERTS and abs(new_news["news_risk_score"]) >= NEWS_VETO_THRESHOLD:
        last_alert_ts = state_mgr.get_meta("last_news_alert_ts", 0)
        last_alert_hash = state_mgr.get_meta("last_news_alert_hash")
        current_hash = headline_hash(new_news.get("headline"))
        cooled = now_ts() - last_alert_ts >= NEWS_ALERT_COOLDOWN_SECONDS
        new_headline = current_hash != last_alert_hash

        if cooled and new_headline and send_message(format_news_alert(new_news)):
            state_mgr.set_meta("last_news_alert_ts", now_ts())
            state_mgr.set_meta("last_news_alert_hash", current_hash)

    return new_news


def _process_symbol(
    symbol: str,
    features: dict,
    btc: dict,
    eth: dict,
    session_ctx: SessionContext,
    news_ctx: dict,
    state_mgr: StateManager,
) -> Optional[dict]:
    try:
        result = weighted_signal(symbol, features, btc, eth, session_ctx, news_ctx)

        if SEND_MOVEMENT_ALERTS:
            movement_reasons = detect_movement_alert(symbol, result)
            if movement_reasons and not should_movement_alert_cooldown(state_mgr, symbol):
                if send_message(format_movement_alert(symbol, result, movement_reasons)):
                    mark_movement_alert_sent(state_mgr, symbol)

        old = state_mgr.get_symbol(symbol)
        pending = old.get("pending_alert_type") if old else None
        if pending_alert_still_relevant(pending, result):
            alert_type = pending
        else:
            pending = None
            alert_type = decide_alert(old, result)

        last_alert_ts = old.get("last_alert_ts", 0) if old else 0
        if alert_type and not should_cooldown(old, alert_type):
            if send_message(format_signal(result, alert_type)):
                last_alert_ts = now_ts()
                pending = None
            else:
                pending = alert_type

        state_mgr.update_symbol(
            symbol,
            {
                "signal": result["signal"],
                "level": result["level"],
                "score": result["score"],
                "confidence": result["confidence"],
                "last_alert_ts": last_alert_ts,
                "pending_alert_type": pending,
                "updated_at": now_ts(),
            },
        )
        return result

    except Exception as e:
        log.warning("%s sinyal islemi basarisiz: %s", symbol, e)
        return None


def _send_periodic_messages(
    state_mgr: StateManager,
    results: list[dict],
    session_ctx: SessionContext,
    news_ctx: dict,
) -> None:
    last_summary = state_mgr.get_meta("last_summary_ts", 0)
    if now_ts() - last_summary >= SUMMARY_INTERVAL_SECONDS:
        if results:
            send_message(make_summary(results, session_ctx, news_ctx))
        state_mgr.set_meta("last_summary_ts", now_ts())

    last_heartbeat = state_mgr.get_meta("last_heartbeat_ts", 0)
    if now_ts() - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
        send_message(
            f"✅ Bot aktif (v{__version__})\n"
            f"Son kontrol: {tr_now_text()}\n"
            f"Seans: {session_ctx.session}\n"
            f"Makro: x{session_ctx.macro_multiplier} | "
            f"Mikro: x{session_ctx.micro_multiplier} | "
            f"News: x{session_ctx.news_multiplier}\n"
            f"Haber: {news_ctx['category']} (risk {news_ctx['news_risk_score']})"
        )
        state_mgr.set_meta("last_heartbeat_ts", now_ts())


def bot_loop(stop_event: threading.Event = _STOP_EVENT) -> None:
    log.info("BOT BASLADI v%s", __version__)
    send_message(f"BOT BASLADI 🚀 v{__version__} - News + session + basis aktif.")

    state_mgr = _STATE_MGR
    if not state_mgr.get_meta("last_summary_ts"):
        state_mgr.set_meta("last_summary_ts", now_ts())
    if not state_mgr.get_meta("last_heartbeat_ts"):
        state_mgr.set_meta("last_heartbeat_ts", now_ts())
    state_mgr.save()

    news_context = default_news_context()
    consecutive_failures = 0

    while not stop_event.is_set():
        try:
            state_mgr.set_meta("last_scan_started_ts", now_ts())
            session_ctx = get_session_context()
            news_context = _process_news_cycle(state_mgr, news_context)
            state_mgr.save()

            try:
                btc = get_features("BTCUSDT")
                eth = get_features("ETHUSDT")
            except Exception as e:
                consecutive_failures += 1
                wait = min(ERROR_BACKOFF_LONG, ERROR_BACKOFF_SHORT * consecutive_failures)
                state_mgr.set_meta("consecutive_failures", consecutive_failures)
                state_mgr.save()
                log.error("BTC/ETH features alinamadi (#%d), %ds bekleniyor: %s", consecutive_failures, wait, e)
                if sleep_or_stop(wait):
                    break
                continue

            consecutive_failures = 0
            state_mgr.set_meta("consecutive_failures", 0)

            features_cache: dict[str, dict] = {"BTCUSDT": btc, "ETHUSDT": eth}
            other_symbols = [s for s in COINS if s not in features_cache]
            if other_symbols:
                future_to_symbol = {_SYMBOL_EXECUTOR.submit(get_features, sym): sym for sym in other_symbols}
                for fut in as_completed(future_to_symbol):
                    sym = future_to_symbol[fut]
                    try:
                        features_cache[sym] = fut.result()
                    except Exception as e:
                        log.warning("%s features alinamadi: %s", sym, e)

            results: list[dict] = []
            for symbol in COINS:
                features = features_cache.get(symbol)
                if features is None:
                    continue
                result = _process_symbol(symbol, features, btc, eth, session_ctx, news_context, state_mgr)
                if result:
                    results.append(result)

            _send_periodic_messages(state_mgr, results, session_ctx, news_context)
            state_mgr.set_meta("last_successful_scan_ts", now_ts())
            state_mgr.save()

            if sleep_or_stop(SCAN_INTERVAL):
                break

        except Exception as e:
            if stop_event.is_set():
                break
            log.exception("ANA HATA: %s", e)
            try:
                send_message(f"⚠️ Bot hata aldi:\n{e}\nZaman: {tr_now_text()}")
            except Exception:
                pass
            consecutive_failures = 0
            state_mgr.set_meta("consecutive_failures", 0)
            state_mgr.save()
            if sleep_or_stop(ERROR_BACKOFF_SHORT):
                break

    log.info("Bot loop durdu.")


# ============================================================
# ENTRYPOINT / SHUTDOWN
# ============================================================

_SHUTDOWN_LOCK = threading.RLock()
_SHUTDOWN_DONE = False


def shutdown_resources() -> None:
    global _SHUTDOWN_DONE
    with _SHUTDOWN_LOCK:
        if _SHUTDOWN_DONE:
            return
        _SHUTDOWN_DONE = True
        log.info("Shutdown basladi, kaynaklar kapatiliyor.")
        _STOP_EVENT.set()
        try:
            _STATE_MGR.save()
        except Exception as e:
            log.warning("Shutdown'da state kaydedilemedi: %s", e)
        try:
            _FEATURE_EXECUTOR.shutdown(wait=False, cancel_futures=True)
            _SYMBOL_EXECUTOR.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        close_http_sessions()


def _signal_handler(*_args) -> None:
    shutdown_resources()
    raise SystemExit(0)


def main() -> None:
    import atexit
    import signal as _signal

    validate_env()
    atexit.register(shutdown_resources)

    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            _signal.signal(getattr(_signal, sig_name), _signal_handler)
        except (ValueError, AttributeError):
            pass

    threading.Thread(target=bot_loop, daemon=True, name="bot-loop").start()
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)


if __name__ == "__main__":
    main()
