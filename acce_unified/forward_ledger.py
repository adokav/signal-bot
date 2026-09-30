"""Forward (shadow paper) track record for tactical Long setups (spec §10, §32, §34, §37).

The tactical radar has no historical evidence. This ledger collects it
going forward, without any order authority:

1. **Record** — when a symbol newly enters READY/TRIGGERED, its full plan
   is written with a hash over the decision fields. The decision part of a
   record can never change afterwards; a hash mismatch on load is treated
   as tampering/corruption and the ledger refuses to proceed.
2. **Resolve** — later MEXC M5 candles decide what would have happened:
   - the entry is a limit inside the zone: it fills when a candle trades
     at or below ``entry_high`` before the plan expires, at
     ``min(entry_high, candle.open)``; otherwise ``NOT_FILLED``;
   - after the fill, ``hard_stop`` before ``target_1`` → ``LOSS_STOP``,
     ``target_1`` first → ``WIN_T1``; if one candle touches both, the stop
     is assumed first, and on the fill candle itself only the stop counts
     (intrabar order is unknown — conservative);
   - still open ``MAX_HOLD_SECONDS`` after the fill → ``TIME_EXIT`` at the
     candle close;
   - any gap in the candle stream (bot down longer than the 240-candle
     window, missing bars) → ``UNRESOLVABLE``. Guessing is not allowed.
3. **Thin** — at most one open record per symbol, so outcomes of the same
   symbol do not overlap (AGENTS.md §7). BTC and ETH records can still be
   correlated; the summary says so.

The measured quantity is the spec §12 probability "target before stop",
using target 1 only; the plan's second target is not modelled here.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from trading.research.calibration import wilson_interval
from trading.research.metrics import ADEQUATE_TRADES, sample_quality

from .tactical_long import Candle


RULESET = "tactical_long_engine/v1"
MAX_HOLD_SECONDS = 48 * 3600
M5_SECONDS = 300
OPEN = "OPEN"
TERMINAL = {"NOT_FILLED", "WIN_T1", "LOSS_STOP", "TIME_EXIT", "UNRESOLVABLE"}
DECISION_FIELDS = (
    "record_id", "ruleset", "symbol", "setup", "state_at_record", "structure_4h",
    "decided_at", "entry_low", "entry_high", "technical_invalidation",
    "hard_stop", "target_1", "target_2", "estimated_round_trip_cost_pct", "expires_at",
)


class LedgerIntegrityError(ValueError):
    """The stored ledger is unreadable or a decision field was altered."""


@dataclass(frozen=True)
class ForwardRecord:
    record_id: str
    ruleset: str
    symbol: str
    setup: str
    state_at_record: str
    structure_4h: str
    decided_at: int
    entry_low: float
    entry_high: float
    technical_invalidation: float
    hard_stop: float
    target_1: float
    target_2: float
    estimated_round_trip_cost_pct: float
    expires_at: int
    decision_hash: str
    status: str = OPEN
    cursor: int = 0
    filled_at: int | None = None
    fill_price: float | None = None
    resolved_at: int | None = None
    exit_price: float | None = None
    net_return_pct: float | None = None
    r_multiple: float | None = None
    note: str = ""
    can_authorize_trade: bool = False

    def __post_init__(self) -> None:
        if self.can_authorize_trade:
            raise ValueError("a forward record cannot authorize a trade")
        if self.status != OPEN and self.status not in TERMINAL:
            raise ValueError(f"unknown status {self.status!r}")
        prices = (self.entry_low, self.entry_high, self.technical_invalidation,
                  self.hard_stop, self.target_1, self.target_2)
        if any(not math.isfinite(p) or p <= 0 for p in prices):
            raise ValueError("plan prices must be finite and positive")
        if not self.hard_stop < self.technical_invalidation < self.entry_low <= self.entry_high < self.target_1 <= self.target_2:
            raise ValueError("plan geometry is inconsistent")
        if self.expires_at <= self.decided_at:
            raise ValueError("plan must expire after its decision")


def _decision_hash(fields: Mapping[str, Any]) -> str:
    canonical = json.dumps({k: fields[k] for k in DECISION_FIELDS}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_from_assessment(item: Mapping[str, Any], *, ruleset: str = RULESET) -> ForwardRecord | None:
    """Build an OPEN record from a tactical assessment dict; None if unusable."""

    plan = item.get("plan") or {}
    state = str(item.get("state") or "")
    if not plan or state not in {"READY", "TRIGGERED"}:
        return None
    try:
        decided_at = int(item["decision_at"])
        fields: dict[str, Any] = {
            "ruleset": ruleset,
            "symbol": str(item["symbol"]).upper(),
            "setup": str(item.get("setup") or "UNKNOWN"),
            "state_at_record": state,
            "structure_4h": str(item.get("structure_4h") or "UNKNOWN"),
            "decided_at": decided_at,
            "entry_low": float(plan["entry_low"]),
            "entry_high": float(plan["entry_high"]),
            "technical_invalidation": float(plan["technical_invalidation"]),
            "hard_stop": float(plan["hard_stop"]),
            "target_1": float(plan["target_1"]),
            "target_2": float(plan["target_2"]),
            "estimated_round_trip_cost_pct": float(plan.get("estimated_round_trip_cost_pct") or 0.0),
            "expires_at": int(plan["expires_at"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    fields["record_id"] = hashlib.sha256(
        f"{fields['symbol']}|{fields['setup']}|{decided_at}|{ruleset}".encode()
    ).hexdigest()[:16]
    try:
        return ForwardRecord(**fields, decision_hash=_decision_hash(fields), cursor=decided_at)
    except ValueError:
        return None


def _outcome(record: ForwardRecord, exit_price: float) -> tuple[float, float]:
    assert record.fill_price is not None
    cost = record.estimated_round_trip_cost_pct
    net = (exit_price / record.fill_price - 1.0) * 100.0 - cost
    risk = (record.fill_price - record.hard_stop) / record.fill_price * 100.0 + cost
    return net, net / risk


def _close(record: ForwardRecord, status: str, at: int, exit_price: float | None, note: str = "") -> ForwardRecord:
    if exit_price is None:
        return replace(record, status=status, resolved_at=at, note=note)
    net, r = _outcome(record, exit_price)
    return replace(
        record, status=status, resolved_at=at, exit_price=exit_price,
        net_return_pct=net, r_multiple=r, note=note,
    )


def advance(record: ForwardRecord, candles: Sequence[Candle]) -> ForwardRecord:
    """Walk M5 candles after the record's cursor; returns the updated record."""

    if record.status != OPEN:
        return record
    fresh = [c for c in candles if c.open_time >= record.cursor]
    if not fresh:
        return record
    if fresh[0].open_time - record.cursor >= M5_SECONDS:
        return _close(record, "UNRESOLVABLE", fresh[0].open_time, None, "candle gap after cursor")
    current = record
    previous: Candle | None = None
    for candle in fresh:
        if previous is not None and candle.open_time - previous.close_time > 1:
            return _close(current, "UNRESOLVABLE", candle.open_time, None, "missing candles")
        previous = candle
        if current.fill_price is None:
            if candle.open_time >= current.expires_at:
                return _close(current, "NOT_FILLED", candle.open_time, None)
            if candle.low <= current.entry_high:
                fill = min(current.entry_high, candle.open)
                current = replace(current, filled_at=candle.open_time, fill_price=fill)
                if candle.low <= current.hard_stop:
                    return _close(current, "LOSS_STOP", candle.close_time, min(current.hard_stop, candle.open))
                current = replace(current, cursor=candle.close_time + 1)
                continue
            current = replace(current, cursor=candle.close_time + 1)
            continue
        if candle.low <= current.hard_stop:
            return _close(current, "LOSS_STOP", candle.close_time, min(current.hard_stop, candle.open))
        if candle.high >= current.target_1:
            return _close(current, "WIN_T1", candle.close_time, max(current.target_1, candle.open))
        if candle.close_time - current.filled_at >= MAX_HOLD_SECONDS:
            return _close(current, "TIME_EXIT", candle.close_time, candle.close)
        current = replace(current, cursor=candle.close_time + 1)
    return current


