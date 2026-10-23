from __future__ import annotations

import json
import math

import pytest

from acce_unified import long_watch as lw
from acce_unified import register as rg
from acce_unified.trend_loop import Ledger

DAY = lw.DAY
# 2026-10-19 is a Monday; NOW is Tuesday 2026-10-20 01:00 UTC
MONDAY = 1792368000
NOW = MONDAY + DAY + 3600
assert lw.week_start(MONDAY) == MONDAY


# ---------------------------------------------------------------------------
# synthetic candles
# ---------------------------------------------------------------------------


def _candles(days: int, *, end: int = MONDAY + DAY, price: float = 100.0, wick_day: int | None = None,
             wick_low: float | None = None, skip: set[int] = frozenset(), drift: float = 0.0) -> list[lw.Candle]:
    """``days`` closed daily candles ending the day before ``end``; a flat price unless told otherwise."""

    out = []
    first = end - days * DAY
    for i in range(days):
        day0 = first + i * DAY
        if i in skip:
            continue
        p = price * (1 + drift) ** i
        low = p * 0.98
        if wick_day is not None and i == wick_day and wick_low is not None:
            low = wick_low
        out.append(lw.Candle(day0, p, p * 1.02, low, p, 1000.0))
    return out


def test_calendar_helpers():
    assert lw.day_start(NOW) == MONDAY + DAY
    assert lw.week_start(MONDAY + 6 * DAY + 100) == MONDAY and lw.week_start(MONDAY - 1) == MONDAY - 7 * DAY
    assert lw._iso(MONDAY) == "2026-10-19"


def test_mexc_klines_parse_validate_and_drop_the_forming_day():
    payload = {"success": True, "data": {"time": [MONDAY - DAY, MONDAY, MONDAY + DAY], "open": [1, 1, 1],
                                         "high": [1.1, 1.2, 1.3], "low": [0.9, 0.8, 0.7], "close": [1.05, 1.1, 1.0],
                                         "vol": [10, 20, 30]}}
    candles = lw.parse_mexc_klines(payload, now=NOW)
    assert [c.day0 for c in candles] == [MONDAY - DAY, MONDAY]          # the day holding NOW is still forming
    assert candles[-1].low == 0.8
    bad = json.loads(json.dumps(payload))
    bad["data"]["high"][0] = 0.5                                        # high below open: impossible
    with pytest.raises(lw.LongDataError):
        lw.parse_mexc_klines(bad, now=NOW)
    bad = json.loads(json.dumps(payload))
    bad["data"]["close"][1] = "nan"
    with pytest.raises(lw.LongDataError):
        lw.parse_mexc_klines(bad, now=NOW)
    bad = json.loads(json.dumps(payload))
    bad["data"]["vol"] = [10, 20]
    with pytest.raises(lw.LongDataError):
        lw.parse_mexc_klines(bad, now=NOW)
    with pytest.raises(lw.LongDataError):
        lw.parse_mexc_klines({"success": True, "data": []}, now=NOW)


def test_the_wick_reference_is_the_worst_of_three_known_depths():
    candles = _candles(740, wick_day=600, wick_low=45.0)               # inside the last 365 days
    ref = lw.wick_reference(candles, now=NOW, price=100.0)
    assert ref["wick365"] == pytest.approx(-0.55)
    assert ref["flash365"] == pytest.approx(-0.55)                       # 45 / previous close 100 − 1
    assert ref["cycle_low"] == pytest.approx(0.0)                        # flat closes
    assert ref["worst"] == pytest.approx(-0.55) and ref["reference_price"] == pytest.approx(45.0)
    # unknown price → nothing
    assert lw.wick_reference(candles, now=NOW, price=None)["worst"] is None


def test_short_or_holed_history_leaves_the_reference_unknown():
    short = _candles(300)
    ref = lw.wick_reference(short, now=NOW, price=100.0)
    assert ref["wick365"] is None and ref["cycle_low"] is None and ref["worst"] is None
    middle = _candles(500)                                               # 365 known, 730 not
    ref = lw.wick_reference(middle, now=NOW, price=100.0)
    assert ref["wick365"] is not None and ref["cycle_low"] is None and ref["worst"] is not None
    holed = _candles(740, skip=set(range(700, 707)))                     # seven missing days inside 365
    ref = lw.wick_reference(holed, now=NOW, price=100.0)
    assert ref["wick365"] is None and ref["cycle_low"] is not None       # 730 tolerates ten


