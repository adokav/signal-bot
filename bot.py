"""
Signal Bot — Integrated Movement + News + Session + Basis

Production-grade kripto sinyal botu. MEXC üzerinden coin verilerini çeker,
makro/trend/momentum/volume/likidite/basis skorlarını hesaplar, news katmanı
ile yön-bağımlı modülasyon uygular ve Telegram'a sinyal gönderir.

Mimari:
    - Flask: health endpoint
    - Background thread: ana bot döngüsü
    - State: atomik dosya yazımı + thread-safe
    - HTTP: requests.Session ile connection pooling, akıllı retry
    - News: NewsAPI + GDELT, kategori bazlı keyword classification
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

__version__ = "2.0.0-regime-commander"

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
# ENV VARIABLES (validation at startup)
# ============================================================

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
PORT = env_int("PORT", 10000, min_value=1)


def validate_env() -> None:
    """Startup-time env validation. Eksik kritik değişkenleri logla.

    STRICT_ENV=1 verilmişse kritik değişken eksikse exit eder (production).
    """
    missing = []
    if not TOKEN:
        missing.append("TOKEN")
    if not CHAT_ID:
        missing.append("CHAT_ID")
    if missing:
        msg = f"Eksik env değişkenleri: {', '.join(missing)} — Telegram mesajları gönderilemez."
        if os.getenv("STRICT_ENV") == "1":
            log.error(msg)
            raise SystemExit(1)
        log.warning(msg)
    if not NEWS_API_KEY:
        log.info("NEWS_API_KEY eksik — sadece GDELT kullanılacak.")


# ============================================================
# CONSTANTS — BASE URLS
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
# TIMING CONFIG (saniye cinsinden)
# ============================================================

STATE_FILE = os.getenv("STATE_FILE", "state.json")
STATE_VERSION = 4  # Migration için

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

# Telegram noise control:
# Default behavior: only trade open/close messages + hourly heartbeat.
SEND_STANDALONE_NEWS_ALERTS = os.getenv("SEND_STANDALONE_NEWS_ALERTS", "0") == "1"
SEND_MOVEMENT_ALERTS = os.getenv("SEND_MOVEMENT_ALERTS", "0") == "1"
SEND_SUMMARY_MESSAGES = os.getenv("SEND_SUMMARY_MESSAGES", "0") == "1"

TRADE_TRACKING_ENABLED = os.getenv("TRADE_TRACKING_ENABLED", "1") == "1"
TRADE_OPEN_COOLDOWN_SECONDS = env_int("TRADE_OPEN_COOLDOWN_SECONDS", 4 * 60 * 60, min_value=0)
TRADE_ALERT_RETRY_SECONDS = env_int("TRADE_ALERT_RETRY_SECONDS", 5 * 60, min_value=30)

# Feature Importance Analyzer v1
FEATURE_IMPORTANCE_ENABLED = os.getenv("FEATURE_IMPORTANCE_ENABLED", "1") == "1"
FEATURE_IMPORTANCE_MIN_TRADES = env_int("FEATURE_IMPORTANCE_MIN_TRADES", 12, min_value=3)
FEATURE_IMPORTANCE_MIN_BUCKET_TRADES = env_int("FEATURE_IMPORTANCE_MIN_BUCKET_TRADES", 5, min_value=2)
FEATURE_IMPORTANCE_COMPONENTS = (
    "macro", "market", "mtf", "trend", "momentum",
    "volume", "liquidity", "basis", "funding",
)

# Weight Learning Engine v1
WEIGHT_LEARNING_ENABLED = os.getenv("WEIGHT_LEARNING_ENABLED", "1") == "1"
WEIGHT_LEARNING_MIN_TRADES = env_int("WEIGHT_LEARNING_MIN_TRADES", 25, min_value=5)
WEIGHT_LEARNING_MIN_BUCKET_TRADES = env_int("WEIGHT_LEARNING_MIN_BUCKET_TRADES", 8, min_value=3)
WEIGHT_LEARNING_RATE = env_float("WEIGHT_LEARNING_RATE", 0.10, min_value=0.001)
WEIGHT_LEARNING_MAX_DELTA = env_float("WEIGHT_LEARNING_MAX_DELTA", 0.15, min_value=0.01)
WEIGHT_LEARNING_MIN_WEIGHT = env_float("WEIGHT_LEARNING_MIN_WEIGHT", 0.02, min_value=0.0)
WEIGHT_LEARNING_MAX_WEIGHT = env_float("WEIGHT_LEARNING_MAX_WEIGHT", 0.35, min_value=0.05)
WEIGHT_LEARNING_SUGGESTIONS_FILE = os.getenv("WEIGHT_LEARNING_SUGGESTIONS_FILE", "weight_suggestions.json")

# Human-in-the-loop Parameter Approval v1
PARAMETER_SUGGESTIONS_FILE = os.getenv("PARAMETER_SUGGESTIONS_FILE", "parameter_suggestions.json")
ADAPTIVE_CONFIG_FILE = os.getenv("ADAPTIVE_CONFIG_FILE", "adaptive_config.json")
PARAMETER_CHANGE_LOG_FILE = os.getenv("PARAMETER_CHANGE_LOG_FILE", "parameter_change_log.jsonl")
TELEGRAM_COMMANDS_ENABLED = os.getenv("TELEGRAM_COMMANDS_ENABLED", "1") == "1"
TELEGRAM_COMMAND_POLL_INTERVAL_SECONDS = env_int(
    "TELEGRAM_COMMAND_POLL_INTERVAL_SECONDS", 60, min_value=10
)
PARAMETER_SUGGESTION_MIN_CONFIDENCE = env_float(
    "PARAMETER_SUGGESTION_MIN_CONFIDENCE", 0.45, min_value=0.0
)

# Backtest Validation v1
# V1 gerçek tarihsel replay değildir; kapalı trade hafızasındaki raw skor snapshotlarını
# kullanarak mevcut ve önerilen ağırlıkların kazanan/kaybeden trade ayrımını kıyaslar.
BACKTEST_VALIDATION_ENABLED = os.getenv("BACKTEST_VALIDATION_ENABLED", "1") == "1"
BACKTEST_VALIDATION_MIN_TRADES = env_int("BACKTEST_VALIDATION_MIN_TRADES", 20, min_value=5)
BACKTEST_VALIDATION_MIN_IMPROVEMENT = env_float(
    "BACKTEST_VALIDATION_MIN_IMPROVEMENT", 0.02, min_value=0.0
)
BACKTEST_VALIDATION_REQUIRE_PASS = os.getenv("BACKTEST_VALIDATION_REQUIRE_PASS", "1") == "1"
BACKTEST_VALIDATION_REPORT_FILE = os.getenv(
    "BACKTEST_VALIDATION_REPORT_FILE", "backtest_validation_report.json"
)

# Position Sizing Engine v2
# Risk artık sadece sabit RISK_PCT_PER_TRADE değildir; sinyal kalitesi,
# confidence, piyasa rejimi, sembol/grup edge'i ve loss-streak'e göre
# kontrollü şekilde ölçeklenir.
RISK_PCT_PER_TRADE = env_float("RISK_PCT_PER_TRADE", 0.01, min_value=0.0001)
POSITION_SIZING_ENABLED = os.getenv("POSITION_SIZING_ENABLED", "1") == "1"
POSITION_SIZING_MIN_RISK_PCT = env_float("POSITION_SIZING_MIN_RISK_PCT", 0.0025, min_value=0.0001)
POSITION_SIZING_MAX_RISK_PCT = env_float("POSITION_SIZING_MAX_RISK_PCT", 0.03, min_value=0.001)
POSITION_SIZING_BASE_RISK_PCT = env_float(
    "POSITION_SIZING_BASE_RISK_PCT", RISK_PCT_PER_TRADE, min_value=0.0001
)
POSITION_SIZING_MIN_EDGE_TRADES = env_int("POSITION_SIZING_MIN_EDGE_TRADES", 6, min_value=2)
POSITION_SIZING_LOOKBACK_TRADES = env_int("POSITION_SIZING_LOOKBACK_TRADES", 60, min_value=10)
POSITION_SIZING_LOSS_STREAK_CUT_1 = env_int("POSITION_SIZING_LOSS_STREAK_CUT_1", 2, min_value=1)
POSITION_SIZING_LOSS_STREAK_CUT_2 = env_int("POSITION_SIZING_LOSS_STREAK_CUT_2", 3, min_value=2)

# Regime Commander + Entry + Strategy Simulation
REGIME_COMMANDER_ENABLED = os.getenv("REGIME_COMMANDER_ENABLED", "1") == "1"
ENTRY_ENGINE_ENABLED = os.getenv("ENTRY_ENGINE_ENABLED", "1") == "1"
ENTRY_ENGINE_REQUIRE_READY = os.getenv("ENTRY_ENGINE_REQUIRE_READY", "1") == "1"
STRATEGY_SIMULATION_ENABLED = os.getenv("STRATEGY_SIMULATION_ENABLED", "1") == "1"
STRATEGY_SIMULATION_MIN_TRADES = env_int("STRATEGY_SIMULATION_MIN_TRADES", 20, min_value=5)
STRATEGY_SIMULATION_REPORT_FILE = os.getenv(
    "STRATEGY_SIMULATION_REPORT_FILE", "strategy_simulation_report.json"
)

# Hata sonrası bekleme süreleri
ERROR_BACKOFF_SHORT = env_int("ERROR_BACKOFF_SHORT", 60, min_value=1)
ERROR_BACKOFF_LONG = env_int("ERROR_BACKOFF_LONG", 300, min_value=1)

# ============================================================
# FEATURE / KLINE CONFIG
# ============================================================

KLINE_LIMIT = env_int("KLINE_LIMIT", 100, min_value=60)
VOLUME_WINDOW = env_int("VOLUME_WINDOW", 30, min_value=5)
MIN_KLINES_5M = 14                       # son kapanmış bar referansıyla ret_1h için
MIN_KLINES_15M = 18                      # son kapanmış bar referansıyla ret_4h için

# Periodlar (bar adedi)
# Not: Getiri hesapları "son kapanmış bar" üzerinden yapılır (-2 index).
RET_5M_BARS = 1        # 5m
RET_15M_BARS = 3       # 15m (5m × 3)
RET_1H_BARS = 12       # 60m (5m × 12)
RET_4H_BARS = 16       # 240m (15m × 16)

# EMA periyotları
EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 50

# Multi-timeframe engine için daha yüksek zaman dilimleri
MIN_KLINES_1H = 55
MIN_KLINES_4H = 55


# ============================================================
# MOVEMENT ALERT THRESHOLDS
# ============================================================

MOVEMENT_ALERT_THRESHOLDS: dict[str, dict[str, float]] = {
    "CORE": {"ret_15m": 2.0, "ret_1h": 3.0, "volume_ratio": 2.5},
    "HIGH_BETA": {"ret_15m": 3.0, "ret_1h": 5.0, "volume_ratio": 2.0},
}
# ============================================================
# TRADE PLAN CONFIG (sadece plan üretir, emir göndermez)
# ============================================================

ACCOUNT_SIZE_USD = env_float("ACCOUNT_SIZE_USD", 800.0, min_value=1.0)

TRADE_PLAN_CONFIG: dict[str, dict[str, float]] = {
    "CORE": {
        "stop_pct": 0.020,
        "tp1_r": 1.0,
        "tp2_r": 2.0,
        "tp3_r": 3.0,
    },
    "HIGH_BETA": {
        "stop_pct": 0.035,
        "tp1_r": 1.0,
        "tp2_r": 2.0,
        "tp3_r": 3.0,
    },
}

if POSITION_SIZING_MIN_RISK_PCT > POSITION_SIZING_MAX_RISK_PCT:
    log.warning(
        "POSITION_SIZING_MIN_RISK_PCT (%s) > POSITION_SIZING_MAX_RISK_PCT (%s), degerler swap edildi.",
        POSITION_SIZING_MIN_RISK_PCT,
        POSITION_SIZING_MAX_RISK_PCT,
    )
    POSITION_SIZING_MIN_RISK_PCT, POSITION_SIZING_MAX_RISK_PCT = (
        POSITION_SIZING_MAX_RISK_PCT,
        POSITION_SIZING_MIN_RISK_PCT,
    )


# ============================================================
# SIGNAL CONFIG
# ============================================================

MIN_SIGNAL_LEVEL = os.getenv("MIN_SIGNAL_LEVEL", "MEDIUM").upper()
LEVEL_ORDER = {"WEAK": 1, "MEDIUM": 2, "STRONG": 3}
if MIN_SIGNAL_LEVEL not in LEVEL_ORDER:
    log.warning("MIN_SIGNAL_LEVEL=%r gecersiz, MEDIUM kullaniliyor.", MIN_SIGNAL_LEVEL)
    MIN_SIGNAL_LEVEL = "MEDIUM"

CRITICAL_ALERTS = frozenset({
    "❌ SİNYAL İPTAL",
    "🔄 YÖN DEĞİŞTİ",
    "🛑 NEWS VETO",
})

THRESHOLDS: dict[str, dict[str, float]] = {
    "CORE": {"long": 1.55, "short": -1.55, "strong": 2.25},
    "HIGH_BETA": {"long": 1.95, "short": -1.95, "strong": 2.70},
}

WEIGHTS: dict[str, dict[str, float]] = {
    # mtf = higher-timeframe alignment. funding = crowding/squeeze filtresi.
    "CORE": {
        "macro": 0.19, "market": 0.17, "mtf": 0.17, "trend": 0.14,
        "momentum": 0.11, "volume": 0.04, "liquidity": 0.04,
        "basis": 0.09, "funding": 0.05,
    },
    "HIGH_BETA": {
        "macro": 0.11, "market": 0.14, "mtf": 0.15, "trend": 0.15,
        "momentum": 0.21, "volume": 0.09, "liquidity": 0.05,
        "basis": 0.05, "funding": 0.05,
    },
}

# Spread limits (basis points) — DRY: tek kaynaktan
SPREAD_LIMITS: dict[str, float] = {
    "CORE": 12,
    "HIGH_BETA": 25,
}

SCORE_PARAMS: dict[str, dict[str, float]] = {
    "macro": {
        "change_24h_strong": 2.0,
        "ret_4h_strong": 1.0,
        "weight_change_24h": 1.5,
        "weight_ret_4h": 1.5,
    },
    "market": {"ret_1h_strong": 0.5},
    "momentum": {
        "ret_5m_strong": 0.15,
        "ret_15m_strong": 0.35,
        "ret_1h_strong": 0.8,
    },
    "volume": {"spike": 2.0, "high": 1.3, "low": 0.6},
    "veto": {
        "vol_low_high_beta": 0.7,
        "btc_unclear_1h": 0.25,
        "btc_unclear_4h": 0.40,
    },
}

# ============================================================
# REGIME / QUALITY / PORTFOLIO CONFIG
# ============================================================

REGIME_CONFIG = {
    "trend_ret_4h": 1.2,
    "trend_ret_24h": 2.5,
    "chop_ret_4h": 0.45,
    "risk_off_news": -2.0,
    "news_chaos_abs": 2.5,
    "altseason_eth_outperf": 1.0,
    "squeeze_funding_abs": 0.08,
    "high_vol_1h_core": 3.0,
    "high_vol_1h_high_beta": 5.0,
}

# Regime Commander Strategy Matrix
# Felsefe:
#   Kötü rejimde hayatta kal.
#   İyi rejimde büyü.
#   Mükemmel rejimde agresifleş.
#   Rejim bozulunca çık.
REGIME_STRATEGY_MATRIX: dict[str, dict[str, Any]] = {
    "RISK_ON_TREND_UP": {
        "direction_bias": "LONG_ONLY",
        "allow_long": True,
        "allow_short": False,
        "high_beta_allowed": True,
        "risk_multiplier": 1.25,
        "min_quality": "A",
        "quality_bonus": 8,
        "tp_style": "trend_runner",
        "note": "Risk-on yukarı trend: pullback long ve güçlü A/A+ setup öncelikli.",
    },
    "RISK_ON_ALTSEASON": {
        "direction_bias": "LONG_ONLY_HIGH_BETA_OK",
        "allow_long": True,
        "allow_short": False,
        "high_beta_allowed": True,
        "risk_multiplier": 1.45,
        "min_quality": "A",
        "quality_bonus": 10,
        "tp_style": "aggressive_runner",
        "note": "Altseason rejimi: HIGH_BETA long serbest ama pump kovalamadan retest/pullback şart.",
    },
    "RISK_OFF_TREND_DOWN": {
        "direction_bias": "SHORT_ONLY_OR_CASH",
        "allow_long": False,
        "allow_short": True,
        "high_beta_allowed": False,
        "risk_multiplier": 0.55,
        "min_quality": "A+",
        "quality_bonus": -10,
        "tp_style": "defensive",
        "note": "Risk-off düşüş: long kapalı, sadece çok kaliteli short veya nakit.",
    },
    "CHOP_RANGE": {
        "direction_bias": "SELECTIVE_ONLY",
        "allow_long": True,
        "allow_short": True,
        "high_beta_allowed": False,
        "risk_multiplier": 0.45,
        "min_quality": "A+",
        "quality_bonus": -12,
        "tp_style": "quick_tp",
        "note": "Chop/range: sinyal gürültüsü yüksek; A+ dışında işlem yok, risk düşük.",
    },
    "NEWS_CHAOS": {
        "direction_bias": "NO_NEW_TRADE",
        "allow_long": False,
        "allow_short": False,
        "high_beta_allowed": False,
        "risk_multiplier": 0.0,
        "min_quality": "A+",
        "quality_bonus": -30,
        "tp_style": "cash",
        "note": "Haber kaosu: yeni trade kapalı; mevcut pozisyonlarda risk azaltma öncelikli.",
    },
    "SQUEEZE_LONG": {
        "direction_bias": "LONG_ONLY_FAST_TP",
        "allow_long": True,
        "allow_short": False,
        "high_beta_allowed": True,
        "risk_multiplier": 0.85,
        "min_quality": "A+",
        "quality_bonus": 4,
        "tp_style": "fast_tp_runner",
        "note": "Short squeeze ihtimali: küçük başlangıç, hızlı TP1 ve runner yaklaşımı.",
    },
    "SQUEEZE_SHORT": {
        "direction_bias": "SHORT_ONLY_FAST_TP",
        "allow_long": False,
        "allow_short": True,
        "high_beta_allowed": True,
        "risk_multiplier": 0.80,
        "min_quality": "A+",
        "quality_bonus": 4,
        "tp_style": "fast_tp_runner",
        "note": "Long liquidation ihtimali: küçük short, hızlı kâr alma.",
    },
    "NEUTRAL": {
        "direction_bias": "BALANCED",
        "allow_long": True,
        "allow_short": True,
        "high_beta_allowed": False,
        "risk_multiplier": 0.75,
        "min_quality": "A",
        "quality_bonus": -3,
        "tp_style": "standard",
        "note": "Nötr rejim: sadece kaliteli setup, HIGH_BETA sınırlı.",
    },
}

TRADE_QUALITY_MIN_GRADE = os.getenv("TRADE_QUALITY_MIN_GRADE", "A")
TRADE_QUALITY_ORDER = {"D": 0, "C": 1, "B": 2, "A": 3, "A+": 4}
if TRADE_QUALITY_MIN_GRADE not in TRADE_QUALITY_ORDER:
    log.warning(
        "TRADE_QUALITY_MIN_GRADE=%r gecersiz, A kullaniliyor.",
        TRADE_QUALITY_MIN_GRADE,
    )
    TRADE_QUALITY_MIN_GRADE = "A"

PORTFOLIO_LIMITS = {
    "max_active_signals": env_int("MAX_ACTIVE_SIGNALS", 2, min_value=1),
    "max_same_direction": env_int("MAX_SAME_DIRECTION", 1, min_value=1),
    "max_high_beta_active": env_int("MAX_HIGH_BETA_ACTIVE", 1, min_value=0),
}

FUNDING_CONFIG = {
    "crowded_positive": 0.06,
    "crowded_negative": -0.06,
    "extreme_positive": 0.12,
    "extreme_negative": -0.12,
}

# ============================================================
# US MARKET CALENDAR
# ============================================================

US_MARKET_HOLIDAYS: dict[int, set[str]] = {
    2025: {
        "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
        "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
        "2025-11-27", "2025-12-25",
    },
    2026: {
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
        "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
        "2026-11-26", "2026-12-25",
    },
    2027: {
        "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26",
        "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
        "2027-11-25", "2027-12-24",
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
            "war", "missile", "attack", "strike", "military",
            "iran", "israel", "taiwan", "russia", "ukraine",
            "hormuz", "nuclear", "escalation", "invasion",
        ],
        "risk": -3,
    },
    "TARIFF": {
        "keywords": [
            "trump tariff", "tariff", "trade war", "sanction",
            "china tariff", "import duty", "export ban",
        ],
        "risk": -2,
    },
    "FED_MACRO": {
        "keywords": [
            "powell", "cpi", "pce", "inflation", "payroll",
            "nfp", "unemployment", "rate cut", "rate hike",
            "fed funds", "fomc",
        ],
        "risk": -1,
    },
    "CRYPTO_RISK": {
        "keywords": [
            "stablecoin depeg", "exchange hack", "crypto hack",
            "exchange outage", "liquidation cascade",
            "etf rejection", "sec lawsuit",
            "depeg", "exploit", "binance", "coinbase", "kraken",
        ],
        "risk": -2,
    },
    "RISK_ON": {
        "keywords": [
            "ceasefire", "deal reached", "tariff pause",
            "rate cut hopes", "etf approval", "peace agreement",
            "diplomatic breakthrough",
        ],
        "risk": 2,
    },
}

# News thresholds
NEWS_VETO_THRESHOLD = 2.0
NEWS_DAMPEN_THRESHOLD = 1.0
NEWS_DAMPEN_FACTOR = 0.6
NEWS_BOOST_THRESHOLD = 1.0
NEWS_BOOST_FACTOR = 1.1
NEWS_MULTI_SOURCE_BOOST = 3
NEWS_MULTI_SOURCE_FACTOR = 1.2

# NewsAPI günlük rate limit (free tier: 100/gün) — defansif kullanım
NEWSAPI_QUERIES_PER_SCAN = 3
NEWSAPI_DAILY_LIMIT = env_int("NEWSAPI_DAILY_LIMIT", 90, min_value=1)

# ============================================================
# HTTP SESSION (connection pooling + retry)
# ============================================================

HTTP_MAX_CONCURRENCY = env_int("HTTP_MAX_CONCURRENCY", 24, min_value=1)
_HTTP_SEMAPHORE = threading.BoundedSemaphore(HTTP_MAX_CONCURRENCY)
_HTTP_LOCAL = threading.local()
_HTTP_SESSION_LOCK = threading.RLock()
_HTTP_SESSIONS: list[requests.Session] = []
_STOP_EVENT = threading.Event()


def _build_session() -> requests.Session:
    """Thread-local requests.Session connection pooling sağlar."""
    sess = requests.Session()
    retry = Retry(
        total=0,  # Manual retry yapıyoruz; urllib3'a bırakmıyoruz
        backoff_factor=0,
        status_forcelist=[],
    )
    # 9 coin × 5 endpoint = 45 paralel istek olabiliyor; pool buna göre.
    adapter = HTTPAdapter(
        pool_connections=32,
        pool_maxsize=128,
        max_retries=retry,
    )
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

# Feature engine için kalıcı thread pool (her get_features çağrısında yeni
# pool yaratmamak için). Coin sayısı × endpoint sayısı kadar worker yeterli.
_FEATURE_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(16, len(COINS) * 5),
    thread_name_prefix="feat",
)
# Coinler arası paralel feature çekimi için ayrı pool.
_SYMBOL_EXECUTOR = ThreadPoolExecutor(
    max_workers=max(4, len(COINS)),
    thread_name_prefix="symbol",
)

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
# SESSION CONTEXT (US market hours)
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
        "US_OPEN", 1.00, 1.00, 1.00,
        "ABD piyasası açık: makro/risk verileri normal ağırlıkta.",
    ),
    "US_EXTENDED": SessionContext(
        "US_EXTENDED", 0.50, 1.10, 0.95,
        "ABD pre/after-market: makro etkisi kısmen azaltıldı.",
    ),
    "US_CLOSED": SessionContext(
        "US_CLOSED", 0.35, 1.15, 0.90,
        "ABD piyasası kapalı: kripto içi sinyallerin ağırlığı artırıldı.",
    ),
    "US_HOLIDAY": SessionContext(
        "US_HOLIDAY", 0.20, 1.20, 0.85,
        "ABD piyasa tatili: makro/risk verilerinin etkisi azaltıldı.",
    ),
    "WEEKEND": SessionContext(
        "WEEKEND", 0.20, 1.20, 0.85,
        "Hafta sonu: ABD piyasa verileri bayat kabul edildi.",
    ),
}


def _compute_session_context(minute_key: int) -> SessionContext:
    """minute_key ile cache'lenir — aynı dakika içinde tekrar hesap yapılmaz."""
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
        log.warning("%d yılı için ABD tatil listesi tanımlı değil.", year)

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
    """60s cache: aynı dakika içinde tekrar tekrar hesaplama yapma."""
    return _cached_session(now_ts() // 60)


# ============================================================
# TELEGRAM
# ============================================================

# Telegram mesaj boyut limiti: 4096 karakter
TELEGRAM_MAX_LEN = 4000  # Güvenli pay


def send_message(text: str) -> bool:
    """Telegram mesaj gönder. Başarı durumunu döner."""
    if not TOKEN or not CHAT_ID:
        log.warning("TOKEN veya CHAT_ID eksik, mesaj gönderilemedi.")
        return False

    if len(text) > TELEGRAM_MAX_LEN:
        text = text[:TELEGRAM_MAX_LEN - 20] + "\n\n[...mesaj kısaltıldı]"

    url = f"{TELEGRAM_BASE}/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}

    try:
        with _HTTP_SEMAPHORE:
            r = _http_session().post(url, data=data, timeout=15)
        if r.status_code != 200:
            log.error("Telegram %s: %s", r.status_code, r.text[:200])
            return False
        log.debug("Telegram OK")
        return True
    except requests.RequestException as e:
        log.error("Telegram gönderim hatası: %s", type(e).__name__)
        return False


# ============================================================
# HTTP HELPERS
# ============================================================

class TransientHTTPError(Exception):
    """Geçici HTTP hatası — retry edilebilir."""


class PermanentHTTPError(Exception):
    """Kalıcı HTTP hatası — retry'a değmez (4xx)."""


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
    Akıllı HTTP retry:
      - 4xx (429 hariç): fail-fast, PermanentHTTPError
      - 429: Retry-After'a uyar, exponential backoff
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
                log.debug("Bağlantı hatası (%s), %.1fs bekleniyor: %s", type(e).__name__, wait, url)
                if sleep_or_stop(wait):
                    raise TransientHTTPError("Shutdown requested") from e
            continue
        except requests.RequestException as e:
            # Genel requests hatası — geçici varsay
            last_err = e
            if attempt < attempts - 1 and sleep_or_stop(_retry_wait(base_delay, attempt)):
                raise TransientHTTPError("Shutdown requested") from e
            continue

        # 429 → rate limit
        if r.status_code == 429:
            wait = _retry_wait(base_delay, attempt, r)
            log.warning("429 rate limit, %.1fs bekleniyor: %s", wait, url)
            last_err = TransientHTTPError(f"429 Too Many Requests: {url}")
            if attempt < attempts - 1 and sleep_or_stop(wait):
                raise TransientHTTPError("Shutdown requested")
            continue

        # Diğer 4xx → kalıcı
        if 400 <= r.status_code < 500:
            raise PermanentHTTPError(
                f"HTTP {r.status_code}: {url} — {r.text[:200]}"
            )

        # 5xx → geçici
        if r.status_code >= 500:
            last_err = TransientHTTPError(f"HTTP {r.status_code}: {url}")
            if attempt < attempts - 1 and sleep_or_stop(_retry_wait(base_delay, attempt, r)):
                raise TransientHTTPError("Shutdown requested")
            continue

        # 2xx
        try:
            return r.json()
        except ValueError as e:
            raise PermanentHTTPError(f"Geçersiz JSON: {url} — {e}") from e

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
        raise PermanentHTTPError(f"{symbol} kline response list değil")
    return data


def get_ticker(symbol: str) -> dict:
    data = request_json(
        f"{MEXC_SPOT_BASE}/api/v3/ticker/24hr",
        {"symbol": symbol},
    )
    if not isinstance(data, dict):
        raise PermanentHTTPError(f"{symbol} ticker response dict değil")
    return data


def get_spot_price(symbol: str) -> float:
    data = request_json(
        f"{MEXC_SPOT_BASE}/api/v3/ticker/price",
        {"symbol": symbol},
    )
    if not isinstance(data, dict):
        raise PermanentHTTPError(f"{symbol} price response dict değil")
    return float(data["price"])


def get_book(symbol: str) -> dict:
    data = request_json(
        f"{MEXC_SPOT_BASE}/api/v3/ticker/bookTicker",
        {"symbol": symbol},
    )
    if not isinstance(data, dict):
        raise PermanentHTTPError(f"{symbol} book response dict değil")
    return data


def get_futures_fair_price(symbol: str) -> Optional[float]:
    """MEXC futures fair price. Bulunamazsa None döner."""
    futures_symbol = to_futures_symbol(symbol)
    url = f"{MEXC_FUTURES_BASE}/api/v1/contract/fair_price/{futures_symbol}"

    try:
        data = request_json(url)
    except (TransientHTTPError, PermanentHTTPError) as e:
        log.debug("%s futures fair price alınamadı: %s", symbol, e)
        return None

    if not isinstance(data, dict):
        return None

    # MEXC bazen {"data": {"fairPrice": ...}}, bazen {"fairPrice": ...} dönüyor
    candidates = []
    if isinstance(data.get("data"), dict):
        candidates.append(data["data"])
    candidates.append(data)

    for src in candidates:
        for k in ("fairPrice", "price"):
            if k in src:
                try:
                    return float(src[k])
                except (TypeError, ValueError):
                    continue
    return None




def get_funding_rate(symbol: str) -> Optional[float]:
    """MEXC futures funding rate (%). Veri alınamazsa None döner."""
    futures_symbol = to_futures_symbol(symbol)
    endpoints = [
        (f"{MEXC_FUTURES_BASE}/api/v1/contract/funding_rate/{futures_symbol}", None),
        (f"{MEXC_FUTURES_BASE}/api/v1/contract/funding_rate/history", {"symbol": futures_symbol, "page_num": 1, "page_size": 1}),
    ]
    for url, params in endpoints:
        try:
            data = request_json(url, params=params, retries=2)
        except Exception as e:
            log.debug("%s funding rate alınamadı (%s): %s", symbol, url, e)
            continue
        candidates = []
        if isinstance(data, dict):
            if isinstance(data.get("data"), dict):
                candidates.append(data["data"])
            if isinstance(data.get("data"), list) and data["data"]:
                candidates.append(data["data"][0])
            candidates.append(data)
        for src in candidates:
            if not isinstance(src, dict):
                continue
            for key in ("fundingRate", "funding_rate", "rate"):
                if key in src:
                    try:
                        return float(src[key]) * 100.0
                    except (TypeError, ValueError):
                        pass
    return None

# ============================================================
# MATH HELPERS
# ============================================================

def pct(a: float, b: float) -> float:
    """Yüzde değişim. b=0 ise 0.0 döner."""
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def closed_bar_return(closes: list[float], bars: int, *, series_name: str) -> float:
    """Son kapanmış bara göre getiri hesapla.

    MEXC kline endpoint'i son barı henüz kapanmamış verebildiği için hesap
    daima closes[-2] üzerinden yapılır. Bu, sinyal jitter'ını azaltır.
    """
    need = bars + 2  # latest closed (-2) ve referans için
    if len(closes) < need:
        raise ValueError(
            f"{series_name} için yetersiz bar: gerekli={need}, mevcut={len(closes)}"
        )
    latest_closed = closes[-2]
    reference = closes[-(bars + 2)]
    return pct(latest_closed, reference)


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def ema(values: list[float], period: int) -> float:
    """Exponential Moving Average. Veri yetersizse SMA fallback."""
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
    """Görsel bar gösterimi: -max_abs ile +max_abs arası."""
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
# STATE MANAGEMENT (thread-safe)
# ============================================================

class StateManager:
    """Thread-safe state yönetimi. Atomik yazım, in-memory cache."""

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
            log.error("State okunamadı, sıfırlanıyor: %s", e)
            return self._fresh_state()

        if not isinstance(data, dict):
            log.error("State dict değil, sıfırlanıyor.")
            return self._fresh_state()
        return self._migrate(data)

    @staticmethod
    def _fresh_state() -> dict:
        return {"version": STATE_VERSION, "symbols": {}, "meta": {}, "trades": {}}

    @staticmethod
    def _migrate(data: dict) -> dict:
        version = data.get("version", 1)

        # v1 → v2: meta key'leri _ prefix'inden ayır
        if "symbols" not in data:
            symbols = {k: v for k, v in data.items() if not k.startswith("_")}
            meta = {
                "last_summary_ts": data.get("_last_summary_ts", 0),
                "last_heartbeat_ts": data.get("_last_heartbeat_ts", 0),
                "last_news_scan_ts": data.get("_last_news_scan_ts", 0),
                "last_news_alert_ts": data.get("_last_news_alert_ts", 0),
                "last_news_alert_hash": data.get("_last_news_alert_hash"),
            }
            log.info("State v1 → v%d migration uygulandı.", STATE_VERSION)
            return {"version": STATE_VERSION, "symbols": symbols, "meta": meta, "trades": {}}

        # Eksik alanları doldur (forward compat)
        data.setdefault("symbols", {})
        data.setdefault("meta", {})
        data.setdefault("trades", {})
        if version < 3:
            for symbol_state in data["symbols"].values():
                if isinstance(symbol_state, dict):
                    symbol_state.setdefault("pending_alert_type", None)
                    symbol_state.setdefault("updated_at", 0)
                    symbol_state.setdefault("actionable", symbol_state.get("signal") in ("LONG", "SHORT"))
            log.info("State v%s → v3 migration uygulandı.", version)
        if version < 4:
            for trade in data["trades"].values():
                if not isinstance(trade, dict):
                    continue
                is_closed = trade.get("result") is not None
                trade.setdefault("open_alert_sent", True)
                trade.setdefault("open_alert_pending", False)
                trade.setdefault("close_alert_sent", is_closed)
                trade.setdefault("close_alert_pending", False)
                trade.setdefault("last_notify_attempt_ts", 0)
            log.info("State v%s → v4 migration uygulandı.", version)
        data["version"] = STATE_VERSION
        return data

    def save(self) -> None:
        """Atomik yazım: tmp + fsync + os.replace."""
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
        """Read-only kopya."""
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

    def get_trades(self) -> dict:
        """Open/closed tracked trades copy."""
        with self._lock:
            return copy.deepcopy(self._state.setdefault("trades", {}))

    def update_trades(self, trades: dict) -> None:
        """Replace tracked trades atomically inside state."""
        with self._lock:
            self._state["trades"] = copy.deepcopy(trades)


# Global state manager
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
        "note": "Önemli haber etkisi yok.",
    }


