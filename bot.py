import os
import re
import time
import json
import hashlib
import logging
import threading
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("signal-bot")


# ============================================================
# ENV VARIABLES
# ============================================================

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")


# ============================================================
# BASE URLS
# ============================================================

MEXC_SPOT_BASE = "https://api.mexc.com"
MEXC_FUTURES_BASE = "https://contract.mexc.com"


# ============================================================
# BOT CONFIG
# ============================================================

COINS = {
    "BTCUSDT": "CORE",
    "ETHUSDT": "CORE",
    "AVAXUSDT": "CORE",
    "RENDERUSDT": "HIGH_BETA",
    "ONDOUSDT": "HIGH_BETA",
    "LDOUSDT": "HIGH_BETA",
    "POPCATUSDT": "HIGH_BETA",
}

STATE_FILE = "state.json"

SCAN_INTERVAL = 300
COOLDOWN_SECONDS = 900
SUMMARY_INTERVAL_SECONDS = 4 * 60 * 60
HEARTBEAT_INTERVAL_SECONDS = 60 * 60

NEWS_SCAN_INTERVAL_SECONDS = 15 * 60
NEWS_ALERT_COOLDOWN_SECONDS = 30 * 60
NEWS_MAX_AGE_HOURS = 2  # Bundan eski haberler skoru etkilemez

MIN_SIGNAL_LEVEL = "MEDIUM"

LEVEL_ORDER = {"WEAK": 1, "MEDIUM": 2, "STRONG": 3}

CRITICAL_ALERTS = {
    "❌ SİNYAL İPTAL",
    "🔄 YÖN DEĞİŞTİ",
    "🛑 NEWS VETO",
}


# ============================================================
# US MARKET CALENDAR
# ============================================================

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


# ============================================================
# SCORE CONFIG
# ============================================================

THRESHOLDS = {
    "CORE": {"long": 1.55, "short": -1.55, "strong": 2.25},
    "HIGH_BETA": {"long": 1.95, "short": -1.95, "strong": 2.70},
}

# Not: News artık ağırlıklı bir skor değil; veto/dampener olarak çalışıyor.
WEIGHTS = {
    "CORE": {
        "macro": 0.25, "market": 0.22, "trend": 0.18,
        "momentum": 0.14, "volume": 0.05, "liquidity": 0.05,
        "basis": 0.11,
    },
    "HIGH_BETA": {
        "macro": 0.15, "market": 0.18, "trend": 0.18,
        "momentum": 0.23, "volume": 0.10, "liquidity": 0.06,
        "basis": 0.10,
    },
}

# DRY: spread eşikleri tek kaynaktan
SPREAD_LIMITS = {
    "CORE": 12,
    "HIGH_BETA": 25,
}

SCORE_PARAMS = {
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
# NEWS CONFIG
# ============================================================

# Tek kelime → word boundary regex; çok kelimeli ifade → substring match.
NEWS_CATEGORIES = {
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
            # Çok kelimeli (yüksek-spesifite, substring match)
            "stablecoin depeg",
            "exchange hack",
            "crypto hack",
            "exchange outage",
            "liquidation cascade",
            "etf rejection",
            "sec lawsuit",
            # Tek kelime (word-boundary regex ile — false positive korumalı)
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
            "ceasefire", "deal reached", "tariff pause",
            "rate cut hopes", "etf approval", "peace agreement",
            "diplomatic breakthrough",
        ],
        "risk": 2,
    },
}

# News thresholds
NEWS_VETO_THRESHOLD = 2.0      # |risk| >= 2 → ters yöndeki sinyali veto et
NEWS_DAMPEN_THRESHOLD = 1.0    # |risk| >= 1 → ters yöndeki skoru sönümle
NEWS_DAMPEN_FACTOR = 0.6
NEWS_BOOST_THRESHOLD = 1.0     # |risk| >= 1 → aynı yöndeki skoru hafif güçlendir
NEWS_BOOST_FACTOR = 1.1
NEWS_MULTI_SOURCE_BOOST = 3    # Aynı kategoriden 3+ kaynak varsa risk x1.2
NEWS_MULTI_SOURCE_FACTOR = 1.2


# ============================================================
# FLASK HEALTH SERVER
# ============================================================

app = Flask(__name__)


@app.route("/")
def health():
    return "signal-bot is running"