def test_leverage_follows_the_wick_rule():
    assert lw.max_leverage(-0.55, maintenance=0.05) == pytest.approx(1 / (1.05 - 0.36), rel=1e-6)   # ≈ 1.45
    assert lw.max_leverage(-0.10) == lw.MAX_LEVERAGE                     # shallow wick: the code cap binds
    assert lw.max_leverage(-0.95) < 1                                    # even 1× violates the rule
    assert lw.max_leverage(None) is None
    assert lw.liquidation_price(100.0, 1.0, maintenance=0.05) == pytest.approx(5.0)
    assert lw.liquidation_price(100.0, 1.45, maintenance=0.05) == pytest.approx(100 * (1 - 1 / 1.45 + 0.05))
    assert lw.protection_stop(45.0) == pytest.approx(40.5)


def test_weekly_closes_need_a_closed_sunday_and_the_channel_walks_history():
    candles = _candles(30, end=MONDAY + DAY)                             # ends Monday: that week is incomplete
    weeks = lw.weekly_closes(candles, now=NOW)
    assert all(lw.week_start(w) == w for w, _ in weeks) and weeks[-1][0] == MONDAY - 7 * DAY
    assert lw.channel([1.0] * 10)["state"] is None                       # too short
    closes = [100.0] * 25 + [110.0]                                      # breakout above the 10-week high
    assert lw.channel(closes)["state"] == lw.UP
    closes += [105.0] * 5                                                # inside the channel: state kept
    assert lw.channel(closes)["state"] == lw.UP
    closes += [90.0]                                                     # below the 10-week low
    out = lw.channel(closes)
    assert out["state"] == lw.DOWN and out["low"] == 90.0 and out["high"] == 110.0


def test_funding_is_annualised_only_with_enough_settlements():
    settlements = [(NOW - i * 8 * 3600, 0.0001) for i in range(270)]
    out = lw.funding_90(settlements, now=NOW)
    assert out["count"] == 270 and out["annual"] == pytest.approx(0.0001 * 1095)
    assert lw.funding_90(settlements[:30], now=NOW)["annual"] is None
    old = [(NOW - 100 * DAY - i * 8 * 3600, 0.01) for i in range(300)]  # outside the window
    assert lw.funding_90(old, now=NOW)["count"] == 0


def test_leverage_buildup_and_regime_fail_closed_on_unknowns():
    th = {"oi_mcap_gt": 0.20, "perp_spot_gt": 6.0}
    assert lw.leverage_buildup(oi_usd=300, market_cap=1000, perp_volume=70, spot_volume=10, thresholds=th)["triggered"] is True
    assert lw.leverage_buildup(oi_usd=100, market_cap=1000, perp_volume=70, spot_volume=10, thresholds=th)["triggered"] is False
    assert lw.leverage_buildup(oi_usd=None, market_cap=1000, perp_volume=70, spot_volume=10, thresholds=th)["triggered"] is None
    assert lw.regime(btc_d=None, btc_d_then=None, alt90=61)["slice"] is None
    assert lw.regime(btc_d=54.9, btc_d_then=None, alt90=None)["slice"] == lw.SLICE_2      # needs no direction
    assert lw.regime(btc_d=57.7, btc_d_then=None, alt90=61)["slice"] is None              # direction unknown
    assert lw.regime(btc_d=61.0, btc_d_then=59.0, alt90=61)["slice"] == lw.REDUCE
    assert lw.regime(btc_d=57.7, btc_d_then=59.0, alt90=61)["slice"] == lw.SLICE_1
    assert lw.regime(btc_d=57.7, btc_d_then=57.6, alt90=61)["slice"] == lw.WAIT           # flat, not falling
    assert lw.regime(btc_d=57.7, btc_d_then=59.0, alt90=None)["slice"] is None
    assert lw.regime(btc_d=57.7, btc_d_then=59.0, alt90=40)["slice"] == lw.WAIT


