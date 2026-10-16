from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from acce_unified import altcoin_watch as aw
from acce_unified.trend_loop import Ledger

NOW = int(datetime(2026, 10, 16, 12, tzinfo=timezone.utc).timestamp())
DAY = 86_400


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def market(gecko_id: str, symbol: str, *, rank: int = 10, price: float = 2.0, cap: float = 2e9,
           circ: float | None = 1e9, total: float | None = 1.02e9, max_supply: float | None = None,
           fdv: float | None = 2.04e9, ath: float | None = 6.0, ath_date: int | None = NOW - 400 * DAY,
           name: str | None = None, high: float | None = None, low: float | None = None) -> dict:
    return {"id": gecko_id, "symbol": symbol.lower(), "name": name or f"Coin {symbol}", "market_cap_rank": rank,
            "current_price": price, "market_cap": cap, "fully_diluted_valuation": fdv, "circulating_supply": circ,
            "total_supply": total, "max_supply": max_supply, "ath": ath, "ath_date": _iso(ath_date) if ath_date else None,
            "total_volume": 3e7, "high_24h": high or price * 1.05, "low_24h": low or price * 0.95}


def chart(*, supply: float = 1e9, growth: float = 0.0, volume: float = 2e7, price: float = 2.0,
          days: int = 365, jump_at: int | None = None) -> dict:
    prices, caps, vols = [], [], []
    for k in range(days + 1):
        ts = (NOW - (days - k) * DAY) * 1000
        s = supply * (1 + growth * k / days)
        if jump_at is not None and k >= jump_at:
            s *= 1.5
        prices.append([ts, price])
        caps.append([ts, price * s])
        vols.append([ts, volume])
    return {"prices": prices, "market_caps": caps, "total_volumes": vols}


def fee(name: str, *, did: str, d30: float = 3e6, y1: float | None = 3.6e7, monthly: float | None = 3e6,
        chain: bool = False) -> dict:
    return {"name": name, "displayName": name, "defillamaId": did, "protocolType": "chain" if chain else "protocol",
            "total30d": d30, "total1y": y1, "monthlyAverage1y": monthly}


LITE = {"protocols": [{"defillamaId": "11", "geckoId": None, "parentProtocol": "parent#lend"},
                      {"defillamaId": "12", "geckoId": None, "parentProtocol": "parent#lend"},
                      {"defillamaId": "21", "geckoId": "dexco"},
                      {"defillamaId": "31", "geckoId": "hacked"}],
        "parentProtocols": [{"id": "parent#lend", "gecko_id": "lendco"}]}
CHAINS = [{"name": "Chainone", "gecko_id": "chainone", "gasTokenGeckoId": "chainone"},
          {"name": "Rollup", "gecko_id": "rollup-token", "gasTokenGeckoId": "ethereum"}]


class FakeGecko:
    keyed = False

    def __init__(self, rows, *, categories=None, platforms=None, charts=None):
        self.rows, self.categories = rows, categories or {}
        self.plats = platforms if platforms is not None else {}
        self.charts, self.chart_calls, self.price_calls = charts or {}, [], []
        self.quotes = {"bitcoin": 80_000.0}

    def markets(self, page, *, category=None):
        if category:
            return [{"id": i} for i in self.categories.get(category, [])]
        return self.rows if page == 1 else []

    def platforms(self):
        return self.plats

    def chart(self, gecko_id):
        self.chart_calls.append(gecko_id)
        found = self.charts.get(gecko_id, chart())
        if isinstance(found, Exception):
            raise found
        return found

    def prices(self, ids):
        self.price_calls.append(list(ids))
        return {i: p for i, p in self.quotes.items() if i in ids}


class FakeLlama:
    def __init__(self, *, fees=None, revenue=None, holders=None, hacks=None, broken=None):
        self.data = {"dailyFees": fees if fees is not None else [], "dailyRevenue": revenue or [],
                     "dailyHoldersRevenue": holders or []}
        self.hack_rows, self.broken = hacks or [], broken

    def lite(self):
        if self.broken == "lite":
            raise aw.AltcoinDataError("defillama http 500")
        return LITE

    def chains(self):
        return CHAINS

    def overview(self, data_type):
        return self.data[data_type]

    def hacks(self):
        return self.hack_rows


