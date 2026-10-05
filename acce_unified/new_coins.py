"""New CoinMarketCap listings through a contract-security screen (docs/NEW_COINS.md).

The screen looks for known scam patterns: a token that cannot be sold
(honeypot, sell tax, freeze authority), owner powers (minting, changing
balances, hidden owner), concentrated holders, liquidity that can be pulled,
and thin or inflated markets. Passing it means "no obvious red flag found",
never "safe" or "will rise": the thresholds are common rules of thumb, not
learned from data, and no accuracy has been measured yet. A forward record
(the ledger) is kept so that it can be.

Fail closed: a check whose data is missing, malformed or unavailable is
UNKNOWN, and an UNKNOWN critical check keeps a coin out of the "no red flag"
group. Every name, symbol, tag and provider field is untrusted external data:
it is escaped for display and never interpreted.

No order authority: research screen only (``can_authorize_trade`` is False).
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence

CMC_LISTINGS_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
CMC_QUOTES_URL = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"
GOPLUS_URL = "https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"
HONEYPOT_URL = "https://api.honeypot.is/v2/IsHoneypot"

HOUR = 3_600
DAY = 86_400
WINDOW_HOURS = 72                 # a coin is "new" for 72 hours after CoinMarketCap added it
LISTINGS_LIMIT = 200              # newest coins read per scan (one CMC credit)
RECHECK_SECONDS = 6 * HOUR        # contract powers and liquidity locks can change: re-check
MAX_CHECKS_PER_SCAN = 40          # security look-ups per scan (provider rate limits)
OUTCOME_DAYS = (7, 30, 90)
OUTCOME_TOLERANCE_SECONDS = DAY   # a horizon is measured within a day of it, or it is MISSED (never backfilled)
# Every field that can raise a red flag must be present before its group can pass.
# GoPlus fields that must parse. Live replies (2026-10-05) leave buy_tax/sell_tax empty and omit
# cannot_sell_all, so taxes and sellability come from an actual buy/sell simulation (honeypot.is).
SELLABILITY_FIELDS = ("is_honeypot", "slippage_modifiable", "personal_slippage_modifiable")
SIMULATION_FIELDS = ("buyTax", "sellTax", "transferTax")
POWER_FIELDS = ("is_open_source", "is_mintable", "owner_change_balance", "hidden_owner",
                "can_take_back_ownership", "selfdestruct")
KEEP_PER_VERDICT = 20             # cards kept in the bot state per verdict (newest first); counts cover all
SCHEMA = "new-coins/v2"
SCHEMA_TAG = "v2"                 # in ledger ids, so v1 records never shadow v2 first sights or outcomes

PASS, WARN, FAIL, UNKNOWN = "PASS", "WARN", "FAIL", "UNKNOWN"
ICON = {PASS: "✅", WARN: "⚠️", FAIL: "❌", UNKNOWN: "❔"}
HEAVY_RISK, DATA_MISSING, NO_RED_FLAG = "AGIR_RISK", "VERI_EKSIK", "BAYRAK_YOK"
VERDICT_LABEL = {HEAVY_RISK: "AĞIR RİSK", DATA_MISSING: "VERİ EKSİK", NO_RED_FLAG: "BARİZ KIRMIZI BAYRAK YOK"}
# A coin can only reach NO_RED_FLAG when every one of these groups was actually checked.
SELLABILITY, POWERS, HOLDERS, LIQUIDITY, IDENTITY, VOLUME, SUPPLY = (
    "Satılabilirlik", "Yetkiler", "Dağılım", "Likidite", "Kimlik", "Hacim", "Arz")
CRITICAL = (SELLABILITY, POWERS, HOLDERS, LIQUIDITY, IDENTITY)
GROUPS = (*CRITICAL, VOLUME, SUPPLY)

# Rules of thumb (not learned from data; docs/NEW_COINS.md).
TAX_FAIL, TAX_WARN = Decimal("0.10"), Decimal("0.05")
TOP10_FAIL, TOP10_WARN = Decimal("0.50"), Decimal("0.30")
LP_WALLET_FAIL = Decimal("0.50")  # LP held by plain wallets (not locked, burned or in a contract) can be pulled
# Data that cannot be measured is UNKNOWN once it is material: an LP holder or LP pool carrying at least this share.
MATERIAL_SHARE = Decimal("0.01")
CREATOR_FAIL, CREATOR_WARN = Decimal("0.20"), Decimal("0.05")
LP_LOCK_WARN = Decimal("0.90")
LIQUIDITY_FAIL_USD, LIQUIDITY_WARN_USD = 10_000.0, 50_000.0
YOUNG_POOL_SECONDS = DAY
NO_SELLS_MIN_BUYS = 20
VOLUME_TO_LIQUIDITY_WARN = 50.0
CIRCULATING_WARN = 0.20

# Chains the screen can check: (key, GoPlus chain id or "rugcheck", DexScreener chain id, CMC names/slugs).
CHAINS = (
    ("solana", "rugcheck", "solana", ("solana",)),   # honeypot.is chain id = the GoPlus id for EVM chains
    ("bsc", "56", "bsc", ("bnb smart chain (bep20)", "bnb smart chain", "bnb", "bsc", "binance smart chain",
                           "binance-smart-chain")),
    ("base", "8453", "base", ("base",)),
    ("arbitrum", "42161", "arbitrum", ("arbitrum", "arbitrum one", "arbitrum-one")),
    ("polygon", "137", "polygon", ("polygon", "polygon pos", "matic", "matic-network", "polygon-ecosystem-token")),
    ("avalanche", "43114", "avalanche", ("avalanche c-chain", "avalanche", "avalanche-c-chain")),
    ("optimism", "10", "optimism", ("optimism", "op mainnet", "optimism-ethereum")),
    ("ethereum", "1", "ethereum", ("ethereum",)),
)
CHAIN_LABEL = {"solana": "Solana", "bsc": "BSC", "base": "Base", "arbitrum": "Arbitrum", "polygon": "Polygon",
               "avalanche": "Avalanche", "optimism": "Optimism", "ethereum": "Ethereum"}
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
SOLANA_ADDRESS = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
BURN_ADDRESSES = frozenset({"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead",
                            "0xdead000000000000000042069420694206942069", "11111111111111111111111111111111"})


# Forward evaluation, pre-registered before the screen produced any record (research/trials/registry.jsonl).
# A change to a threshold, a critical group or the verdict rule needs a new SCHEMA and a new trial.
TRIAL_FAMILY = "new_coin_security_screen_forward"
TRIAL_DATASET = {"source": "coinmarketcap listings/latest by date_added; GoPlus and a honeypot.is buy/sell "
                           "simulation (EVM), RugCheck (Solana), DexScreener; outcomes from CoinMarketCap quotes",
                 "forward_from": "2026-10-05", "kind": "live forward record (/data/new_coins_ledger.jsonl)",
                 "supersedes": "3deff6cb87ad4e38 (new-coins/v1, never evaluated: recalibrated on live provider "
                               "replies the day it shipped, before any outcome)"}
TRIAL = "b57b9eb0a6073085"


def trial_params() -> dict[str, Any]:
    return {
        "schema": SCHEMA, "window_hours": WINDOW_HOURS, "recheck_seconds": RECHECK_SECONDS,
        "critical_groups": list(CRITICAL), "verdict_rule": "any FAIL -> AGIR_RISK; else any UNKNOWN critical "
                                                          "group -> VERI_EKSIK; else BAYRAK_YOK",
        "thresholds": {"tax_fail": str(TAX_FAIL), "tax_warn": str(TAX_WARN), "top10_fail": str(TOP10_FAIL),
                       "top10_warn": str(TOP10_WARN), "creator_fail": str(CREATOR_FAIL),
                       "creator_warn": str(CREATOR_WARN),
                       "lp_lock_warn": str(LP_LOCK_WARN), "liquidity_fail_usd": LIQUIDITY_FAIL_USD,
                       "liquidity_warn_usd": LIQUIDITY_WARN_USD, "young_pool_seconds": YOUNG_POOL_SECONDS,
                       "no_sells_min_buys": NO_SELLS_MIN_BUYS, "lp_wallet_fail": str(LP_WALLET_FAIL),
                       "material_share": str(MATERIAL_SHARE)},
        "required_fields": {"sellability": list(SELLABILITY_FIELDS) + [f"simulation.{k}" for k in SIMULATION_FIELDS]
                            + ["simulation.isHoneypot"], "powers": list(POWER_FIELDS),
                            "holders": "at least one counted holder; Solana creatorBalance",
                            "lp": "every LP holder >= 1% classified (EVM); LP-token pools carrying >= 1% of "
                                  "their liquidity measurable (Solana)"},
        "lp_rule": {"evm": "FAIL if >= 50% of LP is held by plain wallets; PASS if >= 90% locked or burned; "
                           "else WARN (contract-held, unverifiable)",
                    "solana": "liquidity-weighted lock over LP-token pools and bonding curves; PASS >= 90%, else "
                              "WARN; FAIL only from RugCheck risks or its rug flag"},
        "holders_rule": {"evm": "top 10 excluding locked, burn, pair and tagged contracts; exchanges included",
                         "solana": "top 10 excluding RugCheck AMM/LOCKER accounts, WARN at most; FAIL from RugCheck "
                                   "holder risks or a creator share >= 20%"},
        "outcome_days": list(OUTCOME_DAYS),
        "outcome_rule": "price measured within 1 day after the horizon; past that the horizon is MISSED, or "
                        "NO_QUOTE if CMC was asked inside the window and had no price; never backfilled",
        "outcome_tolerance_seconds": OUTCOME_TOLERANCE_SECONDS,
        "evaluation": {
            "not_before": "2027-04-05",
            "unit": "coin, by its verdict at first sight (FIRST_SEEN)",
            "primary": "collapse rate at 30 days: share with a 30-day return <= -90% or NO_QUOTE",
            "compare": "AGIR_RISK minus BAYRAK_YOK; VERI_EKSIK reported separately",
            "ci": "95% bootstrap over ISO weeks of first sight, 2000 draws, seed 0",
            "min_per_group": 30, "max_missing_outcomes": 0.2, "missing": "MISSED outcomes (NO_QUOTE is not missing)",
            "decisions": {"SCREEN_SEPARATES": "difference > 0 and the interval excludes 0",
                          "NO_SEPARATION": "the interval includes 0",
                          "REVERSED": "difference < 0 and the interval excludes 0",
                          "INCOMPLETE_DATA": "fewer than 30 coins in either group, or more than 20% of due "
                                             "30-day outcomes missing"},
            "secondary": ["median 30- and 90-day return by verdict",
                          "collapse rate of BAYRAK_YOK: how often a coin without an obvious red flag still collapsed"],
            "live_effect": "none automatic; any label change goes through a separate PR and the user's approval",
        },
    }


class NewCoinsDataError(RuntimeError):
    """Provider data that cannot be used (transport, HTTP status, malformed payload). Type-only text."""


# ---------------------------------------------------------------------------
# CoinMarketCap: the newest coins
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewCoin:
    cmc_id: int
    name: str
    symbol: str
    slug: str
    date_added: int
    tags: tuple[str, ...]
    platform: str | None             # CMC platform name, untrusted text
    chain: str | None                # our chain key when the screen supports it
    address: str | None
    price: float | None
    volume_24h: float | None
    market_cap: float | None
    fdv: float | None
    change_24h: float | None
    circulating_supply: float | None
    total_supply: float | None

    @property
    def is_meme(self) -> bool:
        return any("meme" in t.lower() for t in self.tags)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _epoch(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return int(parsed.timestamp())


def chain_of(platform: Mapping[str, Any] | None) -> str | None:
    """Our chain key for a CMC platform, matched on its exact name or slug; None when unsupported."""

    if not isinstance(platform, Mapping):
        return None
    names = {str(platform.get(k) or "").strip().lower() for k in ("name", "slug")} - {""}
    for key, _goplus, _dex, aliases in CHAINS:
        if names & set(aliases):
            return key
    return None


def valid_address(chain: str | None, address: Any) -> str | None:
    if not isinstance(address, str):
        return None
    address = address.strip()
    if chain == "solana":
        return address if SOLANA_ADDRESS.match(address) else None
    if chain is not None:
        return address if EVM_ADDRESS.match(address) else None
    return None


def parse_listing(row: Any, *, now: int) -> NewCoin | None:
    """One CMC listings row; None when it cannot be placed in time or identified (never guessed)."""

    if not isinstance(row, Mapping):
        return None
    try:
        cmc_id = int(row["id"])
    except (KeyError, TypeError, ValueError):
        return None
    added = _epoch(row.get("date_added"))
    if added is None or added > now:
        return None                                    # not yet added as of now: never seen early
    platform = row.get("platform") if isinstance(row.get("platform"), Mapping) else None
    chain = chain_of(platform)
    usd = ((row.get("quote") or {}).get("USD") or {}) if isinstance(row.get("quote"), Mapping) else {}
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    return NewCoin(
        cmc_id=cmc_id, name=str(row.get("name") or "?")[:60], symbol=str(row.get("symbol") or "?")[:20],
        slug=str(row.get("slug") or "")[:80], date_added=added,
        tags=tuple(str(t)[:40] for t in tags if isinstance(t, (str, int)))[:20],
        platform=str(platform.get("name"))[:40] if platform and platform.get("name") else None,
        chain=chain, address=valid_address(chain, platform.get("token_address")) if platform else None,
        price=_positive(usd.get("price")), volume_24h=_finite(usd.get("volume_24h")),
        market_cap=_finite(usd.get("market_cap")), fdv=_finite(usd.get("fully_diluted_market_cap")),
        change_24h=_finite(usd.get("percent_change_24h")),
        circulating_supply=_finite(row.get("circulating_supply")), total_supply=_finite(row.get("total_supply")),
    )


class _Http:
    def __init__(self, *, timeout: int, session: Any = None) -> None:
        import requests

        self.timeout = timeout
        self.session = session or requests.Session()

    def get(self, name: str, url: str, *, params: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None, not_found_ok: bool = False) -> Any:
        try:
            response = self.session.get(url, params=dict(params or {}), headers=dict(headers or {}),
                                        timeout=self.timeout)
        except Exception as exc:                       # exception text can carry URLs and headers
            raise NewCoinsDataError(f"{name} transport {type(exc).__name__}") from None
        if not_found_ok and response.status_code == 404:
            return None
        if response.status_code != 200:
            raise NewCoinsDataError(f"{name} http {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise NewCoinsDataError(f"{name} malformed JSON") from None


class CoinMarketCap:
    """CMC Pro API (free Basic plan). The key goes in a header, never in a URL, log or error."""

    def __init__(self, api_key: str, *, timeout: int = 15, session: Any = None) -> None:
        self._key = api_key.strip()
        self.http = _Http(timeout=timeout, session=session)

    @property
    def configured(self) -> bool:
        return bool(self._key)

    def _get(self, url: str, params: Mapping[str, Any]) -> Any:
        if not self._key:
            raise NewCoinsDataError("cmc api key missing")
        payload = self.http.get("cmc", url, params=params,
                                headers={"X-CMC_PRO_API_KEY": self._key, "Accept": "application/json"})
        status = payload.get("status") if isinstance(payload, Mapping) else None
        code = status.get("error_code") if isinstance(status, Mapping) else None
        if code not in (0, "0", None):
            raise NewCoinsDataError(f"cmc error {str(code)[:8]}")
        return payload.get("data") if isinstance(payload, Mapping) else None

    def newest(self, *, limit: int = LISTINGS_LIMIT) -> list[Any]:
        data = self._get(CMC_LISTINGS_URL, {
            "sort": "date_added", "sort_dir": "desc", "limit": limit, "convert": "USD",
            "aux": "date_added,tags,platform,circulating_supply,total_supply,max_supply"})
        if not isinstance(data, list):
            raise NewCoinsDataError("cmc listings payload is not a list")
        return data

    def prices(self, ids: Sequence[int]) -> dict[int, float]:
        """Current USD price per CMC id; ids without a usable quote are left out (unknown, not zero)."""

        out: dict[int, float] = {}
        for k in range(0, len(ids), 100):
            data = self._get(CMC_QUOTES_URL, {"id": ",".join(str(i) for i in ids[k:k + 100]), "convert": "USD"})
            if not isinstance(data, Mapping):
                raise NewCoinsDataError("cmc quotes payload is not an object")
            for key, row in data.items():
                row = row[0] if isinstance(row, list) and row else row
                if not isinstance(row, Mapping):
                    continue
                price = _positive(((row.get("quote") or {}).get("USD") or {}).get("price"))
                try:
                    if price is not None:
                        out[int(key)] = price
                except (TypeError, ValueError):
                    continue
        return out


# ---------------------------------------------------------------------------
# Security providers
# ---------------------------------------------------------------------------


class SecurityProviders:
    """GoPlus (EVM), RugCheck (Solana) and DexScreener (all chains). Public endpoints, no keys."""

    def __init__(self, *, timeout: int = 15, session: Any = None) -> None:
        self.http = _Http(timeout=timeout, session=session)

    def goplus(self, chain: str, address: str) -> Mapping[str, Any] | None:
        chain_id = next(g for key, g, _d, _a in CHAINS if key == chain)
        payload = self.http.get("goplus", GOPLUS_URL.format(chain_id=chain_id),
                                params={"contract_addresses": address})
        if not isinstance(payload, Mapping) or str(payload.get("code")) != "1":
            raise NewCoinsDataError("goplus unusable reply")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            return None
        entry = result.get(address.lower()) or result.get(address)
        return entry if isinstance(entry, Mapping) else None

    def honeypot(self, chain: str, address: str) -> Mapping[str, Any] | None:
        """honeypot.is buy/sell simulation; None when it has no pair for the token (404)."""

        chain_id = next(g for key, g, _d, _a in CHAINS if key == chain)
        payload = self.http.get("honeypot", HONEYPOT_URL, params={"address": address, "chainID": chain_id},
                                not_found_ok=True)
        return payload if isinstance(payload, Mapping) else None

    def rugcheck(self, mint: str) -> Mapping[str, Any] | None:
        payload = self.http.get("rugcheck", RUGCHECK_URL.format(mint=mint), not_found_ok=True)
        return payload if isinstance(payload, Mapping) else None

    def dex_pairs(self, chain: str, address: str) -> list[Mapping[str, Any]]:
        dex_chain = next(d for key, _g, d, _a in CHAINS if key == chain)
        payload = self.http.get("dexscreener", DEXSCREENER_URL.format(address=address))
        pairs = payload.get("pairs") if isinstance(payload, Mapping) else None
        if pairs is None:
            return []
        if not isinstance(pairs, list):
            raise NewCoinsDataError("dexscreener pairs payload is not a list")
        same = (lambda a: a.lower() == address.lower()) if chain != "solana" else (lambda a: a == address)
        return [p for p in pairs if isinstance(p, Mapping) and p.get("chainId") == dex_chain
                and isinstance(p.get("baseToken"), Mapping) and same(str(p["baseToken"].get("address") or ""))]


# ---------------------------------------------------------------------------
# The screen (pure: provider payloads in, checks and a verdict out)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    status: str
    text: str                        # built by this module from numbers and fixed words, never provider text


@dataclass
class Card:
    coin: NewCoin
    groups: dict[str, list[Finding]] = field(default_factory=dict)
    liquidity_usd: float | None = None
    checked_at: int | None = None

    def add(self, group: str, status: str, text: str) -> None:
        self.groups.setdefault(group, []).append(Finding(status, text))

    def status(self, group: str) -> str:
        findings = self.groups.get(group) or [Finding(UNKNOWN, "kontrol edilmedi")]
        for level in (FAIL, UNKNOWN, WARN):
            if any(f.status == level for f in findings):
                return level
        return PASS

    @property
    def verdict(self) -> str:
        if any(self.status(g) == FAIL for g in GROUPS):
            return HEAVY_RISK
        if any(self.status(g) == UNKNOWN for g in CRITICAL):
            return DATA_MISSING
        return NO_RED_FLAG

    def reasons(self, *levels: str) -> list[str]:
        return [f.text for g in GROUPS for f in self.groups.get(g, []) if f.status in levels]

    def summary(self) -> dict[str, Any]:
        return {"verdict": self.verdict, "groups": {g: self.status(g) for g in GROUPS},
                "red_flags": self.reasons(FAIL), "unknown": self.reasons(UNKNOWN), "warnings": self.reasons(WARN),
                "liquidity_usd": self.liquidity_usd, "checked_at": self.checked_at}


def _flag(value: Any) -> bool | None:
    text = str(value).strip() if value is not None else ""
    return True if text == "1" else False if text == "0" else None


def _dec(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _fraction(value: Any) -> Decimal | None:
    """A GoPlus fraction (0.1 = 10%); anything outside 0..1 is not trusted."""

    number = _dec(value)
    return number if number is not None and 0 <= number <= 1 else None


def _pct(value: Decimal) -> str:
    return f"%{float(value) * 100:.0f}"


def _share(part: Any, whole: Any) -> Decimal | None:
    """part / whole from raw amounts, so a provider's percent units can never be misread."""

    a, b = _dec(part), _dec(whole)
    if a is None or b is None or b <= 0 or a < 0 or a > b * Decimal("1.000001"):
        return None
    return a / b


