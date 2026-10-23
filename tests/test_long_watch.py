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
        return {"price": 130.0, "oi_contracts": 1000.0, "volume_usdt_24h": 5e6, "funding_rate": 0.0001,
                "index_price": 129.87}

    def index_daily(self, symbol, *, now):
        if symbol in self.fail:
            raise lw.LongDataError("mexc http 500")
        # the index sits 0.1 % below every perp close: a +0.1 % premium
        return {c.day0: c.close / 1.001 for c in self.daily(symbol, now=now)}

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


def _watch(tmp_path, mexc=None, gecko=None, vix=lambda: {"value": 19.03, "date": "2026-10-22"}):
    reg = rg.load()
    ledger = Ledger(tmp_path / "long.jsonl")
    return lw.LongWatch(reg, mexc or FakeMexc(), gecko or FakeGecko(), ledger, vix=vix), ledger


def test_vix_and_premium_are_information_never_conditions(tmp_path):
    watch, _ = _watch(tmp_path)
    snap = watch.refresh(NOW, gates=_gates())
    assert snap["regime"]["vix"] == 19.03 and snap["regime"]["vix_date"] == "2026-10-22"
    uni = lw.find(snap, "UNI")
    assert uni["premium90"]["mean_pct"] == pytest.approx(0.1, rel=1e-3) and uni["premium90"]["days"] >= 60
    assert uni["premium_now"] == pytest.approx(130 / 129.87 - 1)
    assert uni["signal"] == lw.SLICE_2                                              # neither is a gate
    text = lw.render(snap, now=NOW)
    assert "VIX 19.0 (2026-10-22)" in text
    card = lw.render_card(uni, now=NOW, snapshot=snap)
    assert "Prim 90g      +0.100%" in card and "Prim şimdi" in card and "koşul değil" in card
    # a failing VIX source or index series is a note, not an error: the snapshot stays complete
    def broken():
        raise lw.LongDataError("vix http 503")
    watch, _ = _watch(tmp_path, vix=broken)
    snap = watch.refresh(NOW, gates=_gates())
    assert snap["regime"]["vix"] is None and "VIX alınamadı: vix http 503" in snap["regime"]["vix_note"]
    assert lw.complete(snap) and "ℹ️ VIX alınamadı" in lw.render(snap, now=NOW) and "VIX ?" in lw.render(snap, now=NOW)
    assert lw.find(snap, "UNI")["signal"] == lw.SLICE_2
    no_vix, _ = _watch(tmp_path, vix=None)
    assert no_vix.refresh(NOW, gates=_gates())["regime"]["vix"] is None


def test_the_vix_csv_parser_takes_the_last_numeric_row_and_the_premium_needs_sixty_days():
    assert lw.parse_vix_csv("observation_date,VIXCLS\n2026-10-21,16.68\n2026-10-22,.\n") == {"value": 16.68, "date": "2026-10-21"}
    with pytest.raises(lw.LongDataError):
        lw.parse_vix_csv("observation_date,VIXCLS\n2026-10-21,.\n")
    candles = _candles(100)
    index = {c.day0: c.close for c in candles}
    assert lw.premium_90(candles, index, now=NOW)["mean"] == pytest.approx(0.0)
    assert lw.premium_90(candles, {c.day0: c.close for c in candles[-30:]}, now=NOW)["mean"] is None   # 30 paired days

    class Session:
        def __init__(self, status=200, text="observation_date,VIXCLS\n2026-10-22,19.03\n", exc=None):
            self.status_code, self.text, self.exc = status, text, exc

        def get(self, url, timeout=None):
            if self.exc:
                raise self.exc
            return self
    assert lw.fetch_vix(session=Session()) == {"value": 19.03, "date": "2026-10-22"}
    with pytest.raises(lw.LongDataError, match="http 500"):
        lw.fetch_vix(session=Session(status=500))
    with pytest.raises(lw.LongDataError) as info:
        lw.fetch_vix(session=Session(exc=ConnectionError("https://fred.stlouisfed.org/x")))
    assert "://" not in str(info.value)


def _gates(now=NOW, **fails):
    """A clean gate check for every register coin, with the given symbols' ❌ gates."""

    return {c.symbol: {"status": "ELENDI" if c.symbol in fails else "KAPILARI_GECTI",
                       "fails": fails.get(c.symbol, {}), "at": now} for c in rg.load().coins}


