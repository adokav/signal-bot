"""Descriptive atlas of the crypto majors, 2017-09..2024-08: market, macro and calendar, month by month.

docs/ATLAS.md explains how to read it. The atlas is for understanding, not a
signal: it learns no threshold, ranks nothing and makes no decision. Ideas it
suggests become hypotheses that must be pre-registered and tested on data the
atlas has not shown. That is why nothing after 2024-08-31 is read or shown:
the 2024-09..2026-08 confirmation window stays sealed.

Layers:

- **market** (Binance spot, closed 15m candles turned into daily candles):
  the point-in-time majors universe (BTC, ETH, top 10 by 30-day volume,
  largest meme; no perpetual condition, as perpetuals start in 2019-09),
  returns, volatility, drawdown, correlation, breadth, BTC's volume share;
- **macro** (FRED, downloaded in GitHub Actions because this container
  cannot reach FRED): rates, the real rate, the Fed funds target, the Fed
  balance sheet, the Treasury account, reverse repo, EUR/USD, USD/CNY,
  Brent oil, VIX and equities. FRED serves the current vintage, so only series that are not
  revised after publication are used (``FRED_SERIES``); revised ones (M2,
  the trade-weighted dollar, ...) would leak revisions published in the
  sealed window and stay out until they come from ALFRED vintages;
- **calendar**: halvings and Fed rate changes (both known when they happen),
  plus a short list of crypto shocks that are only known in hindsight and
  are shown as context, never used as inputs.

Missing data stays missing: a month without enough observations shows n/a,
never zero. No order authority (AGENTS.md §4).
"""

from __future__ import annotations

import csv
import io
import json
import math
import statistics
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


ATLAS_START = 1_504_224_000          # 2017-09-01
ATLAS_END = 1_725_148_800            # 2024-09-01: the confirmation window starts here and stays sealed
DAY = 86_400

# Only series that are not revised after publication: market prices and rates,
# policy settings and operational records. FRED serves the current vintage, so a
# revised series (M2, the trade-weighted dollar, GDP, CPI, payrolls) would carry
# revisions published inside the sealed 2024-09..2026-08 window back into the
# atlas. Those stay out until they come from ALFRED vintages as of 2024-08-31.
FRED_SERIES = {
    "us_2y": "DGS2",                  # Treasury yields (H.15): market rates
    "us_10y": "DGS10",
    "us_10y_real": "DFII10",
    "fed_upper": "DFEDTARU",          # policy setting
    "fed_assets_musd": "WALCL",       # H.4.1 record, millions of US dollars, weekly (Wednesday)
    "tga_busd": "WTREGEN",            # H.4.1 record, billions of US dollars, weekly
    "rrp_busd": "RRPONTSYD",          # NY Fed operation results, billions of US dollars, daily
    "eur_usd": "DEXUSEU",             # H.10 noon rate, US dollars per euro: a falling value is a stronger dollar
    "usd_cny": "DEXCHUS",             # H.10 noon rate, yuan per US dollar: the trade-war channel (2018-2019)
    "brent": "DCOILBRENTEU",          # EIA Brent spot, US dollars per barrel: the Middle East channel
    "vix": "VIXCLS",
    "nasdaq": "NASDAQCOM",
    "sp500": "SP500",
}
PCT_CHANGE = frozenset({"nasdaq", "eur_usd", "usd_cny", "brent"})   # prices: change in %, rates: in points
REVISED_SERIES = frozenset({"M2SL", "DTWEXBGS", "GDP", "GDPC1", "CPIAUCSL", "PCEPI", "PAYEMS", "UNRATE", "INDPRO"})
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
MAX_STALE_DAYS = 40                    # a month-end value older than this is missing, not carried