def _sim_tax(value: Any) -> Decimal | None:
    """A honeypot.is tax in percent (0..100) as a fraction; anything else is not trusted."""

    number = _dec(value)
    return number / 100 if number is not None and 0 <= number <= 100 else None


def screen_evm(card: Card, entry: Mapping[str, Any] | None, simulation: Mapping[str, Any] | None = None) -> None:
    """GoPlus token_security (contract powers, holders, LP) and a honeypot.is buy/sell simulation."""

    _screen_sellability(card, entry, simulation)
    if entry is None:
        for group in (POWERS, HOLDERS):
            card.add(group, UNKNOWN, "güvenlik verisi bulunamadı")
        card.add(LIQUIDITY, UNKNOWN, "havuz kilidi bilinmiyor")
        return
    coin = card.coin
    required = {k: _flag(entry.get(k)) for k in POWER_FIELDS}
    if required["is_open_source"] is False:
        card.add(POWERS, FAIL, "kaynak kodu doğrulanmamış")
    for key, status, text in (("is_mintable", FAIL, "yeni token basılabilir"),
                              ("owner_change_balance", FAIL, "sahip bakiyeleri değiştirebilir"),
                              ("hidden_owner", FAIL, "gizli sahip var"),
                              ("can_take_back_ownership", FAIL, "sahiplik geri alınabilir"),
                              ("selfdestruct", FAIL, "kontrat kendini yok edebilir"),
                              ("is_proxy", WARN, "kod sonradan değiştirilebilir (proxy)"),
                              ("external_call", WARN, "dış kontrata bağımlı")):
        if _flag(entry.get(key)):
            card.add(POWERS, status, text)
    if any(v is None for v in required.values()):
        card.add(POWERS, UNKNOWN, "yetkiler doğrulanamadı")
    elif not card.groups.get(POWERS):
        card.add(POWERS, PASS, "kod açık, basım ve bakiye yetkisi yok")

    total = entry.get("total_supply")
    holders = entry.get("holders") if isinstance(entry.get("holders"), list) else None
    if holders is None or _dec(total) is None:
        card.add(HOLDERS, UNKNOWN, "cüzdan dağılımı bilinmiyor")
    else:
        pairs = {str(d.get("pair") or "").lower() for d in entry.get("dex") or [] if isinstance(d, Mapping)}
        counted = [h for h in holders if isinstance(h, Mapping) and not _flag(h.get("is_locked"))
                   and str(h.get("address") or "").lower() not in BURN_ADDRESSES | pairs
                   and not (_flag(h.get("is_contract")) and h.get("tag"))]
        shares = [_share(h.get("balance"), total) for h in counted[:10]]
        if not shares or any(s is None for s in shares):
            card.add(HOLDERS, UNKNOWN, "cüzdan payları okunamadı")
        else:
            _holder_findings(card, sum(shares, Decimal(0)), "borsa cüzdanları dahil")
    for key, label in (("creator_percent", "yaratıcı"), ("owner_percent", "sahip")):
        share = _fraction(entry.get(key))
        if share is not None and share >= CREATOR_FAIL:
            card.add(HOLDERS, FAIL, f"{label} payı {_pct(share)}")
        elif share is not None and share >= CREATOR_WARN:
            card.add(HOLDERS, WARN, f"{label} payı {_pct(share)}")

    _evm_lp_lock(card, entry)
    symbol = str(entry.get("token_symbol") or "").strip()
    if symbol and symbol.upper() != coin.symbol.upper():
        card.add(IDENTITY, WARN, "kontrattaki sembol CMC'dekiyle aynı değil")