def test_the_gate_guard_blocks_hard_fails_at_once_and_liquidity_after_two_decisions(tmp_path):
    watch, _ = _watch(tmp_path)
    clean = watch.refresh(NOW, gates=_gates())
    uni = lw.find(clean, "UNI")
    assert uni["signal"] == lw.SLICE_2 and uni["gate"]["known"] and uni["gate"]["block"] is None
    hacked = watch.refresh(NOW + 7 * DAY, previous=clean, gates=_gates(NOW + 7 * DAY, UNI={"Güvenlik": "son 180 günde hack: $2,000,000 kayıp"}))
    uni = lw.find(hacked, "UNI")
    assert uni["signal"] is None and any(m.startswith("kapı ❌: son 180 günde hack") for m in uni["missing"])
    assert lw.find(hacked, "PEPE")["gate"]["block"] is None                 # the other coins are untouched
    thin = watch.refresh(NOW + 14 * DAY, previous=hacked, gates=_gates(NOW + 14 * DAY, UNI={"Likidite": "30 günlük medyan hacim $8.0M"}))
    uni = lw.find(thin, "UNI")
    assert uni["signal"] == lw.SLICE_2 and uni["gate"]["warning"].startswith("likidite ❌ ilk karar")
    assert any(e.startswith("likidite ❌ ilk karar") for e in uni["errors"]) and not lw.complete(thin)
    still = watch.refresh(NOW + 21 * DAY, previous=thin, gates=_gates(NOW + 21 * DAY, UNI={"Likidite": "30 günlük medyan hacim $7.0M"}))
    uni = lw.find(still, "UNI")
    assert uni["signal"] is None and any("iki ardışık karar" in m for m in uni["missing"])
    assert "Kapılar" in lw.render_card(uni, now=NOW + 21 * DAY) and "❌" in lw.render_card(uni, now=NOW + 21 * DAY)


def test_missing_or_stale_gate_checks_are_unknown_and_block(tmp_path):
    watch, _ = _watch(tmp_path)
    none = watch.refresh(NOW)                                                  # no altcoin_watch snapshot at all
    assert all("kapı kontrolü bilinmiyor" in c["missing"] and c["signal"] is None for c in none["cards"])
    assert any(e.startswith("kapı kontrolü yok") for e in none["errors"]) and not lw.complete(none)
    stale = watch.refresh(NOW, gates=_gates(NOW - 4 * DAY))                    # older than GATES_MAX_AGE
    assert all("kapı kontrolü bilinmiyor" in c["missing"] for c in stale["cards"])
    partial = watch.refresh(NOW, gates={"UNI": {"status": "KAPILARI_GECTI", "fails": {}, "at": NOW}})
    assert lw.find(partial, "UNI")["signal"] == lw.SLICE_2 and "kapı kontrolü bilinmiyor" in lw.find(partial, "PEPE")["missing"]
    assert "bilinmiyor" in lw.render_card(lw.find(partial, "PEPE"), now=NOW)
    assert lw.gate_verdict({"status": "x", "fails": {"Faaliyet": "ücret yok"}, "at": NOW}, now=NOW, previous=None)["block"].startswith("kapı ❌: ücret yok")


def test_a_refresh_evaluates_every_coin_and_records_each_decision_once(tmp_path):
    watch, ledger = _watch(tmp_path)
    snap = watch.refresh(NOW, gates=_gates())
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
    decisions = [r for r in records if r["event"] == "KARAR"]
    assert len(decisions) == 22 and all(r["can_authorize_trade"] is False for r in records)
    assert decisions[0]["register_sha256"] == snap["register"]["sha256"]
    signals = [r for r in records if r["event"] == lw.EVENT_SIGNAL]
    assert [r["symbol"] for r in signals] == ["UNI"] and signals[0]["text"].startswith("🟢 <b>LONG SİNYALİ · UNI · DİLİM 2</b>")
    assert snap["kind"] == lw.DECISION and snap["decision_signals"] == {"UNI": lw.SLICE_2}
    assert snap["changes"] == {"new": ["UNI"], "lost": []} and len(records) == 23
    # a second refresh in the same week writes nothing new (the decision stands, the signal is not re-announced)
    again = watch.refresh(NOW + 3600, previous=snap, gates=_gates(NOW + 3600))
    assert len(ledger.records()) == 23 and again["changes"] == {"new": [], "lost": []}
    # the next week decides again; UNI's standing signal is not a new one
    nxt = watch.refresh(NOW + 7 * DAY, previous=snap, gates=_gates(NOW + 7 * DAY))
    assert len([r for r in ledger.records() if r["event"] == "KARAR"]) == 44 and nxt["changes"]["new"] == []


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


