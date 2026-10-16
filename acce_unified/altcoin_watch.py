"""Altcoin watch: a long-horizon fundamental screen of the largest altcoins (docs/ALTCOIN_WATCH.md).

The design is the synthesis of two selection models the user supplied
(2026-10-16). Both models scored supply pressure, value accrual, usage and
valuation with hand-picked weights. This module keeps what can be measured
from free, reproducible data and drops what cannot:

- hard gates first (supply not yet in circulation, recent hacks and contract
  powers, liquidity, measurable economic activity);
- then three measures that do not repeat each other, ranked with equal
  weight: net holder yield (holder revenue minus dilution), valuation (FDV
  over revenue) and usage momentum (recent fees against the year's average);
- the ATH arithmetic of both models as information, never as a score.

Fail closed: missing, unverifiable or unmapped data never passes a gate. It
leaves the coin "İNCELEME GEREKLİ", and a missing measure ranks last. Names
and symbols are untrusted external text: they are escaped for display and
never interpreted. Every lookup error carries its type only, never a URL,
header or provider message.

The ranking is not evidence: the weights are not learned from data and no
accuracy has been measured. A pre-registered forward record (the ledger)
measures it. Research only; no order authority (``can_authorize_trade`` is
False).
"""

from __future__ import annotations

import math
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .cex import is_stable_or_synthetic


COINGECKO_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_URL = "https://pro-api.coingecko.com/api/v3"
LLAMA_URL = "https://api.llama.fi"

HOUR = 3_600
DAY = 86_400
UNIVERSE_SIZE = 150               # altcoins by market cap; BTC is the benchmark, not a member
MARKET_PAGES = 3                  # 250 rows each, until the universe is full
# CoinGecko categories that are claims on another asset, not ordinary crypto assets.
EXCLUDED_CATEGORIES = ("stablecoins", "wrapped-tokens", "liquid-staking-tokens", "liquid-restaking-tokens",
                       "bridged-tokens", "tokenized-gold", "tokenized-products", "synthetic-dollar")

# Gates (rules of thumb from the two models; not learned from data)
LOCKED_MAX = 0.05                 # minted but not circulating, as a share of circulating supply
SUPPLY_TOLERANCE = 0.005          # total below circulating by more than this is inconsistent data
VOLUME_MIN_USD = 10_000_000.0     # 30-day median daily volume
VOLUME_MIN_DAYS = 20              # distinct complete days among the last 31
VOLUME_MAX_STALE_DAYS = 3         # the newest complete day must be this recent
HACK_MIN_USD = 1_000_000.0        # stolen minus returned
HACK_FAIL_DAYS = 180
HACK_WARN_DAYS = 730
MAX_DEEP_CHECKS = 80              # coins that reach the per-coin look-ups in one refresh
# Measures
DILUTION_JUMP = 0.15              # a one-day move in implied supply this large is not trusted
CHART_MIN_DAYS = 330
CHART_MIN_POINTS = 300
CHART_EDGE_POINTS = 7
# Schedule and forward record
REFRESH_SECONDS = 7 * DAY
OUTCOME_DAYS = (182, 365, 730)
OUTCOME_TOLERANCE_SECONDS = 2 * DAY
SCHEMA = "altcoin-watch/v1"
SCHEMA_TAG = "v1"

PASS, WARN, FAIL, REVIEW, SKIP = "PASS", "WARN", "FAIL", "REVIEW", "SKIP"
ICON = {PASS: "✅", WARN: "⚠️", FAIL: "❌", REVIEW: "❔", SKIP: "·"}
PASSED, NEEDS_REVIEW, ELIMINATED = "KAPILARI_GECTI", "INCELEME_GEREKLI", "ELENDI"
STATUS_LABEL = {PASSED: "KAPILARI GEÇTİ", NEEDS_REVIEW: "İNCELEME GEREKLİ", ELIMINATED: "ELENDİ"}
SUPPLY, ACTIVITY, SECURITY, LIQUIDITY = "Arz", "Faaliyet", "Güvenlik", "Likidite"
GATES = (SUPPLY, ACTIVITY, SECURITY, LIQUIDITY)
MEASURES = ("net_yield", "valuation", "usage")
HIGHER_IS_BETTER = {"net_yield": True, "valuation": False, "usage": True}

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

# Forward evaluation, pre-registered before the module produced any record (research/trials/registry.jsonl).
# A change to the universe, a gate, a measure, the ranking or the outcome rule needs a new SCHEMA and trial.
TRIAL_FAMILY = "altcoin_watch_forward"
TRIAL_DATASET = {"source": "CoinGecko markets, categories, coins/list platforms, 365-day market_chart and "
                           "simple/price; DefiLlama lite/protocols2, v2/chains, overview/fees (dailyFees, "
                           "dailyRevenue, dailyHoldersRevenue), hacks; GoPlus token_security (EVM), RugCheck "
                           "report (Solana)",
                 "forward_from": "2026-10-16", "kind": "live forward record (/data/altcoin_watch_ledger.jsonl)"}
