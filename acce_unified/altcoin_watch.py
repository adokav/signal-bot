"""Gate guard for the register's coins: supply, activity, security and liquidity, checked daily.

v2 (2026-10-23). The user's v3.2 report selected the coins; the list lives in
``research/register`` (``acce_unified.register``). This module no longer
screens a 150-coin universe, ranks anything or keeps a forward record. It
runs the mechanical gates the report's thesis-invalidation rules need
(docs/POSITION_LONG.md §5.1) on the register's coins only:

- supply: minted-but-not-circulating share (information: the unlock schedule
  is not in free data, so a large share is REVIEW, never a verdict);
- activity: DefiLlama fees in the last 30 days (altcoins only; meme coins
  have no revenue gate in the report);
- security: DefiLlama hacks (net of returned funds) and the contract's powers
  on the coin's primary chain (GoPlus / RugCheck);
- liquidity: 30-day median daily spot volume on CoinGecko against the
  report's 10 M$ gate.

The gates only demote. A ❌ reaches ``/long`` as a thesis-invalidation block;
a ❔ is information (the report evaluated those fields by hand). Missing or
failed data is REVIEW, never PASS. No order authority anywhere
(``can_authorize_trade`` is always False).

v1's pre-registered forward test of the 150-coin ranking (trial
``b6e0fec86714efc2``) is SUPERSEDED by this change: its universe, ranking and
outcome rules no longer run; its ledger records stay; no result was read.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from . import register as rg
from .text import esc

COINGECKO_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_URL = "https://pro-api.coingecko.com/api/v3"
LLAMA_URL = "https://api.llama.fi"

HOUR = 3_600
DAY = 86_400

# Gates (the report's thresholds; not learned from data)
LOCKED_MAX = 0.05                 # minted but not circulating, as a share of circulating supply
SUPPLY_TOLERANCE = 0.005          # total below circulating by more than this is inconsistent data
VOLUME_MIN_USD = 10_000_000.0     # 30-day median daily spot volume (report G1 / M3)
VOLUME_MIN_DAYS = 20              # distinct complete days among the last 31
VOLUME_MAX_STALE_DAYS = 3         # the newest complete day must be this recent
HACK_MIN_USD = 1_000_000.0        # stolen minus returned
HACK_FAIL_DAYS = 180
HACK_WARN_DAYS = 730
# Schedule
REFRESH_SECONDS = DAY             # §5.1: the hack check is daily; the other gates ride along (22 coins)
RETRY_DELAYS = (30 * 60, HOUR, 2 * HOUR, 4 * HOUR, 6 * HOUR)   # after a failed refresh: 30 min, then doubling
MAX_RETRY_WAIT = 600              # longest Retry-After honoured within one request
SCHEMA = "altcoin-watch/v2"
SUPERSEDED_TRIAL = "b6e0fec86714efc2"   # v1 forward test of the 150-coin ranking; closed unread (docs/ALTCOIN_WATCH.md)

PASS, WARN, FAIL, REVIEW, SKIP, NA = "PASS", "WARN", "FAIL", "REVIEW", "SKIP", "NA"
ICON = {PASS: "✅", WARN: "⚠️", FAIL: "❌", REVIEW: "❔", SKIP: "·", NA: "—"}
PASSED, NEEDS_REVIEW, ELIMINATED = "KAPILARI_GECTI", "INCELEME_GEREKLI", "ELENDI"
STATUS_LABEL = {PASSED: "KAPILARI GEÇTİ", NEEDS_REVIEW: "İNCELEME GEREKLİ", ELIMINATED: "ELENDİ"}
SUPPLY, ACTIVITY, SECURITY, LIQUIDITY = "Arz", "Faaliyet", "Güvenlik", "Likidite"
GATES = (SUPPLY, ACTIVITY, SECURITY, LIQUIDITY)

# CoinGecko platform key -> the chain key acce_unified.new_coins uses for GoPlus / RugCheck
PLATFORMS = (("ethereum", "ethereum"), ("binance-smart-chain", "bsc"), ("base", "base"),
             ("arbitrum-one", "arbitrum"), ("polygon-pos", "polygon"), ("avalanche", "avalanche"),
             ("optimistic-ethereum", "optimism"), ("solana", "solana"))
EVM_FAIL = (("is_honeypot", "honeypot"), ("owner_change_balance", "sahip bakiyeleri değiştirebilir"),
            ("hidden_owner", "gizli sahip var"), ("selfdestruct", "kontrat kendini yok edebilir"),
            ("can_take_back_ownership", "sahiplik geri alınabilir"))
EVM_WARN = (("is_mintable", "basım fonksiyonu var (yönetişim denetimi doğrulanamadı)"),
            ("is_proxy", "kod değiştirilebilir (proxy)"), ("transfer_pausable", "transfer durdurulabilir"),
            ("is_blacklisted", "kara liste yetkisi var"))


class AltcoinDataError(RuntimeError):
    """Provider data that cannot be used (transport, HTTP status, malformed payload). Type-only text."""


# ---------------------------------------------------------------------------
# Small parsers (every provider value is untrusted)
# ---------------------------------------------------------------------------


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
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
    return int(parsed.timestamp()) if parsed.tzinfo is not None else None


@dataclass(frozen=True)
class Coin:
    gecko_id: str
    symbol: str
    name: str
    rank: int | None
    price: float
    market_cap: float
    fdv: float | None
    circulating: float | None
    total_supply: float | None
    max_supply: float | None
    ath: float | None
    ath_date: int | None
    volume_24h: float | None


def parse_market(row: Any) -> Coin | None:
    """One CoinGecko markets row; None when it cannot be identified or priced (never guessed)."""

    if not isinstance(row, Mapping):
        return None
    gecko_id = row.get("id")
    if not isinstance(gecko_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,99}", gecko_id):
        return None
    price, cap = _positive(row.get("current_price")), _positive(row.get("market_cap"))
    if price is None or cap is None:
        return None
    rank = row.get("market_cap_rank")
    return Coin(
        gecko_id=gecko_id, symbol=str(row.get("symbol") or "?").upper()[:20], name=str(row.get("name") or "?")[:60],
        rank=rank if isinstance(rank, int) and not isinstance(rank, bool) and rank > 0 else None,
        price=price, market_cap=cap, fdv=_positive(row.get("fully_diluted_valuation")),
        circulating=_positive(row.get("circulating_supply")), total_supply=_positive(row.get("total_supply")),
        max_supply=_positive(row.get("max_supply")), ath=_positive(row.get("ath")),
        ath_date=_epoch(row.get("ath_date")), volume_24h=_finite(row.get("total_volume")),
    )


# ---------------------------------------------------------------------------
# HTTP and providers
# ---------------------------------------------------------------------------


class _Http:
    def __init__(self, *, timeout: int, session: Any = None, sleep: Callable[[float], None] = time.sleep,
                 retries: int = 3, backoff: float = 30.0) -> None:
        if session is None:
            import requests

            session = requests.Session()
        self.timeout, self.session, self.sleep = timeout, session, sleep
        self.retries, self.backoff = retries, backoff

    def get(self, name: str, url: str, *, params: Mapping[str, Any] | None = None,
            headers: Mapping[str, str] | None = None) -> Any:
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, params=dict(params or {}), headers=dict(headers or {}),
                                            timeout=self.timeout)
            except Exception as exc:                   # exception text can carry URLs and headers
                raise AltcoinDataError(f"{name} transport {type(exc).__name__}") from None
            if response.status_code == 429 and attempt < self.retries:
                self.sleep(self._wait(response, attempt))
                continue
            if response.status_code != 200:
                raise AltcoinDataError(f"{name} http {response.status_code}")
            try:
                return response.json()
            except ValueError:
                raise AltcoinDataError(f"{name} malformed JSON") from None
        raise AltcoinDataError(f"{name} http 429")     # pragma: no cover - the loop always returns or raises

    def _wait(self, response: Any, attempt: int) -> float:
        """The provider's Retry-After when it gives a usable one (capped), else a growing backoff."""

        default = self.backoff * (attempt + 1)
        headers = getattr(response, "headers", None)
        try:
            after = float(headers.get("Retry-After")) if headers is not None else None
        except (TypeError, ValueError, AttributeError):
            after = None
        if after is None or not math.isfinite(after) or after <= 0:
            return default
        return min(float(MAX_RETRY_WAIT), max(default, after))


