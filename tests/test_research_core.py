from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from acce_unified.research import (
    CostEstimate,
    Decision,
    Direction,
    Evidence,
    EvidenceStatus,
    MarketState,
    Opportunity,
    ResearchStore,
)


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _state(as_of: datetime = NOW) -> MarketState:
    return MarketState(
        as_of=as_of,
        trend="UP",
        volatility="NORMAL",
        liquidity="HEALTHY",
        breadth="POSITIVE",
        leverage="BALANCED",
    )


def _evidence(observed_at: datetime = NOW) -> tuple[Evidence, ...]:
    return (
        Evidence(
            name="ema20_above_ema50",
            value=True,
            observed_at=observed_at,
            source="mexc_15m_closed_candles",
        ),
    )


def _opportunity(**overrides) -> Opportunity:
    values = {
        "strategy": "LIQUID_LONG",
        "symbol": "BTCUSDT",
        "venue": "MEXC",
        "direction": Direction.LONG,
        "decision_at": NOW,
        "market_state": _state(),
        "evidence": _evidence(),
        "decision": Decision.ENGAGE,
        "gross_edge_bps": 45.0,
        "edge_lower_bound_bps": 25.0,
        "costs": CostEstimate(fee_bps=4.0, spread_bps=3.0, slippage_bps=5.0),
        "confidence": 0.72,
        "reason_codes": ("TREND_CONFIRMED", "COST_ADJUSTED_EDGE"),
    }
    values.update(overrides)
    return Opportunity(**values)


def test_future_evidence_is_rejected():
    with pytest.raises(ValueError, match="future evidence"):
        _opportunity(evidence=_evidence(NOW + timedelta(seconds=1)))


def test_future_market_state_is_rejected():
    with pytest.raises(ValueError, match="market state cannot be from the future"):
        _opportunity(market_state=_state(NOW + timedelta(seconds=1)))


def test_engage_requires_conservative_edge_above_all_costs():
    with pytest.raises(ValueError, match="lower-bound edge above total costs"):
        _opportunity(
            edge_lower_bound_bps=12.0,
            costs=CostEstimate(fee_bps=4.0, spread_bps=3.0, slippage_bps=5.0),
        )


def test_engage_cannot_reward_missing_or_stale_evidence():
    missing = Evidence(
        name="circulating_supply",
        value=None,
        observed_at=NOW,
        source="fundamental_provider",
        status=EvidenceStatus.MISSING,
        quality=0.0,
    )
    with pytest.raises(ValueError, match="cannot use missing"):
        _opportunity(evidence=(missing,))


def test_observe_can_record_uncertain_evidence_without_authorizing_trade():
    missing = Evidence(
        name="circulating_supply",
        value=None,
        observed_at=NOW,
        source="fundamental_provider",
        status=EvidenceStatus.MISSING,
        quality=0.0,
    )
    item = _opportunity(
        evidence=(missing,),
        decision=Decision.OBSERVE,
        edge_lower_bound_bps=None,
    )
    assert item.decision is Decision.OBSERVE
    assert item.net_edge_lower_bound_bps is None


def test_research_store_is_append_only_and_point_in_time():
    store = ResearchStore()
    first = _opportunity(opportunity_id="first")
    second = _opportunity(
        opportunity_id="second",
        decision_at=NOW + timedelta(hours=1),
        market_state=_state(NOW + timedelta(hours=1)),
        evidence=_evidence(NOW + timedelta(hours=1)),
    )
    store.append_many((first, second))

    rows = store.list_as_of(NOW + timedelta(minutes=30))
    assert [row["opportunity_id"] for row in rows] == ["first"]

    with pytest.raises(Exception, match="append-only"):
        store.connection.execute(
            "UPDATE opportunities SET decision = 'ABSTAIN' WHERE opportunity_id = 'first'"
        )

    with pytest.raises(Exception, match="append-only"):
        store.connection.execute("DELETE FROM opportunities WHERE opportunity_id = 'first'")

    store.close()


def test_duplicate_opportunity_id_is_rejected_instead_of_overwritten():
    store = ResearchStore()
    item = _opportunity(opportunity_id="same")
    store.append(item)
    with pytest.raises(Exception):
        store.append(item)
    assert store.get("same")["decision"] == "ENGAGE"
    store.close()


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        _opportunity(decision_at=datetime(2026, 1, 1, 12, 0))