TRIAL = "b6e0fec86714efc2"


def trial_params() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "universe": {"size": UNIVERSE_SIZE, "order": "CoinGecko market_cap_desc", "benchmark": "bitcoin",
                     "excluded_categories": list(EXCLUDED_CATEGORIES),
                     "excluded_other": "stable symbol rule; price within 3% of $1 with a 24h range under 0.5%"},
        "gates": {
            "supply": f"(total_supply - circulating) / circulating <= {LOCKED_MAX}; above it REVIEW (unlock "
                      "schedules are not in free data); missing supply REVIEW; total below circulating by more "
                      f"than {SUPPLY_TOLERANCE} REVIEW (inconsistent data)",
            "activity": "DefiLlama fees mapped to the coin (chain gas token, protocol or parent) with 30-day "
                        "fees > 0; zero fees FAIL; not mapped REVIEW",
            "security": f"DefiLlama hack with stolen minus returned >= {HACK_MIN_USD:.0f} USD within "
                        f"{HACK_FAIL_DAYS} days FAIL, within {HACK_WARN_DAYS} days WARN; a hack with an unknown "
                        f"amount within {HACK_FAIL_DAYS} days or an unknown date REVIEW, an unknown amount within "
                        f"{HACK_WARN_DAYS} days WARN (a chain's own coin counts only hacks whose target type is "
                        "Chain); contract on the primary CoinGecko "
                        "platform only (listed first): GoPlus honeypot, balance change, hidden owner, "
                        "selfdestruct, take-back ownership FAIL, mint/proxy/pause/blacklist WARN, a missing FAIL "
                        "field REVIEW, a missing WARN field WARN; Solana freeze authority FAIL, mint authority "
                        "WARN; gas tokens and coins without "
                        "a contract PASS; a primary platform on another chain REVIEW",
            "liquidity": f"median daily CoinGecko volume over the complete UTC days of the last 31 (one point "
                         f"per day) >= {VOLUME_MIN_USD:.0f} USD else FAIL; fewer than {VOLUME_MIN_DAYS} days, the "
                         f"newest older than {VOLUME_MAX_STALE_DAYS} days, or no chart REVIEW",
            "deep_checks": f"security contract check, liquidity and dilution only for coins passing supply and "
                           f"activity without a hack FAIL, at most {MAX_DEEP_CHECKS} by market cap; the rest "
                           "REVIEW",
        },
        "measures": {
            "net_yield": "DefiLlama holders revenue 1y / market cap - max(0, 12-month dilution)",
            "dilution": f"implied supply (market cap / price, 365-day daily chart, one point per UTC day): "
                        f"median of the last "
                        f"{CHART_EDGE_POINTS} points / median of the first {CHART_EDGE_POINTS} - 1; unknown "
                        f"when the chart spans < {CHART_MIN_DAYS} days or < {CHART_MIN_POINTS} points or any "
                        f"one-day move in implied supply exceeds {DILUTION_JUMP}",
            "valuation": "FDV (market cap when FDV is missing) / DefiLlama revenue 1y; revenue <= 0 unknown",
            "usage": "DefiLlama fees 30d / fees monthly average over 1y - 1; unknown when any contributing "
                     "entry has no 1-year history",
            "ranking": "coins passing every gate; percentile rank per measure (ties averaged), unknown measure "
                       "ranks 0; composite = mean of the three; position by composite desc, then market cap "
                       "desc, then CoinGecko id; thirds: top floor(n/3), bottom floor(n/3), rest middle",
        },
        "cohort": "one per UTC month: the first complete refresh of the month, every universe coin recorded, "
                  "closed by a MONTH record",
        "outcome_days": list(OUTCOME_DAYS),
        "outcome_rule": "CoinGecko price and bitcoin price within 2 days after the horizon; past that MISSED, or "
                        "NO_QUOTE if CoinGecko was asked inside the window and had no price; never backfilled",
        "outcome_tolerance_seconds": OUTCOME_TOLERANCE_SECONDS,
        "refresh_seconds": REFRESH_SECONDS,
        "evaluation": {
            "not_before": "2028-11-01",
            "cohorts": "every cohort whose 365-day outcome is due by not_before (expected 2026-10..2027-10)",
            "primary": "per cohort: mean 365-day return minus bitcoin's of the top third minus the same for "
                       "the bottom third; statistic = mean over cohorts",
            "ci": "95% moving block bootstrap over cohorts, block 3, 2000 draws, seed 0",
            "no_quote": "a NO_QUOTE outcome counts as a -100% return",
            "decisions": {"SEPARATES": "mean > 0 and the interval excludes 0",
                          "NO_SEPARATION": "the interval includes 0",
                          "REVERSED": "mean < 0 and the interval excludes 0",
                          "INCOMPLETE_DATA": "fewer than 9 cohorts with at least 3 coins in each third, or more "
                                             "than 20% of due 365-day outcomes MISSED"},
            "secondary": ["passing coins (equal weight) minus the whole universe, against bitcoin",
                          "eliminated coins minus the whole universe, against bitcoin",
                          "the same at 182 and 730 days"],
            "power": "13 monthly cohorts with overlapping 12-month windows are about one market cycle: low "
                     "power; no result changes the module automatically",
            "live_effect": "none automatic; any change goes through a separate PR and the user's approval",
        },
    }


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