class CoinGecko:
    """CoinGecko v3. A demo or pro key goes in a header, never in a URL, log or error."""

    def __init__(self, *, demo_key: str = "", pro_key: str = "", timeout: int = 20, session: Any = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        demo_key, pro_key = demo_key.strip(), pro_key.strip()
        self.base = COINGECKO_PRO_URL if pro_key else COINGECKO_URL
        self.headers = {"Accept": "application/json"}
        if pro_key:
            self.headers["x-cg-pro-api-key"] = pro_key
        elif demo_key:
            self.headers["x-cg-demo-api-key"] = demo_key
        self.keyed = bool(pro_key or demo_key)
        # A key has its own 30/min limit. Without one the public limit is shared by everyone on the same IP
        # (a hosting provider's address), so the refresh goes slowly and waits out 429s for longer.
        self.pace = 2.5 if self.keyed else 12.0
        self.sleep = sleep
        self.http = (_Http(timeout=timeout, session=session, sleep=sleep, retries=3, backoff=30.0) if self.keyed
                     else _Http(timeout=timeout, session=session, sleep=sleep, retries=5, backoff=60.0))

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        payload = self.http.get("coingecko", self.base + path, params=params, headers=self.headers)
        self.sleep(self.pace)
        return payload

    def markets(self, page: int, *, category: str | None = None) -> list[Any]:
        params: dict[str, Any] = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250,
                                  "page": page, "sparkline": "false"}
        if category:
            params["category"] = category
        data = self._get("/coins/markets", params)
        if not isinstance(data, list):
            raise AltcoinDataError("coingecko markets payload is not a list")
        return data

    def markets_by_id(self, ids: Sequence[str]) -> list[Any]:
        """Market rows for the given CoinGecko ids (the register's), 200 per request."""

        rows: list[Any] = []
        for k in range(0, len(ids), 200):
            data = self._get("/coins/markets", {"vs_currency": "usd", "ids": ",".join(ids[k:k + 200]),
                                                "per_page": 250, "page": 1, "sparkline": "false"})
            if not isinstance(data, list):
                raise AltcoinDataError("coingecko markets payload is not a list")
            rows += data
        return rows

    def platforms(self) -> dict[str, dict[str, str]]:
        data = self._get("/coins/list", {"include_platform": "true"})
        if not isinstance(data, list):
            raise AltcoinDataError("coingecko coins list is not a list")
        out: dict[str, dict[str, str]] = {}
        for row in data:
            if isinstance(row, Mapping) and isinstance(row.get("id"), str):
                plats = row.get("platforms") if isinstance(row.get("platforms"), Mapping) else {}
                out[row["id"]] = {str(k): str(v) for k, v in plats.items() if k and isinstance(v, str) and v}
        return out

    def chart(self, gecko_id: str) -> Mapping[str, Any]:
        data = self._get(f"/coins/{gecko_id}/market_chart", {"vs_currency": "usd", "days": "365",
                                                              "interval": "daily"})
        if not isinstance(data, Mapping):
            raise AltcoinDataError("coingecko chart payload is not an object")
        return data

    def prices(self, ids: Sequence[str]) -> dict[str, float]:
        """Current USD price per CoinGecko id; ids without a usable price are left out (unknown, not zero)."""

        out: dict[str, float] = {}
        for k in range(0, len(ids), 200):
            data = self._get("/simple/price", {"ids": ",".join(ids[k:k + 200]), "vs_currencies": "usd"})
            if not isinstance(data, Mapping):
                raise AltcoinDataError("coingecko price payload is not an object")
            for key, row in data.items():
                price = _positive(row.get("usd")) if isinstance(row, Mapping) else None
                if price is not None:
                    out[str(key)] = price
        return out