# ============================================================
# THREAD-SAFE STATE
# ============================================================

state_lock = threading.Lock()


# ============================================================
# TIME HELPERS
# ============================================================

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

    if year not in US_MARKET_HOLIDAYS:
        log.warning("%d yılı için ABD tatil listesi tanımlı değil.", year)

    if weekday >= 5:
        return {
            "session": "WEEKEND",
            "macro_multiplier": 0.20,
            "micro_multiplier": 1.20,
            "news_multiplier": 0.85,
            "note": "Hafta sonu: ABD piyasa verileri bayat kabul edildi.",
        }

    if date_key in holidays:
        return {
            "session": "US_HOLIDAY",
            "macro_multiplier": 0.20,
            "micro_multiplier": 1.20,
            "news_multiplier": 0.85,
            "note": "ABD piyasa tatili: makro/risk verilerinin etkisi azaltıldı.",
        }

    close_time = early_close if date_key in early_closes else regular_close

    if regular_open <= minutes < close_time:
        return {
            "session": "US_OPEN",
            "macro_multiplier": 1.00,
            "micro_multiplier": 1.00,
            "news_multiplier": 1.00,
            "note": "ABD piyasası açık: makro/risk verileri normal ağırlıkta.",
        }

    if 4 * 60 <= minutes < regular_open or close_time <= minutes < 20 * 60:
        return {
            "session": "US_EXTENDED",
            "macro_multiplier": 0.50,
            "micro_multiplier": 1.10,
            "news_multiplier": 0.95,
            "note": "ABD pre/after-market: makro etkisi kısmen azaltıldı.",
        }

    return {
        "session": "US_CLOSED",
        "macro_multiplier": 0.35,
        "micro_multiplier": 1.15,
        "news_multiplier": 0.90,
        "note": "ABD piyasası kapalı: kripto içi sinyallerin ağırlığı artırıldı.",
    }


# ============================================================
# TELEGRAM
# ============================================================

def send_message(text):
    if not TOKEN or not CHAT_ID:
        log.warning("TOKEN veya CHAT_ID eksik, mesaj gönderilemedi.")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text}

    try:
        r = requests.post(url, data=data, timeout=15)
        log.info("Telegram %s: %s", r.status_code, r.text[:120])
    except Exception as e:
        log.error("Telegram gönderim hatası: %s", e)


# ============================================================
# HTTP HELPERS
# ============================================================

def request_json(url, params=None, retries=3, base_delay=1.5):
    """Akıllı retry: 4xx (429 hariç) fail-fast; 429 Retry-After'a uyar;
    5xx/timeout/connection için exponential backoff."""
    last_err = None

    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=15)

            # 429 → rate limit, Retry-After'a uy
            if r.status_code == 429:
                wait = base_delay * (2 ** i)
                try:
                    wait = max(wait, float(r.headers.get("Retry-After", wait)))
                except (TypeError, ValueError):
                    pass
                log.warning("429 rate limit, %.1fs bekleniyor: %s", wait, url)
                time.sleep(wait)
                last_err = requests.HTTPError("429 Too Many Requests")
                continue

            # Diğer 4xx → kalıcı hata, retry'a değmez
            if 400 <= r.status_code < 500:
                r.raise_for_status()

            r.raise_for_status()
            return r.json()

        except requests.HTTPError as e:
            sc = e.response.status_code if e.response is not None else None
            if sc is not None and 400 <= sc < 500 and sc != 429:
                raise
            last_err = e
            if i < retries - 1:
                time.sleep(base_delay * (2 ** i))
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(base_delay * (2 ** i))

    raise last_err


# ============================================================
# MEXC HELPERS
# ============================================================

def to_futures_symbol(symbol):
    return symbol.replace("USDT", "_USDT")