def month_of(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")


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


def looks_pegged(row: Mapping[str, Any]) -> bool:
    """A dollar-pegged token the categories missed: price near $1 and almost no 24-hour range."""

    price, high, low = (_positive(row.get(k)) for k in ("current_price", "high_24h", "low_24h"))
    return bool(price and high and low and 0.97 <= price <= 1.03 and high / low <= 1.005)


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
                self.sleep(self.backoff * (attempt + 1))
                continue
            if response.status_code != 200:
                raise AltcoinDataError(f"{name} http {response.status_code}")
            try:
                return response.json()
            except ValueError:
                raise AltcoinDataError(f"{name} malformed JSON") from None
        raise AltcoinDataError(f"{name} http 429")     # pragma: no cover - the loop always returns or raises


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
        self.pace = 2.5 if self.keyed else 7.0         # stay inside 30/min (key) or the public limit
        self.sleep = sleep
        self.http = _Http(timeout=timeout, session=session, sleep=sleep)

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
    """DefiLlama's free API: protocol and chain mapping, fees, revenue, holders revenue, hacks."""

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
# The screen (pure: data in, gates, measures and a status out)
# ---------------------------------------------------------------------------


@dataclass
class Card:
    coin: Coin
    gates: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    measures: dict[str, float | None] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)
    composite: float | None = None
    position: int | None = None
    third: int | None = None

    def add(self, gate: str, status: str, text: str) -> None:
        self.gates.setdefault(gate, []).append((status, text))

    def gate(self, gate: str) -> str:
        found = [s for s, _ in self.gates.get(gate, [])]
        if not found:
            return SKIP
        for level in (FAIL, REVIEW, WARN, SKIP):
            if level in found:
                return level
        return PASS

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


def screen_activity(card: Card, fees: Totals | None) -> None:
    if fees is None:
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


def screen_chart(card: Card, chart: Mapping[str, Any] | None, *, now: int) -> None:
    """Liquidity gate, 12-month dilution and the ATH-day market cap from one 365-day daily chart."""

    if not isinstance(chart, Mapping):
        card.add(LIQUIDITY, REVIEW, "hacim verisi alınamadı")
        card.measures["dilution"] = None
        return
    days = [(ts, v) for ts, v in _series(chart, "total_volumes")
            if now - 31 * DAY <= ts < now - DAY and v >= 0]          # complete days of the last month
    volumes = [v for _, v in days]
    if len(volumes) < VOLUME_MIN_DAYS or now - days[-1][0] > VOLUME_MAX_STALE_DAYS * DAY:
        card.add(LIQUIDITY, REVIEW, "30 günlük hacim verisi eksik ya da eski")
    else:
        median = statistics.median(volumes)
        card.info["volume_median_30d"] = median
        status = PASS if median >= VOLUME_MIN_USD else FAIL
        card.add(LIQUIDITY, status, f"30 günlük medyan hacim ${median / 1e6:,.1f}M")
    prices = dict(_series(chart, "prices"))
    supply = [(ts, cap / prices[ts]) for ts, cap in _series(chart, "market_caps")
              if cap > 0 and prices.get(ts, 0) > 0]
    card.measures["dilution"] = _dilution(card, supply)
    coin = card.coin
    if coin.ath_date is not None and supply and supply[0][0] - DAY <= coin.ath_date and coin.ath:
        nearest = min(supply, key=lambda p: abs(p[0] - coin.ath_date))
        if abs(nearest[0] - coin.ath_date) <= 2 * DAY:
            card.info["ath_market_cap"] = coin.ath * nearest[1]


def _dilution(card: Card, supply: Sequence[tuple[int, float]]) -> float | None:
    if len(supply) < CHART_MIN_POINTS or supply[-1][0] - supply[0][0] < CHART_MIN_DAYS * DAY:
        card.info["dilution_note"] = "arz serisi bir yıldan kısa"
        return None
    for (_, a), (_, b) in zip(supply, supply[1:]):
        if a > 0 and abs(b / a - 1) > DILUTION_JUMP:
            card.info["dilution_note"] = f"arz serisinde tek günde %{abs(b / a - 1) * 100:.0f} sıçrama"
            return None
    start = statistics.median(v for _, v in supply[:CHART_EDGE_POINTS])
    end = statistics.median(v for _, v in supply[-CHART_EDGE_POINTS:])
    return end / start - 1 if start > 0 else None