def is_fresh_article(published_at: Optional[str]) -> bool:
    """ISO-8601 tarih kontrolü."""
    if not published_at:
        return True  # Tarih yoksa şüpheli; agregasyon süreci elemese de güvenli

    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True

    age = datetime.now(timezone.utc) - dt
    return age <= timedelta(hours=NEWS_MAX_AGE_HOURS)


def _newsapi_quota_used_today() -> int:
    """Günlük NewsAPI kullanımını state'ten oku."""
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
    """NewsAPI'den jeopolitik/makro/kripto başlıkları çeker."""
    if not NEWS_API_KEY:
        return []

    used_today = _newsapi_quota_used_today()
    if used_today + NEWSAPI_QUERIES_PER_SCAN > NEWSAPI_DAILY_LIMIT:
        log.warning(
            "NewsAPI günlük limit korumasına takıldı (%d/%d). Atlanıyor.",
            used_today, NEWSAPI_DAILY_LIMIT,
        )
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
                params={
                    "q": q,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 15,
                },
                # apiKey'i header'da göndermek log'larda görünmesini engeller
                headers={"X-Api-Key": NEWS_API_KEY},
            )
            success_count += 1
        except (TransientHTTPError, PermanentHTTPError) as e:
            log.warning("NewsAPI query başarısız (%s...): %s", q[:30], e)
            continue

        for article in data.get("articles", []) if isinstance(data, dict) else []:
            title = article.get("title") or ""
            published_at = article.get("publishedAt")

            if not title or not is_fresh_article(published_at):
                continue

            headlines.append({
                "title": title,
                "source": (article.get("source") or {}).get("name", "Unknown"),
                "url": article.get("url", ""),
                "published_at": published_at,
                "provider": "NewsAPI",
            })

    if success_count > 0:
        _newsapi_quota_increment(success_count)

    return headlines


def fetch_gdelt_headlines() -> list[dict]:
    """GDELT son haber feed'i."""
    query = (
        "trump tariff iran israel china war missile fed powell "
        "crypto sec etf hack stablecoin"
    )

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
        log.warning("GDELT alınamadı: %s", e)
        return []

    headlines = []
    for article in data.get("articles", []) if isinstance(data, dict) else []:
        title = article.get("title") or ""
        if not title:
            continue

        # GDELT seendate formatı: 'YYYYMMDDhhmmss'
        seendate = article.get("seendate")
        published_at = None
        if seendate and len(seendate) >= 14:
            try:
                published_at = (
                    f"{seendate[0:4]}-{seendate[4:6]}-{seendate[6:8]}T"
                    f"{seendate[8:10]}:{seendate[10:12]}:{seendate[12:14]}Z"
                )
            except Exception:
                published_at = None

        if not is_fresh_article(published_at):
            continue

        headlines.append({
            "title": title,
            "source": article.get("sourceCountry", "GDELT"),
            "url": article.get("url", ""),
            "published_at": published_at,
            "provider": "GDELT",
        })

    return headlines


def _compile_keyword_patterns() -> dict:
    """Tek kelime keyword'leri word-boundary regex'e, çok kelimeli ifadeleri
    substring match'e çevirir. Modül yüklenirken bir kez çalışır."""
    compiled = {}
    for category, cfg in NEWS_CATEGORIES.items():
        patterns = []
        for kw in cfg["keywords"]:
            kw_lower = kw.lower().strip()
            if not kw_lower:
                log.warning("Boş keyword atlanıyor (kategori: %s)", category)
                continue
            if " " in kw_lower:
                patterns.append(("substring", kw_lower))
            else:
                patterns.append((
                    "regex",
                    re.compile(rf"\b{re.escape(kw_lower)}\b", re.IGNORECASE),
                ))
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
        else:
            if pattern in text_lower:
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

    # Match yoğunluğuna göre risk şiddeti
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
    """Tüm news kaynaklarını tarayıp en yüksek risk skorunu döndürür."""
    headlines = []

    try:
        headlines.extend(fetch_newsapi_headlines())
    except Exception as e:
        log.warning("NewsAPI hata: %s", e)

    try:
        headlines.extend(fetch_gdelt_headlines())
    except Exception as e:
        log.warning("GDELT hata: %s", e)

    # Başlık dedup
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

    # Çoklu kaynak doğrulaması
    category_counts: dict[str, int] = {}
    for item in classified:
        category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1

    # En şiddetli skoru al; çoklu kaynak varsa boost
    classified.sort(key=lambda x: (abs(x["risk_score"]), x["hits"]), reverse=True)
    top = classified[0]

    final_risk = top["risk_score"]
    multi_count = category_counts.get(top["category"], 1)
    if multi_count >= NEWS_MULTI_SOURCE_BOOST:
        final_risk = clamp(final_risk * NEWS_MULTI_SOURCE_FACTOR, -3, 3)

    note = "Haber katmanı aktif."
    if multi_count >= NEWS_MULTI_SOURCE_BOOST:
        note += f" {multi_count} kaynak {top['category']} kategorisini doğruluyor."

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
        f"Doğrulayan Kaynak Sayısı: {news_context.get('match_count', 1)}\n"
        f"Provider: {news_context.get('provider', '-')} ({news_context.get('source', '-')})\n\n"
        f"Başlık:\n{news_context.get('headline', '-')}\n\n"
        f"Bot Etkisi:\n"
        f"- |risk| ≥ {NEWS_VETO_THRESHOLD}: ters yöndeki sinyaller VETO edilir\n"
        f"- |risk| ≥ {NEWS_DAMPEN_THRESHOLD}: ters yöndeki skor x{NEWS_DAMPEN_FACTOR} sönümlenir\n"
        f"- Aynı yön: skor x{NEWS_BOOST_FACTOR} güçlendirilir\n\n"
        f"Zaman: {tr_now_text()}"
    )


