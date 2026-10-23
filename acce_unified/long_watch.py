"""Long tracking for the register's coins on USDT perpetuals (docs/POSITION_LONG.md §13–§14).

The list is the user's v3.2 report, read from ``research/register`` by
``acce_unified.register``. For every coin in it this module collects, once a
week after the Sunday daily close, what a long position on that coin would
have to survive and where it stands:

- the wick reference: the deepest intraday low of the last 365 days, the
  deepest single-day flash and the lowest daily close of the last 730 days,
  relative to today's price; the leverage at which the liquidation price
  stays at least 20 % below that reference (never above 2×);
- the annualised funding of the last 90 days;
- open interest against market cap and perpetual against spot volume (the
  report's leverage-build-up trigger);
- the weekly 10-week channel state (the D1 exit family at weekly scale);
- the regime slice from BTC dominance and the altcoin-season share.

A slice signal needs every condition at once; whatever is missing is named.
Nothing here places, sizes or authorises an order (``can_authorize_trade`` is
False in every record), and no missing or failed series ever reads as safe:
it reads as BİLİNMİYOR and the coin has no signal that week.
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import register as rg
from .altcoin_watch import LIQUIDITY as LIQUIDITY_GATE, AltcoinDataError, CoinGecko
from .cex import is_stable_or_synthetic
from .text import _num, esc

DAY = 86_400
WEEK = 7 * DAY
VENUE = "MEXC"

HISTORY_DAYS = 740                 # daily candles fetched per coin
WICK_DAYS = 365                    # WICK365, FLASH365 window
CYCLE_DAYS = 730                   # CYCLE_LOW window
MAX_MISSING = {WICK_DAYS: 5, CYCLE_DAYS: 10}   # single missing days tolerated inside a window (cohort hole rule)
WICK_BUFFER = 0.20                 # liquidation at least this far below the wick reference (§13.2)
MAINTENANCE_MARGIN = 0.05          # conservative floor; the exchange's own rate is used when it is higher
MAX_LEVERAGE = 2.0                 # code-level cap (§13.1); the default position is 1×
CHANNEL_WEEKS = 10                 # weekly channel (§5.2, §14.3)
MIN_WEEKS = 20                     # fewer complete weeks: channel state unknown
FUND_DAYS = 90
MIN_SETTLEMENTS = 60               # fewer settlements in 90 days: FUND90 unknown
SETTLEMENTS_PER_YEAR = 365 * 3     # 8-hour funding
FUND_MAX = 0.20                    # no new slice above this annualised funding (§13.4)
SCALE_OUT = (2.0, 4.0)             # partial-sale multiples (§5.3)
ALT_TOP = 50                       # altcoin-season universe (Blockchaincenter definition)
ALT_DAYS = 90
ALT_MIN_KNOWN = 40                 # fewer coins with a 90-day return: ALT90 unknown
BTCD_SLICE_2 = 55.0                # BTC.D weekly value below this: second slice (report §9)
BTCD_REDUCE = 60.0                 # BTC.D above this and rising: reduce
ALT_SLICE_1 = 50.0                 # altcoin-season share above this and BTC.D falling: first slice
DIRECTION_WEEKS = 4                # BTC.D direction from the archive this many weeks back
DIRECTION_DEADBAND = 0.25          # BTC.D points; smaller moves are flat
LOSS_BUDGET = {rg.ALTCOIN: 0.01, rg.MEME: 0.005}   # capital lost at −80 % (§4.2); user budget, env-overridable
DRAWDOWN_BUDGET = 0.80
SIGMA_TARGET = 0.9
# CoinGecko derivatives markets summed for the aggregate open interest (a fixed list, named on the card)
DERIVATIVE_MARKETS = ("Binance (Futures)", "Bybit (Futures)", "OKX (Futures)", "Bitget Futures", "Gate (Futures)",
                      "Hyperliquid (Futures)", "MEXC (Futures)", "KuCoin Futures", "HTX Futures")
REGISTER_MAX_AGE_DAYS = 180        # an older report is stale: shown with a warning, no new slice (§3.5)
GATES_MAX_AGE = 3 * DAY            # a gate check (altcoin_watch) older than this is unknown: no slice
UNKNOWN = "BİLİNMİYOR"
SLICE_1, SLICE_2, REDUCE, WAIT = "DİLİM 1", "DİLİM 2", "AZALT", "BEKLE"
UP, DOWN = "YUKARI", "AŞAĞI"
TRIAL: str | None = None           # the forward record is not pre-registered yet (§8.2); events say so
EVENT_DECISION = "KARAR"
FOOTER = "Gölge kayıt: emir yok, karar senin. KANIT YOK — ileri kayıt ön-kaydı bekliyor."


class LongDataError(RuntimeError):
    """A provider failed or returned something unusable. The message never carries a URL or a secret."""


@dataclass(frozen=True)
class Candle:
    day0: int
    open: float
    high: float
    low: float
    close: float
    volume: float


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def day_start(ts: int) -> int:
    return int(ts) - int(ts) % DAY


def week_start(ts: int) -> int:
    """Monday 00:00 UTC of the week holding ``ts`` (1970-01-01 was a Thursday)."""

    day0 = day_start(ts)
    return day0 - ((day0 // DAY + 3) % 7) * DAY


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    return float(value) if math.isfinite(value) else None


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


# ---------------------------------------------------------------------------
# Daily candles → wick reference, weekly closes, channel
# ---------------------------------------------------------------------------


def parse_mexc_klines(payload: Any, *, now: int) -> list[Candle]:
    """MEXC contract klines (arrays) → closed daily candles, oldest first. Malformed data raises."""

    data = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(data, Mapping):
        raise LongDataError("mexc kline payload is not an object")
    columns = {}
    for key in ("time", "open", "high", "low", "close", "vol"):
        column = data.get(key)
        if not isinstance(column, list):
            raise LongDataError(f"mexc kline column {key} missing")
        columns[key] = column
    length = len(columns["time"])
    if any(len(c) != length for c in columns.values()):
        raise LongDataError("mexc kline columns differ in length")
    today0 = day_start(now)
    by_day: dict[int, Candle] = {}
    for i in range(length):
        ts = _finite(columns["time"][i])
        if ts is None or ts <= 0:
            raise LongDataError("mexc kline time malformed")
        values = [_finite(columns[k][i]) for k in ("open", "high", "low", "close", "vol")]
        if any(v is None for v in values):
            raise LongDataError("mexc kline value malformed")
        o, h, l, c, v = values                                     # type: ignore[misc]
        if min(o, h, l, c) <= 0 or v < 0 or h < max(o, c) or l > min(o, c):   # type: ignore[operator]
            raise LongDataError("mexc kline OHLC impossible")
        day0 = day_start(int(ts))
        if day0 + DAY > today0:                                    # the forming day is not evidence
            continue
        candle = Candle(day0, o, h, l, c, v)                       # type: ignore[arg-type]
        if day0 in by_day and by_day[day0] != candle:
            raise LongDataError("mexc kline duplicate day with different values")
        by_day[day0] = candle
    return [by_day[d] for d in sorted(by_day)]


def _window(candles: Sequence[Candle], *, start: int, end: int) -> list[Candle] | None:
    """Candles with start ≤ day0 < end when the window is covered: the series reaches back to ``start``
    and no more than the allowed number of single days is missing."""

    days = end - start
    allowed = MAX_MISSING.get(days // DAY, 0)
    if not candles or candles[0].day0 > start + allowed * DAY:
        return None
    inside = [c for c in candles if start <= c.day0 < end]
    if days // DAY - len(inside) > allowed:
        return None
    return inside


def wick_reference(candles: Sequence[Candle], *, now: int, price: float | None) -> dict[str, Any]:
    """WICK365, FLASH365 and CYCLE_LOW relative to ``price``; the worst of the known ones is the reference."""

    out: dict[str, Any] = {"wick365": None, "flash365": None, "cycle_low": None, "worst": None,
                           "reference_price": None, "days": len(candles)}
    if price is None or price <= 0:
        return out
    today0 = day_start(now)
    w365 = _window(candles, start=today0 - WICK_DAYS * DAY, end=today0)
    if w365:
        out["wick365"] = min(c.low for c in w365) / price - 1
        flashes = [b.low / a.close - 1 for a, b in zip(w365, w365[1:]) if b.day0 - a.day0 == DAY]
        if flashes:
            out["flash365"] = min(flashes)
    w730 = _window(candles, start=today0 - CYCLE_DAYS * DAY, end=today0)
    if w730:
        out["cycle_low"] = min(c.close for c in w730) / price - 1
    known = [v for v in (out["wick365"], out["flash365"], out["cycle_low"]) if v is not None]
    if known:
        # the flash depth is a move from a close, not from today's price; as a worst case it is applied to today
        out["worst"] = min(known)
        out["reference_price"] = price * (1 + out["worst"])
    return out


def max_leverage(worst: float | None, *, maintenance: float = MAINTENANCE_MARGIN) -> float | None:
    """L_max = 1 / (1 + maintenance − 0,8 × (1 + worst)), capped at MAX_LEVERAGE (§13.2). Below 1 means
    even an unleveraged position does not satisfy the wick rule."""

    if worst is None or not math.isfinite(worst):
        return None
    q = (1 - WICK_BUFFER) * (1 + min(0.0, worst))
    denominator = 1 + maintenance - q
    if denominator <= 0:
        return MAX_LEVERAGE
    return min(MAX_LEVERAGE, 1 / denominator)


def liquidation_price(entry: float, leverage: float, *, maintenance: float = MAINTENANCE_MARGIN) -> float:
    return entry * (1 - 1 / leverage + maintenance)


def protection_stop(reference_price: float) -> float:
    """Margin-protection stop for leverage above 1×: below the wick reference, above the liquidation (§13.3)."""

    return reference_price * (1 - WICK_BUFFER / 2)


def weekly_closes(candles: Sequence[Candle], *, now: int) -> list[tuple[int, float]]:
    """(Monday 00:00 UTC, Sunday close) for every complete week whose Sunday candle exists and is closed."""

    today0 = day_start(now)
    by_day = {c.day0: c for c in candles}
    weeks: list[tuple[int, float]] = []
    for day0 in sorted(by_day):
        w = week_start(day0)
        if day0 == w + 6 * DAY and w + WEEK <= today0:
            weeks.append((w, by_day[day0].close))
    return weeks


def channel(closes: Sequence[float], *, weeks: int = CHANNEL_WEEKS) -> dict[str, Any]:
    """Weekly channel state walked from history: a close above the previous ``weeks`` closes' high turns
    UP, a close below their low turns DOWN; otherwise the state is kept. Short history → unknown."""

    out: dict[str, Any] = {"state": None, "high": None, "low": None, "weeks": len(closes)}
    if len(closes) < max(MIN_WEEKS, weeks + 1):
        return out
    state = None
    for i in range(weeks, len(closes)):
        window = closes[i - weeks:i]
        if closes[i] > max(window):
            state = UP
        elif closes[i] < min(window):
            state = DOWN
    out["state"] = state
    out["high"], out["low"] = max(closes[-weeks:]), min(closes[-weeks:])
    return out


# ---------------------------------------------------------------------------
# Funding, leverage build-up, regime, size
# ---------------------------------------------------------------------------


def funding_90(settlements: Iterable[tuple[int, float]], *, now: int) -> dict[str, Any]:
    """Annualised mean funding of the last 90 days; unknown below MIN_SETTLEMENTS."""

    since = now - FUND_DAYS * DAY
    rates = [r for t, r in settlements if since <= t <= now and math.isfinite(r)]
    if len(rates) < MIN_SETTLEMENTS:
        return {"annual": None, "count": len(rates)}
    return {"annual": statistics.fmean(rates) * SETTLEMENTS_PER_YEAR, "count": len(rates)}


def leverage_buildup(*, oi_usd: float | None, market_cap: float | None, perp_volume: float | None,
                     spot_volume: float | None, thresholds: Mapping[str, float]) -> dict[str, Any]:
    """The report's trigger: OI/market cap > 20 % and perp/spot > 6. Unknown inputs give an unknown verdict."""

    oi_mcap = oi_usd / market_cap if oi_usd is not None and market_cap else None
    perp_spot = perp_volume / spot_volume if perp_volume is not None and spot_volume else None
    triggered = None
    if oi_mcap is not None and perp_spot is not None:
        triggered = oi_mcap > float(thresholds.get("oi_mcap_gt", 0.20)) and perp_spot > float(thresholds.get("perp_spot_gt", 6.0))
    return {"oi_mcap": oi_mcap, "perp_spot": perp_spot, "triggered": triggered}


