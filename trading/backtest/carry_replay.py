"""Funding-carry replay: long spot + short USDⓈ-M perpetual, survivorship-free.

Hypothesis (spec §14): leveraged long demand makes perpetual funding
positive on average; a delta-neutral position (long spot, short perp)
collects that funding. The premium is real in the literature but comes with
crash risk (basis blow-outs, exchange failure) and must survive four legs of
trading costs. This replay measures whether it did, point in time.

Timing (AGENTS.md §1): decisions are taken at each 8h funding boundary
``O_k`` (00/08/16 UTC) using only funding paid at or before ``O_k`` and
candles closed before ``O_k``. Positions are opened at the open of the
candle starting at ``O_k`` — after that funding was paid — so the first
funding a position earns is the one at ``O_{k+1}``.

Variants (pre-registered, docs/CARRY_REPLAY_REPORT.md):

- ``STATIC_CARRY`` — carry every member of the point-in-time top 20 perps by
  trailing 30-day quote volume.
- ``SIGNED_CARRY`` — the same, only while the trailing 7-day funding sum is
  positive (needs a full week of funding history).

Returns are per unit of notional on one leg; a fully margined (1x) hedge
needs twice that in capital. Delisted pairs exit at their last close.

No order authority anywhere (AGENTS.md §4, §10).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from trading.research.robustness import bootstrap_mean_ci


PERIOD = 8 * 3600
PERIODS_PER_DAY = 3
VOLUME_PERIODS = 90          # 30 days of 8h candles
SIGNAL_PERIODS = 21          # 7 days of 8h funding windows
UNIVERSE_SIZE = 20
VARIANTS = ("STATIC_CARRY", "SIGNED_CARRY")
FAMILY_ALPHA = 0.05 / len(VARIANTS)
MIN_DAYS = 500
BLOCK_DAYS = 10
MARGIN_STRESS = (1.5, 2.0)   # perp high vs entry: 2x-margin and 1x-margin danger zones

REPO_ROOT = Path(__file__).resolve().parents[2]
FINGERPRINT_FILES = (
    "acce_unified/cex.py",
    "trading/data/binance_vision.py",
    "trading/data/binance_universe.py",
    "trading/data/binance_carry_universe.py",
    "trading/backtest/carry_replay.py",
)


@dataclass(frozen=True)
class CarryCosts:
    spot_fee_bps: float = 7.5
    perp_fee_bps: float = 5.0
    slippage_bps_per_leg: float = 2.0

    def one_way_pct(self, *, fee_mult: float = 1.0, slip_mult: float = 1.0) -> float:
        """Opening (or closing) both legs, as % of one leg's notional."""

        fees = (self.spot_fee_bps + self.perp_fee_bps) * fee_mult
        return (fees + 2 * self.slippage_bps_per_leg * slip_mult) / 100.0


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass
class CarryMarket:
    symbols: list[str]
    grid_open: np.ndarray
    perp_open: np.ndarray
    perp_high: np.ndarray
    perp_close: np.ndarray
    perp_qv: np.ndarray
    spot_open: np.ndarray
    spot_close: np.ndarray
    funding: np.ndarray          # sum of rates paid in (O_k, O_k+1], per symbol and period
    funding_rows: np.ndarray     # how many funding payments fell in that window
    rejected_rows: int = 0

    @property
    def n(self) -> int:
        return len(self.grid_open)


KLINE_COLUMNS = ("open_time", "open", "high", "low", "close", "quote_volume")


def _clean_klines(cols: Mapping[str, np.ndarray]) -> tuple[dict[str, np.ndarray], int]:
    t = np.asarray(cols["open_time"], dtype=np.int64)
    o, h, low, c, qv = (np.asarray(cols[k], dtype=float) for k in ("open", "high", "low", "close", "quote_volume"))
    with np.errstate(invalid="ignore"):
        ok = (
            (t % PERIOD == 0) & np.isfinite(o) & np.isfinite(h) & np.isfinite(low) & np.isfinite(c) & np.isfinite(qv)
            & (np.minimum(np.minimum(o, h), np.minimum(low, c)) > 0)
            & (h >= np.maximum(o, c)) & (low <= np.minimum(o, c)) & (qv >= 0)
        )
    idx = np.nonzero(ok)[0]
    order = idx[np.argsort(t[idx], kind="stable")]
    _, first = np.unique(t[order], return_index=True)
    keep = order[first]
    return {"open_time": t[keep], "open": o[keep], "high": h[keep], "close": c[keep], "quote_volume": qv[keep]}, \
        int(len(t) - len(keep))