def _screen_sellability(card: Card, entry: Mapping[str, Any] | None, simulation: Mapping[str, Any] | None) -> None:
    """Sellability passes only on a successful buy/sell simulation and complete GoPlus sell-side flags."""

    sim_ok = isinstance(simulation, Mapping) and simulation.get("simulationSuccess") is True
    result = simulation.get("simulationResult") if sim_ok and isinstance(simulation.get("simulationResult"),
                                                                          Mapping) else {}
    taxes = {k: _sim_tax(result.get(k)) for k in SIMULATION_FIELDS}
    verdict = simulation.get("honeypotResult") if sim_ok and isinstance(simulation.get("honeypotResult"),
                                                                         Mapping) else {}
    is_honeypot = verdict.get("isHoneypot") if isinstance(verdict.get("isHoneypot"), bool) else None
    if is_honeypot:
        card.add(SELLABILITY, FAIL, "honeypot: satış simülasyonu başarısız")
    for key, label in (("buyTax", "alım"), ("sellTax", "satış"), ("transferTax", "transfer")):
        tax = taxes[key]
        if tax is not None and tax >= TAX_FAIL:
            card.add(SELLABILITY, FAIL, f"{label} vergisi {_pct(tax)}")
        elif tax is not None and tax >= TAX_WARN:
            card.add(SELLABILITY, WARN, f"{label} vergisi {_pct(tax)}")
    flags = {k: _flag((entry or {}).get(k)) for k in SELLABILITY_FIELDS}
    if flags["is_honeypot"]:
        card.add(SELLABILITY, FAIL, "GoPlus: honeypot")
    for key, status, text in (("slippage_modifiable", FAIL, "vergi sonradan artırılabilir"),
                              ("personal_slippage_modifiable", FAIL, "cüzdana özel vergi konabilir"),
                              ("transfer_pausable", WARN, "transfer durdurulabilir"),
                              ("is_blacklisted", WARN, "kara liste yetkisi var"),
                              ("trading_cooldown", WARN, "işlem bekleme süresi var")):
        if _flag((entry or {}).get(key)):
            card.add(SELLABILITY, status, text)
    if not sim_ok or is_honeypot is None or any(t is None for t in taxes.values()):
        card.add(SELLABILITY, UNKNOWN, "alım-satım simülasyonu yapılamadı")
    elif entry is None or any(v is None for v in flags.values()):
        card.add(SELLABILITY, UNKNOWN, "satış yetkileri doğrulanamadı")
    elif not card.groups.get(SELLABILITY):
        card.add(SELLABILITY, PASS, f"simülasyonda alım-satım çalıştı, vergi {_pct(taxes['buyTax'])}/"
                                    f"{_pct(taxes['sellTax'])}")