# ============================================================
# FEATURE ENGINE (paralel HTTP + partial failure handling)
# ============================================================

def _to_float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field} float'a çevrilemedi: {value!r}") from e


def get_features(symbol: str) -> dict:
    """Tek coin için 5 endpoint paralel çağrılır.

    Spot endpoint'lerden biri fail olursa exception fırlatır (kritik).
    Futures fail olursa basis_pct=None (non-kritik).
    """
    futures = {
        "k5": _FEATURE_EXECUTOR.submit(get_klines, symbol, "5m", KLINE_LIMIT),
        "k15": _FEATURE_EXECUTOR.submit(get_klines, symbol, "15m", KLINE_LIMIT),
        "k1h": _FEATURE_EXECUTOR.submit(get_klines, symbol, "60m", KLINE_LIMIT),
        "k4h": _FEATURE_EXECUTOR.submit(get_klines, symbol, "4h", KLINE_LIMIT),
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

    # Kritik endpoint'lerden biri bile fail ise exception
    critical = ("k5", "k15", "k1h", "k4h", "ticker", "book", "spot")
    failed = [k for k in critical if k in errors]
    if failed:
        raise RuntimeError(
            f"{symbol}: kritik endpoint hatası ({', '.join(failed)}): "
            f"{errors[failed[0]]}"
        )

    k5 = results["k5"]
    k15 = results["k15"]
    k1h = results["k1h"]
    k4h = results["k4h"]
    ticker = results["ticker"]
    book = results["book"]
    spot_price = results["spot"]

    closes_5 = [_to_float(x[4], f"{symbol}.k5.close") for x in k5]
    closes_15 = [_to_float(x[4], f"{symbol}.k15.close") for x in k15]
    closes_1h = [_to_float(x[4], f"{symbol}.k1h.close") for x in k1h]
    closes_4h = [_to_float(x[4], f"{symbol}.k4h.close") for x in k4h]
    volumes_5 = [_to_float(x[5], f"{symbol}.k5.volume") for x in k5]

    if (
        len(closes_5) < MIN_KLINES_5M
        or len(closes_15) < MIN_KLINES_15M
        or len(closes_1h) < MIN_KLINES_1H
        or len(closes_4h) < MIN_KLINES_4H
    ):
        raise ValueError(
            f"{symbol} için yetersiz kline verisi "
            f"(5m={len(closes_5)}/{MIN_KLINES_5M}, "
            f"15m={len(closes_15)}/{MIN_KLINES_15M}, "
            f"1h={len(closes_1h)}/{MIN_KLINES_1H}, "
            f"4h={len(closes_4h)}/{MIN_KLINES_4H})"
        )

    last = closes_5[-1]

    # EMA hesapları
    ema9 = ema(closes_5, EMA_FAST)
    ema21 = ema(closes_5, EMA_MID)
    ema50 = ema(closes_5, EMA_SLOW)
    ema9_15 = ema(closes_15, EMA_FAST)
    ema21_15 = ema(closes_15, EMA_MID)
    ema50_15 = ema(closes_15, EMA_SLOW)

    # Multi-timeframe: 1h setup + 4h trend
    ema9_1h = ema(closes_1h, EMA_FAST)
    ema21_1h = ema(closes_1h, EMA_MID)
    ema50_1h = ema(closes_1h, EMA_SLOW)
    ema9_4h = ema(closes_4h, EMA_FAST)
    ema21_4h = ema(closes_4h, EMA_MID)
    ema50_4h = ema(closes_4h, EMA_SLOW)

    # Returns: son kapanmış bar baz alınır (incomplete candle gürültüsünü azaltır)
    ret_5m = closed_bar_return(closes_5, RET_5M_BARS, series_name=f"{symbol}.5m")
    ret_15m = closed_bar_return(closes_5, RET_15M_BARS, series_name=f"{symbol}.5m")
    ret_1h = closed_bar_return(closes_5, RET_1H_BARS, series_name=f"{symbol}.5m")
    ret_4h = closed_bar_return(closes_15, RET_4H_BARS, series_name=f"{symbol}.15m")
    ret_1h_tf = closed_bar_return(closes_1h, 1, series_name=f"{symbol}.1h")
    ret_4h_tf = closed_bar_return(closes_4h, 1, series_name=f"{symbol}.4h")
    ret_24h_tf = closed_bar_return(closes_4h, 6, series_name=f"{symbol}.4h")

    # Hacim oranı: son kapanmış barın, önceki VOLUME_WINDOW kapanmış bar median'ına oranı
    recent_vols = volumes_5[-(VOLUME_WINDOW + 2):-2]
    if recent_vols:
        median_vol = statistics.median(recent_vols)
        vol_ratio = volumes_5[-2] / median_vol if median_vol > 0 else 1.0
    else:
        vol_ratio = 1.0

    bid = _to_float(book["bidPrice"], f"{symbol}.bid")
    ask = _to_float(book["askPrice"], f"{symbol}.ask")
    mid = (bid + ask) / 2
    raw_spread_bps = ((ask - bid) / mid) * 10000 if mid else 999.0
    spread_bps = max(0.0, raw_spread_bps)

    try:
        change_24h = _to_float(ticker.get("priceChangePercent", 0), f"{symbol}.change_24h")
    except (TypeError, ValueError):
        change_24h = 0.0

    # Futures fair price / funding — non-kritik, fail olabilir
    futures_price = None
    basis_pct = None
    funding_rate = None
    try:
        futures_price = get_futures_fair_price(symbol)
        if futures_price:
            basis_pct = pct(futures_price, spot_price)
    except Exception as e:
        log.debug("%s futures basis alınamadı: %s", symbol, e)
    try:
        funding_rate = get_funding_rate(symbol)
    except Exception as e:
        log.debug("%s funding rate alınamadı: %s", symbol, e)

    return {
        "last": last,
        "spot_price": spot_price,
        "futures_price": futures_price,
        "basis_pct": basis_pct,
        "funding_rate": funding_rate,
        "ema9": ema9, "ema21": ema21, "ema50": ema50,
        "ema9_15": ema9_15, "ema21_15": ema21_15, "ema50_15": ema50_15,
        "ema9_1h": ema9_1h, "ema21_1h": ema21_1h, "ema50_1h": ema50_1h,
        "ema9_4h": ema9_4h, "ema21_4h": ema21_4h, "ema50_4h": ema50_4h,
        "ret_1h_tf": ret_1h_tf, "ret_4h_tf": ret_4h_tf, "ret_24h_tf": ret_24h_tf,
        "ret_5m": ret_5m, "ret_15m": ret_15m,
        "ret_1h": ret_1h, "ret_4h": ret_4h,
        "vol_ratio": vol_ratio,
        "spread_bps": spread_bps,
        "change_24h": change_24h,
    }


# ============================================================
# SCORING FUNCTIONS (saf fonksiyonlar — test edilebilir)
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


def score_mtf(f: dict) -> float:
    """Multi-timeframe skor: 4H ana trend + 1H setup + 15m/5m giriş uyumu.

    Pozitif skor LONG yönünü, negatif skor SHORT yönünü destekler.
    Bu katman görseldeki "Günlük/4H trend → 15m entry" mantığının
    bot içindeki karşılığıdır.
    """
    score = 0.0

    # 4H = ana trend filtresi
    if f["ema9_4h"] > f["ema21_4h"] > f["ema50_4h"]:
        score += 1.4
    elif f["ema9_4h"] < f["ema21_4h"] < f["ema50_4h"]:
        score -= 1.4

    # 1H = setup yönü
    if f["ema9_1h"] > f["ema21_1h"] > f["ema50_1h"]:
        score += 1.0
    elif f["ema9_1h"] < f["ema21_1h"] < f["ema50_1h"]:
        score -= 1.0

    # 4H ve 24H momentum teyidi
    if f["ret_4h_tf"] > 0.6:
        score += 0.3
    elif f["ret_4h_tf"] < -0.6:
        score -= 0.3

    if f["ret_24h_tf"] > 2.0:
        score += 0.3
    elif f["ret_24h_tf"] < -2.0:
        score -= 0.3

    return clamp(score, -3, 3)


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




def score_funding(funding_rate: Optional[float], f: dict) -> float:
    """Funding skorunu crowding/squeeze filtresi olarak kullanır."""
    if funding_rate is None:
        return 0.0
    score = 0.0
    if funding_rate >= FUNDING_CONFIG["extreme_positive"]:
        score -= 1.5
    elif funding_rate >= FUNDING_CONFIG["crowded_positive"]:
        score -= 0.8
    if funding_rate <= FUNDING_CONFIG["extreme_negative"]:
        score += 1.5
    elif funding_rate <= FUNDING_CONFIG["crowded_negative"]:
        score += 0.8
    if funding_rate < 0 and f.get("ret_1h", 0) > 0.8:
        score += 0.4
    if funding_rate > 0 and f.get("ret_1h", 0) < -0.8:
        score -= 0.4
    return clamp(score, -2, 2)

# ============================================================
# SIGNAL ENGINE
# ============================================================

def veto_signal(symbol: str, f: dict, btc: dict, eth: dict) -> Optional[str]:
    """None döner = veto yok. String döner = veto sebebi."""
    group = COINS[symbol]
    p = SCORE_PARAMS["veto"]

    if f["spread_bps"] > SPREAD_LIMITS[group]:
        return "Spread yüksek"

    if group == "HIGH_BETA" and f["vol_ratio"] < p["vol_low_high_beta"]:
        return "Hacim zayıf"

    if symbol not in ("BTCUSDT", "ETHUSDT"):
        btc_unclear = (
            abs(btc["ret_1h"]) < p["btc_unclear_1h"]
            and abs(btc["ret_4h"]) < p["btc_unclear_4h"]
        )
        if btc_unclear:
            return "BTC yönü belirsiz"

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


def apply_news_modulation(
    total_score: float, news_risk: float, session_news_mult: float
) -> tuple[Optional[float], str]:
    """News skorunu yön-bağımlı uygular.

    Returns:
        (modulated_score, action). Veto durumunda (None, "news_veto").
    """
    effective_risk = news_risk * session_news_mult
    abs_risk = abs(effective_risk)

    same_direction = (
        (total_score > 0 and effective_risk > 0)
        or (total_score < 0 and effective_risk < 0)
    )
    opposite_direction = (
        (total_score > 0 and effective_risk < 0)
        or (total_score < 0 and effective_risk > 0)
    )

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
    base_weights = WEIGHTS[group]
    weights = normalize_weights_if_basis_missing(base_weights, features)

    veto = veto_signal(symbol, features, btc, eth)

    macro_mult = session_context.macro_multiplier
    micro_mult = session_context.micro_multiplier
    news_mult = session_context.news_multiplier

    raw = {
        "macro": score_macro(btc) * macro_mult,
        "market": score_market(btc, eth) * micro_mult,
        "mtf": score_mtf(features),
        "trend": score_trend(features) * micro_mult,
        "momentum": score_momentum(features) * micro_mult,
        "volume": score_volume(features) * micro_mult,
        "liquidity": score_liquidity(features, group),
        "basis": score_basis(features["basis_pct"], features) * micro_mult,
        "funding": score_funding(features.get("funding_rate"), features) * micro_mult,
    }

    pre_news_total = sum(raw[k] * weights[k] for k in raw)

    news_risk = float(news_context.get("news_risk_score", 0))
    modulated_total, news_action = apply_news_modulation(
        pre_news_total, news_risk, news_mult
    )

    max_components = {
        "macro": 3 * abs(macro_mult),
        "market": 2 * abs(micro_mult),
        "mtf": 3,
        "trend": 2 * abs(micro_mult),
        "momentum": 2 * abs(micro_mult),
        "volume": 2 * abs(micro_mult),
        "liquidity": 2,
        "basis": 2 * abs(micro_mult),
        "funding": 2 * abs(micro_mult),
    }
    max_total = sum(weights[k] * max_components[k] for k in raw)

    if modulated_total is None:
        # News veto
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

    # NO_TRADE için confidence yanıltıcı olmasın — sadece sinyal varken anlamlı
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
        "funding_missing": features.get("funding_rate") is None,
    }




# ============================================================
# POSITION SIZING ENGINE v2
# ============================================================

def _ps_outcome_value(trade: dict) -> Optional[float]:
    """Closed trade outcome for sizing:
    WIN = +1, LOSS = -1, EXIT uses pnl sign / magnitude.
    """
    if trade.get("result") is None:
        return None
    result = trade.get("result")
    pnl_pct = float(trade.get("pnl_pct", 0) or 0)
    if result == "WIN":
        return 1.0
    if result == "LOSS":
        return -1.0
    if pnl_pct > 0:
        return 0.5
    if pnl_pct < 0:
        return -0.5
    return 0.0


def _ps_closed_trades(state_mgr: StateManager = _STATE_MGR) -> list[dict]:
    trades = [
        t for t in state_mgr.get_trades().values()
        if isinstance(t, dict) and t.get("result") is not None
    ]
    trades.sort(key=lambda t: int(t.get("closed_at") or t.get("opened_at") or 0))
    if POSITION_SIZING_LOOKBACK_TRADES > 0:
        trades = trades[-POSITION_SIZING_LOOKBACK_TRADES:]
    return trades


def _ps_loss_streak(closed: list[dict]) -> int:
    streak = 0
    for t in reversed(closed):
        outcome = _ps_outcome_value(t)
        if outcome is None:
            continue
        if outcome < 0:
            streak += 1
        else:
            break
    return streak


def _ps_edge_for(
    closed: list[dict],
    *,
    symbol: Optional[str] = None,
    group: Optional[str] = None,
    direction: Optional[str] = None,
) -> dict:
    rows = []
    for t in closed:
        if symbol and t.get("symbol") != symbol:
            continue
        if group and t.get("group") != group:
            continue
        if direction and t.get("direction") != direction:
            continue
        outcome = _ps_outcome_value(t)
        if outcome is None:
            continue
        rows.append((outcome, float(t.get("pnl_pct", 0) or 0)))

    sample = len(rows)
    if sample < POSITION_SIZING_MIN_EDGE_TRADES:
        return {
            "ready": False,
            "sample": sample,
            "win_rate": None,
            "avg_pnl_pct": None,
            "edge_score": 0.0,
            "multiplier": 1.0,
            "note": f"Veri yetersiz ({sample}/{POSITION_SIZING_MIN_EDGE_TRADES}).",
        }

    wins = sum(1 for outcome, _ in rows if outcome > 0)
    win_rate = wins / sample
    avg_pnl_pct = sum(pnl for _, pnl in rows) / sample
    edge_score = clamp((win_rate - 0.50) * 2.0 + avg_pnl_pct * 4.0, -1.0, 1.0)

    if edge_score >= 0.35:
        multiplier = 1.30
    elif edge_score >= 0.15:
        multiplier = 1.15
    elif edge_score <= -0.35:
        multiplier = 0.50
    elif edge_score <= -0.15:
        multiplier = 0.75
    else:
        multiplier = 1.0

    return {
        "ready": True,
        "sample": sample,
        "win_rate": round(win_rate * 100, 1),
        "avg_pnl_pct": round(avg_pnl_pct * 100, 2),
        "edge_score": round(edge_score, 3),
        "multiplier": multiplier,
        "note": f"sample={sample}, WR={win_rate*100:.1f}%, avgPnL={avg_pnl_pct*100:.2f}%",
    }


def _ps_quality_multiplier(grade: Optional[str]) -> float:
    return {
        "A+": 1.60,
        "A": 1.20,
        "B": 0.80,
        "C": 0.50,
        "D": 0.25,
    }.get(str(grade or "").upper(), 1.0)


def _ps_confidence_multiplier(confidence: float) -> float:
    if confidence >= 85:
        return 1.35
    if confidence >= 75:
        return 1.20
    if confidence >= 65:
        return 1.05
    if confidence >= 55:
        return 0.85
    return 0.65



def get_regime_strategy(regime_name: str) -> dict:
    """Return a defensive copy of the strategy profile for the current regime."""
    profile = REGIME_STRATEGY_MATRIX.get(regime_name) or REGIME_STRATEGY_MATRIX["NEUTRAL"]
    return copy.deepcopy(profile)


def quality_meets_min(grade: Optional[str], minimum: Optional[str]) -> bool:
    """Compare A+/A/B/C/D grades."""
    if not minimum:
        return True
    return TRADE_QUALITY_ORDER.get(str(grade or "D").upper(), 0) >= TRADE_QUALITY_ORDER.get(str(minimum).upper(), 0)


def _ps_regime_multiplier(signal: str, regime_name: str) -> float:
    """Position sizing regime multiplier delegated to Regime Commander matrix."""
    if not REGIME_COMMANDER_ENABLED:
        return 1.0

    strategy = get_regime_strategy(regime_name or "NEUTRAL")
    base = float(strategy.get("risk_multiplier", 1.0))

    if signal == "LONG" and not strategy.get("allow_long", True):
        return 0.0
    if signal == "SHORT" and not strategy.get("allow_short", True):
        return 0.0

    return clamp(base, 0.0, 1.75)

def _ps_loss_streak_multiplier(streak: int) -> float:
    if streak >= POSITION_SIZING_LOSS_STREAK_CUT_2:
        return 0.35
    if streak >= POSITION_SIZING_LOSS_STREAK_CUT_1:
        return 0.60
    return 1.0