def build_market(
    perp: Mapping[str, Mapping[str, np.ndarray]],
    spot: Mapping[str, Mapping[str, np.ndarray]],
    funding: Mapping[str, Sequence[tuple[int, float]]],
) -> CarryMarket:
    """Align perp/spot 8h candles and funding windows on one grid (NaN where missing)."""

    clean_perp, clean_spot, rejected = {}, {}, 0
    for symbol in perp:
        if symbol not in spot:
            continue
        p, rp = _clean_klines(perp[symbol])
        s, rs = _clean_klines(spot[symbol])
        rejected += rp + rs
        if len(p["open_time"]) and len(s["open_time"]):
            clean_perp[symbol], clean_spot[symbol] = p, s
    if not clean_perp:
        raise ValueError("no usable perp/spot pairs")
    symbols = sorted(clean_perp)
    start = min(int(min(clean_perp[s]["open_time"][0], clean_spot[s]["open_time"][0])) for s in symbols)
    end = max(int(max(clean_perp[s]["open_time"][-1], clean_spot[s]["open_time"][-1])) for s in symbols)
    grid = np.arange(start, end + PERIOD, PERIOD, dtype=np.int64)
    shape = (len(symbols), len(grid))
    mats = {k: np.full(shape, np.nan) for k in
            ("perp_open", "perp_high", "perp_close", "perp_qv", "spot_open", "spot_close")}
    fund = np.zeros(shape)
    fund_rows = np.zeros(shape, dtype=np.int64)
    for k, symbol in enumerate(symbols):
        p, s = clean_perp[symbol], clean_spot[symbol]
        ip, is_ = (p["open_time"] - start) // PERIOD, (s["open_time"] - start) // PERIOD
        mats["perp_open"][k, ip], mats["perp_high"][k, ip] = p["open"], p["high"]
        mats["perp_close"][k, ip], mats["perp_qv"][k, ip] = p["close"], p["quote_volume"]
        mats["spot_open"][k, is_], mats["spot_close"][k, is_] = s["open"], s["close"]
        for t, rate in funding.get(symbol, ()):
            if not (math.isfinite(rate) and -1.0 < rate < 1.0):
                rejected += 1
                continue
            j = math.ceil((int(t) - start) / PERIOD) - 1  # (O_j, O_j+1] contains t
            if 0 <= j < len(grid):
                fund[k, j] += rate
                fund_rows[k, j] += 1
    return CarryMarket(symbols, grid, mats["perp_open"], mats["perp_high"], mats["perp_close"], mats["perp_qv"],
                       mats["spot_open"], mats["spot_close"], fund, fund_rows, rejected)


def load_market(data_dir: Path) -> tuple[CarryMarket, dict]:
    import pandas as pd

    manifest = json.loads((data_dir / "manifest.json").read_text("utf-8"))
    perp, spot, funding = {}, {}, {}
    for symbol in manifest["symbols"]:
        p = pd.read_parquet(data_dir / "perp" / f"{symbol}.parquet")
        s = pd.read_parquet(data_dir / "spot" / f"{symbol}.parquet")
        perp[symbol] = {c: p[c].to_numpy() for c in KLINE_COLUMNS}
        spot[symbol] = {c: s[c].to_numpy() for c in KLINE_COLUMNS}
        path = data_dir / "funding" / f"{symbol}.parquet"
        if path.exists():
            f = pd.read_parquet(path)
            funding[symbol] = list(zip(f["funding_time"].tolist(), f["rate"].tolist()))
    return build_market(perp, spot, funding), manifest


# ---------------------------------------------------------------------------
# Point-in-time universe, signal and portfolio
# ---------------------------------------------------------------------------