def _evm_lp_lock(card: Card, entry: Mapping[str, Any]) -> None:
    """LP (V2 tokens or V3/V4 positions) held by plain wallets can be pulled: that is the red flag.

    Locked or burned LP passes. LP held by contracts (lockers GoPlus does not
    tag, the token contract itself, position managers) cannot be verified and
    is a warning, never a pass.
    """

    lp_holders = entry.get("lp_holders") if isinstance(entry.get("lp_holders"), list) else None
    lp_total = entry.get("lp_total_supply")
    if not _flag(entry.get("is_in_dex")) or not lp_holders or _dec(lp_total) is None:
        card.add(LIQUIDITY, UNKNOWN, "havuz kilidi bilinmiyor")
        return
    safe, wallets = [], []
    for h in lp_holders:
        if not isinstance(h, Mapping):
            continue
        share = _share(h.get("balance"), lp_total)
        if share is None:
            card.add(LIQUIDITY, UNKNOWN, "havuz kilidi okunamadı")
            return
        if _flag(h.get("is_locked")) or str(h.get("address") or "").lower() in BURN_ADDRESSES:
            safe.append(share)
        elif _flag(h.get("is_contract")) is False:
            wallets.append(share)
        elif _flag(h.get("is_contract")) is None and share >= MATERIAL_SHARE:
            card.add(LIQUIDITY, UNKNOWN, "havuz sahibinin cüzdan mı kontrat mı olduğu bilinmiyor")
            return
    safe_share, wallet_share = sum(safe, Decimal(0)), sum(wallets, Decimal(0))
    if wallet_share >= LP_WALLET_FAIL:
        card.add(LIQUIDITY, FAIL, f"havuzun {_pct(wallet_share)}'ı cüzdanlarda; çekilebilir (rug riski)")
    elif safe_share >= LP_LOCK_WARN:
        card.add(LIQUIDITY, PASS, f"havuzun {_pct(safe_share)}'ı kilitli/yakılmış")
    else:
        card.add(LIQUIDITY, WARN, f"havuzun {_pct(safe_share)}'ı kilitli/yakılmış; geri kalanı kontratlarda, "
                                  "kilit doğrulanamadı")


