"""Orchestration layer: scan, isolate failures, annotate without authority."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .cex import rank_cex_tickers
from .config import UnifiedConfig
from .dex import rank_dex_pairs
from .models import RadarSnapshot
from .providers import DexScreenerProvider, MexcPublicProvider


log = logging.getLogger(__name__)


class UnifiedRadarEngine:
    def __init__(
        self,
        config: UnifiedConfig,
        trade_universe: dict[str, str],
        *,
        cex_provider: Any | None = None,
        dex_provider: Any | None = None,
    ):
        self.config = config
        self.trade_universe = dict(trade_universe)
        self.cex_provider = cex_provider or MexcPublicProvider(
            timeout=config.request_timeout_seconds
        )
        self.dex_provider = dex_provider or DexScreenerProvider(
            timeout=config.request_timeout_seconds,
            max_tokens_per_chain=config.dex_max_tokens_per_chain,
        )

    def scan_once(self, *, now: int | None = None) -> RadarSnapshot:
        timestamp = int(now if now is not None else time.time())
        cex_candidates = []
        dex_candidates = []
        errors: list[str] = []
        if self.config.cex_enabled:
            try:
                cex_candidates = rank_cex_tickers(
                    self.cex_provider.fetch_tickers(),
                    trade_universe=self.trade_universe,
                    top_n=self.config.cex_top_n,
                    min_quote_volume=self.config.cex_min_quote_volume,
                )
            except Exception as exc:
                errors.append(f"CEX:{type(exc).__name__}")
                log.warning("Unified CEX radar failed: %s", exc)
        if self.config.dex_enabled:
            try:
                pairs, profiles, boosted, takeovers = self.dex_provider.fetch_pairs()
                dex_candidates = rank_dex_pairs(
                    pairs,
                    profiles=profiles,
                    boosted=boosted,
                    takeovers=takeovers,
                    top_n=self.config.dex_top_n,
                    min_liquidity=self.config.dex_min_liquidity,
                    min_h1_transactions=self.config.dex_min_h1_transactions,
                    max_age_hours=self.config.dex_max_age_hours,
                    now_ms=timestamp * 1000,
                )
            except Exception as exc:
                errors.append(f"DEX:{type(exc).__name__}")
                log.warning("Unified DEX radar failed: %s", exc)
        return RadarSnapshot(
            generated_at=timestamp,
            mode=self.config.mode,
            cex_candidates=tuple(cex_candidates),
            dex_candidates=tuple(dex_candidates),
            errors=tuple(errors),
        )


class UnifiedRadarRuntime:
    """Non-blocking periodic runner.

    Network discovery never occupies the main trade loop. A failed scan leaves
    the last good snapshot intact and can never change an existing trade.
    """

    def __init__(self, engine: UnifiedRadarEngine):
        self.engine = engine
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="unified-radar")
        self._future: Future[RadarSnapshot] | None = None
        self._latest: RadarSnapshot | None = None
        self._next_due = 0.0
        self._lock = threading.RLock()

    def tick(self, *, now: float | None = None, force: bool = False) -> RadarSnapshot | None:
        current = float(now if now is not None else time.time())
        with self._lock:
            if self._future is not None and self._future.done():
                try:
                    completed = self._future.result()
                    # A total provider outage must not erase the last known-good view.
                    if (
                        self._latest is None
                        or not completed.errors
                        or completed.cex_candidates
                        or completed.dex_candidates
                    ):
                        self._latest = completed
                except Exception as exc:  # defensive; scan_once isolates provider failures
                    log.warning("Unified radar future failed: %s", exc)
                finally:
                    self._future = None
                    self._next_due = current + self.engine.config.scan_interval_seconds
            if (
                self.engine.config.enabled
                and self._future is None
                and (force or current >= self._next_due)
            ):
                self._future = self._executor.submit(self.engine.scan_once, now=int(current))
            return self._latest

    def latest(self) -> RadarSnapshot | None:
        with self._lock:
            return self._latest

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def attach_snapshot_to_results(results: list[dict], snapshot: RadarSnapshot | None) -> None:
    """Attach confluence metadata without mutating signal/actionable fields."""

    if snapshot is None:
        return
    by_symbol = {candidate.symbol: candidate for candidate in snapshot.cex_candidates}
    for result in results:
        symbol = str(result.get("symbol") or "").upper()
        candidate = by_symbol.get(symbol)
        result["unified_radar"] = {
            "mode": snapshot.mode,
            "can_change_trade_decision": False,
            "candidate": candidate.to_dict() if candidate else None,
        }


def _candidate_line(item: dict | None) -> str:
    if not item:
        return "yok"
    return f"{item.get('symbol', '?')} {int(item.get('score', 0))}/100 ({item.get('stage', '-')})"


def format_snapshot_brief(snapshot: RadarSnapshot | dict | None) -> str:
    if snapshot is None:
        return "Unified Radar: ilk gölge tarama bekleniyor."
    data = snapshot.to_dict() if isinstance(snapshot, RadarSnapshot) else snapshot
    cex = (data.get("cex_candidates") or [None])[0]
    dex = (data.get("dex_candidates") or [None])[0]
    errors = data.get("errors") or []
    error_text = f" | hata: {', '.join(errors)}" if errors else ""
    return (
        f"Unified Radar [{data.get('mode', 'SHADOW')}]: "
        f"CEX {_candidate_line(cex)} | DEX {_candidate_line(dex)}"
        f" | trade yetkisi: YOK{error_text}"
    )
