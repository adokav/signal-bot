from __future__ import annotations

from acce_unified.dex import rank_dex_pairs, score_dex_pair


NOW_MS = 1_800_000_000_000


def _pair(**overrides):
    pair = {
        "chainId": "solana",
        "baseToken": {"symbol": "NOVA", "address": "mint-1"},
        "quoteToken": {"symbol": "SOL"},
        "pairCreatedAt": NOW_MS - 24 * 3_600_000,
        "liquidity": {"usd": 180_000},
        "txns": {
            "h1": {"buys": 80, "sells": 40},
            "h6": {"buys": 180, "sells": 120},
        },
        "volume": {"h1": 60_000, "h6": 180_000},
        "priceChange": {"h1": 12, "h6": 55},
        "info": {"socials": [{"type": "twitter"}], "websites": [{"url": "https://example.test"}]},
        "url": "https://dexscreener.com/solana/mint-1",
    }
    pair.update(overrides)
    return pair


def test_dex_candidate_is_research_only_even_with_high_score():
    candidate = score_dex_pair(_pair(), now_ms=NOW_MS, takeover=True)
    assert candidate is not None
    assert candidate.score >= 68
    assert candidate.role == "RESEARCH_ONLY"
    assert candidate.execution_eligible is False
    assert any("güvenliği doğrulanmadı" in flag for flag in candidate.risk_flags)


def test_dex_hard_liquidity_filter_runs_after_scoring():
    pair = _pair(liquidity={"usd": 5_000})
    ranked = rank_dex_pairs(
        [pair],
        top_n=3,
        min_liquidity=35_000,
        min_h1_transactions=24,
        max_age_hours=72,
        now_ms=NOW_MS,
    )
    assert ranked == []


def test_dex_rejects_unapproved_chain():
    assert score_dex_pair(_pair(chainId="ethereum"), now_ms=NOW_MS) is None