def screen_solana(card: Card, report: Mapping[str, Any] | None) -> None:
    """RugCheck report for a Solana mint (authorities, holders, LP lock, rug flag)."""

    if report is None:
        for group in (SELLABILITY, POWERS, HOLDERS):
            card.add(group, UNKNOWN, "güvenlik verisi bulunamadı")
        card.add(LIQUIDITY, UNKNOWN, "havuz kilidi bilinmiyor")
        return
    if report.get("rugged") is True:
        card.add(LIQUIDITY, FAIL, "RugCheck: rug pull olmuş")
    token = report.get("token") if isinstance(report.get("token"), Mapping) else None
    if token is None or "freezeAuthority" not in token or "mintAuthority" not in token:
        card.add(SELLABILITY, UNKNOWN, "dondurma yetkisi bilinmiyor")
        card.add(POWERS, UNKNOWN, "basım yetkisi bilinmiyor")
    else:
        if token.get("freezeAuthority"):
            card.add(SELLABILITY, FAIL, "cüzdan dondurulabilir (freeze yetkisi açık)")
        else:
            card.add(SELLABILITY, PASS, "dondurma yetkisi kapalı")
        if token.get("mintAuthority"):
            card.add(POWERS, FAIL, "yeni token basılabilir (mint yetkisi açık)")
        else:
            card.add(POWERS, PASS, "basım yetkisi kapalı")
    fee = report.get("transferFee") if isinstance(report.get("transferFee"), Mapping) else None
    fee_pct = _finite(fee.get("pct")) if fee else None
    if fee_pct is not None and fee_pct >= float(TAX_FAIL * 100):
        card.add(SELLABILITY, FAIL, f"transfer ücreti %{fee_pct:.0f}")
    elif fee_pct is not None and fee_pct > 0:
        card.add(SELLABILITY, WARN, f"transfer ücreti %{fee_pct:.1f}")
    meta = report.get("tokenMeta") if isinstance(report.get("tokenMeta"), Mapping) else {}
    if meta.get("mutable") is True:
        card.add(POWERS, WARN, "isim ve sembol değiştirilebilir")
    symbol = str(meta.get("symbol") or "").strip()
    if symbol and symbol.upper() != card.coin.symbol.upper():
        card.add(IDENTITY, WARN, "kontrattaki sembol CMC'dekiyle aynı değil")

    supply = token.get("supply") if token else None
    known = report.get("knownAccounts") if isinstance(report.get("knownAccounts"), Mapping) else {}
    pools = {a for a, v in known.items() if isinstance(v, Mapping) and str(v.get("type")) in ("AMM", "LOCKER")}
    holders = report.get("topHolders") if isinstance(report.get("topHolders"), list) else None
    if holders is None or _dec(supply) is None:
        card.add(HOLDERS, UNKNOWN, "cüzdan dağılımı bilinmiyor")
    else:
        counted = [h for h in holders if isinstance(h, Mapping)
                   and not ({str(h.get("owner") or ""), str(h.get("address") or "")} & pools)]
        shares = [_share(h.get("amount"), supply) for h in counted[:10]]
        if not shares or any(s is None for s in shares):
            card.add(HOLDERS, UNKNOWN, "cüzdan payları okunamadı (arzın hepsi havuzda olabilir)")
        else:
            top10 = sum(shares, Decimal(0))
            status = WARN if top10 >= TOP10_WARN else PASS     # FAIL comes from RugCheck's own risk list
            card.add(HOLDERS, status, f"ilk 10 cüzdan {_pct(top10)} (havuzlar hariç)")
        if sum(1 for h in counted[:10] if h.get("insider") is True) >= 3:
            card.add(HOLDERS, WARN, "ilk 10 cüzdanda birbirine bağlı (insider) hesaplar")
    creator = _share(report.get("creatorBalance"), supply)
    if creator is None:
        card.add(HOLDERS, UNKNOWN, "yaratıcının payı bilinmiyor")
    elif creator >= CREATOR_FAIL:
        card.add(HOLDERS, FAIL, f"yaratıcı payı {_pct(creator)}")
    elif creator is not None and creator >= CREATOR_WARN:
        card.add(HOLDERS, WARN, f"yaratıcı payı {_pct(creator)}")
    _rugcheck_risks(card, report.get("risks"))
    _solana_lp_lock(card, report.get("markets"))


SYSTEM_PROGRAM = "11111111111111111111111111111111"


def _solana_lp_lock(card: Card, markets: Any) -> None:
    """Liquidity-weighted LP lock over pools that have an LP token (or a bonding curve).

    Concentrated-liquidity pools (Orca, Meteora DLMM…) have no LP token to
    lock and are left out. Our own figure is at most a warning; a red flag
    comes from RugCheck's risk list ("LP unlocked") or its rug flag.
    """

    weighted, weight, unmeasured, unknown_size = Decimal(0), Decimal(0), Decimal(0), False
    for market in markets if isinstance(markets, list) else []:
        lp = market.get("lp") if isinstance(market, Mapping) and isinstance(market.get("lp"), Mapping) else None
        if lp is None:
            continue
        curve = str(market.get("marketType") or "") == "pump_fun"
        if str(lp.get("lpMint") or SYSTEM_PROGRAM) == SYSTEM_PROGRAM and not curve:
            continue
        quote, base = _dec(lp.get("quoteUSD")), _dec(lp.get("baseUSD"))
        if quote is None or base is None or quote < 0 or base < 0:
            unknown_size = True                            # cannot tell whether this pool matters
            continue
        usd = quote + base
        if usd <= 0:
            continue                                       # an empty pool carries nothing to pull
        share = _share(lp.get("lpLocked"), lp.get("lpTotalSupply"))
        if share is None:
            unmeasured += usd
            continue
        weighted, weight = weighted + share * usd, weight + usd
    if unknown_size or (unmeasured > 0 and unmeasured / (weight + unmeasured) >= MATERIAL_SHARE):
        card.add(LIQUIDITY, UNKNOWN, "havuz kilidi ölçülemiyor (LP tokenli bir havuzun verisi bozuk)")
        return
    if weight <= 0:
        card.add(LIQUIDITY, UNKNOWN, "havuz kilidi ölçülemiyor (LP tokeni olan havuz yok)")
        return
    locked = weighted / weight
    status = PASS if locked >= LP_LOCK_WARN else WARN
    card.add(LIQUIDITY, status, f"LP tokenli havuzların {_pct(locked)}'ı kilitli/yakılmış (likiditeye göre)")


# RugCheck risk names (English, untrusted) mapped to our groups by keyword; the text shown is ours.
RUGCHECK_RISKS = (
    (("holder",), HOLDERS, "RugCheck: cüzdan yoğunlaşması"),
    (("ownership",), HOLDERS, "RugCheck: tek cüzdan yoğunlaşması"),
    (("lp", "unlocked"), LIQUIDITY, "RugCheck: havuzun büyük kısmı kilitsiz"),
    (("liquidity",), LIQUIDITY, "RugCheck: düşük likidite"),
    (("copycat",), IDENTITY, "RugCheck: taklit token"),
    (("rug", "creator"), IDENTITY, "RugCheck: yaratıcının geçmişinde rug pull var"),
)


