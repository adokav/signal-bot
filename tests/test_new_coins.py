from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from acce_unified import new_coins as nc
from acce_unified.trend_loop import Ledger

NOW = int(datetime(2026, 10, 5, 12, tzinfo=timezone.utc).timestamp())
EVM = "0x" + "ab" * 20
MINT = "So1aNaMint1111111111111111111111111111111"
VAULT = "Vau1tAccount11111111111111111111111111111"
HOLDER = "Ho1derWa11et1111111111111111111111111111"


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def cmc_row(cmc_id: int, symbol: str, *, added: int = NOW - 3_600, platform: tuple | None = ("Ethereum", "ethereum"),
            address: str = EVM, tags=("memes",), price=0.001, volume=1_000_000.0, name: str | None = None) -> dict:
    return {
        "id": cmc_id, "name": name or f"Coin {symbol}", "symbol": symbol, "slug": symbol.lower(),
        "date_added": _iso(added), "tags": list(tags),
        "platform": ({"id": 1, "name": platform[0], "slug": platform[1], "symbol": "X", "token_address": address}
                     if platform else None),
        "circulating_supply": 900_000_000, "total_supply": 1_000_000_000,
        "quote": {"USD": {"price": price, "volume_24h": volume, "market_cap": 900_000.0,
                          "fully_diluted_market_cap": 1_000_000.0, "percent_change_24h": 12.0}},
    }


def coin(**kw) -> nc.NewCoin:
    return nc.parse_listing(cmc_row(1, kw.pop("symbol", "PEPE2"), **kw), now=NOW)


def clean_evm(**over) -> dict:
    entry = {
        "is_honeypot": "0", "cannot_sell_all": "0", "buy_tax": "0", "sell_tax": "0",
        "slippage_modifiable": "0", "personal_slippage_modifiable": "0", "transfer_pausable": "0",
        "is_blacklisted": "0", "trading_cooldown": "0", "is_open_source": "1", "is_mintable": "0",
        "owner_change_balance": "0", "hidden_owner": "0", "can_take_back_ownership": "0", "selfdestruct": "0",
        "is_proxy": "0", "external_call": "0", "creator_percent": "0.01", "owner_percent": "0",
        "total_supply": "1000000", "token_symbol": "PEPE2", "is_in_dex": "1",
        "dex": [{"pair": "0x" + "99" * 20}],
        "holders": [{"address": "0x" + f"{k:02x}" * 20, "balance": "20000", "percent": "0.02", "is_locked": 0,
                     "is_contract": 0, "tag": ""} for k in range(1, 11)],
        "lp_total_supply": "1000",
        "lp_holders": [{"address": "0x000000000000000000000000000000000000dead", "balance": "990", "is_locked": 0},
                       {"address": "0x" + "22" * 20, "balance": "10", "is_locked": 0}],
    }
    entry.update(over)
    return entry


def pairs(*, liquidity=250_000.0, buys=300, sells=250, age_days=3.0, chain="ethereum", address=EVM) -> list:
    return [{"chainId": chain, "baseToken": {"address": address, "symbol": "X"}, "liquidity": {"usd": liquidity},
             "pairCreatedAt": int((NOW - age_days * 86_400) * 1000), "txns": {"h24": {"buys": buys, "sells": sells}}}]


def clean_solana(**over) -> dict:
    report = {
        "token": {"mintAuthority": None, "freezeAuthority": None, "supply": 1_000_000_000_000},
        "tokenMeta": {"symbol": "DOG2", "mutable": False},
        "topHolders": [{"address": VAULT, "owner": "RaydiumAuth1111111111111111111111111111", "amount": 600_000_000_000,
                        "insider": False}]
                      + [{"address": HOLDER[:-2] + f"{k:02d}", "owner": HOLDER[:-2] + f"{k:02d}",
                          "amount": 10_000_000_000, "insider": False} for k in range(10, 19)],
        "markets": [{"pubkey": "Poo1Address1111111111111111111111111111111", "liquidityA": VAULT,
                     "lp": {"lpLocked": 9_990, "lpTotalSupply": 10_000}}],
        "risks": [], "rugged": False, "transferFee": {"pct": 0},
    }
    report.update(over)
    return report


