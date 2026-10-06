from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "new_coins"
POOL_AUTHORITY = "Poo1Authority111111111111111111111111111"


def fixture(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def clean_goplus(**over) -> dict:
    """A GoPlus reply shaped like the live ones: taxes blank, cannot_sell_all absent."""

    entry = {
        "is_honeypot": "0", "buy_tax": "", "sell_tax": "", "cannot_buy": "0",
        "slippage_modifiable": "0", "personal_slippage_modifiable": "0", "transfer_pausable": "0",
        "is_blacklisted": "0", "trading_cooldown": "0", "is_open_source": "1", "is_mintable": "0",
        "owner_change_balance": "0", "hidden_owner": "0", "can_take_back_ownership": "0", "selfdestruct": "0",
        "is_proxy": "0", "external_call": "0", "creator_percent": "0.01", "owner_percent": "0",
        "total_supply": "1000000", "token_symbol": "PEPE2", "is_in_dex": "1",
        "dex": [{"liquidity_type": "UniV2", "pair": "0x" + "99" * 20}],
        "holders": [{"address": "0x" + f"{k:02x}" * 20, "balance": "20000", "percent": "0.02", "is_locked": 0,
                     "is_contract": 0, "tag": ""} for k in range(1, 11)],
        "lp_total_supply": "1000",
        "lp_holders": [{"address": "0x000000000000000000000000000000000000dead", "balance": "990", "is_locked": 0,
                        "is_contract": 0},
                       {"address": "0x" + "22" * 20, "balance": "10", "is_locked": 0, "is_contract": 0}],
    }
    entry.update(over)
    return entry


def clean_sim(**over) -> dict:
    """A honeypot.is reply shaped like the live ones (taxes in percent)."""

    sim = {"simulationSuccess": True, "honeypotResult": {"isHoneypot": False},
           "simulationResult": {"buyTax": 0, "sellTax": 0, "transferTax": 0}}
    sim.update(over)
    return sim


def evm(goplus="clean", sim="clean") -> dict:
    return {"goplus": clean_goplus() if goplus == "clean" else goplus,
            "honeypot": clean_sim() if sim == "clean" else sim}


def pairs(*, liquidity=250_000.0, buys=300, sells=250, age_days=3.0, chain="ethereum", address=EVM) -> list:
    return [{"chainId": chain, "baseToken": {"address": address, "symbol": "X"}, "liquidity": {"usd": liquidity},
             "pairCreatedAt": int((NOW - age_days * 86_400) * 1000), "txns": {"h24": {"buys": buys, "sells": sells}}}]


def clean_solana(**over) -> dict:
    """A RugCheck report shaped like the live ones (knownAccounts tag the pool, LP mint on the market)."""

    report = {
        "token": {"mintAuthority": None, "freezeAuthority": None, "supply": 1_000_000_000_000},
        "tokenMeta": {"symbol": "DOG2", "mutable": False},
        "topHolders": [{"address": VAULT, "owner": POOL_AUTHORITY, "amount": 600_000_000_000, "insider": False}]
                      + [{"address": HOLDER[:-2] + f"{k:02d}", "owner": HOLDER[:-2] + f"{k:02d}",
                          "amount": 10_000_000_000, "insider": False} for k in range(10, 19)],
        "knownAccounts": {POOL_AUTHORITY: {"name": "Raydium Authority", "type": "AMM"}},
        "markets": [{"pubkey": "Poo1Address1111111111111111111111111111111", "marketType": "raydium",
                     "lp": {"lpMint": "LpMint11111111111111111111111111111111111", "lpLocked": 9_990,
                            "lpTotalSupply": 10_000, "quoteUSD": 60_000.0, "baseUSD": 60_000.0}}],
        "creatorBalance": 0, "totalMarketLiquidity": 120_000.0,
        "risks": [], "rugged": False, "transferFee": {"pct": 0},
    }
    report.update(over)
    return report


def sol_coin(symbol: str = "DOG2", address: str = MINT) -> nc.NewCoin:
    return nc.parse_listing(cmc_row(2, symbol, platform=("Solana", "solana"), address=address), now=NOW)


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
# Live provider replies (trimmed, 2026-10-05): established coins are not red-flagged, scams are
# ---------------------------------------------------------------------------


def _live(symbol, chain, address, security, dex):
    platform = {"ethereum": ("Ethereum", "ethereum"), "base": ("Base", "base"), "bsc": ("BNB Smart Chain (BEP20)", "bnb"),
                "solana": ("Solana", "solana")}[chain]
    c = nc.parse_listing(cmc_row(9, symbol, platform=platform, address=address), now=NOW)
    return nc.assess(c, security=security, pairs=nc_pairs(dex, chain, address), copies=1, now=NOW)


def nc_pairs(payload, chain, address):
    dex_chain = next(d for key, _g, d, _a in nc.CHAINS if key == chain)
    same = (lambda a: a.lower() == address.lower()) if chain != "solana" else (lambda a: a == address)
    return [p for p in (payload or {}).get("pairs") or [] if p.get("chainId") == dex_chain
            and same(p["baseToken"]["address"])]


def _goplus(name):
    return next(iter(fixture(name)["result"].values()))


def test_live_pepe_and_brett_are_not_red_flagged_but_carry_their_warnings():
    pepe = _live("PEPE", "ethereum", "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                 {"goplus": _goplus("pepe_goplus"), "honeypot": fixture("pepe_honeypot")}, fixture("pepe_dex"))
    assert pepe.verdict == nc.NO_RED_FLAG and not pepe.reasons(nc.FAIL)
    assert pepe.status(nc.SELLABILITY) == nc.WARN                    # blacklist and pause powers exist
    assert pepe.status(nc.LIQUIDITY) == nc.WARN                      # LP sits in a contract: not verifiable
    brett = _live("BRETT", "base", "0x532f27101965dd16442E59d40670FaF5eBB142E4",
                  {"goplus": _goplus("brett_goplus"), "honeypot": fixture("brett_honeypot")}, fixture("brett_dex"))
    assert brett.verdict == nc.NO_RED_FLAG and brett.status(nc.SELLABILITY) == nc.PASS


def test_live_unlaunched_token_held_by_one_wallet_is_a_heavy_risk():
    card = _live("X", "bsc", "0x8819581bD88352BF4F9cD377B3BddeE35c46f2cc",
                 {"goplus": _goplus("bsc_unlaunched_goplus"), "honeypot": None}, {"pairs": []})
    assert card.verdict == nc.HEAVY_RISK and "%100" in card.reasons(nc.FAIL)[0]
    assert card.status(nc.SELLABILITY) == nc.UNKNOWN                 # no pool: nothing to simulate


def test_live_bonk_is_not_red_flagged_and_mint_freeze_authority_is():
    bonk = _live("Bonk", "solana", "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", fixture("bonk_rugcheck"),
                 fixture("bonk_dex"))
    assert bonk.verdict == nc.NO_RED_FLAG and not bonk.reasons(nc.FAIL)
    scam = fixture("mint_freeze_rugcheck")
    card = _live("MPN", "solana", scam["mint"], scam, {"pairs": []})
    assert card.verdict == nc.HEAVY_RISK
    assert {"cüzdan dondurulabilir (freeze yetkisi açık)", "yeni token basılabilir (mint yetkisi açık)"} <= set(
        card.reasons(nc.FAIL))


# ---------------------------------------------------------------------------
# EVM screen (GoPlus + honeypot.is simulation + DexScreener)
# ---------------------------------------------------------------------------


def test_a_clean_evm_token_has_no_red_flag_and_every_critical_group_checked():
    card = nc.assess(coin(), security=evm(), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.NO_RED_FLAG
    assert all(card.status(g) == nc.PASS for g in nc.CRITICAL)
    assert card.liquidity_usd == 250_000.0


@pytest.mark.parametrize("security, reason", [
    (evm(sim=clean_sim(honeypotResult={"isHoneypot": True})), "satış simülasyonu başarısız"),
    (evm(sim=clean_sim(simulationResult={"buyTax": 0, "sellTax": 15, "transferTax": 0})), "satış vergisi %15"),
    (evm(goplus=clean_goplus(is_honeypot="1")), "GoPlus: honeypot"),
    (evm(goplus=clean_goplus(is_mintable="1")), "yeni token basılabilir"),
    (evm(goplus=clean_goplus(owner_change_balance="1")), "bakiyeleri değiştirebilir"),
    (evm(goplus=clean_goplus(slippage_modifiable="1")), "vergi sonradan artırılabilir"),
    (evm(goplus=clean_goplus(is_open_source="0")), "kaynak kodu doğrulanmamış"),
    (evm(goplus=clean_goplus(creator_percent="0.25")), "yaratıcı payı %25"),
])
def test_each_known_scam_pattern_is_a_heavy_risk(security, reason):
    card = nc.assess(coin(), security=security, pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK
    assert any(reason in text for text in card.reasons(nc.FAIL))


def test_holder_concentration_counts_wallets_but_not_pools_locks_or_burns():
    holders = [{"address": "0x" + "99" * 20, "balance": "400000", "is_locked": 0, "is_contract": 1, "tag": ""},
               {"address": "0x" + "33" * 20, "balance": "300000", "is_locked": 1, "is_contract": 1, "tag": "locker"},
               {"address": "0x000000000000000000000000000000000000dead", "balance": "100000", "is_locked": 0},
               {"address": "0x" + "44" * 20, "balance": "90000", "is_locked": 0, "is_contract": 1, "tag": "Pool"},
               {"address": "0x" + "55" * 20, "balance": "50000", "is_locked": 0, "is_contract": 0, "tag": ""}]
    card = nc.assess(coin(), security=evm(goplus=clean_goplus(holders=holders)), pairs=pairs(), copies=1, now=NOW)
    assert card.status(nc.HOLDERS) == nc.PASS and "%5" in card.groups[nc.HOLDERS][0].text
    whales = [{"address": "0x" + f"{k:02x}" * 20, "balance": "60000", "is_locked": 0, "is_contract": 0, "tag": ""}
              for k in range(1, 11)]
    card = nc.assess(coin(), security=evm(goplus=clean_goplus(holders=whales)), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK and "ilk 10 cüzdan arzın %60" in card.reasons(nc.FAIL)[0]


def test_lp_in_wallets_is_a_rug_risk_lp_in_contracts_only_a_warning():
    wallets = [{"address": "0x" + "22" * 20, "balance": "700", "is_locked": 0, "is_contract": 0},
               {"address": "0x" + "23" * 20, "balance": "300", "is_locked": 1, "is_contract": 1}]
    card = nc.assess(coin(), security=evm(goplus=clean_goplus(lp_holders=wallets)), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK and "cüzdanlarda; çekilebilir" in card.reasons(nc.FAIL)[0]
    contract = [{"address": "0x" + "24" * 20, "balance": "990", "is_locked": 0, "is_contract": 1}]
    card = nc.assess(coin(), security=evm(goplus=clean_goplus(lp_holders=contract)), pairs=pairs(), copies=1, now=NOW)
    assert card.status(nc.LIQUIDITY) == nc.WARN and card.verdict == nc.NO_RED_FLAG
    assert "kilit doğrulanamadı" in " ".join(card.reasons(nc.WARN))


@pytest.mark.parametrize("security, dex, error", [
    (evm(goplus=None), pairs(), False),                             # GoPlus does not know the token
    (evm(sim=None), pairs(), False),                                # no pool to simulate (404)
    (evm(sim=clean_sim(simulationSuccess=False)), pairs(), False),  # the simulation itself failed
    (evm(sim=clean_sim(honeypotResult={})), pairs(), False),
    (evm(sim=clean_sim(simulationResult={"buyTax": 0, "sellTax": 0})), pairs(), False),
    (evm(sim=clean_sim(simulationResult={"buyTax": "abc", "sellTax": 0, "transferTax": 0})), pairs(), False),
    (evm(sim=clean_sim(simulationResult={"buyTax": 150, "sellTax": 0, "transferTax": 0})), pairs(), False),
    (evm(goplus=clean_goplus(is_honeypot=None)), pairs(), False),   # every red-flag field must be present
    (evm(goplus=clean_goplus(slippage_modifiable=None)), pairs(), False),
    (evm(goplus=clean_goplus(personal_slippage_modifiable="?")), pairs(), False),
    (evm(goplus=clean_goplus(is_mintable=None)), pairs(), False),
    (evm(goplus=clean_goplus(hidden_owner=None)), pairs(), False),
    (evm(goplus=clean_goplus(can_take_back_ownership="")), pairs(), False),
    (evm(goplus=clean_goplus(selfdestruct=None)), pairs(), False),
    (evm(goplus=clean_goplus(holders=None)), pairs(), False),
    (evm(goplus=clean_goplus(holders=[])), pairs(), False),         # no holder evidence is not 0%
    (evm(goplus=clean_goplus(holders=[{"address": "0x000000000000000000000000000000000000dead", "balance": "1"}])),
     pairs(), False),                                               # nothing left to count
    (evm(goplus=clean_goplus(lp_holders=[])), pairs(), False),
    (evm(goplus=clean_goplus(is_in_dex="0")), pairs(), False),
    (evm(), None, False),                                           # DexScreener unreachable
    (evm(), [], False),                                             # no DEX pool
    (evm(), pairs(), True),                                         # security provider unreachable
])
def test_missing_or_untrusted_data_is_never_a_clean_result(security, dex, error):
    card = nc.assess(coin(), security=security, pairs=dex, copies=1, now=NOW, security_error=error)
    assert card.verdict == nc.DATA_MISSING


def test_market_signals_from_dex_pairs():
    card = nc.assess(coin(), security=evm(), pairs=pairs(buys=80, sells=0), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK and "hiç satış yok" in card.reasons(nc.FAIL)[0]
    card = nc.assess(coin(), security=evm(), pairs=pairs(liquidity=5_000.0), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK
    card = nc.assess(coin(volume=60_000_000.0), security=evm(), pairs=pairs(age_days=0.5), copies=2, now=NOW)
    assert card.verdict == nc.NO_RED_FLAG                            # warnings, not red flags
    warnings = " ".join(card.reasons(nc.WARN))
    assert "1 günden genç" in warnings and "240 katı" in warnings and "taklit" in warnings


def test_a_contract_symbol_that_differs_from_cmc_is_flagged():
    card = nc.assess(coin(), security=evm(goplus=clean_goplus(token_symbol="OTHER")), pairs=pairs(), copies=1, now=NOW)
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
    ({"risks": [{"name": "Large Amount of LP Unlocked", "level": "danger"}]}, "kilitsiz"),
    ({"risks": [{"name": "Single holder ownership", "level": "danger"}]}, "yoğunlaşması"),
    ({"risks": [{"name": "Low Liquidity", "level": "danger"}]}, "düşük likidite"),
    ({"transferFee": {"pct": 25}}, "transfer ücreti %25"),
    ({"creatorBalance": 250_000_000_000}, "yaratıcı payı %25"),
])
def test_solana_scam_patterns(override, reason):
    card = nc.assess(sol_coin(), security=clean_solana(**override), pairs=sol_pairs(), copies=1, now=NOW)
    assert card.verdict == nc.HEAVY_RISK and any(reason in t for t in card.reasons(nc.FAIL))


def test_solana_lp_lock_is_weighted_by_liquidity_and_never_a_red_flag_on_its_own():
    markets = [{"marketType": "raydium", "lp": {"lpMint": "LpA1111111111111111111111111111111111111111",
                                                "lpLocked": 20, "lpTotalSupply": 100, "quoteUSD": 50.0, "baseUSD": 50.0}},
               {"marketType": "orca", "lp": {"lpMint": nc.SYSTEM_PROGRAM, "lpLocked": 0, "lpTotalSupply": 999,
                                             "quoteUSD": 900_000.0, "baseUSD": 900_000.0}}]
    card = nc.assess(sol_coin(), security=clean_solana(markets=markets), pairs=sol_pairs(), copies=1, now=NOW)
    assert card.status(nc.LIQUIDITY) == nc.WARN and card.verdict == nc.NO_RED_FLAG   # orca has no LP to lock
    curve = [{"marketType": "pump_fun", "lp": {"lpMint": nc.SYSTEM_PROGRAM, "lpLocked": 1, "lpTotalSupply": 1,
                                               "quoteUSD": 30_000.0, "baseUSD": 30_000.0}}]
    no_dex_liquidity = [{**sol_pairs()[0], "liquidity": None}]
    card = nc.assess(sol_coin(), security=clean_solana(markets=curve, totalMarketLiquidity=60_000.0),
                     pairs=no_dex_liquidity, copies=1, now=NOW)
    assert card.verdict == nc.NO_RED_FLAG and card.liquidity_usd == 60_000.0       # RugCheck's figure as fallback


def test_unmeasurable_lp_data_is_unknown_once_it_is_material():
    good = {"marketType": "raydium", "lp": {"lpMint": "LpA1111111111111111111111111111111111111111", "lpLocked": 99,
                                            "lpTotalSupply": 100, "quoteUSD": 5_000.0, "baseUSD": 5_000.0}}
    broken = {"marketType": "raydium", "lp": {"lpMint": "LpB1111111111111111111111111111111111111111", "lpLocked": None,
                                              "lpTotalSupply": 100, "quoteUSD": 500_000.0, "baseUSD": 500_000.0}}
    card = nc.assess(sol_coin(), security=clean_solana(markets=[good, broken]), pairs=sol_pairs(), copies=1, now=NOW)
    assert card.verdict == nc.DATA_MISSING                                   # the broken pool holds most liquidity
    dust = {**broken, "lp": {**broken["lp"], "quoteUSD": 1.0, "baseUSD": 1.0}}
    card = nc.assess(sol_coin(), security=clean_solana(markets=[good, dust]), pairs=sol_pairs(), copies=1, now=NOW)
    assert card.verdict == nc.NO_RED_FLAG                                    # a dust pool cannot hide a rug
    no_size = {**good, "lp": {**good["lp"], "quoteUSD": None}}
    card = nc.assess(sol_coin(), security=clean_solana(markets=[good, no_size]), pairs=sol_pairs(), copies=1, now=NOW)
    assert card.verdict == nc.DATA_MISSING
    lp = [{"address": "0x000000000000000000000000000000000000dead", "balance": "900", "is_locked": 0},
          {"address": "0x" + "25" * 20, "balance": "100", "is_locked": 0}]               # is_contract missing
    card = nc.assess(coin(), security=evm(goplus=clean_goplus(lp_holders=lp)), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.DATA_MISSING
    lp[1]["balance"], lp[0]["balance"] = "5", "995"
    card = nc.assess(coin(), security=evm(goplus=clean_goplus(lp_holders=lp)), pairs=pairs(), copies=1, now=NOW)
    assert card.verdict == nc.NO_RED_FLAG                                    # 0.5% unclassified is immaterial


def test_solana_missing_authorities_or_lp_data_is_data_missing():
    only_concentrated = [{"marketType": "meteoraDlmm", "lp": {"lpMint": nc.SYSTEM_PROGRAM, "lpLocked": 0,
                                                              "lpTotalSupply": 0, "quoteUSD": 1.0, "baseUSD": 1.0}}]
    for override in ({"token": None}, {"markets": []}, {"markets": only_concentrated}, {"topHolders": None},
                     {"topHolders": []}, {"token": {"mintAuthority": None, "freezeAuthority": None}},
                     {"creatorBalance": None}, {"creatorBalance": "unknown"}):
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
    assert providers.honeypot("base", EVM) is None                     # no pair to simulate
    session = Session(Response(200, clean_sim()))
    assert nc.SecurityProviders(session=session).honeypot("base", EVM)["simulationSuccess"] is True
    assert session.calls[0][1] == {"address": EVM, "chainID": "8453"}
    other = pairs(chain="bsc") + pairs() + [{"chainId": "ethereum", "baseToken": {"address": "0x" + "cd" * 20}}]
    providers = nc.SecurityProviders(session=Session(Response(200, {"pairs": other})))
    assert len(providers.dex_pairs("ethereum", EVM.upper().replace("0X", "0x"))) == 1


# ---------------------------------------------------------------------------
# Scanner and forward record
# ---------------------------------------------------------------------------


PEPE_ADDRESS = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


def reference_rows(n: int = 450) -> list:
    """CMC's market-cap listing as the scanner asks for it: PEPE at rank 30 with its Ethereum contract."""

    rows = [{"id": 50_000 + k, "name": f"Established {k}", "symbol": f"EST{k}", "cmc_rank": k + 1, "platform": None}
            for k in range(n)]
    rows[29] = {"id": 24478, "name": "Pepe", "symbol": "PEPE", "cmc_rank": 30,
                "platform": {"name": "Ethereum", "slug": "ethereum", "token_address": PEPE_ADDRESS}}
    return rows


LINKS = {"website": ["https://example.org"], "twitter": ["https://x.com/example"], "chat": [], "reddit": []}


class FakeCmc:
    def __init__(self, rows):
        self.rows, self.price_calls = rows, []
        self.quotes: dict[int, float] = {}
        self.reference_calls, self.info_calls = 0, []
        self.reference: list | Exception = reference_rows()
        self.links: dict | Exception = {}

    def newest(self):
        return self.rows

    def established(self):
        self.reference_calls += 1
        if isinstance(self.reference, Exception):
            raise self.reference
        return self.reference

    def info(self, ids):
        self.info_calls.append(list(ids))
        if isinstance(self.links, Exception):
            raise self.links
        return {i: self.links.get(i, LINKS) for i in ids}

    def prices(self, ids):
        self.price_calls.append(list(ids))
        return {i: p for i, p in self.quotes.items() if i in ids}


class FakeProviders:
    def __init__(self):
        self.calls = 0
        self.security = clean_goplus()

    def goplus(self, chain, address):
        self.calls += 1
        return self.security

    def honeypot(self, chain, address):
        return clean_sim()

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
    providers.security = clean_goplus(is_honeypot="1")
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
    providers.goplus = lambda chain, address: clean_goplus()
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


def test_records_from_the_superseded_v1_screen_never_shadow_v2(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append({"id": "FIRST_SEEN:1", "event": "FIRST_SEEN", "cmc_id": 1, "schema": "new-coins/v1",
                   "verdict": nc.DATA_MISSING, "at": NOW - 86_400, "price": 0.001})
    scanner = nc.NewCoinsScanner(FakeCmc([cmc_row(1, "AAA")]), FakeProviders(), ledger, sleep=lambda s: None)
    scanner.scan(NOW)
    first = [e for e in _events(tmp_path) if e["event"] == "FIRST_SEEN"]
    assert [e["id"] for e in first] == ["FIRST_SEEN:1", "FIRST_SEEN:v2:1"]
    assert first[-1]["schema"] == nc.SCHEMA and first[-1]["verdict"] == nc.NO_RED_FLAG
    scanner.cmc.quotes = {1: 0.002}
    assert scanner.record_outcomes(NOW + 6 * 86_400) == 0                     # v1's earlier sight is ignored


# ---------------------------------------------------------------------------
# Identity and provenance: lookalikes and project links (WARN at most)
# ---------------------------------------------------------------------------


def test_the_reference_indexes_symbol_name_and_contract_and_refuses_a_short_list():
    rows = reference_rows() + ["junk", {"id": "x"}, {"id": 7, "symbol": " $wif ", "name": "dog wif hat!"}]
    ref = nc.build_reference(rows, now=NOW)
    assert ref.own("ethereum", PEPE_ADDRESS.lower()) == nc.Established(24478, 30)        # EVM case-insensitive
    assert ref.own("base", PEPE_ADDRESS) is None                                          # same address, other chain
    assert ref.lookalike("pepe", "Something", exclude=1) == (nc.Established(24478, 30), "sembol")
    assert ref.lookalike("$WIF", "x", exclude=1)[0].cmc_id == 7                           # "$WIF" is "WIF"
    assert ref.lookalike("ZZZ", "Dog-Wif-Hat", exclude=1) == (nc.Established(7, 453), "ad")
    assert ref.lookalike("PEPE", "Pepe", exclude=24478) is None                           # never the coin itself
    with pytest.raises(nc.NewCoinsDataError, match="too short"):
        nc.build_reference(reference_rows(100), now=NOW)                                  # a cut list is not used


def _verdict_cards():
    clean = nc.assess(coin(), security=evm(), pairs=pairs(), copies=1, now=NOW)
    heavy = nc.assess(coin(), security=evm(goplus=clean_goplus(is_mintable="1")), pairs=pairs(), copies=1, now=NOW)
    unknown = nc.assess(coin(), security=None, pairs=None, copies=1, now=NOW, security_error=True)
    return clean, heavy, unknown


@pytest.mark.parametrize("reference,urls", [
    (None, None), ("ref", None), ("ref", {}), ("ref", {"website": [], "twitter": []}), ("ref", LINKS)])
def test_identity_checks_add_warnings_but_never_move_a_coin_between_verdicts(reference, urls):
    ref = nc.build_reference(reference_rows(), now=NOW) if reference else None
    for card in _verdict_cards():
        before = card.verdict
        counts = {g: len(card.groups.get(g, [])) for g in nc.GROUPS}
        lookalike = nc.assess(nc.parse_listing(cmc_row(1, "PEPE"), now=NOW), security=evm(), pairs=pairs(),
                              copies=1, now=NOW)
        for target in (card, lookalike):
            n = len(target.groups.get(nc.IDENTITY, []))
            nc.screen_identity(target, ref, urls=urls)
            assert {f.status for f in target.groups[nc.IDENTITY][n:]} <= {nc.WARN, nc.PASS}
        assert card.verdict == before and lookalike.verdict == nc.NO_RED_FLAG
        assert all(len(card.groups.get(g, [])) == counts[g] for g in nc.GROUPS if g != nc.IDENTITY)


def test_identity_warnings_say_what_could_not_be_checked():
    ref = nc.build_reference(reference_rows(), now=NOW)

    def warnings(symbol="PEPE2", reference=ref, urls=LINKS):
        card = nc.assess(nc.parse_listing(cmc_row(1, symbol), now=NOW), security=evm(), pairs=pairs(), copies=1,
                         now=NOW)
        nc.screen_identity(card, reference, urls=urls)
        return [f.text for f in card.groups[nc.IDENTITY] if f.status == nc.WARN and "kontrattaki" not in f.text]

    assert warnings() == []
    assert warnings("PEPE") == ["CMC #30 ile aynı sembol: taklit ya da köprü olabilir"]
    assert warnings(reference=None) == ["taklit kontrolü yapılamadı (CMC listesi yok)"]
    assert warnings(urls=None) == ["proje bilgisi okunamadı (CMC)"]                       # unknown, not "no links"
    assert warnings(urls={"twitter": ["https://x.com/a"]}) == ["proje bilgisi okunamadı (CMC)"]
    assert warnings(urls={"website": [""], "twitter": [], "chat": []}) == ["CMC'de web sitesi yok",
                                                                           "CMC'de sosyal hesap yok"]


def test_the_scan_refreshes_the_reference_daily_and_fetches_links_once_per_coin(tmp_path):
    rows = [cmc_row(1, "PEPE", address="0x" + "a1" * 20), cmc_row(2, "BBB", address="0x" + "b2" * 20)]
    scanner, _ = _scanner(tmp_path, rows)
    scanner.cmc.links = {2: {"website": [], "twitter": []}}
    snap = scanner.scan(NOW)
    assert scanner.cmc.reference_calls == 1 and scanner.cmc.info_calls == [[1, 2]]
    assert snap["counts"][nc.NO_RED_FLAG] == 2                                            # warnings only
    first = {e["cmc_id"]: e for e in _events(tmp_path) if e["event"] == "FIRST_SEEN"}
    assert "CMC #30 ile aynı sembol: taklit ya da köprü olabilir" in first[1]["warnings"]
    assert "CMC'de web sitesi yok" in first[2]["warnings"] and first[2]["verdict"] == nc.NO_RED_FLAG
    scanner.scan(NOW + 3_600)
    assert scanner.cmc.reference_calls == 1 and len(scanner.cmc.info_calls) == 1          # cached
    scanner.cmc.reference = nc.NewCoinsDataError("cmc http 500")
    scanner.scan(NOW + 86_400)                                                            # refresh fails: kept
    assert scanner.cmc.reference_calls == 2 and scanner.current_reference(NOW + 86_400) is not None
    scanner.scan(NOW + 86_400 + 1_800)
    assert scanner.cmc.reference_calls == 2                                               # retried after an hour
    assert scanner.current_reference(NOW + 2 * 86_400 + 1) is None                       # too old: not used


def test_a_failed_links_call_is_unknown_and_retried(tmp_path):
    scanner, _ = _scanner(tmp_path, [cmc_row(1, "AAA")])
    scanner.cmc.links = nc.NewCoinsDataError("cmc http 429")
    scanner.scan(NOW)
    first = [e for e in _events(tmp_path) if e["event"] == "FIRST_SEEN"][0]
    assert "proje bilgisi okunamadı (CMC)" in first["warnings"] and first["verdict"] == nc.NO_RED_FLAG
    scanner.cmc.links = {}
    scanner.scan(NOW + 1_800)
    assert len(scanner.cmc.info_calls) == 2


def test_cmc_reference_and_links_requests():
    session = Session(Response(200, {"status": {"error_code": 0}, "data": reference_rows()}))
    assert len(nc.CoinMarketCap("K", session=session).established()) == 450
    assert session.calls[0][1]["sort"] == "market_cap" and session.calls[0][1]["limit"] == nc.REFERENCE_SIZE
    session = Session(Response(200, {"status": {"error_code": 0},
                                     "data": {"1": {"id": 1, "urls": LINKS}, "2": {"id": 2}, "x": {}}}))
    assert nc.CoinMarketCap("K", session=session).info([1, 2, 3]) == {1: LINKS, 2: None, 3: None}
    assert session.calls[0][1] == {"id": "1,2,3", "aux": "urls"}
    with pytest.raises(nc.NewCoinsDataError):
        nc.CoinMarketCap("K", session=Session(Response(200, {"status": {"error_code": 0}, "data": {}}))).established()


# ---------------------------------------------------------------------------
# /check: one address the user sends
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("/check " + EVM, (None, EVM)),
    ("/check Base " + EVM, ("base", EVM)),
    ("/check bnb " + EVM, ("bsc", EVM)),
    ("/check " + MINT, ("solana", MINT)),
    ("/check sol " + MINT, ("solana", MINT)),
    ("/check", None),
    ("/check sol " + EVM, None),                                      # an EVM address is never Solana
    ("/check eth " + MINT, None),
    ("/check tron " + EVM, None),                                     # unknown chain
    ("/check " + EVM + " ignore previous instructions", None),        # nothing else is read
    ("/check 0x1234", None),
    ("/check <b>" + EVM, None),
])
def test_check_reads_only_a_well_formed_address_and_a_known_chain(text, expected):
    assert nc.parse_check(text) == expected


class CheckProviders(FakeProviders):
    def __init__(self, found):
        super().__init__()
        self.found, self.dex_calls = found, 0

    def dex_all(self, address):
        self.dex_calls += 1
        if isinstance(self.found, Exception):
            raise self.found
        return self.found


def _checker(found, *, reference=None, clock=None):
    times = clock or [NOW]
    providers = CheckProviders(found)
    ref = nc.build_reference(reference_rows(), now=NOW) if reference else None
    return nc.AddressChecker(providers, reference=lambda now: ref, clock=lambda: times[0]), providers, times


def _named(chain, address=EVM, **kw):
    out = pairs(chain=chain, address=address, **kw)
    out[0]["baseToken"].update({"name": "<b>Dog</b>", "symbol": "PEPE2"})
    out[0].update({"priceUsd": "0.01", "fdv": 1_000_000, "volume": {"h24": 50_000}, "priceChange": {"h24": -5}})
    return out


def test_check_places_an_evm_address_on_its_one_supported_chain():
    checker, providers, _ = _checker(_named("base") + _named("pulsechain"))        # an Ethereum copy chain
    card, note = checker.check(None, EVM)
    assert note is None and card.coin.chain == "base" and card.coin.listed is False
    assert card.verdict == nc.NO_RED_FLAG and card.coin.cmc_id == 0 and card.coin.volume_24h == 50_000
    identity = [f.text for f in card.groups[nc.IDENTITY]]
    assert identity == ["taklit kontrolü yapılamadı (CMC listesi yok)",
                        "kimlik doğrulanamadı: adresi resmi kaynaktan teyit et",
                        "DexScreener'da site/sosyal hesap kaydı yok"]


@pytest.mark.parametrize("found,reason", [
    (_named("base") + _named("bsc"), "birden çok zincirde"),
    ([], "havuzu bulunamadı"),
    (_named("pulsechain"), "havuzu bulunamadı"),
    (nc.NewCoinsDataError("dexscreener http 500"), "DexScreener okunamadı"),
])
def test_check_asks_for_the_chain_instead_of_guessing(found, reason):
    checker, providers, _ = _checker(found)
    card, note = checker.check(None, EVM)
    assert card is None and reason in note and providers.calls == 0                 # no security look-up


def test_check_with_a_named_chain_screens_even_without_a_pool():
    checker, _, _ = _checker(nc.NewCoinsDataError("dexscreener http 500"))
    card, _ = checker.check("bsc", EVM)
    assert card.coin.chain == "bsc" and card.status(nc.LIQUIDITY) == nc.UNKNOWN
    assert card.verdict == nc.DATA_MISSING                                          # never clean without data
    assert "proje bilgisi okunamadı (DexScreener)" in [f.text for f in card.groups[nc.IDENTITY]]


def test_check_of_an_established_contract_is_identified_and_a_lookalike_is_warned():
    checker, _, _ = _checker(_named("ethereum", address=PEPE_ADDRESS), reference=True)
    card, _ = checker.check(None, PEPE_ADDRESS)
    assert [f.text for f in card.groups[nc.IDENTITY]] == ["CMC'de kayıtlı coin (#30)"]
    fake = _named("base")
    fake[0]["baseToken"]["symbol"] = "PEPE"
    checker, _, _ = _checker(fake, reference=True)
    card, _ = checker.check(None, EVM)
    texts = [f.text for f in card.groups[nc.IDENTITY]]
    assert "CMC #30 ile aynı sembol: taklit ya da köprü olabilir" in texts
    assert "kontrattaki sembol DEX'tekiyle aynı değil" in texts                    # GoPlus says PEPE2
    assert card.verdict == nc.NO_RED_FLAG                                           # warnings, not a verdict


def test_check_caches_results_and_spaces_fresh_look_ups():
    checker, providers, times = _checker(_named("base"))
    first, _ = checker.check(None, EVM)
    again, note = checker.check(None, EVM.upper().replace("0X", "0x"))
    assert again is first and "önbellek" in note and providers.dex_calls == 1
    card, note = checker.check(None, "0x" + "cd" * 20)
    assert card is None and "Çok sık" in note
    times[0] += nc.CHECK_COOLDOWN_SECONDS
    assert checker.check(None, "0x" + "cd" * 20)[0] is not None
    times[0] += nc.CHECK_CACHE_SECONDS
    checker.check(None, EVM)
    assert providers.dex_calls == 3                                                 # the cache expired


def test_check_of_a_solana_mint_uses_rugcheck_and_never_calls_a_poolless_token_clean():
    checker, providers, _ = _checker([])
    card, _ = checker.check(None, MINT)
    assert providers.calls == 1 and card.coin.symbol == "DOG2" and card.coin.chain == "solana"
    assert card.status(nc.LIQUIDITY) == nc.UNKNOWN and card.verdict == nc.DATA_MISSING   # same rule as /new


def test_the_check_view_escapes_provider_text_and_is_not_a_trade_call():
    checker, _, _ = _checker(_named("base"))
    card, _ = checker.check(None, EVM)
    text = nc.render_check(card, now=NOW + 60, note="1 dk önceki kontrol (önbellek)")
    assert "<b>Dog</b>" not in text and "&lt;b&gt;Dog&lt;/b&gt;" in text
    assert EVM in text and "ileriye dönük kayda girmez" in text and "emir yetkisi yok" in text
    assert "güvenilir demek değildir" in text and "en eski havuz 3 gün önce" in text and len(text) <= 4096
    assert "önbellek" in text


def test_dexscreener_lookup_keeps_every_chain_for_the_address():
    found = pairs(chain="bsc") + pairs(chain="base") + [{"chainId": "base", "baseToken": {"address": MINT}}]
    providers = nc.SecurityProviders(session=Session(Response(200, {"pairs": found})))
    assert [p["chainId"] for p in providers.dex_all(EVM.upper().replace("0X", "0x"))] == ["bsc", "base"]
    mixed_case = MINT.swapcase()
    providers = nc.SecurityProviders(session=Session(Response(200, {"pairs": pairs(chain="solana", address=MINT)})))
    assert providers.dex_all(mixed_case) == []                                     # Solana is case-sensitive