def _rugcheck_risks(card: Card, risks: Any) -> None:
    if not isinstance(risks, list):
        return
    for risk in risks:
        if not isinstance(risk, Mapping):
            continue
        name = str(risk.get("name") or "").lower()
        level = str(risk.get("level") or "").lower()
        status = FAIL if level == "danger" else WARN if level == "warn" else None
        if status is None:
            continue
        for words, group, text in RUGCHECK_RISKS:
            if all(w in name for w in words):
                card.add(group, status, text)
                break


def _holder_findings(card: Card, top10: Decimal, note: str) -> None:
    if top10 >= TOP10_FAIL:
        card.add(HOLDERS, FAIL, f"ilk 10 cüzdan arzın {_pct(top10)}'ını tutuyor ({note})")
    elif top10 >= TOP10_WARN:
        card.add(HOLDERS, WARN, f"ilk 10 cüzdan {_pct(top10)} ({note})")
    else:
        card.add(HOLDERS, PASS, f"ilk 10 cüzdan {_pct(top10)} ({note})")


def screen_market(card: Card, pairs: Sequence[Mapping[str, Any]] | None, *, now: int,
                  fallback_usd: float | None = None) -> None:
    """DexScreener liquidity, pool age and buy/sell counts; CMC volume against liquidity."""

    coin = card.coin
    if pairs is None:
        card.add(LIQUIDITY, UNKNOWN, "DEX likiditesi okunamadı")
    elif not pairs:
        card.add(LIQUIDITY, UNKNOWN, "DEX havuzu bulunamadı")
    else:
        liquidity = [_finite((p.get("liquidity") or {}).get("usd")) if isinstance(p.get("liquidity"), Mapping)
                     else None for p in pairs]
        known = [v for v in liquidity if v is not None and v >= 0]
        if not known and fallback_usd is not None and fallback_usd >= 0:
            known = [fallback_usd]                         # e.g. a pump.fun curve DexScreener does not price
        if not known:
            card.add(LIQUIDITY, UNKNOWN, "DEX likiditesi okunamadı")
        else:
            total = sum(known)
            card.liquidity_usd = total
            if total < LIQUIDITY_FAIL_USD:
                card.add(LIQUIDITY, FAIL, f"çok sığ likidite (${total:,.0f})")
            elif total < LIQUIDITY_WARN_USD:
                card.add(LIQUIDITY, WARN, f"sığ likidite (${total:,.0f})")
            else:
                card.add(LIQUIDITY, PASS, f"DEX likiditesi ${total:,.0f}")
        created = [_finite(p.get("pairCreatedAt")) for p in pairs]
        created = [c / 1000.0 for c in created if c is not None and c > 0]
        if created and now - min(created) < YOUNG_POOL_SECONDS:
            card.add(LIQUIDITY, WARN, "en eski havuz 1 günden genç")
        buys = sells = 0
        counted = False
        for p in pairs:
            h24 = ((p.get("txns") or {}).get("h24") or {}) if isinstance(p.get("txns"), Mapping) else {}
            b, s = _finite(h24.get("buys")), _finite(h24.get("sells"))
            if b is not None and s is not None:
                buys, sells, counted = buys + int(b), sells + int(s), True
        if counted and buys >= NO_SELLS_MIN_BUYS and sells == 0:
            card.add(SELLABILITY, FAIL, f"24 saatte {buys} alış, hiç satış yok (honeypot belirtisi)")
    if coin.volume_24h is not None and card.liquidity_usd:
        ratio = coin.volume_24h / card.liquidity_usd
        if ratio >= VOLUME_TO_LIQUIDITY_WARN:
            card.add(VOLUME, WARN, f"24s hacim likiditenin {ratio:.0f} katı (şişirilmiş hacim olabilir)")
        else:
            card.add(VOLUME, PASS, f"hacim/likidite {ratio:.1f}")
    else:
        card.add(VOLUME, UNKNOWN, "hacim/likidite oranı hesaplanamadı")
    if coin.circulating_supply and coin.total_supply and coin.total_supply > 0:
        share = coin.circulating_supply / coin.total_supply
        if share < CIRCULATING_WARN:
            card.add(SUPPLY, WARN, f"dolaşımdaki arz toplamın %{share * 100:.0f}'ı (seyrelme riski)")
        else:
            card.add(SUPPLY, PASS, f"dolaşımdaki arz %{min(share, 1.0) * 100:.0f}")
    else:
        card.add(SUPPLY, UNKNOWN, "arz verisi yok (CMC)")


def assess(coin: NewCoin, *, security: Mapping[str, Any] | None, pairs: Sequence[Mapping[str, Any]] | None,
           copies: int, now: int, security_error: bool = False) -> Card:
    """The full card for one coin from already-fetched provider payloads."""

    card = Card(coin=coin, checked_at=now)
    if copies > 1:
        card.add(IDENTITY, WARN, f"aynı sembolde {copies} yeni coin (taklit olabilir)")
    if coin.chain is None:
        text = "zincir desteklenmiyor" if coin.platform else "kontrat yok (ana ağ coini ya da bilinmiyor)"
        for group in CRITICAL:
            card.add(group, UNKNOWN, text)
        screen_market(card, None, now=now)
        return card
    if coin.address is None:
        for group in CRITICAL:
            card.add(group, UNKNOWN, "kontrat adresi geçersiz")
        screen_market(card, None, now=now)
        return card
    card.add(IDENTITY, PASS, f"{CHAIN_LABEL[coin.chain]} kontratı CMC'de kayıtlı")
    if security_error:
        for group in (SELLABILITY, POWERS, HOLDERS):
            card.add(group, UNKNOWN, "güvenlik sağlayıcısına ulaşılamadı")
        card.add(LIQUIDITY, UNKNOWN, "havuz kilidi bilinmiyor")
    elif coin.chain == "solana":
        screen_solana(card, security)
    else:
        bundle = security if isinstance(security, Mapping) else {}
        screen_evm(card, bundle.get("goplus"), bundle.get("honeypot"))
    fallback = (_finite(security.get("totalMarketLiquidity"))
                if coin.chain == "solana" and isinstance(security, Mapping) else None)
    screen_market(card, pairs, now=now, fallback_usd=fallback)
    return card


# ---------------------------------------------------------------------------
# The scanner: CMC -> security look-ups (cached) -> cards; forward record
# ---------------------------------------------------------------------------