def sol_coin() -> nc.NewCoin:
    return nc.parse_listing(cmc_row(2, "DOG2", platform=("Solana", "solana"), address=MINT), now=NOW)


# ---------------------------------------------------------------------------
# Listings and identity
# ---------------------------------------------------------------------------


def test_chains_match_exact_platform_names_and_addresses_must_be_valid():
    assert nc.chain_of({"name": "Ethereum", "slug": "ethereum"}) == "ethereum"
    assert nc.chain_of({"name": "BNB Smart Chain (BEP20)", "slug": "bnb"}) == "bsc"
    assert nc.chain_of({"name": "Optimism", "slug": "optimism-ethereum"}) == "optimism"   # not Ethereum
    assert nc.chain_of({"name": "Solana", "slug": "solana"}) == "solana"
    assert nc.chain_of({"name": "TON", "slug": "toncoin"}) is None
    assert nc.chain_of({"name": "Coinbase Wrapped", "slug": "cbw"}) is None
    assert nc.valid_address("ethereum", EVM) == EVM
    assert nc.valid_address("ethereum", "0x123") is None
    assert nc.valid_address("solana", MINT) == MINT
    assert nc.valid_address("solana", "0O-not-base58") is None


def test_a_listing_needs_an_id_and_a_past_add_date():
    assert coin().cmc_id == 1 and coin().chain == "ethereum" and coin().is_meme
    assert nc.parse_listing(cmc_row(1, "X", added=NOW + 3_600), now=NOW) is None      # from the future
    assert nc.parse_listing(cmc_row(1, "X", added=NOW + 60), now=NOW) is None         # not even a minute early
    row = cmc_row(1, "X")
    row["date_added"] = None
    assert nc.parse_listing(row, now=NOW) is None
    assert nc.parse_listing({**cmc_row(1, "X"), "id": "abc"}, now=NOW) is None
    assert nc.parse_listing("garbage", now=NOW) is None


def test_a_coin_without_a_checkable_contract_is_data_missing_never_clean():
    native = nc.parse_listing(cmc_row(3, "L1", platform=None), now=NOW)
    ton = nc.parse_listing(cmc_row(4, "TONX", platform=("TON", "toncoin")), now=NOW)
    bad = nc.parse_listing(cmc_row(5, "BAD", address="0xnope"), now=NOW)
    for c in (native, ton, bad):
        assert nc.assess(c, security=None, pairs=None, copies=1, now=NOW).verdict == nc.DATA_MISSING


# ---------------------------------------------------------------------------
# EVM screen (GoPlus + DexScreener)
# ---------------------------------------------------------------------------