def compute_position_sizing(result: dict, stop_pct: float, state_mgr: StateManager = _STATE_MGR) -> dict:
    """Dynamic risk sizing for trade plan.

    The engine is deliberately conservative:
    - base risk starts from POSITION_SIZING_BASE_RISK_PCT
    - multipliers are capped by min/max risk
    - no data means neutral multiplier, not aggressive sizing
    """
    base_risk = float(POSITION_SIZING_BASE_RISK_PCT)
    if not POSITION_SIZING_ENABLED:
        risk_pct = clamp(base_risk, POSITION_SIZING_MIN_RISK_PCT, POSITION_SIZING_MAX_RISK_PCT)
        risk_amount = ACCOUNT_SIZE_USD * risk_pct
        return {
            "enabled": False,
            "risk_pct": risk_pct,
            "risk_amount": risk_amount,
            "position_notional": risk_amount / stop_pct if stop_pct > 0 else 0.0,
            "multiplier": 1.0,
            "mode": "STATIC",
            "loss_streak": 0,
            "reasons": ["Position sizing kapalı; sabit risk kullanıldı."],
        }

    symbol = result.get("symbol")
    group = result.get("group")
    signal = result.get("signal")
    confidence = float(result.get("confidence", 0) or 0)
    tq = result.get("trade_quality") or {}
    grade = tq.get("grade")
    regime = result.get("regime") or {}
    regime_name = regime.get("regime", "UNKNOWN")

    closed = _ps_closed_trades(state_mgr)
    loss_streak = _ps_loss_streak(closed)

    symbol_edge = _ps_edge_for(closed, symbol=symbol, direction=signal)
    group_edge = _ps_edge_for(closed, group=group, direction=signal)

    edge_multiplier = 1.0
    edge_notes = []
    if symbol_edge.get("ready"):
        edge_multiplier *= float(symbol_edge["multiplier"])
        edge_notes.append(f"Sembol edge: {symbol_edge['note']}")
    if group_edge.get("ready"):
        edge_multiplier *= (1.0 + (float(group_edge["multiplier"]) - 1.0) * 0.60)
        edge_notes.append(f"Grup edge: {group_edge['note']}")
    if not edge_notes:
        edge_notes.append("Edge verisi yetersiz; edge çarpanı nötr.")

    q_mult = _ps_quality_multiplier(grade)
    c_mult = _ps_confidence_multiplier(confidence)
    r_mult = _ps_regime_multiplier(signal, regime_name)
    ls_mult = _ps_loss_streak_multiplier(loss_streak)

    raw_multiplier = q_mult * c_mult * r_mult * edge_multiplier * ls_mult

    if regime_name == "RISK_OFF" or loss_streak >= POSITION_SIZING_LOSS_STREAK_CUT_2:
        raw_multiplier = min(raw_multiplier, 1.0)

    risk_pct = clamp(
        base_risk * raw_multiplier,
        POSITION_SIZING_MIN_RISK_PCT,
        POSITION_SIZING_MAX_RISK_PCT,
    )
    risk_amount = ACCOUNT_SIZE_USD * risk_pct
    position_notional = risk_amount / stop_pct if stop_pct > 0 else 0.0

    if risk_pct >= base_risk * 1.35:
        mode = "GROWTH"
    elif risk_pct <= base_risk * 0.70:
        mode = "DEFENSIVE"
    else:
        mode = "NORMAL"

    reasons = [
        f"Quality {grade or '-'} çarpanı x{q_mult:.2f}",
        f"Confidence %{confidence:.1f} çarpanı x{c_mult:.2f}",
        f"Regime {regime_name} çarpanı x{r_mult:.2f}",
        f"Loss streak {loss_streak} çarpanı x{ls_mult:.2f}",
        *edge_notes,
    ]

    return {
        "enabled": True,
        "mode": mode,
        "base_risk_pct": base_risk,
        "risk_pct": risk_pct,
        "risk_amount": risk_amount,
        "position_notional": position_notional,
        "multiplier": round(raw_multiplier, 3),
        "loss_streak": loss_streak,
        "quality_multiplier": q_mult,
        "confidence_multiplier": c_mult,
        "regime_multiplier": r_mult,
        "edge_multiplier": round(edge_multiplier, 3),
        "loss_streak_multiplier": ls_mult,
        "symbol_edge": symbol_edge,
        "group_edge": group_edge,
        "reasons": reasons,
    }


def format_position_sizing_brief(ps: Optional[dict]) -> str:
    if not ps:
        return "PS: yok"
    return (
        f"PS: {ps.get('mode', '-')} | "
        f"Risk %{float(ps.get('risk_pct', 0))*100:.2f} | "
        f"Çarpan x{ps.get('multiplier', 1.0)} | "
        f"LossStreak {ps.get('loss_streak', 0)}"
    )


# ============================================================
# TRADE PLAN ENGINE (bilgilendirme amaçlı, emir göndermez)
# ============================================================

def build_trade_plan(result: dict) -> Optional[dict]:
    """Sinyal varsa entry/stop/TP/pozisyon büyüklüğü planı üretir.

    Bu fonksiyon emir göndermez. Amaç: Telegram sinyalinde trader'a
    uygulanabilir risk planı sunmak.

    Pullback zone mantığı:
      - LONG: Trader spot'tan girmek yerine EMA9/EMA21 bölgesine pullback
        beklemeli. Bu yüzden zone, EMA'ların oluşturduğu aralık içinde ve
        spot'a göre AŞAĞIDA (geri çekilme) olmalı.
      - SHORT: Tam tersi — EMA bölgesi spot'a göre YUKARIDA (yukarı çekilme).
      - EMA'lar yanlış tarafta ise (örn. LONG sinyalinde EMA'lar zaten
        spot'un üstünde), spot'tan stop_pct/2 kadar uzakta synthetic zone üretilir.
    """
    signal = result.get("signal")
    if signal not in ("LONG", "SHORT"):
        return None
    tq = result.get("trade_quality")
    if tq and not tq.get("tradable", False):
        return None
    pc = result.get("portfolio_check")
    if pc and not pc.get("allowed", True):
        return None
    rc = result.get("regime_commander")
    if rc and not rc.get("allowed", True):
        return None
    ee = result.get("entry_engine")
    if ee and ee.get("status") == "BLOCKED":
        return None
    if ENTRY_ENGINE_REQUIRE_READY and ee and ee.get("status") != "READY":
        return None

    group = result["group"]
    f = result["features"]
    cfg = TRADE_PLAN_CONFIG[group]

    spot = float(f["spot_price"])
    ema9 = float(f["ema9"])
    ema21 = float(f["ema21"])
    stop_pct = float(cfg["stop_pct"])

    if spot <= 0:
        log.warning("%s build_trade_plan: spot=%s, plan üretilemiyor", result["symbol"], spot)
        return None

    # Referans entry: sinyal anındaki spot.
    reference_entry = spot

    if signal == "LONG":
        # Pullback bölgesi: EMA9 ile EMA21 arası, spot'un altında olmalı.
        ema_low = min(ema9, ema21)
        ema_high = max(ema9, ema21)
        if ema_high < spot and ema_low > 0:
            # EMA'lar spot'un altında — sağlıklı pullback bölgesi
            zone_low = ema_low
            zone_high = ema_high
        else:
            # Synthetic zone: spot'un %0.5-1.0 altı
            zone_high = spot * (1 - stop_pct / 4)
            zone_low = spot * (1 - stop_pct / 2)

        stop_price = reference_entry * (1 - stop_pct)
        risk_per_unit = reference_entry - stop_price
        tp1 = reference_entry + risk_per_unit * cfg["tp1_r"]
        tp2 = reference_entry + risk_per_unit * cfg["tp2_r"]
        tp3 = reference_entry + risk_per_unit * cfg["tp3_r"]

    else:  # SHORT
        # Pullback bölgesi: spot'un üstünde olmalı.
        ema_low = min(ema9, ema21)
        ema_high = max(ema9, ema21)
        if ema_low > spot:
            # EMA'lar spot'un üstünde — sağlıklı pullback bölgesi
            zone_low = ema_low
            zone_high = ema_high
        else:
            # Synthetic zone: spot'un %0.5-1.0 üstü
            zone_low = spot * (1 + stop_pct / 4)
            zone_high = spot * (1 + stop_pct / 2)

        stop_price = reference_entry * (1 + stop_pct)
        risk_per_unit = stop_price - reference_entry
        tp1 = reference_entry - risk_per_unit * cfg["tp1_r"]
        tp2 = reference_entry - risk_per_unit * cfg["tp2_r"]
        tp3 = reference_entry - risk_per_unit * cfg["tp3_r"]

    regime_info = result.get("regime") or {}
    tp_style = regime_info.get("tp_style") or (result.get("regime_commander", {}).get("strategy", {}) or {}).get("tp_style")
    if tp_style in ("quick_tp", "fast_tp_runner"):
        # Chop/squeeze rejimlerinde hızlı TP tercih edilir: hedefler biraz yakınlaşır.
        if signal == "LONG":
            tp1 = reference_entry + risk_per_unit * max(0.75, cfg["tp1_r"] * 0.80)
            tp2 = reference_entry + risk_per_unit * max(1.25, cfg["tp2_r"] * 0.75)
            tp3 = reference_entry + risk_per_unit * max(2.00, cfg["tp3_r"] * 0.70)
        else:
            tp1 = reference_entry - risk_per_unit * max(0.75, cfg["tp1_r"] * 0.80)
            tp2 = reference_entry - risk_per_unit * max(1.25, cfg["tp2_r"] * 0.75)
            tp3 = reference_entry - risk_per_unit * max(2.00, cfg["tp3_r"] * 0.70)
    elif tp_style in ("trend_runner", "aggressive_runner"):
        # Risk-on trend/altseason: TP3 runner daha geniş bırakılır.
        if signal == "LONG":
            tp3 = reference_entry + risk_per_unit * max(cfg["tp3_r"], 3.5)
        else:
            tp3 = reference_entry - risk_per_unit * max(cfg["tp3_r"], 3.5)

    position_sizing = compute_position_sizing(result, stop_pct)
    risk_pct = float(position_sizing["risk_pct"])
    risk_amount = float(position_sizing["risk_amount"])
    position_notional = float(position_sizing["position_notional"])
    quantity = position_notional / reference_entry if reference_entry > 0 else 0

    # R:R sanity check — trader'a görünür olsun
    rr_ratio = cfg["tp2_r"]  # tp2'yi referans alıyoruz (hedef R:R)

    return {
        "direction": signal,
        "account_size": ACCOUNT_SIZE_USD,
        "risk_pct": risk_pct,
        "risk_amount": risk_amount,
        "position_sizing": position_sizing,
        "entry_engine": copy.deepcopy(result.get("entry_engine", {})),
        "regime_commander": copy.deepcopy(result.get("regime_commander", {})),
        "reference_entry": reference_entry,
        "entry_zone_low": zone_low,
        "entry_zone_high": zone_high,
        "stop_price": stop_price,
        "stop_pct": stop_pct,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr_ratio": rr_ratio,
        "position_notional": position_notional,
        "quantity": quantity,
    }


def format_trade_plan_block(plan: Optional[dict]) -> list[str]:
    if not plan:
        return []

    return [
        "",
        "📌 Trade Plan (emir göndermez)",
        f"Yön: {plan['direction']}",
        f"Referans Entry: {format_price(plan['reference_entry'])}",
        f"Tercihli Entry Bölgesi: {format_price(plan['entry_zone_low'])} - {format_price(plan['entry_zone_high'])}",
        f"Stop: {format_price(plan['stop_price'])} (%{plan['stop_pct'] * 100:.2f})",
        f"TP1 (1R): {format_price(plan['tp1'])}",
        f"TP2 (2R): {format_price(plan['tp2'])}",
        f"TP3 / Runner (3R): {format_price(plan['tp3'])}",
        f"Hesap: {format_money(plan['account_size'])}",
        f"İşlem Riski: %{plan['risk_pct'] * 100:.2f} = {format_money(plan['risk_amount'])}",
        format_position_sizing_brief(plan.get("position_sizing")),
        f"Önerilen Notional: {format_money(plan['position_notional'])}",
        f"Yaklaşık Miktar: {plan['quantity']:.6f}",
        "Not: Sinyal geldi diye anlık piyasa emri şart değildir; entry bölgesine pullback beklemek daha sağlıklıdır.",
    ]
# ============================================================
# MESSAGE FORMATTERS
# ============================================================

def format_signal(result: dict, title: str) -> str:
    f = result["features"]
    raw = result["raw"]
    session = result["session_context"]
    news = result["news_context"]

    basis_text = (
        f"{f['basis_pct']:+.3f}%" if f["basis_pct"] is not None else "N/A"
    )
    funding_text = (
        f"{f.get('funding_rate'):+.4f}%" if f.get("funding_rate") is not None else "N/A"
    )
    futures_text = (
        format_price(f["futures_price"]) if f["futures_price"] is not None else "N/A"
    )

    lines = [
        title,
        f"{result['symbol']} → {result['signal']} / {result['level']}",
        f"Skor: {result['score']} / {result['max_score']} {bar(result['score'], result['max_score'])}",
        f"News-öncesi Skor: {result['pre_news_score']}",
        f"News Aksiyonu: {result['news_action']}",
        f"Güven: %{result['confidence']}",
        f"Seans: {session['session']}",
        f"Makro Çarpan: x{session['macro_multiplier']}",
        f"Mikro Çarpan: x{session['micro_multiplier']}",
        f"News Çarpanı: x{session['news_multiplier']}",
        f"Not: {session['note']}",
        f"Haber Kategorisi: {news['category']}",
        f"Haber Risk Skoru: {news['news_risk_score']} / 3",
        f"Haber Notu: {news['note']}",
    ]
    if result.get("blocked_signal"):
        lines.append(f"Ham Sinyal: {result['blocked_signal']} / {result.get('blocked_level', '-')}")
        lines.append("Aksiyon: kalite/portföy filtresi nedeniyle NO_TRADE")

    lines.extend([
        "",
        "Alt Skorlar:",
        f"Macro: {raw['macro']:+.2f} / 3 {bar(raw['macro'], 3)}",
        f"Market: {raw['market']:+.2f} / 2 {bar(raw['market'], 2)}",
        f"MTF: {raw['mtf']:+.2f} / 3 {bar(raw['mtf'], 3)}",
        f"Trend: {raw['trend']:+.2f} / 2 {bar(raw['trend'], 2)}",
        f"Momentum: {raw['momentum']:+.2f} / 2 {bar(raw['momentum'], 2)}",
        f"Volume: {raw['volume']:+.2f} / 2 {bar(raw['volume'], 2)}",
        f"Liquidity: {raw['liquidity']:+.2f} / 2 {bar(raw['liquidity'], 2)}",
        f"Basis: {raw['basis']:+.2f} / 2 {bar(raw['basis'], 2)}",
        f"Funding: {raw.get('funding', 0):+.2f} / 2 {bar(raw.get('funding', 0), 2)}",
        "",
        f"Spot: {format_price(f['spot_price'])}",
        f"Futures Fair: {futures_text}",
        f"Basis: {basis_text}",
        f"Funding: {funding_text}",
        f"5m: %{f['ret_5m']:.2f} | 15m: %{f['ret_15m']:.2f} | "
        f"1h: %{f['ret_1h']:.2f} | 4h: %{f['ret_4h']:.2f}",
        f"HTF: 1H %{f['ret_1h_tf']:.2f} | 4H %{f['ret_4h_tf']:.2f} | 24H %{f['ret_24h_tf']:.2f}",
        f"Hacim Oranı: {f['vol_ratio']:.2f}x | Spread: {f['spread_bps']:.2f} bps",
    ])

    if result.get("regime"):
        rg = result["regime"]
        lines.extend(["", "🧭 Rejim", f"Durum: {rg['regime']}", f"Not: {rg['note']}"])
    if result.get("trade_quality"):
        tq = result["trade_quality"]
        lines.extend(["", "⭐ Trade Quality", f"Grade: {tq['grade']} / Skor: {tq['score']}", f"Tradable: {'EVET' if tq['tradable'] else 'HAYIR'}"])
        if tq.get("reasons"):
            lines.append("Gerekçeler:")
            lines.extend(f"- {r}" for r in tq["reasons"][:5])
    if result.get("portfolio_check"):
        pc = result["portfolio_check"]
        lines.extend(["", "🧱 Portfolio Risk", f"Durum: {'UYGUN' if pc['allowed'] else 'BLOKLU'}", f"Sebep: {pc['reason']}"])

    lines.extend(format_trade_plan_block(build_trade_plan(result)))

    if result.get("basis_missing"):
        lines.append("Not: Basis verisi yok, ağırlıklar yeniden dağıtıldı.")

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
        "📊 DURUM ÖZETİ\n\n"
        f"Seans: {sd['session']}\n"
        f"Makro Çarpan: x{sd['macro_multiplier']}\n"
        f"Mikro Çarpan: x{sd['micro_multiplier']}\n"
        f"News Çarpanı: x{sd['news_multiplier']}\n"
        f"Not: {sd['note']}\n\n"
        f"Haber Kategorisi: {news_context['category']}\n"
        f"Haber Risk Skoru: {news_context['news_risk_score']} / 3\n"
        f"Haber Başlığı: {headline_line}\n\n"
        f"LONG: {', '.join(longs) if longs else '-'}\n"
        f"SHORT: {', '.join(shorts) if shorts else '-'}\n"
        f"NO_TRADE: {', '.join(neutral) if neutral else '-'}\n"
        f"Zaman: {tr_now_text()}"
    )


# ============================================================
# REGIME / QUALITY / PORTFOLIO ENGINE
# ============================================================

def detect_market_regime(btc: dict, eth: dict, news_context: dict) -> dict:
    """Market Regime Engine v2.

    Üst akıl: aynı sinyal, her rejimde aynı anlama gelmez. Bu fonksiyon
    BTC/ETH üst zaman dilimi, haber riski ve funding proxy'siyle piyasanın
    oynadığı oyunu sınıflandırır.
    """
    news_risk = float(news_context.get("news_risk_score", 0) or 0)
    category = news_context.get("category") or "NONE"
    match_count = int(news_context.get("match_count", 0) or 0)

    btc_mtf = score_mtf(btc)
    eth_mtf = score_mtf(eth)
    btc_4h = float(btc.get("ret_4h_tf", 0) or 0)
    btc_24h = float(btc.get("ret_24h_tf", 0) or 0)
    eth_24h = float(eth.get("ret_24h_tf", 0) or 0)

    btc_funding = btc.get("funding_rate")
    btc_funding = float(btc_funding) if btc_funding is not None else 0.0

    notes: list[str] = []

    if abs(news_risk) >= REGIME_CONFIG["news_chaos_abs"] or (
        category in ("WAR", "CRYPTO_RISK")
        and news_risk <= -2
        and match_count >= 2
    ):
        regime_name = "NEWS_CHAOS"
        notes.append(f"Yüksek haber şoku: {category}, risk={news_risk}.")
    elif (
        btc_funding <= -REGIME_CONFIG["squeeze_funding_abs"]
        and btc_4h > 0.4
        and btc_mtf > 0
    ):
        regime_name = "SQUEEZE_LONG"
        notes.append("Negatif funding + yukarı tepki: short squeeze ihtimali.")
    elif (
        btc_funding >= REGIME_CONFIG["squeeze_funding_abs"]
        and btc_4h < -0.4
        and btc_mtf < 0
    ):
        regime_name = "SQUEEZE_SHORT"
        notes.append("Pozitif funding + aşağı kırılım: long liquidation ihtimali.")
    elif (
        news_risk <= REGIME_CONFIG["risk_off_news"]
        or (btc_mtf < -1.4 and eth_mtf < -0.6 and btc_4h < -REGIME_CONFIG["trend_ret_4h"])
        or (btc_24h < -REGIME_CONFIG["trend_ret_24h"] and eth_24h < -REGIME_CONFIG["trend_ret_24h"])
    ):
        regime_name = "RISK_OFF_TREND_DOWN"
        notes.append("BTC/ETH zayıf veya negatif haber baskısı yüksek.")
    elif (
        btc_24h > -1.0
        and eth_24h - btc_24h >= REGIME_CONFIG["altseason_eth_outperf"]
        and eth_mtf > 0.8
        and news_risk >= -0.5
    ):
        regime_name = "RISK_ON_ALTSEASON"
        notes.append("ETH BTC'ye göre güçlü; altcoin risk iştahı proxy'si pozitif.")
    elif (
        btc_mtf > 1.4
        and eth_mtf > 0.5
        and btc_4h > REGIME_CONFIG["trend_ret_4h"]
        and btc_24h > 0
        and news_risk > -1.0
    ):
        regime_name = "RISK_ON_TREND_UP"
        notes.append("BTC/ETH üst zaman dilimlerinde yukarı trend teyitli.")
    elif abs(btc_4h) < REGIME_CONFIG["chop_ret_4h"] and abs(btc_24h) < REGIME_CONFIG["trend_ret_24h"]:
        regime_name = "CHOP_RANGE"
        notes.append("BTC yönsüz/range; fake sinyal riski yüksek.")
    else:
        regime_name = "NEUTRAL"
        notes.append("Net risk-on/risk-off yok; seçici mod.")

    strategy = get_regime_strategy(regime_name)
    score_map = {
        "NEWS_CHAOS": -3,
        "RISK_OFF_TREND_DOWN": -2,
        "CHOP_RANGE": -1,
        "NEUTRAL": 0,
        "SQUEEZE_SHORT": -1,
        "SQUEEZE_LONG": 1,
        "RISK_ON_TREND_UP": 2,
        "RISK_ON_ALTSEASON": 3,
    }

    return {
        "regime": regime_name,
        "score": score_map.get(regime_name, 0),
        "direction_bias": strategy["direction_bias"],
        "risk_multiplier": strategy["risk_multiplier"],
        "min_quality": strategy["min_quality"],
        "high_beta_allowed": strategy["high_beta_allowed"],
        "allow_long": strategy["allow_long"],
        "allow_short": strategy["allow_short"],
        "tp_style": strategy.get("tp_style", "standard"),
        "strategy_note": strategy["note"],
        "note": " ".join(notes),
        "metrics": {
            "btc_mtf": round(btc_mtf, 2),
            "eth_mtf": round(eth_mtf, 2),
            "btc_4h": round(btc_4h, 2),
            "btc_24h": round(btc_24h, 2),
            "eth_24h": round(eth_24h, 2),
            "news_risk": news_risk,
            "btc_funding": round(btc_funding, 4),
        },
    }



