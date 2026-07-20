from __future__ import annotations

from acce_unified import UnifiedConfig, UnifiedRadarEngine
from acce_unified.config import DEFAULT_TRADE_UNIVERSE
from acce_unified.fundamentals import FundamentalMetricsProvider, score_fundamental_snapshot
from acce_unified.models import MexcListing


def _listing(symbol: str = "NOVA", *, title: str | None = None) -> MexcListing:
    return MexcListing(
        symbol=symbol,
        pair=f"{symbol}USDT",
        title=title or f"MEXC Will List Nova Protocol ({symbol})",
        rank=1,
        spot_status="OPEN",
        last_price=0.42,
        change_pct=4.0,
        quote_volume=3_000_000,
        volume_acceleration=2.2,
    )


def _market_row(**overrides):
    row = {
        "id": "nova-protocol",
        "symbol": "nova",
        "name": "Nova Protocol",
        "market_cap": 20_000_000,
        "fully_diluted_valuation": 40_000_000,
        "total_volume": 4_000_000,
        "market_cap_rank": 700,
        "circulating_supply": 50_000_000,
        "total_supply": 100_000_000,
        "max_supply": 100_000_000,
        "ath_change_percentage": -35.0,
        "price_change_percentage_24h": 4.0,
    }
    row.update(overrides)
    return row


def test_fundamental_score_exposes_dilution_turnover_and_supply():
    signal = score_fundamental_snapshot(
        _market_row(),
        mexc_quote_volume=1_000_000,
        identity_confidence=96,
        matched_by="TITLE_AND_SYMBOL",
    )

    assert signal["status"] == "READY"
    assert signal["fundamental_score"] >= 75
    assert signal["circulation_pct"] == 50
    assert signal["market_cap_to_fdv_pct"] == 50
    assert signal["volume_to_market_cap_pct"] == 20
    assert signal["identity_confidence"] == 96
    assert signal["can_authorize_trade"] is False


def test_missing_fundamentals_are_pending_instead_of_zero_quality():
    signal = score_fundamental_snapshot(None)

    assert signal["status"] == "DATA_PENDING"
    assert signal["fundamental_score"] is None
    assert signal["can_authorize_trade"] is False


def test_extreme_dilution_and_turnover_remain_explicit_high_risk():
    signal = score_fundamental_snapshot(
        _market_row(
            market_cap=2_000_000,
            fully_diluted_valuation=100_000_000,
            total_volume=50_000_000,
            circulating_supply=2_000_000,
        ),
        identity_confidence=96,
    )

    assert signal["stage"] == "HIGH_RISK"
    assert any("seyrelme" in risk for risk in signal["risk_flags"])
    assert any("spekülasyon" in risk for risk in signal["risk_flags"])


class _Response:
    status_code = 200
    headers = {}
    content = b"json"

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response(self.payload)


class _RateLimitedResponse:
    status_code = 429
    headers = {"Retry-After": "120"}
    content = b""


class _RateLimitedSession:
    def __init__(self):
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return _RateLimitedResponse()


def test_provider_batches_symbols_and_refuses_ambiguous_identity():
    provider = FundamentalMetricsProvider(cache_ttl_seconds=1800)
    provider.session = _Session([
        _market_row(),
        _market_row(id="other-nova", name="Other Nova", market_cap=1_000_000),
        _market_row(id="alpha", symbol="alpha", name="Alpha"),
    ])

    result = provider.fetch_many([
        _listing(title="MEXC Spot API'de yeni görülen NOVAUSDT"),
        _listing("ALPHA", title="MEXC Will List Alpha (ALPHA)"),
    ])

    assert len(provider.session.calls) == 1
    assert provider.session.calls[0][1]["params"]["include_tokens"] == "all"
    assert result["NOVAUSDT"]["status"] == "AMBIGUOUS"
    assert result["ALPHAUSDT"]["status"] == "READY"


def test_provider_429_starts_shared_cooldown_without_repeat_request():
    provider = FundamentalMetricsProvider(cache_ttl_seconds=1800)
    provider.session = _RateLimitedSession()

    first = provider.fetch_many([_listing()])
    second = provider.fetch_many([_listing()])

    assert first["NOVAUSDT"]["status"] == "PROVIDER_COOLDOWN"
    assert second["NOVAUSDT"]["status"] == "PROVIDER_COOLDOWN"
    assert provider.session.calls == 1


class _ListingProvider:
    def fetch_listings(self):
        return [_listing()]


class _FundamentalProvider:
    def fetch_many(self, listings):
        assert [item.pair for item in listings] == ["NOVAUSDT"]
        return {
            "NOVAUSDT": score_fundamental_snapshot(
                _market_row(), identity_confidence=96, matched_by="TITLE_AND_SYMBOL"
            )
        }


def test_engine_keeps_fundamentals_separate_from_opportunity_score():
    config = UnifiedConfig(
        cex_enabled=False,
        listing_enabled=True,
        social_enabled=False,
        fundamental_enabled=True,
    )
    snapshot = UnifiedRadarEngine(
        config,
        DEFAULT_TRADE_UNIVERSE,
        listing_provider=_ListingProvider(),
        fundamental_provider=_FundamentalProvider(),
    ).scan_once(now=1_700_000_000)

    assert len(snapshot.fundamental_candidates) == 1
    candidate = snapshot.fundamental_candidates[0]
    assert candidate.metadata["fundamentals"]["fundamental_score"] >= 75
    assert candidate.score != candidate.metadata["fundamentals"]["fundamental_score"]
    assert candidate.execution_eligible is False