def compute_measures(card: Card, *, holders: Totals | None, revenue: Totals | None, fees: Totals | None) -> None:
    coin = card.coin
    dilution = card.measures.get("dilution")
    holder_yield = holders.y1 / coin.market_cap if holders is not None and holders.y1_known else None
    card.measures["holder_yield"] = holder_yield
    card.measures["net_yield"] = (holder_yield - max(0.0, dilution)
                                  if holder_yield is not None and dilution is not None else None)
    value = coin.fdv or coin.market_cap
    card.measures["valuation"] = (value / revenue.y1 if revenue is not None and revenue.y1_known
                                  and revenue.y1 > 0 else None)
    card.measures["usage"] = (fees.d30 / fees.monthly - 1 if fees is not None and fees.monthly_known
                              and fees.monthly > 0 else None)


def ath_info(card: Card) -> None:
    """The ATH arithmetic both models asked for: information only, never a score."""

    coin, info = card.coin, card.info
    if not coin.ath:
        return
    info["ath_multiple"] = coin.ath / coin.price
    if coin.circulating:
        info["ath_cap_today_supply"] = coin.ath * coin.circulating
        dilution = card.measures.get("dilution")
        if dilution is not None and dilution > -1:
            info["ath_cap_24m_supply"] = coin.ath * coin.circulating * (1 + dilution) ** 2
        if info.get("ath_market_cap"):
            info["price_for_ath_cap"] = info["ath_market_cap"] / coin.circulating
    supply = max(coin.total_supply or 0.0, coin.max_supply or 0.0)
    if supply:
        info["ath_fdv"] = coin.ath * supply


def _percentiles(values: Sequence[float | None], higher_is_better: bool) -> list[float]:
    known = sorted({v for v in values if v is not None})
    if not known:
        return [0.0 for _ in values]
    ordered = sorted(v for v in values if v is not None)
    if not higher_is_better:
        ordered = ordered[::-1]
    n = len(ordered)
    out = []
    for value in values:
        if value is None:
            out.append(0.0)                            # unknown ranks last: no optimistic assumption
            continue
        places = [i for i, v in enumerate(ordered) if v == value]
        out.append((sum(places) / len(places)) / (n - 1) if n > 1 else 1.0)
    return out


def rank(cards: Sequence[Card]) -> None:
    passed = [c for c in cards if c.status == PASSED]
    if not passed:
        return
    scores = [0.0] * len(passed)
    for measure in MEASURES:
        for i, p in enumerate(_percentiles([c.measures.get(measure) for c in passed], HIGHER_IS_BETTER[measure])):
            scores[i] += p / len(MEASURES)
    for card, score in zip(passed, scores):
        card.composite = round(score, 6)
    ordered = sorted(passed, key=lambda c: (-(c.composite or 0.0), -c.coin.market_cap, c.coin.gecko_id))
    third = len(ordered) // 3
    for position, card in enumerate(ordered, 1):
        card.position = position
        card.third = None if third == 0 else 1 if position <= third else 3 if position > len(ordered) - third else 2


# ---------------------------------------------------------------------------
# The watcher: refresh (universe -> gates -> deep checks -> ranking) and forward record
# ---------------------------------------------------------------------------


