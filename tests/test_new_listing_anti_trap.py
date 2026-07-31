from __future__ import annotations

from acce_unified.anti_trap import assess_new_listing


def _candidate(**overrides):
    value = {
        "score": 90,
        "metadata": {
            "quote_volume": 3_000_000,
            "volume_acceleration": 1.8,
            "spread_bps": 20,
            "change_pct": 12,
            "fundamentals": {
                "status": "READY",
                "stage": "HEALTHY",
                "identity_confidence": 96,
                "circulating_supply": 50_000_000,
                "circulation_pct": 50,
            },
            "social": {"status": "READY", "stage": "HEALTHY"},
        },
    }
    value.update(overrides)
    return value


def test_high_raw_score_cannot_hide_thin_manufactured_momentum():
    item = _candidate()
    item["metadata"] = {
        **item["metadata"],
        "quote_volume": 95_000,
        "volume_acceleration": 2.5,
    }
    result = assess_new_listing(item)
    assert result.trap_risk_score >= 30
    assert result.conservative_score < result.opportunity_score
    assert any("ani ivme" in reason for reason in result.trap_reasons)


def test_high_risk_fundamentals_are_hard_veto():
    item = _candidate()
    item["metadata"] = {
        **item["metadata"],
        "fundamentals": {
            **item["metadata"]["fundamentals"],
            "stage": "HIGH_RISK",
        },
    }
    result = assess_new_listing(item)
    assert result.vetoed is True
    assert result.state == "TRAP_RISK"


def test_provider_cooldown_reduces_quality_instead_of_improving_rank():
    item = _candidate()
    item["metadata"] = {
        **item["metadata"],
        "fundamentals": {"status": "PROVIDER_COOLDOWN"},
        "social": {"status": "UNAVAILABLE"},
    }
    result = assess_new_listing(item)
    assert result.intelligence_quality < 60
    assert result.state == "INTELLIGENCE_INCOMPLETE"
    assert result.conservative_score < result.opportunity_score


def test_wide_spread_is_hard_veto_even_with_strong_headline_score():
    item = _candidate()
    item["metadata"] = {**item["metadata"], "spread_bps": 220}
    result = assess_new_listing(item)
    assert result.vetoed is True
    assert any("spread" in reason for reason in result.veto_reasons)


def test_extreme_extension_is_not_called_opportunity():
    item = _candidate()
    item["metadata"] = {**item["metadata"], "change_pct": 120}
    result = assess_new_listing(item)
    assert result.vetoed is True
    assert result.state == "TRAP_RISK"


def test_low_circulation_is_hard_veto():
    item = _candidate()
    item["metadata"] = {
        **item["metadata"],
        "fundamentals": {
            **item["metadata"]["fundamentals"],
            "circulation_pct": 10,
        },
    }
    result = assess_new_listing(item)
    assert result.vetoed is True
    assert any("dolaşım" in reason for reason in result.veto_reasons)


def test_clean_setup_still_needs_multi_scan_confirmation():
    result = assess_new_listing(_candidate())
    assert result.vetoed is False
    assert result.state == "BASE_BUILDING"
    assert result.state != "CONFIRMED"