class DefiLlama:
    """DefiLlama's free API: protocol and chain mapping, fees, hacks."""

    def __init__(self, *, timeout: int = 60, session: Any = None, sleep: Callable[[float], None] = time.sleep):
        self.http = _Http(timeout=timeout, session=session, sleep=sleep, retries=2, backoff=20.0)

    def _get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self.http.get("defillama", LLAMA_URL + path, params=params)

    def lite(self) -> Mapping[str, Any]:
        data = self._get("/lite/protocols2")
        if not isinstance(data, Mapping) or not isinstance(data.get("protocols"), list):
            raise AltcoinDataError("defillama protocols payload unusable")
        return data

    def chains(self) -> list[Any]:
        data = self._get("/v2/chains")
        if not isinstance(data, list):
            raise AltcoinDataError("defillama chains payload is not a list")
        return data

    def overview(self, data_type: str) -> list[Any]:
        data = self._get("/overview/fees", {"excludeTotalDataChart": "true",
                                            "excludeTotalDataChartBreakdown": "true", "dataType": data_type})
        rows = data.get("protocols") if isinstance(data, Mapping) else None
        if not isinstance(rows, list):
            raise AltcoinDataError(f"defillama {data_type} payload unusable")
        return rows

    def hacks(self) -> list[Any]:
        data = self._get("/hacks")
        if not isinstance(data, list):
            raise AltcoinDataError("defillama hacks payload is not a list")
        return data


# ---------------------------------------------------------------------------
# DefiLlama -> CoinGecko identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LlamaIndex:
    protocol_gecko: Mapping[str, str]          # defillamaId -> own gecko id
    protocol_parent: Mapping[str, str]         # defillamaId -> parent id
    parent_gecko: Mapping[str, str]            # parent id -> gecko id
    chain_gecko: Mapping[str, str]             # lower-case chain name -> gecko id of the chain's token
    gas_tokens: frozenset[str]                 # gecko ids that pay gas on a chain: native coins

    def gecko_of(self, entry: Mapping[str, Any]) -> str | None:
        """The CoinGecko id an overview entry's fees belong to; None when DefiLlama gives no link."""

        did = str(entry.get("defillamaId") or "")
        if entry.get("protocolType") == "chain" or did.startswith("chain#"):
            for key in ("name", "displayName"):
                gecko = self.chain_gecko.get(str(entry.get(key) or "").lower())
                if gecko:
                    return gecko
            return None
        parent = self.protocol_parent.get(did)
        return (self.parent_gecko.get(parent) if parent else None) or self.protocol_gecko.get(did)


def build_llama_index(lite: Mapping[str, Any], chains: Sequence[Any]) -> LlamaIndex:
    protocol_gecko: dict[str, str] = {}
    protocol_parent: dict[str, str] = {}
    for p in lite.get("protocols") or []:
        if not isinstance(p, Mapping):
            continue
        did = str(p.get("defillamaId") or "")
        if not did:
            continue
        gecko = p.get("geckoId") or p.get("gecko_id")
        if isinstance(gecko, str) and gecko:
            protocol_gecko[did] = gecko
        if isinstance(p.get("parentProtocol"), str):
            protocol_parent[did] = p["parentProtocol"]
    parent_gecko = {str(p["id"]): p["gecko_id"] for p in lite.get("parentProtocols") or []
                    if isinstance(p, Mapping) and isinstance(p.get("id"), str) and isinstance(p.get("gecko_id"), str)
                    and p["gecko_id"]}
    chain_gecko: dict[str, str] = {}
    gas: set[str] = set()
    for c in chains or []:
        if not isinstance(c, Mapping) or not isinstance(c.get("name"), str):
            continue
        if isinstance(c.get("gecko_id"), str) and c["gecko_id"]:
            chain_gecko[c["name"].lower()] = c["gecko_id"]
        gas_token = c.get("gasTokenGeckoId") or c.get("gecko_id")
        if isinstance(gas_token, str) and gas_token:
            gas.add(gas_token)
    return LlamaIndex(protocol_gecko, protocol_parent, parent_gecko, chain_gecko, frozenset(gas))


@dataclass
class Totals:
    d30: float = 0.0
    y1: float = 0.0
    monthly: float = 0.0
    entries: int = 0
    y1_known: bool = True
    monthly_known: bool = True