def test_position_share_scales_with_volatility():
    budget = {rg.ALTCOIN: 0.01, rg.MEME: 0.005}
    assert lw.position_share(rg.ALTCOIN, 0.9, loss_budget=budget) == pytest.approx(0.0125)
    assert lw.position_share(rg.ALTCOIN, 1.8, loss_budget=budget) == pytest.approx(0.00625)
    assert lw.position_share(rg.MEME, 0.6, loss_budget=budget) == pytest.approx(0.00625)
    assert lw.position_share(rg.MEME, None, loss_budget=budget) is None


def _ok(coin, **over):
    base = dict(regime_slice=lw.SLICE_1, channel_state=lw.UP, worst=-0.5, l_max=1.5, fund_annual=0.05, buildup=False)
    base.update(over)
    return lw.evaluate(coin, **base)


def test_every_condition_alone_removes_the_signal():
    reg = rg.load()
    uni, bnb, sky = reg.find("UNI"), reg.find("BNB"), reg.find("SKY")
    assert _ok(uni) == {"signal": lw.SLICE_1, "missing": []}
    assert _ok(uni, regime_slice=lw.SLICE_2)["signal"] == lw.SLICE_2
    assert "koşul bekliyor" in _ok(bnb)["missing"] and _ok(bnb)["signal"] is None
    assert any(m.startswith("sepet") for m in _ok(sky)["missing"])
    for over, text in ((dict(regime_slice=lw.WAIT), "rejim BEKLE"), (dict(regime_slice=None), "rejim BİLİNMİYOR"),
                       (dict(regime_slice=lw.REDUCE), "rejim AZALT"), (dict(channel_state=lw.DOWN), "kanal AŞAĞI"),
                       (dict(channel_state=None), "kanal BİLİNMİYOR"), (dict(worst=None, l_max=None), "fitil referansı bilinmiyor"),
                       (dict(worst=-0.95, l_max=0.9), "1× bile"), (dict(fund_annual=None), "funding bilinmiyor"),
                       (dict(fund_annual=0.25), "funding yıllık %25"), (dict(buildup=None), "OI/hacim bilinmiyor"),
                       (dict(buildup=True), "kaldıraç birikimi")):
        out = _ok(uni, **over)
        assert out["signal"] is None and any(text in m for m in out["missing"]), (over, out)


# ---------------------------------------------------------------------------
# the weekly refresh with fake providers
# ---------------------------------------------------------------------------


class FakeMexc:
    def __init__(self, *, fail: set[str] = frozenset(), wick: dict[str, float] | None = None):
        self.fail, self.wick, self.calls = set(fail), wick or {}, []

    def daily(self, symbol, *, now):
        self.calls.append(("daily", symbol))
        if symbol in self.fail:
            raise lw.LongDataError("mexc http 500")
        candles = _candles(740, end=lw.day_start(now), wick_day=600, wick_low=self.wick.get(symbol, 45.0))
        # a breakout in the last complete week so the channel is UP
        last_week = lw.week_start(lw.day_start(now)) - 7 * DAY
        return [lw.Candle(c.day0, c.open, c.high * 1.5, c.low, c.close * (1.3 if c.day0 >= last_week else 1.0), c.volume)
                for c in candles]

    def detail(self, symbol):
        return {"contract_size": 1.0, "maintenance_margin": 0.004}

    def ticker(self, symbol):
        return {"price": 130.0, "oi_contracts": 1000.0, "volume_usdt_24h": 5e6, "funding_rate": 0.0001}

    def funding(self, symbol, *, since):
        return [(since + i * 8 * 3600, 0.0001) for i in range(270)]


