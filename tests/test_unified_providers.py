from __future__ import annotations

import json

from acce_unified.providers import MexcNewListingProvider, MexcPublicProvider


class FakeResponse:
    def __init__(self, payload, *, content_type="application/json", text=""):
        self.payload = payload
        self.headers = {"content-type": content_type}
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        if "mexc" in url:
            return FakeResponse(
                ValueError("not json"),
                content_type="text/html",
                text="<html>Site Unavailable</html>",
            )
        return FakeResponse([
            {
                "symbol": "BTCUSDT",
                "lastPrice": "100000",
                "priceChangePercent": "3.5",
                "quoteVolume": "123456789",
            }
        ])


def test_cex_provider_falls_back_and_labels_venue():
    provider = MexcPublicProvider(
        endpoints=(
            ("MEXC", "https://mexc.test/tickers"),
            ("BINANCE", "https://binance.test/tickers"),
        )
    )
    provider.session = FakeSession()
    rows = provider.fetch_tickers()
    assert len(provider.session.calls) == 2
    assert rows[0].symbol == "BTCUSDT"
    assert rows[0].venue == "BINANCE"


class ListingSession:
    def get(self, url, params=None, timeout=15):
        if "announcement.test" in url:
            return FakeResponse([], content_type="text/html", text="<html>blocked</html>")
        if url.endswith("/exchangeInfo"):
            return FakeResponse({
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "1",
                        "isSpotTradingAllowed": True,
                    },
                    {
                        "symbol": "NEWUSDT",
                        "status": "1",
                        "isSpotTradingAllowed": False,
                    },
                ]
            })
        if url.endswith("/ticker/24hr"):
            return FakeResponse([
                {
                    "symbol": "NEWUSDT",
                    "lastPrice": "0.25",
                    "priceChangePercent": "12",
                    "quoteVolume": "15000000",
                }
            ])
        if url.endswith("/klines"):
            volumes = [10, 10, 10, 10, 10, 10, 20, 999]
            return FakeResponse([
                [index, 0, 0, 0, 0, volume, 0, volume]
                for index, volume in enumerate(volumes)
            ])
        raise AssertionError(f"unexpected URL: {url}")


def test_listing_provider_uses_persisted_mexc_exchange_diff_fallback(tmp_path):
    seen_file = tmp_path / "seen.json"
    seen_file.write_text(json.dumps(["BTCUSDT"]), encoding="utf-8")
    provider = MexcNewListingProvider(
        base_url="https://mexc.test",
        announcement_endpoints=("https://announcement.test",),
        seen_file=str(seen_file),
    )
    provider.session = ListingSession()

    rows = provider.fetch_listings()

    assert len(rows) == 1
    assert rows[0].pair == "NEWUSDT"
    assert rows[0].discovery_source == "EXCHANGE_DIFF"
    assert rows[0].volume_acceleration == 2.0
    assert set(json.loads(seen_file.read_text("utf-8"))) == {"BTCUSDT", "NEWUSDT"}


def test_new_listing_is_rechecked_after_one_time_exchange_diff(tmp_path):
    seen_file = tmp_path / "seen.json"
    seen_file.write_text(json.dumps(["BTCUSDT"]), encoding="utf-8")
    provider = MexcNewListingProvider(
        base_url="https://mexc.test",
        announcement_endpoints=("https://announcement.test",),
        seen_file=str(seen_file),
    )
    provider.session = ListingSession()

    first = provider.fetch_listings()
    second = provider.fetch_listings()

    assert [row.pair for row in first] == ["NEWUSDT"]
    assert [row.pair for row in second] == ["NEWUSDT"]
    catalog = json.loads((tmp_path / "mexc_listing_candidates.json").read_text("utf-8"))
    assert catalog["NEWUSDT"]["first_seen_at"] > 0


class AnnouncementOnlySession:
    def get(self, url, params=None, timeout=15):
        if "announcement.test" in url:
            return FakeResponse(
                [],
                content_type="text/html",
                text='<script>{"title":"First in Market: JIMOTHY Now Live on MEXC Meme+"}</script>',
            )
        return FakeResponse(
            ValueError("not json"),
            content_type="text/html",
            text="<html>Site Unavailable</html>",
        )


def test_listing_provider_keeps_official_announcement_when_spot_api_is_blocked(tmp_path):
    provider = MexcNewListingProvider(
        base_url="https://mexc.test",
        announcement_endpoints=("https://announcement.test",),
        seen_file=str(tmp_path / "seen.json"),
    )
    provider.session = AnnouncementOnlySession()

    rows = provider.fetch_listings()

    assert [(row.pair, row.spot_status, row.discovery_source) for row in rows] == [
        ("JIMOTHYUSDT", "UNKNOWN", "ANNOUNCEMENT")
    ]


class MixedDiscoverySession(ListingSession):
    def get(self, url, params=None, timeout=15):
        if "announcement.test" in url:
            return FakeResponse(
                [],
                content_type="text/html",
                text='<script>{"title":"MEXC Will List Nova Protocol (NOVA)"}</script>',
            )
        return super().get(url, params=params, timeout=timeout)