class FakeSecurity:
    def __init__(self):
        self.calls = []
        self.evm = {k: "0" for k, _ in aw.EVM_FAIL + aw.EVM_WARN}

    def goplus(self, chain, address):
        self.calls.append((chain, address))
        return dict(self.evm)

    def rugcheck(self, mint):
        self.calls.append(("solana", mint))
        return {"token": {"freezeAuthority": None, "mintAuthority": None}}


def _watch(tmp_path, rows, *, llama=None, size=None, **gecko_kw):
    gecko = FakeGecko(rows, **gecko_kw)
    security = FakeSecurity()
    watch = aw.AltcoinWatch(gecko, llama or default_llama(), security, Ledger(tmp_path / "ledger.jsonl"),
                            universe_size=size or len(rows), sleep=lambda s: None)
    return watch, gecko, security


def default_llama(**kw):
    fees = [fee("Lend V1", did="11", d30=4e6, monthly=2e6), fee("Lend V2", did="12", d30=1e6, monthly=1e6),
            fee("Dex", did="21", d30=2e6, monthly=2e6), fee("Chainone", did="chain#chainone", chain=True),
            fee("Rollup", did="chain#rollup", chain=True, d30=0.0)]
    revenue = [fee("Lend V1", did="11", y1=1e8), fee("Dex", did="21", y1=2e7), fee("Chainone", did="chain#c", chain=True,
                                                                                     y1=5e7)]
    holders = [fee("Lend V1", did="11", y1=4e7), fee("Chainone", did="chain#c", chain=True, y1=1e7)]
    return FakeLlama(fees=kw.get("fees", fees), revenue=kw.get("revenue", revenue), holders=kw.get("holders", holders),
                     hacks=kw.get("hacks"), broken=kw.get("broken"))


def _events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]


# ---------------------------------------------------------------------------
# Parsing and identity
# ---------------------------------------------------------------------------


def test_a_market_row_needs_a_clean_id_and_a_price():
    assert aw.parse_market(market("lendco", "LEND")).symbol == "LEND"
    assert aw.parse_market(market("Bad Id", "X")) is None
    assert aw.parse_market(market("x", "X", price=0)) is None
    assert aw.parse_market({**market("x", "X"), "market_cap_rank": True}).rank is None
    assert aw.parse_market({**market("x", "X"), "circulating_supply": "nan"}).circulating is None
    assert aw.looks_pegged(market("usdx", "USDX", price=1.0, high=1.002, low=0.999))
    assert not aw.looks_pegged(market("one", "ONE", price=1.0, high=1.08, low=0.95))


def test_defillama_fees_are_summed_per_coin_through_parents_chains_and_own_ids():
    index = aw.build_llama_index(LITE, CHAINS)
    rows = [fee("Lend V1", did="11", d30=4e6, y1=None, monthly=None), fee("Lend V2", did="12", d30=1e6, y1=1.2e7),
            fee("Chainone", did="chain#x", chain=True), fee("Nobody", did="99")]
    totals = aw.aggregate(rows, index)
    assert set(totals) == {"lendco", "chainone"}                         # unmapped fees are dropped, never guessed
    assert totals["lendco"].d30 == 5e6 and totals["lendco"].entries == 2
    assert totals["lendco"].y1_known is False                           # one year unknown: the year is unknown
    assert totals["lendco"].monthly == pytest.approx(3e6)               # monthly average falls back to 1y / 12
    assert index.gas_tokens == frozenset({"chainone", "ethereum"})      # a rollup's governance token pays no gas


def test_hacks_count_what_was_not_returned():
    index = aw.build_llama_index(LITE, CHAINS)
    hacks = aw.hacks_by_gecko([{"date": NOW - DAY, "amount": 5e6, "returnedFunds": 4.5e6, "defillamaId": "11",
                                "targetType": "DeFi Protocol"},
                               {"date": NOW, "amount": "x", "defillamaId": "31"},
                               {"date": NOW, "amount": 2e6, "defillamaId": "31", "targetType": "Chain"}], index)
    assert hacks == {"lendco": [(NOW - DAY, 5e5, "defi protocol")],
                     "hacked": [(NOW, None, ""), (NOW, 2e6, "chain")]}      # an unknown amount is kept, not dropped