HALVINGS = {"2020-05-11": "Bitcoin halving (3.)", "2024-04-20": "Bitcoin halving (4.)"}
# Known only in hindsight: context for reading the atlas, never an input (dates from public reporting).
SHOCKS = {
    "2017-09-04": "Çin ICO yasağı",
    "2017-12-17": "CME BTC vadelileri; BTC zirvesi",
    "2018-01-26": "Coincheck hack",
    "2018-11-15": "BCH çatallanma savaşı, çöküş",
    "2019-05-07": "Binance hack",
    "2019-06-18": "Facebook Libra duyurusu",
    "2020-03-12": "COVID çöküşü",
    "2020-08-11": "MicroStrategy BTC alımı",
    "2020-10-21": "PayPal kripto desteği",
    "2021-02-08": "Tesla BTC alımı",
    "2021-04-14": "Coinbase halka arz",
    "2021-05-19": "Çin baskısı, çöküş",
    "2021-09-07": "El Salvador BTC yasal para",
    "2021-09-24": "Çin kripto yasağı",
    "2022-05-09": "LUNA/UST çöküşü",
    "2022-06-13": "Celsius / 3AC krizi",
    "2022-09-15": "Ethereum Merge",
    "2022-11-08": "FTX çöküşü",
    "2023-03-10": "SVB çöküşü, USDC sapması",
    "2023-06-15": "BlackRock spot ETF başvurusu",
    "2024-01-10": "Spot BTC ETF onayı",
    "2024-08-05": "Yen carry çözülmesi",
}


# ---------------------------------------------------------------------------
# Macro (FRED)
# ---------------------------------------------------------------------------


def parse_fred_csv(text: str) -> list[tuple[date, float]]:
    """(day, value) rows of a fredgraph CSV; missing values ('.', '') are skipped, not zeroed."""

    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 2:
        raise ValueError("not a FRED CSV")
    out = []
    for row in rows[1:]:
        if len(row) < 2 or row[1].strip() in {"", "."}:
            continue
        try:
            day, value = date.fromisoformat(row[0].strip()), float(row[1])
        except ValueError:
            continue
        if math.isfinite(value):
            out.append((day, value))
    return sorted(out)


def fetch_fred(series_id: str, *, start: date, end: date, session=None, retries: int = 4) -> list[tuple[date, float]]:
    import requests

    session = session or requests.Session()
    last = "unknown"
    for attempt in range(retries):
        try:
            response = session.get(FRED_URL, params={"id": series_id, "cosd": start.isoformat(),
                                                     "coed": end.isoformat()}, timeout=60)
        except requests.RequestException as exc:
            last = type(exc).__name__                       # exception text can carry the URL
        else:
            if response.status_code == 200:
                return [(d, v) for d, v in parse_fred_csv(response.text) if start <= d <= end]
            last = f"HTTP {response.status_code}"
        time.sleep(2.0 * 2 ** attempt)
    raise RuntimeError(f"FRED {series_id}: {last} after {retries} attempts")


def month_ends(start: int = ATLAS_START, end: int = ATLAS_END) -> list[date]:
    """Last calendar day of every month in [start, end)."""

    d = datetime.fromtimestamp(start, tz=timezone.utc).date().replace(day=1)
    stop = datetime.fromtimestamp(end, tz=timezone.utc).date()
    out = []
    while d < stop:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append(nxt - timedelta(days=1))
        d = nxt
    return out


def value_at(rows: Sequence[tuple[date, float]], day: date, *, max_stale: int = MAX_STALE_DAYS) -> float | None:
    """Last value on or before ``day``, if it is at most ``max_stale`` days old."""

    best = None
    for d, v in rows:
        if d > day:
            break
        best = (d, v)
    if best is None or (day - best[0]).days > max_stale:
        return None
    return best[1]