def test_listing_provider_merges_announcement_and_exchange_diff(tmp_path):
    seen_file = tmp_path / "seen.json"
    seen_file.write_text(json.dumps(["BTCUSDT"]), encoding="utf-8")
    provider = MexcNewListingProvider(
        base_url="https://mexc.test",
        announcement_endpoints=("https://announcement.test",),
        seen_file=str(seen_file),
    )
    provider.session = MixedDiscoverySession()

    rows = provider.fetch_listings()
    by_pair = {row.pair: row for row in rows}

    assert set(by_pair) == {"NOVAUSDT", "NEWUSDT"}
    assert by_pair["NOVAUSDT"].discovery_source == "ANNOUNCEMENT"
    assert by_pair["NEWUSDT"].discovery_source == "EXCHANGE_DIFF"


def test_listing_provider_prioritizes_exchange_diff_before_candidate_cap(tmp_path):
    seen_file = tmp_path / "seen.json"
    seen_file.write_text(json.dumps(["BTCUSDT"]), encoding="utf-8")
    provider = MexcNewListingProvider(
        base_url="https://mexc.test",
        announcement_endpoints=("https://announcement.test",),
        seen_file=str(seen_file),
        max_candidates=1,
    )
    provider.session = MixedDiscoverySession()

    rows = provider.fetch_listings()

    assert [(row.pair, row.discovery_source) for row in rows] == [
        ("NEWUSDT", "EXCHANGE_DIFF")
    ]


class SpotTransitionSession(ListingSession):
    def __init__(self):
        self.market_status = "2"

    def get(self, url, params=None, timeout=15):
        if "announcement.test" in url:
            return FakeResponse([], content_type="text/html", text="<html>blocked</html>")
        if url.endswith("/exchangeInfo"):
            return FakeResponse({
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "1",
                        "isSpotTradingAllowed": True,
                    },
                    {
                        "symbol": "NEWUSDT",
                        "status": self.market_status,
                        "isSpotTradingAllowed": False,
                    },
                ]
            })
        return super().get(url, params=params, timeout=timeout)


def test_listing_provider_surfaces_market_status_transition(tmp_path):
    seen_file = tmp_path / "seen.json"
    seen_file.write_text(json.dumps(["BTCUSDT"]), encoding="utf-8")
    session = SpotTransitionSession()
    provider = MexcNewListingProvider(
        base_url="https://mexc.test",
        announcement_endpoints=("https://announcement.test",),
        seen_file=str(seen_file),
    )
    provider.session = session

    assert provider.fetch_listings() == []
    assert set(json.loads(seen_file.read_text("utf-8"))) == {"BTCUSDT"}

    session.market_status = "1"
    rows = provider.fetch_listings()

    assert [(row.pair, row.spot_status, row.discovery_source) for row in rows] == [
        ("NEWUSDT", "OPEN", "EXCHANGE_DIFF")
    ]
    assert set(json.loads(seen_file.read_text("utf-8"))) == {"BTCUSDT", "NEWUSDT"}


class ApiPermissionTransitionSession(ListingSession):
    def __init__(self):
        self.api_trading_allowed = False

    def get(self, url, params=None, timeout=15):
        if url.endswith("/exchangeInfo"):
            return FakeResponse({
                "symbols": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "1",
                        "isSpotTradingAllowed": True,
                    },
                    {
                        "symbol": "NEWUSDT",
                        "status": "1",
                        "isSpotTradingAllowed": self.api_trading_allowed,
                    },
                ]
            })
        return super().get(url, params=params, timeout=timeout)


def test_listing_provider_ignores_api_permission_transition(tmp_path):
    seen_file = tmp_path / "seen.json"
    session = ApiPermissionTransitionSession()
    provider = MexcNewListingProvider(
        base_url="https://mexc.test",
        announcement_endpoints=("https://announcement.test",),
        seen_file=str(seen_file),
    )
    provider.session = session

    assert provider.fetch_listings() == []
    assert set(json.loads(seen_file.read_text("utf-8"))) == {"BTCUSDT", "NEWUSDT"}

    session.api_trading_allowed = True

    assert provider.fetch_listings() == []


class ManualWatchSession(ListingSession):
    def __init__(self):
        self.kline_params = []

    def get(self, url, params=None, timeout=15):
        if url.endswith("/exchangeInfo"):
            return FakeResponse({
                "symbols": [
                    {"symbol": "BTCUSDT", "status": "1"},
                    {"symbol": "OLDUSDT", "status": "1"},
                ]
            })
        if url.endswith("/ticker/24hr"):
            return FakeResponse([
                {
                    "symbol": "OLDUSDT",
                    "lastPrice": "1.0",
                    "priceChangePercent": "4",
                    "quoteVolume": "3000000",
                    "bidPrice": "0.999",
                    "askPrice": "1.001",
                }
            ])
        if url.endswith("/klines"):
            self.kline_params.append(params)
        return super().get(url, params=params, timeout=timeout)


def test_manual_watch_is_scanned_without_a_fresh_listing_event(tmp_path):
    seen_file = tmp_path / "seen.json"
    seen_file.write_text(json.dumps(["BTCUSDT", "OLDUSDT"]), encoding="utf-8")
    session = ManualWatchSession()
    provider = MexcNewListingProvider(
        base_url="https://mexc.test",
        announcement_endpoints=("https://announcement.test",),
        seen_file=str(seen_file),
    )
    provider.session = session
    provider.set_watched_pairs(["OLDUSDT"])

    rows = provider.fetch_listings()

    assert [(row.pair, row.manually_watched) for row in rows] == [("OLDUSDT", True)]
    assert session.kline_params[0]["interval"] == "5m"