def update_records(
    records: Sequence[ForwardRecord],
    *,
    new_assessments: Iterable[Mapping[str, Any]],
    m5_by_symbol: Mapping[str, Sequence[Candle]],
) -> list[ForwardRecord]:
    """Advance open records, then add new ones (one open record per symbol)."""

    updated = [
        advance(r, m5_by_symbol.get(r.symbol, ())) if r.status == OPEN else r
        for r in records
    ]
    known = {r.record_id for r in updated}
    open_symbols = {r.symbol for r in updated if r.status == OPEN}
    for item in new_assessments:
        record = record_from_assessment(item)
        if record is None or record.record_id in known or record.symbol in open_symbols:
            continue
        updated.append(record)
        known.add(record.record_id)
        open_symbols.add(record.symbol)
    return updated


class ForwardLedger:
    """JSON file store with atomic replace and integrity checks."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[ForwardRecord]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text("utf-8"))
            rows = payload["records"]
            records = [ForwardRecord(**row) for row in rows]
        except (ValueError, TypeError, KeyError) as exc:
            raise LedgerIntegrityError("forward ledger is unreadable") from exc
        for record in records:
            fields = asdict(record)
            if _decision_hash(fields) != record.decision_hash:
                raise LedgerIntegrityError(f"decision fields altered for {record.record_id}")
        if len({r.record_id for r in records}) != len(records):
            raise LedgerIntegrityError("duplicate record ids")
        return records

    def save(self, records: Sequence[ForwardRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        payload = {"version": 1, "records": [asdict(r) for r in records]}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


@dataclass(frozen=True)
class SetupSummary:
    setup: str
    recorded: int
    open: int
    not_filled: int
    unresolvable: int
    resolved: int
    wins: int
    hit_rate: float | None
    hit_rate_low: float | None
    hit_rate_high: float | None
    mean_r: float | None
    sample_quality: str


def summarize(records: Sequence[ForwardRecord]) -> list[SetupSummary]:
    groups: dict[str, list[ForwardRecord]] = {"ALL": list(records)}
    for record in records:
        groups.setdefault(record.setup, []).append(record)
    out: list[SetupSummary] = []
    for setup, rows in groups.items():
        resolved = [r for r in rows if r.status in {"WIN_T1", "LOSS_STOP", "TIME_EXIT"}]
        wins = sum(1 for r in resolved if r.status == "WIN_T1")
        low, high = wilson_interval(wins, len(resolved)) if resolved else (None, None)
        rs = [r.r_multiple for r in resolved if r.r_multiple is not None]
        out.append(SetupSummary(
            setup=setup,
            recorded=len(rows),
            open=sum(1 for r in rows if r.status == OPEN),
            not_filled=sum(1 for r in rows if r.status == "NOT_FILLED"),
            unresolvable=sum(1 for r in rows if r.status == "UNRESOLVABLE"),
            resolved=len(resolved),
            wins=wins,
            hit_rate=(wins / len(resolved)) if resolved else None,
            hit_rate_low=low,
            hit_rate_high=high,
            mean_r=(sum(rs) / len(rs)) if rs else None,
            sample_quality=sample_quality(len(resolved)),
        ))
    return out


def status_text(records: Sequence[ForwardRecord]) -> str:
    overall = summarize(records)[0]
    if overall.resolved == 0:
        return (
            f"İleriye dönük kayıt: {overall.recorded} setup, henüz çözümlenen yok "
            f"({overall.open} açık) — kanıt oluşmadı"
        )
    return (
        f"İleriye dönük kayıt: {overall.resolved} çözümlendi · hedef-önce-stop "
        f"%{overall.hit_rate * 100:.0f} [%{overall.hit_rate_low * 100:.0f}–%{overall.hit_rate_high * 100:.0f}] · "
        f"ort. {overall.mean_r:+.2f}R · örnek {overall.sample_quality} "
        f"(≥{ADEQUATE_TRADES} gerekli; BTC/ETH korele olabilir)"
    )

