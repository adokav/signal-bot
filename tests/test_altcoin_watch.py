from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from acce_unified import altcoin_watch as aw
from acce_unified import register as rg
from acce_unified.trend_loop import Ledger

NOW = int(datetime(2026, 10, 23, 12, tzinfo=timezone.utc).timestamp())
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


def chart(*, volume: float = 2e7, price: float = 2.0, days: int = 365) -> dict:
    prices, caps, vols = [], [], []
    for k in range(days + 1):
        ts = (NOW - (days - k) * DAY) * 1000
        prices.append([ts, price])
        caps.append([ts, price * 1e9])
        vols.append([ts, volume])
    return {"prices": prices, "market_caps": caps, "total_volumes": vols}


def fee(name: str, *, did: str, d30: float = 3e6, y1: float | None = 3.6e7, monthly: float | None = 3e6,
        chain: bool = False) -> dict:
    return {"name": name, "displayName": name, "defillamaId": did, "protocolType": "chain" if chain else "protocol",
            "total30d": d30, "total1y": y1, "monthlyAverage1y": monthly}


# A DefiLlama index around the register's coins: UNI its own protocol, AAVE through a parent, BNB a chain.
LITE = {"protocols": [{"defillamaId": "u1", "geckoId": "uniswap"},
                      {"defillamaId": "a1", "geckoId": None, "parentProtocol": "parent#aave"},
                      {"defillamaId": "a2", "geckoId": None, "parentProtocol": "parent#aave"},
                      {"defillamaId": "h1", "geckoId": "hacked"}],
        "parentProtocols": [{"id": "parent#aave", "gecko_id": "aave"}]}
CHAINS = [{"name": "BSC", "gecko_id": "binancecoin", "gasTokenGeckoId": "binancecoin"},
          {"name": "Rollup", "gecko_id": "rollup-token", "gasTokenGeckoId": "ethereum"}]