def grade_from_quality(score: float) -> str:
    if score >= 85:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def compute_trade_quality(result: dict, regime: dict) -> dict:
    """Sinyalin işlem yapılabilirliğini A+ / A / B / C / D olarak sınıflandırır."""
    if result.get("signal") not in ("LONG", "SHORT"):
        return {"score": 0, "grade": "D", "tradable": False, "reasons": ["LONG/SHORT sinyali yok."]}

    signal = result["signal"]
    raw = result["raw"]
    f = result["features"]
    group = result["group"]
    reasons: list[str] = []
    score = 50.0
    direction = 1 if signal == "LONG" else -1

    for key, weight in (
        ("mtf", 14),
        ("trend", 10),
        ("momentum", 10),
        ("market", 8),
        ("macro", 8),
        ("basis", 5),
        ("funding", 5),
    ):
        val = raw.get(key, 0) * direction
        if val > 0.5:
            score += weight
            reasons.append(f"{key} aynı yönde destekliyor")
        elif val < -0.5:
            score -= weight
            reasons.append(f"{key} ters yönde uyarı veriyor")

    reg = regime.get("regime", "NEUTRAL")
    strategy = get_regime_strategy(reg)

    score += float(strategy.get("quality_bonus", 0))
    reasons.append(f"Rejim: {reg} / bias: {strategy.get('direction_bias')}")

    if signal == "LONG" and strategy.get("allow_long"):
        score += 4
    elif signal == "SHORT" and strategy.get("allow_short"):
        score += 4
    else:
        score -= 25
        reasons.append("Rejim bu yönü desteklemiyor")

    if group == "HIGH_BETA" and not strategy.get("high_beta_allowed", False):
        score -= 18
        reasons.append("Rejim HIGH_BETA riskini desteklemiyor")

    if signal == "LONG":
        if group == "HIGH_BETA" and f.get("ret_1h", 0) > REGIME_CONFIG["high_vol_1h_high_beta"]:
            score -= 20
            reasons.append("HIGH_BETA 1s pump yüksek; FOMO riski")
        elif group == "CORE" and f.get("ret_1h", 0) > REGIME_CONFIG["high_vol_1h_core"]:
            score -= 15
            reasons.append("CORE 1s hareket aşırı; pullback beklemek daha sağlıklı")
    else:
        if group == "HIGH_BETA" and f.get("ret_1h", 0) < -REGIME_CONFIG["high_vol_1h_high_beta"]:
            score -= 20
            reasons.append("HIGH_BETA 1s dump yüksek; geç short riski")
        elif group == "CORE" and f.get("ret_1h", 0) < -REGIME_CONFIG["high_vol_1h_core"]:
            score -= 15
            reasons.append("CORE 1s düşüş aşırı; geç short riski")

    if f.get("vol_ratio", 1) > 3.0:
        score -= 8
        reasons.append("Aşırı hacim spike; dağıtım/squeeze sonrası geç giriş riski")

    if f.get("spread_bps", 999) > SPREAD_LIMITS[group] / 2:
        score -= 8
        reasons.append("Spread görece geniş")

    score = clamp(score, 0, 100)
    grade = grade_from_quality(score)

    global_min_ok = quality_meets_min(grade, TRADE_QUALITY_MIN_GRADE)
    regime_min_ok = quality_meets_min(grade, strategy.get("min_quality"))
    tradable = global_min_ok and regime_min_ok

    if not global_min_ok:
        reasons.append(f"Global trade quality minimum {TRADE_QUALITY_MIN_GRADE} altında: {grade}")
    if not regime_min_ok:
        reasons.append(f"Rejim minimum kalite {strategy.get('min_quality')} altında: {grade}")

    return {"score": round(score, 1), "grade": grade, "tradable": tradable, "reasons": reasons[:10]}



def evaluate_entry_engine(result: dict) -> dict:
    """Entry Engine v1.

    READY: mevcut fiyatla takip edilebilir.
    WAIT_PULLBACK: sinyal var ama fiyat aşırı uzamış; pullback/retest bekle.
    BLOCKED: likidite, rejim veya kalite nedeniyle giriş uygun değil.
    """
    if not ENTRY_ENGINE_ENABLED:
        return {"status": "READY", "reason": "Entry engine kapalı; doğrudan hazır kabul edildi.", "checks": []}

    if result.get("signal") not in ("LONG", "SHORT"):
        return {"status": "BLOCKED", "reason": "LONG/SHORT sinyali yok.", "checks": []}

    signal = result["signal"]
    f = result["features"]
    group = result["group"]
    regime = result.get("regime") or {}
    checks: list[str] = []

    if regime.get("regime") == "NEWS_CHAOS":
        return {"status": "BLOCKED", "reason": "NEWS_CHAOS rejiminde yeni giriş kapalı.", "checks": checks}

    if f.get("spread_bps", 999) > SPREAD_LIMITS[group]:
        return {"status": "BLOCKED", "reason": "Spread limitin üstünde.", "checks": checks}

    ret_1h = float(f.get("ret_1h", 0) or 0)
    vol_ratio = float(f.get("vol_ratio", 1) or 1)
    raw = result.get("raw", {})
    direction = 1 if signal == "LONG" else -1

    if signal == "LONG":
        if group == "HIGH_BETA" and ret_1h > REGIME_CONFIG["high_vol_1h_high_beta"]:
            return {"status": "WAIT_PULLBACK", "reason": "HIGH_BETA long için 1s pump yüksek; pullback bekle.", "checks": checks}
        if group == "CORE" and ret_1h > REGIME_CONFIG["high_vol_1h_core"]:
            return {"status": "WAIT_PULLBACK", "reason": "CORE long için fiyat uzamış; pullback bekle.", "checks": checks}
    else:
        if group == "HIGH_BETA" and ret_1h < -REGIME_CONFIG["high_vol_1h_high_beta"]:
            return {"status": "WAIT_PULLBACK", "reason": "HIGH_BETA short için dump sonrası geç giriş riski; retest bekle.", "checks": checks}
        if group == "CORE" and ret_1h < -REGIME_CONFIG["high_vol_1h_core"]:
            return {"status": "WAIT_PULLBACK", "reason": "CORE short için düşüş uzamış; retest bekle.", "checks": checks}

    if vol_ratio > 4.0:
        return {"status": "WAIT_PULLBACK", "reason": "Aşırı hacim spike; squeeze/dağıtım sonrası teyit bekle.", "checks": checks}

    mtf_ok = raw.get("mtf", 0) * direction > 0
    trend_ok = raw.get("trend", 0) * direction > -0.2
    momentum_ok = raw.get("momentum", 0) * direction > -0.2

    checks.extend([
        f"MTF {'OK' if mtf_ok else 'zayıf'}",
        f"Trend {'OK' if trend_ok else 'zayıf'}",
        f"Momentum {'OK' if momentum_ok else 'zayıf'}",
    ])

    if not mtf_ok:
        return {"status": "WAIT_PULLBACK", "reason": "Üst zaman dilimi sinyali tam desteklemiyor; teyit bekle.", "checks": checks}

    if not trend_ok and not momentum_ok:
        return {"status": "WAIT_PULLBACK", "reason": "Trend ve momentum aynı anda zayıf; giriş bekletildi.", "checks": checks}

    return {"status": "READY", "reason": "Rejim, MTF ve mikro sinyal giriş için yeterli.", "checks": checks}


def regime_commander_decision(result: dict, regime: dict) -> dict:
    """Regime Commander: direction/risk/quality/high-beta kurallarını tek noktada uygular."""
    if not REGIME_COMMANDER_ENABLED:
        return {"allowed": True, "reason": "Regime Commander kapalı.", "strategy": get_regime_strategy("NEUTRAL")}

    strategy = get_regime_strategy(regime.get("regime", "NEUTRAL"))
    signal = result.get("signal")
    group = result.get("group")

    if signal not in ("LONG", "SHORT"):
        return {"allowed": True, "reason": "İşlem sinyali yok.", "strategy": strategy}

    if float(strategy.get("risk_multiplier", 1.0)) <= 0:
        return {"allowed": False, "reason": "Regime Commander: bu rejimde yeni trade kapalı.", "strategy": strategy}

    if signal == "LONG" and not strategy.get("allow_long", True):
        return {"allowed": False, "reason": f"Regime Commander: {regime.get('regime')} LONG yönünü kapatıyor.", "strategy": strategy}

    if signal == "SHORT" and not strategy.get("allow_short", True):
        return {"allowed": False, "reason": f"Regime Commander: {regime.get('regime')} SHORT yönünü kapatıyor.", "strategy": strategy}

    if group == "HIGH_BETA" and not strategy.get("high_beta_allowed", False):
        return {"allowed": False, "reason": f"Regime Commander: {regime.get('regime')} HIGH_BETA riskini kapatıyor.", "strategy": strategy}

    tq = result.get("trade_quality") or {}
    if tq and not quality_meets_min(tq.get("grade"), strategy.get("min_quality")):
        return {"allowed": False, "reason": f"Regime Commander: minimum kalite {strategy.get('min_quality')} gerekir.", "strategy": strategy}

    return {"allowed": True, "reason": strategy.get("note", "Rejim uygun."), "strategy": strategy}


def portfolio_risk_check(result: dict, state_mgr: StateManager) -> dict:
    """Aynı anda çok fazla benzer sinyal riskini sınırlayan basit portföy filtresi."""
    if result.get("signal") not in ("LONG", "SHORT"):
        return {"allowed": True, "reason": "Sinyal yok."}
    snapshot = state_mgr.snapshot()
    symbols = snapshot.get("symbols", {})
    trades = snapshot.get("trades", {})
    exposures: dict[str, dict] = {}

    for sym, v in symbols.items():
        if (
            sym != result["symbol"]
            and v.get("signal") in ("LONG", "SHORT")
            and v.get("actionable", True)
        ):
            exposures[sym] = {"symbol": sym, "signal": v["signal"], "group": COINS.get(sym)}

    for t in trades.values():
        sym = t.get("symbol")
        if not sym or sym == result["symbol"] or t.get("result") is not None:
            continue
        direction = t.get("direction")
        if direction in ("LONG", "SHORT"):
            exposures[sym] = {"symbol": sym, "signal": direction, "group": COINS.get(sym)}

    active = list(exposures.values())
    same_dir = [v for v in active if v.get("signal") == result["signal"]]
    high_beta_active = sum(1 for v in active if v.get("group") == "HIGH_BETA")
    if len(active) >= PORTFOLIO_LIMITS["max_active_signals"]:
        return {"allowed": False, "reason": "Maksimum aktif sinyal limiti dolu."}
    if len(same_dir) >= PORTFOLIO_LIMITS["max_same_direction"]:
        return {"allowed": False, "reason": "Aynı yönde aktif sinyal limiti dolu."}
    if result["group"] == "HIGH_BETA" and high_beta_active >= PORTFOLIO_LIMITS["max_high_beta_active"]:
        return {"allowed": False, "reason": "HIGH_BETA aktif sinyal limiti dolu."}
    return {"allowed": True, "reason": "Portföy riski uygun."}


def apply_trade_filters(result: dict) -> dict:
    """Regime Commander + Quality + Entry + Portfolio filtrelerini aksiyon alınabilir sinyale uygular."""
    result["raw_signal"] = result.get("signal")
    result["actionable"] = result.get("signal") in ("LONG", "SHORT")
    if result.get("signal") not in ("LONG", "SHORT"):
        return result

    tq = result.get("trade_quality") or {}
    pc = result.get("portfolio_check") or {}
    rc = result.get("regime_commander") or {}
    ee = result.get("entry_engine") or {}

    veto_reason = None
    if rc and not rc.get("allowed", True):
        veto_reason = rc.get("reason", "Regime Commander blokladı.")
    elif tq and not tq.get("tradable", True):
        veto_reason = f"Trade quality filtresi: {tq.get('grade', '-')}"
    elif ee and ee.get("status") == "BLOCKED":
        veto_reason = f"Entry engine: {ee.get('reason', '-')}"
    elif ENTRY_ENGINE_REQUIRE_READY and ee and ee.get("status") == "WAIT_PULLBACK":
        veto_reason = f"Entry bekleniyor: {ee.get('reason', '-')}"
    elif pc and not pc.get("allowed", True):
        veto_reason = f"Portföy riski: {pc.get('reason', '-')}"

    if veto_reason:
        result["blocked_signal"] = result["signal"]
        result["blocked_level"] = result["level"]
        result["signal"] = "NO_TRADE"
        result["level"] = "WEAK"
        result["confidence"] = 0.0
        result["actionable"] = False
        result["veto"] = result.get("veto") or veto_reason

    return result


# ============================================================
# ALERT LOGIC
# ============================================================

def should_notify_signal(result: dict) -> bool:
    if result["signal"] == "NO_TRADE":
        return False
    if not result.get("actionable", True):
        return False
    return LEVEL_ORDER[result["level"]] >= LEVEL_ORDER[MIN_SIGNAL_LEVEL]


def decide_alert(old: Optional[dict], new: dict) -> Optional[str]:
    """Hangi tipte alert gönderileceğine karar verir. None = alert yok."""
    # News veto: aktif pozisyonu olan kullanıcıya kritik bilgi
    if (
        new.get("news_action") == "news_veto"
        and old
        and old.get("signal") in ("LONG", "SHORT")
    ):
        return "🛑 NEWS VETO"

    if not should_notify_signal(new):
        if (
            old
            and old.get("signal") in ("LONG", "SHORT")
            and new["signal"] == "NO_TRADE"
        ):
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
    if (
        old_signal != new_signal
        and old_signal != "NO_TRADE"
        and new_signal != "NO_TRADE"
    ):
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
    """Cooldown kararı.

    Kritik alertler (NEWS VETO, SİNYAL İPTAL, YÖN DEĞİŞTİ) cooldown'a takılmaz —
    kullanıcının ANLIK bilmesi gereken durumlar bunlar.
    """
    # Kritik alert ise cooldown bypass
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
    """Sinyal oluşmasa bile olağan dışı coin hareketlerini yakalar."""
    group = COINS[symbol]
    f = result["features"]
    thresholds = MOVEMENT_ALERT_THRESHOLDS[group]

    reasons = []
    if abs(f["ret_15m"]) >= thresholds["ret_15m"]:
        reasons.append(f"15dk değişim: %{f['ret_15m']:+.2f}")
    if abs(f["ret_1h"]) >= thresholds["ret_1h"]:
        reasons.append(f"1s değişim: %{f['ret_1h']:+.2f}")
    if f["vol_ratio"] >= thresholds["volume_ratio"]:
        reasons.append(f"Hacim oranı: {f['vol_ratio']:.2f}x")

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
        f"Hacim Oranı: {f['vol_ratio']:.2f}x\n"
        f"Spread: {f['spread_bps']:.2f} bps\n\n"
        f"Not: Bu bir LONG/SHORT sinyali değildir. "
        f"Bot sadece olağan dışı coin hareketi tespit etti.\n"
        f"Zaman: {tr_now_text()}"
    )


def should_movement_alert_cooldown(state_mgr: StateManager, symbol: str) -> bool:
    last_ts = state_mgr.get_meta(f"last_movement_alert_ts_{symbol}", 0)
    return now_ts() - last_ts < MOVEMENT_ALERT_COOLDOWN_SECONDS


def mark_movement_alert_sent(state_mgr: StateManager, symbol: str) -> None:
    state_mgr.set_meta(f"last_movement_alert_ts_{symbol}", now_ts())



# ============================================================
# TRADE TRACKING + EDGE ANALYZER
# ============================================================

def get_trades(state_mgr: StateManager = _STATE_MGR) -> dict:
    return state_mgr.get_trades()


def save_trades(trades: dict, state_mgr: StateManager = _STATE_MGR) -> None:
    state_mgr.update_trades(trades)
    state_mgr.save()


def active_trade_exists(symbol: str, state_mgr: StateManager = _STATE_MGR) -> bool:
    for t in get_trades(state_mgr).values():
        if t.get("symbol") == symbol and t.get("result") is None:
            return True
    return False


def _trade_id(symbol: str) -> str:
    return f"{symbol}_{now_ts()}_{random.randint(1000, 9999)}"


def can_open_new_trade(result: dict, state_mgr: StateManager) -> tuple[bool, str]:
    if not TRADE_TRACKING_ENABLED:
        return False, "Trade tracking disabled."
    if result.get("signal") not in ("LONG", "SHORT"):
        return False, "LONG/SHORT sinyali yok."
    if not result.get("actionable", True):
        return False, "Sinyal actionable değil."
    if active_trade_exists(result["symbol"], state_mgr):
        return False, "Bu sembolde açık trade var."

    last_key = f"last_trade_open_ts_{result['symbol']}"
    last_open = int(state_mgr.get_meta(last_key, 0) or 0)
    if TRADE_OPEN_COOLDOWN_SECONDS > 0 and now_ts() - last_open < TRADE_OPEN_COOLDOWN_SECONDS:
        remain = TRADE_OPEN_COOLDOWN_SECONDS - (now_ts() - last_open)
        return False, f"Trade open cooldown aktif ({remain//60} dk)."

    return True, "Trade açılabilir."


def format_trade_open_msg(t: dict) -> str:
    tq = t.get("trade_quality") or {}
    regime = t.get("regime") or {}
    return (
        "🚀 TRADE OPENED\n\n"
        f"{t['symbol']} → {t['direction']}\n\n"
        f"Entry: {format_price(t['entry'])}\n"
        f"Entry Zone: {format_price(t.get('entry_zone_low'))} - {format_price(t.get('entry_zone_high'))}\n"
        f"Stop: {format_price(t['stop'])}\n"
        f"TP1: {format_price(t['tp1'])}\n"
        f"TP2: {format_price(t['tp2'])}\n"
        f"TP3: {format_price(t['tp3'])}\n\n"
        f"Score: {t.get('score')}\n"
        f"Confidence: %{t.get('confidence', 0)}\n"
        f"Quality: {tq.get('grade', '-')}, score {tq.get('score', '-')}\n"
        f"Regime: {regime.get('regime', '-')}\n"
        f"Bias: {regime.get('direction_bias', '-')}, Risk x{regime.get('risk_multiplier', '-')}\n"
        f"Entry: {(t.get('entry_engine') or {}).get('status', '-')} — {(t.get('entry_engine') or {}).get('reason', '-')}\n"
        f"Risk: {format_money(t.get('risk_amount'))} (%{float(t.get('risk_pct', 0))*100:.2f}) | Notional: {format_money(t.get('position_notional'))}\\n"
        f"{format_position_sizing_brief(t.get('position_sizing'))}\\n\\n"
        f"Not: Bu mesaj otomatik emir değildir; botun trade tracking kaydıdır.\n"
        f"Zaman: {tr_now_text()}"
    )


def format_trade_close_msg(t: dict) -> str:
    duration = 0
    if t.get("closed_at") and t.get("opened_at"):
        duration = (int(t["closed_at"]) - int(t["opened_at"])) // 60

    return (
        "🏁 TRADE CLOSED\n\n"
        f"{t['symbol']} → {t['direction']}\n"
        f"Result: {t['result']}\n"
        f"Close Reason: {t.get('close_reason', '-')}\n"
        f"Exit Price: {format_price(t.get('exit_price'))}\n\n"
        f"PnL: %{t.get('pnl_pct', 0) * 100:.2f}\n"
        f"Max Favorable: %{t.get('max_favor', 0) * 100:.2f}\n"
        f"Max Adverse: %{t.get('max_adverse', 0) * 100:.2f}\n\n"
        f"Süre: {duration} dk\n"
        f"Zaman: {tr_now_text()}"
    )