class AltcoinWatch:
    def __init__(self, gecko: CoinGecko, llama: DefiLlama, security: Any, ledger: Any, *,
                 universe_size: int = UNIVERSE_SIZE, sleep: Callable[[float], None] = time.sleep) -> None:
        self.gecko, self.llama, self.security, self.ledger = gecko, llama, security, ledger
        self.universe_size, self.sleep = universe_size, sleep

    def universe(self) -> list[Coin]:
        excluded: set[str] = set()
        for category in EXCLUDED_CATEGORIES:
            excluded |= {r["id"] for r in self.gecko.markets(1, category=category)
                         if isinstance(r, Mapping) and isinstance(r.get("id"), str)}
        coins: list[Coin] = []
        for page in range(1, MARKET_PAGES + 1):
            for row in self.gecko.markets(page):
                coin = parse_market(row)
                if (coin is None or coin.gecko_id == "bitcoin" or coin.gecko_id in excluded
                        or is_stable_or_synthetic(coin.symbol) or looks_pegged(row)):
                    continue
                if all(c.gecko_id != coin.gecko_id for c in coins):
                    coins.append(coin)
                if len(coins) >= self.universe_size:
                    return coins
        if len(coins) < self.universe_size * 0.9:
            raise AltcoinDataError(f"coingecko universe too small ({len(coins)})")
        return coins

    def refresh(self, now: int) -> dict[str, Any]:
        """One full screen. Raises when a core source fails; per-coin failures leave that coin REVIEW."""

        coins = self.universe()
        index = build_llama_index(self.llama.lite(), self.llama.chains())
        fees = aggregate(self.llama.overview("dailyFees"), index)
        revenue = aggregate(self.llama.overview("dailyRevenue"), index)
        holders = aggregate(self.llama.overview("dailyHoldersRevenue"), index)
        hacks = hacks_by_gecko(self.llama.hacks(), index)
        platforms = self.gecko.platforms()
        btc = self.gecko.prices(["bitcoin"]).get("bitcoin")
        if btc is None:
            raise AltcoinDataError("coingecko has no bitcoin price")
        cards: list[Card] = []
        deep = 0
        for coin in coins:
            card = Card(coin=coin)
            contract = contract_of(coin.gecko_id, platforms, index)
            screen_supply(card)
            screen_activity(card, fees.get(coin.gecko_id))
            hacked = screen_hacks(card, hacks.get(coin.gecko_id, ()), now=now, native=contract[0] == "native")
            if card.gate(SUPPLY) == PASS and card.gate(ACTIVITY) == PASS and not hacked:
                if deep < MAX_DEEP_CHECKS:
                    deep += 1
                    self._deep(card, contract, now)
                else:
                    card.add(LIQUIDITY, REVIEW, "bu taramada sıraya alınamadı")
            compute_measures(card, holders=holders.get(coin.gecko_id), revenue=revenue.get(coin.gecko_id),
                             fees=fees.get(coin.gecko_id))
            ath_info(card)
            cards.append(card)
        rank(cards)
        counts = {s: sum(1 for c in cards if c.status == s) for s in (PASSED, NEEDS_REVIEW, ELIMINATED)}
        snapshot = {"generated_at": now, "schema": SCHEMA, "trial": TRIAL, "universe": len(cards),
                    "counts": counts, "btc_price": btc, "coingecko_keyed": self.gecko.keyed,
                    "cards": [card_payload(c) for c in cards], "can_authorize_trade": False}
        self._record_cohort(snapshot, now)
        return snapshot

    def _deep(self, card: Card, contract: tuple[str, str | None, str | None], now: int) -> None:
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
        try:
            chart: Mapping[str, Any] | None = self.gecko.chart(card.coin.gecko_id)
        except AltcoinDataError:
            chart = None
        screen_chart(card, chart, now=now)

    def _record_cohort(self, snapshot: Mapping[str, Any], now: int) -> bool:
        """The month's cohort: the first complete refresh of each UTC month, closed by a MONTH record."""

        month = month_of(now)
        if f"MONTH:{SCHEMA_TAG}:{month}" in self.ledger.ids():
            return False
        for c in snapshot["cards"]:
            self.ledger.append({
                "id": f"WATCH:{SCHEMA_TAG}:{month}:{now}:{c['id']}", "event": "WATCH", "month": month,
                "refresh_at": now, "gecko_id": c["id"], "symbol": c["symbol"], "status": c["status"],
                "gates": {g: v["status"] for g, v in c["gates"].items()}, "measures": c["measures"],
                "composite": c["composite"], "position": c["position"], "third": c["third"], "price": c["price"],
                "market_cap": c["market_cap"], "schema": SCHEMA, "trial": TRIAL, "can_authorize_trade": False})
        self.ledger.append({"id": f"MONTH:{SCHEMA_TAG}:{month}", "event": "MONTH", "month": month, "refresh_at": now,
                            "coins": len(snapshot["cards"]), "counts": snapshot["counts"],
                            "btc_price": snapshot["btc_price"], "schema": SCHEMA, "trial": TRIAL,
                            "can_authorize_trade": False})
        return True

    def record_outcomes(self, now: int) -> int:
        """Price and bitcoin's price 182, 365 and 730 days after each cohort, within 2 days of the horizon.

        A horizon is never filled with a later price: past its tolerance it is
        MISSED, or NO_QUOTE when CoinGecko was asked inside the window and had
        no price (a QUOTE_GAP record proves the attempt).
        """

        records = self.ledger.records()
        months = {r["month"]: r for r in records if r.get("event") == "MONTH" and r.get("schema") == SCHEMA}
        done = {(r["month"], r["gecko_id"], r["days"]) for r in records
                if r.get("event") == "OUTCOME" and r.get("schema") == SCHEMA}
        gaps = {(r["month"], r["gecko_id"], r["days"]) for r in records
                if r.get("event") == "QUOTE_GAP" and r.get("schema") == SCHEMA}
        watch = [r for r in records if r.get("event") == "WATCH" and r.get("schema") == SCHEMA
                 and r.get("month") in months and r.get("refresh_at") == months[r["month"]]["refresh_at"]]
        due = [(r, d) for r in watch for d in OUTCOME_DAYS
               if (r["month"], r["gecko_id"], d) not in done and now >= int(r["refresh_at"]) + d * DAY]
        if not due:
            return 0
        open_ids = sorted({r["gecko_id"] for r, d in due
                           if now - (int(r["refresh_at"]) + d * DAY) <= OUTCOME_TOLERANCE_SECONDS})
        prices = self.gecko.prices(open_ids + ["bitcoin"]) if open_ids else {}
        btc = prices.get("bitcoin")
        recorded = 0
        for r, days in due:
            key = (r["month"], r["gecko_id"], days)
            horizon = int(r["refresh_at"]) + days * DAY
            late = now - horizon
            price = prices.get(r["gecko_id"])
            if late <= OUTCOME_TOLERANCE_SECONDS:
                if price is None or btc is None:
                    self.ledger.append({"id": f"QUOTE_GAP:{SCHEMA_TAG}:{r['month']}:{r['gecko_id']}:{days}",
                                        "event": "QUOTE_GAP", "month": r["month"], "gecko_id": r["gecko_id"],
                                        "days": days, "at": now, "schema": SCHEMA, "trial": TRIAL,
                                        "can_authorize_trade": False})
                    continue                           # ask again until the window closes
                status = "OK"
            else:
                status, price = ("NO_QUOTE" if key in gaps else "MISSED"), None
            start_btc = months[r["month"]].get("btc_price")
            recorded += 1
            self.ledger.append({
                "id": f"OUTCOME:{SCHEMA_TAG}:{r['month']}:{r['gecko_id']}:{days}", "event": "OUTCOME",
                "month": r["month"], "gecko_id": r["gecko_id"], "days": days, "at": now, "horizon_at": horizon,
                "late_hours": round(late / HOUR, 1), "status": status, "price": price,
                "btc_price": btc if status == "OK" else None,
                "return_pct": (price / r["price"] - 1) * 100 if status == "OK" and r.get("price") else None,
                "btc_return_pct": (btc / start_btc - 1) * 100 if status == "OK" and btc and start_btc else None,
                "status_at_cohort": r.get("status"), "third_at_cohort": r.get("third"), "schema": SCHEMA,
                "trial": TRIAL, "can_authorize_trade": False})
        return recorded