def trailing_volume(market: CarryMarket) -> np.ndarray:
    """Sum of perp quote volume over the 90 candles closed before O_k (NaN unless all exist)."""

    qv = market.perp_qv
    present = np.isfinite(qv)
    csum = np.concatenate([np.zeros((qv.shape[0], 1)), np.cumsum(np.where(present, qv, 0.0), axis=1)], axis=1)
    ccount = np.concatenate([np.zeros((qv.shape[0], 1)), np.cumsum(present, axis=1)], axis=1)
    out = np.full(qv.shape, np.nan)
    if qv.shape[1] > VOLUME_PERIODS:
        window_sum = csum[:, VOLUME_PERIODS:-1] - csum[:, :-VOLUME_PERIODS - 1]
        window_count = ccount[:, VOLUME_PERIODS:-1] - ccount[:, :-VOLUME_PERIODS - 1]
        out[:, VOLUME_PERIODS:] = np.where(window_count == VOLUME_PERIODS, window_sum, np.nan)
    return out


def trailing_funding(market: CarryMarket) -> tuple[np.ndarray, np.ndarray]:
    """Funding paid in (O_k - 7d, O_k] and whether every 8h window in it had a payment."""

    f = np.concatenate([np.zeros((market.funding.shape[0], 1)), np.cumsum(market.funding, axis=1)], axis=1)
    covered = (market.funding_rows > 0).astype(np.int64)
    c = np.concatenate([np.zeros((covered.shape[0], 1), dtype=np.int64), np.cumsum(covered, axis=1)], axis=1)
    total = np.full(market.funding.shape, np.nan)
    complete = np.zeros(market.funding.shape, dtype=bool)
    if market.n > SIGNAL_PERIODS:
        # windows j = k-21 .. k-1 end at or before O_k
        total[:, SIGNAL_PERIODS:] = f[:, SIGNAL_PERIODS:-1] - f[:, :-SIGNAL_PERIODS - 1]
        complete[:, SIGNAL_PERIODS:] = (c[:, SIGNAL_PERIODS:-1] - c[:, :-SIGNAL_PERIODS - 1]) == SIGNAL_PERIODS
    return total, complete


def universe_at(market: CarryMarket, volume: np.ndarray, k: int, size: int = UNIVERSE_SIZE) -> list[int]:
    eligible = np.isfinite(volume[:, k]) & np.isfinite(market.perp_open[:, k]) & np.isfinite(market.spot_open[:, k])
    idx = np.nonzero(eligible)[0]
    order = sorted(idx.tolist(), key=lambda s: (-volume[s, k], market.symbols[s]))
    return order[:size]


@dataclass
class PortfolioPath:
    period_open: list[int] = field(default_factory=list)
    returns: list[float] = field(default_factory=list)
    funding: list[float] = field(default_factory=list)
    basis: list[float] = field(default_factory=list)
    costs: list[float] = field(default_factory=list)
    positions: list[int] = field(default_factory=list)
    forced_exits: int = 0
    funding_gaps: int = 0
    negative_funding_periods: int = 0
    position_periods: int = 0
    entries: int = 0
    margin_stress: dict[str, int] = field(default_factory=dict)
    stressed_symbols: set[str] = field(default_factory=set)