def macro_monthly(series: Mapping[str, Sequence[tuple[date, float]]], months: Sequence[date]) -> list[dict]:
    """One row per month end: levels, monthly changes, net liquidity and the Fed's rate moves."""

    out, prev = [], None
    for month_end in months:
        row: dict[str, Any] = {"month": month_end.strftime("%Y-%m")}
        for name, rows in series.items():
            row[name] = value_at(rows, month_end)
        assets, tga, rrp = row.get("fed_assets_musd"), row.get("tga_busd"), row.get("rrp_busd")
        row["net_liquidity_busd"] = (assets / 1000.0 - tga - rrp) if None not in (assets, tga, rrp) else None
        y2, y10 = row.get("us_2y"), row.get("us_10y")
        row["curve_10y_2y"] = (y10 - y2) if None not in (y2, y10) else None
        first = month_end.replace(day=1)
        moves = [(d, v) for d, v in series.get("fed_upper", ()) if first <= d <= month_end]
        before = value_at(series.get("fed_upper", ()), first - timedelta(days=1), max_stale=10)
        row["fed_move_bp"] = (round((moves[-1][1] - before) * 100) if moves and before is not None else None)
        for name in ("us_10y_real", "net_liquidity_busd", "eur_usd", "usd_cny", "brent", "vix", "nasdaq", "us_2y"):
            now, then = row.get(name), (prev or {}).get(name)
            if now is None or then is None:
                row[f"{name}_chg"] = None
            elif name in PCT_CHANGE:
                row[f"{name}_chg"] = (now / then - 1.0) * 100.0
            else:
                row[f"{name}_chg"] = now - then
        out.append(row)
        prev = row
    return out