def card_payload(card: Card) -> dict[str, Any]:
    coin = card.coin

    def rounded(value: Any) -> Any:
        return round(value, 6) if isinstance(value, float) else value

    return {
        "id": coin.gecko_id, "symbol": coin.symbol, "name": coin.name, "rank": coin.rank, "price": coin.price,
        "market_cap": coin.market_cap, "fdv": coin.fdv, "ath": coin.ath, "ath_date": coin.ath_date,
        "status": card.status, "composite": card.composite, "position": card.position, "third": card.third,
        "gates": {g: {"status": card.gate(g), "notes": [t for _, t in card.gates.get(g, [])][:3]} for g in GATES},
        "measures": {k: rounded(v) for k, v in card.measures.items()},
        "info": {k: rounded(v) for k, v in card.info.items()},
    }


# ---------------------------------------------------------------------------
# Telegram view (HTML; every external value escaped)
# ---------------------------------------------------------------------------

DISCLAIMER = ("Uzun vadeli araştırma listesidir. Sıralama kanıtlanmadı: ağırlıklar veriden öğrenilmedi ve "
              "isabeti ileriye dönük kayıtla ölçülüyor. Alım önerisi değildir.")


def _pct(value: Any, digits: int = 1) -> str:
    number = _finite(value)
    return "?" if number is None else f"{number * 100:+.{digits}f}%"


def _usd(value: Any) -> str:
    amount = _finite(value)
    if amount is None:
        return "?"
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(amount) >= threshold:
            return f"${amount / threshold:.1f}{suffix}"
    return f"${amount:,.0f}"