def simulate(market: CarryMarket, variant: str, costs: CarryCosts, *, fee_mult: float = 1.0,
             slip_mult: float = 1.0, size: int = UNIVERSE_SIZE) -> PortfolioPath:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    one_way = costs.one_way_pct(fee_mult=fee_mult, slip_mult=slip_mult)
    volume = trailing_volume(market)
    signal, complete = trailing_funding(market)
    path = PortfolioPath(margin_stress={f"{m:g}x": 0 for m in MARGIN_STRESS})
    held: dict[int, float] = {}     # symbol -> perp entry price of the current episode
    flagged: dict[int, set[float]] = {}
    for k in range(market.n - 1):
        universe = universe_at(market, volume, k, size)
        if variant == "STATIC_CARRY":
            target = universe
        else:
            target = [s for s in universe if complete[s, k] and signal[s, k] > 0]
        exits = [s for s in held if s not in target]
        cost = one_way * len(exits) / max(1, len(held)) if held else 0.0
        for s in exits:
            held.pop(s)
            flagged.pop(s, None)
        if not target:
            if exits or path.returns:  # cash period once the strategy has started
                path.period_open.append(int(market.grid_open[k]))
                path.returns.append(-cost)
                path.funding.append(0.0)
                path.basis.append(0.0)
                path.costs.append(cost)
                path.positions.append(0)
            continue
        rets, funds, bases = [], [], []
        for s in target:
            if s not in held:
                held[s] = float(market.perp_open[s, k])
                flagged[s] = set()
                path.entries += 1
                cost += one_way / len(target)
            p0, s0 = market.perp_open[s, k], market.spot_open[s, k]
            p1, s1 = market.perp_open[s, k + 1], market.spot_open[s, k + 1]
            forced = not (math.isfinite(p1) and math.isfinite(s1))
            if forced:  # data stops (delisting/halt): close both legs at this candle's close
                p1, s1 = market.perp_close[s, k], market.spot_close[s, k]
                cost += one_way / len(target)
                path.forced_exits += 1
            basis = (s1 / s0 - 1.0) * 100.0 - (p1 / p0 - 1.0) * 100.0
            fund = 0.0 if forced else market.funding[s, k] * 100.0
            if not forced and market.funding_rows[s, k] == 0:
                path.funding_gaps += 1
            if fund < 0:
                path.negative_funding_periods += 1
            for m in MARGIN_STRESS:
                if m not in flagged[s] and market.perp_high[s, k] >= m * held[s]:
                    flagged[s].add(m)
                    path.margin_stress[f"{m:g}x"] += 1
                    path.stressed_symbols.add(market.symbols[s])
            rets.append(basis + fund)
            funds.append(fund)
            bases.append(basis)
            path.position_periods += 1
            if forced:
                held.pop(s)
                flagged.pop(s, None)
        path.period_open.append(int(market.grid_open[k]))
        path.returns.append(float(np.mean(rets)) - cost)
        path.funding.append(float(np.mean(funds)))
        path.basis.append(float(np.mean(bases)))
        path.costs.append(cost)
        path.positions.append(len(target))
    return path


# ---------------------------------------------------------------------------
# Statistics and verdict
# ---------------------------------------------------------------------------