def test_an_incomplete_derivative_sum_or_an_unknown_margin_blocks_the_signal(tmp_path):
    class Gecko(FakeGecko):
        def derivatives(self):
            out = super().derivatives()
            out["UNI"] = dict(out["UNI"], incomplete=True)                 # one venue row was unusable
            out["RENDER"] = {"oi_usd": 5e7, "perp_volume_usd": 1e8, "tickers": 5, "incomplete": False}
            return out

    class Mexc(FakeMexc):
        def detail(self, symbol):
            if symbol == "PENDLE_USDT":
                raise lw.LongDataError("mexc http 502")
            if symbol == "AAVE_USDT":
                return {"contract_size": 0.01, "maintenance_margin": None}
            return super().detail(symbol)

    watch, _ = _watch(tmp_path, mexc=Mexc(), gecko=Gecko())
    snap = watch.refresh(NOW, gates=_gates())
    uni = lw.find(snap, "UNI")
    assert uni["buildup"] is None and uni["oi_mcap"] is None and uni["signal"] is None
    assert "OI/hacim bilinmiyor" in uni["missing"] and any(e.startswith("türev") for e in uni["errors"])
    for symbol in ("PENDLE", "AAVE"):
        card = lw.find(snap, symbol)
        assert card["l_max"] is None and card["maintenance_margin"] is None and card["liquidation_1x"] is None
        assert card["wick"]["worst"] is not None                           # the wick itself is known
        assert card["signal"] is None and "sürdürme marjı bilinmiyor" in card["missing"]
        assert "fitil referansı bilinmiyor" not in card["missing"] and "kaldıraç sınırı bilinmiyor" not in card["missing"]
    assert "borsa oranı alınamadı" in lw.render_card(lw.find(snap, "AAVE"), now=NOW)
    assert lw.find(snap, "RENDER")["signal"] == lw.SLICE_2                  # untouched coins still signal
    assert not lw.complete(snap)


def test_a_stale_register_warns_and_gives_no_new_slice(tmp_path):
    raw = json.loads(rg.DEFAULT_PATH.read_text(encoding="utf-8"))
    raw["report"]["report_date"], raw["report"]["data_as_of"] = "2026-03-01", "2026-02-28"   # 234 days before NOW
    stale = rg.parse(raw, path=rg.DEFAULT_PATH, sha256="stale")
    watch = lw.LongWatch(stale, FakeMexc(), FakeGecko(), Ledger(tmp_path / "stale.jsonl"))
    snap = watch.refresh(NOW)
    assert snap["register"]["stale"] is True and snap["register"]["age_days"] == 234
    assert all(c["signal"] is None and any(m.startswith("kayıt bayat") for m in c["missing"]) for c in snap["cards"])
    assert any(e.startswith("kayıt bayat") for e in snap["errors"]) and not lw.complete(snap)
    assert "Kayıt bayat" in lw.render(snap, now=NOW)
    fresh_snap = lw.LongWatch(rg.load(), FakeMexc(), FakeGecko(), Ledger(tmp_path / "fresh.jsonl")).refresh(NOW, gates=_gates())
    assert fresh_snap["register"]["stale"] is False and lw.complete(fresh_snap)
    assert lw.complete(None) is False and lw.complete({"cards": []}) is False
    assert lw.complete({"cards": [{"errors": ["x"]}], "errors": []}) is False


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
    assert agg == {"PEPE": {"oi_usd": 120.0, "perp_volume_usd": 12.0, "tickers": 2, "incomplete": True}}   # the nan row
    clean = G([r for r in rows if r["open_interest"] != "nan"]).derivatives()
    assert clean["PEPE"]["incomplete"] is False and clean["PEPE"]["oi_usd"] == 120.0
    glob = G({"data": {"market_cap_percentage": {"btc": 57.7}, "total_market_cap": {"usd": 2.8e12}, "updated_at": NOW}}).global_()
    assert glob["btc_dominance"] == 57.7
    with pytest.raises(lw.AltcoinDataError):
        G({"data": "x"}).global_()
    prices = [[(NOW - i * DAY) * 1000, 100.0 + (i == 0) * 50] for i in range(100, -1, -1)]
    assert G({"prices": prices}).return_days("x", 90) == pytest.approx(0.5)
    assert G({"prices": prices[:50]}).return_days("x", 90) is None


