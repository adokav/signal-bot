"""Signal Bot v5 Core: research menus without order authority.

Three modules run: the altcoin watch (/altcoin), the CoinMarketCap new-coin
security screen with /check, and the D1 trend loop shadow record (/d1). The
long position system that replaces the removed short-term radars is designed
in docs/POSITION_LONG.md and arrives in later PRs.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify

from acce_unified import altcoin_watch, new_coins
from acce_unified.text import esc
from acce_unified.trend_loop import Ledger as TrendLedger, MexcSpot, TrendLoop

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("signal-bot-v5")

TOKEN = os.getenv("TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "10000"))
STATE_FILE = Path(os.getenv("CORE_STATE_FILE", "/data/core_state.json"))
POLL_SECONDS = max(2, int(os.getenv("TELEGRAM_COMMAND_POLL_INTERVAL_SECONDS", "5")))
STARTUP_MESSAGE = os.getenv("CORE_SEND_STARTUP_MESSAGE", "1") == "1"
REQUEST_TIMEOUT_SECONDS = max(3, int(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")))
# D1 trend loop in shadow mode (docs/TREND_LOOP_LIVE.md): the majors' daily close at 00:00 UTC,
# intraday acute stops, an append-only ledger. User decision (2026-10-02): entry and exit alerts are
# pushed with the RİSK_AZALTIR label; "0" records them without pushing. No orders either way.
TREND_LOOP_ENABLED = os.getenv("TREND_LOOP_ENABLED", "1") == "1"
TREND_LOOP_ALERTS = os.getenv("TREND_LOOP_ALERTS_ENABLED", "1") == "1"
TREND_LOOP_STOP_SECONDS = max(60, int(os.getenv("TREND_LOOP_STOP_INTERVAL_SECONDS", "300")))
TREND_LOOP_TICK_SECONDS = 60
# "Yeni Coinler" menu (docs/NEW_COINS.md). User decision (2026-10-05): new coins come from
# CoinMarketCap, and every coin goes through a contract-security screen (honeypot, owner
# powers, holder concentration, liquidity lock). Needs CMC_API_KEY (free Basic plan) in the environment.
NEW_COINS_ENABLED = os.getenv("NEW_COINS_ENABLED", "1") == "1"
NEW_COINS_SCAN_SECONDS = max(600, int(os.getenv("NEW_COINS_SCAN_INTERVAL_SECONDS", "1800")))
NEW_COINS_MAX_AGE_SECONDS = 3 * NEW_COINS_SCAN_SECONDS
CMC_API_KEY = os.getenv("CMC_API_KEY", "").strip()
# "Altcoin İzleme" (docs/ALTCOIN_WATCH.md). User decision (2026-10-16): a long-horizon fundamental screen
# built from the synthesis of two selection models; research only, with a pre-registered forward record.
ALTCOIN_WATCH_ENABLED = os.getenv("ALTCOIN_WATCH_ENABLED", "1") == "1"
ALTCOIN_WATCH_REFRESH_SECONDS = max(86_400, int(os.getenv("ALTCOIN_WATCH_REFRESH_SECONDS",
                                                          str(altcoin_watch.REFRESH_SECONDS))))
ALTCOIN_WATCH_MAX_AGE_SECONDS = 2 * ALTCOIN_WATCH_REFRESH_SECONDS + 86_400
ALTCOIN_WATCH_TICK_SECONDS = 30 * 60
COINGECKO_DEMO_API_KEY = os.getenv("COINGECKO_DEMO_API_KEY", "").strip()
COINGECKO_PRO_API_KEY = os.getenv("COINGECKO_PRO_API_KEY", "").strip()

# Historical base rate shown under the new-coin menu (docs/LISTING_REPLAY_REPORT.md, run #1, closed study).
NEW_COINS_BASE_RATE = (
    "Geçmiş test (Binance yeni listelemeleri 2020-10 → 2026-08): kaçınma iddiası doğrulanmadı (NO_CLAIM 6/6); "
    "medyan getiri negatif, yeni listelemelerin yalnızca %15–28'i BTC'yi geçti, ortalamayı az sayıda büyük kazanan "
    "taşıdı. CoinMarketCap yeni coinleri için test yok."
)

TREND_LOOP = TrendLoop(
    TrendLedger(Path(os.getenv("TREND_LOOP_LEDGER_FILE", str(STATE_FILE.parent / "trend_loop_ledger.jsonl")))),
    market=MexcSpot(timeout=REQUEST_TIMEOUT_SECONDS),
)
TREND_LOOP_LOADED = threading.Event()
CMC = new_coins.CoinMarketCap(CMC_API_KEY, timeout=REQUEST_TIMEOUT_SECONDS)
NEW_COINS = new_coins.NewCoinsScanner(
    CMC, new_coins.SecurityProviders(timeout=REQUEST_TIMEOUT_SECONDS),
    TrendLedger(Path(os.getenv("NEW_COINS_LEDGER_FILE", str(STATE_FILE.parent / "new_coins_ledger.jsonl")))),
    window_hours=max(1, int(os.getenv("NEW_COINS_WINDOW_HOURS", str(new_coins.WINDOW_HOURS)))),
)
ALTCOIN_WATCH = altcoin_watch.AltcoinWatch(
    altcoin_watch.CoinGecko(demo_key=COINGECKO_DEMO_API_KEY, pro_key=COINGECKO_PRO_API_KEY),
    altcoin_watch.DefiLlama(),
    new_coins.SecurityProviders(timeout=REQUEST_TIMEOUT_SECONDS),
    TrendLedger(Path(os.getenv("ALTCOIN_WATCH_LEDGER_FILE", str(STATE_FILE.parent / "altcoin_watch_ledger.jsonl")))),
)
# /check runs in the Telegram thread: its own HTTP session; the scanner's lookalike reference is only read.
CHECKER = new_coins.AddressChecker(new_coins.SecurityProviders(timeout=REQUEST_TIMEOUT_SECONDS),
                                   reference=NEW_COINS.current_reference)
APP = Flask(__name__)
HTTP = requests.Session()
LOCK = threading.RLock()
STATE: dict[str, Any] = {
    "offset": 0,
    "trend_loop": None,
    "trend_loop_error": None,
    "new_coins": None,
    "new_coins_error": None,
    "altcoin_watch": None,
    "altcoin_watch_error": None,
    "altcoin_watch_failures": 0,
    "altcoin_watch_retry_at": 0,
    "altcoin_watch_failed_keyed": None,
}

COMMANDS = [
    {"command": "panel", "description": "Sade kontrol paneli"},
    {"command": "new", "description": "CoinMarketCap yeni coinler: dolandırıcılık taraması"},
    {"command": "check", "description": "Kontrat adresiyle anında dolandırıcılık taraması: /check <adres>"},
    {"command": "altcoin", "description": "Altcoin izleme: arz, gelir, değerleme (uzun vade, araştırma)"},
    {"command": "d1", "description": "D1 döngü: gölge pozisyonlar, çıkış seviyesi ve acil stop"},
    {"command": "status", "description": "Tarama sağlığı ve veri durumu"},
    {"command": "scan", "description": "Yeni coin taramasını şimdi yap"},
]


_URL_RE = re.compile(r"https?://\S+")


def _safe_error(exc: BaseException) -> str:
    """Exception type plus a URL-redacted, truncated message.

    Request exceptions can carry URLs with query parameters; nothing from
    them may reach state files, logs, Telegram or the public endpoint
    unredacted (AGENTS.md §3).
    """

    message = _URL_RE.sub("<url>", str(exc))
    for secret, mask in ((TOKEN, "<token>"), (CMC_API_KEY, "<cmc-key>"), (COINGECKO_DEMO_API_KEY, "<cg-key>"),
                         (COINGECKO_PRO_API_KEY, "<cg-key>")):
        if secret:
            message = message.replace(secret, mask)
    message = message.strip()[:160]
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _load_state() -> None:
    try:
        payload = json.loads(STATE_FILE.read_text("utf-8"))
    except FileNotFoundError:
        return
    except Exception as exc:
        log.warning("State okunamadı: %s", exc)
        return
    if isinstance(payload, dict):
        STATE.update({key: payload.get(key) for key in STATE if key in payload})


def _save_state() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:
        log.warning("State yazılamadı: %s", exc)


def _api(method: str, payload: dict[str, Any] | None = None) -> Any:
    if not TOKEN:
        return None
    # HTTP client timeout must exceed Telegram's server-side long-poll timeout.
    # getUpdates uses timeout=20 (see telegram_loop); if the HTTP client also
    # times out at 20 the two race and abort every poll cycle with a
    # ReadTimeout even when the server is healthy. 30 covers 20+overhead.
    try:
        response = HTTP.post(
            f"https://api.telegram.org/bot{TOKEN}/{method}",
            json=payload or {}, timeout=30,
        )
    except requests.RequestException as exc:
        # Request URL and exception text can contain the bot token. Strip both.
        raise RuntimeError(f"telegram_transport_error:{type(exc).__name__}") from None
    if not response.ok:
        # Response body may echo the URL, headers, or query params. Do not
        # forward provider text into logs or exceptions.
        raise RuntimeError(f"telegram_http_{response.status_code}:{method}")
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError(f"telegram_bad_json:{method}") from None
    if not body.get("ok"):
        # Telegram description text is safe (does not contain the token) but
        # keep a short fixed prefix so downstream logs cannot be spoofed.
        description = str(body.get("description") or method)[:200]
        raise RuntimeError(f"telegram_api_error:{description}")
    return body.get("result")


def _plain(text: str) -> str:
    """Telegram HTML reduced to plain text (fallback when Telegram rejects the markup)."""

    return html.unescape(re.sub(r"</?(?:b|i|pre|code)>", "", text))


def send(text: str, *, keyboard: dict[str, Any] | None = None, html_mode: bool = False) -> None:
    """Send a message. ``html_mode`` texts must escape every untrusted value (acce_unified.text.esc)."""

    if not TOKEN or not CHAT_ID:
        log.info("Telegram kapalı: %s", (_plain(text) if html_mode else text).replace("\n", " | ")[:180])
        return
    payload: dict[str, Any] = {
        "chat_id": CHAT_ID,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    if not html_mode:
        _api("sendMessage", payload)
        return
    try:
        _api("sendMessage", dict(payload, parse_mode="HTML"))
    except RuntimeError as exc:
        # A markup error must not swallow the alert: send the same text without formatting.
        if not str(exc).startswith(("telegram_http_400", "telegram_api_error")):
            raise
        log.warning("HTML mesaj reddedildi, düz metin gönderiliyor: %s", exc)
        _api("sendMessage", dict(payload, text=_plain(text)[:4096]))


def panel_keyboard() -> dict[str, Any]:
    return {"inline_keyboard": [
        [{"text": "🔭 Altcoin İzleme", "callback_data": "ALTCOIN"}],
        [{"text": "📈 D1 Döngü", "callback_data": "D1"}],
        [{"text": "🆕 Yeni Coinler (CMC)", "callback_data": "NEW"},
         {"text": "🔎 Kontrat Kontrol", "callback_data": "CHECK"}],
        [
            {"text": "📊 Durum", "callback_data": "STATUS"},
            {"text": "🔄 Şimdi Tara", "callback_data": "SCAN"},
        ],
    ]}


def format_new() -> str:
    with LOCK:
        snapshot, error = STATE.get("new_coins"), STATE.get("new_coins_error")
    return new_coins.render(
        snapshot, now=int(time.time()), error=error, enabled=NEW_COINS_ENABLED,
        max_age=NEW_COINS_MAX_AGE_SECONDS, base_rate=NEW_COINS_BASE_RATE,
    )


def format_status() -> str:
    now = int(time.time())
    return "\n".join(line for line in [
        "📊 SIGNAL BOT v5 CORE",
        "Mod: SHADOW / RADAR ONLY",
        altcoin_watch.status_line(STATE.get("altcoin_watch"), now=now, enabled=ALTCOIN_WATCH_ENABLED,
                                  error=STATE.get("altcoin_watch_error"), keyed=ALTCOIN_WATCH.gecko.keyed),
        new_coins.status_line(STATE.get("new_coins"), now=now, enabled=NEW_COINS_ENABLED,
                              configured=CMC.configured, error=STATE.get("new_coins_error")),
        TREND_LOOP.status_line() if TREND_LOOP_ENABLED else "D1 döngü: kapalı",
        f"D1 hata: {STATE.get('trend_loop_error')}" if STATE.get("trend_loop_error") else "",
        "Emir yetkisi: YOK",
    ] if line)


# ---------------------------------------------------------------------------
# D1 trend loop (shadow; acce_unified.trend_loop)
# ---------------------------------------------------------------------------


def _deliver_trend_alerts(now: int) -> None:
    """Push the ledger's pending D1 messages in order; a failed send stays pending for the next tick."""

    for message in TREND_LOOP.outbox():
        pushed = TREND_LOOP_ALERTS and bool(TOKEN and CHAT_ID)
        if pushed:
            try:
                send(message["text"], keyboard=panel_keyboard(), html_mode=True)
            except Exception as exc:
                log.warning("D1 uyarısı gönderilemedi, tekrar denenecek: %s", _safe_error(exc))
                return
        TREND_LOOP.delivered(message["id"], at=now, sent=pushed)