def _multiple(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "?"
    return f"{number:.1f}x" if abs(number) < 100 else f"{number:,.0f}x"


def _share(value: Any) -> str:
    number = _finite(value)
    return "?" if number is None else f"%{number * 100:.1f}"


def _age(seconds: int) -> str:
    if seconds < HOUR:
        return f"{seconds // 60} dk"
    if seconds < 2 * DAY:
        return f"{seconds // HOUR} saat"
    return f"{seconds // DAY} gün"


def render(snapshot: Mapping[str, Any] | None, *, now: int, error: str | None = None, enabled: bool = True,
           max_age: int = 2 * REFRESH_SECONDS) -> str:
    from .long_alerts import esc

    head = "🔭 <b>ALTCOIN İZLEME · uzun vade (24–36 ay) · araştırma</b>"
    if not enabled:
        return head + "\n" + esc("Modül kapalı (ALTCOIN_WATCH_ENABLED=0).")
    if not snapshot:
        reason = f"Henüz tarama yok. Hata: {error}" if error else "İlk tarama sürüyor (10–20 dakika)."
        return head + "\n" + esc(reason + " Liste boş demek değildir.")
    cards = [c for c in snapshot.get("cards") or [] if isinstance(c, Mapping)]
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), Mapping) else {}
    age = max(0, now - int(snapshot.get("generated_at") or 0))
    lines = [head, esc(f"İlk {snapshot.get('universe', len(cards))} altcoin (BTC, stabil, sarılı ve tokenize "
                       f"varlıklar hariç) · tarama {_age(age)} önce"),
             esc(f"✅ kapıları geçen {counts.get(PASSED, 0)} · ❔ inceleme gerekli {counts.get(NEEDS_REVIEW, 0)} · "
                 f"❌ elenen {counts.get(ELIMINATED, 0)}")]
    if age > max_age or error:
        lines.append(esc(f"⚠️ Tarama güncel değil. Hata: {error or 'yok'}"))
    passed = sorted((c for c in cards if c.get("status") == PASSED), key=lambda c: c.get("position") or 10**6)
    lines += ["", "✅ <b>KAPILARI GEÇENLER</b> <i>(doğrulanmamış sıralama)</i>"]
    if passed:
        rows = [f"{'#':>2} {'Coin':<7}{'NetGetiri':>10}{'FDV/Gelir':>10}{'Kullanım':>9}{'ATH':>8}"]
        for c in passed[:10]:
            m, info = c.get("measures") or {}, c.get("info") or {}
            rows.append(f"{c.get('position') or '-':>2} {str(c.get('symbol'))[:6]:<7}{_pct(m.get('net_yield')):>10}"
                        f"{_multiple(m.get('valuation')):>10}{_pct(m.get('usage'), 0):>9}"
                        f"{_multiple(info.get('ath_multiple')):>8}")
        lines.append("<pre>" + esc("\n".join(rows)) + "</pre>")
        lines.append(esc("NetGetiri: token sahibine giden gelir − seyrelme (yıllık). FDV/Gelir: düşük daha ucuz. "
                         "Kullanım: son 30 günün ücreti, yıllık aylık ortalamaya göre. ATH: zirveye kaç kat."))
    else:
        lines.append(esc("Bu taramada yok."))
    review = [c for c in cards if c.get("status") == NEEDS_REVIEW]
    lines += ["", f"❔ <b>{STATUS_LABEL[NEEDS_REVIEW]}</b> <i>(veri eksik ya da doğrulanamıyor; geçti sayılmaz)</i>"]
    reasons: dict[str, int] = {}
    for c in review:
        for g in GATES:
            info = (c.get("gates") or {}).get(g) or {}
            if info.get("status") == REVIEW:
                key = re.sub(r"%\d+(\.\d+)?", "%X", (info.get("notes") or ["?"])[-1])
                reasons[key] = reasons.get(key, 0) + 1
                break
    for text, n in sorted(reasons.items(), key=lambda kv: -kv[1])[:4]:
        lines.append(esc(f"• {n} coin: {text}"))
    lines.append(esc("Örnek: " + ", ".join(str(c.get("symbol"))[:8] for c in review[:12]) if review else "Yok."))
    eliminated = [c for c in cards if c.get("status") == ELIMINATED]
    lines += ["", f"❌ <b>{STATUS_LABEL[ELIMINATED]}</b>"]
    for c in eliminated[:6]:
        fails = [n for g in GATES for n in ((c.get("gates") or {}).get(g) or {}).get("notes") or []
                 if ((c.get("gates") or {}).get(g) or {}).get("status") == FAIL]
        lines.append(f"• {esc(str(c.get('symbol'))[:10])}: {esc('; '.join(fails[:1])[:90])}")
    if len(eliminated) > 6:
        lines.append(esc(f"… ve {len(eliminated) - 6} coin daha"))
    if not eliminated:
        lines.append(esc("Yok."))
    lines += ["", esc("Tek coin kartı: /altcoin <sembol>"), f"<i>{esc(DISCLAIMER)}</i>",
              esc("Emir yetkisi yok, karar senin.")]
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "\n…"


def find(snapshot: Mapping[str, Any] | None, query: str) -> list[Mapping[str, Any]]:
    """Cards whose symbol or CoinGecko id equals the query (case-insensitive); symbols can collide."""

    q = query.strip().lower()
    return [c for c in (snapshot or {}).get("cards") or [] if isinstance(c, Mapping)
            and q in (str(c.get("symbol") or "").lower(), str(c.get("id") or "").lower())]