class FakeGecko:
    def __init__(self, *, btc_d=54.0, alt=None, deriv_fail=False, global_fail=False):
        self.btc_d, self.alt, self.deriv_fail, self.global_fail = btc_d, alt, deriv_fail, global_fail
        self.alt_calls = 0

    def markets_for(self, ids):
        return {i: {"price": 130.0, "market_cap": 1e9, "spot_volume": 5e7} for i in ids}

    def global_(self):
        if self.global_fail:
            raise lw.AltcoinDataError("coingecko http 429")
        return {"btc_dominance": self.btc_d, "total_market_cap": 2.8e12, "updated_at": NOW}

    def derivatives(self):
        if self.deriv_fail:
            raise lw.AltcoinDataError("coingecko http 500")
        return {"UNI": {"oi_usd": 5e7, "perp_volume_usd": 1e8, "tickers": 5},
                "PEPE": {"oi_usd": 5e8, "perp_volume_usd": 1e9, "tickers": 5}}      # PEPE: OI/pd 50 %, perp/spot 20

    def altseason_90(self):
        self.alt_calls += 1
        return {"share": self.alt, "known": 45 if self.alt is not None else 10, "universe": 50}


def test_significant_rounding_keeps_meme_prices():
    assert lw._sig(0.0000040 * 0.05) == pytest.approx(2e-7) and lw._sig(0.0000040 * 0.05) != 0
    assert lw._sig(7.5234567891) == 7.5234568 and lw._sig(None) is None and lw._sig(0.0) == 0.0


class FakeMexcThousand(FakeMexc):
    """A ``1000``-prefixed contract quotes 1000 coins: prices come back a thousand times larger."""

    def daily(self, symbol, *, now):
        return [lw.Candle(c.day0, c.open * 1000, c.high * 1000, c.low * 1000, c.close * 1000, c.volume)
                for c in super().daily(symbol, now=now)]


def test_thousand_contracts_are_quoted_per_coin(tmp_path):
    watch, _ = _watch(tmp_path, mexc=FakeMexcThousand())
    snap = watch.refresh(NOW)
    bonk, uni = lw.find(snap, "BONK"), lw.find(snap, "UNI")
    assert bonk["price_multiplier"] == 1000 and uni["price_multiplier"] == 1
    assert bonk["perp_close"] == pytest.approx(130.0) and bonk["wick"]["reference_price"] == pytest.approx(45.0)
    assert bonk["liquidation_1x"] == pytest.approx(130 * 0.05) and bonk["scale_out"][0] == pytest.approx(260.0)
    assert bonk["venue_oi_usd"] == pytest.approx(1000.0 * 1.0 * 130.0 * 1000)      # contracts × size × venue price
    assert uni["perp_close"] == pytest.approx(130_000.0)                            # UNI_USDT is not prefixed: untouched
    assert "1000 coin kote eder" in lw.render_card(bonk, now=NOW)


def _watch(tmp_path, mexc=None, gecko=None):
    reg = rg.load()
    ledger = Ledger(tmp_path / "long.jsonl")
    return lw.LongWatch(reg, mexc or FakeMexc(), gecko or FakeGecko(), ledger), ledger


def test_a_refresh_evaluates_every_coin_and_records_each_decision_once(tmp_path):
    watch, ledger = _watch(tmp_path)
    snap = watch.refresh(NOW)
    assert snap["can_authorize_trade"] is False and snap["trial"] is None and snap["week"] == "2026-10-19"
    assert snap["regime"]["slice"] == lw.SLICE_2                                  # BTC.D 54 < 55 needs no history
    cards = {c["symbol"]: c for c in snap["cards"]}
    assert len(cards) == 22
    uni = cards["UNI"]
    assert uni["signal"] == lw.SLICE_2 and uni["missing"] == []
    assert uni["wick"]["worst"] == pytest.approx(45 / 130 - 1, rel=1e-3)        # relative to the perp close
    assert uni["maintenance_margin"] == 0.05                                     # the floor beats the exchange's 0.4 %
    assert 1 < uni["l_max"] <= lw.MAX_LEVERAGE and uni["liquidation_1x"] == pytest.approx(130 * 0.05)
    assert uni["channel"]["state"] == lw.UP and uni["fund90"]["annual"] == pytest.approx(0.1095)
    assert uni["scale_out"] == [pytest.approx(260.0), pytest.approx(520.0)]
    assert cards["BNB"]["signal"] is None and "koşul bekliyor" in cards["BNB"]["missing"]
    assert cards["SKY"]["signal"] is None
    pepe = cards["PEPE"]
    assert pepe["buildup"] is True and pepe["signal"] is None and any("kaldıraç" in m for m in pepe["missing"])
    assert cards["DOGE"]["buildup"] is None and "OI/hacim bilinmiyor" in cards["DOGE"]["missing"]   # not in derivatives
    records = ledger.records()
    assert len(records) == 22 and all(r["event"] == "KARAR" and r["can_authorize_trade"] is False for r in records)
    assert records[0]["register_sha256"] == snap["register"]["sha256"]
    # a second refresh in the same week writes nothing new
    watch.refresh(NOW + 3600, previous=snap)
    assert len(ledger.records()) == 22
    # the next week decides again
    watch.refresh(NOW + 7 * DAY, previous=snap)
    assert len(ledger.records()) == 44