def get_klines(symbol, interval="5m", limit=100):
    return request_json(
        f"{MEXC_SPOT_BASE}/api/v3/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )


def get_ticker(symbol):
    return request_json(
        f"{MEXC_SPOT_BASE}/api/v3/ticker/24hr",
        {"symbol": symbol},
    )


def get_spot_price(symbol):
    data = request_json(
        f"{MEXC_SPOT_BASE}/api/v3/ticker/price",
        {"symbol": symbol},
    )
    return float(data["price"])


def get_book(symbol):
    return request_json(
        f"{MEXC_SPOT_BASE}/api/v3/ticker/bookTicker",
        {"symbol": symbol},
    )


def get_futures_fair_price(symbol):
    futures_symbol = to_futures_symbol(symbol)
    url = f"{MEXC_FUTURES_BASE}/api/v1/contract/fair_price/{futures_symbol}"
    data = request_json(url)

    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            for k in ("fairPrice", "price"):
                if k in data["data"]:
                    return float(data["data"][k])
        for k in ("fairPrice", "price"):
            if k in data:
                return float(data[k])

    return None


# ============================================================
# MATH HELPERS
# ============================================================

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
    if price == 0:
        return "0"
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    if price >= 0.01:
        return f"{price:.6f}"
    return f"{price:.8f}"


# ============================================================
# STATE
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"symbols": {}, "meta": {}}

    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)

        # Eski format migration
        if "symbols" not in data:
            symbols = {k: v for k, v in data.items() if not k.startswith("_")}
            meta = {
                "last_summary_ts": data.get("_last_summary_ts", 0),
                "last_heartbeat_ts": data.get("_last_heartbeat_ts", 0),
                "last_news_scan_ts": data.get("_last_news_scan_ts", 0),
                "last_news_alert_ts": data.get("_last_news_alert_ts", 0),
                "last_news_alert_hash": data.get("_last_news_alert_hash"),
            }
            return {"symbols": symbols, "meta": meta}

        return data

    except Exception as e:
        log.error("State okunamadı, sıfırlanıyor: %s", e)
        return {"symbols": {}, "meta": {}}


def save_state(state):
    """Atomik yazım: tmp dosyaya yaz, sonra rename."""
    with state_lock:
        try:
            tmp_file = STATE_FILE + ".tmp"
            with open(tmp_file, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_file, STATE_FILE)
        except Exception as e:
            log.error("State kaydedilemedi: %s", e)


# ============================================================
# NEWS LAYER
# ============================================================

def default_news_context():
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


def is_fresh_article(published_at):
    """ISO-8601 publishedAt değerini parse eder, NEWS_MAX_AGE_HOURS içindeyse True."""
    if not published_at:
        return True  # Tarih yoksa şüpheli; agregasyon süreci elemese de güvenli

    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return True

    age = datetime.now(timezone.utc) - dt
    return age <= timedelta(hours=NEWS_MAX_AGE_HOURS)


def fetch_newsapi_headlines():
    if not NEWS_API_KEY:
        log.info("NEWS_API_KEY eksik, NewsAPI atlandı.")
        return []

    # Üç ayrı query: jeopolitik / makro / kripto — kategoriler karışmasın
    queries = [
        "Iran OR Israel OR Taiwan OR Russia OR Ukraine OR war OR missile",
        "Fed OR Powell OR CPI OR inflation OR tariff OR Trump OR FOMC",
        "crypto SEC OR ETF OR hack OR stablecoin OR depeg",
    ]

    headlines = []

    for q in queries:
        try:
            data = request_json(
                "https://newsapi.org/v2/everything",
                {
                    "q": q,
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 15,
                    "apiKey": NEWS_API_KEY,
                },
            )
        except Exception as e:
            log.warning("NewsAPI query başarısız (%s...): %s", q[:30], e)
            continue

        for article in data.get("articles", []):
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

    return headlines


def fetch_gdelt_headlines():
    """GDELT 'sort=DateDesc' + tazelik filtresi ile son haberleri verir."""
    query = (
        "trump tariff iran israel china war missile fed powell "
        "crypto sec etf hack stablecoin"
    )

    try:
        data = request_json(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            {
                "query": query,
                "mode": "ArtList",
                "format": "json",
                "maxrecords": 20,
                "sort": "DateDesc",
            },
        )
    except Exception as e:
        log.warning("GDELT alınamadı: %s", e)
        return []

    headlines = []
    for article in data.get("articles", []):
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


def _compile_keyword_patterns():
    """Tek kelime keyword'leri word-boundary regex'e, çok kelimeli ifadeleri
    substring match'e çevirir. Modül yüklenirken bir kez çalışır."""
    compiled = {}
    for category, cfg in NEWS_CATEGORIES.items():
        patterns = []
        for kw in cfg["keywords"]:
            kw_lower = kw.lower().strip()
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