def test_a_hack_with_an_unknown_amount_or_date_is_never_read_as_no_hack():
    for hacks, status in (([(NOW - 10 * DAY, None, "defi protocol")], aw.REVIEW),
                          ([(None, 5e6, "defi protocol")], aw.REVIEW),
                          ([(NOW - 400 * DAY, None, "defi protocol")], aw.WARN)):
        card = _card()
        assert aw.screen_hacks(card, hacks, now=NOW) is False
        assert card.gate(aw.SECURITY) == status


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def _card(**kw):
    return aw.Card(coin=aw.parse_market(market("lendco", "LEND", **kw)))


@pytest.mark.parametrize("total,status", [(1.05e9, aw.PASS), (1.051e9, aw.REVIEW), (None, aw.REVIEW),
                                          (0.999e9, aw.PASS), (0.99e9, aw.REVIEW)])
def test_supply_not_yet_circulating_must_be_small_or_the_coin_needs_review(total, status):
    card = _card(circ=1e9, total=total, max_supply=2e9)
    aw.screen_supply(card)
    assert card.gate(aw.SUPPLY) == status and card.gate(aw.SUPPLY) != aw.FAIL     # a schedule we lack is no verdict
    if total == 1.051e9:
        assert "takvimi ücretsiz veride yok" in card.reasons(aw.REVIEW)[0]
        assert card.info["unminted_share"] == pytest.approx(0.949)
    if total == 0.999e9:
        assert card.info["locked_share"] == 0.0                            # a rounding gap, shown as 0
    if total == 0.99e9:
        assert "veri tutarsız" in card.reasons(aw.REVIEW)[0]               # never a negative overhang


def test_economic_activity_needs_mapped_fees():
    for fees, status in ((None, aw.REVIEW), (aw.Totals(d30=0.0), aw.FAIL), (aw.Totals(d30=1.0), aw.PASS)):
        card = _card()
        aw.screen_activity(card, fees)
        assert card.gate(aw.ACTIVITY) == status


def test_a_recent_hack_eliminates_an_older_one_warns_and_returned_funds_count():
    for hacks, status, eliminated in (([(NOW - 10 * DAY, 2e6, "defi protocol")], aw.FAIL, True),
                                      ([(NOW - 400 * DAY, 2e6, "defi protocol")], aw.WARN, False),
                                      ([(NOW - 10 * DAY, 5e5, "defi protocol")], aw.SKIP, False),
                                      ([(NOW - 900 * DAY, 9e9, "defi protocol")], aw.SKIP, False)):
        card = _card()
        assert aw.screen_hacks(card, hacks, now=NOW) is eliminated
        assert card.gate(aw.SECURITY) == status


def test_a_chain_coin_is_eliminated_only_by_an_attack_on_the_chain_itself():
    app_hack = [(NOW - 10 * DAY, 4e6, "defi protocol")]       # an application DefiLlama files under the chain
    card = _card()
    assert aw.screen_hacks(card, app_hack, now=NOW, native=True) is False and card.gate(aw.SECURITY) == aw.SKIP
    card = _card()
    assert aw.screen_hacks(card, [(NOW - 10 * DAY, 4e6, "chain")], now=NOW, native=True) is True
    card = _card()
    assert aw.screen_hacks(card, app_hack, now=NOW, native=False) is True       # a token's protocol: counts


def test_contracts_are_located_by_platform_and_gas_tokens_are_native():
    index = aw.build_llama_index(LITE, CHAINS)
    plats = {"chainone": {"ethereum": "0x" + "11" * 20}, "plain": {},
             "sol-first": {"solana": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "ethereum": "0x" + "ab" * 20},
             "base-first": {"base": "0x" + "ab" * 20, "solana": "Mint"}, "tron-first": {"tron": "T1", "ethereum": "0x1"}}
    assert aw.contract_of("chainone", plats, index) == ("native", None, None)      # its bridged copy is not it
    assert aw.contract_of("plain", plats, index) == ("native", None, None)
    assert aw.contract_of("sol-first", plats, index) == ("token", "solana",
                                                         "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")
    assert aw.contract_of("base-first", plats, index) == ("token", "base", "0x" + "ab" * 20)
    assert aw.contract_of("tron-first", plats, index)[0] == "unsupported"         # never a bridged copy instead
    assert aw.contract_of("missing", plats, index)[0] == "unsupported"