def _mark_trade_notification(
    t: dict,
    *,
    kind: str,
    sent: bool,
) -> None:
    now = now_ts()
    t["last_notify_attempt_ts"] = now
    if kind == "open":
        t["open_alert_sent"] = bool(sent)
        t["open_alert_pending"] = not sent
    else:
        t["close_alert_sent"] = bool(sent)
        t["close_alert_pending"] = not sent


def _send_trade_notification(t: dict, kind: str) -> bool:
    if kind == "open":
        sent = send_message(format_trade_open_msg(t))
    else:
        sent = send_message(format_trade_close_msg(t))
    _mark_trade_notification(t, kind=kind, sent=sent)
    return sent


def open_trade(result: dict, plan: dict, state_mgr: StateManager = _STATE_MGR) -> bool:
    allowed, reason = can_open_new_trade(result, state_mgr)
    if not allowed:
        log.info("%s trade açılmadı: %s", result.get("symbol"), reason)
        return False

    trade_id = _trade_id(result["symbol"])
    trades = get_trades(state_mgr)

    trade = {
        "id": trade_id,
        "symbol": result["symbol"],
        "group": result.get("group", COINS.get(result.get("symbol"), "UNKNOWN")),
        "direction": result["signal"],
        "entry": float(plan["reference_entry"]),
        "entry_zone_low": float(plan.get("entry_zone_low", plan["reference_entry"])),
        "entry_zone_high": float(plan.get("entry_zone_high", plan["reference_entry"])),
        "stop": float(plan["stop_price"]),
        "tp1": float(plan["tp1"]),
        "tp2": float(plan["tp2"]),
        "tp3": float(plan["tp3"]),
        "risk_pct": float(plan.get("risk_pct", 0)),
        "risk_amount": float(plan.get("risk_amount", 0)),
        "position_notional": float(plan.get("position_notional", 0)),
        "quantity": float(plan.get("quantity", 0)),
        "position_sizing": copy.deepcopy(plan.get("position_sizing", {})),
        "entry_engine": copy.deepcopy(plan.get("entry_engine", {})),
        "regime_commander": copy.deepcopy(plan.get("regime_commander", {})),
        "opened_at": now_ts(),
        "closed_at": None,
        "result": None,
        "close_reason": None,
        "exit_price": None,
        "pnl_pct": 0.0,
        "max_favor": 0.0,
        "max_adverse": 0.0,
        "score": result.get("score"),
        "confidence": result.get("confidence"),
        "level": result.get("level"),
        "trade_quality": copy.deepcopy(result.get("trade_quality", {})),
        "regime": copy.deepcopy(result.get("regime", {})),
        "session": copy.deepcopy(result.get("session_context", {})),
        "news_context": copy.deepcopy(result.get("news_context", {})),
        "raw": copy.deepcopy(result.get("raw", {})),
        "open_alert_sent": False,
        "open_alert_pending": True,
        "close_alert_sent": False,
        "close_alert_pending": False,
        "last_notify_attempt_ts": 0,
    }

    sent = _send_trade_notification(trade, "open")
    trades[trade_id] = trade
    state_mgr.set_meta(f"last_trade_open_ts_{result['symbol']}", now_ts())
    save_trades(trades, state_mgr)

    return sent


def _trade_pnl(direction: str, entry: float, price: float) -> float:
    if entry <= 0:
        return 0.0
    if direction == "LONG":
        return (price - entry) / entry
    return (entry - price) / entry


def close_trade(trades: dict, tid: str, t: dict, price: float, result: str, reason: str) -> None:
    t["result"] = result
    t["close_reason"] = reason
    t["closed_at"] = now_ts()
    t["exit_price"] = float(price)
    t["pnl_pct"] = _trade_pnl(t["direction"], float(t["entry"]), float(price))
    t["open_alert_pending"] = False
    _send_trade_notification(t, "close")
    trades[tid] = t


def update_trades(results_by_symbol: dict, state_mgr: StateManager = _STATE_MGR) -> None:
    """TP2/STOP ve MFE/MAE tracking. TP2 = WIN, STOP = LOSS."""
    if not TRADE_TRACKING_ENABLED:
        return

    trades = get_trades(state_mgr)
    changed = False

    for tid, t in list(trades.items()):
        if t.get("result") is not None:
            continue

        symbol = t.get("symbol")
        result = results_by_symbol.get(symbol)
        if not result:
            continue

        price = float(result["features"]["last"])
        entry = float(t["entry"])
        direction = t["direction"]
        pnl = _trade_pnl(direction, entry, price)

        t["max_favor"] = max(float(t.get("max_favor", 0)), pnl)
        t["max_adverse"] = min(float(t.get("max_adverse", 0)), pnl)

        if (direction == "LONG" and price <= float(t["stop"])) or (
            direction == "SHORT" and price >= float(t["stop"])
        ):
            close_trade(trades, tid, t, price, "LOSS", "STOP")
            changed = True
            continue

        if (direction == "LONG" and price >= float(t["tp2"])) or (
            direction == "SHORT" and price <= float(t["tp2"])
        ):
            close_trade(trades, tid, t, price, "WIN", "TP2")
            changed = True
            continue

        trades[tid] = t
        changed = True

    if changed:
        save_trades(trades, state_mgr)


def close_trades_on_signal_change(symbol: str, result: dict, state_mgr: StateManager = _STATE_MGR) -> None:
    """Sinyal yönü bozulursa açık trade'i EXIT_SIGNAL ile kapat."""
    if not TRADE_TRACKING_ENABLED:
        return
    trades = get_trades(state_mgr)
    changed = False
    current_signal = result.get("raw_signal", result.get("signal"))
    price = float(result["features"]["last"])

    for tid, t in list(trades.items()):
        if t.get("result") is not None or t.get("symbol") != symbol:
            continue
        if current_signal != t.get("direction"):
            close_trade(trades, tid, t, price, "EXIT", "SIGNAL_CHANGED_OR_CANCELLED")
            changed = True

    if changed:
        save_trades(trades, state_mgr)


def close_trades_on_signal_changes(results_by_symbol: dict, state_mgr: StateManager = _STATE_MGR) -> None:
    for symbol, result in results_by_symbol.items():
        close_trades_on_signal_change(symbol, result, state_mgr)


def retry_pending_trade_alerts(state_mgr: StateManager = _STATE_MGR) -> None:
    if not TRADE_TRACKING_ENABLED:
        return
    trades = get_trades(state_mgr)
    changed = False
    now = now_ts()

    for tid, t in list(trades.items()):
        last_attempt = int(t.get("last_notify_attempt_ts", 0) or 0)
        if now - last_attempt < TRADE_ALERT_RETRY_SECONDS:
            continue

        if t.get("result") is None and t.get("open_alert_pending"):
            _send_trade_notification(t, "open")
            trades[tid] = t
            changed = True
        elif t.get("result") is not None and t.get("close_alert_pending"):
            _send_trade_notification(t, "close")
            trades[tid] = t
            changed = True

    if changed:
        save_trades(trades, state_mgr)