def test_signals_are_announced_once_lost_signals_are_reported_and_interim_refreshes_decide_nothing(tmp_path):
    watch, ledger = _watch(tmp_path)
    first = watch.refresh(NOW, gates=_gates())                                       # UNI gets DİLİM 2
    assert first["changes"]["new"] == ["UNI"]
    outbox = watch.outbox()
    assert len(outbox) == 1 and "LONG SİNYALİ · UNI" in outbox[0]["text"] and "Emir yetkisi yok" in outbox[0]["text"]
    assert "Karar fiyatı" in outbox[0]["text"] and "alım talimatı değildir" in outbox[0]["text"]
    watch.delivered(outbox[0]["id"], at=NOW + 60, sent=True)
    assert watch.outbox() == [] and ledger.records()[-1] == {"id": f"DELIVERED:{outbox[0]['id']}", "event": "DELIVERED",
                                                             "message_id": outbox[0]["id"], "at": NOW + 60, "sent": True}
    # an interim (manual) refresh mid-week: a coin that would newly qualify is NOT declared, a lost one is
    class Gecko(FakeGecko):
        def derivatives(self):
            out = super().derivatives()
            out["PENDLE"] = {"oi_usd": 5e7, "perp_volume_usd": 1e8, "tickers": 5, "incomplete": False}   # PENDLE now qualifies
            return out
    interim_watch = lw.LongWatch(rg.load(), FakeMexc(fail={"UNI_USDT"}), Gecko(), ledger)   # UNI's candles fail: signal lost
    interim = interim_watch.refresh(NOW + 2 * DAY, previous=first, gates=_gates(NOW + 2 * DAY), kind=lw.INTERIM)
    assert interim["kind"] == lw.INTERIM and interim["decision_signals"] == {}
    assert lw.find(interim, "PENDLE")["signal"] == lw.SLICE_2                        # shown, but not a decision
    assert interim["changes"] == {"new": [], "lost": ["UNI"]}
    assert [r["event"] for r in ledger.records() if r["event"] in (lw.EVENT_SIGNAL, lw.EVENT_SIGNAL_LOST)] == [lw.EVENT_SIGNAL, lw.EVENT_SIGNAL_LOST]
    assert not any(r["event"] == "KARAR" and r["at"] == NOW + 2 * DAY for r in ledger.records())   # no KARAR mid-week
    assert "ara tarama" in lw.render(interim, now=NOW + 2 * DAY)
    lost = watch.outbox()
    assert len(lost) == 1 and lost[0]["text"].startswith("🔴 <b>SİNYAL DÜŞTÜ · UNI</b>") and "fitil referansı" in lost[0]["text"]
    # the next decision declares PENDLE as new (it was never a decision signal)
    decision = interim_watch.refresh(NOW + 7 * DAY, previous=interim, gates=_gates(NOW + 7 * DAY))
    assert "PENDLE" in decision["changes"]["new"] and decision["decision_signals"].get("PENDLE") == lw.SLICE_2
    with pytest.raises(ValueError):
        watch.refresh(NOW, gates=_gates(), kind="weird")