def render_card(card: Mapping[str, Any], *, now: int, generated_at: int | None = None) -> str:
    from .long_alerts import _num, esc

    m, info = card.get("measures") or {}, card.get("info") or {}
    status = str(card.get("status"))
    icon = {PASSED: "✅", NEEDS_REVIEW: "❔", ELIMINATED: "❌"}.get(status, "❔")
    position = f" · sıra {card['position']}" if card.get("position") else ""
    lines = [f"🔭 <b>{esc(str(card.get('name'))[:28])} ({esc(str(card.get('symbol'))[:12])})</b> · "
             f"{esc(str(card.get('id'))[:40])}",
             f"{icon} <b>{STATUS_LABEL.get(status, status)}</b>{esc(position)}"]
    rows = []
    for g in GATES:
        info_g = (card.get("gates") or {}).get(g) or {}
        notes = info_g.get("notes") or ["değerlendirilmedi (önceki kapı)"]
        rows.append(f"{g:<10}{ICON.get(str(info_g.get('status')), '·')} {notes[0][:80]}")
        rows += [f"{'':<12}{n[:80]}" for n in notes[1:3]]
    lines.append("<pre>" + esc("\n".join(rows)) + "</pre>")
    measure_rows = [
        f"Net getiri     {_pct(m.get('net_yield'))}  (sahip geliri {_pct(m.get('holder_yield'), 2)}, "
        f"seyrelme {_pct(m.get('dilution'))})",
        f"FDV / gelir    {_multiple(m.get('valuation'))}",
        f"Kullanım       {_pct(m.get('usage'), 0)} (son 30 gün / yıllık ortalama)",
    ]
    if info.get("dilution_note"):
        measure_rows.append(f"Seyrelme notu  {info['dilution_note']}")
    if info.get("locked_share") is not None:
        measure_rows.append(f"Dolaşım dışı   {_share(info.get('locked_share'))} (basılmış, dolaşımda değil)")
    if info.get("unminted_share") is not None:
        measure_rows.append(f"Basılmamış     {_share(info.get('unminted_share'))} (maksimum arza kadar)")
    lines.append("<pre>" + esc("\n".join(measure_rows)) + "</pre>")
    price, ath = card.get("price"), card.get("ath")
    ath_rows = [f"Fiyat ${_num(price) if price else '?'} · ATH ${_num(ath) if ath else '?'}"
                + (f" ({datetime.fromtimestamp(card['ath_date'], tz=timezone.utc):%Y-%m-%d})"
                   if card.get("ath_date") else "")]
    if info.get("ath_multiple") and info["ath_multiple"] > 1:
        ath_rows.append(f"ATH'ye dönüş için %{(info['ath_multiple'] - 1) * 100:,.0f} artış gerekir")
    elif info.get("ath_multiple"):
        ath_rows.append("Fiyat ATH'de ya da üstünde")
    ath_rows.append(f"ATH fiyatı bugünkü dolaşımla: {_usd(info.get('ath_cap_today_supply'))} piyasa değeri")
    ath_rows.append(f"ATH fiyatı 24 ay sonraki tahmini arzla: {_usd(info.get('ath_cap_24m_supply'))} "
                    "(son 12 ayın seyrelme hızıyla)")
    ath_rows.append(f"ATH fiyatı tam seyreltilmiş arzla: {_usd(info.get('ath_fdv'))}")
    if info.get("ath_market_cap"):
        ath_rows.append(f"ATH günündeki piyasa değeri {_usd(info['ath_market_cap'])}; bugünkü arzla bu değere "
                        f"denk fiyat ${_num(info.get('price_for_ath_cap'))}")
    else:
        ath_rows.append("ATH günündeki piyasa değeri bilinmiyor (ATH 365 günden eski; ücretsiz veride yok)")
    lines += [esc(r) for r in ath_rows]
    lines.append(esc(f"Piyasa değeri {_usd(card.get('market_cap'))} · FDV {_usd(card.get('fdv'))}"
                     + (f" · tarama {_age(max(0, now - generated_at))} önce" if generated_at else "")))
    lines += [esc("Elle bakılacaklar: ekip, ortaklıklar, yol haritası, yönetişim. Bot bunları puanlamaz."),
              f"<i>{esc(DISCLAIMER)}</i>", esc("Emir yetkisi yok, karar senin.")]
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3990] + "\n…"


def status_line(snapshot: Mapping[str, Any] | None, *, now: int, enabled: bool, error: str | None) -> str:
    if not enabled:
        return "Altcoin izleme: kapalı"
    if not snapshot:
        return f"Altcoin izleme: henüz tarama yok{' · hata ' + error if error else ''}"
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), Mapping) else {}
    age = max(0, now - int(snapshot.get("generated_at") or 0))
    key = "" if snapshot.get("coingecko_keyed") else " · CoinGecko anahtarı yok (yavaş)"
    return (f"Altcoin izleme: ✅ {counts.get(PASSED, 0)} ❔ {counts.get(NEEDS_REVIEW, 0)} "
            f"❌ {counts.get(ELIMINATED, 0)} · {_age(age)} önce{key}" + (f" · hata {error}" if error else ""))


def fresh(snapshot: Mapping[str, Any] | None, *, now: int, max_age: int) -> bool:
    try:
        generated = int((snapshot or {}).get("generated_at") or 0)
    except (TypeError, ValueError):
        return False
    return generated > 0 and 0 <= now - generated <= max_age
