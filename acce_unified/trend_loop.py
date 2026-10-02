"""D1 trend loop in shadow mode: "exit when the trend breaks" on the majors, as daily notifications.

The rule is frozen (docs/TRADE_LOOP_STUDY.md, ``trading/backtest/trade_loop.py``
loop D1_20_10, trial 1fcc8f2ef5d92516):

- at each 00:00 UTC daily close, a universe member whose close is above its
  previous 20 daily closes enters at the next open;
- the acute stop is the entry price minus 3 × ATR(20) of the decision day,
  checked intraday; a candle that opens below it fills at its open;
- the position exits at the next open after a daily close below the previous
  10 daily closes; one position per coin, re-entry after the exit;
- slot: 1/N of the month's universe, and 1/N × min(1, 50% / 30-day volatility)
  for the volatility-targeted variant.

Evidence: RISK_AZALTIR in two independent tests (2018-20 and 2024-26), no
return evidence, entry timing no better than random. Live differences from
the research, documented in docs/TREND_LOOP_LIVE.md: MEXC spot data instead of
Binance; the universe ranks MEXC 30-day volume without the perpetual
condition (the 2018-20 test had none and replicated).

This module only reads public market data and writes a shadow record. It
never places or authorizes an order (``can_authorize_trade`` is always False).
Missing, stale or malformed data never becomes a signal: the coin is reported
as unknown for that day.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .cex import is_leveraged_token, is_stable_or_synthetic

DAY = 86_400
HOUR = 3_600
QUARTER = 900
ENTRY_DAYS = 20
EXIT_DAYS = 10
ATR_DAYS = 20
STOP_ATR = 3.0
VOL_DAYS = 30
VOL_TARGET = 0.50
VOLUME_DAYS = 30
MIN_VOLUME_DAYS = 28
SEASON_DAYS = 90
TOP_OTHERS = 10
CANDIDATES = 40                  # MEXC pairs ranked by 24h volume before the 30-day ranking
FIXED = ("BTCUSDT", "ETHUSDT")
QUOTE = "USDT"
SETTLE_SECONDS = 120             # wait after 00:00 UTC so the last hourly candle is published
HISTORY_DAYS = 33                # closed days fetched per decision: 31 needed for the 30-day volatility
DECISION_RETRY_SECONDS = 900     # retry coins with missing data for 15 minutes after 00:00 UTC
UNIVERSE_RETRY_SECONDS = 900
LATE_SECONDS = 3_600             # a message this long after its price point says so: it is not "buy now"
TRIAL = "1fcc8f2ef5d92516"
EVIDENCE = ("Kanıt: RİSK_AZALTIR (2 bağımsız test). Getiri kanıtı yok, risk azaltır; "
            "giriş zamanlaması rastgeleden iyi değil.")
NO_ORDER = "Gölge kayıt: emir yok, karar senin."
# Copied from trading/data/majors_data.py (which needs research-only packages); a test keeps them equal.
MEMES = frozenset({
    "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "BOME", "MEME", "PEOPLE", "TURBO", "NEIRO",
    "1000SATS", "1MBABYDOGE", "DOGS", "PNUT", "ACT", "TRUMP", "PENGU",
})
# Not ordinary crypto assets (trading/data/binance_history_identity.py); a test keeps them in sync.
NOT_ORDINARY = frozenset({"PAX", "UST", "AUD", "BKRW", "U", "KGST", "PAXG", "XAUT", "BULL", "BEAR",
                          "BNSOL", "WBETH", "BETH", "WBTC"})


class TrendLoopDataError(RuntimeError):
    """Market data that cannot support a decision (missing, malformed, stale or future)."""


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float


def parse_klines(rows: Any, *, interval: int, now: int, aligned: bool = True) -> tuple[list[Bar], Bar | None]:
    """Closed bars (chronological) and the one bar still open at ``now``, from a MEXC kline payload.

    A row from the future, more than one open row, a non-finite or impossible
    OHLC or an out-of-order row refuses the whole payload.
    """

    if not isinstance(rows, list):
        raise TrendLoopDataError("kline payload is not a list")
    closed: list[Bar] = []
    open_bar: Bar | None = None
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            raise TrendLoopDataError("malformed kline row")
        try:
            bar = Bar(int(row[0]) // 1000, float(row[1]), float(row[2]), float(row[3]), float(row[4]),
                      float(row[7]))
        except (TypeError, ValueError):
            raise TrendLoopDataError("invalid kline values") from None
        values = (bar.open, bar.high, bar.low, bar.close, bar.quote_volume)
        if not all(math.isfinite(v) for v in values) or min(values[:4]) <= 0 or bar.quote_volume < 0:
            raise TrendLoopDataError("non-finite or non-positive kline values")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            raise TrendLoopDataError("impossible OHLC geometry")
        if aligned and bar.open_time % interval:
            raise TrendLoopDataError("kline not aligned to its interval")
        if bar.open_time > now:
            raise TrendLoopDataError("kline from the future")
        if bar.open_time + interval > now:
            if open_bar is not None:
                raise TrendLoopDataError("more than one open kline")
            open_bar = bar
            continue
        if open_bar is not None:
            raise TrendLoopDataError("closed kline after the open one")
        if closed and bar.open_time <= closed[-1].open_time:
            raise TrendLoopDataError("klines out of order")
        closed.append(bar)
    return closed, open_bar


@dataclass(frozen=True)
class DayBar:
    day0: int
    high: float
    low: float
    close: float
    quote_volume: float


def daily_bars(hours: Sequence[Bar]) -> dict[int, DayBar]:
    """UTC days built from closed hourly bars; a day counts only with its 23:00 hour (its close)."""

    groups: dict[int, list[Bar]] = {}
    for bar in hours:
        groups.setdefault(bar.open_time - bar.open_time % DAY, []).append(bar)
    out = {}
    for day0, bars in groups.items():
        last = bars[-1]
        if last.open_time != day0 + 23 * HOUR:
            continue                                     # no daily close: the day is unknown
        out[day0] = DayBar(day0, max(b.high for b in bars), min(b.low for b in bars), last.close,
                           sum(b.quote_volume for b in bars))
    return out


# ---------------------------------------------------------------------------
# The rule (mirrors trading/backtest/trade_loop.py D1_20_10; a test compares them)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Levels:
    day0: int                    # the decision day (its close is the decision)
    close: float
    high20: float                # highest close of the 20 days before
    low10: float                 # lowest close of the 10 days before
    atr20: float                 # mean true range of the 20 days up to and including the decision day
    vol30: float | None          # annualised volatility of the 30 daily log returns; None if a day is unknown


def levels_at(days: Mapping[int, DayBar], day0: int) -> Levels | None:
    """The rule's inputs at the close of ``day0``; None when a day the rule needs is unknown.

    Entry, exit and the stop need the 21 closes up to ``day0``; the
    volatility (only for the volatility-targeted slot) needs 31 and is None
    without them, as in the research.
    """

    need = [day0 - k * DAY for k in range(ENTRY_DAYS, -1, -1)]          # 21 consecutive closes
    if any(d not in days for d in need):
        return None
    closes = [days[d].close for d in need]
    true_ranges = []
    for k in range(len(need) - ATR_DAYS, len(need)):
        bar, prev = days[need[k]], closes[k - 1]
        true_ranges.append(max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev)))
    vol_days = [day0 - k * DAY for k in range(VOL_DAYS, -1, -1)]
    vol30 = None
    if all(d in days for d in vol_days):
        vc = [days[d].close for d in vol_days]
        vol30 = statistics.stdev([math.log(vc[k] / vc[k - 1]) for k in range(1, len(vc))]) * math.sqrt(365)
    return Levels(
        day0=day0, close=closes[-1], high20=max(closes[:-1]), low10=min(closes[-1 - EXIT_DAYS:-1]),
        atr20=sum(true_ranges) / ATR_DAYS, vol30=vol30,
    )


def enters(levels: Levels) -> bool:
    return levels.close > levels.high20


def exits(levels: Levels) -> bool:
    return levels.close < levels.low10


def entry_plan(levels: Levels, entry_price: float, universe_size: int) -> dict[str, float] | None:
    """Stop and slots for an entry at ``entry_price``; None when the price opened at or below its own stop."""

    stop = entry_price - STOP_ATR * levels.atr20
    if levels.atr20 <= 0 or stop >= entry_price:
        return None
    slot = 1.0 / max(1, universe_size)
    vol_slot = slot * min(1.0, VOL_TARGET / levels.vol30) if levels.vol30 else None
    return {"stop": stop, "stop_pct": (1.0 - stop / entry_price) * 100.0, "slot_pct": slot * 100.0,
            "vol_slot_pct": None if vol_slot is None else vol_slot * 100.0}


def stop_fill(stop: float, bars: Sequence[Bar]) -> tuple[Bar, float] | None:
    """The first bar whose low reaches the stop, and the fill: the stop, or the open when it opened below."""

    for bar in bars:
        if bar.low <= stop:
            return bar, min(bar.open, stop)
    return None


# ---------------------------------------------------------------------------
# Universe (monthly, from data visible before the month starts)
# ---------------------------------------------------------------------------


def ordinary(base: str, all_bases: Iterable[str]) -> bool:
    base = base.upper()
    bases = {b.upper() for b in all_bases}
    return not (is_stable_or_synthetic(base) or is_leveraged_token(base, bases) or base in NOT_ORDINARY)


def rank_universe(volumes: Mapping[str, float], seasoned: Mapping[str, bool]) -> list[str]:
    """BTC, ETH, the top 10 others by 30-day volume, and the largest meme if none is in the top 10.

    ``volumes`` holds only pairs with enough days of data; unseasoned pairs are left out.
    """

    eligible = sorted((s for s in volumes if seasoned.get(s)), key=lambda s: (-volumes[s], s))
    members = [s for s in FIXED if s in eligible]
    others = [s for s in eligible if s not in FIXED]
    top = others[:TOP_OTHERS]
    members += top
    if not any(s[: -len(QUOTE)] in MEMES for s in top):
        meme = next((s for s in others if s[: -len(QUOTE)] in MEMES), None)
        if meme:
            members.append(meme)
    return members


# ---------------------------------------------------------------------------
# MEXC market data (spot only: the loop never substitutes another venue)
# ---------------------------------------------------------------------------


class MexcSpot:
    BASE = "https://api.mexc.com"
    NAMES = {QUARTER: "15m", HOUR: "60m", DAY: "1d"}

    def __init__(self, *, timeout: int = 15, session: Any = None) -> None:
        import requests

        self.timeout = timeout
        self.session = session or requests.Session()

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        try:
            response = self.session.get(f"{self.BASE}{path}", params=dict(params), timeout=self.timeout)
        except Exception as exc:                                   # the exception text can carry the URL
            raise TrendLoopDataError(f"mexc transport {type(exc).__name__}") from None
        if response.status_code != 200:
            raise TrendLoopDataError(f"mexc http {response.status_code}")
        if response.text.lstrip().startswith("<"):
            raise TrendLoopDataError("mexc returned an HTML page")
        try:
            return response.json()
        except ValueError:
            raise TrendLoopDataError("mexc returned malformed JSON") from None

    def klines(self, symbol: str, interval: int, *, end: int | None = None, start: int | None = None,
               limit: int = 1000) -> Any:
        params: dict[str, Any] = {"symbol": symbol, "interval": self.NAMES[interval], "limit": limit}
        if start is not None:
            params["startTime"] = start * 1000
        if end is not None:
            params["endTime"] = end * 1000 - 1
        return self._get("/api/v3/klines", params)

    def tickers(self) -> list[dict[str, Any]]:
        rows = self._get("/api/v3/ticker/24hr", {})
        if not isinstance(rows, list):
            raise TrendLoopDataError("ticker payload is not a list")
        return [r for r in rows if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# The loop: daily decisions, intraday stops, ledger, messages
# ---------------------------------------------------------------------------


def _iso(day0: int) -> str:
    return datetime.fromtimestamp(day0, tz=timezone.utc).strftime("%Y-%m-%d")


def _month(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m")


def _month_start(t: int) -> int:
    d = datetime.fromtimestamp(t, tz=timezone.utc)
    return int(datetime(d.year, d.month, 1, tzinfo=timezone.utc).timestamp())


class Ledger:
    """Append-only JSONL of shadow events: the durable record of the loop.

    An event id is written at most once. A message is stored with its event
    and stays pending until a DELIVERED record follows it, so a restart never
    loses an alert (it may repeat one whose delivery was not yet recorded).
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._records: list[dict[str, Any]] | None = None
        self._ids: set[str] = set()
        self._pending: dict[str, str] = {}

    def _load(self) -> list[dict[str, Any]]:
        if self._records is None:
            records: list[dict[str, Any]] = []
            if self.path.exists():
                raw = self.path.read_bytes()
                if raw and not raw.endswith(b"\n"):           # a write torn by a crash: drop the unfinished line
                    keep = raw.rfind(b"\n") + 1
                    with self.path.open("r+b") as handle:
                        handle.truncate(keep)
                        handle.flush()
                        os.fsync(handle.fileno())
                    raw = raw[:keep]
                for line in raw.decode("utf-8").splitlines():
                    if line.strip():
                        record = json.loads(line)                  # a malformed complete line refuses to continue
                        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                            raise TrendLoopDataError("ledger record without an id")
                        records.append(record)
            self._records = []
            for record in records:
                self._remember(record)
        return self._records

    def _remember(self, record: dict[str, Any]) -> None:
        assert self._records is not None
        self._records.append(record)
        self._ids.add(record["id"])
        if record.get("event") == "DELIVERED":
            self._pending.pop(str(record.get("message_id")), None)
        elif record.get("text"):
            self._pending[record["id"]] = str(record["text"])

    def records(self) -> list[dict[str, Any]]:
        return list(self._load())

    def ids(self) -> set[str]:
        self._load()
        return set(self._ids)

    def append(self, event: Mapping[str, Any], *, text: str | None = None) -> bool:
        self._load()
        if event["id"] in self._ids:
            return False
        record = {**event, **({"text": text} if text else {})}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._remember(record)
        return True

    def pending(self) -> list[dict[str, str]]:
        self._load()
        return [{"id": k, "text": v} for k, v in self._pending.items()]