def aggregate(rows: Sequence[Any], index: LlamaIndex) -> dict[str, Totals]:
    """Sum an overview's 30-day, 1-year and monthly-average totals per CoinGecko id."""

    out: dict[str, Totals] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        gecko = index.gecko_of(row)
        if not gecko:
            continue
        t = out.setdefault(gecko, Totals())
        t.entries += 1
        t.d30 += max(0.0, _finite(row.get("total30d")) or 0.0)
        y1 = _finite(row.get("total1y"))
        if y1 is None:
            t.y1_known = False
        else:
            t.y1 += max(0.0, y1)
        monthly = _finite(row.get("monthlyAverage1y"))
        if monthly is None and y1 is None:
            t.monthly_known = False                    # recent fees without their history would inflate usage
        t.monthly += max(0.0, monthly if monthly is not None else (y1 or 0.0) / 12.0)
    return out


def hacks_by_gecko(rows: Sequence[Any], index: LlamaIndex
                   ) -> dict[str, list[tuple[int | None, float | None, str]]]:
    """(date, stolen minus returned USD, target type) per CoinGecko id, from protocol and parent links only.

    A mapped record with a missing date or amount is kept as unknown (None),
    never dropped: missing risk evidence is not absence of risk.
    """

    out: dict[str, list[tuple[int | None, float | None, str]]] = {}
    for row in rows or []:
        if not isinstance(row, Mapping):
            continue
        date, amount = row.get("date"), _finite(row.get("amount"))
        date = date if isinstance(date, int) and not isinstance(date, bool) else None
        returned = max(0.0, _finite(row.get("returnedFunds")) or 0.0)
        did = str(row.get("defillamaId") or "")
        parent = row.get("parentProtocolId") or index.protocol_parent.get(did)
        gecko = (index.parent_gecko.get(str(parent)) if parent else None) or index.protocol_gecko.get(did)
        if gecko:
            out.setdefault(gecko, []).append((date, None if amount is None else max(0.0, amount - returned),
                                              str(row.get("targetType") or "").strip().lower()))
    return out


# ---------------------------------------------------------------------------
# The gates (pure: data in, gate findings and a status out)
# ---------------------------------------------------------------------------


@dataclass
class Card:
    coin: Coin
    gates: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)

    def add(self, gate: str, status: str, text: str) -> None:
        self.gates.setdefault(gate, []).append((status, text))

    def gate(self, gate: str) -> str:
        """FAIL beats REVIEW beats WARN beats PASS; NA when the gate does not apply; SKIP when it never ran."""

        statuses = [s for s, _ in self.gates.get(gate, [])]
        if NA in statuses:
            return NA
        for level in (FAIL, REVIEW, WARN, SKIP):
            if level in statuses:
                return level
        return PASS if statuses else SKIP

    @property
    def status(self) -> str:
        states = [self.gate(g) for g in GATES]
        if FAIL in states:
            return ELIMINATED
        if REVIEW in states or SKIP in states:
            return NEEDS_REVIEW
        return PASSED

    def reasons(self, *levels: str) -> list[str]:
        return [text for g in GATES for status, text in self.gates.get(g, []) if status in levels]

    def fails(self) -> dict[str, str]:
        return {g: next(t for s, t in self.gates[g] if s == FAIL) for g in GATES if self.gate(g) == FAIL}


def screen_supply(card: Card) -> None:
    coin = card.coin
    if coin.circulating is None or coin.total_supply is None:
        card.add(SUPPLY, REVIEW, "dolaşım ya da toplam arz verisi yok")
        return
    locked = (coin.total_supply - coin.circulating) / coin.circulating
    if locked < -SUPPLY_TOLERANCE:
        card.add(SUPPLY, REVIEW, "toplam arz dolaşımdakinden küçük (veri tutarsız)")
        return
    locked = max(0.0, locked)                          # a rounding gap, not a negative overhang
    card.info["locked_share"] = locked
    if coin.max_supply and coin.max_supply > coin.total_supply * 1.001:
        card.info["unminted_share"] = (coin.max_supply - coin.total_supply) / coin.circulating
    if locked <= LOCKED_MAX:
        card.add(SUPPLY, PASS, f"dolaşım dışı arz %{locked * 100:.1f}")
    else:
        card.add(SUPPLY, REVIEW, f"dolaşım dışı arz %{locked * 100:.0f}; açılış takvimi ücretsiz veride yok")


def screen_activity(card: Card, fees: Totals | None, *, kind: str = rg.ALTCOIN) -> None:
    if kind == rg.MEME:
        card.add(ACTIVITY, NA, "meme coin: gelir kapısı yok (rapor M1–M3)")
    elif fees is None:
        card.add(ACTIVITY, REVIEW, "DefiLlama'da ücret verisi eşleşmedi")
    elif fees.d30 <= 0:
        card.add(ACTIVITY, FAIL, "son 30 günde ücret geliri yok")
    else:
        card.add(ACTIVITY, PASS, f"30 günde ${fees.d30:,.0f} ücret")