def count_keyword_hits(text, category):
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


def classify_headline(headline):
    text = headline["title"]

    best_category = "NONE"
    best_risk = 0
    best_hits = 0

    for category, cfg in NEWS_CATEGORIES.items():
        hits = count_keyword_hits(text, category)
        if hits > best_hits:
            best_hits = hits
            best_category = category
            best_risk = cfg["risk"]

    if best_hits == 0:
        return None

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


def scan_news():
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
    category_counts = {}
    for item in classified:
        category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1

    # En şiddetli skoru al; çoklu kaynak varsa boost
    classified.sort(key=lambda x: abs(x["risk_score"]), reverse=True)
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


def headline_hash(headline):
    if not headline:
        return None
    return hashlib.md5(headline.encode("utf-8")).hexdigest()[:12]


def format_news_alert(news_context):
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
# FEATURE ENGINE (paralel HTTP)
# ============================================================

def get_features(symbol):
    """Tek coin için 5 endpoint paralel çağrılır."""
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_k5 = ex.submit(get_klines, symbol, "5m", 100)
        f_k15 = ex.submit(get_klines, symbol, "15m", 100)
        f_ticker = ex.submit(get_ticker, symbol)
        f_book = ex.submit(get_book, symbol)
        f_spot = ex.submit(get_spot_price, symbol)

        k5 = f_k5.result()
        k15 = f_k15.result()
        ticker = f_ticker.result()
        book = f_book.result()
        spot_price = f_spot.result()

    closes_5 = [float(x[4]) for x in k5]
    closes_15 = [float(x[4]) for x in k15]
    volumes_5 = [float(x[5]) for x in k5]

    if len(closes_5) < 13 or len(closes_15) < 17:
        raise ValueError(
            f"{symbol} için yetersiz kline verisi "
            f"(5m={len(closes_5)}, 15m={len(closes_15)})"
        )

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

    # Son barı dahil etmeden, son 30 barın median'ı
    recent_vols = volumes_5[-31:-1]
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

    futures_price = None
    basis_pct = None
    try:
        futures_price = get_futures_fair_price(symbol)
        if futures_price:
            basis_pct = pct(futures_price, spot_price)
    except Exception as e:
        log.info("%s futures basis alınamadı: %s", symbol, e)

    return {
        "last": last,
        "spot_price": spot_price,
        "futures_price": futures_price,
        "basis_pct": basis_pct,
        "ema9": ema9, "ema21": ema21, "ema50": ema50,
        "ema9_15": ema9_15, "ema21_15": ema21_15, "ema50_15": ema50_15,
        "ret_5m": ret_5m, "ret_15m": ret_15m,
        "ret_1h": ret_1h, "ret_4h": ret_4h,
        "vol_ratio": vol_ratio,
        "spread_bps": spread_bps,
        "change_24h": change_24h,
    }


# ============================================================
# SCORING FUNCTIONS
# ============================================================

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
    bad_limit = SPREAD_LIMITS[group]
    spread = f["spread_bps"]
    if spread > bad_limit:
        return -2
    if spread > bad_limit / 2:
        return -1
    return 0


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


# ============================================================
# SIGNAL ENGINE
# ============================================================

def veto_signal(symbol, f, btc, eth):
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


def classify_level(score_abs, group):
    strong = THRESHOLDS[group]["strong"]
    medium = THRESHOLDS[group]["long"]
    if score_abs >= strong:
        return "STRONG"
    if score_abs >= medium:
        return "MEDIUM"
    return "WEAK"


def normalize_weights_if_basis_missing(weights, features):
    if features["basis_pct"] is not None:
        return weights

    active = {k: v for k, v in weights.items() if k != "basis"}
    total_w = sum(active.values())
    if total_w == 0:
        return weights

    redistributed = {k: v / total_w for k, v in active.items()}
    redistributed["basis"] = 0
    return redistributed


