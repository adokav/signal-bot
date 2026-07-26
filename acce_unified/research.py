"""Point-in-time research records for replay, audit and validation.

This module deliberately has no exchange or Telegram dependencies. It defines
append-only research facts; strategy scoring and live execution remain outside
this boundary.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4


class Decision(str, Enum):
    ENGAGE = "ENGAGE"
    OBSERVE = "OBSERVE"
    ABSTAIN = "ABSTAIN"
    INVALID = "INVALID"


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class EvidenceStatus(str, Enum):
    OBSERVED = "OBSERVED"
    MISSING = "MISSING"
    STALE = "STALE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class Evidence:
    name: str
    value: Any
    observed_at: datetime
    source: str
    status: EvidenceStatus = EvidenceStatus.OBSERVED
    quality: float = 1.0

    def __post_init__(self) -> None:
        _require_aware(self.observed_at, "observed_at")
        if not self.name.strip():
            raise ValueError("evidence name cannot be empty")
        if not self.source.strip():
            raise ValueError("evidence source cannot be empty")
        if not 0.0 <= self.quality <= 1.0:
            raise ValueError("evidence quality must be between 0 and 1")


@dataclass(frozen=True)
class MarketState:
    as_of: datetime
    trend: str
    volatility: str
    liquidity: str
    breadth: str
    leverage: str
    macro: str = "UNKNOWN"
    event_risk: str = "NORMAL"

    def __post_init__(self) -> None:
        _require_aware(self.as_of, "as_of")


@dataclass(frozen=True)
class CostEstimate:
    fee_bps: float = 0.0
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    funding_bps: float = 0.0
    uncertainty_bps: float = 0.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if value < 0:
                raise ValueError(f"{name} cannot be negative")

    @property
    def total_bps(self) -> float:
        return sum(asdict(self).values())


@dataclass(frozen=True)
class Opportunity:
    strategy: str
    symbol: str
    venue: str
    direction: Direction
    decision_at: datetime
    market_state: MarketState
    evidence: tuple[Evidence, ...]
    decision: Decision
    gross_edge_bps: float | None = None
    edge_lower_bound_bps: float | None = None
    costs: CostEstimate = field(default_factory=CostEstimate)
    confidence: float | None = None
    reason_codes: tuple[str, ...] = ()
    model_version: str = "research-v1"
    opportunity_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _require_aware(self.decision_at, "decision_at")
        if not self.strategy.strip() or not self.symbol.strip() or not self.venue.strip():
            raise ValueError("strategy, symbol and venue are required")
        if self.market_state.as_of > self.decision_at:
            raise ValueError("market state cannot be from the future")
        future = [item.name for item in self.evidence if item.observed_at > self.decision_at]
        if future:
            raise ValueError(f"future evidence is forbidden: {', '.join(future)}")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.decision is Decision.ENGAGE:
            if self.edge_lower_bound_bps is None:
                raise ValueError("ENGAGE requires a conservative edge lower bound")
            if self.edge_lower_bound_bps <= self.costs.total_bps:
                raise ValueError("ENGAGE requires lower-bound edge above total costs")
            if any(item.status is not EvidenceStatus.OBSERVED for item in self.evidence):
                raise ValueError("ENGAGE cannot use missing, stale or invalid evidence")

    @property
    def net_edge_lower_bound_bps(self) -> float | None:
        if self.edge_lower_bound_bps is None:
            return None
        return self.edge_lower_bound_bps - self.costs.total_bps

    def to_record(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "venue": self.venue,
            "direction": self.direction.value,
            "decision_at": _iso(self.decision_at),
            "decision": self.decision.value,
            "gross_edge_bps": self.gross_edge_bps,
            "edge_lower_bound_bps": self.edge_lower_bound_bps,
            "net_edge_lower_bound_bps": self.net_edge_lower_bound_bps,
            "confidence": self.confidence,
            "model_version": self.model_version,
            "market_state_json": _json(asdict(self.market_state)),
            "evidence_json": _json([_evidence_record(item) for item in self.evidence]),
            "costs_json": _json(asdict(self.costs)),
            "reason_codes_json": _json(list(self.reason_codes)),
        }


@dataclass(frozen=True)
class OutcomeLabel:
    opportunity_id: str
    horizon: timedelta
    labelled_at: datetime
    entry_reference: float
    exit_reference: float
    max_favourable_excursion: float
    max_adverse_excursion: float
    net_return: float
    return_r: float | None = None
    realised_volatility: float | None = None
    label_version: str = "outcome-v1"
    cost_assumption_version: str = "cost-v1"
    label_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        _require_aware(self.labelled_at, "labelled_at")
        if not self.opportunity_id.strip():
            raise ValueError("opportunity_id is required")
        if self.horizon <= timedelta(0):
            raise ValueError("horizon must be positive")
        if self.entry_reference <= 0 or self.exit_reference <= 0:
            raise ValueError("price references must be positive")

    def validate_maturity(self, decision_at: datetime) -> None:
        _require_aware(decision_at, "decision_at")
        if self.labelled_at < decision_at + self.horizon:
            raise ValueError("outcome label cannot be generated before horizon maturity")

    def to_record(self) -> dict[str, Any]:
        return {
            "label_id": self.label_id,
            "opportunity_id": self.opportunity_id,
            "horizon_seconds": int(self.horizon.total_seconds()),
            "labelled_at": _iso(self.labelled_at),
            "entry_reference": self.entry_reference,
            "exit_reference": self.exit_reference,
            "max_favourable_excursion": self.max_favourable_excursion,
            "max_adverse_excursion": self.max_adverse_excursion,
            "net_return": self.net_return,
            "return_r": self.return_r,
            "realised_volatility": self.realised_volatility,
            "label_version": self.label_version,
            "cost_assumption_version": self.cost_assumption_version,
        }


@dataclass
class ReplayClock:
    current: datetime

    def __post_init__(self) -> None:
        _require_aware(self.current, "current")
        self.current = self.current.astimezone(timezone.utc)

    def advance_to(self, timestamp: datetime) -> datetime:
        _require_aware(timestamp, "timestamp")
        timestamp = timestamp.astimezone(timezone.utc)
        if timestamp < self.current:
            raise ValueError("replay clock cannot move backward")
        self.current = timestamp
        return self.current

    def advance_by(self, step: timedelta) -> datetime:
        if step <= timedelta(0):
            raise ValueError("replay step must be positive")
        self.current += step
        return self.current

    def require_available(self, observed_at: datetime) -> None:
        _require_aware(observed_at, "observed_at")
        if observed_at > self.current:
            raise ValueError("observation is not available at replay time")

    def horizon_matured(self, decision_at: datetime, horizon: timedelta) -> bool:
        _require_aware(decision_at, "decision_at")
        if horizon <= timedelta(0):
            raise ValueError("horizon must be positive")
        return self.current >= decision_at + horizon


class ResearchStore:
    """Small append-only SQLite boundary for deterministic research journals."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self._migrate()

    def close(self) -> None:
        self.connection.close()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS opportunities (
                opportunity_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL,
                symbol TEXT NOT NULL,
                venue TEXT NOT NULL,
                direction TEXT NOT NULL,
                decision_at TEXT NOT NULL,
                decision TEXT NOT NULL,
                gross_edge_bps REAL,
                edge_lower_bound_bps REAL,
                net_edge_lower_bound_bps REAL,
                confidence REAL,
                model_version TEXT NOT NULL,
                market_state_json TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                costs_json TEXT NOT NULL,
                reason_codes_json TEXT NOT NULL,
                inserted_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_opportunities_time
                ON opportunities(decision_at, opportunity_id);
            CREATE INDEX IF NOT EXISTS idx_opportunities_strategy_symbol
                ON opportunities(strategy, symbol, decision_at);
            CREATE TABLE IF NOT EXISTS outcome_labels (
                label_id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL,
                horizon_seconds INTEGER NOT NULL,
                labelled_at TEXT NOT NULL,
                entry_reference REAL NOT NULL,
                exit_reference REAL NOT NULL,
                max_favourable_excursion REAL NOT NULL,
                max_adverse_excursion REAL NOT NULL,
                net_return REAL NOT NULL,
                return_r REAL,
                realised_volatility REAL,
                label_version TEXT NOT NULL,
                cost_assumption_version TEXT NOT NULL,
                inserted_at TEXT NOT NULL,
                UNIQUE(opportunity_id, horizon_seconds, label_version),
                FOREIGN KEY(opportunity_id) REFERENCES opportunities(opportunity_id)
            );
            CREATE INDEX IF NOT EXISTS idx_outcome_labels_opportunity
                ON outcome_labels(opportunity_id, horizon_seconds);
            CREATE TRIGGER IF NOT EXISTS opportunities_no_update
            BEFORE UPDATE ON opportunities BEGIN
                SELECT RAISE(ABORT, 'research journal is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS opportunities_no_delete
            BEFORE DELETE ON opportunities BEGIN
                SELECT RAISE(ABORT, 'research journal is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS outcome_labels_no_update
            BEFORE UPDATE ON outcome_labels BEGIN
                SELECT RAISE(ABORT, 'outcome labels are append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS outcome_labels_no_delete
            BEFORE DELETE ON outcome_labels BEGIN
                SELECT RAISE(ABORT, 'outcome labels are append-only');
            END;
            """
        )
        self.connection.commit()

    def append(self, opportunity: Opportunity) -> None:
        self._insert("opportunities", opportunity.to_record())

    def append_many(self, opportunities: Iterable[Opportunity]) -> None:
        with self.connection:
            for opportunity in opportunities:
                self._execute_insert("opportunities", opportunity.to_record())

    def append_outcome(self, label: OutcomeLabel) -> None:
        opportunity = self.get(label.opportunity_id)
        if opportunity is None:
            raise ValueError("outcome label requires an existing opportunity")
        label.validate_maturity(_parse_iso(str(opportunity["decision_at"])))
        self._insert("outcome_labels", label.to_record())

    def get(self, opportunity_id: str) -> Mapping[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM opportunities WHERE opportunity_id = ?", (opportunity_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def list_as_of(self, as_of: datetime, *, strategy: str | None = None) -> list[Mapping[str, Any]]:
        _require_aware(as_of, "as_of")
        params: list[Any] = [_iso(as_of)]
        where = "decision_at <= ?"
        if strategy is not None:
            where += " AND strategy = ?"
            params.append(strategy)
        rows = self.connection.execute(
            f"SELECT * FROM opportunities WHERE {where} ORDER BY decision_at, opportunity_id",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def list_outcomes(self, opportunity_id: str) -> list[Mapping[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM outcome_labels WHERE opportunity_id = ? "
            "ORDER BY horizon_seconds, labelled_at, label_id",
            (opportunity_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _insert(self, table: str, record: Mapping[str, Any]) -> None:
        with self.connection:
            self._execute_insert(table, record)

    def _execute_insert(self, table: str, record: Mapping[str, Any]) -> None:
        values = dict(record)
        values["inserted_at"] = _iso(datetime.now(timezone.utc))
        columns = tuple(values)
        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
        self.connection.execute(sql, tuple(values[column] for column in columns))


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported JSON value: {type(value)!r}")


def _evidence_record(item: Evidence) -> dict[str, Any]:
    return {
        "name": item.name,
        "value": item.value,
        "observed_at": _iso(item.observed_at),
        "source": item.source,
        "status": item.status.value,
        "quality": item.quality,
    }
