"""Signal Bot v5 Core: MEXC research radars without order authority."""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify

from acce_unified import UnifiedConfig, UnifiedRadarEngine, build_trade_universe
from acce_unified import long_alerts
from acce_unified.listing_fundamentals import ListingFundamentalMetricsProvider
from acce_unified.forward_ledger import ForwardLedger, status_text, update_records
from acce_unified.radar_gate import (
    evidence_line,
    evidence_status_text,
    family_disqualified,
    liquid_evidence_line,
    atr15_from_metadata,
    closed_change_24h,
    likit_quality_label,
    liquid_long_gate,
    listing_evidence_line,
    live_vs_replay_text,
    status_line,
    tactical_gate,
    universe_median_change,
)
from acce_unified.liquid_long import select_liquid_universe
from acce_unified.tactical_long_data import (
    TACTICAL_SYMBOLS,
    MexcTacticalMarketData,
    TacticalTimeframe,
)
from acce_unified.tactical_long_engine import TacticalLongEngine
from acce_unified.providers import MexcPublicProvider
from acce_unified.models import MexcListing

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
TACTICAL_SCAN_SECONDS = max(60, int(os.getenv("TACTICAL_SCAN_INTERVAL_SECONDS", "300")))
TACTICAL_ALERTS = os.getenv("TACTICAL_LONG_ALERTS_ENABLED", "1") == "1"
# Setups whose family failed the historical replay are REJECT (spec §3, §38,
# §40). User policy (2026-10-01): every long signal is pushed with its
# evidence label, stop levels and position share; set to 0 to silence REJECT
# setups. They are recorded in the forward ledger and the radar log either way.
TACTICAL_REJECTED_ALERTS = os.getenv("TACTICAL_REJECTED_ALERTS_ENABLED", "1") == "1"
# Likit-100 top 3: push a labelled alert with stop levels when a coin enters the list.
LIQUID_LONG_ALERTS = os.getenv("LIQUID_LONG_ALERTS_ENABLED", "1") == "1"

CONFIG = UnifiedConfig.from_env()
# A snapshot older than three scan cycles is stale evidence, not a current view.
MAIN_MAX_AGE_SECONDS = 3 * max(60, int(CONFIG.scan_interval_seconds))
TACTICAL_MAX_AGE_SECONDS = 3 * TACTICAL_SCAN_SECONDS
EVIDENCE_FOOTER = (
    "REJECT = geçmiş test negatif ya da kurulum kapısı başarısız; WATCH = setup tespit edildi, "
    "doğrulama eksik. Puan ve R/R olasılık değildir. Otomatik emir veya pozisyon yetkisi yoktur."
)
FUNDAMENTAL_PROVIDER = ListingFundamentalMetricsProvider(
    demo_api_key=os.getenv("COINGECKO_DEMO_API_KEY", ""),
    pro_api_key=os.getenv("COINGECKO_PRO_API_KEY", ""),
    timeout=CONFIG.request_timeout_seconds,
    cache_ttl_seconds=CONFIG.fundamental_cache_ttl_seconds,
    max_assets=CONFIG.fundamental_max_assets,
)
TRADE_UNIVERSE = build_trade_universe()
ENGINE = UnifiedRadarEngine(CONFIG, TRADE_UNIVERSE, fundamental_provider=FUNDAMENTAL_PROVIDER)
TACTICAL_DATA = MexcTacticalMarketData(timeout_seconds=CONFIG.request_timeout_seconds)
TACTICAL_ENGINE = TacticalLongEngine()
# Klines for stop levels and stop tracking (closed candles only; research use).
KLINES = MexcPublicProvider(timeout=CONFIG.request_timeout_seconds)
FORWARD_LEDGER = ForwardLedger(Path(os.getenv(
    "FORWARD_LEDGER_FILE", str(STATE_FILE.parent / "tactical_forward_ledger.json")
)))
APP = Flask(__name__)
HTTP = requests.Session()
LOCK = threading.RLock()
STATE: dict[str, Any] = {
    "offset": 0,
    "snapshot": None,
    "tactical_snapshot": None,
    "tactical_last_states": {},
    "last_error": None,
    "tactical_last_error": None,
    "forward_ledger_status": None,
    "forward_vs_replay": None,
    "forward_ledger_error": None,
    "radar_log": [],
}

COMMANDS = [
    {"command": "panel", "description": "Sade kontrol paneli"},
    {"command": "tactical", "description": "BTC ve ETH giriş/stop radarı"},
    {"command": "longs", "description": "MEXC Likit 100 Long İlk 3"},
    {"command": "new", "description": "Doğrulanmış MEXC yeni listeleri"},
    {"command": "radar", "description": "Radara giren long sinyalleri ve stop durumu"},
    {"command": "status", "description": "Tarama sağlığı ve veri durumu"},
    {"command": "scan", "description": "Şimdi yeniden tara"},
]