def apply_news_modulation(total_score, news_risk, session_news_mult):
    """News skorunu yön-bağımlı uygular:
       |risk| ≥ VETO ve ters yön → veto (None döner)
       |risk| ≥ DAMPEN ve ters yön → x DAMPEN_FACTOR (sönümle)
       |risk| ≥ BOOST ve aynı yön → x BOOST_FACTOR (güçlendir)
    """
    effective_risk = news_risk * session_news_mult
    abs_risk = abs(effective_risk)

    same_direction = (total_score > 0 and effective_risk > 0) or \
                     (total_score < 0 and effective_risk < 0)
    opposite_direction = (total_score > 0 and effective_risk < 0) or \
                         (total_score < 0 and effective_risk > 0)

    if abs_risk >= NEWS_VETO_THRESHOLD and opposite_direction:
        return None, "news_veto"
    if abs_risk >= NEWS_DAMPEN_THRESHOLD and opposite_direction:
        return total_score * NEWS_DAMPEN_FACTOR, "news_dampened"
    if abs_risk >= NEWS_BOOST_THRESHOLD and same_direction:
        return total_score * NEWS_BOOST_FACTOR, "news_boosted"

    return total_score, "news_neutral"


def weighted_signal(symbol, features, btc, eth, session_context, news_context):
    group = COINS[symbol]
    base_weights = WEIGHTS[group]
    weights = normalize_weights_if_basis_missing(base_weights, features)

    veto = veto_signal(symbol, features, btc, eth)

    macro_multiplier = session_context["macro_multiplier"]
    micro_multiplier = session_context["micro_multiplier"]
    news_multiplier = session_context["news_multiplier"]

    raw = {
        "macro": score_macro(btc) * macro_multiplier,
        "market": score_market(btc, eth) * micro_multiplier,
        "trend": score_trend(features) * micro_multiplier,
        "momentum": score_momentum(features) * micro_multiplier,
        "volume": score_volume(features) * micro_multiplier,
        "liquidity": score_liquidity(features, group),
        "basis": score_basis(features["basis_pct"], features) * micro_multiplier,
    }

    pre_news_total = sum(raw[k] * weights[k] for k in raw)

    # News yön-bağımlı modülatör
    news_risk = news_context.get("news_risk_score", 0)
    modulated_total, news_action = apply_news_modulation(
        pre_news_total, news_risk, news_multiplier
    )

    max_total = (
        weights["macro"] * 3
        + weights["market"] * 2
        + weights["trend"] * 2
        + weights["momentum"] * 2
        + weights["volume"] * 2
        + weights["liquidity"] * 2
        + weights["basis"] * 2
    )

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

    confidence = round(abs(total) / max_total * 100, 1) if max_total > 0 else 0

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
        "session_context": session_context,
        "news_context": news_context,
        "basis_missing": features["basis_pct"] is None,
    }


# ============================================================
# MESSAGE FORMATTERS
# ============================================================

def format_signal(result, title):
    f = result["features"]
    raw = result["raw"]
    session = result["session_context"]
    news = result["news_context"]

    basis_text = f"{f['basis_pct']:+.3f}%" if f["basis_pct"] is not None else "N/A"
    futures_text = format_price(f["futures_price"]) if f["futures_price"] is not None else "N/A"

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


def make_summary(results, session_context, news_context):
    longs = [f"{r['symbol']}({r['level']})" for r in results if r["signal"] == "LONG"]
    shorts = [f"{r['symbol']}({r['level']})" for r in results if r["signal"] == "SHORT"]
    neutral = [r["symbol"] for r in results if r["signal"] == "NO_TRADE"]

    headline = news_context.get("headline") or "-"
    url = news_context.get("url") or ""
    headline_line = headline + (f"\n{url}" if url else "")

    return (
        "📊 DURUM ÖZETİ\n\n"
        f"Seans: {session_context['session']}\n"
        f"Makro Çarpan: x{session_context['macro_multiplier']}\n"
        f"Mikro Çarpan: x{session_context['micro_multiplier']}\n"
        f"News Çarpanı: x{session_context['news_multiplier']}\n"
        f"Not: {session_context['note']}\n\n"
        f"Haber Kategorisi: {news_context['category']}\n"
        f"Haber Risk Skoru: {news_context['news_risk_score']} / 3\n"
        f"Haber Başlığı: {headline_line}\n\n"
        f"LONG: {', '.join(longs) if longs else '-'}\n"
        f"SHORT: {', '.join(shorts) if shorts else '-'}\n"
        f"NO_TRADE: {', '.join(neutral) if neutral else '-'}\n"
        f"Zaman: {tr_now_text()}"
    )