def screen_hacks(card: Card, hacks: Sequence[tuple[int | None, float | None, str]], *, now: int,
                 native: bool = False) -> bool:
    """Adds the hack finding; True when a recent hack eliminates the coin.

    A chain's own coin counts only attacks on the chain itself: DefiLlama files
    some applications under the chain's parent, and an application hack does
    not compromise the chain's coin. A token counts every hack of its protocol.
    """

    hacks = [(date, net) for date, net, target in hacks if not native or target == "chain"]
    dated = [(date, net) for date, net in hacks if date is not None]
    recent = [net for date, net in dated if 0 <= now - date <= HACK_FAIL_DAYS * DAY]
    older = [net for date, net in dated if HACK_FAIL_DAYS * DAY < now - date <= HACK_WARN_DAYS * DAY]
    if any(net is not None and net >= HACK_MIN_USD for net in recent):
        card.add(SECURITY, FAIL, f"son {HACK_FAIL_DAYS} günde hack: "
                                 f"${max(n for n in recent if n is not None):,.0f} kayıp")
        return True
    if len(dated) < len(hacks):
        card.add(SECURITY, REVIEW, "tarihi bilinmeyen hack kaydı")
    if any(net is None for net in recent):
        card.add(SECURITY, REVIEW, f"son {HACK_FAIL_DAYS} günde tutarı bilinmeyen hack")
    big = [net for net in older if net is not None and net >= HACK_MIN_USD]
    if big:
        card.add(SECURITY, WARN, f"son 2 yılda hack: ${max(big):,.0f} kayıp")
    if any(net is None for net in older):
        card.add(SECURITY, WARN, "son 2 yılda tutarı bilinmeyen hack")
    return False


def contract_of(gecko_id: str, platforms: Mapping[str, Mapping[str, str]], index: LlamaIndex
                ) -> tuple[str, str | None, str | None]:
    """("native", None, None) | ("token", chain, address) | ("unsupported", None, None).

    Only the coin's primary platform is checked: CoinGecko lists it first. A
    bridged copy on another chain is a different contract with other powers,
    so a token whose primary chain is unsupported is not checked elsewhere.
    """

    plats = platforms.get(gecko_id)
    if gecko_id in index.gas_tokens or plats is not None and not plats:
        return "native", None, None
    if not plats:
        return "unsupported", None, None
    key, address = next(iter(plats.items()))
    chain = dict(PLATFORMS).get(key)
    return ("token", chain, address.strip()) if chain else ("unsupported", None, None)


def screen_contract(card: Card, kind: str, chain: str | None, payload: Any, *, error: bool = False) -> None:
    if kind == "native":
        card.add(SECURITY, PASS, "ana ağ coini: kontrat yetkisi yok")
        return
    if kind != "token" or chain is None:
        card.add(SECURITY, REVIEW, "kontrat güvenliği bu zincirde doğrulanamıyor")
        return
    if error or not isinstance(payload, Mapping):
        card.add(SECURITY, REVIEW, "kontrat güvenlik verisi alınamadı")
        return
    if chain == "solana":
        token = payload.get("token") if isinstance(payload.get("token"), Mapping) else None
        if token is None or "freezeAuthority" not in token or "mintAuthority" not in token:
            card.add(SECURITY, REVIEW, "basım ve dondurma yetkisi doğrulanamadı")
            return
        if token.get("freezeAuthority"):
            card.add(SECURITY, FAIL, "cüzdan dondurulabilir (freeze yetkisi açık)")
        if token.get("mintAuthority"):
            card.add(SECURITY, WARN, "basım yetkisi açık (yönetişim denetimi doğrulanamadı)")
        if not card.gates.get(SECURITY) or all(s == WARN for s, _ in card.gates[SECURITY]):
            card.add(SECURITY, PASS, "dondurma yetkisi kapalı")
        return
    flags = {k: str(payload.get(k)).strip() if payload.get(k) is not None else None
             for k, _ in EVM_FAIL + EVM_WARN}
    for key, text in EVM_FAIL:
        if flags[key] == "1":
            card.add(SECURITY, FAIL, text)
    for key, text in EVM_WARN:
        if flags[key] == "1":
            card.add(SECURITY, WARN, text)
    if any(flags[k] not in ("0", "1") for k, _ in EVM_FAIL):
        card.add(SECURITY, REVIEW, "proxy: yetkiler uygulama kontratında, doğrulanamadı" if flags["is_proxy"] == "1"
                 else "kontrat yetkileri doğrulanamadı")
    elif not any(flags[k] == "1" for k, _ in EVM_FAIL):
        card.add(SECURITY, PASS, "kontratta tehlikeli yetki yok")
    if any(flags[k] not in ("0", "1") for k, _ in EVM_WARN):
        card.add(SECURITY, WARN, "basım, proxy, durdurma ya da kara liste alanı okunamadı")


