"""Long radar alerts: stop levels, a radar log and stop tracking. Research only.

What the user sees for every long signal (Likit-100 top 3 and the BTC/ETH
tactical radar): the price at the alert, a technical invalidation, a hard
stop, the stop distance and the position size that risks 1% of the account,
plus the signal's evidence status. Every signal enters a radar log that
records what happened afterwards: stop reached or 72 hours elapsed.

Rules (fixed, closed candles only, nothing is moved after the alert):

- **Technical invalidation:** the lowest low of the last 12 closed 1h candles.
- **Hard stop:** that low minus 0.5 x ATR(14, 1h), and at least 1.5 x ATR
  below the alert price, so a stop never sits inside one candle of noise.
- **Tracking:** a stop counts as reached when a closed 15m candle that opened
  after the alert trades at or below it. The exit is the stop, or the
  candle's open when it gapped below the stop. After 72 hours the entry
  closes at the last closed price.

A stop limits a loss; it does not turn a signal with a negative history into
a positive one. No order authority anywhere (AGENTS.md §4, §8, §10).
"""

from __future__ import annotations

import html
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

STOP_INTERVAL_SECONDS = 3_600
STOP_LOOKBACK = 12            # closed 1h candles for the structural low
ATR_PERIOD = 14
BUFFER_ATR = 0.5
MIN_STOP_ATR = 1.5
WIDE_STOP_PCT = 12.0          # above this the alert warns that the stop is very wide
RISK_PER_TRADE_PCT = 1.0
TRACK_INTERVAL_SECONDS = 900
TRACK_HOURS = 72
COOLDOWN_HOURS = 12
STALE_GRACE_HOURS = 6        # expire with the last known price if tracking data never arrives
LOG_LIMIT = 200
TSI = timezone(timedelta(hours=3))   # Türkiye saati (UTC+3, yaz saati yok)

OPEN, STOPPED, EXPIRED = "OPEN", "STOPPED", "EXPIRED"
SOURCE_LABELS = {"LIKIT100": "Likit-100", "TAKTIK": "Taktik"}

Candle = tuple[int, float, float, float, float]     # open_time, open, high, low, close


def closed_candles(rows: Iterable[Any], *, interval_seconds: int, now: int) -> list[Candle]:
    """Binance-style kline rows reduced to valid candles that had closed by ``now``."""

    out: dict[int, Candle] = {}
    for row in rows or []:
        try:
            t = int(row[0])
            t = t // 1000 if t > 10_000_000_000 else t
            o, h, low, c = (float(row[i]) for i in (1, 2, 3, 4))
        except (TypeError, ValueError, IndexError):
            continue
        if not all(math.isfinite(v) and v > 0 for v in (o, h, low, c)):
            continue
        if h < max(o, c) or low > min(o, c) or t + interval_seconds > now:
            continue
        out.setdefault(t, (t, o, h, low, c))
    return [out[t] for t in sorted(out)]


@dataclass(frozen=True)
class StopPlan:
    entry_price: float
    technical_invalidation: float
    hard_stop: float
    stop_pct: float
    atr_pct: float | None
    position_pct: float           # share of the account that risks RISK_PER_TRADE_PCT at the hard stop
    warnings: tuple[str, ...]
    last_candle_open: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_stop_plan(rows_1h: Iterable[Any], entry_price: float, *, now: int) -> StopPlan | None:
    """Stop levels from closed 1h candles; None when the data cannot support a plan (fail closed)."""

    if not (isinstance(entry_price, (int, float)) and math.isfinite(entry_price) and entry_price > 0):
        return None
    candles = closed_candles(rows_1h, interval_seconds=STOP_INTERVAL_SECONDS, now=now)
    if len(candles) < max(STOP_LOOKBACK, ATR_PERIOD + 1):
        return None
    true_ranges = [
        max(h - low, abs(h - candles[i - 1][4]), abs(low - candles[i - 1][4]))
        for i, (_, _, h, low, _) in enumerate(candles) if i > 0
    ]
    atr = statistics.mean(true_ranges[-ATR_PERIOD:])
    structural_low = min(c[3] for c in candles[-STOP_LOOKBACK:])
    hard_stop = min(structural_low - BUFFER_ATR * atr, entry_price - MIN_STOP_ATR * atr)
    if not (atr > 0 and hard_stop > 0):
        return None
    warnings = []
    if structural_low >= entry_price:
        warnings.append("fiyat son 12 saatin dibinin altında: yapı zaten bozulmuş")
    stop_pct = (entry_price - hard_stop) / entry_price * 100.0
    if stop_pct > WIDE_STOP_PCT:
        warnings.append(f"stop mesafesi %{stop_pct:.1f}: çok geniş, pozisyon çok küçük olmalı")
    return StopPlan(
        entry_price=float(entry_price), technical_invalidation=float(structural_low), hard_stop=float(hard_stop),
        stop_pct=stop_pct, atr_pct=atr / entry_price * 100.0,
        position_pct=min(100.0, RISK_PER_TRADE_PCT / stop_pct * 100.0), warnings=tuple(warnings),
        last_candle_open=candles[-1][0],
    )