def regime(*, btc_d: float | None, btc_d_then: float | None, alt90: float | None) -> dict[str, Any]:
    """Report §9: BTC.D below 55 → second slice; above 60 and rising → reduce; altcoin-season share above 50
    and BTC.D falling → first slice; otherwise wait. Unknown inputs give no slice."""

    direction = None
    if btc_d is not None and btc_d_then is not None:
        delta = btc_d - btc_d_then
        direction = "düşüyor" if delta < -DIRECTION_DEADBAND else "yükseliyor" if delta > DIRECTION_DEADBAND else "yatay"
    out = {"btc_d": btc_d, "btc_d_then": btc_d_then, "direction": direction, "alt90": alt90, "slice": None,
           "reason": ""}
    if btc_d is None:
        out["reason"] = "BTC.D bilinmiyor"
    elif btc_d < BTCD_SLICE_2:
        out["slice"], out["reason"] = SLICE_2, f"BTC.D {btc_d:.1f} < {BTCD_SLICE_2:.0f}"
    elif direction is None:
        out["reason"] = f"BTC.D yönü bilinmiyor ({DIRECTION_WEEKS} haftalık arşiv yok)"
    elif btc_d > BTCD_REDUCE and direction == "yükseliyor":
        out["slice"], out["reason"] = REDUCE, f"BTC.D {btc_d:.1f} > {BTCD_REDUCE:.0f} ve yükseliyor"
    elif alt90 is None:
        out["reason"] = "altcoin sezonu payı bilinmiyor"
    elif alt90 > ALT_SLICE_1 and direction == "düşüyor":
        out["slice"], out["reason"] = SLICE_1, f"ALT90 {alt90:.0f} > {ALT_SLICE_1:.0f} ve BTC.D düşüyor"
    else:
        out["slice"], out["reason"] = WAIT, f"ALT90 {alt90:.0f}, BTC.D {btc_d:.1f} {direction}"
    return out


