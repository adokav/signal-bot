"""Fail-closed integrity boundary for the public MEXC new-listing radar.

The scoring layer is not allowed to define the candidate universe.  This module
revalidates listing-event provenance, age and data gates after orchestration and
before a snapshot is exposed to Telegram.  Rejected rows remain research facts
inside their originating components but are not returned as public candidates.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from .config import UnifiedConfig
from .engine import UnifiedRadarEngine as CoreUnifiedRadarEngine
from .models import RadarCandidate, RadarSnapshot


_ALLOWED_DISCOVERY_SOURCES = {"ANNOUNCEMENT", "EXCHANGE_DIFF"}


def _versioned_state_path(path: str, marker: str = "v3") -> str:
    """Return a fresh state path so polluted legacy catalogs cannot survive."""
    value = str(path or "").strip()
    if not value:
        return value
    source = Path(value)
    if source.stem.endswith(f"_{marker}") or source.stem.endswith(f".{marker}"):
        return value
    suffix = source.suffix
    name = f"{source.stem}_{marker}{suffix}" if suffix else f"{source.name}_{marker}"
    return str(source.with_name(name))


def _explicit_announcement_match(title: str, symbol: str) -> bool:
    """Require the symbol to be attached to an actual listing phrase.

    Merely mentioning BTC, USDT or another established asset elsewhere in a
    headline is not listing evidence.  The deliberately narrow patterns prefer
    false negatives over contaminating the recent-listing universe.
    """
    clean_title = re.sub(r"\s+", " ", str(title or "")).strip().upper()
    clean_symbol = re.sub(r"[^A-Z0-9]", "", str(symbol or "")).upper()
    if not clean_title or not clean_symbol:
        return False
    escaped = re.escape(clean_symbol)
    patterns = (
        rf"\b(?:WILL\s+LIST|TO\s+LIST|LISTS?|LISTING)\b[^\n]{{0,90}}\({escaped}\)",
        rf"\b(?:WILL\s+LIST|TO\s+LIST|LISTS?)\s+{escaped}\b",
        rf"\bFIRST\s+IN\s+MARKET\s*:\s*{escaped}\b",
        rf"\b{escaped}/USDT\b[^\n]{{0,60}}\b(?:LISTING|NOW\s+LIVE)\b",
    )
    return any(re.search(pattern, clean_title) for pattern in patterns)


def candidate_integrity_reasons(
    candidate: RadarCandidate,
    *,
    now: int,
    max_age_seconds: int,
    require_open_spot: bool,
    require_supply_data: bool,
) -> tuple[str, ...]:
    """Return reasons that make a row ineligible for the public `/new` view."""
    metadata = candidate.metadata or {}
    reasons: list[str] = []
    source = str(metadata.get("discovery_source") or "").upper()
    first_seen = int(metadata.get("first_seen_at") or 0)
    age = now - first_seen if first_seen else max_age_seconds + 1

    if candidate.source != "PHENOMENONX_MEXC_NEW_LISTING":
        reasons.append("WRONG_CANDIDATE_SOURCE")
    if source not in _ALLOWED_DISCOVERY_SOURCES:
        reasons.append("UNVERIFIED_DISCOVERY_SOURCE")
    if bool(metadata.get("manually_watched")):
        reasons.append("MANUAL_WATCH_IS_NOT_LISTING_EVENT")
    if first_seen <= 0 or age < 0 or age > max_age_seconds:
        reasons.append("OUTSIDE_RECENT_LISTING_WINDOW")
    if source == "ANNOUNCEMENT" and not _explicit_announcement_match(
        str(metadata.get("title") or ""),
        str(metadata.get("base_symbol") or candidate.symbol.removesuffix("USDT")),
    ):
        reasons.append("ANNOUNCEMENT_CONTEXT_NOT_VERIFIED")
    if require_open_spot and str(metadata.get("spot_status") or "").upper() != "OPEN":
        reasons.append("SPOT_NOT_OPEN")

    fundamentals = metadata.get("fundamentals") or {}
    supply_ready = bool(
        fundamentals.get("status") == "READY"
        and fundamentals.get("circulating_supply") is not None
        and (
            fundamentals.get("total_supply") is not None
            or fundamentals.get("max_supply") is not None
        )
    )
    if require_supply_data and not supply_ready:
        reasons.append("SUPPLY_NOT_VERIFIED")
    if fundamentals.get("stage") == "HIGH_RISK":
        reasons.append("FUNDAMENTALS_HIGH_RISK")
    if str(metadata.get("filter_status") or "PASSED").upper() != "PASSED":
        reasons.append("UPSTREAM_FILTER_REJECTED")
    return tuple(dict.fromkeys(reasons))


def sanitize_listing_snapshot(
    snapshot: RadarSnapshot,
    config: UnifiedConfig,
) -> RadarSnapshot:
    """Expose only verified accepted rows; never refill from rejected rows."""
    max_age_seconds = max(1, int(config.listing_candidate_ttl_hours)) * 60 * 60
    accepted: list[RadarCandidate] = []
    for candidate in snapshot.listing_candidates:
        reasons = candidate_integrity_reasons(
            candidate,
            now=int(snapshot.generated_at),
            max_age_seconds=max_age_seconds,
            require_open_spot=config.listing_require_open_spot,
            require_supply_data=config.listing_require_supply_data,
        )
        if not reasons:
            accepted.append(candidate)
    accepted.sort(
        key=lambda item: (
            int(item.score),
            float((item.metadata or {}).get("quote_volume") or 0.0),
            str(item.symbol),
        ),
        reverse=True,
    )
    return replace(
        snapshot,
        listing_candidates=tuple(accepted[: max(1, config.listing_top_n)]),
        # The Telegram formatter historically merged this collection back into
        # the ranking.  Fail closed until a separate diagnostics view exists.
        listing_filtered_candidates=(),
    )


class StrictNewListingRadarEngine(CoreUnifiedRadarEngine):
    """Core radar with fresh discovery state and a final integrity firewall."""

    def __init__(
        self,
        config: UnifiedConfig,
        trade_universe: dict[str, str],
        **kwargs: Any,
    ) -> None:
        strict_state = os.getenv("STRICT_NEW_LISTING_STATE_V3", "1").strip().lower()
        if strict_state in {"1", "true", "yes", "on", "evet"}:
            config = replace(
                config,
                listing_seen_file=_versioned_state_path(config.listing_seen_file),
                listing_candidate_file=_versioned_state_path(config.listing_candidate_file),
            )
        self.strict_listing_config = config
        super().__init__(config, trade_universe, **kwargs)

    def scan_once(self, *, now: int | None = None) -> RadarSnapshot:
        snapshot = super().scan_once(now=now)
        return sanitize_listing_snapshot(snapshot, self.strict_listing_config)