class TrendLoop:
    """Shadow D1 loop. ``state`` is plain JSON so the bot can persist it with the rest of its state."""

    def __init__(self, ledger: Ledger, *, market: Any = None, workers: int = 8) -> None:
        self.ledger = ledger
        self.market = market
        self.workers = workers
        self.retry_at = 0
        self.lock = threading.RLock()
        self.state: dict[str, Any] = {"universe": None, "positions": {}, "reentry_from": {},
                                      "last_decision_day": None, "unknown": [], "stop_unknown": [],
                                      "last_run_at": None, "last_stop_check_at": None}

    # -- state -------------------------------------------------------------

    def load(self, payload: Mapping[str, Any] | None) -> None:
        """Restore saved state, then reconcile it with the ledger (the durable record)."""

        with self.lock:
            if isinstance(payload, Mapping):
                for key in self.state:
                    if key in payload:
                        self.state[key] = payload[key]
            self._reconcile()

    def _reconcile(self) -> None:
        """Positions the ledger shows closed are dropped; ones it shows open but the state lost are restored.

        A restored position has lost its tracking (exit level, checked
        bars): it is marked with a tracking gap and its stop is re-checked
        from the entry.
        """

        entries: dict[str, dict[str, Any]] = {}
        closed: set[str] = set()
        reentry: dict[str, int] = {}
        last_day = None
        for record in self.ledger.records():
            kind = record.get("event")
            if kind == "ENTRY":
                entries[record["id"].split(":", 1)[1]] = record
            elif kind in ("STOP", "EXIT"):
                closed.add(record["id"].split(":", 1)[1])
                at = int(record["exited_at"])
                start = at if kind == "EXIT" else at - at % DAY
                reentry[record["symbol"]] = max(reentry.get(record["symbol"], start), start)
            elif kind == "DAY":
                last_day = max(last_day or record["day"], record["day"])
        positions = {s: p for s, p in (self.state.get("positions") or {}).items()
                     if f"{s}:{_iso(p['decision_day'])}" not in closed}
        for key, record in sorted(entries.items(), key=lambda kv: kv[1]["decision_day"]):
            symbol = record["symbol"]
            if key in closed or (symbol in positions and positions[symbol]["decision_day"] >= record["decision_day"]):
                continue
            positions[symbol] = {**{k: v for k, v in record.items()
                                    if k not in ("id", "event", "trial", "can_authorize_trade", "text")},
                                 "checked_from": record["entered_at"], "tracking_gap": True}
        self.state["positions"] = positions
        merged = dict(self.state.get("reentry_from") or {})
        for symbol, start in reentry.items():
            merged[symbol] = max(int(merged.get(symbol, start)), start)
        self.state["reentry_from"] = merged
        if last_day is not None and (self.state.get("last_decision_day") or 0) < last_day:
            self.state["last_decision_day"] = last_day

    def export(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def _record(self, event: dict[str, Any], text: str | None) -> None:
        self.ledger.append({**event, "trial": TRIAL, "can_authorize_trade": False}, text=text)

    def outbox(self) -> list[dict[str, str]]:
        """Messages recorded in the ledger whose delivery is not recorded yet, oldest first."""

        with self.lock:
            return self.ledger.pending()

    def delivered(self, message_id: str, *, at: int, sent: bool = True) -> None:
        with self.lock:                                            # sent=False: alerts are switched off
            self._record({"id": f"DELIVERED:{message_id}", "event": "DELIVERED", "message_id": message_id,
                          "at": at, "sent": sent}, None)

    # -- data --------------------------------------------------------------

    def _days(self, symbol: str, *, end: int, now: int) -> tuple[dict[int, DayBar], Bar | None]:
        """Closed days before ``end`` and the hourly bar opening at ``end`` (its open is the next open)."""

        rows = self.market.klines(symbol, HOUR, start=end - HISTORY_DAYS * DAY, end=end + HOUR, limit=1000)
        closed, open_bar = parse_klines(rows, interval=HOUR, now=now)
        bars = [*closed, *([open_bar] if open_bar else [])]
        return daily_bars([b for b in closed if b.open_time < end]), next((b for b in bars if b.open_time == end), None)

    def _universe(self, start: int) -> dict[str, Any]:
        tickers = self.market.tickers()
        bases = {str(r.get("symbol", "")).upper()[: -len(QUOTE)] for r in tickers
                 if str(r.get("symbol", "")).upper().endswith(QUOTE)}
        ranked = []
        for row in tickers:
            symbol = str(row.get("symbol", "")).upper()
            if not symbol.endswith(QUOTE) or len(symbol) <= len(QUOTE) or not ordinary(symbol[:-len(QUOTE)], bases):
                continue
            try:
                ranked.append((float(row.get("quoteVolume") or 0.0), symbol))
            except (TypeError, ValueError):
                continue
        ranked.sort(reverse=True)
        pool = list(dict.fromkeys([*FIXED, *(s for _, s in ranked[:CANDIDATES])]))

        def measure(symbol: str) -> tuple[str, float | None, bool]:
            rows = self.market.klines(symbol, HOUR, start=start - (VOLUME_DAYS + 1) * DAY, end=start, limit=1000)
            closed, _ = parse_klines(rows, interval=HOUR, now=start)
            window = [b for b in closed if start - VOLUME_DAYS * DAY <= b.open_time < start]
            days_seen = {b.open_time - b.open_time % DAY for b in window}
            volume = sum(b.quote_volume for b in window) if len(days_seen) >= MIN_VOLUME_DAYS else None
            first = self.market.klines(symbol, DAY, start=start - (SEASON_DAYS + 10) * DAY, end=start, limit=5)
            first_closed, _ = parse_klines(first, interval=DAY, now=start, aligned=False)   # venue day boundary
            seasoned = bool(first_closed) and first_closed[0].open_time <= start - SEASON_DAYS * DAY
            return symbol, volume, seasoned

        volumes, seasoned, failed = {}, {}, []
        with ThreadPoolExecutor(max_workers=self.workers) as pool_exec:
            futures = {symbol: pool_exec.submit(measure, symbol) for symbol in pool}
            for symbol, future in futures.items():
                try:
                    _, volume, ok = future.result()
                except TrendLoopDataError:
                    failed.append(symbol)
                    continue
                if volume is not None:
                    volumes[symbol], seasoned[symbol] = volume, ok
        missing = [s for s in FIXED if not seasoned.get(s)]
        if missing:
            raise TrendLoopDataError("universe needs BTC and ETH; missing " + ",".join(missing))
        members = rank_universe(volumes, seasoned)
        return {"month": _month(start), "members": members, "built_at": int(time.time()),
                "candidates": len(pool), "failed": failed}

    # -- daily decision ----------------------------------------------------

    def due_day(self, now: int) -> int | None:
        """The UTC day whose close is to be decided now, or None if it was already decided or is not ready."""

        today = now - now % DAY
        if now - today < SETTLE_SECONDS:
            return None
        day = today - DAY
        last = self.state.get("last_decision_day")
        return None if last is not None and last >= day else day

    def run_daily(self, now: int) -> int | None:
        """Decide the close of the due day once: stops first, then exits, then entries. Returns the day."""

        day = self.due_day(now)
        if day is None or now < self.retry_at:
            return None
        decision = day + DAY                                       # 00:00 UTC: the decision time
        with self.lock:
            universe = self.state.get("universe")
            last = self.state.get("last_decision_day")
        if not universe or universe.get("month") != _month(decision):
            try:
                universe = self._universe(_month_start(decision))  # no decision until the universe is known
            except Exception:
                self.retry_at = now + UNIVERSE_RETRY_SECONDS
                raise
            with self.lock:
                self.state["universe"] = universe
            self._record({"id": f"UNIVERSE:{universe['month']}", "event": "UNIVERSE", **universe}, None)
        members = list(universe["members"])
        if last is not None and day - last > DAY:
            with self.lock:                                        # days without a decision: exits may be missed
                for position in self.state["positions"].values():
                    position["tracking_gap"] = True
            self._record({"id": f"DECISION_GAP:{_iso(last + DAY)}:{_iso(day - DAY)}", "event": "DECISION_GAP",
                          "from_day": last + DAY, "to_day": day - DAY}, None)
        self.check_stops(now, until=decision)                      # a stop before the close comes first
        with self.lock:
            positions = dict(self.state["positions"])
        symbols = sorted(set(members) | set(positions))
        results: dict[str, tuple[Levels | None, Bar | None]] = {}

        def evaluate(symbol: str) -> tuple[str, Levels | None, Bar | None]:
            try:
                days, next_open = self._days(symbol, end=decision, now=now)
            except TrendLoopDataError:
                return symbol, None, None
            return symbol, levels_at(days, day), next_open

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for symbol, levels, next_open in pool.map(evaluate, symbols):
                results[symbol] = (levels, next_open)
        unknown = sorted(s for s, (lv, op) in results.items() if lv is None or op is None)
        with self.lock:
            for symbol, position in positions.items():
                if symbol not in self.state["positions"]:
                    continue                                       # stopped out just above
                levels, next_open = results.get(symbol, (None, None))
                if levels is None or next_open is None:
                    continue
                position["exit_level"] = levels.low10
                if day > position["decision_day"] and exits(levels):
                    self._close(symbol, position, price=next_open.open, at=decision, reason="EXIT", now=now,
                                close=levels.close, level=levels.low10)
                    self.state["reentry_from"][symbol] = day + DAY
            for symbol in members:
                if symbol in self.state["positions"] or self.state["reentry_from"].get(symbol, -1) > day:
                    continue
                levels, next_open = results.get(symbol, (None, None))
                if levels is None or next_open is None or not enters(levels):
                    continue
                plan = entry_plan(levels, next_open.open, len(members))
                if plan is None:
                    continue
                position = {"symbol": symbol, "decision_day": day, "entered_at": decision, "decided_at": now,
                            "entry_price": next_open.open, "close": levels.close, "high20": levels.high20,
                            "atr20": levels.atr20, "vol30": levels.vol30, "exit_level": levels.low10,
                            "checked_from": decision, **plan}
                self.state["positions"][symbol] = position
                self._record({"id": f"ENTRY:{symbol}:{_iso(day)}", "event": "ENTRY", **position},
                             entry_text(position, len(members)))
            self.state["unknown"] = unknown
            self.state["last_run_at"] = now
            if unknown and now - decision < DECISION_RETRY_SECONDS:
                return None                                        # decided what is known; retry the rest
            for symbol in unknown:
                self._record({"id": f"UNKNOWN:{symbol}:{_iso(day)}", "event": "UNKNOWN", "symbol": symbol,
                              "day": day, "open_position": symbol in self.state["positions"]}, None)
            self._record({"id": f"DAY:{_iso(day)}", "event": "DAY", "day": day, "decided_at": now,
                          "universe": universe["month"], "members": members, "unknown": unknown,
                          "open": {s: {"exit_level": p["exit_level"], "stop": p["stop"]}
                                   for s, p in self.state["positions"].items()}}, None)
            self.state["last_decision_day"] = day
        return day

    def _close(self, symbol: str, position: Mapping[str, Any], *, price: float, at: int, reason: str,
               now: int, close: float | None = None, level: float | None = None) -> None:
        result = (price / position["entry_price"] - 1.0) * 100.0
        event = {"id": f"{reason}:{symbol}:{_iso(position['decision_day'])}", "event": reason, "symbol": symbol,
                 "entry_price": position["entry_price"], "entered_at": position["entered_at"],
                 "exit_price": price, "exited_at": at, "reported_at": now, "result_pct": result,
                 "close": close, "level": level,
                 "stop": position["stop"], "tracking_gap": bool(position.get("tracking_gap"))}
        del self.state["positions"][symbol]
        self._record(event, exit_text(event, reason))

    # -- intraday stops ----------------------------------------------------

    def check_stops(self, now: int, *, until: int | None = None) -> None:
        """Close positions whose acute stop was reached by a 15m bar since the last check (before ``until``)."""

        with self.lock:
            positions = dict(self.state["positions"])
        failed = []
        for symbol, position in positions.items():
            start = int(position["checked_from"])
            try:
                rows = self.market.klines(symbol, QUARTER, start=start,          # ≤ 1000 bars either way
                                          end=min(now + QUARTER, start + 1000 * QUARTER), limit=1000)
                closed, open_bar = parse_klines(rows, interval=QUARTER, now=now)
            except TrendLoopDataError:
                failed.append(symbol)                              # reported: the stop is unchecked, not safe
                continue
            bars = [b for b in (*closed, *([open_bar] if open_bar else []))
                    if b.open_time >= start and (until is None or b.open_time < until)]
            if not bars and (until is None or start < until):
                failed.append(symbol)                              # the bar at ``start`` exists: none is missing data
                continue
            gap = not bars or bars[0].open_time > start or any(
                b.open_time - a.open_time > QUARTER for a, b in zip(bars, bars[1:]))
            hit = stop_fill(position["stop"], bars)
            with self.lock:
                current = self.state["positions"].get(symbol)
                if current is None:
                    continue
                if gap and bars:
                    current["tracking_gap"] = True                 # a stop could have hidden in the hole
                if hit:
                    bar, price = hit
                    self._close(symbol, current, price=price, at=bar.open_time, reason="STOP", now=now)
                    self.state["reentry_from"][symbol] = bar.open_time - bar.open_time % DAY
                elif bars:
                    current["checked_from"] = bars[-1].open_time   # re-read the still-open bar next time
        with self.lock:
            self.state["stop_unknown"] = sorted(s for s in failed if s in self.state["positions"])
            if until is None:
                self.state["last_stop_check_at"] = now

    # -- reports -----------------------------------------------------------

    def positions_text(self, now: int) -> str:
        from .long_alerts import _clock, _num, esc, table

        with self.lock:
            state = self.export()
        universe = state.get("universe") or {}
        lines = ["📈 <b>D1 DÖNGÜ · trend bozulunca çık</b>",
                 esc(f"Evren {universe.get('month', '?')}: " + " ".join(
                     s[: -len(QUOTE)] for s in universe.get("members") or []) if universe else "Evren henüz kurulmadı.")]
        positions = state.get("positions") or {}
        if not positions:
            lines.append(esc("Açık gölge pozisyon yok."))
        for symbol in sorted(positions):
            p = positions[symbol]
            rows = [("Giriş", f"{_num(p['entry_price'])}  {_clock(p['entered_at'])}"),
                    ("Çıkış", f"kapanış < {_num(p['exit_level'])}"),
                    ("Acil stop", f"{_num(p['stop'])}  −%{p['stop_pct']:.1f}")]
            if p.get("tracking_gap"):
                rows.append(("Uyarı", "takipte boşluk"))
            lines += [f"<b>{esc(symbol)}</b>", table(rows)]
        if state.get("unknown"):
            lines.append(esc("Veri eksik (karar yok): " + " ".join(state["unknown"])))
        if state.get("stop_unknown"):
            lines.append(esc("Acil stop kontrol edilemedi: " + " ".join(state["stop_unknown"])))
        last = state.get("last_run_at")
        lines += [esc(f"Son karar: {_clock(last)} TSİ" if last else "Henüz karar verilmedi."),
                  f"<i>{esc(EVIDENCE)}</i>", esc(NO_ORDER)]
        return "\n".join(lines)

    def fresh(self, now: int, *, stop_seconds: int) -> bool:
        """The last daily close was decided and, with open positions, every stop was checked recently."""

        with self.lock:
            day = self.state.get("last_decision_day")
            open_positions = bool(self.state.get("positions"))
            checked = self.state.get("last_stop_check_at")
            stop_unknown = bool(self.state.get("stop_unknown"))
        today = now - now % DAY
        grace = SETTLE_SECONDS + DECISION_RETRY_SECONDS + HOUR
        expected = today - DAY if now - today >= grace else today - 2 * DAY
        if day is None or day < expected:
            return False
        return not open_positions or (checked is not None and now - checked <= 3 * stop_seconds
                                      and not stop_unknown)

    def status_line(self) -> str:
        with self.lock:
            universe = self.state.get("universe") or {}
            positions = len(self.state.get("positions") or {})
            day = self.state.get("last_decision_day")
        return (f"D1 döngü: {positions} açık gölge pozisyon · evren {universe.get('month', '?')} "
                f"({len(universe.get('members') or [])} coin) · son karar {_iso(day) if day else 'yok'}")


# ---------------------------------------------------------------------------
# Messages (Telegram HTML; every value escaped)
# ---------------------------------------------------------------------------


def entry_text(p: Mapping[str, Any], universe_size: int) -> str:
    from .long_alerts import _clock, _num, esc, table

    exit_pct = (1.0 - p["exit_level"] / p["entry_price"]) * 100.0
    slot = f"1/{universe_size}"
    if p.get("vol_slot_pct") is not None:
        slot += f" · oyn. ayarlı %{p['vol_slot_pct']:.1f}"
    rows = [("Kapanış", f"{_num(p['close'])}  > 20g zirve {_num(p['high20'])}"),
            ("Giriş ref", f"{_num(p['entry_price'])}  (00:00 UTC açılış)"),
            ("Çıkış", f"kapanış < {_num(p['exit_level'])}  (−%{exit_pct:.0f}, 10g dip)"),
            ("Acil stop", f"{_num(p['stop'])}  (−%{p['stop_pct']:.0f}, 3 ATR, gün içi)"),
            ("Pay", slot)]
    late = int(p.get("decided_at") or p["entered_at"]) - int(p["entered_at"])
    warning = ([esc(f"⚠️ Geç karar: {late // HOUR} saat gecikti. Giriş referansı geçmiş fiyattır, şimdiki "
                    "fiyat farklı olabilir; bu bir 'şimdi al' talimatı değildir.")] if late >= LATE_SECONDS else [])
    return "\n".join([
        f"📈 <b>D1 DÖNGÜ · GİRİŞ · {esc(p['symbol'])}</b>",
        esc(f"{_clock(p['entered_at'])} TSİ · {NO_ORDER}"),
        *warning,
        table(rows),
        esc("Çıkış seviyesi her gün güncellenir: /d1"),
        f"<i>{esc(EVIDENCE)}</i>",
    ])


def exit_text(event: Mapping[str, Any], reason: str) -> str:
    from .long_alerts import _clock, _num, esc, table

    held = max(0, round((event["exited_at"] - event["entered_at"]) / DAY))
    if reason == "STOP":
        head = f"🛑 <b>D1 DÖNGÜ · ACİL STOP · {esc(event['symbol'])}</b>"
        why = ("Neden", "gün içi acil stop")
        note = "Çıkış stop fiyatından; mum stop'un altında açıldıysa açılış fiyatından. Maliyet hariç."
    else:
        head = f"📉 <b>D1 DÖNGÜ · ÇIKIŞ · {esc(event['symbol'])}</b>"
        why = ("Neden", f"kapanış {_num(event['close'])} < {_num(event['level'])}")
        note = "Trend bozuldu: kapanış 10 günlük dibin altında. Çıkış 00:00 UTC açılışından. Maliyet hariç."
    rows = [why, ("Giriş", f"{_num(event['entry_price'])}  {_clock(event['entered_at'])}"),
            ("Çıkış", f"{_num(event['exit_price'])}  {_clock(event['exited_at'])}"),
            ("Sonuç", f"%{event['result_pct']:+.1f} · {held} gün")]
    if event.get("tracking_gap"):
        rows.append(("Uyarı", "takipte boşluk vardı"))
    late = int(event.get("reported_at") or event["exited_at"]) - int(event["exited_at"])
    if late >= LATE_SECONDS:
        rows.append(("Gecikme", f"{late // HOUR} saat sonra bildirildi"))
    return "\n".join([head, table(rows), esc(note), esc(NO_ORDER)])