class NewCoinsScanner:
    def __init__(self, cmc: CoinMarketCap, providers: SecurityProviders, ledger: Any, *,
                 window_hours: int = WINDOW_HOURS, sleep: Callable[[float], None] = time.sleep) -> None:
        self.cmc, self.providers, self.ledger = cmc, providers, ledger
        self.window = window_hours * HOUR
        self.sleep = sleep
        self._cache: dict[str, tuple[int, Mapping[str, Any] | None, list[Mapping[str, Any]] | None, bool]] = {}
        self._verdicts: dict[int, str] | None = None        # last recorded verdict per coin, from the ledger

    def _lookup(self, coin: NewCoin, now: int, budget: list[int]) -> tuple[Any, Any, bool] | None:
        key = f"{coin.chain}:{coin.address}"
        cached = self._cache.get(key)
        if cached and now - cached[0] < RECHECK_SECONDS:
            return cached[1], cached[2], cached[3]
        if budget[0] <= 0:
            return (cached[1], cached[2], cached[3]) if cached else None
        budget[0] -= 1
        security_error, retry = False, False
        try:
            if coin.chain == "solana":
                security: Any = self.providers.rugcheck(coin.address)
            else:
                security = {"goplus": self.providers.goplus(coin.chain, coin.address)}
                try:
                    security["honeypot"] = self.providers.honeypot(coin.chain, coin.address)
                except NewCoinsDataError as exc:     # sellability stays unknown; try again next scan
                    security["honeypot"], retry = None, True
                    if "http 429" in str(exc):
                        budget[0] = 0
        except NewCoinsDataError as exc:
            security, security_error = None, True
            if "http 429" in str(exc):
                budget[0] = 0                              # rate limited: no more look-ups this scan
        try:
            pairs = self.providers.dex_pairs(coin.chain, coin.address)
        except NewCoinsDataError as exc:
            pairs = None
            if "http 429" in str(exc):
                budget[0] = 0
        self.sleep(1.0)                                    # stay well inside the free rate limits
        if not security_error and not retry and pairs is not None:   # a failed look-up is retried next scan
            self._cache[key] = (now, security, pairs, False)
        return security, pairs, security_error

    def scan(self, now: int) -> dict[str, Any]:
        rows = self.cmc.newest()
        coins = [c for c in (parse_listing(r, now=now) for r in rows) if c is not None]
        coins = [c for c in coins if now - c.date_added <= self.window]
        symbols: dict[str, int] = {}
        for coin in coins:
            symbols[coin.symbol.upper()] = symbols.get(coin.symbol.upper(), 0) + 1
        budget = [MAX_CHECKS_PER_SCAN]
        cards = []
        for coin in sorted(coins, key=lambda c: -c.date_added):
            looked = self._lookup(coin, now, budget) if coin.chain and coin.address else (None, None, False)
            if looked is None:
                card = Card(coin=coin, checked_at=None)
                for group in CRITICAL:
                    card.add(group, UNKNOWN, "bu taramada sıraya alınamadı; sonraki taramada kontrol edilecek")
            else:
                security, pairs, error = looked
                card = assess(coin, security=security, pairs=pairs, copies=symbols[coin.symbol.upper()],
                              now=now, security_error=error)
                self._record(card, now)
            cards.append(card)
        live = {f"{c.chain}:{c.address}" for c in coins}
        self._cache = {k: v for k, v in self._cache.items() if k in live}
        counts = {v: sum(1 for c in cards if c.verdict == v) for v in (NO_RED_FLAG, HEAVY_RISK, DATA_MISSING)}
        kept = [c for v in (NO_RED_FLAG, HEAVY_RISK, DATA_MISSING)
                for c in [c for c in cards if c.verdict == v][:KEEP_PER_VERDICT]]
        return {"generated_at": now, "window_hours": self.window // HOUR, "read": len(rows), "coins": len(cards),
                "counts": counts, "cards": [card_payload(c) for c in kept], "can_authorize_trade": False}

    def _record(self, card: Card, now: int) -> None:
        """First sight of a coin and each later change of its verdict, for the forward evaluation."""

        coin = card.coin
        summary = card.summary()
        base = {"cmc_id": coin.cmc_id, "symbol": coin.symbol, "name": coin.name, "chain": coin.chain,
                "address": coin.address, "date_added": coin.date_added, "meme": coin.is_meme,
                "price": coin.price, "volume_24h": coin.volume_24h, "fdv": coin.fdv, "at": now, **summary,
                "schema": SCHEMA, "trial": TRIAL, "can_authorize_trade": False}
        if self._verdicts is None:
            self._verdicts = {r["cmc_id"]: r.get("verdict") for r in self.ledger.records()
                              if r.get("event") in ("FIRST_SEEN", "VERDICT") and "cmc_id" in r
                              and r.get("schema") == SCHEMA}
        previous = self._verdicts.get(coin.cmc_id)
        if previous is None:
            self.ledger.append({"id": f"FIRST_SEEN:{SCHEMA_TAG}:{coin.cmc_id}", "event": "FIRST_SEEN", **base})
        elif previous != summary["verdict"]:
            self.ledger.append({"id": f"VERDICT:{SCHEMA_TAG}:{coin.cmc_id}:{now}", "event": "VERDICT",
                                "previous": previous,
                                **base})
        self._verdicts[coin.cmc_id] = summary["verdict"]

    def record_outcomes(self, now: int) -> int:
        """Price 7, 30 and 90 days after first sight, measured within a day of each horizon.

        A horizon is never filled with a later price: past its tolerance it is
        MISSED (explicitly missing), or NO_QUOTE when CoinMarketCap was asked
        inside the window and had no price (a QUOTE_GAP record proves the
        attempt), never zero.
        """

        first, done, gaps = {}, set(), set()
        for record in self.ledger.records():
            if record.get("event") == "FIRST_SEEN" and record.get("schema") == SCHEMA:
                first[record["cmc_id"]] = record
            elif record.get("event") == "OUTCOME" and record.get("schema") == SCHEMA:
                done.add((record["cmc_id"], record["days"]))
            elif record.get("event") == "QUOTE_GAP" and record.get("schema") == SCHEMA:
                gaps.add((record["cmc_id"], record["days"]))
        due = [(cid, d) for cid, r in first.items() for d in OUTCOME_DAYS
               if (cid, d) not in done and now >= int(r["at"]) + d * DAY]
        if not due:
            return 0
        open_ids = sorted({cid for cid, d in due if now - (int(first[cid]["at"]) + d * DAY)
                           <= OUTCOME_TOLERANCE_SECONDS})
        prices = self.cmc.prices(open_ids) if open_ids else {}
        recorded = 0
        for cid, days in due:
            horizon = int(first[cid]["at"]) + days * DAY
            late = now - horizon
            start, price = first[cid].get("price"), prices.get(cid)
            if late <= OUTCOME_TOLERANCE_SECONDS:
                if price is None:
                    self.ledger.append({"id": f"QUOTE_GAP:{SCHEMA_TAG}:{cid}:{days}", "event": "QUOTE_GAP",
                                        "cmc_id": cid,
                                        "days": days, "at": now, "schema": SCHEMA, "trial": TRIAL,
                                        "can_authorize_trade": False})
                    continue                               # ask again until the window closes
                status = "OK"
            else:
                status, price = ("NO_QUOTE" if (cid, days) in gaps else "MISSED"), None
            recorded += 1
            self.ledger.append({
                "id": f"OUTCOME:{SCHEMA_TAG}:{cid}:{days}", "event": "OUTCOME", "cmc_id": cid, "days": days,
                "at": now,
                "horizon_at": horizon, "late_hours": round(late / HOUR, 1), "status": status, "price": price,
                "return_pct": ((price / start - 1.0) * 100.0) if price and start else None,
                "verdict_at_first_sight": first[cid].get("verdict"), "schema": SCHEMA, "trial": TRIAL,
                "can_authorize_trade": False})
        return recorded


def card_payload(card: Card) -> dict[str, Any]:
    coin = card.coin
    return {
        "cmc_id": coin.cmc_id, "name": coin.name, "symbol": coin.symbol, "slug": coin.slug,
        "date_added": coin.date_added, "meme": coin.is_meme, "platform": coin.platform, "chain": coin.chain,
        "address": coin.address, "price": coin.price, "volume_24h": coin.volume_24h, "fdv": coin.fdv,
        "change_24h": coin.change_24h, "verdict": card.verdict, "liquidity_usd": card.liquidity_usd,
        "checked_at": card.checked_at,
        "groups": {g: {"status": card.status(g), "findings": [f.text for f in card.groups.get(g, [])
                                                              if f.status == card.status(g)][:3]}
                   for g in GROUPS},
    }