def gate_verdict(gate: Mapping[str, Any] | None, *, now: int,
                 previous: Mapping[str, Any] | None) -> dict[str, Any]:
    """The gate guard's result for one coin (altcoin_watch.gates_of) read as thesis invalidation (§5.1).

    A hack, contract or activity ❌ blocks at once. A liquidity ❌ blocks only when the previous weekly
    decision also saw it (two consecutive checks; the first is a warning). No check, or one older than
    GATES_MAX_AGE, is unknown and blocks as unknown. ❔ never blocks: the report evaluated those by hand."""

    if gate is None or now - int(gate.get("at") or 0) > GATES_MAX_AGE:
        return {"known": False, "block": None, "warning": None, "status": None, "fails": {}, "at": None}
    fails = {str(k): str(v) for k, v in (gate.get("fails") or {}).items()}
    out = {"known": True, "block": None, "warning": None, "status": gate.get("status"), "fails": fails,
           "at": int(gate.get("at") or 0)}
    hard = {g: r for g, r in fails.items() if g != LIQUIDITY_GATE}
    if hard:
        out["block"] = "kapı ❌: " + "; ".join(hard.values())
    elif LIQUIDITY_GATE in fails:
        if LIQUIDITY_GATE in ((previous or {}).get("fails") or {}):
            out["block"] = f"kapı ❌: {fails[LIQUIDITY_GATE]} (iki ardışık karar)"
        else:
            out["warning"] = f"likidite ❌ ilk karar: {fails[LIQUIDITY_GATE]}"
    return out


def position_share(kind: str, sigma: float | None, *, loss_budget: Mapping[str, float]) -> float | None:
    """Share of capital: loss budget ÷ 0,8, scaled by min(1, 0,9/σ) (§4.2). Unknown σ → unknown share."""

    budget = loss_budget.get(kind)
    if budget is None or sigma is None or sigma <= 0:
        return None
    return budget / DRAWDOWN_BUDGET * min(1.0, SIGMA_TARGET / sigma)