def test_contract_powers():
    evm = {k: "0" for k, _ in aw.EVM_FAIL + aw.EVM_WARN}
    cases = [("native", None, None, False, aw.PASS), ("unsupported", None, None, False, aw.REVIEW),
             ("token", "ethereum", evm, False, aw.PASS), ("token", "ethereum", {**evm, "hidden_owner": "1"}, False, aw.FAIL),
             ("token", "ethereum", {**evm, "is_mintable": "1"}, False, aw.WARN),
             ("token", "ethereum", {**evm, "selfdestruct": None}, False, aw.REVIEW),
             ("token", "ethereum", {**evm, "is_mintable": None}, False, aw.WARN),
             ("token", "ethereum", None, False, aw.REVIEW), ("token", "ethereum", evm, True, aw.REVIEW),
             ("token", "solana", {"token": {"freezeAuthority": "F", "mintAuthority": None}}, False, aw.FAIL),
             ("token", "solana", {"token": {"freezeAuthority": None, "mintAuthority": "M"}}, False, aw.WARN),
             ("token", "solana", {"token": {"mintAuthority": None}}, False, aw.REVIEW)]
    for kind, chain, payload, error, status in cases:
        card = _card()
        aw.screen_contract(card, kind, chain, payload, error=error)
        assert card.gate(aw.SECURITY) == status, (kind, chain, payload)


def test_the_chart_gives_liquidity_dilution_and_the_ath_day_market_cap():
    card = _card(ath_date=NOW - 100 * DAY, ath=6.0)
    aw.screen_chart(card, chart(growth=0.10, volume=2e7), now=NOW)
    assert card.gate(aw.LIQUIDITY) == aw.PASS and card.info["volume_median_30d"] == 2e7
    assert card.measures["dilution"] == pytest.approx(0.10, abs=0.005)
    assert card.info["ath_market_cap"] == pytest.approx(6.0 * 1e9 * (1 + 0.10 * 265 / 365), rel=1e-3)
    thin = _card()
    aw.screen_chart(thin, chart(volume=9e6), now=NOW)
    assert thin.gate(aw.LIQUIDITY) == aw.FAIL
    short = _card()
    aw.screen_chart(short, chart(days=200), now=NOW)
    assert short.measures["dilution"] is None and "bir yıldan kısa" in short.info["dilution_note"]
    assert "ath_market_cap" not in short.info                         # the ATH is older than the chart
    none = _card()
    aw.screen_chart(none, None, now=NOW)
    assert none.gate(aw.LIQUIDITY) == aw.REVIEW and none.measures["dilution"] is None


def test_volume_needs_recent_distinct_days():
    stale = chart(volume=5e7)
    for key in ("prices", "market_caps", "total_volumes"):
        stale[key] = [p for p in stale[key] if p[0] < (NOW - 10 * DAY) * 1000]   # the newest day is 10 days old
    card = _card()
    aw.screen_chart(card, stale, now=NOW)
    assert card.gate(aw.LIQUIDITY) == aw.REVIEW
    repeated = chart(volume=5e7, days=12)
    repeated["total_volumes"] = [[(NOW - 5 * DAY) * 1000 + k, 5e7] for k in range(40)]   # one day, 40 points
    card = _card()
    aw.screen_chart(card, repeated, now=NOW)
    assert card.gate(aw.LIQUIDITY) == aw.REVIEW


def test_usage_is_unknown_when_part_of_its_history_is():
    index = aw.build_llama_index(LITE, CHAINS)
    totals = aw.aggregate([fee("Lend V1", did="11", d30=4e6, y1=None, monthly=None),
                           fee("Lend V2", did="12", d30=1e6, y1=1.2e7, monthly=1e6)], index)
    card = _card()
    aw.compute_measures(card, holders=None, revenue=None, fees=totals["lendco"])
    assert totals["lendco"].monthly_known is False and card.measures["usage"] is None


def test_a_jump_in_implied_supply_is_not_trusted_as_dilution():
    card = _card()
    aw.screen_chart(card, chart(jump_at=200), now=NOW)
    assert card.measures["dilution"] is None and "sıçrama" in card.info["dilution_note"]


def test_measures_never_count_a_burn_twice_and_unknown_stays_unknown():
    card = _card(cap=1e9, fdv=None)
    card.measures["dilution"] = -0.03                                     # supply fell: burns are in holder revenue
    aw.compute_measures(card, holders=aw.Totals(y1=2e7), revenue=aw.Totals(y1=5e7),
                        fees=aw.Totals(d30=3e6, monthly=2e6))
    assert card.measures["net_yield"] == pytest.approx(0.02)             # not 0.05: the burn counts once
    assert card.measures["valuation"] == pytest.approx(20.0)             # market cap when FDV is missing
    assert card.measures["usage"] == pytest.approx(0.5)
    card.measures["dilution"] = 0.04
    aw.compute_measures(card, holders=aw.Totals(y1=2e7), revenue=aw.Totals(y1=0.0), fees=None)
    assert card.measures["net_yield"] == pytest.approx(-0.02)
    assert card.measures["valuation"] is None and card.measures["usage"] is None
    aw.compute_measures(card, holders=aw.Totals(y1=2e7, y1_known=False), revenue=None, fees=None)
    assert card.measures["net_yield"] is None