# ---------------------------------------------------------------------------
# Telegram view (HTML; every external value escaped)
# ---------------------------------------------------------------------------

DISCLAIMER = ("Bu tarama bilinen dolandırıcılık kalıplarını arar. \"Bariz kırmızı bayrak yok\" güvenilir ya da "
              "yükselecek demek değildir; eşikler kaba kurallardır ve isabeti henüz ölçülmedi.")


def _ago(seconds: int) -> str:
    return f"{seconds // 60} dk" if seconds < HOUR else f"{seconds // HOUR} saat"


def _usd(value: Any) -> str:
    amount = _finite(value)
    if amount is None:
        return "?"
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(amount) >= threshold:
            return f"${amount / threshold:.1f}{suffix}"
    return f"${amount:,.0f}"


def render(snapshot: Mapping[str, Any] | None, *, now: int, error: str | None = None, base_rate: str = "",
           enabled: bool = True, max_age: int = 3 * HOUR) -> str:
    from .long_alerts import _num, esc

    head = "🆕 <b>YENİ COİNLER · CoinMarketCap · güvenlik taraması</b>"
    if not enabled:
        return head + "\n" + esc("Tarama kapalı (NEW_COINS_ENABLED=0).")
    if not snapshot:
        reason = f"Henüz tarama yok. Hata: {error}" if error else "İlk tarama bekleniyor."
        return head + "\n" + esc(reason + " Aday yok demek değildir.")
    age = max(0, now - int(snapshot.get("generated_at") or 0))
    cards = [c for c in snapshot.get("cards") or [] if isinstance(c, Mapping)]
    groups = {v: [c for c in cards if c.get("verdict") == v] for v in (NO_RED_FLAG, HEAVY_RISK, DATA_MISSING)}
    counts = _counts(snapshot)
    lines = [head, esc(f"Son {snapshot.get('window_hours', WINDOW_HOURS)} saatte eklenen "
                       f"{snapshot.get('coins', len(cards))} coin · ❌ {counts[HEAVY_RISK]} · "
                       f"❔ {counts[DATA_MISSING]} · ✅ {counts[NO_RED_FLAG]} · tarama {_ago(age)} önce")]
    if age > max_age or error:
        lines.append(esc(f"⚠️ Tarama güncel değil. Hata: {error or 'yok'}"))

    def title(c: Mapping[str, Any]) -> str:
        chain = CHAIN_LABEL.get(str(c.get("chain")), str(c.get("platform") or "kontratsız"))
        meme = " · meme" if c.get("meme") else ""
        return (f"<b>{esc(str(c.get('name'))[:28])} ({esc(str(c.get('symbol'))[:12])})</b> · "
                f"{esc(chain)}{esc(meme)} · {esc(_ago(max(0, now - int(c.get('date_added') or now))))} önce eklendi")

    lines += ["", f"✅ <b>{VERDICT_LABEL[NO_RED_FLAG]}</b> <i>(güvenilir demek değildir)</i>"]
    if not groups[NO_RED_FLAG]:
        lines.append(esc("Bu pencerede yok."))
    for c in groups[NO_RED_FLAG][:4]:
        rows = []
        for g in GROUPS:
            info = (c.get("groups") or {}).get(g) or {}
            status = str(info.get("status") or UNKNOWN)
            rows.append(f"{g:<14}{ICON.get(status, '❔')} {'; '.join(info.get('findings') or [])}"[:70])
        price = c.get("price")
        lines += [title(c), "<pre>" + esc("\n".join(rows)) + "</pre>",
                  esc(f"Fiyat {'$' + _num(price) if price else '?'} · 24s "
                      f"{'%{:+.0f}'.format(c['change_24h']) if c.get('change_24h') is not None else '?'} · "
                      f"hacim {_usd(c.get('volume_24h'))} · FDV {_usd(c.get('fdv'))}")]
    lines += ["", f"❌ <b>{VERDICT_LABEL[HEAVY_RISK]} · uzak dur</b>"]
    if not groups[HEAVY_RISK]:
        lines.append(esc("Bu pencerede yok."))
    for c in groups[HEAVY_RISK][:8]:
        flags = [f for g in GROUPS for f in ((c.get("groups") or {}).get(g) or {}).get("findings") or []
                 if ((c.get("groups") or {}).get(g) or {}).get("status") == FAIL]
        lines.append(f"• {esc(str(c.get('symbol'))[:12])}: {esc('; '.join(flags[:2])[:110])}")
    if counts[HEAVY_RISK] > 8:
        lines.append(esc(f"… ve {counts[HEAVY_RISK] - 8} coin daha"))
    lines += ["", f"❔ <b>{VERDICT_LABEL[DATA_MISSING]}</b> <i>(eksik veri güvenli sayılmaz)</i>"]
    if not groups[DATA_MISSING]:
        lines.append(esc("Bu pencerede yok."))
    for c in groups[DATA_MISSING][:6]:
        missing = [f for g in CRITICAL for f in ((c.get("groups") or {}).get(g) or {}).get("findings") or []
                   if ((c.get("groups") or {}).get(g) or {}).get("status") == UNKNOWN]
        lines.append(f"• {esc(str(c.get('symbol'))[:12])}: {esc((missing[0] if missing else 'veri eksik')[:90])}")
    if counts[DATA_MISSING] > 6:
        lines.append(esc(f"… ve {counts[DATA_MISSING] - 6} coin daha"))
    lines += ["", f"<i>{esc(DISCLAIMER)}</i>"]
    if base_rate:
        lines.append(f"<i>{esc(base_rate)}</i>")
    lines.append(esc("Araştırma taramasıdır: emir yetkisi yok, karar senin."))
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "\n…"


def _counts(snapshot: Mapping[str, Any]) -> dict[str, int]:
    stored = snapshot.get("counts") if isinstance(snapshot.get("counts"), Mapping) else {}
    cards = [c for c in snapshot.get("cards") or [] if isinstance(c, Mapping)]
    out = {}
    for verdict in (NO_RED_FLAG, HEAVY_RISK, DATA_MISSING):
        try:
            out[verdict] = int(stored[verdict])
        except (KeyError, TypeError, ValueError):
            out[verdict] = sum(1 for c in cards if c.get("verdict") == verdict)
    return out


def status_line(snapshot: Mapping[str, Any] | None, *, now: int, enabled: bool, configured: bool,
                error: str | None) -> str:
    if not enabled:
        return "Yeni coinler (CMC): kapalı"
    if not configured:
        return "Yeni coinler (CMC): API anahtarı yok (CMC_API_KEY)"
    if not snapshot:
        return f"Yeni coinler (CMC): henüz tarama yok{' · hata ' + error if error else ''}"
    counts = _counts(snapshot)
    age = max(0, now - int(snapshot.get("generated_at") or 0))
    return (f"Yeni coinler (CMC): {snapshot.get('coins', 0)} coin · ✅ {counts[NO_RED_FLAG]} ❌ {counts[HEAVY_RISK]} "
            f"❔ {counts[DATA_MISSING]} · {age // 60} dk önce" + (f" · hata {error}" if error else ""))


def fresh(snapshot: Mapping[str, Any] | None, *, now: int, max_age: int) -> bool:
    try:
        generated = int((snapshot or {}).get("generated_at") or 0)
    except (TypeError, ValueError):
        return False
    return generated > 0 and 0 <= now - generated <= max_age