def plan_from_levels(entry_price: float, technical_invalidation: float, hard_stop: float,
                     *, last_candle_open: int) -> StopPlan | None:
    """Wrap levels another engine already fixed (the tactical radar) with the same sizing rule."""

    values = (entry_price, technical_invalidation, hard_stop)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values):
        return None
    if hard_stop >= entry_price:
        return None
    stop_pct = (entry_price - hard_stop) / entry_price * 100.0
    warnings = []
    if stop_pct > WIDE_STOP_PCT:
        warnings.append(f"stop mesafesi %{stop_pct:.1f}: çok geniş, pozisyon çok küçük olmalı")
    return StopPlan(
        entry_price=float(entry_price), technical_invalidation=float(technical_invalidation),
        hard_stop=float(hard_stop), stop_pct=stop_pct, atr_pct=None,
        position_pct=min(100.0, RISK_PER_TRADE_PCT / stop_pct * 100.0), warnings=tuple(warnings),
        last_candle_open=int(last_candle_open),
    )


# ---------------------------------------------------------------------------
# Radar log (plain dicts so it persists in the bot state file)
# ---------------------------------------------------------------------------


def can_open(log: Sequence[Mapping[str, Any]], source: str, symbol: str, *, now: int) -> bool:
    """One open entry per source and symbol, and no re-entry within the cooldown."""

    for entry in log:
        if entry.get("source") != source or entry.get("symbol") != symbol:
            continue
        if entry.get("status") == OPEN or now - int(entry.get("opened_at") or 0) < COOLDOWN_HOURS * 3_600:
            return False
    return True


def open_entry(*, source: str, symbol: str, now: int, gate_status: str, detail: str,
               plan: StopPlan | None, entry_price: float | None) -> dict[str, Any]:
    price = plan.entry_price if plan else entry_price
    return {
        "id": f"{source}:{symbol}:{now}",
        "source": source,
        "symbol": symbol,
        "opened_at": now,
        "gate_status": gate_status,
        "detail": detail,
        "entry_price": price,
        "plan": plan.to_dict() if plan else None,
        "status": OPEN if plan else EXPIRED,    # without a stop plan there is nothing to track
        "closed_at": None if plan else now,
        "exit_price": None,
        "result_pct": None,
        "last_price": price,
        "low_since": price,
        "high_since": price,
        "checked_until": now,
        "can_authorize_trade": False,
    }


def track_entry(entry: Mapping[str, Any], rows_15m: Iterable[Any], *, now: int) -> tuple[dict[str, Any], str | None]:
    """Advance an OPEN entry with 15m candles that opened after the alert; returns (entry, event)."""

    entry = dict(entry)
    plan = entry.get("plan")
    if entry.get("status") != OPEN or not plan:
        return entry, None
    stop, price = float(plan["hard_stop"]), float(entry["entry_price"])
    expires_at = int(entry["opened_at"]) + TRACK_HOURS * 3_600
    start = int(entry["checked_until"])
    candles = [c for c in closed_candles(rows_15m, interval_seconds=TRACK_INTERVAL_SECONDS, now=now)
               if c[0] >= start and c[0] + TRACK_INTERVAL_SECONDS <= expires_at]
    for t, o, h, low, c in candles:
        entry["low_since"] = min(float(entry["low_since"]), low)
        entry["high_since"] = max(float(entry["high_since"]), h)
        entry["last_price"] = c
        entry["checked_until"] = t + TRACK_INTERVAL_SECONDS
        if low <= stop:
            exit_price = min(o, stop)
            entry.update(status=STOPPED, closed_at=t + TRACK_INTERVAL_SECONDS, exit_price=exit_price,
                         result_pct=(exit_price / price - 1.0) * 100.0)
            return entry, STOPPED
    complete = int(entry["checked_until"]) >= expires_at - TRACK_INTERVAL_SECONDS
    if now >= expires_at and (complete or now >= expires_at + STALE_GRACE_HOURS * 3_600):
        last = float(entry["last_price"])
        entry.update(status=EXPIRED, closed_at=expires_at, exit_price=last, result_pct=(last / price - 1.0) * 100.0)
        if not complete:
            entry["note"] = "takip verisi eksik; son bilinen kapanış kullanıldı"
        return entry, EXPIRED
    return entry, None


