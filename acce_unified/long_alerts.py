"""State transitions for automatic Liquid Long Telegram alerts.

This module is deliberately independent from Telegram and exchange providers.  A
scan adapter supplies the set of fully evaluated symbols, the qualified Long
candidates, and rejection reasons.  The tracker emits idempotent lifecycle
events that the application may deliver as notifications.  It never authorizes
or places an order.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Mapping


class LongAlertKind(str, Enum):
    ENTERED = "ENTERED"
    BROKEN = "BROKEN"


@dataclass(frozen=True)
class LongAlertEvent:
    kind: LongAlertKind
    symbol: str
    observed_at: datetime
    score: float | None = None
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol cannot be empty")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")


@dataclass
class _WatchState:
    qualified: bool
    score: float | None
    changed_at: str


class LongAlertTracker:
    """Persisted absent/qualified/broken state machine.

    A symbol is considered safely evaluated only when it appears in
    ``evaluated_symbols``.  Provider outages therefore cannot turn an existing
    watch into a false BROKEN event.
    """

    def __init__(self, state_path: str | Path | None = None) -> None:
        self.state_path = Path(state_path) if state_path else None
        self._states: dict[str, _WatchState] = {}
        self._load()

    def evaluate(
        self,
        *,
        observed_at: datetime,
        evaluated_symbols: Iterable[str],
        qualified_scores: Mapping[str, float],
        rejection_reasons: Mapping[str, Iterable[str]] | None = None,
    ) -> tuple[LongAlertEvent, ...]:
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")

        evaluated = {symbol.upper() for symbol in evaluated_symbols}
        qualified = {symbol.upper(): float(score) for symbol, score in qualified_scores.items()}
        reasons = {
            symbol.upper(): tuple(str(code) for code in codes)
            for symbol, codes in (rejection_reasons or {}).items()
        }
        events: list[LongAlertEvent] = []

        for symbol, score in sorted(qualified.items()):
            if symbol not in evaluated:
                raise ValueError(f"qualified symbol was not evaluated: {symbol}")
            previous = self._states.get(symbol)
            if previous is None or not previous.qualified:
                events.append(
                    LongAlertEvent(
                        kind=LongAlertKind.ENTERED,
                        symbol=symbol,
                        observed_at=observed_at,
                        score=score,
                        reason_codes=("LIQUID_LONG_QUALIFIED",),
                    )
                )
            self._states[symbol] = _WatchState(True, score, _iso(observed_at))

        for symbol, previous in sorted(tuple(self._states.items())):
            if not previous.qualified or symbol in qualified or symbol not in evaluated:
                continue
            events.append(
                LongAlertEvent(
                    kind=LongAlertKind.BROKEN,
                    symbol=symbol,
                    observed_at=observed_at,
                    score=previous.score,
                    reason_codes=reasons.get(symbol) or ("LIQUID_LONG_FORMATION_BROKEN",),
                )
            )
            self._states[symbol] = _WatchState(False, previous.score, _iso(observed_at))

        self._save()
        return tuple(events)

    def watched_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(symbol for symbol, state in self._states.items() if state.qualified))

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._states = {
            symbol: _WatchState(
                qualified=bool(value["qualified"]),
                score=value.get("score"),
                changed_at=str(value["changed_at"]),
            )
            for symbol, value in payload.get("symbols", {}).items()
        }

    def _save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "symbols": {symbol: asdict(state) for symbol, state in sorted(self._states.items())},
        }
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)


def format_long_alert(event: LongAlertEvent) -> str:
    if event.kind is LongAlertKind.ENTERED:
        score = f" · skor {event.score:.1f}" if event.score is not None else ""
        return (
            f"🟢 LONG FORMASYONU OLUŞTU\n"
            f"{event.symbol}{score}\n"
            "Otomatik takip başladı. Bu bir işlem emri değildir; tetik ve risk onayı gerekir."
        )
    reasons = ", ".join(event.reason_codes)
    return (
        f"🔴 LONG FORMASYONU BOZULDU\n"
        f"{event.symbol}\n"
        f"Neden: {reasons}\n"
        "Takip kapatıldı; açık pozisyon varsa bağımsız stop ve risk kuralları geçerlidir."
    )


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