def _passed(gid, cap, **measures):
    card = aw.Card(coin=aw.parse_market(market(gid, gid[:4].upper(), cap=cap)))
    for g in aw.GATES:
        card.add(g, aw.PASS, "ok")
    card.measures.update({m: None for m in aw.MEASURES}, **measures)
    return card


def test_ranking_uses_equal_weights_and_an_unknown_measure_ranks_last():
    cards = [_passed("aaa", 3e9, net_yield=0.03, valuation=10.0, usage=0.2),
             _passed("bbb", 2e9, net_yield=0.01, valuation=30.0, usage=0.0),
             _passed("ccc", 1e9, net_yield=None, valuation=5.0, usage=0.5),
             _passed("ddd", 4e9, net_yield=0.02, valuation=20.0, usage=None)]
    review = _passed("eee", 5e9, net_yield=1.0, valuation=1.0, usage=9.0)
    review.add(aw.SUPPLY, aw.REVIEW, "x")
    aw.rank(cards + [review])
    assert review.position is None and review.composite is None         # only coins passing every gate are ranked
    by_id = {c.coin.gecko_id: c for c in cards}
    assert by_id["aaa"].composite == pytest.approx((1.0 + 2 / 3 + 0.5) / 3)
    assert by_id["ccc"].composite == pytest.approx((0.0 + 1.0 + 1.0) / 3)   # no holder data: last there, no guess
    assert [c.coin.gecko_id for c in sorted(cards, key=lambda c: c.position)] == ["aaa", "ccc", "ddd", "bbb"]
    assert [by_id[g].third for g in ("aaa", "ccc", "ddd", "bbb")] == [1, 2, 2, 3]


def test_a_gate_that_was_not_reached_keeps_the_coin_in_review():
    card = _card()
    card.add(aw.SUPPLY, aw.PASS, "ok")
    card.add(aw.ACTIVITY, aw.PASS, "ok")
    assert card.status == aw.NEEDS_REVIEW                               # security and liquidity never ran
    card.add(aw.SECURITY, aw.WARN, "eski hack")
    card.add(aw.SECURITY, aw.PASS, "ok")
    card.add(aw.LIQUIDITY, aw.PASS, "ok")
    assert card.status == aw.PASSED
    card.add(aw.LIQUIDITY, aw.FAIL, "sığ")
    assert card.status == aw.ELIMINATED


# ---------------------------------------------------------------------------
# Refresh and forward record
# ---------------------------------------------------------------------------


def _rows():
    return [market("bitcoin", "BTC", rank=1, cap=1e12),
            market("lendco", "LEND", rank=20, cap=3e9),
            market("dexco", "DEX", rank=30, cap=2e9, circ=1e9, total=1.5e9),          # supply needs review
            market("chainone", "ONE", rank=25, cap=2.5e9),
            market("rollup-token", "ROLL", rank=40, cap=1e9),                        # zero fees: eliminated
            market("usdx", "USDX", rank=5, price=1.0, high=1.001, low=0.999),        # pegged: not in the universe
            market("wrapped", "WRAP", rank=6),                                       # category: not in the universe
            market("usdt", "USDT", rank=3)]                                          # stable symbol