# ============================================================
# ALERT LOGIC
# ============================================================

def should_notify_signal(result):
    if result["signal"] == "NO_TRADE":
        return False
    return LEVEL_ORDER[result["level"]] >= LEVEL_ORDER[MIN_SIGNAL_LEVEL]


def decide_alert(old, new):
    # News veto: aktif pozisyonu olan kullanıcıya kritik bilgi
    if new.get("news_action") == "news_veto" and old and \
            old.get("signal") in ["LONG", "SHORT"]:
        return "🛑 NEWS VETO"

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
    if alert_type in CRITICAL_ALERTS:
        return False
    if not old:
        return False
    return now_ts() - old.get("last_alert_ts", 0) < COOLDOWN_SECONDS


# ============================================================
# MAIN BOT LOOP
# ============================================================

def bot_loop():
    log.info("BOT BAŞLADI")
    send_message("BOT BAŞLADI 🚀 News (yön-bağımlı veto/dampener) + session + basis aktif.")

    state = load_state()

    # İlk açılışta hemen özet/heartbeat tetiklenmesin
    if not state["meta"].get("last_summary_ts"):
        state["meta"]["last_summary_ts"] = now_ts()
    if not state["meta"].get("last_heartbeat_ts"):
        state["meta"]["last_heartbeat_ts"] = now_ts()

    news_context = default_news_context()

    while True:
        try:
            session_context = get_session_context()

            # News taraması
            last_news_scan = state["meta"].get("last_news_scan_ts", 0)
            if now_ts() - last_news_scan >= NEWS_SCAN_INTERVAL_SECONDS:
                try:
                    news_context = scan_news()
                except Exception as e:
                    log.error("News scan hata: %s", e)
                    news_context = default_news_context()
                state["meta"]["last_news_scan_ts"] = now_ts()

                # News alert: aynı başlık tekrar gönderilmesin
                if abs(news_context["news_risk_score"]) >= NEWS_VETO_THRESHOLD:
                    last_alert_ts = state["meta"].get("last_news_alert_ts", 0)
                    last_alert_hash = state["meta"].get("last_news_alert_hash")
                    current_hash = headline_hash(news_context.get("headline"))

                    cooled = now_ts() - last_alert_ts >= NEWS_ALERT_COOLDOWN_SECONDS
                    new_headline = current_hash != last_alert_hash

                    if cooled and new_headline:
                        send_message(format_news_alert(news_context))
                        state["meta"]["last_news_alert_ts"] = now_ts()
                        state["meta"]["last_news_alert_hash"] = current_hash

            # BTC ve ETH features (referans, başarısızsa tur atla)
            try:
                btc = get_features("BTCUSDT")
                eth = get_features("ETHUSDT")
            except Exception as e:
                log.error("BTC/ETH features alınamadı, tur atlanıyor: %s", e)
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

                    result = weighted_signal(
                        symbol, features, btc, eth, session_context, news_context
                    )
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
                    log.warning("%s bu turda atlandı: %s", symbol, e)
                    continue

            # Periyodik özet
            last_summary = state["meta"].get("last_summary_ts", 0)
            if now_ts() - last_summary >= SUMMARY_INTERVAL_SECONDS and results:
                send_message(make_summary(results, session_context, news_context))
                state["meta"]["last_summary_ts"] = now_ts()

            # Heartbeat
            last_heartbeat = state["meta"].get("last_heartbeat_ts", 0)
            if now_ts() - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                send_message(
                    f"✅ Bot aktif\n"
                    f"Son kontrol: {tr_now_text()}\n"
                    f"Seans: {session_context['session']}\n"
                    f"Makro: x{session_context['macro_multiplier']} | "
                    f"Mikro: x{session_context['micro_multiplier']} | "
                    f"News: x{session_context['news_multiplier']}\n"
                    f"Haber: {news_context['category']} "
                    f"(risk {news_context['news_risk_score']})"
                )
                state["meta"]["last_heartbeat_ts"] = now_ts()

            save_state(state)
            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            log.exception("ANA HATA: %s", e)
            try:
                send_message(f"⚠️ Bot hata aldı:\n{e}\nZaman: {tr_now_text()}")
            except Exception:
                pass
            time.sleep(60)


# ============================================================
# ENTRYPOINT
# ============================================================

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