def daily(path: PortfolioPath, values: Sequence[float] | None = None) -> tuple[list[int], list[float]]:
    """Daily sums over complete UTC days (a partial first or last day would bias the mean)."""

    days: dict[int, float] = {}
    counts: dict[int, int] = {}
    for t, r in zip(path.period_open, path.returns if values is None else values):
        days[t // 86_400] = days.get(t // 86_400, 0.0) + r
        counts[t // 86_400] = counts.get(t // 86_400, 0) + 1
    keys = sorted(d for d in days if counts[d] == PERIODS_PER_DAY)
    return keys, [days[d] for d in keys]


def max_drawdown(values: Sequence[float]) -> float:
    equity = peak = worst = 0.0
    for v in values:
        equity += v
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


@dataclass(frozen=True)
class VariantStats:
    name: str
    days: int
    days_with_positions: int
    mean_daily_pct: float | None
    annual_return_pct: float | None
    annual_vol_pct: float | None
    sharpe: float | None
    max_drawdown_pct: float | None
    worst_day_pct: float | None
    ci95_daily: tuple[float, float] | None
    ci_family_daily: tuple[float, float] | None
    stressed_annual_return_pct: float | None
    first_half_annual_pct: float | None
    second_half_annual_pct: float | None
    annual_funding_pct: float | None
    annual_basis_pct: float | None
    annual_cost_pct: float | None
    avg_positions: float
    entries: int
    forced_exits: int
    funding_gaps: int
    negative_funding_share: float | None
    margin_stress: dict[str, int]
    stressed_symbols: list[str]
    verdict: str

    def to_dict(self) -> dict:
        return asdict(self)


def _annual(values: Sequence[float]) -> float | None:
    return float(np.mean(values) * 365) if len(values) else None


def variant_stats(name: str, path: PortfolioPath, stressed: PortfolioPath, *, seed: int = 0,
                  decision_group: bool = True) -> VariantStats:
    """PASS_CANDIDATE needs >= 500 days with positions, a family-wise block-bootstrap lower bound
    of mean daily net return above zero, positive return under stressed costs and positive
    returns in both chronological halves."""

    _, rets = daily(path)
    _, s_rets = daily(stressed)
    held_days = len({t // 86_400 for t, n in zip(path.period_open, path.positions) if n > 0})
    ci95 = bootstrap_mean_ci(rets, block_size=BLOCK_DAYS, seed=seed) if len(rets) > 2 * BLOCK_DAYS else None
    ci_family = (bootstrap_mean_ci(rets, block_size=BLOCK_DAYS, alpha=FAMILY_ALPHA, n_resamples=4000, seed=seed)
                 if len(rets) > 2 * BLOCK_DAYS else None)
    half = len(rets) // 2
    first, second = _annual(rets[:half]), _annual(rets[half:])
    vol = float(np.std(rets, ddof=1) * math.sqrt(365)) if len(rets) > 1 else None
    annual = _annual(rets)
    stressed_annual = _annual(s_rets)
    if not decision_group:
        verdict = "DIAGNOSTIC_ONLY"
    elif ci_family is not None and ci_family[1] < 0:
        verdict = "NEGATIVE"
    elif held_days < MIN_DAYS:
        verdict = "INSUFFICIENT"
    elif (ci_family is not None and ci_family[0] > 0 and stressed_annual is not None and stressed_annual > 0
          and first is not None and first > 0 and second is not None and second > 0):
        verdict = "PASS_CANDIDATE"
    else:
        verdict = "NO_EDGE"
    _, f = daily(path, path.funding)
    _, b = daily(path, path.basis)
    _, c = daily(path, path.costs)
    periods = max(1, path.position_periods)
    return VariantStats(
        name=name, days=len(rets), days_with_positions=held_days,
        mean_daily_pct=float(np.mean(rets)) if rets else None, annual_return_pct=annual, annual_vol_pct=vol,
        sharpe=(annual / vol) if annual is not None and vol else None,
        max_drawdown_pct=max_drawdown(rets) if rets else None, worst_day_pct=min(rets) if rets else None,
        ci95_daily=ci95, ci_family_daily=ci_family, stressed_annual_return_pct=stressed_annual,
        first_half_annual_pct=first, second_half_annual_pct=second,
        annual_funding_pct=_annual(f), annual_basis_pct=_annual(b), annual_cost_pct=_annual(c),
        avg_positions=float(np.mean(path.positions)) if path.positions else 0.0,
        entries=path.entries, forced_exits=path.forced_exits, funding_gaps=path.funding_gaps,
        negative_funding_share=path.negative_funding_periods / periods if path.position_periods else None,
        margin_stress=dict(path.margin_stress), stressed_symbols=sorted(path.stressed_symbols)[:30],
        verdict=verdict,
    )


def by_year(path: PortfolioPath) -> dict[str, float]:
    days, rets = daily(path)
    years: dict[str, list[float]] = {}
    for d, r in zip(days, rets):
        years.setdefault(str(datetime.fromtimestamp(d * 86_400, tz=timezone.utc).year), []).append(r)
    return {y: float(np.mean(v) * 365) for y, v in sorted(years.items())}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def engine_fingerprint(repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in FINGERPRINT_FILES:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


@dataclass
class CarryReport:
    start_time: int
    end_time: int
    symbols: int
    periods: int
    rejected_rows: int
    costs: CarryCosts
    engine_fingerprint: str
    variants: dict[str, VariantStats]
    by_year: dict[str, dict[str, float]]
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "start_time": self.start_time, "end_time": self.end_time, "symbols": self.symbols,
            "periods": self.periods, "rejected_rows": self.rejected_rows, "costs": asdict(self.costs),
            "engine_fingerprint": self.engine_fingerprint,
            "variants": {k: v.to_dict() for k, v in self.variants.items()},
            "by_year": self.by_year, "notes": list(self.notes), "can_authorize_trade": False,
        }


def run_replay(market: CarryMarket, *, costs: CarryCosts = CarryCosts(), fingerprint: str = "unknown",
               size: int = UNIVERSE_SIZE) -> CarryReport:
    variants, years = {}, {}
    for variant in VARIANTS:
        path = simulate(market, variant, costs, size=size)
        stressed = simulate(market, variant, costs, fee_mult=1.5, slip_mult=2.0, size=size)
        variants[variant] = variant_stats(variant, path, stressed)
        years[variant] = by_year(path)
    notes = [
        "Binance USDⓈ-M perp + Binance spot; canlı uygulama MEXC'de olursa funding ve maliyetler farklıdır.",
        f"Maliyet: spot {costs.spot_fee_bps:g} bp + perp {costs.perp_fee_bps:g} bp komisyon, bacak başına "
        f"{costs.slippage_bps_per_leg:g} bp slippage (açılış veya kapanış: %{costs.one_way_pct():.3f}); stres komisyon x1.5, slippage x2.",
        "Getiri tek bacağın nominaline göre; 1x teminatlı hedge iki katı sermaye ister (yıllık getiri sermayeye göre yarıya iner).",
        "Pozisyon boyutu dönem başına eşit ağırlık; aradaki yeniden dengeleme maliyeti modellenmedi.",
        "Teminat stresi: perp fiyatı girişin 1.5 / 2 katına ulaştığında kısa bacak 2x / 1x teminatta tasfiye riski taşır; "
        "tasfiye modellenmedi, sayısı raporlanır.",
        f"Karar eşiği: ≥{MIN_DAYS} pozisyonlu gün, Bonferroni (α={FAMILY_ALPHA:g}) blok bootstrap alt sınırı > 0, "
        "stres maliyetiyle yıllık getiri > 0, iki kronolojik yarı > 0.",
    ]
    if market.rejected_rows:
        notes.append(f"UYARI: {market.rejected_rows} bozuk/tekrarlı satır atıldı (onarılmadı).")
    for v in variants.values():
        if v.funding_gaps > 0.01 * max(1, v.avg_positions * v.days * PERIODS_PER_DAY):
            notes.append(f"UYARI: {v.name} pozisyon dönemlerinin %1'inden fazlasında funding kaydı yok (0 sayıldı).")
    return CarryReport(
        start_time=int(market.grid_open[0]), end_time=int(market.grid_open[-1]) + PERIOD - 1,
        symbols=len(market.symbols), periods=market.n, rejected_rows=market.rejected_rows, costs=costs,
        engine_fingerprint=fingerprint, variants=variants, by_year=years, notes=notes,
    )


def _fmt(value, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def print_summary(report: CarryReport) -> None:
    print(f"symbols={report.symbols} periods={report.periods} rejected_rows={report.rejected_rows} "
          f"engine={report.engine_fingerprint}")
    for v in report.variants.values():
        fam = "n/a" if v.ci_family_daily is None else f"[{v.ci_family_daily[0]*365:+.2f},{v.ci_family_daily[1]*365:+.2f}]"
        print(f"{v.name}: days={v.days} held_days={v.days_with_positions} annual={_fmt(v.annual_return_pct, '+.2f')}% "
              f"famCI(annual)={fam}% vol={_fmt(v.annual_vol_pct, '.2f')}% sharpe={_fmt(v.sharpe, '.2f')} "
              f"maxDD={_fmt(v.max_drawdown_pct, '.2f')}% worst_day={_fmt(v.worst_day_pct, '.2f')}% "
              f"stress={_fmt(v.stressed_annual_return_pct, '+.2f')}% halves={_fmt(v.first_half_annual_pct, '+.2f')}/"
              f"{_fmt(v.second_half_annual_pct, '+.2f')}% -> {v.verdict}")
        print(f"   decomposition/yr: funding={_fmt(v.annual_funding_pct, '+.2f')}% basis={_fmt(v.annual_basis_pct, '+.2f')}% "
              f"costs={_fmt(v.annual_cost_pct, '.2f')}% · avg_positions={v.avg_positions:.1f} entries={v.entries} "
              f"forced_exits={v.forced_exits} funding_gaps={v.funding_gaps} "
              f"neg_funding_share={_fmt(v.negative_funding_share, '.2f')} margin_stress={v.margin_stress}")
        print(f"   by year: {report.by_year.get(v.name)}")
        if v.stressed_symbols:
            print(f"   margin-stressed: {', '.join(v.stressed_symbols)}")
    for note in report.notes:
        print(f"note: {note}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


REPLAY_FAMILY = "funding_carry_spot_perp"


def replay_trial_params(*, fingerprint: str, costs: CarryCosts) -> dict:
    return {
        "engine_fingerprint": fingerprint,
        "period_seconds": PERIOD,
        "universe_size": UNIVERSE_SIZE,
        "volume_periods": VOLUME_PERIODS,
        "signal_periods": SIGNAL_PERIODS,
        "variants": list(VARIANTS),
        "costs": asdict(costs),
        "outcome": "daily_net_return_per_notional_long_spot_short_perp",
    }


def _cli(argv: Iterable[str] | None = None) -> int:
    import argparse

    from trading.research.report import StrategyReport
    from trading.research.robustness import TrialRecord, TrialRegistry, trial_id_for
    from trading.strategies.carry_dossier import CARRY_BLOW_UP_SCENARIOS, CARRY_FRAGILITY_ANSWERS, CARRY_HYPOTHESIS

    parser = argparse.ArgumentParser(description="Replay the spot/perp funding carry.")
    parser.add_argument("--data-dir", type=Path, default=Path("research/data/carry_universe"))
    parser.add_argument("--out", type=Path, default=Path("research/data/carry_replay.json"))
    parser.add_argument("--trial-registry", type=Path, default=Path("research/trials/registry.jsonl"))
    parser.add_argument("--record-trial", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    started = time.time()
    market, manifest = load_market(args.data_dir)
    costs = CarryCosts()
    fingerprint = engine_fingerprint()
    report = run_replay(market, costs=costs, fingerprint=fingerprint)
    stopped = manifest.get("stopped_before_window_end") or []
    report.notes.append(f"Evren: {manifest.get('candidates')} aday perp, {len(stopped)} tanesinin verisi pencere bitmeden duruyor.")
    if not stopped:
        report.notes.append("UYARI: verisi erken biten hiç perp yok; survivorship'ten arındırma doğrulanamadı.")
    payload = report.to_dict()
    payload["universe_manifest"] = {k: v for k, v in manifest.items() if k != "symbols"}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str), "utf-8")
    tmp.replace(args.out)
    args.out.with_suffix(".md").write_text(StrategyReport(
        hypothesis=CARRY_HYPOTHESIS, fragility_answers=CARRY_FRAGILITY_ANSWERS,
        blow_up_scenarios=CARRY_BLOW_UP_SCENARIOS,
        evidence={k: payload[k] for k in ("variants", "notes", "engine_fingerprint", "can_authorize_trade")},
    ).render_markdown(), "utf-8")

    params = replay_trial_params(fingerprint=fingerprint, costs=costs)
    trial_id = trial_id_for(family=REPLAY_FAMILY, params=params, dataset={})
    registry = TrialRegistry(args.trial_registry)
    known = {r.trial_id for r in registry.selection_trials(REPLAY_FAMILY)}
    print(f"trial {trial_id} ({'pre-registered' if trial_id in known else 'NOT pre-registered'})"
          f" · family trials: {len(known | {trial_id})}")
    if args.record_trial:
        registry.append(TrialRecord(
            trial_id=trial_id, family=REPLAY_FAMILY, kind="SELECTION_CANDIDATE",
            description="carry replay CLI run", params=params,
            dataset={"start": report.start_time, "end": report.end_time},
            recorded_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            n_trades=sum(v.entries for v in report.variants.values()), sharpe_per_trade=None,
        ))
    print_summary(report)
    print(f"wrote {args.out} and {args.out.with_suffix('.md')} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli())