def test_a_refresh_screens_the_universe_and_records_the_month_once(tmp_path):
    watch, gecko, security = _watch(tmp_path, _rows(), size=4, categories={"wrapped-tokens": ["wrapped"]},
                                    platforms={"lendco": {"ethereum": "0x" + "11" * 20}, "chainone": {}})
    snap = watch.refresh(NOW)
    by_id = {c["id"]: c for c in snap["cards"]}
    assert set(by_id) == {"lendco", "dexco", "chainone", "rollup-token"} and snap["can_authorize_trade"] is False
    assert by_id["lendco"]["status"] == aw.PASSED and by_id["chainone"]["status"] == aw.PASSED
    assert by_id["dexco"]["status"] == aw.NEEDS_REVIEW and by_id["rollup-token"]["status"] == aw.ELIMINATED
    assert sorted(gecko.chart_calls) == ["chainone", "lendco"]                      # deep checks only for candidates
    assert security.calls == [("ethereum", "0x" + "11" * 20)]                       # the native coin needs none
    assert snap["counts"] == {aw.PASSED: 2, aw.NEEDS_REVIEW: 1, aw.ELIMINATED: 1}
    events = _events(tmp_path)
    assert [e["event"] for e in events] == ["WATCH"] * 4 + ["MONTH"]
    assert all(e["can_authorize_trade"] is False and e["schema"] == aw.SCHEMA for e in events)
    watch.refresh(NOW + 7 * DAY)                                                    # same month: no second cohort
    assert len(_events(tmp_path)) == 5
    watch.refresh(NOW + 20 * DAY)                                                   # next month
    assert [e["event"] for e in _events(tmp_path)].count("MONTH") == 2


def test_a_core_source_failure_fails_the_refresh_instead_of_a_hollow_snapshot(tmp_path):
    watch, _, _ = _watch(tmp_path, _rows(), size=4, llama=default_llama(broken="lite"))
    with pytest.raises(aw.AltcoinDataError):
        watch.refresh(NOW)
    assert not (tmp_path / "ledger.jsonl").exists() or _events(tmp_path) == []
    small, _, _ = _watch(tmp_path, _rows(), size=40)
    with pytest.raises(aw.AltcoinDataError, match="universe too small"):
        small.refresh(NOW)


def test_per_coin_failures_and_the_deep_check_budget_leave_coins_in_review(tmp_path, monkeypatch):
    watch, gecko, _ = _watch(tmp_path, _rows(), size=4, platforms={"lendco": {"ethereum": "0x" + "11" * 20},
                                                                    "chainone": {}},
                             charts={"chainone": aw.AltcoinDataError("coingecko http 429")})
    monkeypatch.setattr(aw, "MAX_DEEP_CHECKS", 1)
    snap = watch.refresh(NOW)
    by_id = {c["id"]: c for c in snap["cards"]}
    assert by_id["lendco"]["status"] == aw.PASSED
    assert by_id["chainone"]["status"] == aw.NEEDS_REVIEW
    assert "sıraya alınamadı" in by_id["chainone"]["gates"][aw.LIQUIDITY]["notes"][0]
    monkeypatch.setattr(aw, "MAX_DEEP_CHECKS", 80)
    snap = watch.refresh(NOW + 30 * DAY)
    assert {c["id"]: c for c in snap["cards"]}["chainone"]["gates"][aw.LIQUIDITY]["status"] == aw.REVIEW


def test_outcomes_are_measured_near_the_horizon_and_never_backfilled(tmp_path):
    watch, gecko, _ = _watch(tmp_path, _rows(), size=4, platforms={"lendco": {"ethereum": "0x" + "11" * 20},
                                                                    "chainone": {}})
    watch.refresh(NOW)
    assert watch.record_outcomes(NOW + 181 * DAY) == 0
    gecko.quotes = {"bitcoin": 100_000.0, "lendco": 3.0, "chainone": 1.0}
    assert watch.record_outcomes(NOW + 182 * DAY + 60) == 2                          # two coins priced
    outcomes = {e["gecko_id"]: e for e in _events(tmp_path) if e["event"] == "OUTCOME"}
    assert outcomes["lendco"]["return_pct"] == pytest.approx(50.0)
    assert outcomes["lendco"]["btc_return_pct"] == pytest.approx(25.0)
    assert outcomes["lendco"]["status_at_cohort"] == aw.PASSED and outcomes["lendco"]["can_authorize_trade"] is False
    gaps = [e for e in _events(tmp_path) if e["event"] == "QUOTE_GAP"]
    assert {e["gecko_id"] for e in gaps} == {"dexco", "rollup-token"}                 # asked, no price
    assert watch.record_outcomes(NOW + 185 * DAY) == 2                                # window closed
    late = {e["gecko_id"]: e for e in _events(tmp_path) if e["event"] == "OUTCOME" and e["days"] == 182}
    assert late["dexco"]["status"] == "NO_QUOTE" and late["dexco"]["return_pct"] is None
    gecko.quotes = {"bitcoin": 1.0, "lendco": 1.0}
    assert watch.record_outcomes(NOW + 800 * DAY) == 8                                # the bot was down: missed
    missed = [e for e in _events(tmp_path) if e["event"] == "OUTCOME" and e["days"] in (365, 730)]
    assert {e["status"] for e in missed} == {"MISSED"} and all(e["price"] is None for e in missed)