def edge_analysis(state_mgr: StateManager = _STATE_MGR) -> dict:
    trades = list(get_trades(state_mgr).values())
    closed = [t for t in trades if t.get("result") is not None]
    if not closed:
        return {"ready": False, "summary": "Henüz kapanmış trade yok."}

    wins = [t for t in closed if t.get("result") == "WIN"]
    losses = [t for t in closed if t.get("result") == "LOSS"]
    winrate = len(wins) / len(closed) * 100 if closed else 0.0
    pnl_values = [float(t.get("pnl_pct", 0)) for t in closed]
    avg_pnl = sum(pnl_values) / len(pnl_values) * 100
    win_pnls = [float(t.get("pnl_pct", 0)) for t in wins]
    loss_pnls = [float(t.get("pnl_pct", 0)) for t in losses]
    avg_win = sum(win_pnls) / len(win_pnls) * 100 if win_pnls else 0.0
    avg_loss = sum(loss_pnls) / len(loss_pnls) * 100 if loss_pnls else 0.0
    gross_win = sum(max(p, 0) for p in pnl_values)
    gross_loss = abs(sum(min(p, 0) for p in pnl_values))
    profit_factor = gross_win / gross_loss if gross_loss > 0 else None

    by_symbol: dict[str, dict] = {}
    by_direction: dict[str, dict] = {}
    by_regime: dict[str, dict] = {}
    by_quality: dict[str, dict] = {}

    def bucket_update(bucket: dict, key: str, trade: dict) -> None:
        item = bucket.setdefault(key, {"total": 0, "wins": 0, "pnl_sum": 0.0})
        item["total"] += 1
        item["wins"] += 1 if trade.get("result") == "WIN" else 0
        item["pnl_sum"] += float(trade.get("pnl_pct", 0)) * 100

    for t in closed:
        bucket_update(by_symbol, t.get("symbol", "?"), t)
        bucket_update(by_direction, t.get("direction", "?"), t)
        reg = (t.get("regime") or {}).get("regime", "UNKNOWN")
        bucket_update(by_regime, reg, t)
        grade = (t.get("trade_quality") or {}).get("grade", "UNKNOWN")
        bucket_update(by_quality, grade, t)

    def compact(bucket: dict) -> dict:
        out = {}
        for k, v in bucket.items():
            out[k] = {
                "total": v["total"],
                "winrate": round(v["wins"] / v["total"] * 100, 1) if v["total"] else 0,
                "avg_pnl": round(v["pnl_sum"] / v["total"], 2) if v["total"] else 0,
            }
        return out

    return {
        "ready": len(closed) >= 5,
        "closed_trades": len(closed),
        "open_trades": len([t for t in trades if t.get("result") is None]),
        "wins": len(wins),
        "losses": len(losses),
        "winrate": round(winrate, 1),
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(avg_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor is not None else None,
        "by_symbol": compact(by_symbol),
        "by_direction": compact(by_direction),
        "by_regime": compact(by_regime),
        "by_quality": compact(by_quality),
    }



def _fi_trade_outcome(t: dict) -> Optional[int]:
    """1 = profitable/winning, 0 = losing. EXIT trades use pnl sign."""
    if t.get("result") is None:
        return None
    result = t.get("result")
    pnl = float(t.get("pnl_pct", 0) or 0)
    if result == "WIN":
        return 1
    if result == "LOSS":
        return 0
    return 1 if pnl > 0 else 0


def _fi_aligned_raw_value(t: dict, component: str) -> Optional[float]:
    """Raw score aligned to trade direction. Positive supports the trade direction."""
    raw = t.get("raw") or {}
    if component not in raw:
        return None
    try:
        value = float(raw[component])
    except (TypeError, ValueError):
        return None
    direction = t.get("direction")
    if direction == "SHORT":
        value = -value
    elif direction != "LONG":
        return None
    return value


def _fi_bucket_stats(trades: list[dict], components: tuple[str, ...]) -> dict:
    stats: dict[str, dict] = {}
    for comp in components:
        rows = []
        for t in trades:
            outcome = _fi_trade_outcome(t)
            aligned = _fi_aligned_raw_value(t, comp)
            if outcome is None or aligned is None:
                continue
            pnl = float(t.get("pnl_pct", 0) or 0) * 100
            rows.append({"aligned": aligned, "win": outcome, "pnl": pnl})
        if not rows:
            continue
        wins = [r for r in rows if r["win"] == 1]
        losses = [r for r in rows if r["win"] == 0]
        pos = [r for r in rows if r["aligned"] > 0]
        neg = [r for r in rows if r["aligned"] <= 0]
        avg_aligned_wins = sum(r["aligned"] for r in wins) / len(wins) if wins else 0.0
        avg_aligned_losses = sum(r["aligned"] for r in losses) / len(losses) if losses else 0.0
        pos_wr = sum(r["win"] for r in pos) / len(pos) * 100 if pos else None
        neg_wr = sum(r["win"] for r in neg) / len(neg) * 100 if neg else None
        avg_pnl_pos = sum(r["pnl"] for r in pos) / len(pos) if pos else None
        avg_pnl_neg = sum(r["pnl"] for r in neg) / len(neg) if neg else None
        separation = avg_aligned_wins - avg_aligned_losses
        wr_edge = 0.0
        if pos_wr is not None and neg_wr is not None:
            wr_edge = (pos_wr - neg_wr) / 100.0
        pnl_edge = 0.0
        if avg_pnl_pos is not None and avg_pnl_neg is not None:
            pnl_edge = (avg_pnl_pos - avg_pnl_neg) / 10.0
        importance_score = round(0.55 * separation + 0.30 * wr_edge + 0.15 * pnl_edge, 4)
        if len(rows) < FEATURE_IMPORTANCE_MIN_BUCKET_TRADES:
            recommendation = "INSUFFICIENT_BUCKET_DATA"
        elif importance_score >= 0.15:
            recommendation = "CONSIDER_WEIGHT_UP"
        elif importance_score <= -0.15:
            recommendation = "CONSIDER_WEIGHT_DOWN"
        else:
            recommendation = "KEEP"
        stats[comp] = {
            "sample": len(rows),
            "winrate_when_supports_direction": round(pos_wr, 1) if pos_wr is not None else None,
            "winrate_when_against_direction": round(neg_wr, 1) if neg_wr is not None else None,
            "avg_pnl_when_supports_direction": round(avg_pnl_pos, 2) if avg_pnl_pos is not None else None,
            "avg_pnl_when_against_direction": round(avg_pnl_neg, 2) if avg_pnl_neg is not None else None,
            "avg_aligned_raw_winners": round(avg_aligned_wins, 3),
            "avg_aligned_raw_losers": round(avg_aligned_losses, 3),
            "separation": round(separation, 3),
            "importance_score": importance_score,
            "recommendation": recommendation,
        }
    return stats


def feature_importance_analysis(state_mgr: StateManager = _STATE_MGR) -> dict:
    """Analyze which raw score components separate winning and losing trades.

    This is a reporting layer only. It does NOT change weights.
    """
    if not FEATURE_IMPORTANCE_ENABLED:
        return {"ready": False, "enabled": False, "summary": "Feature Importance kapalı."}
    trades = list(get_trades(state_mgr).values())
    closed = [t for t in trades if _fi_trade_outcome(t) is not None]
    if len(closed) < FEATURE_IMPORTANCE_MIN_TRADES:
        return {
            "ready": False,
            "enabled": True,
            "closed_trades": len(closed),
            "min_required": FEATURE_IMPORTANCE_MIN_TRADES,
            "summary": f"Feature Importance için veri yetersiz ({len(closed)}/{FEATURE_IMPORTANCE_MIN_TRADES}).",
        }
    overall = _fi_bucket_stats(closed, FEATURE_IMPORTANCE_COMPONENTS)
    by_group: dict[str, dict] = {}
    groups = sorted({t.get("group") or COINS.get(t.get("symbol", ""), "UNKNOWN") for t in closed})
    for group in groups:
        subset = [t for t in closed if (t.get("group") or COINS.get(t.get("symbol", ""), "UNKNOWN")) == group]
        if len(subset) >= FEATURE_IMPORTANCE_MIN_BUCKET_TRADES:
            by_group[group] = _fi_bucket_stats(subset, FEATURE_IMPORTANCE_COMPONENTS)
    ranked = sorted(
        ((comp, data) for comp, data in overall.items() if data.get("sample", 0) >= FEATURE_IMPORTANCE_MIN_BUCKET_TRADES),
        key=lambda item: item[1].get("importance_score", 0),
        reverse=True,
    )
    top_positive = ranked[:3]
    top_negative = list(reversed(ranked[-3:])) if ranked else []
    suggestions = []
    for comp, data in ranked:
        rec = data.get("recommendation")
        if rec in ("CONSIDER_WEIGHT_UP", "CONSIDER_WEIGHT_DOWN"):
            suggestions.append({
                "component": comp,
                "action": rec,
                "importance_score": data["importance_score"],
                "sample": data["sample"],
                "reason": (
                    f"support WR={data.get('winrate_when_supports_direction')}%, "
                    f"against WR={data.get('winrate_when_against_direction')}%, "
                    f"separation={data.get('separation')}"
                ),
            })
    return {
        "ready": True,
        "enabled": True,
        "closed_trades": len(closed),
        "components": overall,
        "by_group": by_group,
        "top_positive": [{"component": c, **d} for c, d in top_positive],
        "top_negative": [{"component": c, **d} for c, d in top_negative],
        "suggestions": suggestions[:5],
    }


def format_feature_importance_brief(state_mgr: StateManager = _STATE_MGR) -> str:
    fi = feature_importance_analysis(state_mgr)
    if not fi.get("ready"):
        return fi.get("summary", "Feature Importance: veri yetersiz.")
    top = fi.get("top_positive") or []
    weak = fi.get("top_negative") or []
    top_text = ", ".join(f"{x['component']} {x['importance_score']:+.2f}" for x in top[:2]) if top else "-"
    weak_text = ", ".join(f"{x['component']} {x['importance_score']:+.2f}" for x in weak[:2]) if weak else "-"
    return f"FI: güçlü [{top_text}] | zayıf [{weak_text}]"


def format_feature_importance_report(state_mgr: StateManager = _STATE_MGR) -> str:
    fi = feature_importance_analysis(state_mgr)
    if not fi.get("ready"):
        return "🧠 FEATURE IMPORTANCE\n\n" + fi.get("summary", "Veri yetersiz.")
    lines = [
        "🧠 FEATURE IMPORTANCE REPORT",
        "",
        f"Kapalı trade: {fi['closed_trades']}",
        "",
        "En güçlü katkılar:",
    ]
    for item in fi.get("top_positive", [])[:5]:
        lines.append(
            f"- {item['component']}: score {item['importance_score']:+.3f} | "
            f"support WR %{item.get('winrate_when_supports_direction')} | "
            f"against WR %{item.get('winrate_when_against_direction')}"
        )
    lines.append("")
    lines.append("Zayıf / yanıltıcı katkılar:")
    for item in fi.get("top_negative", [])[:5]:
        lines.append(
            f"- {item['component']}: score {item['importance_score']:+.3f} | "
            f"support WR %{item.get('winrate_when_supports_direction')} | "
            f"against WR %{item.get('winrate_when_against_direction')}"
        )
    suggestions = fi.get("suggestions", [])
    if suggestions:
        lines.append("")
        lines.append("Öneri adayları (otomatik uygulanmaz):")
        for s in suggestions[:5]:
            action = "weight ↑" if s["action"] == "CONSIDER_WEIGHT_UP" else "weight ↓"
            lines.append(f"- {s['component']}: {action} | {s['reason']}")
    lines.append("")
    lines.append("Not: Bu modül sadece analiz yapar; parametreleri değiştirmez.")
    return "\n".join(lines)

def format_edge_brief(state_mgr: StateManager = _STATE_MGR) -> str:
    ea = edge_analysis(state_mgr)
    if not ea.get("closed_trades"):
        return "Edge: kapanmış trade yok."

    pf = ea.get("profit_factor")
    pf_text = f"{pf}" if pf is not None else "∞"
    return (
        f"Edge: {ea['closed_trades']} kapalı / {ea['open_trades']} açık | "
        f"WR %{ea['winrate']} | Exp %{ea['expectancy']} | PF {pf_text}"
    )

# ============================================================
# BOT LOOP — modülerleştirilmiş
# ============================================================



# ============================================================
# WEIGHT LEARNING ENGINE v1 (analysis + suggestion only)
# ============================================================

def _wl_clamp_weight(value: float) -> float:
    return clamp(float(value), WEIGHT_LEARNING_MIN_WEIGHT, WEIGHT_LEARNING_MAX_WEIGHT)


def _wl_normalize_weights(weights: dict[str, float], target_sum: float = 1.0) -> dict[str, float]:
    total = sum(max(0.0, float(v)) for v in weights.values())
    if total <= 0:
        return dict(weights)
    return {k: round(float(v) / total * target_sum, 5) for k, v in weights.items()}


def _wl_group_feature_stats(fi: dict, group: str) -> dict:
    """Return group-specific FI stats if available, otherwise overall stats."""
    by_group = fi.get("by_group") or {}
    if group in by_group and by_group[group]:
        return by_group[group]
    return fi.get("components") or {}


def weight_learning_analysis(state_mgr: StateManager = _STATE_MGR) -> dict:
    """Generate conservative weight-change suggestions from Feature Importance.

    This module DOES NOT apply changes. It only proposes new weights that can later
    be routed to a Telegram approval / adaptive_config flow.
    """
    if not WEIGHT_LEARNING_ENABLED:
        return {"ready": False, "enabled": False, "summary": "Weight Learning kapalı."}

    fi = feature_importance_analysis(state_mgr)
    if not fi.get("ready"):
        return {
            "ready": False,
            "enabled": True,
            "summary": "Weight Learning için Feature Importance hazır değil: " + fi.get("summary", "veri yetersiz."),
        }

    closed = int(fi.get("closed_trades", 0) or 0)
    if closed < WEIGHT_LEARNING_MIN_TRADES:
        return {
            "ready": False,
            "enabled": True,
            "closed_trades": closed,
            "min_required": WEIGHT_LEARNING_MIN_TRADES,
            "summary": f"Weight Learning için veri yetersiz ({closed}/{WEIGHT_LEARNING_MIN_TRADES}).",
        }

    group_suggestions = {}
    flat_suggestions = []

    for group, current in WEIGHTS.items():
        stats = _wl_group_feature_stats(fi, group)
        if not stats:
            continue

        proposed = {k: float(v) for k, v in current.items()}
        component_changes = []

        for comp, old_weight in current.items():
            comp_stats = stats.get(comp) or {}
            sample = int(comp_stats.get("sample", 0) or 0)
            if sample < WEIGHT_LEARNING_MIN_BUCKET_TRADES:
                continue

            importance = float(comp_stats.get("importance_score", 0) or 0)
            recommendation = comp_stats.get("recommendation")
            if recommendation not in ("CONSIDER_WEIGHT_UP", "CONSIDER_WEIGHT_DOWN"):
                continue

            raw_delta = WEIGHT_LEARNING_RATE * importance
            delta = clamp(raw_delta, -WEIGHT_LEARNING_MAX_DELTA, WEIGHT_LEARNING_MAX_DELTA)
            if abs(delta) < 0.005:
                continue

            new_weight_raw = _wl_clamp_weight(float(old_weight) * (1 + delta))
            proposed[comp] = new_weight_raw
            action = "WEIGHT_UP" if delta > 0 else "WEIGHT_DOWN"
            confidence = clamp(min(sample / 50.0, 1.0) * min(abs(importance) / 0.5, 1.0), 0.0, 1.0)

            change = {
                "group": group,
                "component": comp,
                "action": action,
                "old_weight": round(float(old_weight), 5),
                "raw_new_weight": round(new_weight_raw, 5),
                "delta_pct": round(delta * 100, 2),
                "importance_score": round(importance, 4),
                "sample": sample,
                "confidence": round(confidence, 3),
                "reason": (
                    f"FI={importance:+.3f}; support WR={comp_stats.get('winrate_when_supports_direction')}%; "
                    f"against WR={comp_stats.get('winrate_when_against_direction')}%; sample={sample}"
                ),
            }
            component_changes.append(change)
            flat_suggestions.append(change)

        if component_changes:
            normalized = _wl_normalize_weights(proposed, target_sum=sum(float(v) for v in current.values()))
            for change in component_changes:
                comp = change["component"]
                change["normalized_new_weight"] = normalized.get(comp)
            group_suggestions[group] = {
                "current_weights": {k: round(float(v), 5) for k, v in current.items()},
                "proposed_weights": normalized,
                "changes": component_changes,
            }

    flat_suggestions.sort(key=lambda x: (x.get("confidence", 0), abs(x.get("delta_pct", 0))), reverse=True)

    if not flat_suggestions:
        return {
            "ready": True,
            "enabled": True,
            "closed_trades": closed,
            "summary": "Weight Learning: değişiklik önerisi yok; mevcut ağırlıklar korunabilir.",
            "groups": group_suggestions,
            "suggestions": [],
        }

    return {
        "ready": True,
        "enabled": True,
        "closed_trades": closed,
        "groups": group_suggestions,
        "suggestions": flat_suggestions[:8],
        "summary": f"Weight Learning: {len(flat_suggestions)} ağırlık önerisi adayı üretildi.",
    }


def _wl_safe_write_json(path: str, data: dict) -> None:
    try:
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        log.warning("Weight suggestion dosyası yazılamadı: %s", e)


def persist_weight_learning_suggestions(state_mgr: StateManager = _STATE_MGR) -> dict:
    """Persist latest suggestions to JSON for later approval/backtest workflow."""
    wl = weight_learning_analysis(state_mgr)
    payload = {
        "generated_at": now_ts(),
        "generated_at_tr": tr_now_text(),
        "version": __version__,
        "status": "READY" if wl.get("ready") else "NOT_READY",
        "analysis": wl,
    }
    _wl_safe_write_json(WEIGHT_LEARNING_SUGGESTIONS_FILE, payload)
    return wl


def format_weight_learning_brief(state_mgr: StateManager = _STATE_MGR) -> str:
    wl = weight_learning_analysis(state_mgr)
    if not wl.get("ready"):
        return wl.get("summary", "Weight Learning: veri yetersiz.")
    suggestions = wl.get("suggestions") or []
    if not suggestions:
        return wl.get("summary", "Weight Learning: öneri yok.")
    top = suggestions[:2]
    text = ", ".join(
        f"{s['group']}.{s['component']} {s['delta_pct']:+.1f}%" for s in top
    )
    return f"WL: {text}"


def format_weight_learning_report(state_mgr: StateManager = _STATE_MGR) -> str:
    wl = persist_weight_learning_suggestions(state_mgr)
    if not wl.get("ready"):
        return "🧠 WEIGHT LEARNING\n\n" + wl.get("summary", "Veri yetersiz.")

    lines = [
        "🧠 WEIGHT LEARNING REPORT",
        "",
        f"Kapalı trade: {wl.get('closed_trades', 0)}",
        f"Durum: {wl.get('summary', '-')}",
        "",
        "Öneri adayları (otomatik uygulanmaz):",
    ]
    suggestions = wl.get("suggestions") or []
    if not suggestions:
        lines.append("- Şimdilik ağırlık değişikliği önerisi yok.")
    for s in suggestions[:8]:
        lines.append(
            f"- {s['group']}.{s['component']}: {s['old_weight']:.3f} → "
            f"{s.get('normalized_new_weight', s['raw_new_weight']):.3f} "
            f"({s['delta_pct']:+.1f}%) | conf {s['confidence']:.2f} | {s['reason']}"
        )
    lines.extend([
        "",
        f"Dosya: {WEIGHT_LEARNING_SUGGESTIONS_FILE}",
        "Not: Bu modül sadece öneri üretir; ağırlıkları otomatik değiştirmez.",
    ])
    return "\n".join(lines)

# ============================================================
# PARAMETER APPROVAL ENGINE v1 (Telegram ACCEPT / DECLINE)
# ============================================================

def _safe_read_json(path: str, default):
    try:
        if not os.path.exists(path):
            return copy.deepcopy(default)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if data is not None else copy.deepcopy(default)
    except (OSError, json.JSONDecodeError) as e:
        log.warning("%s okunamadı: %s", path, e)
        return copy.deepcopy(default)


def _safe_write_json(path: str, data) -> None:
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        log.warning("%s yazılamadı: %s", path, e)


def _append_jsonl(path: str, record: dict) -> None:
    try:
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as e:
        log.warning("%s log yazılamadı: %s", path, e)


def _suggestions_payload() -> dict:
    payload = _safe_read_json(PARAMETER_SUGGESTIONS_FILE, {"version": 1, "suggestions": []})
    if not isinstance(payload, dict):
        payload = {"version": 1, "suggestions": []}
    payload.setdefault("version", 1)
    payload.setdefault("suggestions", [])
    if not isinstance(payload["suggestions"], list):
        payload["suggestions"] = []
    return payload


def _suggestion_hash(kind: str, group: str, new_weights: dict) -> str:
    raw = json.dumps(
        {"kind": kind, "group": group, "new_weights": new_weights},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:10]


def _suggestion_id(kind: str, group: str, new_weights: dict) -> str:
    return f"sug_{kind.lower()}_{group.lower()}_{_suggestion_hash(kind, group, new_weights)}"


def _format_weight_group_suggestion_message(suggestion: dict) -> str:
    changes = suggestion.get("changes") or []
    lines = [
        "🧠 PARAMETER / WEIGHT SUGGESTION",
        "",
        f"ID: {suggestion['id']}",
        f"Tip: {suggestion.get('type', '-')}",
        f"Grup: {suggestion.get('group', '-')}",
        f"Güven: %{suggestion.get('confidence', 0) * 100:.1f}",
        f"Validation: {(suggestion.get('backtest_validation') or {}).get('summary', 'Backtest validation yok.')}",
        "",
        "Önerilen değişiklikler:",
    ]
    for ch in changes[:8]:
        lines.append(
            f"- {ch['component']}: {ch['old_weight']:.3f} → "
            f"{ch.get('normalized_new_weight', ch.get('raw_new_weight')):.3f} "
            f"({ch['delta_pct']:+.1f}%)"
        )
    lines.extend([
        "",
        "Komut:",
        f"ACCEPT {suggestion['id']}",
        f"DECLINE {suggestion['id']}",
        "",
        "Not: Kabul edilirse adaptive_config.json güncellenir ve runtime ağırlıkları uygulanır.",
    ])
    return "\n".join(lines)


# ============================================================
# BACKTEST VALIDATION v1 (closed-trade memory validation)
# ============================================================

def _bv_trade_group(t: dict) -> Optional[str]:
    group = t.get("group")
    if group in WEIGHTS:
        return group
    symbol = t.get("symbol")
    if symbol in COINS:
        return COINS[symbol]
    return None


def _bv_trade_direction(t: dict) -> Optional[str]:
    direction = t.get("direction")
    return direction if direction in ("LONG", "SHORT") else None


def _bv_raw_aligned(raw: dict, direction: str) -> dict[str, float]:
    """Raw skorları trade yönüne göre hizalar."""
    out = {}
    sign = -1.0 if direction == "SHORT" else 1.0
    for comp in FEATURE_IMPORTANCE_COMPONENTS:
        try:
            out[comp] = float(raw.get(comp, 0.0)) * sign
        except (TypeError, ValueError):
            out[comp] = 0.0
    return out


def _bv_weighted_support(aligned_raw: dict[str, float], weights: dict[str, float]) -> float:
    score = 0.0
    for comp, weight in weights.items():
        score += float(weight) * float(aligned_raw.get(comp, 0.0))
    return score


def _bv_outcome_value(t: dict) -> Optional[float]:
    if t.get("result") is None:
        return None
    result = t.get("result")
    pnl = float(t.get("pnl_pct", 0) or 0)
    if result == "WIN":
        return 1.0
    if result == "LOSS":
        return -1.0
    if pnl > 0:
        return 0.5
    if pnl < 0:
        return -0.5
    return 0.0


def _bv_metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"sample": 0, "edge_score": 0.0, "win_support_avg": 0.0, "loss_support_avg": 0.0}

    wins = [r for r in rows if r["outcome"] > 0]
    losses = [r for r in rows if r["outcome"] < 0]
    win_support_avg = sum(r["support"] for r in wins) / len(wins) if wins else 0.0
    loss_support_avg = sum(r["support"] for r in losses) / len(losses) if losses else 0.0
    separation = win_support_avg - loss_support_avg
    directional = sum(r["outcome"] * r["support"] for r in rows) / len(rows)

    sorted_rows = sorted(rows, key=lambda x: x["support"])
    half = max(1, len(sorted_rows) // 2)
    low = sorted_rows[:half]
    high = sorted_rows[-half:]
    high_wr = sum(1 for r in high if r["outcome"] > 0) / len(high)
    low_wr = sum(1 for r in low if r["outcome"] > 0) / len(low)
    bucket_edge = high_wr - low_wr

    edge_score = 0.55 * separation + 0.30 * directional + 0.15 * bucket_edge
    return {
        "sample": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_support_avg": round(win_support_avg, 5),
        "loss_support_avg": round(loss_support_avg, 5),
        "separation": round(separation, 5),
        "directional_score": round(directional, 5),
        "bucket_edge": round(bucket_edge, 5),
        "edge_score": round(edge_score, 5),
    }


def validate_weight_suggestion_backtest(
    group: str,
    proposed_weights: dict[str, float],
    state_mgr: StateManager = _STATE_MGR,
) -> dict:
    """Weight önerisini kapalı trade hafızasında doğrular.

    Bu v1 geçmiş mumları baştan replay etmez; açık/kapanmış trade kayıtlarındaki
    raw skor snapshotlarını kullanarak eski ve önerilen ağırlıkların kazanan/kaybeden
    trade'leri ne kadar iyi ayırdığını karşılaştırır.
    """
    if not BACKTEST_VALIDATION_ENABLED:
        return {"enabled": False, "ready": True, "passed": True, "summary": "Backtest validation kapalı."}

    if group not in WEIGHTS:
        return {"enabled": True, "ready": False, "passed": False, "summary": f"Geçersiz grup: {group}"}

    current_weights = {k: float(v) for k, v in WEIGHTS[group].items()}
    proposed = {k: float(proposed_weights.get(k, current_weights.get(k, 0.0))) for k in current_weights}

    rows_current = []
    rows_proposed = []
    for t in get_trades(state_mgr).values():
        if t.get("result") is None:
            continue
        if _bv_trade_group(t) != group:
            continue
        direction = _bv_trade_direction(t)
        outcome = _bv_outcome_value(t)
        raw = t.get("raw") or {}
        if direction is None or outcome is None or not isinstance(raw, dict):
            continue
        aligned = _bv_raw_aligned(raw, direction)
        rows_current.append({"support": _bv_weighted_support(aligned, current_weights), "outcome": outcome})
        rows_proposed.append({"support": _bv_weighted_support(aligned, proposed), "outcome": outcome})

    sample = len(rows_current)
    if sample < BACKTEST_VALIDATION_MIN_TRADES:
        return {
            "enabled": True,
            "ready": False,
            "passed": False,
            "sample": sample,
            "min_required": BACKTEST_VALIDATION_MIN_TRADES,
            "summary": f"Backtest validation için veri yetersiz ({sample}/{BACKTEST_VALIDATION_MIN_TRADES}).",
        }

    current_metrics = _bv_metrics(rows_current)
    proposed_metrics = _bv_metrics(rows_proposed)
    improvement = float(proposed_metrics["edge_score"]) - float(current_metrics["edge_score"])
    passed = improvement >= BACKTEST_VALIDATION_MIN_IMPROVEMENT
    return {
        "enabled": True,
        "ready": True,
        "passed": passed,
        "group": group,
        "sample": sample,
        "min_improvement": BACKTEST_VALIDATION_MIN_IMPROVEMENT,
        "improvement": round(improvement, 5),
        "current": current_metrics,
        "proposed": proposed_metrics,
        "summary": (
            f"BV {group}: edge {current_metrics['edge_score']:.4f} → "
            f"{proposed_metrics['edge_score']:.4f} ({improvement:+.4f}) | "
            f"{'PASS' if passed else 'FAIL'}"
        ),
    }


def persist_backtest_validation_report(report: dict) -> None:
    payload = _safe_read_json(BACKTEST_VALIDATION_REPORT_FILE, {"version": 1, "reports": []})
    if not isinstance(payload, dict):
        payload = {"version": 1, "reports": []}
    payload.setdefault("version", 1)
    payload.setdefault("reports", [])
    if not isinstance(payload["reports"], list):
        payload["reports"] = []
    payload["reports"].append({
        "ts": now_ts(),
        "ts_tr": tr_now_text(),
        "version": __version__,
        "report": report,
    })
    payload["reports"] = payload["reports"][-200:]
    payload["updated_at"] = now_ts()
    _safe_write_json(BACKTEST_VALIDATION_REPORT_FILE, payload)


def format_backtest_validation_brief() -> str:
    payload = _safe_read_json(BACKTEST_VALIDATION_REPORT_FILE, {"reports": []})
    reports = payload.get("reports") if isinstance(payload, dict) else []
    if not reports:
        return "BV: henüz rapor yok"
    last = reports[-1].get("report", {}) if isinstance(reports[-1], dict) else {}
    return "BV: " + last.get("summary", "rapor okunamadı")


def strategy_simulation_analysis(state_mgr: StateManager = _STATE_MGR) -> dict:
    """Strategy Simulation v1.

    V1 gerçek exchange replay değildir; botun kendi kapalı trade hafızasında
    kayıtlı raw skor snapshotlarını kullanarak mevcut ağırlıkların kazanan ve
    kaybeden trade'leri ayırma gücünü ölçer. Amaç: kullanılan ağırlıklar
    tutarlı mı, hangi rejimde edge var, hangi rejimde savunmaya geçmeliyiz?
    """
    if not STRATEGY_SIMULATION_ENABLED:
        return {"ready": False, "reason": "Strategy simulation kapalı."}

    trades = [
        t for t in get_trades(state_mgr).values()
        if isinstance(t, dict)
        and t.get("result") in ("WIN", "LOSS", "EXIT")
        and isinstance(t.get("raw"), dict)
    ]
    if len(trades) < STRATEGY_SIMULATION_MIN_TRADES:
        return {
            "ready": False,
            "closed_trades": len(trades),
            "reason": f"Yeterli kapalı trade yok ({len(trades)}/{STRATEGY_SIMULATION_MIN_TRADES}).",
        }

    rows = []
    regime_stats: dict[str, dict] = {}
    group_stats: dict[str, dict] = {}
    direction_stats: dict[str, dict] = {}

    for t in trades:
        group = t.get("group") or COINS.get(t.get("symbol"), "UNKNOWN")
        direction = t.get("direction")
        raw = t.get("raw") or {}
        weights = WEIGHTS.get(group, {})
        weighted = sum(float(raw.get(k, 0) or 0) * float(weights.get(k, 0) or 0) for k in weights)
        direction_factor = 1 if direction == "LONG" else -1
        aligned_score = weighted * direction_factor
        outcome = 1 if t.get("result") == "WIN" else 0

        regime = (t.get("regime") or {}).get("regime", "UNKNOWN")
        pnl_pct = float(t.get("pnl_pct", 0) or 0)

        rows.append({"aligned_score": aligned_score, "outcome": outcome, "regime": regime, "group": group, "direction": direction, "pnl_pct": pnl_pct})

        for bucket, store in ((regime, regime_stats), (group, group_stats), (direction, direction_stats)):
            if bucket not in store:
                store[bucket] = {"trades": 0, "wins": 0, "pnl_sum": 0.0}
            store[bucket]["trades"] += 1
            store[bucket]["wins"] += outcome
            store[bucket]["pnl_sum"] += pnl_pct

    scores = [r["aligned_score"] for r in rows]
    median_score = statistics.median(scores) if scores else 0.0
    high = [r for r in rows if r["aligned_score"] >= median_score]
    low = [r for r in rows if r["aligned_score"] < median_score]

    def _wr(rs: list[dict]) -> float:
        return sum(r["outcome"] for r in rs) / len(rs) if rs else 0.0

    high_wr = _wr(high)
    low_wr = _wr(low)
    all_wr = _wr(rows)
    separation = high_wr - low_wr

    def _summarize(store: dict) -> dict:
        out = {}
        for key, v in store.items():
            n = v["trades"]
            out[key] = {
                "trades": n,
                "win_rate": round(v["wins"] / n * 100, 1) if n else 0,
                "avg_pnl_pct": round(v["pnl_sum"] / n * 100, 2) if n else 0,
            }
        return out

    report = {
        "ready": True,
        "generated_at": tr_now_text(),
        "closed_trades": len(rows),
        "overall_win_rate": round(all_wr * 100, 1),
        "high_score_win_rate": round(high_wr * 100, 1),
        "low_score_win_rate": round(low_wr * 100, 1),
        "weight_consistency_edge": round(separation, 3),
        "interpretation": (
            "Güçlü" if separation >= 0.15 else
            "Orta" if separation >= 0.05 else
            "Zayıf / overfit riski"
        ),
        "by_regime": _summarize(regime_stats),
        "by_group": _summarize(group_stats),
        "by_direction": _summarize(direction_stats),
    }

    try:
        with open(STRATEGY_SIMULATION_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True, ensure_ascii=False)
    except OSError as e:
        log.warning("Strategy simulation raporu yazılamadı: %s", e)

    return report


def format_strategy_simulation_brief(state_mgr: StateManager = _STATE_MGR) -> str:
    sim = strategy_simulation_analysis(state_mgr)
    if not sim.get("ready"):
        return f"Sim: WAIT ({sim.get('closed_trades', 0)}/{STRATEGY_SIMULATION_MIN_TRADES})"
    return (
        f"Sim: WR %{sim['overall_win_rate']} | "
        f"HighScore %{sim['high_score_win_rate']} vs LowScore %{sim['low_score_win_rate']} | "
        f"Edge {sim['weight_consistency_edge']} ({sim['interpretation']})"
    )

def generate_parameter_suggestions_from_weight_learning(
    state_mgr: StateManager = _STATE_MGR,
    *,
    notify: bool = True,
) -> dict:
    """Convert Weight Learning group proposals into human-approval suggestions.

    This does NOT apply any parameter automatically.
    """
    wl = persist_weight_learning_suggestions(state_mgr)
    payload = _suggestions_payload()
    existing = payload["suggestions"]

    existing_ids = {s.get("id") for s in existing if isinstance(s, dict)}
    created = []

    if not wl.get("ready"):
        return {"created": 0, "summary": wl.get("summary", "Weight Learning hazır değil.")}

    groups = wl.get("groups") or {}
    for group, group_payload in groups.items():
        changes = group_payload.get("changes") or []
        if not changes:
            continue

        confidence = max(float(ch.get("confidence", 0) or 0) for ch in changes)
        if confidence < PARAMETER_SUGGESTION_MIN_CONFIDENCE:
            continue

        proposed = group_payload.get("proposed_weights") or {}
        if not proposed:
            continue

        validation = validate_weight_suggestion_backtest(group, proposed, state_mgr)
        persist_backtest_validation_report(validation)
        if BACKTEST_VALIDATION_REQUIRE_PASS:
            if not validation.get("ready") or not validation.get("passed"):
                log.info(
                    "Weight suggestion %s validation nedeniyle atlandı: %s",
                    group,
                    validation.get("summary"),
                )
                continue

        sid = _suggestion_id("WEIGHTS", group, proposed)
        if sid in existing_ids:
            continue

        # Aynı grup için hâlihazırda bekleyen öneri varsa spam yapma.
        pending_same_group = any(
            s.get("status") == "PENDING"
            and s.get("type") == "WEIGHT_GROUP_UPDATE"
            and s.get("group") == group
            for s in existing
            if isinstance(s, dict)
        )
        if pending_same_group:
            continue

        suggestion = {
            "id": sid,
            "type": "WEIGHT_GROUP_UPDATE",
            "status": "PENDING",
            "created_at": now_ts(),
            "created_at_tr": tr_now_text(),
            "source": "Weight Learning Engine v1",
            "group": group,
            "old_weights": group_payload.get("current_weights", {}),
            "new_weights": proposed,
            "changes": changes,
            "confidence": round(confidence, 3),
            "reason": "Feature Importance + Weight Learning sonuçlarına göre grup ağırlık güncellemesi.",
            "backtest_validation": validation,
        }
        existing.append(suggestion)
        existing_ids.add(sid)
        created.append(suggestion)

    if created:
        payload["updated_at"] = now_ts()
        _safe_write_json(PARAMETER_SUGGESTIONS_FILE, payload)
        if notify:
            for sug in created[:3]:
                send_message(_format_weight_group_suggestion_message(sug))

    return {"created": len(created), "suggestions": created}


def pending_parameter_suggestions_text(limit: int = 8) -> str:
    payload = _suggestions_payload()
    pending = [
        s for s in payload.get("suggestions", [])
        if isinstance(s, dict) and s.get("status") == "PENDING"
    ]
    if not pending:
        return "Bekleyen parametre önerisi yok."

    lines = ["🧠 PENDING PARAMETER SUGGESTIONS", ""]
    for s in pending[:limit]:
        lines.append(
            f"- {s['id']} | {s.get('type')} | {s.get('group')} | "
            f"conf %{float(s.get('confidence', 0))*100:.1f}"
        )
    lines.extend(["", "Kullanım: ACCEPT <id> veya DECLINE <id>"])
    return "\n".join(lines)


def _load_adaptive_config() -> dict:
    cfg = _safe_read_json(ADAPTIVE_CONFIG_FILE, {"version": 1})
    if not isinstance(cfg, dict):
        cfg = {"version": 1}
    cfg.setdefault("version", 1)
    return cfg


def _apply_weights_config(weights_cfg: dict) -> None:
    if not isinstance(weights_cfg, dict):
        return
    for group, group_weights in weights_cfg.items():
        if group not in WEIGHTS or not isinstance(group_weights, dict):
            continue
        for comp, value in group_weights.items():
            if comp in WEIGHTS[group]:
                try:
                    WEIGHTS[group][comp] = float(value)
                except (TypeError, ValueError):
                    log.warning("Adaptive weight değeri geçersiz: %s.%s=%r", group, comp, value)


def load_and_apply_adaptive_config() -> dict:
    """Apply approved adaptive settings at startup."""
    cfg = _load_adaptive_config()
    _apply_weights_config(cfg.get("WEIGHTS", {}))
    return cfg


def _save_adaptive_weights(group: str, new_weights: dict) -> dict:
    cfg = _load_adaptive_config()
    cfg.setdefault("WEIGHTS", {})
    cfg["WEIGHTS"][group] = {k: float(v) for k, v in new_weights.items()}
    cfg["updated_at"] = now_ts()
    cfg["updated_at_tr"] = tr_now_text()
    _safe_write_json(ADAPTIVE_CONFIG_FILE, cfg)

    # Runtime update
    _apply_weights_config({group: cfg["WEIGHTS"][group]})
    return cfg


def accept_parameter_suggestion(suggestion_id: str, actor: str = "telegram") -> str:
    payload = _suggestions_payload()
    suggestions = payload.get("suggestions", [])
    for s in suggestions:
        if not isinstance(s, dict) or s.get("id") != suggestion_id:
            continue
        if s.get("status") != "PENDING":
            return f"{suggestion_id} zaten {s.get('status')} durumunda."

        if s.get("type") != "WEIGHT_GROUP_UPDATE":
            return f"{suggestion_id} tipi desteklenmiyor: {s.get('type')}"

        group = s.get("group")
        new_weights = s.get("new_weights") or {}
        if group not in WEIGHTS or not isinstance(new_weights, dict):
            return f"{suggestion_id} uygulanamadı: grup/weight verisi geçersiz."

        before = copy.deepcopy(WEIGHTS[group])
        _save_adaptive_weights(group, new_weights)
        after = copy.deepcopy(WEIGHTS[group])

        s["status"] = "ACCEPTED"
        s["accepted_at"] = now_ts()
        s["accepted_at_tr"] = tr_now_text()
        s["actor"] = actor
        payload["updated_at"] = now_ts()
        _safe_write_json(PARAMETER_SUGGESTIONS_FILE, payload)

        _append_jsonl(PARAMETER_CHANGE_LOG_FILE, {
            "ts": now_ts(),
            "ts_tr": tr_now_text(),
            "actor": actor,
            "suggestion_id": suggestion_id,
            "type": s.get("type"),
            "group": group,
            "before": before,
            "after": after,
        })

        return (
            f"✅ ACCEPTED {suggestion_id}\n\n"
            f"{group} ağırlıkları adaptive_config.json içine işlendi ve runtime’da uygulandı."
        )

    return f"{suggestion_id} bulunamadı."


def decline_parameter_suggestion(suggestion_id: str, actor: str = "telegram") -> str:
    payload = _suggestions_payload()
    suggestions = payload.get("suggestions", [])
    for s in suggestions:
        if not isinstance(s, dict) or s.get("id") != suggestion_id:
            continue
        if s.get("status") != "PENDING":
            return f"{suggestion_id} zaten {s.get('status')} durumunda."

        s["status"] = "DECLINED"
        s["declined_at"] = now_ts()
        s["declined_at_tr"] = tr_now_text()
        s["actor"] = actor
        payload["updated_at"] = now_ts()
        _safe_write_json(PARAMETER_SUGGESTIONS_FILE, payload)

        _append_jsonl(PARAMETER_CHANGE_LOG_FILE, {
            "ts": now_ts(),
            "ts_tr": tr_now_text(),
            "actor": actor,
            "suggestion_id": suggestion_id,
            "type": s.get("type"),
            "group": s.get("group"),
            "status": "DECLINED",
        })
        return f"❌ DECLINED {suggestion_id}"

    return f"{suggestion_id} bulunamadı."


def _telegram_get_updates(state_mgr: StateManager) -> list[dict]:
    if not TOKEN:
        return []
    offset = state_mgr.get_meta("telegram_update_offset", 0)
    params = {"timeout": 0}
    if offset:
        params["offset"] = offset
    try:
        data = request_json(f"{TELEGRAM_BASE}/bot{TOKEN}/getUpdates", params=params, retries=1, timeout=10)
    except Exception as e:
        log.debug("Telegram command polling başarısız: %s", e)
        return []
    if not isinstance(data, dict) or not data.get("ok"):
        return []
    updates = data.get("result") or []
    if updates:
        max_id = max(int(u.get("update_id", 0)) for u in updates)
        state_mgr.set_meta("telegram_update_offset", max_id + 1)
    return updates if isinstance(updates, list) else []


def _extract_command_text(update: dict) -> tuple[Optional[str], Optional[str]]:
    message = update.get("message") or update.get("edited_message") or {}
    if not isinstance(message, dict):
        return None, None

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if CHAT_ID and str(chat_id) != str(CHAT_ID):
        return None, None

    text = (message.get("text") or "").strip()
    if not text:
        return None, None
    return str(chat_id), text


def process_telegram_commands(state_mgr: StateManager = _STATE_MGR) -> None:
    """Poll Telegram commands for ACCEPT / DECLINE / PENDING.

    Simple polling is enough for Render-style long-running bot deployments.
    """
    if not TELEGRAM_COMMANDS_ENABLED or not TOKEN or not CHAT_ID:
        return

    last_poll = state_mgr.get_meta("last_telegram_command_poll_ts", 0)
    if now_ts() - last_poll < TELEGRAM_COMMAND_POLL_INTERVAL_SECONDS:
        return
    state_mgr.set_meta("last_telegram_command_poll_ts", now_ts())

    updates = _telegram_get_updates(state_mgr)
    for update in updates:
        _, text = _extract_command_text(update)
        if not text:
            continue

        parts = text.strip().split()
        cmd = parts[0].upper()

        if cmd == "PENDING":
            send_message(pending_parameter_suggestions_text())
        elif cmd == "ACCEPT" and len(parts) >= 2:
            send_message(accept_parameter_suggestion(parts[1], actor="telegram"))
        elif cmd == "DECLINE" and len(parts) >= 2:
            send_message(decline_parameter_suggestion(parts[1], actor="telegram"))
        elif cmd in ("ACCEPT", "DECLINE"):
            send_message("Kullanım: ACCEPT <suggestion_id> veya DECLINE <suggestion_id>")

def _process_news_cycle(state_mgr: StateManager, current_news: dict) -> dict:
    """News taraması ve alert gönderim döngüsü.

    Returns:
        Güncel news context.
    """
    last_news_scan = state_mgr.get_meta("last_news_scan_ts", 0)
    if now_ts() - last_news_scan < NEWS_SCAN_INTERVAL_SECONDS:
        return current_news

    try:
        new_news = scan_news()
    except Exception as e:
        log.error("News scan hata: %s", e)
        new_news = default_news_context()

    state_mgr.set_meta("last_news_scan_ts", now_ts())

    # News alert
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
    """Tek coin için sinyal üretip gerekirse alert gönderir."""
    try:
        result = weighted_signal(symbol, features, btc, eth, session_ctx, news_ctx)
        regime = detect_market_regime(btc, eth, news_ctx)
        result["regime"] = regime
        result["trade_quality"] = compute_trade_quality(result, regime)
        result["entry_engine"] = evaluate_entry_engine(result)
        result["regime_commander"] = regime_commander_decision(result, regime)
        result["portfolio_check"] = portfolio_risk_check(result, state_mgr)
        result = apply_trade_filters(result)

        # Noise-free mode:
        # - Standalone signal detail messages are suppressed
        # - Trade tracking engine opens/closes trades and sends trade-level messages
        old = state_mgr.get_symbol(symbol)
        pending = None
        last_alert_ts = old.get("last_alert_ts", 0) if old else 0

        if SEND_MOVEMENT_ALERTS:
            movement_reasons = detect_movement_alert(symbol, result)
            if movement_reasons and not should_movement_alert_cooldown(state_mgr, symbol):
                if send_message(format_movement_alert(symbol, result, movement_reasons)):
                    mark_movement_alert_sent(state_mgr, symbol)

        plan = build_trade_plan(result)
        if plan and result.get("signal") in ("LONG", "SHORT") and result.get("actionable", True):
            open_trade(result, plan, state_mgr)

        state_mgr.update_symbol(symbol, {
            "signal": result["signal"],
            "level": result["level"],
            "score": result["score"],
            "confidence": result["confidence"],
            "actionable": result.get("actionable", result["signal"] in ("LONG", "SHORT")),
            "raw_signal": result.get("raw_signal"),
            "blocked_signal": result.get("blocked_signal"),
            "quality_grade": result.get("trade_quality", {}).get("grade"),
            "entry_status": result.get("entry_engine", {}).get("status"),
            "regime": result.get("regime", {}).get("regime"),
            "direction_bias": result.get("regime", {}).get("direction_bias"),
            "regime_allowed": result.get("regime_commander", {}).get("allowed"),
            "last_alert_ts": last_alert_ts,
            "pending_alert_type": pending,
            "updated_at": now_ts(),
        })

        return result

    except Exception as e:
        log.warning("%s sinyal işlemi başarısız: %s", symbol, e)
        return None



def format_regime_strategy_brief(results: list[dict]) -> str:
    """Short heartbeat summary of current Regime Commander decision."""
    if not results:
        return "Regime: veri yok."
    btc = next((r for r in results if r.get("symbol") == "BTCUSDT"), results[0])
    rg = btc.get("regime") or {}
    if not rg:
        return "Regime: yok."
    return (
        f"Regime: {rg.get('regime', '-')} | "
        f"Bias {rg.get('direction_bias', '-')} | "
        f"Risk x{rg.get('risk_multiplier', '-')} | "
        f"MinQ {rg.get('min_quality', '-')}"
    )


def _send_periodic_messages(
    state_mgr: StateManager,
    results: list[dict],
    session_ctx: SessionContext,
    news_ctx: dict,
) -> None:
    """Özet ve heartbeat mesajları."""
    last_summary = state_mgr.get_meta("last_summary_ts", 0)
    if SEND_SUMMARY_MESSAGES and now_ts() - last_summary >= SUMMARY_INTERVAL_SECONDS:
        if results:
            send_message(make_summary(results, session_ctx, news_ctx))
        # results boş olsa bile ts ilerletilsin ki bir sonraki dilim
        # SUMMARY_INTERVAL kadar sonraya kaysın (dakika dakika tekrar denemesin).
        state_mgr.set_meta("last_summary_ts", now_ts())

    last_heartbeat = state_mgr.get_meta("last_heartbeat_ts", 0)
    if now_ts() - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
        # Weight suggestions are generated at heartbeat cadence to avoid spam.
        generate_parameter_suggestions_from_weight_learning(state_mgr, notify=True)
        pending_count = len([
            s for s in _suggestions_payload().get("suggestions", [])
            if isinstance(s, dict) and s.get("status") == "PENDING"
        ])
        send_message(
            f"✅ BOT AKTİF (v{__version__})\n\n"
            f"Son kontrol: {tr_now_text()}\n"
            f"{format_edge_brief(state_mgr)}\n"
            f"{format_feature_importance_brief(state_mgr)}\n"
            f"{format_weight_learning_brief(state_mgr)}\n"
            f"{format_regime_strategy_brief(results)}\n"
            f"{format_strategy_simulation_brief(state_mgr)}\n"
            f"PS: base %{POSITION_SIZING_BASE_RISK_PCT*100:.2f} | clamp %{POSITION_SIZING_MIN_RISK_PCT*100:.2f}-%{POSITION_SIZING_MAX_RISK_PCT*100:.2f}\n"
            f"{format_backtest_validation_brief()}\n"
            f"Pending suggestions: {pending_count}"
        )
        state_mgr.set_meta("last_heartbeat_ts", now_ts())


def bot_loop(stop_event: threading.Event = _STOP_EVENT) -> None:
    """Ana bot döngüsü."""
    log.info("BOT BAŞLADI v%s", __version__)
    send_message(
        f"BOT BAŞLADI 🚀 v{__version__} — News (yön-bağımlı veto/dampener) + session + basis aktif."
    )

    state_mgr = _STATE_MGR

    # İlk açılışta hemen özet/heartbeat tetiklenmesin
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
            process_telegram_commands(state_mgr)
            news_context = _process_news_cycle(state_mgr, news_context)
            state_mgr.save()

            # BTC ve ETH features (referans, başarısızsa tur atla)
            try:
                btc = get_features("BTCUSDT")
                eth = get_features("ETHUSDT")
            except Exception as e:
                consecutive_failures += 1
                wait = min(ERROR_BACKOFF_LONG, ERROR_BACKOFF_SHORT * consecutive_failures)
                log.error(
                    "BTC/ETH features alınamadı (#%d), %ds bekleniyor: %s",
                    consecutive_failures, wait, e,
                )
                state_mgr.set_meta("consecutive_failures", consecutive_failures)
                state_mgr.save()
                if sleep_or_stop(wait):
                    break
                continue

            consecutive_failures = 0
            state_mgr.set_meta("consecutive_failures", 0)

            # Diğer coinleri paralel çek (BTC/ETH zaten var).
            # Her coin için get_features kendi içinde 5 endpointi paralel çekiyor;
            # coinler arası paralelizm scan turunu dramatik kısaltır.
            features_cache: dict[str, dict] = {"BTCUSDT": btc, "ETHUSDT": eth}
            other_symbols = [s for s in COINS if s not in features_cache]

            if other_symbols:
                future_to_symbol = {
                    _SYMBOL_EXECUTOR.submit(get_features, sym): sym
                    for sym in other_symbols
                }
                for fut in as_completed(future_to_symbol):
                    sym = future_to_symbol[fut]
                    try:
                        features_cache[sym] = fut.result()
                    except Exception as e:
                        log.warning("%s features alınamadı: %s", sym, e)

            results: list[dict] = []
            for symbol in COINS:
                features = features_cache.get(symbol)
                if features is None:
                    continue

                result = _process_symbol(
                    symbol, features, btc, eth, session_ctx, news_context, state_mgr
                )
                if result:
                    results.append(result)

            results_by_symbol = {r["symbol"]: r for r in results}
            update_trades(results_by_symbol, state_mgr)
            close_trades_on_signal_changes(results_by_symbol, state_mgr)
            retry_pending_trade_alerts(state_mgr)

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
                send_message(f"⚠️ Bot hata aldı:\n{e}\nZaman: {tr_now_text()}")
            except Exception:
                pass
            # Ana hata sonrası uzun backoff istemiyoruz; yine de consecutive
            # sayacını da sıfırlayalım ki BTC/ETH başarısızlığı normalleşince
            # hemen agresif backoff'a girilmesin.
            consecutive_failures = 0
            state_mgr.set_meta("consecutive_failures", 0)
            state_mgr.save()
            if sleep_or_stop(ERROR_BACKOFF_SHORT):
                break

    log.info("Bot loop durdu.")


# ============================================================
# ENTRYPOINT
# ============================================================

_SHUTDOWN_LOCK = threading.RLock()
_SHUTDOWN_DONE = False


def shutdown_resources() -> None:
    global _SHUTDOWN_DONE
    with _SHUTDOWN_LOCK:
        if _SHUTDOWN_DONE:
            return
        _SHUTDOWN_DONE = True
        log.info("Shutdown sinyali alındı, kaynaklar kapatılıyor.")
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
    load_and_apply_adaptive_config()
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