def trend_loop_tick(now: int | None = None) -> None:
    """One pass: restore once, decide the due daily close, check the acute stops, deliver pending alerts.

    Each step fails on its own: a failed daily decision does not skip the stop
    check, and alerts already in the ledger are delivered either way.
    """

    now = int(time.time()) if now is None else now
    errors: list[str] = []
    if not TREND_LOOP_LOADED.is_set():
        try:
            with LOCK:
                saved = STATE.get("trend_loop")
            TREND_LOOP.load(saved)
            TREND_LOOP_LOADED.set()
        except Exception as exc:
            errors.append(_safe_error(exc))
    if TREND_LOOP_LOADED.is_set():
        steps = [lambda: TREND_LOOP.run_daily(now)]
        last_check = TREND_LOOP.state.get("last_stop_check_at")
        if last_check is None or now - int(last_check) >= TREND_LOOP_STOP_SECONDS:
            steps.append(lambda: TREND_LOOP.check_stops(now))
        steps.append(lambda: _deliver_trend_alerts(now))
        for step in steps:
            try:
                step()
            except Exception as exc:
                errors.append(_safe_error(exc))
    error = "; ".join(errors) or None
    if error:
        log.warning("D1 döngü adımı başarısız: %s", error)
    with LOCK:
        if TREND_LOOP_LOADED.is_set():
            STATE["trend_loop"] = TREND_LOOP.export()
        STATE["trend_loop_error"] = error
        _save_state()


