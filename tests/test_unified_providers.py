from __future__ import annotations

from acce_unified.providers import MexcPublicProvider


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