def next_check_due(entry: Mapping[str, Any], *, settle_seconds: int = 30) -> int:
    """When the first 15m candle that opened at or after the last check will have closed."""

    checked = int(entry.get("checked_until") or 0)
    first_open = -(-checked // TRACK_INTERVAL_SECONDS) * TRACK_INTERVAL_SECONDS
    return first_open + TRACK_INTERVAL_SECONDS + settle_seconds


def candles_needed(entry: Mapping[str, Any], *, now: int, cap: int = 1000) -> int:
    """15m candles to request so a restart gap since the last check is covered."""

    return max(4, min(cap, (now - int(entry.get("checked_until") or now)) // TRACK_INTERVAL_SECONDS + 3))


def trim(log: Sequence[Mapping[str, Any]], limit: int = LOG_LIMIT) -> list[dict[str, Any]]:
    """Keep every open entry and the newest closed ones."""

    rows = [dict(e) for e in log]
    if len(rows) <= limit:
        return rows
    open_rows = [e for e in rows if e.get("status") == OPEN]
    closed = sorted((e for e in rows if e.get("status") != OPEN), key=lambda e: int(e.get("opened_at") or 0))
    keep = closed[-max(0, limit - len(open_rows)):] if limit > len(open_rows) else []
    return sorted(open_rows + keep, key=lambda e: int(e.get("opened_at") or 0))


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def _clock(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=TSI).strftime("%d.%m %H:%M")


def _num(value: float) -> str:
    value = float(value)
    if value >= 10_000:
        return f"{value:,.0f}"
    if value >= 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value <= 0:
        return f"{value:g}"
    return f"{value:.{min(12, 3 - math.floor(math.log10(value)))}f}"   # 4 significant digits


# Telegram shows a <pre> block in a monospace font; rows stay within ~32
# characters so a phone screen does not wrap them.
LABEL_WIDTH = 12
RULE = "─" * 24
FOOTER = "Stop zararı sınırlar; geçmişi negatif bir sinyali kârlı yapmaz. Emir yetkisi yok, karar senin."


def esc(value: Any) -> str:
    """Escape untrusted text for Telegram HTML (symbols, provider fields, evidence lines)."""

    return html.escape(str(value), quote=False)


Row = tuple[str, str] | None


def table(rows: Iterable[Row]) -> str:
    """Two-column monospace block; ``None`` draws a rule. Everything inside is escaped."""

    lines = [RULE if row is None else f"{row[0]:<{LABEL_WIDTH}}{row[1]}".rstrip() for row in rows]
    return "<pre>" + esc("\n".join(lines)) + "</pre>"


def plan_rows(plan: Mapping[str, Any] | None, *, zone: Row = None, targets: Sequence[Row] = ()) -> list[Row]:
    """Price, entry zone (tactical), invalidation, hard stop, targets (tactical), ATR, position share."""

    if not plan:
        return [("Stop", "hesaplanamadı"), *([zone] if zone else []), *targets]
    rows: list[Row] = [("Fiyat", _num(plan["entry_price"]))]
    if zone:
        rows.append(zone)
    rows += [
        ("Geçersizlik", _num(plan["technical_invalidation"])),
        ("Hard stop", f"{_num(plan['hard_stop'])}  −%{plan['stop_pct']:.1f}"),
        *targets,
    ]
    if plan.get("atr_pct") is not None:
        rows.append(("ATR (1s)", f"%{plan['atr_pct']:.1f}"))
    rows.append(("Pozisyon", f"%{plan['position_pct']:.0f} sermaye"))
    return rows


def plan_notes(plan: Mapping[str, Any] | None, *, invalidation: str = "son 12 saatin dibi") -> list[str]:
    if not plan:
        return ["⚠️ Stop hesaplanamadı (veri yok ya da fiyat stop'un altında): bu sinyalle işlem yapılmamalı."]
    notes = [
        f"Geçersizlik: {invalidation}. Pozisyon: hard stop'ta sermayenin "
        f"%{RISK_PER_TRADE_PCT:g}'i riske girecek büyüklük."
    ]
    return notes + [f"⚠️ {w}" for w in plan.get("warnings") or ()]


def gate_reason(entry: Mapping[str, Any]) -> str:
    """The gate's own reason (``status_line``: failing checks or missing evidence), never a paraphrase.

    A REJECT can come from stale data, the regime, the stop geometry or a
    negative history; showing one fixed meaning for all of them would be false.
    """

    detail = str(entry.get("detail") or "")
    status = str(entry.get("gate_status") or "")
    prefix = f"Durum: {status}"
    if detail.startswith(prefix):
        detail = detail[len(prefix):].lstrip(" —-")
    return detail.strip()


def alert_text(entry: Mapping[str, Any], *, icon: str, title: str, evidence: str, head_rows: Sequence[Row] = (),
               zone: Row = None, targets: Sequence[Row] = (), fact_rows: Sequence[Row] = (),
               notes: Sequence[str] = (), invalidation: str = "son 12 saatin dibi") -> str:
    """Telegram HTML for one long signal: header, one table, then evidence and caveats."""

    source = SOURCE_LABELS.get(str(entry.get("source")), str(entry.get("source")))
    plan = entry.get("plan")
    rows: list[Row] = [("Kapı", str(entry.get("gate_status") or "?")), *head_rows, None,
                       *plan_rows(plan, zone=zone, targets=targets)]
    if fact_rows:
        rows += [None, *fact_rows]
    parts = [
        f"{esc(icon)} <b>{esc(title)}</b>",
        f"<b>{esc(entry['symbol'])}</b> · {esc(source)} · {esc(_clock(int(entry['opened_at'])))} TSİ",
        table(rows),
        *([f"<b>Kapı nedeni:</b> {esc(reason)}"] if (reason := gate_reason(entry)) else []),
        *(esc(n) for n in (*plan_notes(plan, invalidation=invalidation), *notes) if n),
        f"<i>{esc(evidence)}</i>",
        esc(FOOTER),
    ]
    return "\n".join(parts)


def stop_alert_text(entry: Mapping[str, Any]) -> str:
    source = SOURCE_LABELS.get(str(entry.get("source")), str(entry.get("source")))
    plan = entry.get("plan") or {}
    rows: list[Row] = [
        ("Uyarı", f"{_clock(int(entry['opened_at']))} TSİ"),
        ("Giriş", _num(entry["entry_price"])),
        ("Hard stop", _num(plan["hard_stop"]) if plan else "?"),
        ("Çıkış", _num(entry["exit_price"])),
        ("Sonuç", f"%{float(entry['result_pct']):+.1f}"),
    ]
    return "\n".join([
        f"🛑 <b>STOP · {esc(entry['symbol'])}</b> · {esc(source)}",
        table(rows),
        esc("Çıkış stop fiyatından; mum stop'un altında açıldıysa açılış fiyatından. Maliyet hariç."),
    ])


def _status_label(entry: Mapping[str, Any]) -> str:
    status = entry.get("status")
    if status == OPEN:
        last, price = float(entry.get("last_price") or 0), float(entry.get("entry_price") or 0)
        change = (last / price - 1.0) * 100.0 if price else 0.0
        return f"açık %{change:+.1f}"
    if status == STOPPED:
        return f"stop %{float(entry['result_pct']):+.1f}"
    if entry.get("plan") is None:
        return "stop yok"
    mark = "*" if entry.get("note") else ""
    return f"72s %{float(entry['result_pct']):+.1f}{mark}"


def summary_line(log: Sequence[Mapping[str, Any]], *, now: int, days: int = 30) -> str:
    closed = [e for e in log if e.get("status") in {STOPPED, EXPIRED} and e.get("result_pct") is not None
              and now - int(e.get("opened_at") or 0) <= days * 86_400]
    if not closed:
        return f"Son {days} gün: kapanmış kayıt yok."
    results = [float(e["result_pct"]) for e in closed]
    stopped = sum(e.get("status") == STOPPED for e in closed)
    return (
        f"Son {days} gün: {len(closed)} kapanmış kayıt · {stopped} stop · ortalama %{statistics.mean(results):+.1f} · "
        f"medyan %{statistics.median(results):+.1f} (maliyet hariç; az sayıda kayıtla anlamlı değildir)"
    )


QUALITY_MARKS = {"AVOID": "kaçın", "PASSED": "geçti", "UNKNOWN": "?"}


def quality_summary_line(log: Sequence[Mapping[str, Any]], *, now: int, days: int = 30) -> str:
    """Live check of the F1 label: closed Likit-100 entries by label (raw, before costs)."""

    groups: dict[str, list[float]] = {}
    for e in log:
        if (e.get("source") == "LIKIT100" and e.get("quality") in {"AVOID", "PASSED"}
                and e.get("status") in {STOPPED, EXPIRED} and e.get("result_pct") is not None
                and now - int(e.get("opened_at") or 0) <= days * 86_400):
            groups.setdefault(str(e["quality"]), []).append(float(e["result_pct"]))
    if not groups:
        return ""
    parts = [f"{QUALITY_MARKS[k]} {len(v)} kayıt ort. %{statistics.mean(v):+.1f}"
             for k, v in sorted(groups.items(), key=lambda kv: kv[0] != "PASSED")]
    return f"Son {days} gün F1 etiketine göre: " + " · ".join(parts) + " (maliyet hariç; az kayıtla anlamlı değildir)"


RADAR_ROWS = 30   # three lines each; keeps /radar well under Telegram's 4096 characters


def format_radar(log: Sequence[Mapping[str, Any]], *, now: int, hours: int = TRACK_HOURS) -> str:
    """Telegram HTML: one block per signal (time, coin, source, result; then entry and stop)."""

    recent = [e for e in log if e.get("status") == OPEN or now - int(e.get("opened_at") or 0) <= hours * 3_600]
    parts = [f"📋 <b>RADAR KAYDI</b> · son {hours} saat"]
    if not recent:
        parts.append("Bu sürede radara giren long sinyali yok.")
    else:
        lines = []
        newest = sorted(recent, key=lambda e: int(e.get("opened_at") or 0), reverse=True)
        for e in newest[:RADAR_ROWS]:
            source = "T" if e.get("source") == "TAKTIK" else "L"
            coin = str(e.get("symbol") or "?").removesuffix("USDT")[:8]
            plan = e.get("plan") or {}
            price = _num(e["entry_price"]) if e.get("entry_price") else "?"
            stop = _num(plan["hard_stop"]) if plan else "yok"
            lines.append(f"{_clock(int(e['opened_at']))} {source} {coin:<8} {e.get('gate_status') or '?'}")
            lines.append(f"  giriş {price} · stop {stop}")
            quality = QUALITY_MARKS.get(str(e.get("quality") or ""), "")
            lines.append(f"  {_status_label(e)}" + (f" · F1 {quality}" if quality else "")
                         + (" · sessiz" if e.get("muted") else ""))
        if len(newest) > RADAR_ROWS:
            lines.append(f"+{len(newest) - RADAR_ROWS} eski kayıt daha")
        parts.append("<pre>" + esc("\n".join(lines)) + "</pre>")
        parts.append(esc("L: Likit-100, T: taktik (BTC/ETH). Sağdaki: uyarı anındaki kapı durumu. "
                         "72s: 72 saat doldu; * takip verisi eksikti. F1: kalite filtresi etiketi. "
                         "sessiz: KAÇIN olduğu için Telegram'a gönderilmedi, yalnızca kayıtta."))
    parts += [esc(summary_line(log, now=now)),
              *([esc(line)] if (line := quality_summary_line(log, now=now)) else []),
              esc("Stop kontrolü kapanmış 15 dk mumlarının dibiyle yapılır. Emir yetkisi yok.")]
    return "\n".join(parts)