_URL_RE = re.compile(r"https?://\S+")


def _safe_error(exc: BaseException) -> str:
    """Exception type plus a URL-redacted, truncated message.

    Request exceptions can carry URLs with query parameters; nothing from
    them may reach state files, logs, Telegram or the public endpoint
    unredacted (AGENTS.md §3).
    """

    message = _URL_RE.sub("<url>", str(exc))
    if TOKEN:
        message = message.replace(TOKEN, "<token>")
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
    """Send a message. ``html_mode`` texts must escape every untrusted value (long_alerts.esc)."""

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
        [{"text": "₿ BTC / Ξ ETH Long", "callback_data": "TACTICAL"}],
        [{"text": "💧 Long İlk 3", "callback_data": "LONGS"}],
        [{"text": "📋 Radar kaydı", "callback_data": "RADAR"}],
        [{"text": "🆕 Yeni Listeler", "callback_data": "NEW"}],
        [
            {"text": "📊 Durum", "callback_data": "STATUS"},
            {"text": "🔄 Şimdi Tara", "callback_data": "SCAN"},
        ],
    ]}


def _snapshot() -> dict[str, Any] | None:
    value = STATE.get("snapshot")
    return value if isinstance(value, dict) else None


def _tactical_snapshot() -> dict[str, Any] | None:
    value = STATE.get("tactical_snapshot")
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
    if amount >= 1000:
        return f"${amount:,.0f}"
    if amount >= 1:
        return f"${amount:,.2f}"
    return f"${amount:.6f}".rstrip("0").rstrip(".")


def _pct(value: Any, *, missing: str = "?") -> str:
    try:
        return f"%{float(value):+.1f}"
    except (TypeError, ValueError):
        return missing


FUNDAMENTAL_KEYS = (
    "status", "circulating_supply", "total_supply", "max_supply", "circulation_pct",
    "ath_price_usd", "ath_change_pct", "ath_date", "atl_price_usd", "atl_change_pct", "atl_date",
)
# Title tells CoinGecko's resolver which project the symbol means (fake tokens reuse BTC/ETH).
TACTICAL_TITLES = {"BTCUSDT": "Bitcoin (BTC)", "ETHUSDT": "Ethereum (ETH)"}


def _fundamental_lines(fundamental: dict[str, Any] | None, *, indent: str = "   ") -> list[str]:
    """Supply and ATH/ATL facts from CoinGecko; missing data is named, never shown as zero."""

    f = fundamental or {}
    if f.get("status") != "READY":
        return [f"{indent}Arz ve ATH/ATL: {f.get('status') or 'DATA_PENDING'}"]
    supply = (
        f"{indent}Arz: dolaşan {_quantity(f.get('circulating_supply'))} · toplam {_quantity(f.get('total_supply'))} · "
        f"max {_quantity(f.get('max_supply'), missing='açıklanmamış/sınırsız')}"
    )
    if f.get("circulation_pct") is not None:
        supply += f" · dolaşımda %{float(f['circulation_pct']):.1f}"
    lines = [
        supply,
        f"{indent}ATH {_price(f.get('ath_price_usd'))} ({_pct(f.get('ath_change_pct'))}) · "
        f"ATL {_price(f.get('atl_price_usd'))} ({_pct(f.get('atl_change_pct'))})",
    ]
    dates = [f"{label} {str(f[key])[:10]}" for label, key in (("ATH", "ath_date"), ("ATL", "atl_date")) if f.get(key)]
    if dates:
        lines.append(f"{indent}Tarih: " + " · ".join(dates))
    return lines