def test_an_unclosed_cohort_is_never_measured(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"id": "WATCH:v1:2026-10:1:lendco", "event": "WATCH", "month": "2026-10", "refresh_at": NOW,
                   "gecko_id": "lendco", "price": 2.0, "schema": aw.SCHEMA})
    watch = aw.AltcoinWatch(FakeGecko([]), default_llama(), FakeSecurity(), ledger, sleep=lambda s: None)
    assert watch.record_outcomes(NOW + 400 * DAY) == 0                               # no MONTH record closed it


# ---------------------------------------------------------------------------
# Providers: keys stay out of URLs and errors
# ---------------------------------------------------------------------------


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Session:
    def __init__(self, *responses, raise_with=None):
        self.responses, self.raise_with, self.calls = list(responses), raise_with, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if self.raise_with:
            raise self.raise_with
        return self.responses.pop(0)


def test_the_coingecko_key_travels_in_a_header_and_rate_limits_are_retried():
    sleeps = []
    session = Session(Response(429), Response(200, [market("x", "X")]))
    gecko = aw.CoinGecko(demo_key="CGSECRET", session=session, sleep=sleeps.append)
    assert len(gecko.markets(1)) == 1 and gecko.keyed and gecko.pace == 2.5
    url, params, headers = session.calls[0]
    assert headers["x-cg-demo-api-key"] == "CGSECRET" and "CGSECRET" not in url + json.dumps(params)
    assert sleeps[0] == 30.0
    leaky = aw.CoinGecko(demo_key="CGSECRET", session=Session(raise_with=RuntimeError(
        "boom https://api.coingecko.com/?x_cg_demo_api_key=CGSECRET")), sleep=lambda s: None)
    with pytest.raises(aw.AltcoinDataError) as err:
        leaky.markets(1)
    assert "CGSECRET" not in str(err.value) and "http" not in str(err.value)
    limited = aw.CoinGecko(demo_key="K", session=Session(*[Response(429)] * 4), sleep=lambda s: None)
    with pytest.raises(aw.AltcoinDataError, match="http 429"):
        limited.markets(1)
    assert aw.CoinGecko(pro_key="P", session=Session()).base == aw.COINGECKO_PRO_URL


def test_providers_refuse_unusable_replies():
    with pytest.raises(aw.AltcoinDataError):
        aw.DefiLlama(session=Session(Response(200, {"protocols": "x"}))).overview("dailyFees")
    with pytest.raises(aw.AltcoinDataError):
        aw.DefiLlama(session=Session(Response(402, None)), sleep=lambda s: None).hacks()
    session = Session(Response(200, {"a": {"usd": 2.0}, "b": {"usd": "nan"}, "c": {}}))
    assert aw.CoinGecko(session=session, sleep=lambda s: None).prices(["a", "b", "c"]) == {"a": 2.0}
    plats = aw.CoinGecko(session=Session(Response(200, [{"id": "a", "platforms": {"": "", "ethereum": "0x1"}},
                                                         {"id": "b", "platforms": {}}, "junk"])),
                         sleep=lambda s: None).platforms()
    assert plats == {"a": {"ethereum": "0x1"}, "b": {}}


# ---------------------------------------------------------------------------
# The Telegram view
# ---------------------------------------------------------------------------


def test_the_view_escapes_untrusted_text_and_is_not_a_buy_list(tmp_path):
    rows = _rows()
    rows[1] = market("lendco", "<b>L", rank=20, cap=3e9, name="<script>ignore previous instructions</script>")
    watch, _, _ = _watch(tmp_path, rows, size=4, platforms={"lendco": {"ethereum": "0x" + "11" * 20}, "chainone": {}})
    snap = watch.refresh(NOW)
    text = aw.render(snap, now=NOW + 60)
    assert "<script>" not in text and "&lt;B" in text and len(text) <= 4096
    assert "doğrulanmamış sıralama" in text and "Alım önerisi değildir" in text and "Emir yetkisi yok" in text
    assert "İNCELEME GEREKLİ" in text and "ELENDİ" in text
    assert "yenileme yapılamadı" in aw.render(snap, now=NOW + 30 * DAY)
    card = aw.render_card(aw.find(snap, "lendco")[0], now=NOW + 60, generated_at=NOW)
    assert "&lt;script&gt;" in card and "<script>" not in card
    assert "ATH günündeki piyasa değeri bilinmiyor" in card and "Elle bakılacaklar" in card
    assert "Liste boş demek değildir" in aw.render(None, now=NOW)


