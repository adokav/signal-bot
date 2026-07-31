"""Production cadence and fail-safe wiring for Liquid-100 observations."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable

from .config import UnifiedConfig
from .engine import UnifiedRadarEngine as CoreUnifiedRadarEngine
from .observation_archive import (
    LiquidObservationArchive,
    ObservationArchivingCexProvider,
)
from .providers import MexcPublicProvider


log = logging.getLogger(__name__)
ScheduleClock = Callable[[], float]


class CadencedObservationArchivingCexProvider(ObservationArchivingCexProvider):
    """Archive a bounded cadence while returning fresh market data every scan."""

    def __init__(
        self,
        *args: Any,
        interval_seconds: int = 30 * 60,
        schedule_clock: ScheduleClock | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.interval_seconds = max(0, int(interval_seconds))
        self.schedule_clock = schedule_clock or time.monotonic
        self._next_archive_due = 0.0
        self._archive_current_scan = False

    def fetch_tickers(self):
        now = float(self.schedule_clock())
        due = self.interval_seconds == 0 or now >= self._next_archive_due
        if due:
            self._archive_current_scan = True
            self._next_archive_due = now + self.interval_seconds
            return super().fetch_tickers()
        self._archive_current_scan = False
        return list(self.provider.fetch_tickers())

    def fetch_long_metrics(self, symbols):
        if self._archive_current_scan:
            try:
                return super().fetch_long_metrics(symbols)
            finally:
                self._archive_current_scan = False
        return self.provider.fetch_long_metrics(symbols)


class ProductionUnifiedRadarEngine(CoreUnifiedRadarEngine):
    """Core engine plus an explicitly enabled, isolated observation archive."""

    def __init__(
        self,
        config: UnifiedConfig,
        trade_universe: dict[str, str],
        *,
        cex_provider: Any | None = None,
        listing_provider: Any | None = None,
        social_provider: Any | None = None,
        fundamental_provider: Any | None = None,
    ) -> None:
        archive: LiquidObservationArchive | None = None
        provider = cex_provider
        if provider is None:
            provider = MexcPublicProvider(timeout=config.request_timeout_seconds)
            if _env_enabled("LIQUID_OBSERVATION_ARCHIVE_ENABLED", False):
                path = os.getenv(
                    "LIQUID_OBSERVATION_ARCHIVE_PATH",
                    "/data/liquid100_observations.sqlite3",
                ).strip() or "/data/liquid100_observations.sqlite3"
                interval = _env_int(
                    "LIQUID_OBSERVATION_ARCHIVE_INTERVAL_SECONDS",
                    30 * 60,
                    minimum=60,
                )
                try:
                    archive = LiquidObservationArchive(path)
                    provider = CadencedObservationArchivingCexProvider(
                        provider,
                        archive,
                        config,
                        trade_universe,
                        interval_seconds=interval,
                    )
                except Exception:
                    archive = None
                    log.exception(
                        "Liquid-100 observation archive could not start; radar continues"
                    )
        self.liquid_observation_archive = archive
        super().__init__(
            config,
            trade_universe,
            cex_provider=provider,
            listing_provider=listing_provider,
            social_provider=social_provider,
            fundamental_provider=fundamental_provider,
        )

    def close_observation_archive(self) -> None:
        if self.liquid_observation_archive is not None:
            self.liquid_observation_archive.close()
            self.liquid_observation_archive = None


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "evet"}


def _env_int(name: str, default: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default