def write_macro(rows: Sequence[Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(rows[0]) if rows else []
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def read_macro(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            out[row["month"]] = {k: (None if v in {"", "None"} else (v if k == "month" else float(v)))
                                 for k, v in row.items()}
    return out


# ---------------------------------------------------------------------------
# Market (Binance spot daily candles from closed 15m candles)
# ---------------------------------------------------------------------------


@dataclass
class Panel:
    symbols: list[str]
    day0: int                        # 00:00 UTC of global day 0
    close: np.ndarray                # (symbols, days)
    quote_volume: np.ndarray
    resumed: np.ndarray              # True on a day data resumed after a hole of more than a day

    @property
    def index_of(self) -> dict[str, int]:
        return {s: k for k, s in enumerate(self.symbols)}


def atlas_breaks(market: Any) -> dict[int, np.ndarray]:
    """Continuity breaks (``weekly_momentum.continuity_breaks``) minus exchange-wide halts.

    A pair's hole of more than a day that BTCUSDT shares, starting and ending
    within a day of BTC's, is the exchange stopping (Binance, 2018-02-08..10),
    not a token swap: the same token trades on both sides. Without BTC in the
    market nothing can be told apart, so every break stays a break.
    """

    from trading.backtest import liquid_replay as lr
    from trading.backtest import weekly_momentum as wm

    breaks = wm.continuity_breaks(market)
    if "BTCUSDT" not in market.symbols:
        return breaks
    b = market.symbols.index("BTCUSDT")
    btc = [(int(r), _hole_start(market, b, int(r))) for r in breaks.get(b, ())]
    if not btc:
        return breaks
    out = {}
    for s, bars in breaks.items():
        keep = [int(r) for r in bars
                if not any(abs(int(r) - br) <= lr.DAY_BARS and abs(_hole_start(market, s, int(r)) - bs) <= lr.DAY_BARS
                           for br, bs in btc)]
        if keep:
            out[s] = np.asarray(keep, dtype=np.int64)
    return out


def _hole_start(market: Any, s: int, resume: int) -> int:
    """First missing bar of the hole that ends at ``resume``."""

    known = np.nonzero(np.isfinite(market.close[s, :resume]))[0]
    return int(known[-1]) + 1 if len(known) else 0


def _daily_close(close: np.ndarray, bars: int) -> np.ndarray:
    """Last known 15m close of each day (NaN for a day without any): a missing 23:45 bar does not erase the day."""

    days = close.shape[1] // bars
    block = close[:, : days * bars].reshape(close.shape[0], days, bars)
    ok = np.isfinite(block)
    last = bars - 1 - np.argmax(ok[:, :, ::-1], axis=2)
    out = np.take_along_axis(block, last[..., None], axis=2)[..., 0]
    return np.where(ok.any(axis=2), out, np.nan)


def market_history(markets: Sequence[Any]) -> list[tuple[np.ndarray, dict[int, np.ndarray]]]:
    """Per contiguous market, oldest first: each pair's listing time and its breaks.

    The listing time comes from all older data, not just this market, so a
    coin listed shortly before a dataset boundary is not seasoned by it. A
    pair first seen in a later dataset counts as listed when that dataset
    first shows it (conservative: its older history is not in the data).
    Breaks are pair-specific holes of more than a day, inside the market
    (``atlas_breaks``) or across its junction with the older ones.
    """

    from trading.backtest import majors_signals as ms

    out: list[tuple[np.ndarray, dict[int, np.ndarray]]] = []
    last_seen: dict[str, int] = {}
    listed: dict[str, int] = {}
    for market in markets:
        breaks = atlas_breaks(market)
        for s, bars in _junction_gaps(market, last_seen).items():
            breaks[s] = np.union1d(breaks.get(s, np.array([], dtype=np.int64)), bars).astype(np.int64)
        local = ms.first_trade_times(market)
        first = np.array([listed.get(symbol, int(local[s])) for s, symbol in enumerate(market.symbols)],
                         dtype=np.int64)
        out.append((first, breaks))
        for s, symbol in enumerate(market.symbols):
            seen = np.nonzero(np.isfinite(market.close[s]))[0]
            if len(seen):
                last_seen[symbol] = int(market.grid_open[seen[-1]])
                listed[symbol] = ms.listed_since(market, s, np.iinfo(np.int64).max, first, breaks)
    return out


def build_panel(markets: Sequence[Any], *, start: int = ATLAS_START, end: int = ATLAS_END) -> Panel:
    """Daily closes and volumes of every pair over [start, end), from contiguous 15m markets, oldest first.

    ``resumed`` marks the day data comes back after a pair-specific hole of
    more than a day, inside one market or across the junction of two, so no
    return or trend is measured across a possible token swap.
    """

    from trading.backtest import liquid_replay as lr

    symbols = sorted({s for m in markets for s in m.symbols})
    index = {s: k for k, s in enumerate(symbols)}
    n_days = (end - start) // DAY
    close = np.full((len(symbols), n_days), np.nan)
    volume = np.full((len(symbols), n_days), np.nan)
    resumed = np.zeros((len(symbols), n_days), dtype=bool)
    for market, (_, breaks) in zip(markets, market_history(markets)):
        day0 = int(market.grid_open[0])
        if day0 % DAY:
            raise ValueError("the 15m grid must start at 00:00 UTC")
        days = market.n_grid // lr.DAY_BARS
        shape = (len(market.symbols), days, lr.DAY_BARS)
        closes = _daily_close(market.close, lr.DAY_BARS)
        vols = np.nansum(market.quote_volume[:, : days * lr.DAY_BARS].reshape(shape), axis=2)
        known = np.isfinite(market.close[:, : days * lr.DAY_BARS].reshape(shape)).any(axis=2)
        offset = (day0 - start) // DAY
        lo, hi = max(0, -offset), min(days, n_days - offset)
        for s, symbol in enumerate(market.symbols):
            g = index[symbol]
            close[g, offset + lo: offset + hi] = closes[s, lo:hi]
            volume[g, offset + lo: offset + hi] = np.where(known[s, lo:hi], vols[s, lo:hi], np.nan)
            for bar in breaks.get(s, ()):
                d = int(bar) // lr.DAY_BARS + offset
                if 0 <= d < n_days:
                    resumed[g, d] = True
    return Panel(symbols, start, close, volume, resumed)


def _junction_gaps(market: Any, last_seen: Mapping[str, int]) -> dict[int, np.ndarray]:
    """First bars of pairs whose data resumes in ``market`` more than a day after an older market last saw them.

    A gap BTC shares at the junction is an exchange-wide halt, like in ``atlas_breaks``.
    """

    def gap(s: int, symbol: str) -> tuple[int, int] | None:
        seen = np.nonzero(np.isfinite(market.close[s]))[0]
        if symbol not in last_seen or not len(seen):
            return None
        first = int(market.grid_open[seen[0]])
        return (int(seen[0]), last_seen[symbol]) if first - last_seen[symbol] - 900 > DAY else None

    btc = gap(market.symbols.index("BTCUSDT"), "BTCUSDT") if "BTCUSDT" in market.symbols else None
    out = {}
    for s, symbol in enumerate(market.symbols):
        g = gap(s, symbol)
        if g is None:
            continue
        shared = btc is not None and abs(g[0] - btc[0]) * 900 <= DAY and abs(g[1] - btc[1]) <= DAY
        if not shared:
            out[s] = np.array([g[0]], dtype=np.int64)
    return out


def _ema(close: np.ndarray, resumed: np.ndarray, span: int, *, min_known: float = 0.9) -> np.ndarray:
    """EMA restarted at every resume day; NaN until ``min_known`` of the last ``span`` closes since then are known.

    Gaps are carried forward inside a segment (the atlas describes, it does
    not trade); a resume is a new token, so nothing before it is carried.
    """

    import pandas as pd

    out = np.full(close.shape, np.nan)
    for s in range(close.shape[0]):
        cuts = [0, *np.nonzero(resumed[s])[0].tolist(), close.shape[1]]
        for a, b in zip(cuts[:-1], cuts[1:]):
            seg = pd.Series(close[s, a:b])
            if b - a < span or not seg.notna().any():
                continue
            ema = seg.ffill().ewm(span=span, adjust=False).mean().to_numpy()
            known = seg.notna().rolling(span, min_periods=span).sum().to_numpy() >= min_known * span
            out[s, a:b] = np.where(known, ema, np.nan)
    return out


def _month_slices(panel: Panel) -> list[tuple[str, int, int]]:
    out = []
    for month_end in month_ends(panel.day0, panel.day0 + panel.close.shape[1] * DAY):
        first = month_end.replace(day=1)
        a = (int(datetime(first.year, first.month, 1, tzinfo=timezone.utc).timestamp()) - panel.day0) // DAY
        b = (int(datetime(month_end.year, month_end.month, month_end.day, tzinfo=timezone.utc).timestamp())
             - panel.day0) // DAY
        out.append((month_end.strftime("%Y-%m"), a, b))
    return out


def member_return(panel: Panel, s: int, a: int, b: int) -> float | None:
    """Return (%) of holding pair ``s`` from the close before day ``a`` to the close of day ``b``.

    Predeclared treatment, so a member that fails is never silently dropped:
    - a pair that stops trading for good inside the month is sold at its last
      close (LUNA in 2022-05: about -100 %, not a missing value);
    - a pair whose data resumes after a pair-specific hole (a possible token
      swap) is sold at the old token's last close before the hole;
    - a pair with no close at the month end that trades again later without
      a break, or with no close before the month, is unknown (None).
    """

    if a < 1:
        return None
    close = panel.close[s]
    c0 = close[a - 1]
    if not np.isfinite(c0):
        return None
    resumed = np.nonzero(panel.resumed[s, a:b + 1])[0]
    if len(resumed):
        exit_at = a + int(resumed[0])                                   # first day of the new token
    elif np.isfinite(close[b]):
        return float((close[b] / c0 - 1.0) * 100.0)
    elif np.isfinite(close[b + 1:]).any():
        return None                                                     # a hole at the month end, not an exit
    else:
        exit_at = b + 1                                                 # stopped for good
    seg = close[a - 1:exit_at]
    last = seg[np.isfinite(seg)][-1]
    return float((last / c0 - 1.0) * 100.0)


def market_monthly(panel: Panel, universes: Mapping[str, Sequence[str]]) -> list[dict]:
    """One row per month: BTC, the equal-weight majors basket, volatility, correlation, breadth, volume share."""

    idx = panel.index_of
    ema50, ema200 = _ema(panel.close, panel.resumed, 50), _ema(panel.close, panel.resumed, 200)
    with np.errstate(invalid="ignore", divide="ignore"):
        log_ret = np.diff(np.log(panel.close), axis=1, prepend=np.nan)
    log_ret[panel.resumed] = np.nan                                   # never a return across a token-swap gap
    btc = idx.get("BTCUSDT")
    running_max = np.fmax.accumulate(np.where(np.isfinite(panel.close[btc]), panel.close[btc], -np.inf))
    out = []
    for month, a, b in _month_slices(panel):
        members = [m for m in universes.get(month, ()) if m in idx]
        row: dict[str, Any] = {"month": month, "members": len(members), "universe": " ".join(m[:-4] for m in members)}

        def month_return(s: int) -> float | None:
            return member_return(panel, s, a, b)

        def vol(s: int) -> float | None:
            x = log_ret[s, a:b + 1]
            x = x[np.isfinite(x)]
            return float(x.std(ddof=1) * math.sqrt(365) * 100.0) if len(x) >= 20 else None

        # A member with no close on the day before the month cannot be bought at its start; that is
        # visible at the decision time, so leaving it out is not hindsight. It is counted.
        investable = [m for m in members if a >= 1 and np.isfinite(panel.close[idx[m], a - 1])]
        row["not_trading_at_start"] = len(members) - len(investable)
        rets = {m: month_return(idx[m]) for m in investable}
        complete = len(rets) >= 8 and all(v is not None for v in rets.values())     # no survivor-only average
        row["unknown_returns"] = sum(v is None for v in rets.values())
        row["btc_ret_pct"] = month_return(btc)
        row["basket_ret_pct"] = statistics.fmean(rets.values()) if complete else None
        alts = [v for m, v in rets.items() if m != "BTCUSDT"]
        row["alts_minus_btc_pct"] = (statistics.fmean(alts) - row["btc_ret_pct"]
                                     if complete and "BTCUSDT" in rets else None)
        row["dispersion_pct"] = statistics.stdev(rets.values()) if complete else None
        row["btc_close"] = float(panel.close[btc, b]) if np.isfinite(panel.close[btc, b]) else None
        row["btc_drawdown_pct"] = (float((panel.close[btc, b] / running_max[b] - 1.0) * 100.0)
                                   if np.isfinite(panel.close[btc, b]) and running_max[b] > 0 else None)
        row["btc_vol_pct"] = vol(btc)
        vols = [v for m in members if (v := vol(idx[m])) is not None]
        row["basket_vol_pct"] = statistics.fmean(vols) if len(vols) >= 8 else None
        series = np.array([log_ret[idx[m], a:b + 1] for m in members]) if members else np.empty((0, 0))
        ok = [k for k in range(len(members)) if np.isfinite(series[k]).sum() >= 20]
        if len(ok) >= 8:
            import pandas as pd

            corr = pd.DataFrame(series[ok].T).corr(min_periods=20).to_numpy()
            upper = corr[np.triu_indices(len(ok), 1)]
            row["avg_correlation"] = float(np.nanmean(upper))
        else:
            row["avg_correlation"] = None
        above = [panel.close[idx[m], b] > ema50[idx[m], b] for m in members
                 if np.isfinite(panel.close[idx[m], b]) and np.isfinite(ema50[idx[m], b])]
        row["breadth_above_ema50"] = (sum(above) / len(above)) if len(above) >= 8 else None
        e200, e200_prev = ema200[btc, b], ema200[btc, b - 20] if b >= 20 else np.nan
        c = panel.close[btc, b]
        if np.isfinite(c) and np.isfinite(e200) and np.isfinite(e200_prev):
            rising = e200 > e200_prev
            row["btc_trend"] = "YUKSELIS" if c > e200 and rising else "DUSUS" if c < e200 and not rising else "GECIS"
        else:
            row["btc_trend"] = None
        v = row["btc_vol_pct"]
        row["btc_vol_state"] = None if v is None else "SAKIN" if v < 40 else "FIRTINALI" if v > 80 else "NORMAL"
        total = np.nansum([np.nansum(panel.quote_volume[idx[m], a:b + 1]) for m in members])
        btc_vol_usd = np.nansum(panel.quote_volume[btc, a:b + 1])
        row["btc_volume_share"] = float(btc_vol_usd / total) if total > 0 and "BTCUSDT" in members else None
        out.append(row)
    return out


def calendar_notes(month: str, macro: Mapping[str, Any] | None) -> str:
    notes = [f"{d[8:]}: {text}" for d, text in sorted(HALVINGS.items()) if d.startswith(month)]
    move = (macro or {}).get("fed_move_bp")
    if move:
        notes.append(f"Fed {'+' if move > 0 else ''}{int(move)} bp")
    notes += [f"{d[8:]}: {text} (sonradan bilinen)" for d, text in sorted(SHOCKS.items()) if d.startswith(month)]
    return "; ".join(notes)


def combine(market: Sequence[Mapping[str, Any]], macro: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    out = []
    for row in market:
        m = macro.get(row["month"], {})
        merged = {**row, **{k: v for k, v in m.items() if k != "month"}}
        merged["calendar"] = calendar_notes(row["month"], m)
        out.append(merged)
    return out


def phases(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Consecutive months with the same BTC trend state, with what happened inside them."""

    out: list[dict] = []
    for row in rows:
        state = row.get("btc_trend") or "BILINMIYOR"
        if not out or out[-1]["state"] != state:
            out.append({"state": state, "rows": []})
        out[-1]["rows"].append(row)
    summary = []
    for ph in out:
        rs = ph["rows"]

        def compound(key: str) -> float | None:
            vals = [r.get(key) for r in rs]
            if any(v is None for v in vals):
                return None
            return (math.prod(1 + v / 100.0 for v in vals) - 1.0) * 100.0

        def mean(key: str) -> float | None:
            vals = [r[key] for r in rs if r.get(key) is not None]
            return statistics.fmean(vals) if vals else None

        def change(key: str) -> float | None:
            a, b = rs[0].get(key), rs[-1].get(key)
            first_change = rs[0].get(f"{key}_chg")
            if a is None or b is None or first_change is None:
                return None
            start = a - first_change if key not in PCT_CHANGE else a / (1 + first_change / 100.0)
            return (b - start) if key not in PCT_CHANGE else (b / start - 1.0) * 100.0

        summary.append({
            "state": ph["state"], "from": rs[0]["month"], "to": rs[-1]["month"], "months": len(rs),
            "btc_ret_pct": compound("btc_ret_pct"), "basket_ret_pct": compound("basket_ret_pct"),
            "btc_vol_pct": mean("btc_vol_pct"), "avg_correlation": mean("avg_correlation"),
            "real_rate_change": change("us_10y_real"), "net_liquidity_change_busd": change("net_liquidity_busd"),
            "eur_usd_change_pct": change("eur_usd"), "nasdaq_change_pct": change("nasdaq"),
            "brent_change_pct": change("brent"), "usd_cny_change_pct": change("usd_cny"),
            "fed_moves_bp": sum(int(r.get("fed_move_bp") or 0) for r in rs),
            "events": [r["calendar"] for r in rs if r.get("calendar")],
        })
    return summary


def market_universes(market: Any, end: int, *, first_trade: np.ndarray,
                     breaks: Mapping[int, np.ndarray]) -> dict[str, list[str]]:
    """Majors universe of every month the market can rank (30 days of volume), spot only.

    ``first_trade`` and ``breaks`` come from ``market_history``: listing ages
    run across dataset boundaries and exchange-wide halts are not breaks. The
    month opening at ``end`` is included: it is ranked from data before it,
    which lets the next dataset start without a 30-day hole.
    """

    from trading.backtest import majors_signals as ms

    out = {}
    for m in ms.month_starts((int(market.grid_open[0]) + 30 * DAY, min(end, ATLAS_END) + DAY)):
        label = datetime.fromtimestamp(m, tz=timezone.utc).strftime("%Y-%m")
        out[label] = ms.monthly_universe(market, m, perp_of={}, funding_times={}, first_trade=first_trade,
                                         breaks=breaks, require_perp=False, history_start=ATLAS_START)
    return out


def verify_datasets(spot_dirs: Sequence[Path]) -> list[dict]:
    """Manifests of complete, contiguous datasets covering exactly 2017-09..2024-08; otherwise refuse (fail closed)."""

    from trading.backtest import trade_loop as tl

    manifests = []
    for spot_dir in spot_dirs:
        path = spot_dir / "manifest.json"
        if not path.exists():
            raise SystemExit(f"{spot_dir}: no manifest; incomplete or foreign dataset")
        manifest = json.loads(path.read_text("utf-8"))
        window = tuple(manifest.get("window") or ())
        if len(window) != 2 or not isinstance(manifest.get("data_end"), int):
            raise SystemExit(f"{spot_dir}: manifest without a window or data end")
        manifests.append(tl.verify_universe_manifest(spot_dir, end=manifest["data_end"] + 1, window=window))
    first = datetime.fromtimestamp(ATLAS_START, tz=timezone.utc).strftime("%Y-%m")
    if not manifests or manifests[0]["window"][0] != first:
        raise SystemExit(f"the oldest dataset must start in {first}")
    for older, newer in zip(manifests, manifests[1:]):
        nxt = datetime.fromtimestamp(older["data_end"] + 1, tz=timezone.utc).strftime("%Y-%m")
        if newer["window"][0] != nxt:
            raise SystemExit(f"datasets are not contiguous: {older['window']} then {newer['window']}")
    if manifests[-1]["data_end"] + 1 != ATLAS_END:
        raise SystemExit("the newest dataset must end on 2024-08-31: the atlas never reads the sealed window")
    return manifests


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Descriptive 2017-09..2024-08 atlas (docs/ATLAS.md).")
    sub = parser.add_subparsers(dest="mode", required=True)
    macro = sub.add_parser("macro", help="download FRED series and write the monthly macro table")
    macro.add_argument("--out", type=Path, required=True)
    build = sub.add_parser("build", help="market layer from local data + the macro table -> atlas rows")
    build.add_argument("--spot-dir", type=Path, action="append", required=True,
                       help="liquid-universe data directories, oldest first")
    build.add_argument("--macro", type=Path, help="monthly macro table from the atlas_macro workflow; "
                       "without it the macro columns are missing, not zero")
    build.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.mode == "macro":
        if REVISED_SERIES & set(FRED_SERIES.values()):
            raise SystemExit("a revised FRED series would carry revisions from the sealed window; use ALFRED")
        start = datetime.fromtimestamp(ATLAS_START, tz=timezone.utc).date() - timedelta(days=400)
        end = datetime.fromtimestamp(ATLAS_END - DAY, tz=timezone.utc).date()
        series = {name: fetch_fred(sid, start=start, end=end) for name, sid in FRED_SERIES.items()}
        rows = macro_monthly(series, month_ends())
        write_macro(rows, args.out)
        print(f"wrote {len(rows)} months to {args.out}; observations "
              + json.dumps({k: len(v) for k, v in series.items()}, sort_keys=True))
        return 0

    from trading.backtest import signal_quality as sq

    markets, ends, universes = [], [], {}
    for spot_dir, manifest in zip(args.spot_dir, verify_datasets(args.spot_dir)):
        ends.append(int(manifest["data_end"]) + 1)
        markets.append(sq.load_likit_market(spot_dir, end=ends[-1])[0])
    for market, end, (first_trade, breaks) in zip(markets, ends, market_history(markets)):
        for label, members in market_universes(market, end, first_trade=first_trade, breaks=breaks).items():
            universes.setdefault(label, members)
    panel = build_panel(markets)
    rows = combine(market_monthly(panel, universes), read_macro(args.macro) if args.macro else {})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(args.out.name + ".tmp")
    tmp.write_text(json.dumps({"rows": rows, "phases": phases(rows), "sealed_from": "2024-09",
                               "macro": "fred-unrevised-series" if args.macro else "missing",
                               "can_authorize_trade": False}, indent=1, default=str), "utf-8")
    tmp.replace(args.out)
    print(f"wrote {len(rows)} months and {len(phases(rows))} phases to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