def test_a_hard_gate_fail_drops_a_standing_signal_the_same_day(tmp_path):
    watch, ledger = _watch(tmp_path)
    snap = watch.refresh(NOW, gates=_gates())
    for m in watch.outbox():
        watch.delivered(m["id"], at=NOW)
    assert snap["decision_signals"] == {"UNI": lw.SLICE_2}
    # liquidity alone does not drop it between decisions; a hack does
    assert watch.gate_losses(snap, _gates(NOW + DAY, UNI={"Likidite": "30 günlük medyan hacim $8.0M"}), NOW + DAY) == []
    dropped = watch.gate_losses(snap, _gates(NOW + DAY, UNI={"Güvenlik": "son 180 günde hack: $3,000,000 kayıp"}), NOW + DAY)
    assert dropped == ["UNI"] and snap["decision_signals"] == {} and snap["counts"]["signal"] == 0
    uni = lw.find(snap, "UNI")
    assert uni["signal"] is None and uni["missing"][0].startswith("kapı ❌: son 180 günde hack")
    outbox = watch.outbox()
    assert len(outbox) == 1 and "SİNYAL DÜŞTÜ · UNI" in outbox[0]["text"] and "hack" in outbox[0]["text"]
    # reported once per week even if the ❌ persists
    assert watch.gate_losses(snap, _gates(NOW + 2 * DAY, UNI={"Güvenlik": "son 180 günde hack: $3,000,000 kayıp"}), NOW + 2 * DAY) == []
    assert len(watch.outbox()) == 1
    assert watch.gate_losses(None, _gates(), NOW) == [] and watch.gate_losses(snap, None, NOW) == []


def test_the_scores_view_shows_both_categories_with_the_reports_numbers(tmp_path):
    watch, _ = _watch(tmp_path)
    reg = rg.load()
    empty = lw.render_scores(None, reg, now=NOW)
    assert "PUANLAR" in empty and "Altcoinler" in empty and "Meme coinler" in empty and "değerlendirme yok" in empty
    snap = watch.refresh(NOW, gates=_gates())
    text = lw.render_scores(snap, reg, now=NOW + 120)
    assert "UNI     çek     76 0.40 0.62  DİLİM 2" in text                             # the report's row + the bot's state
    assert "BNB     koş     66 0.24 0.49  koşul" in text
    assert "PEPE    çek     90 0.37 0.69  kaldıraç" in text                             # leverage build-up blocks PEPE
    assert "SHIB    dğr?    43 0.32    ?" in text                                       # the report gives no S for SHIB
    assert "bot puanlamaz" in text and "Emir yetkisi yok" in text and len(text) <= 4096
    interim = watch.refresh(NOW + DAY, previous=snap, gates=_gates(NOW + DAY), kind=lw.INTERIM)
    assert "ara tarama, karar değil" in lw.render_scores(interim, reg, now=NOW + DAY)
    import re as _re
    tables = _re.findall(r"<pre>(.*?)</pre>", text, flags=_re.S)
    assert len(tables) == 2 and all(len(row) <= 40 for table in tables for row in table.splitlines())   # phone width


def test_a_snapshot_from_before_decision_signals_is_migrated_not_re_announced(tmp_path):
    watch, ledger = _watch(tmp_path)
    old = watch.refresh(NOW, gates=_gates())
    old = dict(old)
    del old["decision_signals"]                                                      # the previous version's shape
    assert lw.standing_signals(old) == {"UNI": lw.SLICE_2} and lw.standing_signals(None) == {}
    # the first decision under the new version: UNI is a standing signal, not a new one
    nxt = watch.refresh(NOW + 7 * DAY, previous=old, gates=_gates(NOW + 7 * DAY))
    assert nxt["changes"]["new"] == [] and nxt["decision_signals"] == {"UNI": lw.SLICE_2}
    # and a hard gate ❌ during the week drops it from such a snapshot
    old2 = dict(watch.refresh(NOW + 14 * DAY, previous=nxt, gates=_gates(NOW + 14 * DAY)))
    del old2["decision_signals"]
    dropped = watch.gate_losses(old2, _gates(NOW + 15 * DAY, UNI={"Güvenlik": "son 180 günde hack: $2,000,000 kayıp"}), NOW + 15 * DAY)
    assert dropped == ["UNI"] and old2["decision_signals"] == {}


def test_a_failing_ledger_keeps_the_standing_signal(tmp_path):
    watch, ledger = _watch(tmp_path)
    snap = watch.refresh(NOW, gates=_gates())

    class Broken:
        def append(self, event, *, text=None):
            raise OSError("disk full")

        def pending(self):
            return []
    watch.ledger = Broken()
    with pytest.raises(OSError):
        watch.gate_losses(snap, _gates(NOW + DAY, UNI={"Güvenlik": "hack"}), NOW + DAY)
    assert snap["decision_signals"] == {"UNI": lw.SLICE_2} and lw.find(snap, "UNI")["signal"] == lw.SLICE_2   # nothing lost silently