def new_coins_tick(now: int | None = None) -> None:
    """Scan CoinMarketCap's newest coins through the security screen, then record due outcomes."""

    now = int(time.time()) if now is None else now
    snapshot, errors = None, []
    if not CMC.configured:
        errors.append("CMC_API_KEY tanımlı değil")
    else:
        for step in ("scan", "outcomes"):
            try:
                if step == "scan":
                    snapshot = NEW_COINS.scan(now)
                else:
                    NEW_COINS.record_outcomes(now)
            except Exception as exc:
                errors.append(_safe_error(exc))
    error = "; ".join(errors) or None
    if error:
        log.warning("Yeni coin taraması: %s", error)
    with LOCK:
        if snapshot is not None:
            STATE["new_coins"] = snapshot
        STATE["new_coins_error"] = error
        _save_state()


def altcoin_watch_tick(now: int | None = None) -> None:
    """Weekly fundamental refresh of the largest altcoins; due outcomes of earlier cohorts are recorded."""

    now = int(time.time()) if now is None else now
    with LOCK:
        snapshot = STATE.get("altcoin_watch")
        retry_at = int(STATE.get("altcoin_watch_retry_at") or 0)
        # A CoinGecko key was added after a keyless failure: try now. A wait saved before the marker existed
        # (None) is treated as keyless; at worst a keyed bot makes one extra attempt, then the marker is set.
        if retry_at and STATE.get("altcoin_watch_failed_keyed") is not True and ALTCOIN_WATCH.gecko.keyed:
            retry_at = 0
    errors = []
    if not altcoin_watch.fresh(snapshot, now=now, max_age=ALTCOIN_WATCH_REFRESH_SECONDS) and now >= retry_at:
        try:
            snapshot = ALTCOIN_WATCH.refresh(now)
            with LOCK:
                STATE["altcoin_watch"] = snapshot
                STATE["altcoin_watch_failures"], STATE["altcoin_watch_retry_at"] = 0, 0
                STATE["altcoin_watch_failed_keyed"] = None
        except Exception as exc:                     # the last snapshot stays and is shown as stale
            errors.append(_safe_error(exc))
            # A keyless refresh can wait out rate limits for many minutes: count the delay from when it failed.
            failed_at = max(now, int(time.time()))
            with LOCK:                               # retry in 30 minutes, then doubling up to 6 hours
                failures = int(STATE.get("altcoin_watch_failures") or 0) + 1
                STATE["altcoin_watch_failures"] = failures
                STATE["altcoin_watch_retry_at"] = altcoin_watch.next_attempt(failures, failed_at)
                STATE["altcoin_watch_failed_keyed"] = ALTCOIN_WATCH.gecko.keyed
    elif now < retry_at:
        with LOCK:                                   # still waiting to retry: keep showing the last error
            errors.append(str(STATE.get("altcoin_watch_error") or "").split("; ")[0] or "yenileme bekliyor")
    try:
        ALTCOIN_WATCH.record_outcomes(now)
    except Exception as exc:
        errors.append(_safe_error(exc))
    error = "; ".join(errors) or None
    if error:
        log.warning("Altcoin izleme: %s", error)
    with LOCK:
        STATE["altcoin_watch_error"] = error
        _save_state()