PLATFORMS = {"uniswap": {"ethereum": "0x" + "11" * 20}, "aave": {"ethereum": "0x" + "22" * 20}, "binancecoin": {},
             "pepe": {"ethereum": "0x" + "33" * 20}, "bonk": {"solana": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"}}
SYMBOLS = ("UNI", "AAVE", "BNB", "PEPE", "BONK")


def _register(symbols=SYMBOLS) -> rg.Register:
    raw = json.loads(rg.DEFAULT_PATH.read_text(encoding="utf-8"))
    keep = set(symbols)
    raw["altcoins"] = [c for c in raw["altcoins"] if c["symbol"] in keep]
    raw["memes"] = [c for c in raw["memes"] if c["symbol"] in keep]
    raw["triggers"] = [t for t in raw["triggers"] if set(t["affects"]) <= keep | {"ALL", "PENGU"}]
    ids = {t["id"] for t in raw["triggers"]}
    for c in raw["altcoins"] + raw["memes"]:
        c["triggers"] = [t for t in c["triggers"] if t in ids]
    return rg.parse(raw, path=rg.DEFAULT_PATH, sha256="test")


def _rows():
    return [market("uniswap", "UNI", rank=20, cap=4.7e9, circ=624.6e6, total=1e9),          # 60 % not circulating
            market("aave", "AAVE", rank=30, cap=2.3e9, circ=15.44e6, total=16e6),            # fees zero below
            market("binancecoin", "BNB", rank=4, cap=98e9, circ=131.5e6, total=131.5e6),
            market("pepe", "PEPE", rank=40, cap=1.7e9, circ=420e12, total=420e12),
            market("bonk", "BONK", rank=80, cap=2.7e8, circ=88e12, total=88e12)]


class FakeGecko:
    keyed = False

    def __init__(self, rows, *, platforms=None, charts=None):
        self.rows, self.plats = rows, platforms if platforms is not None else dict(PLATFORMS)
        self.charts, self.chart_calls, self.id_calls = charts or {}, [], []

    def markets_by_id(self, ids):
        self.id_calls.append(list(ids))
        return [r for r in self.rows if r["id"] in ids]

    def platforms(self):
        return self.plats

    def chart(self, gecko_id):
        self.chart_calls.append(gecko_id)
        found = self.charts.get(gecko_id, chart())
        if isinstance(found, Exception):
            raise found
        return found


class FakeLlama:
    def __init__(self, *, fees=None, hacks=None, broken=None):
        self.fees, self.hack_rows, self.broken = fees if fees is not None else [], hacks or [], broken

    def lite(self):
        if self.broken == "lite":
            raise aw.AltcoinDataError("defillama http 500")
        return LITE

    def chains(self):
        return CHAINS

    def overview(self, data_type):
        assert data_type == "dailyFees"
        return self.fees

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


def default_llama(**kw):
    fees = [fee("Uniswap", did="u1", d30=4e6), fee("Aave V3", did="a1", d30=0.0), fee("Aave V2", did="a2", d30=0.0),
            fee("BSC", did="chain#bsc", chain=True, d30=5e6)]
    return FakeLlama(fees=kw.get("fees", fees), hacks=kw.get("hacks"), broken=kw.get("broken"))


def _watch(tmp_path, rows=None, *, llama=None, register=None, **gecko_kw):
    gecko = FakeGecko(rows if rows is not None else _rows(), **gecko_kw)
    security = FakeSecurity()
    watch = aw.AltcoinWatch(gecko, llama or default_llama(), security, Ledger(tmp_path / "ledger.jsonl"),
                            register=register or _register(), sleep=lambda s: None)
    return watch, gecko, security


def _events(tmp_path):
    path = tmp_path / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ---------------------------------------------------------------------------
# Parsing and identity
# ---------------------------------------------------------------------------


def test_a_market_row_needs_a_clean_id_and_a_price():
    assert aw.parse_market(market("uniswap", "UNI")).symbol == "UNI"
    assert aw.parse_market(market("Bad Id", "X")) is None
    assert aw.parse_market(market("x", "X", price=0)) is None
    assert aw.parse_market({**market("x", "X"), "market_cap_rank": True}).rank is None
    assert aw.parse_market({**market("x", "X"), "circulating_supply": "nan"}).circulating is None


def test_defillama_fees_are_summed_per_coin_through_parents_chains_and_own_ids():
    index = aw.build_llama_index(LITE, CHAINS)
    rows = [fee("Aave V3", did="a1", d30=4e6, y1=None, monthly=None), fee("Aave V2", did="a2", d30=1e6, y1=1.2e7),
            fee("BSC", did="chain#x", chain=True), fee("Nobody", did="99")]
    totals = aw.aggregate(rows, index)
    assert set(totals) == {"aave", "binancecoin"}                        # unmapped fees are dropped, never guessed
    assert totals["aave"].d30 == 5e6 and totals["aave"].entries == 2
    assert totals["aave"].y1_known is False                             # one year unknown: the year is unknown
    assert index.gas_tokens == frozenset({"binancecoin", "ethereum"})   # a rollup's governance token pays no gas


def test_hacks_count_what_was_not_returned():
    index = aw.build_llama_index(LITE, CHAINS)
    hacks = aw.hacks_by_gecko([{"date": NOW - DAY, "amount": 5e6, "returnedFunds": 4.5e6, "defillamaId": "a1",
                                "targetType": "DeFi Protocol"},
                               {"date": NOW, "amount": "x", "defillamaId": "h1"},
                               {"date": NOW, "amount": 2e6, "defillamaId": "h1", "targetType": "Chain"}], index)
    assert hacks == {"aave": [(NOW - DAY, 5e5, "defi protocol")],
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
    return aw.Card(coin=aw.parse_market(market("uniswap", "UNI", **kw)))


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


def test_economic_activity_needs_mapped_fees_and_does_not_apply_to_memes():
    for fees, status in ((None, aw.REVIEW), (aw.Totals(d30=0.0), aw.FAIL), (aw.Totals(d30=1.0), aw.PASS)):
        card = _card()
        aw.screen_activity(card, fees)
        assert card.gate(aw.ACTIVITY) == status
    meme = _card()
    aw.screen_activity(meme, None, kind=rg.MEME)
    assert meme.gate(aw.ACTIVITY) == aw.NA                                 # not a REVIEW: the report has no such gate
    for g in (aw.SUPPLY, aw.SECURITY, aw.LIQUIDITY):
        meme.add(g, aw.PASS, "ok")
    assert meme.status == aw.PASSED and meme.fails() == {}


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
    plats = {"binancecoin": {"ethereum": "0x" + "11" * 20}, "plain": {},
             "sol-first": {"solana": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", "ethereum": "0x" + "ab" * 20},
             "base-first": {"base": "0x" + "ab" * 20, "solana": "Mint"}, "tron-first": {"tron": "T1", "ethereum": "0x1"}}
    assert aw.contract_of("binancecoin", plats, index) == ("native", None, None)   # its bridged copy is not it
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


def test_liquidity_needs_a_recent_complete_month_of_volume():
    card = _card()
    aw.screen_liquidity(card, chart(volume=2e7), now=NOW)
    assert card.gate(aw.LIQUIDITY) == aw.PASS and card.info["volume_median_30d"] == 2e7
    thin = _card()
    aw.screen_liquidity(thin, chart(volume=9e6), now=NOW)
    assert thin.gate(aw.LIQUIDITY) == aw.FAIL and "9.0M" in thin.fails()[aw.LIQUIDITY]
    none = _card()
    aw.screen_liquidity(none, None, now=NOW)
    assert none.gate(aw.LIQUIDITY) == aw.REVIEW
    stale = chart(volume=5e7)
    for key in ("prices", "market_caps", "total_volumes"):
        stale[key] = [p for p in stale[key] if p[0] < (NOW - 10 * DAY) * 1000]   # the newest day is 10 days old
    card = _card()
    aw.screen_liquidity(card, stale, now=NOW)
    assert card.gate(aw.LIQUIDITY) == aw.REVIEW
    repeated = chart(volume=5e7, days=12)
    repeated["total_volumes"] = [[(NOW - 5 * DAY) * 1000 + k, 5e7] for k in range(40)]   # one day, 40 points
    card = _card()
    aw.screen_liquidity(card, repeated, now=NOW)
    assert card.gate(aw.LIQUIDITY) == aw.REVIEW


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
    assert card.status == aw.ELIMINATED and card.fails() == {aw.LIQUIDITY: "sığ"}


# ---------------------------------------------------------------------------
# The daily pass over the register
# ---------------------------------------------------------------------------


def test_a_refresh_checks_only_the_register_and_records_changes_once(tmp_path):
    watch, gecko, security = _watch(tmp_path, charts={"bonk": chart(volume=9e6)})
    snap = watch.refresh(NOW)
    assert gecko.id_calls == [["uniswap", "aave", "binancecoin", "pepe", "bonk"]]      # the register, nothing else
    by = {c["symbol"]: c for c in snap["cards"]}
    assert set(by) == set(SYMBOLS) and snap["can_authorize_trade"] is False
    assert snap["schema"] == aw.SCHEMA and snap["superseded_trial"] == "b6e0fec86714efc2"
    assert by["UNI"]["status"] == aw.NEEDS_REVIEW and by["UNI"]["gates"][aw.SUPPLY]["status"] == aw.REVIEW
    assert by["UNI"]["fails"] == {} and by["UNI"]["basket"] == rg.CORE and by["UNI"]["kind"] == rg.ALTCOIN
    assert by["AAVE"]["status"] == aw.ELIMINATED and aw.ACTIVITY in by["AAVE"]["fails"]   # zero fees
    assert by["BNB"]["status"] == aw.PASSED and by["BNB"]["info"]["contract"] == "native"
    assert by["PEPE"]["status"] == aw.PASSED and by["PEPE"]["gates"][aw.ACTIVITY]["status"] == aw.NA
    assert by["BONK"]["status"] == aw.ELIMINATED and by["BONK"]["fails"] == {aw.LIQUIDITY: "30 günlük medyan hacim $9.0M"}
    assert security.calls == [("ethereum", "0x" + "11" * 20), ("ethereum", "0x" + "22" * 20),
                              ("ethereum", "0x" + "33" * 20),
                              ("solana", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263")]   # BNB is native: no call
    assert sorted(gecko.chart_calls) == sorted(["uniswap", "aave", "binancecoin", "pepe", "bonk"])
    assert snap["counts"] == {aw.PASSED: 2, aw.NEEDS_REVIEW: 1, aw.ELIMINATED: 2}
    events = _events(tmp_path)
    assert len(events) == 5 and all(e["event"] == "GATE" and e["can_authorize_trade"] is False for e in events)
    assert all(e["register_sha256"] == "test" for e in events)
    # the same day again: nothing changed, nothing written
    watch.refresh(NOW + 3600, previous=snap)
    assert len(_events(tmp_path)) == 5
    # the next day BONK's volume recovers: one change recorded
    gecko.charts = {}
    later = watch.refresh(NOW + DAY, previous=snap)
    assert {c["symbol"]: c["status"] for c in later["cards"]}["BONK"] == aw.PASSED
    events = _events(tmp_path)
    assert len(events) == 6 and events[-1]["symbol"] == "BONK" and events[-1]["previous_status"] == aw.ELIMINATED


def test_security_calls_are_skipped_only_after_a_recent_hack(tmp_path):
    hacks = [{"date": NOW - 10 * DAY, "amount": 3e6, "defillamaId": "a1", "targetType": "DeFi Protocol"}]
    watch, gecko, security = _watch(tmp_path, llama=default_llama(hacks=hacks))
    snap = watch.refresh(NOW)
    aave = {c["symbol"]: c for c in snap["cards"]}["AAVE"]
    assert aave["status"] == aw.ELIMINATED and aw.SECURITY in aave["fails"] and "hack" in aave["fails"][aw.SECURITY]
    assert ("ethereum", "0x" + "22" * 20) not in security.calls           # no contract call after a recent hack
    assert "aave" in gecko.chart_calls                                     # liquidity still measured


def test_a_core_source_failure_fails_the_refresh_instead_of_a_hollow_snapshot(tmp_path):
    watch, _, _ = _watch(tmp_path, llama=default_llama(broken="lite"))
    with pytest.raises(aw.AltcoinDataError):
        watch.refresh(NOW)
    assert _events(tmp_path) == []


def test_a_coin_missing_on_coingecko_or_with_a_failed_chart_is_review_not_pass(tmp_path):
    rows = [r for r in _rows() if r["id"] != "pepe"]
    watch, _, _ = _watch(tmp_path, rows, charts={"uniswap": aw.AltcoinDataError("coingecko http 429")})
    snap = watch.refresh(NOW)
    by = {c["symbol"]: c for c in snap["cards"]}
    assert by["PEPE"]["status"] == aw.NEEDS_REVIEW and by["PEPE"]["price"] is None
    assert all(v["status"] == aw.REVIEW for v in by["PEPE"]["gates"].values())
    assert by["UNI"]["gates"][aw.LIQUIDITY]["status"] == aw.REVIEW and "alınamadı" in by["UNI"]["gates"][aw.LIQUIDITY]["notes"][0]
    gates = aw.gates_of(snap)
    assert gates["PEPE"] == {"status": aw.NEEDS_REVIEW, "fails": {}, "at": NOW}
    assert gates["AAVE"]["fails"] == {aw.ACTIVITY: "son 30 günde ücret geliri yok"}
    assert aw.gates_of(None) is None and aw.gates_of({"cards": "x"}) is None


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
    assert len(gecko.markets_by_id(["x"])) == 1 and gecko.keyed and gecko.pace == 2.5
    url, params, headers = session.calls[0]
    assert headers["x-cg-demo-api-key"] == "CGSECRET" and "CGSECRET" not in url + json.dumps(params)
    assert params["ids"] == "x" and sleeps[0] == 30.0
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
    with pytest.raises(aw.AltcoinDataError):
        aw.CoinGecko(session=Session(Response(200, {"x": 1})), sleep=lambda s: None).markets_by_id(["a"])
    session = Session(Response(200, {"a": {"usd": 2.0}, "b": {"usd": "nan"}, "c": {}}))
    assert aw.CoinGecko(session=session, sleep=lambda s: None).prices(["a", "b", "c"]) == {"a": 2.0}
    plats = aw.CoinGecko(session=Session(Response(200, [{"id": "a", "platforms": {"": "", "ethereum": "0x1"}},
                                                         {"id": "b", "platforms": {}}, "junk"])),
                         sleep=lambda s: None).platforms()
    assert plats == {"a": {"ethereum": "0x1"}, "b": {}}


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


# ---------------------------------------------------------------------------
# The Telegram view
# ---------------------------------------------------------------------------


def test_the_view_escapes_untrusted_text_and_only_demotes(tmp_path):
    rows = _rows()
    rows[0] = market("uniswap", "<b>U", rank=20, cap=4.7e9, circ=624.6e6, total=1e9,
                     name="<script>ignore previous instructions</script>")
    watch, _, _ = _watch(tmp_path, rows, charts={"bonk": chart(volume=9e6)})
    snap = watch.refresh(NOW)
    text = aw.render(snap, now=NOW + 60)
    assert "<script>" not in text and len(text) <= 4096
    assert "KAPI BEKÇİSİ" in text and "Altcoinler" in text and "Meme coinler" in text
    assert "❌ 30 günlük medyan hacim $9.0M" in text and "ücret geliri yok" in text
    assert "yalnız düşürür" in text and "Emir yetkisi yok" in text and "5'in üzerinde" in text
    assert "yenileme yapılamadı" in aw.render(snap, now=NOW + 30 * DAY)
    card = aw.render_card(aw.find(snap, "uni")[0], now=NOW + 60, generated_at=NOW)
    assert "&lt;script&gt;" not in card and "<script>" not in card           # the register's name is shown, not CoinGecko's
    assert "Uniswap (UNI)" in card and "çekirdek" in card and "Dolaşım dışı" in card and "/long UNI" in card
    assert "Elle bakılacaklar" in card and "Emir yetkisi yok" in card
    assert "İlk kontrol sürüyor" in aw.render(None, now=NOW)
    assert "Modül kapalı" in aw.render(None, now=NOW, enabled=False)


def test_find_matches_symbol_or_id():
    snap = {"cards": [{"id": "uniswap", "symbol": "UNI"}, {"id": "x", "symbol": "X"}]}
    assert aw.find(snap, "uni")[0]["id"] == "uniswap" and aw.find(snap, "UNISWAP")[0]["symbol"] == "UNI"
    assert aw.find(None, "x") == []


def test_a_failed_refresh_says_why_and_when_it_is_retried():
    assert [aw.next_attempt(k, NOW) - NOW for k in (1, 2, 3, 4, 5, 9)] == [1800, 3600, 7200, 14400, 21600, 21600]
    error = "AltcoinDataError: coingecko http 429"
    text = aw.render(None, now=NOW, error=error, retry_at=NOW + 1800, keyed=False)
    assert "İlk kontrol yapılamadı" in text and "Sonraki deneme 12:30 UTC" in text
    assert "COINGECKO_DEMO_API_KEY" in text
    assert "COINGECKO_DEMO_API_KEY" not in aw.render(None, now=NOW, error=error, keyed=True)
    assert "CoinGecko anahtarı yok" in aw.status_line(None, now=NOW, enabled=True, error=error, keyed=False)
    assert "anahtarı yok" not in aw.status_line(None, now=NOW, enabled=True, error=None, keyed=True)
    assert aw.status_line(None, now=NOW, enabled=False, error=None) == "Kapı bekçisi: kapalı"


def test_status_line_and_freshness(tmp_path):
    watch, _, _ = _watch(tmp_path)
    snap = watch.refresh(NOW)
    line = aw.status_line(snap, now=NOW + 3600, enabled=True, error=None)
    assert line.startswith("Kapı bekçisi: 5 coin") and "1 sa önce" in line and "anahtarı yok" in line
    assert aw.fresh(snap, now=NOW + DAY, max_age=3 * DAY) and not aw.fresh(snap, now=NOW + 4 * DAY, max_age=3 * DAY)
    assert not aw.fresh(None, now=NOW, max_age=DAY) and not aw.fresh({"generated_at": "x"}, now=NOW, max_age=DAY)