def test_provider_failures_leave_fields_unknown_and_never_a_signal(tmp_path):
    mexc = FakeMexc(fail={"UNI_USDT"})
    watch, _ = _watch(tmp_path, mexc=mexc, gecko=FakeGecko(btc_d=58.0, alt=61.0, deriv_fail=True))
    snap = watch.refresh(NOW)
    assert snap["regime"]["slice"] is None and "yönü bilinmiyor" in snap["regime"]["reason"]   # no 4-week archive yet
    uni = lw.find(snap, "uni")
    assert uni["wick"]["worst"] is None and uni["l_max"] is None and uni["channel"]["state"] is None
    assert uni["signal"] is None and any(e.startswith("mum: mexc http 500") for e in uni["errors"])
    assert "fitil referansı bilinmiyor" in uni["missing"] and "OI/hacim bilinmiyor" in uni["missing"]
    assert any(e.startswith("türev") for e in snap["errors"])
    pendle = lw.find(snap, "PENDLE")
    assert pendle["signal"] is None and "rejim BİLİNMİYOR" in pendle["missing"]
    text = lw.render(snap, now=NOW)
    assert "http" not in text.lower().replace("https://", "") or "http 500" in text   # only the status, never a URL
    assert "://" not in text and "Emir yetkisi yok" in text and "Eksik veri" in text


def test_the_regime_direction_comes_from_the_weekly_archive(tmp_path):
    gecko = FakeGecko(btc_d=57.0, alt=61.0)
    watch, _ = _watch(tmp_path, gecko=gecko)
    previous = None
    for k in range(5):
        gecko.btc_d = 60.0 - k * 0.7                                 # falling over the weeks
        previous = watch.refresh(NOW + k * 7 * DAY, previous=previous)
    assert previous["regime"]["direction"] == "düşüyor" and previous["regime"]["slice"] == lw.SLICE_1
    assert len(previous["btcd_archive"]) == 5
    assert gecko.alt_calls == 5                                      # once per week
    again = watch.refresh(NOW + 4 * 7 * DAY + 3600, previous=previous)
    assert gecko.alt_calls == 5                                      # the same week reuses ALT90
    assert again["regime"]["slice"] == lw.SLICE_1


def test_a_markets_failure_fails_the_refresh_instead_of_a_hollow_snapshot(tmp_path):
    class Broken(FakeGecko):
        def markets_for(self, ids):
            raise lw.AltcoinDataError("coingecko http 429")
    watch, ledger = _watch(tmp_path, gecko=Broken())
    with pytest.raises(lw.AltcoinDataError):
        watch.refresh(NOW)
    assert ledger.records() == []


def test_views_escape_and_fit(tmp_path):
    watch, _ = _watch(tmp_path)
    snap = watch.refresh(NOW)
    text = lw.render(snap, now=NOW + 60)
    assert len(text) <= 4096 and "LONG TAKİBİ" in text and "Altcoin çekirdek" in text and "Meme çekirdek" in text
    assert "DİLİM 2" in text and "<script>" not in text
    card = lw.render_card(lw.find(snap, "PEPE"), now=NOW, snapshot=snap)
    assert "WICK365" in card and "Kaldıraç bir. EVET" in card and "Emir yetkisi yok" in card
    assert "Koşul:" in lw.render_card(lw.find(snap, "BNB"), now=NOW)
    assert "ilk değerlendirme" in lw.status_line(None, now=NOW, enabled=True, max_age=8 * DAY)
    assert "22 coin" in lw.status_line(snap, now=NOW, enabled=True, max_age=8 * DAY)
    assert "bayat" in lw.status_line(snap, now=NOW + 30 * DAY, enabled=True, max_age=8 * DAY)
    assert "Modül kapalı" in lw.render(None, now=NOW, enabled=False)
    assert "Liste rapordan gelir" in lw.render(None, now=NOW, error="coingecko http 429")