def test_a_clean_evm_token_has_no_red_flag_and_every_critical_group_checked():
    card = nc.assess(coin(), security=clean_evm(), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.NO_RED_FLAG
    assert all(card.status(g) == nc.PASS for g in nc.CRITICAL)
    assert card.liquidity_usd == 250_000.0


@pytest.mark.parametrize("override, reason", [
    ({"is_honeypot": "1"}, "honeypot"),
    ({"sell_tax": "0.15"}, "satış vergisi %15"),
    ({"is_mintable": "1"}, "yeni token basılabilir"),
    ({"owner_change_balance": "1"}, "bakiyeleri değiştirebilir"),
    ({"slippage_modifiable": "1"}, "vergi sonradan artırılabilir"),
    ({"is_open_source": "0"}, "kaynak kodu doğrulanmamış"),
    ({"creator_percent": "0.25"}, "yaratıcı payı %25"),
])
def test_each_known_scam_pattern_is_a_heavy_risk(override, reason):
    card = nc.assess(coin(), security=clean_evm(**override), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK
    assert any(reason in text for text in card.reasons(nc.FAIL))


def test_holder_concentration_counts_wallets_but_not_pools_locks_or_burns():
    holders = [{"address": "0x" + "99" * 20, "balance": "400000", "is_locked": 0, "is_contract": 1, "tag": ""},
               {"address": "0x" + "33" * 20, "balance": "300000", "is_locked": 1, "is_contract": 1, "tag": "locker"},
               {"address": "0x000000000000000000000000000000000000dead", "balance": "100000", "is_locked": 0},
               {"address": "0x" + "44" * 20, "balance": "90000", "is_locked": 0, "is_contract": 1, "tag": "Pool"},
               {"address": "0x" + "55" * 20, "balance": "50000", "is_locked": 0, "is_contract": 0, "tag": ""}]
    card = nc.assess(coin(), security=clean_evm(holders=holders), pairs=pairs(), copies=1, now=NOW)
    assert card.status(nc.HOLDERS) == nc.PASS and "%5" in card.groups[nc.HOLDERS][0].text
    whales = [{"address": "0x" + f"{k:02x}" * 20, "balance": "60000", "is_locked": 0, "is_contract": 0, "tag": ""}
              for k in range(1, 11)]
    card = nc.assess(coin(), security=clean_evm(holders=whales), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK and "ilk 10 cüzdan arzın %60" in card.reasons(nc.FAIL)[0]


def test_unlocked_liquidity_is_a_rug_risk():
    lp = [{"address": "0x" + "22" * 20, "balance": "700", "is_locked": 0},
          {"address": "0x" + "23" * 20, "balance": "300", "is_locked": 1}]
    card = nc.assess(coin(), security=clean_evm(lp_holders=lp), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK and "çekilebilir" in card.reasons(nc.FAIL)[0]


@pytest.mark.parametrize("security, dex, error", [
    (None, pairs(), False),                                         # GoPlus does not know the token
    (clean_evm(sell_tax=""), pairs(), False),                       # a blank critical field
    (clean_evm(is_mintable=None), pairs(), False),
    (clean_evm(holders=None), pairs(), False),
    (clean_evm(lp_holders=[]), pairs(), False),
    (clean_evm(), None, False),                                     # DexScreener unreachable
    (clean_evm(), [], False),                                       # no DEX pool
    (clean_evm(), pairs(), True),                                   # security provider unreachable
    (clean_evm(sell_tax="7"), pairs(), False),                      # outside 0..1: not trusted
    (clean_evm(cannot_sell_all=None), pairs(), False),              # every red-flag field must be present
    (clean_evm(buy_tax=""), pairs(), False),
    (clean_evm(slippage_modifiable=None), pairs(), False),
    (clean_evm(personal_slippage_modifiable="?"), pairs(), False),
    (clean_evm(hidden_owner=None), pairs(), False),
    (clean_evm(can_take_back_ownership=""), pairs(), False),
    (clean_evm(selfdestruct=None), pairs(), False),
    (clean_evm(holders=[]), pairs(), False),                        # no holder evidence is not 0%
    (clean_evm(holders=[{"address": "0x000000000000000000000000000000000000dead", "balance": "1"}]),
     pairs(), False),                                               # nothing left to count
])
def test_missing_or_untrusted_data_is_never_a_clean_result(security, dex, error):
    card = nc.assess(coin(), security=security, pairs=dex, copies=1, now=NOW, security_error=error)
    assert card.verdict == nc.DATA_MISSING


def test_market_signals_from_dex_pairs():
    card = nc.assess(coin(), security=clean_evm(), pairs=pairs(buys=80, sells=0), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK and "hiç satış yok" in card.reasons(nc.FAIL)[0]
    card = nc.assess(coin(), security=clean_evm(), pairs=pairs(liquidity=5_000.0), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK
    card = nc.assess(coin(volume=60_000_000.0), security=clean_evm(), pairs=pairs(age_days=0.5), copies=2, now=NOW)
    assert card.verdict == nc.NO_RED_FLAG                            # warnings, not red flags
    warnings = " ".join(card.reasons(nc.WARN))
    assert "1 günden genç" in warnings and "240 katı" in warnings and "taklit" in warnings


def test_a_contract_symbol_that_differs_from_cmc_is_flagged():
    card = nc.assess(coin(), security=clean_evm(token_symbol="OTHER"), pairs=pairs(), copies=1, now=NOW)
    assert "sembol" in " ".join(card.reasons(nc.WARN))


# ---------------------------------------------------------------------------
# Solana screen (RugCheck + DexScreener)
# ---------------------------------------------------------------------------


def sol_pairs(**kw):
    return pairs(chain="solana", address=MINT, **kw)


def test_a_clean_solana_token_and_its_pool_vault_is_not_a_whale():
    card = nc.assess(sol_coin(), security=clean_solana(), pairs=sol_pairs(), copies=1, now=NOW)
    assert card.verdict == nc.NO_RED_FLAG
    assert "ilk 10 cüzdan %9" in card.groups[nc.HOLDERS][0].text      # the 60% pool vault is left out


@pytest.mark.parametrize("override, reason", [
    ({"token": {"mintAuthority": "Auth", "freezeAuthority": None, "supply": 10**12}}, "mint yetkisi açık"),
    ({"token": {"mintAuthority": None, "freezeAuthority": "Auth", "supply": 10**12}}, "freeze yetkisi açık"),
    ({"rugged": True}, "rug pull olmuş"),
    ({"markets": [{"pubkey": "P", "lp": {"lpLocked": 20, "lpTotalSupply": 100}}]}, "çekilebilir"),
    ({"risks": [{"name": "Top 10 holders high ownership", "level": "danger"}]}, "cüzdan yoğunlaşması"),
    ({"transferFee": {"pct": 25}}, "transfer ücreti %25"),
])
def test_solana_scam_patterns(override, reason):
    card = nc.assess(sol_coin(), security=clean_solana(**override), pairs=sol_pairs(), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK and any(reason in t for t in card.reasons(nc.FAIL))


def test_solana_missing_authorities_or_lp_data_is_data_missing():
    for override in ({"token": None}, {"markets": []}, {"topHolders": None}, {"topHolders": []},
                     {"token": {"mintAuthority": None, "freezeAuthority": None}}):
        card = nc.assess(sol_coin(), security=clean_solana(**override), pairs=sol_pairs(), copies=1, now=NOW)
        assert card.verdict == nc.DATA_MISSING, override
    assert nc.assess(sol_coin(), security=None, pairs=sol_pairs(), copies=1, now=NOW).verdict == nc.DATA_MISSING


# ---------------------------------------------------------------------------
# Providers: secrets stay out of errors
# ---------------------------------------------------------------------------


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code, self._payload = status, payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class Session:
    def __init__(self, response=None, raise_with=None):
        self.response, self.raise_with, self.calls = response, raise_with, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        if self.raise_with:
            raise self.raise_with
        return self.response


def test_the_cmc_key_travels_in_a_header_and_never_in_an_error():
    session = Session(Response(200, {"status": {"error_code": 0}, "data": [cmc_row(1, "X")]}))
    cmc = nc.CoinMarketCap("SECRETKEY", session=session)
    assert len(cmc.newest()) == 1
    url, params, headers = session.calls[0]
    assert headers["X-CMC_PRO_API_KEY"] == "SECRETKEY" and "SECRETKEY" not in url + json.dumps(params)
    assert params["sort"] == "date_added"
    leaky = nc.CoinMarketCap("SECRETKEY", session=Session(raise_with=RuntimeError(
        "boom https://pro-api.coinmarketcap.com/?CMC_PRO_API_KEY=SECRETKEY")))
    with pytest.raises(nc.NewCoinsDataError) as err:
        leaky.newest()
    assert "SECRETKEY" not in str(err.value) and "http" not in str(err.value)
    refused = nc.CoinMarketCap("SECRETKEY", session=Session(Response(200, {"status": {"error_code": 1002,
                                                                       "error_message": "SECRETKEY bad"}})))
    with pytest.raises(nc.NewCoinsDataError, match="cmc error 1002") as err:
        refused.newest()
    assert "SECRETKEY" not in str(err.value)
    with pytest.raises(nc.NewCoinsDataError, match="key missing"):
        nc.CoinMarketCap("").newest()


def test_providers_refuse_unusable_replies():
    providers = nc.SecurityProviders(session=Session(Response(200, {"code": 2, "message": "limit"})))
    with pytest.raises(nc.NewCoinsDataError):
        providers.goplus("ethereum", EVM)
    providers = nc.SecurityProviders(session=Session(Response(500, {})))
    with pytest.raises(nc.NewCoinsDataError, match="http 500"):
        providers.dex_pairs("ethereum", EVM)
    providers = nc.SecurityProviders(session=Session(Response(404, None)))
    assert providers.rugcheck(MINT) is None
    other = pairs(chain="bsc") + pairs() + [{"chainId": "ethereum", "baseToken": {"address": "0x" + "cd" * 20}}]
    providers = nc.SecurityProviders(session=Session(Response(200, {"pairs": other})))
    assert len(providers.dex_pairs("ethereum", EVM.upper().replace("0X", "0x"))) == 1


# ---------------------------------------------------------------------------
# Scanner and forward record
# ---------------------------------------------------------------------------


class FakeCmc:
    def __init__(self, rows):
        self.rows, self.price_calls = rows, []
        self.quotes: dict[int, float] = {}

    def newest(self):
        return self.rows

    def prices(self, ids):
        self.price_calls.append(list(ids))
        return {i: p for i, p in self.quotes.items() if i in ids}


class FakeProviders:
    def __init__(self):
        self.calls = 0
        self.security = clean_evm()

    def goplus(self, chain, address):
        self.calls += 1
        return self.security

    def rugcheck(self, mint):
        self.calls += 1
        return clean_solana()

    def dex_pairs(self, chain, address):
        return pairs(chain=chain, address=address)


def _scanner(tmp_path, rows):
    providers = FakeProviders()
    scanner = nc.NewCoinsScanner(FakeCmc(rows), providers, Ledger(tmp_path / "ledger.jsonl"), sleep=lambda s: None)
    return scanner, providers


def _events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]


def test_a_scan_screens_new_coins_once_and_records_first_sight(tmp_path):
    rows = [cmc_row(1, "AAA", address="0x" + "a1" * 20), cmc_row(2, "OLD", added=NOW - 100 * 3_600),
            cmc_row(3, "SOL", platform=("Solana", "solana"), address=MINT)]
    scanner, providers = _scanner(tmp_path, rows)
    snap = scanner.scan(NOW)
    assert snap["coins"] == 2 and snap["can_authorize_trade"] is False          # the 100-hour-old coin is out
    assert snap["counts"][nc.NO_RED_FLAG] == 2 and providers.calls == 2
    scanner.scan(NOW + 1_800)
    assert providers.calls == 2                                                   # cached for 6 hours
    events = _events(tmp_path)
    assert [e["event"] for e in events] == ["FIRST_SEEN", "FIRST_SEEN"]
    assert all(e["can_authorize_trade"] is False and e["schema"] == nc.SCHEMA for e in events)
    providers.security = clean_evm(is_honeypot="1")
    scanner.scan(NOW + 7 * 3_600)                                                 # re-checked: verdict changed
    assert [e["event"] for e in _events(tmp_path)].count("VERDICT") == 1
    again = nc.NewCoinsScanner(FakeCmc(rows), providers, Ledger(tmp_path / "ledger.jsonl"), sleep=lambda s: None)
    again.scan(NOW + 7 * 3_600)                                                   # a restart repeats nothing
    assert [e["event"] for e in _events(tmp_path)].count("FIRST_SEEN") == 2
    assert [e["event"] for e in _events(tmp_path)].count("VERDICT") == 1


def test_the_lookup_budget_leaves_the_rest_unknown_not_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(nc, "MAX_CHECKS_PER_SCAN", 1)
    rows = [cmc_row(1, "AAA", address="0x" + "a1" * 20), cmc_row(2, "BBB", address="0x" + "b2" * 20)]
    scanner, _ = _scanner(tmp_path, rows)
    snap = scanner.scan(NOW)
    assert snap["counts"] == {nc.NO_RED_FLAG: 1, nc.HEAVY_RISK: 0, nc.DATA_MISSING: 1}
    assert len(_events(tmp_path)) == 1                                            # only the assessed coin


def test_outcomes_after_7_30_90_days_and_no_quote_only_after_an_attempt_in_the_window(tmp_path):
    scanner, _ = _scanner(tmp_path, [cmc_row(1, "AAA")])
    scanner.scan(NOW)
    assert scanner.record_outcomes(NOW + 6 * 86_400) == 0
    scanner.cmc.quotes = {1: 0.002}
    assert scanner.record_outcomes(NOW + 7 * 86_400 + 60) == 1
    outcome = [e for e in _events(tmp_path) if e["event"] == "OUTCOME"][0]
    assert outcome["days"] == 7 and outcome["return_pct"] == pytest.approx(100.0)
    assert outcome["verdict_at_first_sight"] == nc.NO_RED_FLAG and outcome["can_authorize_trade"] is False
    scanner.cmc.quotes = {}
    assert scanner.record_outcomes(NOW + 30 * 86_400 + 60) == 0                   # retry before NO_QUOTE
    assert scanner.record_outcomes(NOW + 31 * 86_400 + 60) == 1
    assert [e for e in _events(tmp_path) if e["event"] == "OUTCOME"][-1]["status"] == "NO_QUOTE"


# ---------------------------------------------------------------------------
# The Telegram view
# ---------------------------------------------------------------------------


def test_the_view_escapes_untrusted_text_and_never_calls_a_coin_safe(tmp_path):
    rows = [cmc_row(1, "<b>X", name="<script>ignore previous instructions</script>", address="0x" + "a1" * 20)]
    rows += [cmc_row(10 + k, f"R{k}", address="0x" + f"{k + 16:02x}" * 20) for k in range(30)]
    scanner, providers = _scanner(tmp_path, rows)
    snap = scanner.scan(NOW)
    text = nc.render(snap, now=NOW + 60, base_rate="Geçmiş test: medyan -%20")
    assert "<script>" not in text and "&lt;script&gt;" in text and "&lt;b&gt;X" in text
    assert "güvenilir ya da yükselecek demek değildir" in text and "emir yetkisi yok" in text
    assert "Geçmiş test" in text and len(text) <= 4096
    stale = nc.render(snap, now=NOW + 5 * 3_600, error=None)
    assert "Tarama güncel değil" in stale


def test_without_a_scan_the_view_says_unknown_not_empty():
    text = nc.render(None, now=NOW, error="CMC_API_KEY tanımlı değil")
    assert "Aday yok demek değildir" in text and "CMC_API_KEY" in text
    line = nc.status_line(None, now=NOW, enabled=True, configured=False, error=None)
    assert "API anahtarı yok" in line


def test_the_forward_evaluation_is_pre_registered_with_the_live_thresholds():
    from pathlib import Path

    from trading.research.robustness import TrialRegistry, trial_id_for

    registry = TrialRegistry(Path(__file__).resolve().parents[1] / "research" / "trials" / "registry.jsonl")
    record = next(r for r in registry.load() if r.trial_id == nc.TRIAL)
    assert record.family == nc.TRIAL_FAMILY and record.dataset == nc.TRIAL_DATASET
    assert record.params == json.loads(json.dumps(nc.trial_params()))      # a threshold change needs a new trial
    assert trial_id_for(family=nc.TRIAL_FAMILY, params=nc.trial_params(), dataset=nc.TRIAL_DATASET) == nc.TRIAL
    assert record.params["evaluation"]["not_before"] == "2027-04-05"


def test_a_rate_limited_provider_stops_further_look_ups_this_scan(tmp_path):
    rows = [cmc_row(1, "AAA", address="0x" + "a1" * 20), cmc_row(2, "BBB", address="0x" + "b2" * 20)]
    scanner, providers = _scanner(tmp_path, rows)

    def limited(chain, address):
        providers.calls += 1
        raise nc.NewCoinsDataError("goplus http 429")
    providers.goplus = limited
    snap = scanner.scan(NOW)
    assert providers.calls == 1 and snap["counts"][nc.DATA_MISSING] == 2
    providers.goplus = lambda chain, address: clean_evm()
    assert scanner.scan(NOW + 60)["counts"][nc.NO_RED_FLAG] == 2              # failures were not cached


def test_missed_horizons_are_never_backfilled_with_a_later_price(tmp_path):
    scanner, _ = _scanner(tmp_path, [cmc_row(1, "AAA")])
    scanner.scan(NOW)
    scanner.cmc.quotes = {1: 0.5}
    assert scanner.record_outcomes(NOW + 31 * 86_400 + 60) == 2              # the bot was down for a month
    outcomes = {e["days"]: e for e in _events(tmp_path) if e["event"] == "OUTCOME"}
    assert outcomes[7]["status"] == outcomes[30]["status"] == "MISSED"
    assert outcomes[7]["price"] is None and outcomes[30]["return_pct"] is None
    assert scanner.cmc.price_calls == []                                      # no price was even asked for
    assert scanner.record_outcomes(NOW + 90 * 86_400 + 3_600) == 1
    assert {e["days"]: e for e in _events(tmp_path) if e["event"] == "OUTCOME"}[90]["status"] == "OK"