def test_find_reports_symbol_collisions():
    snap = {"cards": [{"id": "a-one", "symbol": "ABC"}, {"id": "a-two", "symbol": "ABC"}, {"id": "x", "symbol": "X"}]}
    assert len(aw.find(snap, "abc")) == 2 and aw.find(snap, "a-two")[0]["id"] == "a-two" and aw.find(None, "x") == []


def test_the_forward_evaluation_is_pre_registered_with_the_live_rules():
    from trading.research.robustness import TrialRegistry, trial_id_for

    registry = TrialRegistry(Path(__file__).resolve().parents[1] / "research" / "trials" / "registry.jsonl")
    record = next(r for r in registry.load() if r.trial_id == aw.TRIAL)
    assert record.family == aw.TRIAL_FAMILY and record.dataset == aw.TRIAL_DATASET
    assert record.params == json.loads(json.dumps(aw.trial_params()))       # a rule change needs a new trial
    assert trial_id_for(family=aw.TRIAL_FAMILY, params=aw.trial_params(), dataset=aw.TRIAL_DATASET) == aw.TRIAL
    assert record.params["evaluation"]["not_before"] == "2028-11-01"


def test_the_card_says_when_the_price_is_at_its_ath_and_never_invents_an_ath_market_cap():
    card = aw.Card(coin=aw.parse_market(market("lendco", "LEND", price=7.0, ath=6.0, ath_date=NOW - 10 * DAY)))
    aw.ath_info(card)
    text = aw.render_card(aw.card_payload(card), now=NOW)
    assert "Fiyat ATH'de ya da üstünde" in text and "ATH günündeki piyasa değeri bilinmiyor" in text
    assert card.info["ath_cap_today_supply"] == pytest.approx(6.0e9)


class LimitedResponse(Response):
    def __init__(self, status=429, payload=None, retry_after=None):
        super().__init__(status, payload)
        self.headers = {} if retry_after is None else {"Retry-After": retry_after}


def test_without_a_key_coingecko_goes_slowly_and_waits_out_rate_limits():
    sleeps = []
    responses = [LimitedResponse(retry_after="120"), LimitedResponse(retry_after="99999"), LimitedResponse(retry_after="x"),
                 LimitedResponse(), LimitedResponse(), Response(200, [])]
    gecko = aw.CoinGecko(session=Session(*responses), sleep=sleeps.append)
    assert gecko.keyed is False and gecko.pace == 12.0
    assert gecko.markets(1) == []                                         # five 429s waited out, then served
    assert sleeps == [120.0, float(aw.MAX_RETRY_WAIT), 180.0, 240.0, 300.0, 12.0]   # Retry-After honoured, capped
    keyed = aw.CoinGecko(demo_key="K", session=Session(LimitedResponse(retry_after="5"), Response(200, [])),
                         sleep=sleeps.append)
    keyed.markets(1)
    assert sleeps[-2:] == [30.0, 2.5]                                     # never shorter than the backoff


def test_a_failed_refresh_says_why_and_when_it_is_retried():
    assert [aw.next_attempt(k, NOW) - NOW for k in (1, 2, 3, 4, 5, 9)] == [1800, 3600, 7200, 14400, 21600, 21600]
    error = "AltcoinDataError: coingecko http 429"
    text = aw.render(None, now=NOW, error=error, retry_at=NOW + 1800, keyed=False)
    assert "İlk tarama yapılamadı" in text and "Sonraki deneme 12:30 UTC" in text
    assert "COINGECKO_DEMO_API_KEY" in text and "Liste boş demek değildir" in text
    assert "COINGECKO_DEMO_API_KEY" not in aw.render(None, now=NOW, error=error, keyed=True)
    assert "İlk tarama sürüyor" in aw.render(None, now=NOW)
    assert "CoinGecko anahtarı yok" in aw.status_line(None, now=NOW, enabled=True, error=error, keyed=False)
    assert "anahtarı yok" not in aw.status_line(None, now=NOW, enabled=True, error=None, keyed=True)