def test_mexc_client_errors_never_carry_urls():
    class Response:
        def __init__(self, status, payload=None, text="{}"):
            self.status_code, self._payload, self.text = status, payload, text

        def json(self):
            if self._payload is None:
                raise ValueError("no json")
            return self._payload

    class Session:
        def __init__(self, response=None, raise_exc=None):
            self.response, self.raise_exc = response, raise_exc

        def get(self, url, params=None, timeout=None):
            if self.raise_exc:
                raise self.raise_exc
            return self.response

    client = lw.MexcContract(session=Session(raise_exc=ConnectionError("https://contract.mexc.com/x?key=1")), sleep=lambda s: None)
    with pytest.raises(lw.LongDataError) as info:
        client.ticker("UNI_USDT")
    assert "://" not in str(info.value) and "ConnectionError" in str(info.value)
    client = lw.MexcContract(session=Session(Response(503)), sleep=lambda s: None)
    with pytest.raises(lw.LongDataError, match="http 503"):
        client.detail("UNI_USDT")
    client = lw.MexcContract(session=Session(Response(200, {"success": False, "code": 1002})), sleep=lambda s: None)
    with pytest.raises(lw.LongDataError, match="mexc error"):
        client.detail("UNI_USDT")
    good = lw.MexcContract(session=Session(Response(200, {"success": True, "data": {"contractSize": 10, "maintenanceMarginRate": 0.004}})),
                           sleep=lambda s: None)
    assert good.detail("WIF_USDT") == {"contract_size": 10.0, "maintenance_margin": 0.004}
    funding = lw.MexcContract(session=Session(Response(200, {"success": True, "data": {"resultList": [
        {"settleTime": (NOW - 3600) * 1000, "fundingRate": 0.0001}, {"settleTime": (NOW - 200 * DAY) * 1000, "fundingRate": 0.01}]}})),
        sleep=lambda s: None)
    rows = funding.funding("UNI_USDT", since=NOW - 90 * DAY)
    assert rows == [(NOW - 3600, 0.0001)]                              # the old settlement stops the paging and is left out


def test_gecko_aggregates_only_the_fixed_markets_and_perpetuals():
    class G(lw.Gecko):
        def __init__(self, payload):
            self.payload = payload

        def _get(self, path, params=None):
            return self.payload

    rows = [{"market": "Binance (Futures)", "contract_type": "perpetual", "index_id": "PEPE", "open_interest": 100, "volume_24h": 10},
            {"market": "Tiny Exchange", "contract_type": "perpetual", "index_id": "PEPE", "open_interest": 900, "volume_24h": 90},
            {"market": "Bybit (Futures)", "contract_type": "futures", "index_id": "PEPE", "open_interest": 50, "volume_24h": 5},
            {"market": "Bybit (Futures)", "contract_type": "perpetual", "index_id": "PEPE", "open_interest": "nan", "volume_24h": 5},
            {"market": "OKX (Futures)", "contract_type": "perpetual", "index_id": "pepe", "open_interest": 20, "volume_24h": 2}]
    agg = G(rows).derivatives()
    assert agg == {"PEPE": {"oi_usd": 120.0, "perp_volume_usd": 12.0, "tickers": 2}}
    glob = G({"data": {"market_cap_percentage": {"btc": 57.7}, "total_market_cap": {"usd": 2.8e12}, "updated_at": NOW}}).global_()
    assert glob["btc_dominance"] == 57.7
    with pytest.raises(lw.AltcoinDataError):
        G({"data": "x"}).global_()
    prices = [[(NOW - i * DAY) * 1000, 100.0 + (i == 0) * 50] for i in range(100, -1, -1)]
    assert G({"prices": prices}).return_days("x", 90) == pytest.approx(0.5)
    assert G({"prices": prices[:50]}).return_days("x", 90) is None
