from dataclasses import replace

from robinhood_mexc_radar import (
    MexcClient,
    MexcFilterConfig,
    MexcMarket,
    MexcRadarService,
    format_mexc_alert,
    rank_mexc_pools,
    score_mexc_pool,
)
from robinhood_radar import Pool, RadarConfig, State


def radar_config(tmp_path, **kwargs):
    return replace(
        RadarConfig(state_file=str(tmp_path / "state.json"), token="", chat_id=""),
        **kwargs,
    )


def filter_config(**kwargs):
    return replace(MexcFilterConfig(), **kwargs)


def pool(**kwargs):
    data = dict(
        pool="0xpool",
        token="0xtoken",
        symbol="HOODCAT",
        name="Hood Cat",
        quote="WETH",
        dex="Uniswap V3",
        created=1_700_000_000 - 3600,
        price=0.01,
        liquidity=180_000,
        fdv=2_000_000,
        market_cap=0,
        volume_h1=95_000,
        volume_h24=800_000,
        change_m5=4,
        change_h1=18,
        change_h24=55,
        buys_h1=170,
        sells_h1=80,
        sources=("NEW", "TRENDING_1H"),
    )
    data.update(kwargs)
    return Pool(**data)


def market(**kwargs):
    data = dict(
        pair="HOODCATUSDT",
        base="HOODCAT",
        last_price=0.0105,
        change_h24=12,
        quote_volume=2_500_000,
        bid=0.0104,
        ask=0.0106,
    )
    data.update(kwargs)
    return MexcMarket(**data)


class _Response:
    def __init__(self, payload, content_type="application/json"):
        self.payload = payload
        self.headers = {"content-type": content_type}
        self.text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _MexcSession:
    def get(self, url, timeout):
        if url.endswith("exchangeInfo"):
            return _Response(
                {
                    "symbols": [
                        {
                            "symbol": "HOODCATUSDT",
                            "baseAsset": "HOODCAT",
                            "quoteAsset": "USDT",
                            "status": "1",
                        },
                        {
                            "symbol": "PAUSEDUSDT",
                            "baseAsset": "PAUSED",
                            "quoteAsset": "USDT",
                            "status": "2",
                        },
                    ]
                }
            )
        return _Response(
            [
                {
                    "symbol": "HOODCATUSDT",
                    "lastPrice": "0.0105",
                    "openPrice": "0.01",
                    "quoteVolume": "2500000",
                    "bidPrice": "0.0104",
                    "askPrice": "0.0106",
                },
                {
                    "symbol": "PAUSEDUSDT",
                    "lastPrice": "1",
                    "openPrice": "1",
                    "quoteVolume": "9000000",
                },
            ]
        )


def test_mexc_client_keeps_only_active_usdt_spot():
    markets = MexcClient(filter_config(), session=_MexcSession()).markets()
    assert set(markets) == {"HOODCAT"}
    assert markets["HOODCAT"].pair == "HOODCATUSDT"
    assert markets["HOODCAT"].quote_volume == 2_500_000


def test_rank_excludes_robinhood_tokens_not_listed_on_mexc(tmp_path):
    ranked, excluded = rank_mexc_pools(
        [pool(), pool(symbol="NOTLISTED", token="0x2", pool="0x2")],
        {"HOODCAT": market()},
        radar_config(tmp_path),
        filter_config(),
        1_700_000_000,
    )
    assert len(ranked) == 1 and ranked[0].mexc_pair == "HOODCATUSDT"
    assert excluded == 1


def test_mexc_volume_and_price_confirmation_raise_score(tmp_path):
    item = score_mexc_pool(
        pool(), market(), radar_config(tmp_path), filter_config(), 1_700_000_000
    )
    assert item.score >= item.candidate.score
    assert item.stage in {"BUILDING", "HOT"}
    assert any("MEXC" in reason for reason in item.reasons)
    assert item.execution_eligible is False


def test_low_mexc_volume_is_blocked(tmp_path):
    item = score_mexc_pool(
        pool(),
        market(quote_volume=20_000),
        radar_config(tmp_path),
        filter_config(),
        1_700_000_000,
    )
    assert item.stage == "BLOCKED"
    assert any("MEXC 24s hacmi" in risk for risk in item.risks)


def test_symbol_collision_price_gap_is_blocked(tmp_path):
    item = score_mexc_pool(
        pool(price=0.01),
        market(last_price=0.1),
        radar_config(tmp_path),
        filter_config(),
        1_700_000_000,
    )
    assert item.stage == "BLOCKED"
    assert any("eşleşmesi şüpheli" in risk for risk in item.risks)


def test_message_is_mexc_actionable_but_shadow_only(tmp_path):
    item = score_mexc_pool(
        pool(), market(), radar_config(tmp_path), filter_config(), 1_700_000_000
    )
    text = format_mexc_alert([item], radar_config(tmp_path), 1_700_000_000)
    assert "Robinhood × MEXC" in text and "HOODCATUSDT" in text
    assert "MEXC" in text and "işlem açmaz" in text and "0xtoken" in text


class _GeckoStub:
    def __init__(self):
        self.session = _MexcSession()

    def chain_id(self):
        return 4663

    def pools(self):
        return [pool()]


class _MexcFailure:
    def markets(self):
        raise RuntimeError("down")


def test_service_fails_closed_when_mexc_is_unavailable(tmp_path):
    service = MexcRadarService(
        radar_config(tmp_path, startup_report=False),
        filter_config(required=True),
        gecko_client=_GeckoStub(),
        mexc_client=_MexcFailure(),
        state=State(str(tmp_path / "state.json")),
    )
    result = service.scan_once(1_700_000_000)
    assert result["candidates"] == []
    assert any(error.startswith("MEXC_SPOT:") for error in result["errors"])
    assert result["can_authorize_trade"] is False
