from dataclasses import replace

from robinhood_radar import Candidate, GeckoClient, Pool, RadarConfig, State, format_alert, rank_pools, score_pool


def config(tmp_path, **kwargs):
    return replace(RadarConfig(state_file=str(tmp_path / "state.json"), token="", chat_id=""), **kwargs)


def pool(**kwargs):
    data = dict(
        pool="0xpool", token="0xtoken", symbol="HOODCAT", name="Hood Cat",
        quote="WETH", dex="Uniswap V3", created=1_700_000_000 - 3600,
        price=.01, liquidity=180_000, fdv=2_000_000, market_cap=0,
        volume_h1=95_000, volume_h24=800_000, change_m5=4, change_h1=18,
        change_h24=55, buys_h1=170, sells_h1=80, sources=("NEW", "TRENDING_1H"),
    )
    data.update(kwargs)
    return Pool(**data)


def test_parser_uses_non_quote_token_when_weth_is_base():
    row = {
        "attributes": {"address": "0xpool", "base_token_price_usd": "2000", "quote_token_price_usd": ".01", "reserve_in_usd": "100000", "pool_created_at": "2026-07-23T10:00:00Z", "volume_usd": {"h1": "10000"}, "price_change_percentage": {"h1": "5"}, "transactions": {"h1": {"buys": 20, "sells": 10}}},
        "relationships": {"base_token": {"data": {"id": "weth"}}, "quote_token": {"data": {"id": "cat"}}, "dex": {"data": {"id": "uni"}}},
    }
    refs = {
        "weth": {"attributes": {"address": "0xweth", "symbol": "WETH", "name": "Wrapped ETH"}},
        "cat": {"attributes": {"address": "0xcat", "symbol": "CAT", "name": "Cash Cat"}},
        "uni": {"attributes": {"name": "Uniswap V3"}},
    }
    item = GeckoClient._parse(row, refs, {"NEW"})
    assert item and item.symbol == "CAT" and item.token == "0xcat" and item.quote == "WETH"
    assert item.price == .01 and item.fdv == 0


def test_strong_pool_alerts_but_never_executes(tmp_path):
    item = score_pool(pool(), config(tmp_path), 1_700_000_000)
    assert item.score >= 64 and item.stage in {"BUILDING", "HOT"}
    assert item.execution_eligible is False


def test_low_liquidity_is_blocked(tmp_path):
    item = score_pool(pool(liquidity=5_000), config(tmp_path), 1_700_000_000)
    assert item.stage == "BLOCKED" and any("likidite" in x for x in item.risks)


def test_ranking_keeps_best_pool_per_contract(tmp_path):
    ranked = rank_pools([pool(pool="weak", liquidity=30_000), pool(pool="strong", liquidity=300_000)], config(tmp_path), 1_700_000_000)
    assert len(ranked) == 1 and ranked[0].pool == "strong"


def test_state_alerts_once_until_cooldown(tmp_path):
    cfg, state = config(tmp_path, cooldown=3600), State(str(tmp_path / "state.json"))
    item = score_pool(pool(), cfg, 1_700_000_000)
    assert state.alerts([item], cfg, 1_700_000_000) == [item]
    assert state.alerts([item], cfg, 1_700_000_100) == []


def test_message_declares_shadow_mode(tmp_path):
    item = score_pool(pool(), config(tmp_path), 1_700_000_000)
    text = format_alert([item], config(tmp_path), 1_700_000_000)
    assert "Robinhood Chain" in text and "işlem açmaz" in text and "0xtoken" in text