def evaluate(coin: rg.Coin, *, regime_slice: str | None, channel_state: str | None, worst: float | None,
             l_max: float | None, fund_annual: float | None, buildup: bool | None,
             fund_max: float = FUND_MAX, margin_known: bool = True, register_age_days: int = 0,
             gate_known: bool = True, gate_block: str | None = None) -> dict[str, Any]:
    """Every condition of §14.3 at once; the missing ones are named. ``signal`` is None unless all hold."""

    missing: list[str] = []
    if register_age_days > REGISTER_MAX_AGE_DAYS:
        missing.append(f"kayıt bayat ({register_age_days} gün > {REGISTER_MAX_AGE_DAYS})")
    if not gate_known:
        missing.append("kapı kontrolü bilinmiyor")
    elif gate_block:
        missing.append(gate_block)
    if coin.basket != rg.CORE:
        missing.append(f"sepet {coin.basket_label}")
    if coin.condition:
        missing.append("koşul bekliyor")
    if regime_slice not in (SLICE_1, SLICE_2):
        missing.append("rejim " + (regime_slice or UNKNOWN))
    if channel_state != UP:
        missing.append("kanal " + (channel_state or UNKNOWN))
    if worst is None:
        missing.append("fitil referansı bilinmiyor")
    if not margin_known:
        missing.append("sürdürme marjı bilinmiyor")                 # the rule needs the exchange's own rate
    if worst is not None and margin_known and l_max is None:
        missing.append("kaldıraç sınırı bilinmiyor")
    elif l_max is not None and l_max < 1:
        missing.append("1× bile fitil kuralını sağlamıyor")
    if fund_annual is None:
        missing.append("funding bilinmiyor")
    elif fund_annual > fund_max:
        missing.append(f"funding yıllık %{fund_annual * 100:.0f} > %{fund_max * 100:.0f}")
    if buildup is None:
        missing.append("OI/hacim bilinmiyor")
    elif buildup:
        missing.append("kaldıraç birikimi (OI/pd > %20, perp/spot > 6)")
    signal = None if missing else regime_slice
    return {"signal": signal, "missing": missing}


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class MexcContract:
    """contract.mexc.com public endpoints. Errors carry a type or status, never a URL."""

    BASE = "https://contract.mexc.com/api/v1/contract"

    def __init__(self, *, timeout: int = 20, session: Any = None, sleep: Callable[[float], None] = time.sleep,
                 pace: float = 0.3) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.timeout, self.session, self.sleep, self.pace = timeout, session, sleep, pace

    def _get(self, path: str, params: Mapping[str, Any]) -> Any:
        try:
            response = self.session.get(f"{self.BASE}{path}", params=dict(params), timeout=self.timeout)
        except Exception as exc:                                   # the text can carry the URL
            raise LongDataError(f"mexc transport {type(exc).__name__}") from None
        if response.status_code != 200:
            raise LongDataError(f"mexc http {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            raise LongDataError("mexc malformed JSON") from None
        if not isinstance(payload, Mapping) or payload.get("success") is not True:
            code = payload.get("code") if isinstance(payload, Mapping) else None
            raise LongDataError(f"mexc error {code!r}"[:60])
        self.sleep(self.pace)
        return payload

    def daily(self, symbol: str, *, now: int) -> list[Candle]:
        start = day_start(now) - HISTORY_DAYS * DAY
        payload = self._get(f"/kline/{symbol}", {"interval": "Day1", "start": start, "end": now})
        return parse_mexc_klines(payload, now=now)

    def funding(self, symbol: str, *, since: int, pages: int = 6) -> list[tuple[int, float]]:
        out: list[tuple[int, float]] = []
        for page in range(1, pages + 1):
            payload = self._get("/funding_rate/history", {"symbol": symbol, "page_num": page, "page_size": 100})
            data = payload.get("data")
            rows = data.get("resultList") if isinstance(data, Mapping) else None
            if not isinstance(rows, list):
                raise LongDataError("mexc funding payload malformed")
            oldest = None
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                ts, rate = _finite(row.get("settleTime")), _finite(row.get("fundingRate"))
                if ts is None or rate is None:
                    raise LongDataError("mexc funding row malformed")
                ts = int(ts // 1000)
                oldest = ts if oldest is None else min(oldest, ts)
                if ts >= since:
                    out.append((ts, rate))
            if not rows or (oldest is not None and oldest < since):
                break
        return sorted(set(out))

    def ticker(self, symbol: str) -> dict[str, float | None]:
        data = self._get("/ticker", {"symbol": symbol}).get("data")
        if not isinstance(data, Mapping):
            raise LongDataError("mexc ticker payload malformed")
        return {"price": _positive(data.get("lastPrice")), "oi_contracts": _finite(data.get("holdVol")),
                "volume_usdt_24h": _finite(data.get("amount24")), "funding_rate": _finite(data.get("fundingRate"))}

    def detail(self, symbol: str) -> dict[str, float | None]:
        data = self._get("/detail", {"symbol": symbol}).get("data")
        if not isinstance(data, Mapping):
            raise LongDataError("mexc detail payload malformed")
        return {"contract_size": _positive(data.get("contractSize")),
                "maintenance_margin": _positive(data.get("maintenanceMarginRate"))}


class Gecko(CoinGecko):
    """The altcoin watch's CoinGecko client (keyed header, pacing, 429 waits) plus the series this module needs."""

    def markets_for(self, ids: Sequence[str]) -> dict[str, dict[str, float | None]]:
        out: dict[str, dict[str, float | None]] = {}
        for k in range(0, len(ids), 200):
            data = self._get("/coins/markets", {"vs_currency": "usd", "ids": ",".join(ids[k:k + 200]),
                                                "per_page": 250, "page": 1, "sparkline": "false"})
            if not isinstance(data, list):
                raise AltcoinDataError("coingecko markets payload is not a list")
            for row in data:
                if isinstance(row, Mapping) and isinstance(row.get("id"), str):
                    out[row["id"]] = {"price": _positive(row.get("current_price")),
                                      "market_cap": _positive(row.get("market_cap")),
                                      "spot_volume": _positive(row.get("total_volume"))}
        return out

    def global_(self) -> dict[str, float | None]:
        data = self._get("/global")
        inner = data.get("data") if isinstance(data, Mapping) else None
        if not isinstance(inner, Mapping):
            raise AltcoinDataError("coingecko global payload is not an object")
        share = inner.get("market_cap_percentage") if isinstance(inner.get("market_cap_percentage"), Mapping) else {}
        total = inner.get("total_market_cap") if isinstance(inner.get("total_market_cap"), Mapping) else {}
        return {"btc_dominance": _positive(share.get("btc")), "total_market_cap": _positive(total.get("usd")),
                "updated_at": _positive(inner.get("updated_at"))}

    def derivatives(self) -> dict[str, dict[str, float]]:
        """Aggregate perpetual open interest and 24h volume per base asset over DERIVATIVE_MARKETS."""

        data = self._get("/derivatives")
        if not isinstance(data, list):
            raise AltcoinDataError("coingecko derivatives payload is not a list")
        out: dict[str, dict[str, Any]] = {}
        for row in data:
            if not isinstance(row, Mapping) or row.get("contract_type") != "perpetual":
                continue
            if row.get("market") not in DERIVATIVE_MARKETS or not isinstance(row.get("index_id"), str):
                continue
            entry = out.setdefault(row["index_id"].upper(), {"oi_usd": 0.0, "perp_volume_usd": 0.0, "tickers": 0,
                                                             "incomplete": False})
            oi, volume = _finite(row.get("open_interest")), _finite(row.get("volume_24h"))
            if oi is None or volume is None or oi < 0 or volume < 0:
                entry["incomplete"] = True                         # an unusable venue row: the sum is not the sum
                continue
            entry["oi_usd"] += oi
            entry["perp_volume_usd"] += volume
            entry["tickers"] += 1
        return out

    def top_altcoins(self, n: int = ALT_TOP) -> list[str]:
        ids: list[str] = []
        for row in self.markets(1):
            if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
                continue
            symbol, name = str(row.get("symbol") or ""), str(row.get("name") or "")
            if row["id"] == "bitcoin" or is_stable_or_synthetic(symbol):
                continue
            lowered = name.lower()
            if any(w in lowered for w in ("wrapped", "staked", "bridged", "restaked", "liquid")):
                continue
            ids.append(row["id"])
            if len(ids) >= n:
                break
        return ids

    def return_days(self, gecko_id: str, days: int) -> float | None:
        prices = self.chart(gecko_id).get("prices")
        if not isinstance(prices, list) or len(prices) < days + 1:
            return None
        clean = [(_finite(p[0]), _positive(p[1])) for p in prices if isinstance(p, (list, tuple)) and len(p) == 2]
        clean = [(int(t // 1000), v) for t, v in clean if t is not None and v is not None]
        if len(clean) < days + 1:
            return None
        last_t, last = clean[-1]
        target = last_t - days * DAY
        then = min(clean, key=lambda tv: abs(tv[0] - target))
        if abs(then[0] - target) > 2 * DAY:
            return None
        return last / then[1] - 1

    def altseason_90(self) -> dict[str, Any]:
        """Share of the top altcoins whose 90-day return beats BTC's; unknown below ALT_MIN_KNOWN returns."""

        btc = self.return_days("bitcoin", ALT_DAYS)
        if btc is None:
            return {"share": None, "known": 0, "universe": 0}
        ids = self.top_altcoins()
        known, beating = 0, 0
        for gecko_id in ids:
            try:
                r = self.return_days(gecko_id, ALT_DAYS)
            except AltcoinDataError:
                r = None
            if r is None:
                continue
            known += 1
            beating += r > btc
        share = beating / known * 100 if known >= ALT_MIN_KNOWN else None
        return {"share": share, "known": known, "universe": len(ids), "btc_90d": btc}


# ---------------------------------------------------------------------------
# The weekly refresh
# ---------------------------------------------------------------------------


def _safe(exc: BaseException) -> str:
    if isinstance(exc, (LongDataError, AltcoinDataError)):
        return str(exc)[:80]
    return type(exc).__name__                                       # other exception text may carry URLs


def _round(value: Any, digits: int = 6) -> Any:
    """Fixed-decimal rounding for ratios and percentages (JSON-friendly); prices use ``_sig``."""

    return round(value, digits) if isinstance(value, float) and math.isfinite(value) else value


def _sig(value: Any, digits: int = 8) -> Any:
    """Significant-digit rounding for prices: a meme coin at 0.0000040 keeps its digits, 0.05 × that is not 0."""

    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value == 0:
        return value
    return round(float(value), digits - 1 - int(math.floor(math.log10(abs(value)))))


class LongWatch:
    """Builds the weekly snapshot for the register's coins and records the decisions in the ledger."""

    def __init__(self, register: rg.Register, mexc: Any, gecko: Any, ledger: Any, *,
                 maintenance: float = MAINTENANCE_MARGIN, fund_max: float = FUND_MAX,
                 loss_budget: Mapping[str, float] | None = None) -> None:
        self.register, self.mexc, self.gecko, self.ledger = register, mexc, gecko, ledger
        self.maintenance = max(0.0, float(maintenance))
        self.fund_max = float(fund_max)
        self.loss_budget = dict(loss_budget or LOSS_BUDGET)

    # -- market-wide ------------------------------------------------------

    def _regime(self, now: int, previous: Mapping[str, Any] | None, week0: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
        errors: list[str] = []
        archive: list[dict[str, Any]] = []
        if previous and isinstance(previous.get("btcd_archive"), list):
            archive = [dict(a) for a in previous["btcd_archive"] if isinstance(a, Mapping)]
        btc_d = None
        try:
            glob = self.gecko.global_()
            btc_d = glob.get("btc_dominance")
        except Exception as exc:
            errors.append("global: " + _safe(exc))
        if btc_d is not None and not any(a.get("week0") == week0 for a in archive):
            archive.append({"week0": week0, "btc_d": _round(btc_d, 3), "at": now})   # first value of the week
        archive = sorted(archive, key=lambda a: a["week0"])[-60:]
        then = next((a.get("btc_d") for a in archive if a.get("week0") == week0 - DIRECTION_WEEKS * WEEK), None)
        week_value = next((a.get("btc_d") for a in archive if a.get("week0") == week0), btc_d)
        alt: dict[str, Any] = {"share": None, "known": 0, "universe": 0}
        if previous and previous.get("alt90_week0") == week0 and isinstance(previous.get("alt90"), Mapping):
            alt = dict(previous["alt90"])                           # fifty charts once a week, not every tick
        else:
            try:
                alt = self.gecko.altseason_90()
            except Exception as exc:
                errors.append("alt90: " + _safe(exc))
        result = regime(btc_d=week_value, btc_d_then=then, alt90=alt.get("share"))
        result["alt90_known"], result["alt90_universe"] = alt.get("known"), alt.get("universe")
        return result, archive, errors

    # -- one coin -----------------------------------------------------------

    def _coin(self, coin: rg.Coin, *, now: int, market: Mapping[str, Any] | None, deriv: Mapping[str, Any] | None,
              deriv_ok: bool, regime_slice: str | None, register_age_days: int = 0,
              gate: Mapping[str, Any] | None = None, previous_gate: Mapping[str, Any] | None = None) -> dict[str, Any]:
        card: dict[str, Any] = {"symbol": coin.symbol, "name": coin.name, "kind": coin.kind, "gecko_id": coin.gecko_id,
                                "basket": coin.basket, "basket_label": coin.basket_label, "condition": coin.condition,
                                "rank": coin.rank, "venue": VENUE, "venue_symbol": coin.mexc_symbol,
                                "triggers": list(coin.triggers), "errors": []}
        price = market.get("price") if market else None
        market_cap = market.get("market_cap") if market else None
        spot_volume = market.get("spot_volume") if market else None
        if market is None:
            card["errors"].append("piyasa verisi yok")
        wick: dict[str, Any] = wick_reference([], now=now, price=None)
        chan: dict[str, Any] = channel([])
        perp_price = None
        mult = coin.mexc_price_multiplier
        try:
            candles = self.mexc.daily(coin.mexc_symbol, now=now)
            if mult != 1:                                              # quote per coin, not per 1000 coins
                candles = [Candle(c.day0, c.open / mult, c.high / mult, c.low / mult, c.close / mult, c.volume)
                           for c in candles]
            perp_price = candles[-1].close if candles else None
            wick = wick_reference(candles, now=now, price=perp_price)
            closes = [c for _, c in weekly_closes(candles, now=now)]
            chan = channel(closes)
        except Exception as exc:
            card["errors"].append("mum: " + _safe(exc))
        maintenance = self.maintenance
        margin_known = False                                        # the rule needs the exchange's rate: max(rate, floor)
        contract_size = coin.mexc_contract_size
        try:
            detail = self.mexc.detail(coin.mexc_symbol)
            if detail.get("maintenance_margin") is not None:
                maintenance = max(maintenance, float(detail["maintenance_margin"]))
                margin_known = True
            else:
                card["errors"].append("kontrat: sürdürme marjı yok")
            if detail.get("contract_size") is not None:
                contract_size = float(detail["contract_size"])
        except Exception as exc:
            card["errors"].append("kontrat: " + _safe(exc))
        venue_oi_usd = venue_volume = None
        try:
            ticker = self.mexc.ticker(coin.mexc_symbol)
            if ticker.get("oi_contracts") is not None and perp_price:
                venue_oi_usd = ticker["oi_contracts"] * contract_size * perp_price * mult   # contracts quote the venue unit
            venue_volume = ticker.get("volume_usdt_24h")
            card["funding_now"] = _round(ticker.get("funding_rate"))
        except Exception as exc:
            card["errors"].append("ticker: " + _safe(exc))
        fund = {"annual": None, "count": 0}
        try:
            fund = funding_90(self.mexc.funding(coin.mexc_symbol, since=now - FUND_DAYS * DAY), now=now)
        except Exception as exc:
            card["errors"].append("funding: " + _safe(exc))
        agg = deriv.get(coin.symbol) if deriv_ok and deriv else None
        if agg is not None and agg.get("incomplete"):
            card["errors"].append("türev: bir borsanın OI/hacim satırı bozuk; toplam bilinmiyor")
            agg = None                                              # a partial sum would understate the leverage
        thresholds = self.register.rules["position"]["risk_reduce"]
        build = leverage_buildup(oi_usd=agg["oi_usd"] if agg else None, market_cap=market_cap,
                                 perp_volume=agg["perp_volume_usd"] if agg else None, spot_volume=spot_volume,
                                 thresholds=thresholds)
        l_max = max_leverage(wick["worst"], maintenance=maintenance) if margin_known else None
        ref = wick["reference_price"]
        verdict = gate_verdict(gate, now=now, previous=previous_gate)
        decision = evaluate(coin, regime_slice=regime_slice, channel_state=chan["state"], worst=wick["worst"],
                            l_max=l_max, fund_annual=fund["annual"], buildup=build["triggered"], fund_max=self.fund_max,
                            margin_known=margin_known, register_age_days=register_age_days,
                            gate_known=verdict["known"], gate_block=verdict["block"])
        sigma = coin.risk.get("sigma") if coin.risk else None
        card.update({
            "price": _sig(price), "perp_close": _sig(perp_price), "price_multiplier": mult,
            "market_cap": _round(market_cap, 0), "spot_volume": _round(spot_volume, 0),
            "wick": {k: (_sig(v) if k == "reference_price" else _round(v)) for k, v in wick.items()},
            "maintenance_margin": _round(maintenance) if margin_known else None, "l_max": _round(l_max, 3),
            "liquidation_1x": (_sig(liquidation_price(perp_price, 1.0, maintenance=maintenance))
                               if perp_price and margin_known else None),
            "liquidation_lmax": (_sig(liquidation_price(perp_price, l_max, maintenance=maintenance))
                                 if perp_price and l_max and l_max >= 1 else None),
            "protection_stop": _sig(protection_stop(ref)) if ref and l_max and l_max > 1 else None,
            "fund90": {"annual": _round(fund["annual"]), "count": fund["count"]},
            "venue_oi_usd": _round(venue_oi_usd, 0), "venue_volume_usd": _round(venue_volume, 0),
            "aggregate": {"oi_usd": _round(agg["oi_usd"], 0) if agg else None,
                          "perp_volume_usd": _round(agg["perp_volume_usd"], 0) if agg else None,
                          "tickers": agg["tickers"] if agg else None, "markets": len(DERIVATIVE_MARKETS)},
            "oi_mcap": _round(build["oi_mcap"], 4), "perp_spot": _round(build["perp_spot"], 3),
            "buildup": build["triggered"],
            "channel": {k: (_sig(v) if k in ("high", "low") else v) for k, v in chan.items()},
            "scale_out": [_sig(perp_price * m) for m in SCALE_OUT] if perp_price else None,
            "share": _round(position_share(coin.kind, sigma, loss_budget=self.loss_budget), 5),
            "sigma": sigma,
            "position_cap_usd": coin.position_cap_usd,
            "gate": verdict,
            "signal": decision["signal"], "missing": decision["missing"],
        })
        if verdict["warning"]:
            card["errors"].append(verdict["warning"])              # shown as missing data; not a block yet
        return card

    # -- snapshot -------------------------------------------------------------

    def refresh(self, now: int, previous: Mapping[str, Any] | None = None,
                gates: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """Collect every series, evaluate every coin, record the week's decision once per coin.

        ``gates`` is the gate guard's latest result (altcoin_watch.gates_of); None or stale means every
        coin's gate check is unknown and no slice is produced."""

        week0 = week_start(day_start(now))
        ids = [c.gecko_id for c in self.register.coins]
        markets = self.gecko.markets_for(ids)                      # a failure here fails the refresh: no hollow snapshot
        errors: list[str] = []
        register_age_days = max(0, (day_start(now) - int(datetime.combine(
            self.register.data_as_of, datetime.min.time(), tzinfo=timezone.utc).timestamp())) // DAY)
        if register_age_days > REGISTER_MAX_AGE_DAYS:
            errors.append(f"kayıt bayat: rapor verisi {register_age_days} gün önce; yeni dilim yok")
        regime_result, archive, regime_errors = self._regime(now, previous, week0)
        errors += regime_errors
        deriv, deriv_ok = {}, False
        try:
            deriv, deriv_ok = self.gecko.derivatives(), True
        except Exception as exc:
            errors.append("türev: " + _safe(exc))
        previous_cards = {c.get("symbol"): c for c in (previous or {}).get("cards") or [] if isinstance(c, Mapping)}
        cards = [self._coin(c, now=now, market=markets.get(c.gecko_id), deriv=deriv, deriv_ok=deriv_ok,
                            regime_slice=regime_result["slice"], register_age_days=register_age_days,
                            gate=(gates or {}).get(c.symbol),
                            previous_gate=(previous_cards.get(c.symbol) or {}).get("gate"))
                 for c in self.register.coins]
        if gates is None:
            errors.append("kapı kontrolü yok (altcoin_watch anlık görüntüsü yok)")
        snapshot = {
            "generated_at": now, "week0": week0, "week": _iso(week0), "venue": VENUE,
            "register": dict(self.register.summary(), age_days=register_age_days,
                             stale=register_age_days > REGISTER_MAX_AGE_DAYS),
            "regime": regime_result, "btcd_archive": archive,
            "alt90": {"share": _round(regime_result.get("alt90"), 2), "known": regime_result.get("alt90_known"),
                      "universe": regime_result.get("alt90_universe")},
            "alt90_week0": week0 if regime_result.get("alt90") is not None or (previous or {}).get("alt90_week0") == week0 else None,
            "cards": cards, "errors": errors, "can_authorize_trade": False, "trial": TRIAL,
            "counts": {"signal": sum(1 for c in cards if c["signal"]), "coins": len(cards),
                       "errors": sum(1 for c in cards if c["errors"])},
        }
        self._record(snapshot)
        return snapshot

    def _record(self, snapshot: Mapping[str, Any]) -> int:
        """One KARAR event per coin per week (the first refresh of the week decides); later refreshes add none."""

        written = 0
        for card in snapshot["cards"]:
            event = {"id": f"{snapshot['week']}:{card['symbol']}:{EVENT_DECISION}", "event": EVENT_DECISION,
                     "week": snapshot["week"], "at": snapshot["generated_at"], "symbol": card["symbol"],
                     "basket": card["basket"], "signal": card["signal"], "missing": card["missing"],
                     "perp_close": card.get("perp_close"), "wick": card.get("wick"), "l_max": card.get("l_max"),
                     "fund90": card.get("fund90"), "oi_mcap": card.get("oi_mcap"), "perp_spot": card.get("perp_spot"),
                     "channel": card.get("channel"), "regime": snapshot["regime"].get("slice"),
                     "gate": card.get("gate"),
                     "register_sha256": snapshot["register"]["sha256"], "venue": VENUE,
                     "trial": TRIAL, "can_authorize_trade": False}
            if self.ledger.append(event):
                written += 1
        return written


# ---------------------------------------------------------------------------
# Telegram views
# ---------------------------------------------------------------------------


def fresh(snapshot: Mapping[str, Any] | None, *, now: int, max_age: int) -> bool:
    return bool(snapshot) and now - int(snapshot.get("generated_at") or 0) <= max_age   # type: ignore[union-attr]


def complete(snapshot: Mapping[str, Any] | None) -> bool:
    """A snapshot is complete when no series failed: no snapshot-level error and no card error. Health needs it."""

    if not snapshot:
        return False
    if snapshot.get("errors"):
        return False
    cards = snapshot.get("cards") or []
    return bool(cards) and not any(c.get("errors") for c in cards if isinstance(c, Mapping))


def find(snapshot: Mapping[str, Any] | None, query: str) -> Mapping[str, Any] | None:
    key = query.strip().upper()
    for card in (snapshot or {}).get("cards") or []:
        if isinstance(card, Mapping) and (str(card.get("symbol")).upper() == key or str(card.get("gecko_id")).upper() == key):
            return card
    return None


def _pct(value: Any, digits: int = 0) -> str:
    return f"{value * 100:+.{digits}f}%" if isinstance(value, (int, float)) and math.isfinite(value) else "?"


def _mult(value: Any, digits: int = 1) -> str:
    return f"{value:.{digits}f}×" if isinstance(value, (int, float)) and math.isfinite(value) else "?"


def _usd(value: Any) -> str:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return "?"
    for unit, div in (("Mr", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(value) >= div:
            return f"${value / div:,.1f}{unit}"
    return f"${value:,.0f}"


def _age(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{seconds // 60} dk"
    if seconds < DAY:
        return f"{seconds // 3600} sa"
    return f"{seconds // DAY} gün"


def _gate_text(gate: Mapping[str, Any] | None, now: int) -> str:
    if not gate or not gate.get("known"):
        return "bilinmiyor (kapı kontrolü yok ya da eski)"
    fails = gate.get("fails") or {}
    head = "❌ " + "; ".join(str(v) for v in fails.values()) if fails else "✅ ❌ yok"
    age = f" · kontrol {_age(now - int(gate['at']))} önce" if gate.get("at") else ""
    return (head + age)[:90]


def _regime_line(snapshot: Mapping[str, Any]) -> str:
    reg = snapshot.get("regime") or {}
    slice_ = reg.get("slice") or UNKNOWN
    btc = reg.get("btc_d")
    alt = reg.get("alt90")
    return (f"Rejim: {slice_} · BTC.D {btc:.1f}" if isinstance(btc, (int, float)) else f"Rejim: {slice_} · BTC.D ?") + \
           (f" ({reg.get('direction')})" if reg.get("direction") else "") + \
           (f" · ALT90 {alt:.0f}" if isinstance(alt, (int, float)) else " · ALT90 ?") + \
           (f" — {reg.get('reason')}" if reg.get("reason") else "")


def render(snapshot: Mapping[str, Any] | None, *, now: int, enabled: bool = True, error: str | None = None,
           max_age: int = 8 * DAY) -> str:
    head = "📒 <b>LONG TAKİBİ · v3.2 listesi · vadeli (MEXC) · gölge kayıt</b>"
    if not enabled:
        return head + "\n" + esc("Modül kapalı (LONG_WATCH_ENABLED=0).")
    if not snapshot:
        text = "İlk haftalık değerlendirme sürüyor (anahtarsız CoinGecko ile 15 dakikaya kadar)."
        if error:
            text = f"İlk değerlendirme yapılamadı. Hata: {error}."
        return head + "\n" + esc(text + " Liste rapordan gelir; bot seçmez.")
    age = now - int(snapshot.get("generated_at") or 0)
    reg = snapshot.get("register") or {}
    lines = [head, esc(f"Kayıt {reg.get('version')} ({reg.get('report_date')}) · değerlendirme {_age(age)} önce · "
                       f"hafta {snapshot.get('week')}")]
    if age > max_age or error:
        lines.append(esc(f"⚠️ Haftalık yenileme yapılamadı{': ' + error if error else ''}; son değerlendirme gösteriliyor."))
    if reg.get("stale"):
        lines.append(esc(f"⚠️ Kayıt bayat: rapor verisi {reg.get('age_days')} gün önce (> {REGISTER_MAX_AGE_DAYS}). "
                         "Yeni dilim yok; yeni rapor sürümü PR ile girilmeli."))
    lines.append(esc(_regime_line(snapshot)))
    cards = [c for c in snapshot.get("cards") or [] if isinstance(c, Mapping)]
    for kind, title in ((rg.ALTCOIN, "Altcoin çekirdek"), (rg.MEME, "Meme çekirdek")):
        core = [c for c in cards if c.get("kind") == kind and c.get("basket") in (rg.CORE, rg.CORE_CONDITIONAL)]
        rows = [f"{'Coin':<8}{'Durum':<9}{'Fitil':>6}{'Lmax':>5}{'Fund':>6}"]
        for c in core:
            wick = (c.get("wick") or {}).get("worst")
            state = c.get("signal") or ("koşul" if c.get("condition") else (c.get("missing") or ["?"])[0].split(" ")[0])
            rows.append(f"{str(c.get('symbol'))[:7]:<8}{state[:8]:<9}{_pct(wick):>6}{_mult(c.get('l_max')):>5}"
                        f"{_pct((c.get('fund90') or {}).get('annual')):>6}")
        lines += ["", f"<b>{title}</b>", "<pre>" + esc("\n".join(rows)) + "</pre>"]
    others = [c for c in cards if c.get("basket") not in (rg.CORE, rg.CORE_CONDITIONAL)]
    if others:
        lines += ["", esc("İzleme ve havuzlar (sinyal yok): " + ", ".join(
            f"{c.get('symbol')} ({c.get('basket_label')})" for c in others))]
    failed = [str(c.get("symbol")) for c in cards if c.get("errors")]
    if failed or snapshot.get("errors"):
        lines.append(esc("⚠️ Eksik veri: " + ", ".join(failed[:12] + [str(e) for e in snapshot.get("errors") or []][:3])))
    lines += ["", esc("Durum: DİLİM 1/2 = tüm koşullar sağlandı; diğer satırlar eksik koşulun adını yazar. "
                      "Fitil = son 365 gün en derin fitil / 730 gün döngü dibi (bugünkü fiyata göre); "
                      "Lmax = likidasyonu fitilin %20 altında tutan kaldıraç (≤ 2×); Fund = 90 günlük yıllık funding."),
              esc(f"Ayrıntı: /long <sembol>. {FOOTER} Emir yetkisi yok.")]
    text = "\n".join(lines)
    return text if len(text) <= 4096 else text[:4000] + "\n…"


def render_card(card: Mapping[str, Any], *, now: int, snapshot: Mapping[str, Any] | None = None) -> str:
    sym = esc(card.get("symbol"))
    lines = [f"📒 <b>{sym}</b> · {esc(card.get('name'))} · {esc(card.get('basket_label'))}"
             + (f" · rapor sırası {card.get('rank')}" if card.get("rank") else "")]
    if card.get("condition"):
        lines.append(esc(f"Koşul: {card['condition']}"))
    if snapshot:
        lines.append(esc(_regime_line(snapshot)))
    signal = card.get("signal")
    lines.append(esc(f"Durum: {signal if signal else 'sinyal yok — ' + '; '.join(card.get('missing') or ['?'])}"))
    wick = card.get("wick") or {}
    chan = card.get("channel") or {}
    fund = card.get("fund90") or {}
    rows = [
        f"{'Perp kapanış':<14}{_num(card['perp_close']) if card.get('perp_close') else '?'}"
        + (f"  (MEXC sözleşmesi {card['price_multiplier']} coin kote eder; fiyatlar coin başına)" if card.get('price_multiplier', 1) != 1 else ""),
        f"{'Spot fiyat':<14}{_num(card['price']) if card.get('price') else '?'}  (anlık; karar fiyatı değil)",
        f"{'WICK365':<14}{_pct(wick.get('wick365'))}",
        f"{'FLASH365':<14}{_pct(wick.get('flash365'))}",
        f"{'CYCLE_LOW':<14}{_pct(wick.get('cycle_low'))}",
        f"{'Fitil ref.':<14}{_num(wick['reference_price']) if wick.get('reference_price') else '?'}  (en kötüsü {_pct(wick.get('worst'))})",
        f"{'Sürdürme marj':<14}{_pct(card.get('maintenance_margin'), 1).lstrip('+')}"
        + ("" if card.get('maintenance_margin') is not None else "  (borsa oranı alınamadı)"),
        f"{'L_max':<14}{_mult(card.get('l_max'), 2)}  (kod sınırı {MAX_LEVERAGE:.0f}×)",
        f"{'Likidasyon 1×':<14}{_num(card['liquidation_1x']) if card.get('liquidation_1x') else '?'}",
    ]
    if card.get("liquidation_lmax"):
        rows.append(f"{'Likid. L_max':<14}{_num(card['liquidation_lmax'])}")
    if card.get("protection_stop"):
        rows.append(f"{'Marj stopu':<14}{_num(card['protection_stop'])}  (yalnız 1× üstünde)")
    rows += [
        f"{'FUND90':<14}{_pct(fund.get('annual'))} yıllık ({fund.get('count') or 0} ödeme)",
        f"{'Funding şimdi':<14}{_pct(card.get('funding_now'), 4)}",
        f"{'OI / pd':<14}{_pct(card.get('oi_mcap'))}  ({(card.get('aggregate') or {}).get('tickers') or 0} sözleşme, {(card.get('aggregate') or {}).get('markets')} borsa)",
        f"{'Perp / spot':<14}{_mult(card.get('perp_spot'))}",
        f"{'Kaldıraç bir.':<14}{'EVET' if card.get('buildup') else 'hayır' if card.get('buildup') is False else '?'}",
        f"{'Kanal 10h':<14}{chan.get('state') or '?'}  üst {_num(chan['high']) if chan.get('high') else '?'} alt {_num(chan['low']) if chan.get('low') else '?'} ({chan.get('weeks') or 0} hafta)",
        f"{'Kapılar':<14}{_gate_text(card.get('gate'), now)}",
        f"{'2× / 4×':<14}{' / '.join(_num(v) for v in card['scale_out']) if card.get('scale_out') else '?'}  (funding düzeltmesi pozisyonla)",
        f"{'Pay':<14}{_pct(card.get('share'), 2).lstrip('+') if card.get('share') is not None else '? (σ raporda yok)'} sermaye  "
        f"· likidite üst sınırı {_usd(card.get('position_cap_usd'))}",
        f"{'MEXC OI':<14}{_usd(card.get('venue_oi_usd'))}  · 24s hacim {_usd(card.get('venue_volume_usd'))}",
    ]
    lines.append("<pre>" + esc("\n".join(rows)) + "</pre>")
    if card.get("triggers"):
        lines.append(esc("Tetikleyiciler: " + ", ".join(card["triggers"])))
    if card.get("errors"):
        lines.append(esc("⚠️ Eksik veri: " + "; ".join(str(e) for e in card["errors"])))
    lines.append(esc(f"{FOOTER} Emir yetkisi yok. Kaynak: MEXC vadeli (mum, funding, OI), CoinGecko (piyasa, toplu OI)."))
    return "\n".join(lines)


def status_line(snapshot: Mapping[str, Any] | None, *, now: int, enabled: bool, max_age: int,
                error: str | None = None) -> str:
    if not enabled:
        return "Long takibi: kapalı"
    if not snapshot:
        return "Long takibi: ilk değerlendirme bekleniyor" + (f" ({error})" if error else "")
    counts = snapshot.get("counts") or {}
    age = _age(now - int(snapshot.get("generated_at") or 0))
    stale = " · ⚠️ bayat" if not fresh(snapshot, now=now, max_age=max_age) else ""
    return (f"Long takibi: {counts.get('coins', 0)} coin, {counts.get('signal', 0)} sinyal, "
            f"{counts.get('errors', 0)} eksik veri · {age} önce{stale}" + (f" · {error}" if error else ""))