def altcoin_watch_loop() -> None:
    while True:
        try:
            altcoin_watch_tick()
        except Exception:
            log.warning("Altcoin izleme beklenmedik hata", exc_info=True)
        time.sleep(ALTCOIN_WATCH_TICK_SECONDS)


def format_altcoin(text: str = "") -> str:
    """/altcoin: the list; /altcoin <symbol or CoinGecko id>: one coin's card (only that word is read)."""

    with LOCK:
        snapshot, error = STATE.get("altcoin_watch"), STATE.get("altcoin_watch_error")
    now = int(time.time())
    parts = str(text or "").split()
    query = parts[1] if len(parts) == 2 and parts[0].startswith("/") else None
    if len(parts) > 2 or (query is not None and not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", query)):
        return esc("Kullanım: /altcoin ya da /altcoin <sembol>")
    if query is None:
        return altcoin_watch.render(snapshot, now=now, error=error, enabled=ALTCOIN_WATCH_ENABLED,
                                    max_age=ALTCOIN_WATCH_MAX_AGE_SECONDS, keyed=ALTCOIN_WATCH.gecko.keyed,
                                    retry_at=int(STATE.get("altcoin_watch_retry_at") or 0))
    found = altcoin_watch.find(snapshot, query)
    if not found:
        return esc(f"{query[:20]} izleme evreninde yok (ilk {altcoin_watch.UNIVERSE_SIZE} altcoin) "
                   "ya da henüz tarama yapılmadı.")
    if len(found) > 1:
        ids = ", ".join(str(c.get("id"))[:40] for c in found[:5])
        return esc(f"Bu sembolde birden çok coin var: {ids}. CoinGecko kimliğiyle sor: /altcoin <kimlik>")
    return altcoin_watch.render_card(found[0], now=now, generated_at=int((snapshot or {}).get("generated_at") or 0))


def new_coins_loop() -> None:
    while True:
        try:
            new_coins_tick()
        except Exception:
            log.warning("Yeni coin taraması beklenmedik hata", exc_info=True)
        time.sleep(NEW_COINS_SCAN_SECONDS)


def trend_loop_loop() -> None:
    while True:
        try:
            trend_loop_tick()
        except Exception:
            log.warning("D1 döngü beklenmedik hata", exc_info=True)
        time.sleep(TREND_LOOP_TICK_SECONDS)


def _command(text: str) -> str:
    token = (text or "").strip().split(maxsplit=1)[0].lower()
    return {
        "/start": "PANEL", "/panel": "PANEL", "/new": "NEW", "/listings": "NEW",
        "/status": "STATUS", "/scan": "SCAN", "/d1": "D1", "/check": "CHECK", "/altcoin": "ALTCOIN",
    }.get(token, token.lstrip("/").upper())


def check_address(text: str) -> None:
    """/check <address>: the new-coin screen for one address the user sends (not in the forward record)."""

    parsed = new_coins.parse_check(text)
    if parsed is None:
        send(new_coins.CHECK_USAGE, keyboard=panel_keyboard())
        return
    send("🔎 Kontrol ediliyor…")
    try:
        card, note = CHECKER.check(*parsed)
    except Exception as exc:                         # only the error type reaches the chat, never provider text
        log.warning("Kontrat kontrolü hatası: %s", _safe_error(exc))
        send(f"Kontrol yapılamadı ({type(exc).__name__}); biraz sonra yeniden dene.", keyboard=panel_keyboard())
        return
    if card is None:
        send(str(note), keyboard=panel_keyboard())
    else:
        send(new_coins.render_check(card, now=int(time.time()), note=note), keyboard=panel_keyboard(),
             html_mode=True)


def handle(action: str, text: str = "") -> None:
    if action == "NEW":
        send(format_new(), keyboard=panel_keyboard(), html_mode=True)
    elif action == "CHECK":
        check_address(text)
    elif action == "ALTCOIN":
        send(format_altcoin(text), keyboard=panel_keyboard(), html_mode=True)
    elif action == "D1":
        now = int(time.time())
        if not TREND_LOOP_ENABLED:
            send("D1 döngü kapalı (TREND_LOOP_ENABLED=0).", keyboard=panel_keyboard())
        elif not TREND_LOOP_LOADED.is_set():          # an empty report would read as "no open positions"
            send(f"D1 döngü yüklenemedi; pozisyonlar bilinmiyor. Hata: {STATE.get('trend_loop_error') or 'yok'}",
                 keyboard=panel_keyboard())
        else:
            report = TREND_LOOP.positions_text(now)
            if not TREND_LOOP.fresh(now, stop_seconds=TREND_LOOP_STOP_SECONDS):
                report += "\n" + esc(
                    "⚠️ Döngü güncel değil: son kapanış karara bağlanmadı ya da stop kontrolü gecikti. "
                    f"Hata: {STATE.get('trend_loop_error') or 'yok'}")
            send(report, keyboard=panel_keyboard(), html_mode=True)
    elif action == "STATUS":
        send(format_status(), keyboard=panel_keyboard())
    elif action == "SCAN":
        # The altcoin watch refreshes weekly on its own thread (a refresh takes minutes); only the
        # new-coin scan is quick enough to run from the command thread.
        send("🔄 Yeni coin taraması başlatıldı.", keyboard=panel_keyboard())
        new_coins_tick()
        send(format_status(), keyboard=panel_keyboard())
    else:
        send(
            "🎯 SIGNAL BOT v5 CORE\n\nAraştırma menüleri (emir yetkisi yok):\n"
            "• Altcoin izleme: arz, gelir, değerleme (uzun vade)\n"
            "• D1 trend döngüsü (gölge kayıt)\n"
            "• CoinMarketCap yeni coinler ve /check: dolandırıcılık taraması\n\n"
            "Uzun vadeli long pozisyon sistemi hazırlanıyor: docs/POSITION_LONG.md",
            keyboard=panel_keyboard(),
        )


def telegram_loop() -> None:
    if not TOKEN or not CHAT_ID:
        log.warning("TOKEN/CHAT_ID yok; Telegram komut dinleyicisi başlamadı")
        return
    # A previously-set webhook conflicts with getUpdates (Telegram HTTP 409).
    # Idempotent: returns True whether or not a webhook existed. Does not fix
    # the case where a different instance is polling the same token, but
    # covers the common redeploy-after-webhook-experiment failure mode.
    try:
        _api("deleteWebhook", {"drop_pending_updates": False})
    except Exception as exc:
        log.warning("Webhook temizlenemedi (getUpdates yine denenecek): %s", exc)
    try:
        _api("setMyCommands", {"commands": COMMANDS})
    except Exception as exc:
        log.warning("Bot komutları ayarlanamadı: %s", exc)
    while True:
        try:
            updates = _api("getUpdates", {
                "offset": int(STATE.get("offset") or 0), "timeout": 20,
                "allowed_updates": ["message", "callback_query"],
            }) or []
            for update in updates:
                STATE["offset"] = max(int(STATE.get("offset") or 0), int(update.get("update_id") or 0) + 1)
                callback = update.get("callback_query") or {}
                message = update.get("message") or callback.get("message") or {}
                chat = str((message.get("chat") or {}).get("id") or "")
                if chat != CHAT_ID:
                    continue
                # A button press carries no arguments: the message it belongs to is the bot's own text.
                text = "" if callback else str(message.get("text") or "")
                action = str(callback.get("data") or "") or _command(text)
                if callback.get("id"):
                    _api("answerCallbackQuery", {"callback_query_id": callback["id"]})
                handle(action, text)
            _save_state()
        except Exception as exc:
            log.warning("Telegram polling hatası: %s", exc)
            time.sleep(POLL_SECONDS)


def _error_code(value: Any) -> str | None:
    # Public endpoint: expose only the exception class, never message text.
    if not value:
        return None
    return str(value).split(": ", 1)[0].split()[0][:60]


@APP.get("/")
def health() -> Any:
    now = int(time.time())
    trend_fresh = TREND_LOOP.fresh(now, stop_seconds=TREND_LOOP_STOP_SECONDS) if TREND_LOOP_ENABLED else None
    coins_fresh = (new_coins.fresh(STATE.get("new_coins"), now=now, max_age=NEW_COINS_MAX_AGE_SECONDS)
                   if NEW_COINS_ENABLED else None)
    altcoin_fresh = (altcoin_watch.fresh(STATE.get("altcoin_watch"), now=now, max_age=ALTCOIN_WATCH_MAX_AGE_SECONDS)
                     if ALTCOIN_WATCH_ENABLED else None)
    return jsonify({
        # Healthy means fresh artifacts exist, not merely "no error recorded".
        "ok": (
            (not TREND_LOOP_ENABLED or (trend_fresh and STATE.get("trend_loop_error") is None))
            and (not NEW_COINS_ENABLED or (coins_fresh and STATE.get("new_coins_error") is None))
            and (not ALTCOIN_WATCH_ENABLED or (altcoin_fresh and STATE.get("altcoin_watch_error") is None))
        ),
        "service": "signal-bot-v5-core",
        "trend_loop_fresh": trend_fresh,
        "trend_loop_last_error": _error_code(STATE.get("trend_loop_error")),
        "new_coins_fresh": coins_fresh,
        "new_coins_last_error": _error_code(STATE.get("new_coins_error")),
        "altcoin_watch_fresh": altcoin_fresh,
        "altcoin_watch_last_error": _error_code(STATE.get("altcoin_watch_error")),
        "can_authorize_trade": False,
    })


def main() -> None:
    _load_state()
    threading.Thread(target=telegram_loop, name="telegram-command-loop", daemon=True).start()
    if TREND_LOOP_ENABLED:
        threading.Thread(target=trend_loop_loop, name="d1-trend-loop", daemon=True).start()
    if NEW_COINS_ENABLED:
        threading.Thread(target=new_coins_loop, name="cmc-new-coins", daemon=True).start()
    if ALTCOIN_WATCH_ENABLED:
        threading.Thread(target=altcoin_watch_loop, name="altcoin-watch", daemon=True).start()
    if STARTUP_MESSAGE:
        try:
            send("✅ Signal Bot v5 Core başladı.", keyboard=panel_keyboard())
        except Exception:
            log.warning("Başlangıç mesajı gönderilemedi", exc_info=True)
    APP.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