def _series(chart: Mapping[str, Any], key: str) -> list[tuple[int, float]]:
    """One point per UTC day (the latest of the day), oldest first; malformed points are dropped."""

    rows = chart.get(key) if isinstance(chart.get(key), list) else []
    by_day: dict[int, tuple[int, float]] = {}
    for row in rows:
        if isinstance(row, list) and len(row) == 2:
            ts, value = _finite(row[0]), _finite(row[1])
            if ts is not None and value is not None:
                t = int(ts // 1000)
                if t // DAY not in by_day or by_day[t // DAY][0] <= t:
                    by_day[t // DAY] = (t, value)
    return [by_day[d] for d in sorted(by_day)]


def screen_liquidity(card: Card, chart: Mapping[str, Any] | None, *, now: int) -> None:
    """The liquidity gate from one 365-day daily chart: 30-day median of complete UTC days."""

    if not isinstance(chart, Mapping):
        card.add(LIQUIDITY, REVIEW, "hacim verisi alınamadı")
        return
    days = [(ts, v) for ts, v in _series(chart, "total_volumes")
            if now - 31 * DAY <= ts < now - DAY and v >= 0]          # complete days of the last month
    volumes = [v for _, v in days]
    if len(volumes) < VOLUME_MIN_DAYS or now - days[-1][0] > VOLUME_MAX_STALE_DAYS * DAY:
        card.add(LIQUIDITY, REVIEW, "30 günlük hacim verisi eksik ya da eski")
        return
    median = statistics.median(volumes)
    card.info["volume_median_30d"] = median
    card.add(LIQUIDITY, PASS if median >= VOLUME_MIN_USD else FAIL, f"30 günlük medyan hacim ${median / 1e6:,.1f}M")


# ---------------------------------------------------------------------------
# The guard: the register's coins -> gates, daily
# ---------------------------------------------------------------------------


class AltcoinWatch:
    def __init__(self, gecko: CoinGecko, llama: DefiLlama, security: Any, ledger: Any, *,
                 register: rg.Register, sleep: Callable[[float], None] = time.sleep) -> None:
        self.gecko, self.llama, self.security, self.ledger = gecko, llama, security, ledger
        self.register, self.sleep = register, sleep

    def refresh(self, now: int, previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """One pass over the register's coins. Raises when a core source fails; a coin whose own look-ups
        fail is REVIEW. Status changes against the previous snapshot are written to the ledger."""

        coins_by_id = {c.gecko_id: c for c in self.register.coins}
        rows = {c.gecko_id: c for c in (parse_market(r) for r in self.gecko.markets_by_id(list(coins_by_id)))
                if c is not None}
        index = build_llama_index(self.llama.lite(), self.llama.chains())
        fees = aggregate(self.llama.overview("dailyFees"), index)
        hacks = hacks_by_gecko(self.llama.hacks(), index)
        platforms = self.gecko.platforms()
        cards: list[dict[str, Any]] = []
        for entry in self.register.coins:
            coin = rows.get(entry.gecko_id)
            if coin is None:                                   # unpriced or missing on CoinGecko: everything unknown
                cards.append(_unknown_card(entry, "CoinGecko piyasa verisi yok"))
                continue
            card = Card(coin=coin)
            contract = contract_of(coin.gecko_id, platforms, index)
            screen_supply(card)
            screen_activity(card, fees.get(coin.gecko_id), kind=entry.kind)
            hacked = screen_hacks(card, hacks.get(coin.gecko_id, ()), now=now, native=contract[0] == "native")
            if not hacked:
                self._contract(card, contract)
            self._liquidity(card, now)
            card.info["contract"] = contract[0] if contract[0] != "token" else f"token:{contract[1]}"
            cards.append(card_payload(card, entry))
        counts = {s: sum(1 for c in cards if c["status"] == s) for s in (PASSED, NEEDS_REVIEW, ELIMINATED)}
        snapshot = {"generated_at": now, "schema": SCHEMA, "register": self.register.summary(),
                    "superseded_trial": SUPERSEDED_TRIAL, "counts": counts, "coingecko_keyed": self.gecko.keyed,
                    "cards": cards, "can_authorize_trade": False}
        self._record_changes(snapshot, previous)
        return snapshot

    def _contract(self, card: Card, contract: tuple[str, str | None, str | None]) -> None:
        kind, chain, address = contract
        payload, error = None, False
        if kind == "token" and chain and address:
            try:
                payload = (self.security.rugcheck(address) if chain == "solana"
                           else self.security.goplus(chain, address))
            except Exception:                          # provider failure: unknown, never safe
                error = True
            self.sleep(1.0)
        screen_contract(card, kind, chain, payload, error=error)

    def _liquidity(self, card: Card, now: int) -> None:
        try:
            chart: Mapping[str, Any] | None = self.gecko.chart(card.coin.gecko_id)
        except AltcoinDataError:
            chart = None
        screen_liquidity(card, chart, now=now)

    def _record_changes(self, snapshot: Mapping[str, Any], previous: Mapping[str, Any] | None) -> int:
        """A GATE event when a coin's status or its set of ❌ gates differs from the previous snapshot
        (the first snapshot records every coin once). Append-only; ids carry the day."""

        before = {c["symbol"]: c for c in (previous or {}).get("cards") or [] if isinstance(c, Mapping)}
        day = datetime.fromtimestamp(int(snapshot["generated_at"]), tz=timezone.utc).strftime("%Y-%m-%d")
        written = 0
        for card in snapshot["cards"]:
            old = before.get(card["symbol"])
            if old is not None and old.get("status") == card["status"] and old.get("fails") == card["fails"]:
                continue
            event = {"id": f"GATE:{SCHEMA}:{day}:{card['symbol']}", "event": "GATE", "day": day,
                     "at": snapshot["generated_at"], "symbol": card["symbol"], "gecko_id": card["id"],
                     "status": card["status"], "fails": card["fails"],
                     "gates": {g: v["status"] for g, v in card["gates"].items()},
                     "previous_status": old.get("status") if old else None,
                     "register_sha256": snapshot["register"]["sha256"], "schema": SCHEMA,
                     "can_authorize_trade": False}
            if self.ledger.append(event):
                written += 1
        return written


def _unknown_card(entry: rg.Coin, reason: str) -> dict[str, Any]:
    return {"id": entry.gecko_id, "symbol": entry.symbol, "name": entry.name, "kind": entry.kind,
            "basket": entry.basket, "basket_label": entry.basket_label, "rank": entry.rank,
            "price": None, "market_cap": None, "fdv": None, "status": NEEDS_REVIEW, "fails": {},
            "gates": {g: {"status": REVIEW, "notes": [reason]} for g in GATES}, "info": {}}


def card_payload(card: Card, entry: rg.Coin) -> dict[str, Any]:
    coin = card.coin

    def rounded(value: Any) -> Any:
        return round(value, 6) if isinstance(value, float) else value

    return {
        "id": coin.gecko_id, "symbol": entry.symbol, "name": entry.name, "kind": entry.kind, "basket": entry.basket,
        "basket_label": entry.basket_label, "rank": entry.rank, "price": coin.price, "market_cap": coin.market_cap,
        "fdv": coin.fdv, "status": card.status, "fails": card.fails(),
        "gates": {g: {"status": card.gate(g), "notes": [t for _, t in card.gates.get(g, [])][:3]} for g in GATES},
        "info": {k: rounded(v) for k, v in card.info.items()},
    }


def gates_of(snapshot: Mapping[str, Any] | None) -> dict[str, dict[str, Any]] | None:
    """What ``/long`` reads: per symbol the status, the ❌ gates with their reasons and the check time.
    None when there is no snapshot (the long watch then treats the gates as unknown)."""

    if not snapshot or not isinstance(snapshot.get("cards"), list):
        return None
    at = int(snapshot.get("generated_at") or 0)
    out: dict[str, dict[str, Any]] = {}
    for card in snapshot["cards"]:
        if isinstance(card, Mapping) and isinstance(card.get("symbol"), str):
            out[card["symbol"].upper()] = {"status": card.get("status"), "fails": dict(card.get("fails") or {}),
                                           "at": at}
    return out


def next_attempt(failures: int, failed_at: int) -> int:
    """When to retry after the n-th consecutive failed refresh: 30 min, then doubling up to 6 hours."""

    return failed_at + RETRY_DELAYS[min(max(failures, 1), len(RETRY_DELAYS)) - 1]


def explain(error: str | None, *, keyed: bool) -> str:
    if error and "429" in error and not keyed:
        return ("CoinGecko hız sınırı: anahtarsız genel API paylaşılan IP'de sık sık 429 verir. "
                "COINGECKO_DEMO_API_KEY (ücretsiz Demo anahtarı) eklenince sınır ayrı ve tarama hızlı olur.")
    return ""


# ---------------------------------------------------------------------------
# Telegram view (HTML; every external value escaped)
# ---------------------------------------------------------------------------

DISCLAIMER = ("Kapılar yalnız düşürür: ❌ /long'daki dilim sinyalini keser (tez geçersizliği); ❔ bilgidir, rapor o "
              "alanları elle değerlendirdi. Liste rapordan gelir; bot seçmez ve yükseltmez.")


def _age(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < HOUR:
        return f"{seconds // 60} dk"
    if seconds < DAY:
        return f"{seconds // HOUR} sa"
    return f"{seconds // DAY} gün"


def _retry_text(retry_at: int | None, now: int) -> str:
    if not retry_at or retry_at <= now:
        return ""
    return f" Sonraki deneme {datetime.fromtimestamp(retry_at, tz=timezone.utc):%H:%M} UTC."


def _share(value: Any) -> str:
    number = _finite(value)
    return "?" if number is None else f"%{number * 100:,.1f}"


def _usd(value: Any) -> str:
    amount = _finite(value)
    if amount is None:
        return "?"
    for unit, div in (("Mr", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(amount) >= div:
            return f"${amount / div:,.1f}{unit}"
    return f"${amount:,.0f}"


def _icons(card: Mapping[str, Any]) -> str:
    return "".join(ICON.get(str(((card.get("gates") or {}).get(g) or {}).get("status")), "·") for g in GATES)


def render(snapshot: Mapping[str, Any] | None, *, now: int, error: str | None = None, enabled: bool = True,
           max_age: int = 3 * REFRESH_SECONDS, retry_at: int | None = None, keyed: bool = True) -> str:
    head = "🛡 <b>KAPI BEKÇİSİ · v3.2 listesi · günlük kontrol</b>"
    if not enabled:
        return head + "\n" + esc("Modül kapalı (ALTCOIN_WATCH_ENABLED=0).")
    if not snapshot:
        if not error:
            return head + "\n" + esc("İlk kontrol sürüyor (anahtarla birkaç dakika, anahtarsız 10 dakikaya kadar).")
        lines = [head, esc(f"İlk kontrol yapılamadı. Hata: {error}.{_retry_text(retry_at, now)}")]
        if explain(error, keyed=keyed):
            lines.append(esc(explain(error, keyed=keyed)))
        return "\n".join(lines)
    cards = [c for c in snapshot.get("cards") or [] if isinstance(c, Mapping)]
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), Mapping) else {}
    reg = snapshot.get("register") if isinstance(snapshot.get("register"), Mapping) else {}
    age = max(0, now - int(snapshot.get("generated_at") or 0))
    lines = [head, esc(f"Kayıt {reg.get('version')} ({reg.get('report_date')}) · {len(cards)} coin · kontrol "
                       f"{_age(age)} önce · ✅ {counts.get(PASSED, 0)} ❔ {counts.get(NEEDS_REVIEW, 0)} "
                       f"❌ {counts.get(ELIMINATED, 0)}")]
    if age > max_age or error:
        lines.append(esc(f"⚠️ Son kontrol gösteriliyor; yenileme yapılamadı. Hata: {error or 'yok'}."
                         f"{_retry_text(retry_at, now)}"))
        if explain(error, keyed=keyed):
            lines.append(esc(explain(error, keyed=keyed)))
    for kind, title in ((rg.ALTCOIN, "Altcoinler"), (rg.MEME, "Meme coinler")):
        rows = [f"{'Coin':<8}{'A F G L':<9}{'Sepet / ❌ nedeni'}"]
        for c in [c for c in cards if c.get("kind") == kind]:
            fails = c.get("fails") or {}
            tail = ("❌ " + "; ".join(str(v) for v in fails.values())[:40]) if fails else str(c.get("basket_label"))[:22]
            rows.append(f"{str(c.get('symbol'))[:7]:<8}{_icons(c):<9}{tail}")
        lines += ["", f"<b>{title}</b>", "<pre>" + esc("\n".join(rows)) + "</pre>"]
    review: dict[str, int] = {}
    for c in cards:
        for g in GATES:
            info = (c.get("gates") or {}).get(g) or {}
            if info.get("status") == REVIEW:
                key = re.sub(r"%\d+(\.\d+)?", f"%{LOCKED_MAX * 100:.0f}'in üzerinde", (info.get("notes") or ["?"])[-1])
                review[key] = review.get(key, 0) + 1
    if review:
        lines.append(esc("❔ " + " · ".join(f"{n} coin: {t}" for t, n in sorted(review.items(), key=lambda kv: -kv[1])[:4])))
    lines += ["", esc("A arz · F faaliyet (meme'de yok) · G güvenlik (hack, kontrat) · L likidite (30 gün medyan ≥ 10 M$). "
                      "Tek coin: /altcoin <sembol>"), f"<i>{esc(DISCLAIMER)}</i>", esc("Emir yetkisi yok, karar senin.")]
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "\n…"


def find(snapshot: Mapping[str, Any] | None, query: str) -> list[Mapping[str, Any]]:
    """Cards whose symbol or CoinGecko id equals the query (case-insensitive)."""

    q = query.strip().lower()
    return [c for c in (snapshot or {}).get("cards") or [] if isinstance(c, Mapping)
            and q in (str(c.get("symbol") or "").lower(), str(c.get("id") or "").lower())]


def render_card(card: Mapping[str, Any], *, now: int, generated_at: int | None = None) -> str:
    from .text import _num

    info = card.get("info") or {}
    status = str(card.get("status"))
    icon = {PASSED: "✅", NEEDS_REVIEW: "❔", ELIMINATED: "❌"}.get(status, "❔")
    lines = [f"🛡 <b>{esc(str(card.get('name'))[:28])} ({esc(str(card.get('symbol'))[:12])})</b> · "
             f"{esc(str(card.get('basket_label')))}" + (f" · rapor sırası {card['rank']}" if card.get("rank") else ""),
             f"{icon} <b>{STATUS_LABEL.get(status, status)}</b>"]
    rows = []
    for g in GATES:
        info_g = (card.get("gates") or {}).get(g) or {}
        notes = info_g.get("notes") or ["değerlendirilmedi"]
        rows.append(f"{g:<10}{ICON.get(str(info_g.get('status')), '·')} {notes[0][:80]}")
        rows += [f"{'':<12}{n[:80]}" for n in notes[1:3]]
    lines.append("<pre>" + esc("\n".join(rows)) + "</pre>")
    extra = []
    if info.get("locked_share") is not None:
        extra.append(f"Dolaşım dışı   {_share(info.get('locked_share'))} (basılmış, dolaşımda değil)")
    if info.get("unminted_share") is not None:
        extra.append(f"Basılmamış     {_share(info.get('unminted_share'))} (maksimum arza kadar)")
    if info.get("volume_median_30d") is not None:
        extra.append(f"Spot hacim     {_usd(info.get('volume_median_30d'))} (30 gün medyan, CoinGecko)")
    if info.get("contract"):
        extra.append(f"Kontrat        {str(info['contract'])[:30]}")
    price = card.get("price")
    extra.append(f"Fiyat          ${_num(price) if price else '?'} · piyasa değeri {_usd(card.get('market_cap'))} · "
                 f"FDV {_usd(card.get('fdv'))}")
    lines.append("<pre>" + esc("\n".join(extra)) + "</pre>")
    if generated_at:
        lines.append(esc(f"Kontrol {_age(max(0, now - generated_at))} önce. Pozisyon tarafı: /long {card.get('symbol')}"))
    lines += [esc("Elle bakılacaklar: ekip, ortaklıklar, yol haritası, yönetişim, kilit takvimi. Bot bunları okumaz."),
              f"<i>{esc(DISCLAIMER)}</i>", esc("Emir yetkisi yok, karar senin.")]
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "\n…"


def status_line(snapshot: Mapping[str, Any] | None, *, now: int, enabled: bool, error: str | None,
                keyed: bool | None = None) -> str:
    if not enabled:
        return "Kapı bekçisi: kapalı"
    keyed = bool(snapshot.get("coingecko_keyed")) if keyed is None and snapshot else keyed
    key = " · CoinGecko anahtarı yok (yavaş)" if keyed is False else ""
    if not snapshot:
        return f"Kapı bekçisi: henüz kontrol yok{key}{' · hata ' + error if error else ''}"
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), Mapping) else {}
    age = max(0, now - int(snapshot.get("generated_at") or 0))
    return (f"Kapı bekçisi: {len(snapshot.get('cards') or [])} coin · ✅ {counts.get(PASSED, 0)} "
            f"❔ {counts.get(NEEDS_REVIEW, 0)} ❌ {counts.get(ELIMINATED, 0)} · {_age(age)} önce{key}"
            + (f" · hata {error}" if error else ""))


def fresh(snapshot: Mapping[str, Any] | None, *, now: int, max_age: int) -> bool:
    try:
        generated = int((snapshot or {}).get("generated_at") or 0)
    except (TypeError, ValueError):
        return False
    return generated > 0 and 0 <= now - generated <= max_age