def _fundamental_rows(fundamental: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The same facts as table rows for alerts; missing data is named, never shown as zero."""

    f = fundamental or {}
    if f.get("status") != "READY":
        return [("Arz/ATH", str(f.get("status") or "DATA_PENDING"))]
    circulating = _quantity(f.get("circulating_supply"))
    if f.get("circulation_pct") is not None:
        circulating += f" · %{float(f['circulation_pct']):.0f}"
    rows = [
        ("Dolaşan arz", circulating),
        ("Toplam arz", _quantity(f.get("total_supply"))),
        ("Max arz", _quantity(f.get("max_supply"), missing="açıklanmamış")),
    ]
    for label, prefix in (("ATH", "ath"), ("ATL", "atl")):
        rows.append((label, f"{_price(f.get(prefix + '_price_usd'))} · {_pct(f.get(prefix + '_change_pct'))}"))
        if f.get(prefix + "_date"):
            day = str(f[prefix + "_date"])[:10]
            rows.append((f"{label} tarihi", ".".join(reversed(day.split("-"))) if len(day) == 10 else day))
    return rows


def _tactical_fundamentals(symbols: Any) -> dict[str, dict[str, Any]]:
    """CoinGecko facts for BTC/ETH (cached by the provider); empty on any failure."""

    rows = [
        MexcListing(symbol=s[:-4], pair=s, title=TACTICAL_TITLES.get(s, f"{s[:-4]} ({s[:-4]})"), rank=1,
                    spot_status="OPEN", last_price=0.0, change_pct=0.0, quote_volume=0.0,
                    volume_acceleration=0.0, discovery_source="TACTICAL")
        for s in sorted({str(x).upper() for x in symbols}) if s.endswith("USDT")
    ]
    if not rows:
        return {}
    try:
        return dict(FUNDAMENTAL_PROVIDER.fetch_many(rows))
    except Exception as exc:
        log.warning("Taktik temel veri alınamadı: %s", _safe_error(exc))
        return {}


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
    now = int(time.time())
    for index, item in enumerate(rows, 1):
        meta = item.get("metadata") or {}
        metrics = meta.get("long_metrics") or {}
        fundamentals = meta.get("fundamentals") or {}
        decision = liquid_long_gate(
            item,
            market_regime=context.get("regime"),
            generated_at=snapshot.get("generated_at"),
            now=now,
            max_age_seconds=MAIN_MAX_AGE_SECONDS,
        )
        quality = _f1_label(str(item.get("symbol") or ""), meta, now)
        lines.extend([
            f"{index}. {item.get('symbol', '?')} — radar puanı {int(item.get('score') or 0)}/100 · {item.get('stage') or '-'}",
            f"   {status_line(decision)}",
            f"   Kalite filtresi (F1): {quality.text} — {quality.detail}",
            f"   1s %{float(metrics.get('change_1h_pct') or 0):+.1f} · 4s %{float(metrics.get('change_4h_pct') or 0):+.1f} · RSI {float(metrics.get('rsi14') or 0):.0f}",
            f"   Hacim ivmesi {float(metrics.get('volume_ratio') or 0):.1f}x · Spread {float(meta.get('spread_bps') or 0):.1f} bp",
            f"   MEXC 24s {_money(meta.get('quote_volume'))}",
            *_fundamental_lines(fundamentals),
            "",
        ])
    lines.append(liquid_evidence_line())
    lines.append("Radar puanı kalibre edilmemiş bir sıralamadır; işlem emri değildir.")
    lines.append(EVIDENCE_FOOTER)
    return "\n".join(lines)


def format_tactical(report: dict[str, Any] | None, *, fundamentals: dict[str, dict[str, Any]] | None = None) -> str:
    lines = ["₿ BTC / Ξ ETH — TAKTİK LONG RADARI", "MEXC Spot · SHADOW", ""]
    if not report:
        lines.extend(["Henüz geçerli radar taraması yok.", str(STATE.get("tactical_last_error") or "")])
        return "\n".join(line for line in lines if line)
    now = int(time.time())
    for item in report.get("assessments") or []:
        symbol = str(item.get("symbol") or "?")
        state = str(item.get("state") or "NO_LONG")
        setup = str(item.get("setup") or "-")
        structure = str(item.get("structure_4h") or "?")
        decision = tactical_gate(item, now=now, max_age_seconds=TACTICAL_MAX_AGE_SECONDS)
        lines.append(f"{symbol} — setup durumu {state}")
        lines.append(status_line(decision))
        lines.append(f"Yapı: {structure} · Setup: {setup}")
        if fundamentals is not None:
            lines.extend(_fundamental_lines(fundamentals.get(symbol), indent=""))
        plan = item.get("plan") or {}
        if plan:
            lines.extend([
                f"Giriş bölgesi: {_price(plan.get('entry_low'))} – {_price(plan.get('entry_high'))}",
                f"Teknik geçersizlik: {_price(plan.get('technical_invalidation'))}",
                f"Hard stop: {_price(plan.get('hard_stop'))}",
                f"Hedef 1: {_price(plan.get('target_1'))} · net R/R {float(plan.get('net_rr_1') or 0):.2f}",
                f"Hedef 2: {_price(plan.get('target_2'))} · net R/R {float(plan.get('net_rr_2') or 0):.2f}",
                evidence_line(item.get("setup")),
                "Sinyal bazında kalibre olasılık: YOK (yukarıdaki oran aile ortalamasıdır)",
            ])
        reasons = item.get("reasons") or []
        risks = item.get("risk_flags") or []
        evidence = item.get("evidence") or []
        if evidence:
            lines.append("Kanıt: " + ", ".join(evidence))
        if risks:
            lines.append("Risk: " + ", ".join(risks))
        if reasons:
            lines.append("Karar nedeni: " + ", ".join(reasons))
        lines.append("")
    lines.append("Plan araştırma çıktısıdır; otomatik emir veya pozisyon yetkisi yoktur.")
    lines.append(EVIDENCE_FOOTER)
    return "\n".join(lines)


def format_new(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return "🆕 MEXC NEW LISTING\n\nİlk tarama bekleniyor."
    rows = sorted(
        snapshot.get("listing_candidates") or [],
        key=lambda item: (
            int(item.get("score") or 0),
            float((item.get("metadata") or {}).get("quote_volume") or 0),
            str(item.get("symbol") or ""),
        ), reverse=True,
    )[:5]
    lines = ["🆕 MEXC NEW LISTING — ADAYLAR (kimliği doğrulandı, getirisi kanıtlanmadı)", ""]
    if not rows:
        lines.append("Son 72 saatte doğrulanmış aktif aday yok.")
    for index, item in enumerate(rows, 1):
        meta = item.get("metadata") or {}
        social = meta.get("social") or {}
        fundamental = meta.get("fundamentals") or {}
        lines.extend([
            f"{index}. {item.get('symbol', '?')} — {int(item.get('score') or 0)}/100 · {item.get('stage') or '-'}",
            f"   24s %{float(meta.get('change_pct') or 0):+.1f} · MEXC hacim {_money(meta.get('quote_volume'))} · İvme {float(meta.get('volume_acceleration') or 0):.1f}x",
        ])
        lines.extend(_fundamental_lines(fundamental))
        lines.extend([
            f"   Sosyal kapı {social.get('community_gate') or social.get('status') or '?'}",
            f"   Risk: {', '.join(item.get('risk_flags') or []) or 'belirgin sert risk yok'}",
            "",
        ])
    lines.append(listing_evidence_line())
    lines.append("Araştırma sıralamasıdır; otomatik işlem veya sermaye yetkisi vermez.")
    return "\n".join(lines)


def format_status(snapshot: dict[str, Any] | None) -> str:
    errors = (snapshot or {}).get("errors") or []
    generated = int((snapshot or {}).get("generated_at") or 0)
    tactical = _tactical_snapshot() or {}
    tactical_age = max(0, int(time.time()) - int(tactical.get("generated_at") or 0)) if tactical else 0
    age = max(0, int(time.time()) - generated) if generated else 0
    error_text = ", ".join(errors) if errors else str(STATE.get("last_error") or "yok")
    return "\n".join(line for line in [
        "📊 SIGNAL BOT v5 CORE",
        "Mod: SHADOW / RADAR ONLY",
        f"Ana tarama: {age} sn önce" if generated else "Ana tarama: henüz yok",
        f"Taktik radar: {tactical_age} sn önce" if tactical else "Taktik radar: henüz yok",
        f"Likit evren: {int((snapshot or {}).get('liquid_universe_size') or 0)}/100",
        f"Long aday: {len((snapshot or {}).get('liquid_long_candidates') or [])}/3",
        f"Yeni listeleme adayı (kimliği doğrulanmış): {len((snapshot or {}).get('listing_candidates') or [])}",
        f"Ana hata: {error_text}",
        f"Taktik hata: {STATE.get('tactical_last_error') or 'yok'}",
        evidence_status_text(),
        str(STATE.get("forward_ledger_status") or "İleriye dönük kayıt: henüz yok"),
        str(STATE.get("forward_vs_replay") or ""),
        _radar_status_line(),
        f"Kayıt hatası: {STATE.get('forward_ledger_error')}" if STATE.get("forward_ledger_error") else "",
        "Emir yetkisi: YOK",
    ] if line)


def _update_forward_ledger(snapshot: Any, new_items: list[dict[str, Any]]) -> None:
    """Advance the shadow track record; never breaks the radar, never overwrites a corrupt ledger."""

    try:
        records = FORWARD_LEDGER.load()
        m5 = {
            symbol: snapshot.candles[symbol][TacticalTimeframe.M5]
            for symbol in TACTICAL_SYMBOLS
        }
        updated = update_records(records, new_assessments=new_items, m5_by_symbol=m5)
        if updated != records:
            FORWARD_LEDGER.save(updated)
        STATE["forward_ledger_status"] = status_text(updated)
        STATE["forward_vs_replay"] = live_vs_replay_text(updated)
        STATE["forward_ledger_error"] = None
    except Exception as exc:
        error = _safe_error(exc)
        log.warning("İleriye dönük kayıt güncellenemedi: %s", error)
        STATE["forward_ledger_error"] = error


# ---------------------------------------------------------------------------
# Long alerts, stop levels and the radar log (acce_unified.long_alerts)
# ---------------------------------------------------------------------------


def _stop_plan_for(symbol: str, price: float, now: int) -> long_alerts.StopPlan | None:
    try:
        rows = KLINES.fetch_klines(symbol, long_alerts.STOP_INTERVAL_SECONDS, 40)
    except Exception as exc:
        log.warning("Stop için kline alınamadı (%s): %s", symbol, _safe_error(exc))
        return None
    return long_alerts.compute_stop_plan(rows, price, now=now)


def _track_radar(rows: list[dict[str, Any]], now: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Advance open entries with closed 15m candles; returns (updated entries, entries that hit the stop)."""

    updated, stopped = [], []
    for entry in rows:
        if entry.get("status") != long_alerts.OPEN:
            continue
        candles: list[Any] = []
        if now >= long_alerts.next_check_due(entry):
            try:
                candles = KLINES.fetch_klines(entry["symbol"], long_alerts.TRACK_INTERVAL_SECONDS,
                                              long_alerts.candles_needed(entry, now=now))
            except Exception as exc:
                log.warning("Radar takibi için kline alınamadı (%s): %s", entry["symbol"], _safe_error(exc))
        new, event = long_alerts.track_entry(entry, candles, now=now)
        if new != entry:
            updated.append(new)
        if event == long_alerts.STOPPED:
            stopped.append(new)
    return updated, stopped


def _merge_radar(fresh: list[dict[str, Any]], updated: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply tracking updates and append new entries under the lock; returns the entries appended."""

    with LOCK:
        rows = [dict(e) for e in STATE.get("radar_log") or []]
        by_id = {e["id"]: e for e in updated}
        rows = [by_id.get(e.get("id"), e) for e in rows]
        opened = []
        for entry in fresh:
            if long_alerts.can_open(rows, entry["source"], entry["symbol"], now=int(entry["opened_at"])):
                rows.append(entry)
                opened.append(entry)
        STATE["radar_log"] = long_alerts.trim(rows)
        _save_state()
    return opened


F1_LOCK = threading.Lock()
F1_CACHE: dict[str, Any] = {}


def _f1_closed_changes(now: int) -> dict[str, Any]:
    """Closed-candle 24h changes of the current MEXC top-100 and their median, once per 15m bar.

    The research feature used closed candles only; the ticker's 24h change
    includes the forming candle, so it is never used for the F1 label.
    Failures leave changes missing, which makes the label UNKNOWN.
    """

    bar = now - now % 900
    with F1_LOCK:
        if F1_CACHE.get("bar") == bar:
            return dict(F1_CACHE)
        changes: dict[str, float | None] = {}
        try:
            universe = select_liquid_universe(
                ENGINE.cex_provider.fetch_tickers(), size=CONFIG.liquid_universe_size,
                min_quote_volume=CONFIG.liquid_min_quote_volume, required_venue="MEXC",
            )
        except Exception as exc:
            log.warning("F1 için evren alınamadı: %s", _safe_error(exc))
            universe = []

        def one(symbol: str) -> tuple[str, float | None]:
            try:
                rows = KLINES.fetch_klines(symbol, 900, 98)
            except Exception:
                return symbol, None
            return symbol, closed_change_24h(rows, now=now)

        if universe:
            with ThreadPoolExecutor(max_workers=8) as pool:
                changes = dict(pool.map(one, [item.symbol for item in universe]))
        F1_CACHE.clear()
        F1_CACHE.update(bar=bar, changes=changes, median=universe_median_change(changes))
        return dict(F1_CACHE)


def _f1_label(symbol: str, meta: dict[str, Any], now: int) -> Any:
    closed = _f1_closed_changes(now)
    change = closed["changes"].get(symbol)
    if change is None and symbol not in closed["changes"]:
        try:
            change = closed_change_24h(KLINES.fetch_klines(symbol, 900, 98), now=now)
        except Exception:
            change = None
    return likit_quality_label(atr15_pct=atr15_from_metadata(meta), change_24h=change,
                               universe_median=closed["median"])


def _liquid_radar(snapshot: dict[str, Any]) -> None:
    now = int(time.time())
    with LOCK:
        rows = [dict(e) for e in STATE.get("radar_log") or []]
    context = snapshot.get("liquid_market_context") or {}
    fresh: list[dict[str, Any]] = []
    for index, item in enumerate((snapshot.get("liquid_long_candidates") or [])[:3], 1):
        symbol = str(item.get("symbol") or "").upper()
        if not symbol or not long_alerts.can_open(rows + fresh, "LIKIT100", symbol, now=now):
            continue
        meta = item.get("metadata") or {}
        price = float(meta.get("last_price") or 0)
        decision = liquid_long_gate(item, market_regime=context.get("regime"), generated_at=snapshot.get("generated_at"),
                                    now=now, max_age_seconds=MAIN_MAX_AGE_SECONDS)
        entry = long_alerts.open_entry(source="LIKIT100", symbol=symbol, now=now, gate_status=decision.status,
                                       detail=status_line(decision), plan=_stop_plan_for(symbol, price, now),
                                       entry_price=price or None)
        fundamentals = dict(meta.get("fundamentals") or {})
        quality = _f1_label(symbol, meta, now)
        entry.update(rank=index, radar_score=int(item.get("score") or 0),
                     fundamentals={key: fundamentals.get(key) for key in FUNDAMENTAL_KEYS},
                     quality=quality.status, quality_text=quality.text, quality_detail=quality.detail,
                     quality_inputs={"atr15_pct": quality.atr15_pct, "rel_24h": quality.rel_24h})
        fresh.append(entry)
    updated, stopped = _track_radar(rows, now)
    opened = _merge_radar(fresh, updated)
    messages = []
    if LIQUID_LONG_ALERTS:
        for entry in opened:
            icon = "⛔" if entry["gate_status"] == "REJECT" else "👀"
            messages.append(long_alerts.alert_text(
                entry, icon=icon, title=f"LONG SİNYALİ · Likit-100 #{entry['rank']}", evidence=liquid_evidence_line(),
                head_rows=[("Radar puanı", f"{entry['radar_score']}/100"), ("Kalite (F1)", entry["quality_text"])],
                fact_rows=_fundamental_rows(entry.get("fundamentals")),
                notes=[f"Kalite filtresi: {entry['quality_detail']}"],
            ))
    for entry in stopped:
        if (LIQUID_LONG_ALERTS if entry["source"] == "LIKIT100" else TACTICAL_ALERTS):
            messages.append(long_alerts.stop_alert_text(entry))
    for message in messages:
        try:
            send(message, keyboard=panel_keyboard(), html_mode=True)
        except Exception:
            log.warning("Long uyarısı gönderilemedi", exc_info=True)


def _radar_status_line() -> str:
    with LOCK:
        rows = [dict(e) for e in STATE.get("radar_log") or []]
    open_count = sum(e.get("status") == long_alerts.OPEN for e in rows)
    return f"Radar kaydı: {open_count} açık · " + long_alerts.summary_line(rows, now=int(time.time()))


def scan_once() -> dict[str, Any] | None:
    try:
        snapshot = ENGINE.scan_once().to_dict()
    except Exception as exc:
        error = _safe_error(exc)
        log.warning("Tarama başarısız: %s", error)
        with LOCK:
            STATE["last_error"] = error
            _save_state()
        return None
    with LOCK:
        STATE["snapshot"] = snapshot
        STATE["last_error"] = None
        _save_state()
    try:
        _liquid_radar(snapshot)
    except Exception as exc:  # the radar log must never break the scan
        log.warning("Radar kaydı güncellenemedi: %s", _safe_error(exc))
    return snapshot


def _tactical_radar_entry(market: Any, item: dict[str, Any], decision: Any, now: int) -> long_alerts.StopPlan | None:
    """Record a tactical setup in the radar log with the engine's own levels (call under LOCK)."""

    symbol = str(item.get("symbol") or "").upper()
    levels = item.get("plan") or {}
    try:
        m5 = market.candles[symbol][TacticalTimeframe.M5]
        last = m5[-1]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    plan = long_alerts.plan_from_levels(
        float(last.close), float(levels.get("technical_invalidation") or 0), float(levels.get("hard_stop") or 0),
        last_candle_open=int(last.open_time),
    )
    rows = [dict(e) for e in STATE.get("radar_log") or []]
    if long_alerts.can_open(rows, "TAKTIK", symbol, now=now):
        entry = long_alerts.open_entry(source="TAKTIK", symbol=symbol, now=now, gate_status=decision.status,
                                       detail=status_line(decision), plan=plan, entry_price=float(last.close))
        entry["setup"] = str(item.get("setup") or "-")
        STATE["radar_log"] = long_alerts.trim(rows + [entry])
    return plan


SETUP_LABELS = {
    "TREND_PULLBACK": "Trend geri çekilme",
    "BREAKOUT_RETEST": "Kırılım retest",
    "RANGE_RECLAIM": "Aralık geri alma",
    "LIQUIDITY_SWEEP_RECLAIM": "Likidite süpürme",
}
STRUCTURE_LABELS = {"BULLISH": "yükseliş", "BEARISH": "düşüş", "RANGE_OR_TRANSITION": "yatay"}


def _tactical_alert(item: dict[str, Any], decision: Any, plan: long_alerts.StopPlan | None, now: int) -> Any:
    """Alert builder; the fundamentals rows are fetched later, outside the state lock."""

    symbol = str(item.get("symbol") or "?")
    setup = str(item.get("setup") or "-")
    levels = item.get("plan") or {}
    icon = "⛔" if decision.status == "REJECT" else "👀"
    entry = {"symbol": symbol, "source": "TAKTIK", "opened_at": now, "gate_status": decision.status,
             "detail": status_line(decision), "plan": plan.to_dict() if plan else None}
    zone = ("Giriş", f"{long_alerts._num(levels['entry_low'])} – {long_alerts._num(levels['entry_high'])}") \
        if levels.get("entry_low") and levels.get("entry_high") else None
    targets = [
        (f"Hedef {k}", f"{long_alerts._num(levels[f'target_{k}'])} · R/R {float(levels.get(f'net_rr_{k}') or 0):.1f}")
        for k in (1, 2) if levels.get(f"target_{k}")
    ]
    risks = [str(r) for r in item.get("risk_flags") or []]
    head = [("Setup", SETUP_LABELS.get(setup, setup)),
            ("Durum", f"{item.get('state')} · 4s {STRUCTURE_LABELS.get(str(item.get('structure_4h')), '?')}")]

    def render(fact_rows: list[tuple[str, str]]) -> str:
        return long_alerts.alert_text(
            entry, icon=icon, title="LONG SİNYALİ · Taktik", evidence=evidence_line(setup), head_rows=head,
            zone=zone, targets=targets, fact_rows=fact_rows, invalidation="motorun teknik seviyesi",
            notes=[*([f"Risk: {', '.join(risks)}"] if risks else []),
                   "Sinyal bazında kalibre olasılık: YOK (aşağıdaki oran aile ortalamasıdır)."],
        )
    return render


def tactical_scan_once(*, emit_alerts: bool = True) -> dict[str, Any] | None:
    try:
        market = TACTICAL_DATA.snapshot()
        report = TACTICAL_ENGINE.analyze(market).to_dict()
    except Exception as exc:
        error = _safe_error(exc)
        log.warning("Taktik radar taraması başarısız: %s", error)
        with LOCK:
            STATE["tactical_last_error"] = error
            _save_state()
        return None
    alerts: list[tuple[str | None, Any]] = []
    now = int(time.time())
    with LOCK:
        previous = dict(STATE.get("tactical_last_states") or {})
        current: dict[str, str] = {}
        new_setups: list[dict[str, Any]] = []
        for item in report.get("assessments") or []:
            symbol = str(item.get("symbol") or "?")
            state = str(item.get("state") or "NO_LONG")
            setup = str(item.get("setup") or "-")
            current[symbol] = f"{state}:{setup}"
            old = previous.get(symbol)
            if old != current[symbol] and state in {"READY", "TRIGGERED"}:
                new_setups.append(item)  # the ledger records REJECT setups too: live check of the replay
                decision = tactical_gate(item, now=now, max_age_seconds=TACTICAL_MAX_AGE_SECONDS)
                plan = _tactical_radar_entry(market, item, decision, now)
                if decision.status == "REJECT" and not TACTICAL_REJECTED_ALERTS:
                    continue
                alerts.append((symbol, _tactical_alert(item, decision, plan, now)))
            elif old and old.split(":", 1)[0] in {"READY", "TRIGGERED"} and state == "NO_LONG":
                if family_disqualified(old.split(":", 1)[1]) and not TACTICAL_REJECTED_ALERTS:
                    continue  # its opening alert was never sent
                reasons = ", ".join(str(r) for r in item.get("reasons") or ["NO_VALID_SETUP"])
                alerts.append((None, f"⚠️ <b>{long_alerts.esc(symbol)}</b> Long formasyonu bozuldu.\n"
                                     f"Neden: {long_alerts.esc(reasons)}"))
        _update_forward_ledger(market, new_setups)
        STATE["tactical_snapshot"] = report
        STATE["tactical_last_states"] = current
        STATE["tactical_last_error"] = None
        _save_state()
    if emit_alerts and TACTICAL_ALERTS and alerts:
        facts = _tactical_fundamentals(symbol for symbol, _ in alerts if symbol)
        for symbol, alert in alerts:
            text = alert(_fundamental_rows(facts.get(symbol))) if callable(alert) else alert
            try:
                send(text, keyboard=panel_keyboard(), html_mode=True)
            except Exception:
                log.warning("Taktik uyarı gönderilemedi", exc_info=True)
    return report


def scanner_loop() -> None:
    while True:
        scan_once()
        time.sleep(CONFIG.scan_interval_seconds)


def tactical_scanner_loop() -> None:
    while True:
        tactical_scan_once()
        time.sleep(TACTICAL_SCAN_SECONDS)


def _command(text: str) -> str:
    token = (text or "").strip().split(maxsplit=1)[0].lower()
    return {
        "/start": "PANEL", "/panel": "PANEL", "/tactical": "TACTICAL",
        "/btceth": "TACTICAL", "/longs": "LONGS", "/new": "NEW",
        "/listings": "NEW", "/status": "STATUS", "/scan": "SCAN", "/radar": "RADAR",
    }.get(token, token.lstrip("/").upper())


def handle(action: str) -> None:
    snapshot = _snapshot()
    if action == "TACTICAL":
        refreshed = tactical_scan_once(emit_alerts=False)
        report = refreshed or _tactical_snapshot()
        facts = _tactical_fundamentals(str(a.get("symbol") or "") for a in (report or {}).get("assessments") or [])
        send(format_tactical(report, fundamentals=facts), keyboard=panel_keyboard())
    elif action == "LONGS":
        send(format_longs(snapshot), keyboard=panel_keyboard())
    elif action == "NEW":
        send(format_new(snapshot), keyboard=panel_keyboard())
    elif action == "RADAR":
        with LOCK:
            rows = [dict(e) for e in STATE.get("radar_log") or []]
        send(long_alerts.format_radar(rows, now=int(time.time())), keyboard=panel_keyboard(), html_mode=True)
    elif action == "STATUS":
        send(format_status(snapshot), keyboard=panel_keyboard())
    elif action == "SCAN":
        send("🔄 Tüm radar taramaları başlatıldı.", keyboard=panel_keyboard())
        refreshed = scan_once()
        tactical_scan_once()
        send(format_status(refreshed or _snapshot()), keyboard=panel_keyboard())
    else:
        send(
            "🎯 SIGNAL BOT v5 CORE\n\nAktif araştırma motorları:\n• BTC/ETH Taktik Long giriş ve stop radarı\n• MEXC Likit 100 Long İlk 3\n• Doğrulanmış MEXC yeni listelemeleri",
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
                action = str(callback.get("data") or "") or _command(str(message.get("text") or ""))
                if callback.get("id"):
                    _api("answerCallbackQuery", {"callback_query_id": callback["id"]})
                handle(action)
            _save_state()
        except Exception as exc:
            log.warning("Telegram polling hatası: %s", exc)
            time.sleep(POLL_SECONDS)


def _age_ok(generated_at: Any, max_age: int, now: int) -> bool:
    try:
        ts = int(generated_at)
    except (TypeError, ValueError):
        return False
    return ts > 0 and 0 <= now - ts <= max_age


def _error_code(value: Any) -> str | None:
    # Public endpoint: expose only the exception class, never message text.
    if not value:
        return None
    return str(value).split(": ", 1)[0].split()[0][:60]


@APP.get("/")
def health() -> Any:
    snapshot = _snapshot() or {}
    tactical = _tactical_snapshot() or {}
    now = int(time.time())
    main_fresh = _age_ok(snapshot.get("generated_at"), MAIN_MAX_AGE_SECONDS, now)
    tactical_fresh = _age_ok(tactical.get("generated_at"), TACTICAL_MAX_AGE_SECONDS, now)
    return jsonify({
        # Healthy means fresh artifacts exist, not merely "no error recorded".
        "ok": (
            main_fresh and tactical_fresh
            and STATE.get("last_error") is None and STATE.get("tactical_last_error") is None
        ),
        "service": "signal-bot-v5-core",
        "main_scan_fresh": main_fresh,
        "tactical_scan_fresh": tactical_fresh,
        "last_scan_at": snapshot.get("generated_at"),
        "tactical_last_scan_at": tactical.get("generated_at"),
        "errors": [_error_code(e) for e in snapshot.get("errors") or []],
        "last_error": _error_code(STATE.get("last_error")),
        "tactical_last_error": _error_code(STATE.get("tactical_last_error")),
        "can_authorize_trade": False,
    })


def main() -> None:
    _load_state()
    threading.Thread(target=scanner_loop, name="mexc-core-scanner", daemon=True).start()
    threading.Thread(target=tactical_scanner_loop, name="btc-eth-tactical-scanner", daemon=True).start()
    threading.Thread(target=telegram_loop, name="telegram-command-loop", daemon=True).start()
    if STARTUP_MESSAGE:
        try:
            send("✅ Signal Bot v5 Core başladı.", keyboard=panel_keyboard())
        except Exception:
            log.warning("Başlangıç mesajı gönderilemedi", exc_info=True)
    APP.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
