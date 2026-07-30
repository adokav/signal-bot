"""Signal Bot v5 Core — MEXC Liquid-100 and New Listing radar only.

This process intentionally has two jobs:
1) rank the best three long candidates inside MEXC's 100 most liquid USDT spot markets;
2) detect and score verified MEXC new spot listings.

It never places orders. Execution will be reintroduced only behind a separately
validated risk/execution interface.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify

from acce_unified import UnifiedConfig, UnifiedRadarEngine
from acce_unified.listing_fundamentals import ListingFundamentalMetricsProvider

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

CONFIG = UnifiedConfig.from_env()
FUNDAMENTAL_PROVIDER = ListingFundamentalMetricsProvider(
    demo_api_key=os.getenv("COINGECKO_DEMO_API_KEY", ""),
    pro_api_key=os.getenv("COINGECKO_PRO_API_KEY", ""),
    timeout=CONFIG.request_timeout_seconds,
    cache_ttl_seconds=CONFIG.fundamental_cache_ttl_seconds,
    max_assets=CONFIG.fundamental_max_assets,
)
ENGINE = UnifiedRadarEngine(CONFIG, {}, fundamental_provider=FUNDAMENTAL_PROVIDER)
APP = Flask(__name__)
HTTP = requests.Session()
LOCK = threading.RLock()
STATE: dict[str, Any] = {
    "offset": 0,
    "last_scan_at": 0,
    "snapshot": None,
    "last_error": None,
}

COMMANDS = [
    {"command": "panel", "description": "Sade kontrol paneli"},
    {"command": "longs", "description": "MEXC Likit 100 Long İlk 3"},
    {"command": "new", "description": "MEXC yeni listeleme adayları"},
    {"command": "status", "description": "Tarama sağlığı ve veri durumu"},
    {"command": "scan", "description": "Şimdi yeniden tara"},
]


def _load_state() -> None:
    try:
        payload = json.loads(STATE_FILE.read_text("utf-8"))
        if isinstance(payload, dict):
            STATE.update(payload)
    except FileNotFoundError:
        return
    except Exception as exc:
        log.warning("State okunamadı: %s", exc)


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
    response = HTTP.post(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        json=payload or {},
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or method)
    return body.get("result")


def send(text: str, *, keyboard: dict[str, Any] | None = None) -> None:
    if not TOKEN or not CHAT_ID:
        log.info("Telegram kapalı: %s", text.replace("\n", " | ")[:180])
        return
    payload: dict[str, Any] = {
        "chat_id": CHAT_ID,
        "text": text[:4096],
        "disable_web_page_preview": True,
    }
    if keyboard:
        payload["reply_markup"] = keyboard
    _api("sendMessage", payload)


def panel_keyboard() -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "💧 Long İlk 3", "callback_data": "LONGS"}],
            [{"text": "🆕 Yeni Listeler", "callback_data": "NEW"}],
            [
                {"text": "📊 Durum", "callback_data": "STATUS"},
                {"text": "🔄 Şimdi Tara", "callback_data": "SCAN"},
            ],
        ]
    }


def _snapshot() -> dict[str, Any] | None:
    value = STATE.get("snapshot")
    return value if isinstance(value, dict) else None


def _money(value: Any) -> str:
    try:
        amount = float(value or 0)
    except (TypeError, ValueError):
        amount = 0.0
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(amount) >= threshold:
            return f"${amount / threshold:.2f}{suffix}"
    return f"${amount:.0f}"


def _quantity(value: Any, *, missing: str = "yok") -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return missing
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(amount) >= threshold:
            return f"{amount / threshold:.2f}{suffix}"
    return f"{amount:g}"


def _price(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "veri yok"
    if amount >= 1:
        return f"${amount:,.4f}".rstrip("0").rstrip(".")
    if amount >= 0.01:
        return f"${amount:.6f}".rstrip("0").rstrip(".")
    return f"${amount:.10f}".rstrip("0").rstrip(".")


def _pct(value: Any, *, missing: str = "?") -> str:
    try:
        return f"%{float(value):+.1f}"
    except (TypeError, ValueError):
        return missing


def format_longs(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return "💧 MEXC LİKİT 100 — LONG İLK 3\n\nİlk tarama bekleniyor."
    rows = (snapshot.get("liquid_long_candidates") or [])[:3]
    context = snapshot.get("liquid_market_context") or {}
    lines = [
        "💧 MEXC LİKİT 100 — LONG İLK 3",
        f"Rejim: {context.get('regime') or '?'} · Pozitif genişlik %{float(context.get('positive_breadth_pct') or 0):.1f}",
        "",
    ]
    if not rows:
        lines.append("Şu anda bütün kalite ve risk kapılarını geçen aday yok.")
    for i, item in enumerate(rows, 1):
        meta = item.get("metadata") or {}
        metrics = meta.get("long_metrics") or {}
        fundamentals = meta.get("fundamentals") or {}
        lines.extend([
            f"{i}. {item.get('symbol', '?')} — {int(item.get('score') or 0)}/100 · {item.get('stage') or '-'}",
            f"   1s %{float(metrics.get('change_1h_pct') or 0):+.1f} · 4s %{float(metrics.get('change_4h_pct') or 0):+.1f} · RSI {float(metrics.get('rsi14') or 0):.0f}",
            f"   Hacim ivmesi {float(metrics.get('volume_ratio') or 0):.1f}x · Spread {float(meta.get('spread_bps') or 0):.1f} bp",
            f"   MEXC 24s {_money(meta.get('quote_volume'))} · Dolaşım %{float(fundamentals.get('circulation_pct') or 0):.1f}",
            "",
        ])
    lines.append("Radar puanı işlem emri değildir; tetik ve risk onayı ayrıca gerekir.")
    return "\n".join(lines)


def format_new(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return "🆕 MEXC NEW LISTING\n\nİlk tarama bekleniyor."
    strong = snapshot.get("listing_candidates") or []
    watch = snapshot.get("listing_filtered_candidates") or []
    combined = [*strong, *watch]
    rows = sorted(
        combined,
        key=lambda item: (
            int(item.get("score") or 0),
            float((item.get("metadata") or {}).get("quote_volume") or 0),
            str(item.get("symbol") or ""),
        ),
        reverse=True,
    )[:5]
    lines = ["🆕 MEXC NEW LISTING — EN YÜKSEK SKORLU 5", ""]
    if not rows:
        lines.append("Son 72 saatte doğrulanmış aktif aday yok.")
    for i, item in enumerate(rows, 1):
        meta = item.get("metadata") or {}
        social = meta.get("social") or {}
        fundamental = meta.get("fundamentals") or {}
        lines.extend([
            f"{i}. {item.get('symbol', '?')} — {int(item.get('score') or 0)}/100 · {item.get('stage') or '-'}",
            f"   24s %{float(meta.get('change_pct') or 0):+.1f} · MEXC hacim {_money(meta.get('quote_volume'))} · İvme {float(meta.get('volume_acceleration') or 0):.1f}x",
        ])
        if fundamental.get("status") == "READY":
            lines.extend([
                f"   Arz: dolaşan {_quantity(fundamental.get('circulating_supply'))} · toplam {_quantity(fundamental.get('total_supply'))} · max {_quantity(fundamental.get('max_supply'), missing='açıklanmamış/sınırsız')}",
                f"   ATH {_price(fundamental.get('ath_price_usd'))} ({_pct(fundamental.get('ath_change_pct'))}) · ATL {_price(fundamental.get('atl_price_usd'))} ({_pct(fundamental.get('atl_change_pct'))})",
            ])
        else:
            lines.append(
                f"   Arz ve ATH/ATL: {fundamental.get('status') or 'DATA_PENDING'}"
            )
        lines.extend([
            f"   Sosyal kapı {social.get('community_gate') or social.get('status') or '?'}",
            f"   Risk: {', '.join(item.get('risk_flags') or []) or 'belirgin sert risk yok'}",
            "",
        ])
    lines.append("Araştırma sıralamasıdır; otomatik işlem veya sermaye yetkisi vermez.")
    return "\n".join(lines)


def format_status(snapshot: dict[str, Any] | None) -> str:
    errors = (snapshot or {}).get("errors") or []
    generated = int((snapshot or {}).get("generated_at") or 0)
    age = max(0, int(time.time()) - generated) if generated else 0
    return "\n".join([
        "📊 SIGNAL BOT v5 CORE",
        "Mod: SHADOW / RADAR ONLY",
        f"Son tarama: {age} sn önce" if generated else "Son tarama: henüz yok",
        f"Likit evren: {int((snapshot or {}).get('liquid_universe_size') or 0)}/100",
        f"Long aday: {len((snapshot or {}).get('liquid_long_candidates') or [])}/3",
        f"Yeni güçlü: {len((snapshot or {}).get('listing_candidates') or [])}",
        f"Yeni izleme: {len((snapshot or {}).get('listing_filtered_candidates') or [])}",
        f"Hata: {', '.join(errors) if errors else 'yok'}",
        "Emir yetkisi: YOK",
    ])


def scan_once() -> dict[str, Any] | None:
    try:
        snapshot = ENGINE.scan_once().to_dict()
        with LOCK:
            STATE["snapshot"] = snapshot
            STATE["last_scan_at"] = snapshot.get("generated_at")
            STATE["last_error"] = None
            _save_state()
        return snapshot
    except Exception as exc:
        log.exception("Tarama başarısız")
        with LOCK:
            STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            _save_state()
        return None


def scanner_loop() -> None:
    while True:
        scan_once()
        time.sleep(CONFIG.scan_interval_seconds)


def _command(text: str) -> str:
    token = (text or "").strip().split(maxsplit=1)[0].lower()
    return {
        "/start": "PANEL",
        "/panel": "PANEL",
        "/longs": "LONGS",
        "/new": "NEW",
        "/listings": "NEW",
        "/status": "STATUS",
        "/scan": "SCAN",
    }.get(token, token.lstrip("/").upper())


def handle(action: str) -> None:
    snapshot = _snapshot()
    if action == "LONGS":
        send(format_longs(snapshot), keyboard=panel_keyboard())
    elif action == "NEW":
        send(format_new(snapshot), keyboard=panel_keyboard())
    elif action == "STATUS":
        send(format_status(snapshot), keyboard=panel_keyboard())
    elif action == "SCAN":
        send("🔄 Tarama başlatıldı.", keyboard=panel_keyboard())
        snapshot = scan_once()
        send(format_status(snapshot or _snapshot()), keyboard=panel_keyboard())
    else:
        send(
            "🎯 SIGNAL BOT v5 CORE\n\nYalnız iki fırsat motoru aktiftir:\n• MEXC Likit 100 Long İlk 3\n• MEXC New Listing Patlama Radarı",
            keyboard=panel_keyboard(),
        )


def telegram_loop() -> None:
    if not TOKEN or not CHAT_ID:
        log.warning("TOKEN/CHAT_ID yok; Telegram komut dinleyicisi başlamadı")
        return
    try:
        _api("setMyCommands", {"commands": COMMANDS})
    except Exception as exc:
        log.warning("Bot komutları ayarlanamadı: %s", exc)
    while True:
        try:
            offset = int(STATE.get("offset") or 0)
            updates = _api("getUpdates", {"offset": offset, "timeout": 20, "allowed_updates": ["message", "callback_query"]}) or []
            for update in updates:
                STATE["offset"] = max(int(STATE.get("offset") or 0), int(update.get("update_id") or 0) + 1)
                callback = update.get("callback_query") or {}
                message = update.get("message") or callback.get("message") or {}
                chat = str((message.get("chat") or {}).get("id") or "")
                if chat != CHAT_ID:
                    continue
                action = str(callback.get("data") or "") or _command(str(message.get("text") or ""))
                if callback.get("id"):
                    _api("answerCallbackQuery", {"callback_query_id": callback["id"]})
                handle(action)
            _save_state()
        except Exception as exc:
            log.warning("Telegram polling hatası: %s", exc)
            time.sleep(POLL_SECONDS)


@APP.get("/")
def health() -> Any:
    snapshot = _snapshot() or {}
    return jsonify({
        "ok": True,
        "service": "signal-bot-v5-core",
        "last_scan_at": snapshot.get("generated_at"),
        "errors": snapshot.get("errors") or [],
        "can_authorize_trade": False,
    })


def main() -> None:
    _load_state()
    threading.Thread(target=scanner_loop, name="mexc-core-scanner", daemon=True).start()
    threading.Thread(target=telegram_loop, name="telegram-command-loop", daemon=True).start()
    if STARTUP_MESSAGE:
        try:
            send("✅ Signal Bot v5 Core başladı. Eski karmaşık ajan devre dışı.", keyboard=panel_keyboard())
        except Exception:
            log.warning("Başlangıç mesajı gönderilemedi", exc_info=True)
    APP.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
