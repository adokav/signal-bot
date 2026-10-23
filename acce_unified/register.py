"""The v3.2 register: the user's selection report as data.

The selection is made outside the bot. The report "Kripto ve Meme Coin Seçim
Sistemi v3.2 — Nihai Rapor" names the coins and meme coins, their baskets,
scores, scenario outputs, risk outputs and the triggers that would move them.
This module reads that register and nothing else:

- the bot never writes the register; it changes only by pull request, and
  every ledger event records the register file's sha256 so a decision can be
  traced to the list it was made from;
- the bot never promotes a coin: a coin's basket is the report's; the bot's
  own checks (hack, contract, liquidity) can only demote (thesis
  invalidation), never lift a condition or a gate;
- any malformed field fails the whole load (`RegisterError`): a partial
  register would silently drop or mislabel a coin.

Nothing here carries order authority (`can_authorize_trade` is false
everywhere downstream).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "v32-register/v1"
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "research" / "register" / "v3_2_2026-10-23.json"

ALTCOIN, MEME = "altcoin", "meme"
KINDS = (ALTCOIN, MEME)

CORE = "core"                                # gates passed, scored, ranked: slice signals come only from here
CORE_CONDITIONAL = "core_conditional"        # core by score, but a critical field is unverified (report: not core until verified)
WATCH = "watch"                              # a gate failed in a way that can heal (SKY: spot volume)
GROWTH_POOL = "growth_pool"                  # G2 failed with verified token flow: scored, listed apart
UNVERIFIED = "unverified"                    # a gate could not be evaluated
FAILED = "failed"                            # a structural gate failure
SPECULATIVE_POOL = "speculative_pool"        # meme M1 failed (young): listed apart, never core
LIQUIDITY_BORDERLINE = "liquidity_borderline"  # meme M3 just under the threshold
LIQUIDITY_FAILED = "liquidity_failed"        # meme M3 failed
BASKETS = {CORE, CORE_CONDITIONAL, WATCH, GROWTH_POOL, UNVERIFIED, FAILED,
           SPECULATIVE_POOL, LIQUIDITY_BORDERLINE, LIQUIDITY_FAILED}
SIGNAL_BASKETS = {CORE}
BASKET_LABEL = {CORE: "çekirdek", CORE_CONDITIONAL: "çekirdek (koşullu)", WATCH: "izleme",
                GROWTH_POOL: "büyüme/seyrelme havuzu", UNVERIFIED: "doğrulanamadı", FAILED: "başarısız",
                SPECULATIVE_POOL: "spekülatif havuz", LIQUIDITY_BORDERLINE: "likidite sınırında",
                LIQUIDITY_FAILED: "likidite kapısı ✗"}

SCORE_ROUNDING = 2          # the report prints rounded group scores; totals may differ by this much
S_TOLERANCE = 0.01          # S = 0.6 * total / 100 + 0.4 * p_2x, printed to three decimals
PERP_RE = re.compile(r"^(1000)?[A-Z0-9]{2,12}USDT$")
MEXC_RE = re.compile(r"^(1000)?[A-Z0-9]{2,12}_USDT$")
TRIGGER_ID_RE = re.compile(r"^[A-Z0-9_]{3,40}$")
ALL = "ALL"


class RegisterError(ValueError):
    """The register file is missing, malformed or inconsistent. Nothing is loaded."""


@dataclass(frozen=True)
class Coin:
    symbol: str
    name: str
    gecko_id: str
    kind: str
    perp: str
    perp_multiplier: int
    mexc_symbol: str
    mexc_contract_size: float
    basket: str
    basket_note: str | None
    condition: str | None
    rank: int | None
    score: Mapping[str, float]
    total: float
    p_2x: float
    s: float | None
    scenario_medians: Mapping[str, float] | None
    e_m: float | None
    risk: Mapping[str, Any] | None
    snapshot: Mapping[str, Any]
    triggers: tuple[str, ...]
    rationale: str
    pages: tuple[int, ...]

    @property
    def signal_eligible(self) -> bool:
        """Slice signals are produced only for the core basket; a condition keeps a coin out until it clears."""
        return self.basket in SIGNAL_BASKETS and self.condition is None

    @property
    def basket_label(self) -> str:
        return BASKET_LABEL[self.basket]

    @property
    def mexc_price_multiplier(self) -> int:
        """A ``1000``-prefixed MEXC contract quotes 1000 coins: its prices are divided by this for coin prices."""

        return 1000 if self.mexc_symbol.startswith("1000") else 1

    @property
    def position_cap_usd(self) -> float | None:
        """The report's own cap where it states one; otherwise 10 % of the reported daily spot volume."""
        if self.risk and isinstance(self.risk.get("position_cap_usd"), (int, float)):
            return float(self.risk["position_cap_usd"])
        volume = self.snapshot.get("spot_volume_usd")
        if isinstance(volume, (int, float)) and math.isfinite(volume) and volume > 0:
            return 0.10 * float(volume)
        return None


@dataclass(frozen=True)
class Trigger:
    id: str
    condition: str
    affects: tuple[str, ...]
    effect: str
    mechanical: bool
    status: str
    thresholds: Mapping[str, float]


@dataclass(frozen=True)
class Register:
    path: Path
    sha256: str
    schema: str
    version: str
    report_date: date
    data_as_of: date
    report: Mapping[str, Any]
    rules: Mapping[str, Any]
    triggers: tuple[Trigger, ...]
    coins: tuple[Coin, ...]
    not_scored: tuple[Mapping[str, Any], ...]
    meme_excluded: tuple[Mapping[str, Any], ...]

    def find(self, symbol: str) -> Coin | None:
        key = symbol.strip().upper()
        for coin in self.coins:
            if coin.symbol == key:
                return coin
        return None

    def by_kind(self, kind: str) -> tuple[Coin, ...]:
        return tuple(c for c in self.coins if c.kind == kind)

    def basket(self, *baskets: str) -> tuple[Coin, ...]:
        return tuple(c for c in self.coins if c.basket in baskets)

    def core(self, kind: str | None = None) -> tuple[Coin, ...]:
        return tuple(c for c in self.coins if c.basket == CORE and (kind is None or c.kind == kind))

    def signal_eligible(self) -> tuple[Coin, ...]:
        return tuple(c for c in self.coins if c.signal_eligible)

    def trigger(self, trigger_id: str) -> Trigger | None:
        for t in self.triggers:
            if t.id == trigger_id:
                return t
        return None

    def triggers_for(self, symbol: str) -> tuple[Trigger, ...]:
        key = symbol.strip().upper()
        return tuple(t for t in self.triggers if ALL in t.affects or key in t.affects)

    def summary(self) -> dict[str, Any]:
        return {"version": self.version, "report_date": self.report_date.isoformat(), "sha256": self.sha256,
                "coins": len(self.coins), "core": [c.symbol for c in self.core()],
                "conditional": [c.symbol for c in self.basket(CORE_CONDITIONAL)]}


# ---------------------------------------------------------------------------
# Validation helpers: each raises RegisterError with the path of the bad field
# ---------------------------------------------------------------------------


def _fail(where: str, why: str) -> RegisterError:
    return RegisterError(f"{where}: {why}")


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail(where, "nesne bekleniyor")
    return value


def _text(value: Any, where: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, "boş olmayan metin bekleniyor")
    return value


def _number(value: Any, where: str, *, low: float | None = None, high: float | None = None,
            optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _fail(where, "sonlu sayı bekleniyor")
    if low is not None and value < low:
        raise _fail(where, f"{low} altında")
    if high is not None and value > high:
        raise _fail(where, f"{high} üstünde")
    return float(value)


def _date(value: Any, where: str) -> date:
    text = _text(value, where)
    try:
        return date.fromisoformat(text)      # type: ignore[arg-type]
    except ValueError as exc:
        raise _fail(where, "ISO tarih bekleniyor") from exc


def _finite_values(value: Any, where: str) -> None:
    """Snapshots and risk blocks are free-form, but no number in them may be NaN or infinite."""
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise _fail(where, "sonlu olmayan sayı")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _finite_values(item, f"{where}.{key}")
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _finite_values(item, f"{where}[{i}]")


def _trigger(raw: Any, where: str) -> Trigger:
    data = _mapping(raw, where)
    trigger_id = _text(data.get("id"), f"{where}.id")
    if not TRIGGER_ID_RE.match(trigger_id):        # type: ignore[arg-type]
        raise _fail(f"{where}.id", "BÜYÜK_HARF_VE_ALT_ÇİZGİ bekleniyor")
    affects = data.get("affects")
    if not isinstance(affects, list) or not affects or not all(isinstance(a, str) and a.strip() for a in affects):
        raise _fail(f"{where}.affects", "en az bir sembol ya da ALL bekleniyor")
    mechanical = data.get("mechanical")
    if not isinstance(mechanical, bool):
        raise _fail(f"{where}.mechanical", "true/false bekleniyor")
    status = _text(data.get("status"), f"{where}.status")
    if status not in {"open", "met", "closed"}:
        raise _fail(f"{where}.status", "open/met/closed bekleniyor")
    thresholds = data.get("thresholds") or {}
    thresholds = _mapping(thresholds, f"{where}.thresholds")
    clean: dict[str, float] = {}
    for key, value in thresholds.items():
        clean[str(key)] = _number(value, f"{where}.thresholds.{key}")   # type: ignore[assignment]
    return Trigger(id=trigger_id, condition=_text(data.get("condition"), f"{where}.condition"),  # type: ignore[arg-type]
                   affects=tuple(a.strip().upper() for a in affects),
                   effect=_text(data.get("effect"), f"{where}.effect"),  # type: ignore[arg-type]
                   mechanical=mechanical, status=status, thresholds=clean)  # type: ignore[arg-type]


def _coin(raw: Any, where: str, *, kind: str, weights: Mapping[str, float], trigger_ids: set[str]) -> Coin:
    data = _mapping(raw, where)
    symbol = _text(data.get("symbol"), f"{where}.symbol")
    if symbol != symbol.upper() or not re.match(r"^[A-Z0-9]{2,12}$", symbol):  # type: ignore[union-attr]
        raise _fail(f"{where}.symbol", "büyük harf sembol bekleniyor")
    if data.get("kind") != kind:
        raise _fail(f"{where}.kind", f"{kind} bekleniyor")
    perp = _text(data.get("perp"), f"{where}.perp")
    if not PERP_RE.match(perp):                                   # type: ignore[arg-type]
        raise _fail(f"{where}.perp", "Binance USDT-M sembolü bekleniyor (örn. 1000PEPEUSDT)")
    multiplier = data.get("perp_multiplier")
    expected = 1000 if perp.startswith("1000") else 1             # type: ignore[union-attr]
    if multiplier != expected:
        raise _fail(f"{where}.perp_multiplier", f"{expected} bekleniyor ({perp})")
    mexc = _mapping(data.get("mexc"), f"{where}.mexc")
    mexc_symbol = _text(mexc.get("symbol"), f"{where}.mexc.symbol")
    if not MEXC_RE.match(mexc_symbol):                            # type: ignore[arg-type]
        raise _fail(f"{where}.mexc.symbol", "MEXC vadeli sembolü bekleniyor (örn. PEPE_USDT)")
    mexc_size = _number(mexc.get("contract_size"), f"{where}.mexc.contract_size", low=1e-9)
    basket = _text(data.get("basket"), f"{where}.basket")
    if basket not in BASKETS:
        raise _fail(f"{where}.basket", f"bilinmeyen sepet {basket!r}")
    condition = _text(data.get("condition"), f"{where}.condition", optional=True)
    if (basket == CORE_CONDITIONAL) != (condition is not None):
        raise _fail(f"{where}.condition", "koşul yalnız core_conditional sepetinde ve orada zorunlu")
    rank = data.get("rank")
    if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
        raise _fail(f"{where}.rank", "pozitif tam sayı ya da null bekleniyor")
    score_raw = _mapping(data.get("score"), f"{where}.score")
    if set(score_raw) != set(weights):
        raise _fail(f"{where}.score", f"gruplar {sorted(weights)} bekleniyor")
    score = {g: _number(score_raw[g], f"{where}.score.{g}", low=0, high=weights[g]) for g in weights}
    total = _number(data.get("total"), f"{where}.total", low=0, high=100)
    if abs(sum(score.values()) - total) > SCORE_ROUNDING:          # type: ignore[arg-type, operator]
        raise _fail(f"{where}.total", f"grup toplamı {sum(score.values()):.0f} ile {total} uyuşmuyor")
    p_2x = _number(data.get("p_2x"), f"{where}.p_2x", low=0, high=1)
    s = _number(data.get("s"), f"{where}.s", low=0, high=1, optional=True)
    if s is not None and abs(0.6 * total / 100 + 0.4 * p_2x - s) > S_TOLERANCE:   # type: ignore[operator]
        raise _fail(f"{where}.s", "S = 0,6·Puan/100 + 0,4·P(≥2×) ile uyuşmuyor")
    medians = data.get("scenario_medians")
    e_m = _number(data.get("e_m"), f"{where}.e_m", low=0, optional=True)
    if kind == ALTCOIN:
        medians = _mapping(medians, f"{where}.scenario_medians")
        if set(medians) != {"bear", "base", "bull"}:
            raise _fail(f"{where}.scenario_medians", "bear/base/bull bekleniyor")
        medians = {k: _number(v, f"{where}.scenario_medians.{k}", low=0) for k, v in medians.items()}
        if not medians["bear"] <= medians["base"] <= medians["bull"]:   # type: ignore[operator]
            raise _fail(f"{where}.scenario_medians", "ayı ≤ baz ≤ boğa olmalı")
    else:
        if medians is not None:
            raise _fail(f"{where}.scenario_medians", "meme kayıtlarında yok")
        if e_m is None:
            raise _fail(f"{where}.e_m", "meme kayıtlarında zorunlu")
    risk = data.get("risk")
    if risk is not None:
        risk = _mapping(risk, f"{where}.risk")
        _number(risk.get("sigma"), f"{where}.risk.sigma", low=0.01)
        _number(risk.get("median_multiple"), f"{where}.risk.median_multiple", low=0)
        for key in ("p_touch_2x", "p_touch_half"):
            _number(risk.get(key), f"{where}.risk.{key}", low=0, high=1)
        for key in ("worst_5pct", "cvar_5"):
            _number(risk.get(key), f"{where}.risk.{key}", low=0)
        _number(risk.get("position_cap_usd"), f"{where}.risk.position_cap_usd", low=1)
    snapshot = _mapping(data.get("snapshot", {}), f"{where}.snapshot")
    _finite_values(snapshot, f"{where}.snapshot")
    triggers = data.get("triggers")
    if not isinstance(triggers, list) or not all(isinstance(t, str) for t in triggers):
        raise _fail(f"{where}.triggers", "tetikleyici kimlikleri listesi bekleniyor")
    unknown = [t for t in triggers if t not in trigger_ids]
    if unknown:
        raise _fail(f"{where}.triggers", f"tanımsız tetikleyici {unknown}")
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages or not all(isinstance(p, int) and not isinstance(p, bool) and p > 0 for p in pages):
        raise _fail(f"{where}.pages", "rapor sayfaları bekleniyor")
    return Coin(symbol=symbol, name=_text(data.get("name"), f"{where}.name"),  # type: ignore[arg-type]
                gecko_id=_text(data.get("gecko_id"), f"{where}.gecko_id"), kind=kind,  # type: ignore[arg-type]
                perp=perp, perp_multiplier=expected, mexc_symbol=mexc_symbol,  # type: ignore[arg-type]
                mexc_contract_size=mexc_size, basket=basket,  # type: ignore[arg-type]
                basket_note=_text(data.get("basket_note"), f"{where}.basket_note", optional=True),
                condition=condition, rank=rank, score=score, total=total, p_2x=p_2x, s=s,  # type: ignore[arg-type]
                scenario_medians=medians, e_m=e_m, risk=risk, snapshot=snapshot,
                triggers=tuple(triggers), rationale=_text(data.get("rationale"), f"{where}.rationale"),  # type: ignore[arg-type]
                pages=tuple(pages))


def parse(data: Any, *, path: Path, sha256: str) -> Register:
    root = _mapping(data, "register")
    if root.get("schema") != SCHEMA:
        raise _fail("register.schema", f"{SCHEMA} bekleniyor")
    report = _mapping(root.get("report"), "register.report")
    version = _text(report.get("version"), "register.report.version")
    report_date = _date(report.get("report_date"), "register.report.report_date")
    data_as_of = _date(report.get("data_as_of"), "register.report.data_as_of")
    if data_as_of > report_date:
        raise _fail("register.report.data_as_of", "rapor tarihinden sonra olamaz")
    source = _mapping(report.get("source"), "register.report.source")
    digest = _text(source.get("sha256"), "register.report.source.sha256")
    if not re.match(r"^[0-9a-f]{64}$", digest):                   # type: ignore[arg-type]
        raise _fail("register.report.source.sha256", "sha256 bekleniyor")
    rules = _mapping(root.get("rules"), "register.rules")
    weights = _mapping(rules.get("weights"), "register.rules.weights")
    kind_weights: dict[str, dict[str, float]] = {}
    for kind in KINDS:
        block = _mapping(weights.get(kind), f"register.rules.weights.{kind}")
        kind_weights[kind] = {str(g): _number(w, f"register.rules.weights.{kind}.{g}", low=1) for g, w in block.items()}  # type: ignore[misc]
        if abs(sum(kind_weights[kind].values()) - 100) > 1e-9:
            raise _fail(f"register.rules.weights.{kind}", "ağırlıklar 100 etmiyor")
    regime = _mapping(rules.get("regime"), "register.rules.regime")
    _mapping(regime.get("rule"), "register.rules.regime.rule")
    for key in ("slice_1", "slice_2", "reduce"):
        _text(regime["rule"].get(key), f"register.rules.regime.rule.{key}")
    position = _mapping(rules.get("position"), "register.rules.position")
    _number(position.get("max_share_of_daily_spot_volume"), "register.rules.position.max_share_of_daily_spot_volume", low=0.001, high=1)
    reduce = _mapping(position.get("risk_reduce"), "register.rules.position.risk_reduce")
    _number(reduce.get("oi_mcap_gt"), "register.rules.position.risk_reduce.oi_mcap_gt", low=0, high=10)
    _number(reduce.get("perp_spot_gt"), "register.rules.position.risk_reduce.perp_spot_gt", low=0)
    _finite_values(rules, "register.rules")
    raw_triggers = root.get("triggers")
    if not isinstance(raw_triggers, list):
        raise _fail("register.triggers", "liste bekleniyor")
    triggers = tuple(_trigger(t, f"register.triggers[{i}]") for i, t in enumerate(raw_triggers))
    trigger_ids = [t.id for t in triggers]
    if len(set(trigger_ids)) != len(trigger_ids):
        raise _fail("register.triggers", "tekrarlanan kimlik")
    coins: list[Coin] = []
    for kind, key in ((ALTCOIN, "altcoins"), (MEME, "memes")):
        block = root.get(key)
        if not isinstance(block, list) or not block:
            raise _fail(f"register.{key}", "boş olmayan liste bekleniyor")
        coins += [_coin(c, f"register.{key}[{i}]", kind=kind, weights=kind_weights[kind], trigger_ids=set(trigger_ids))
                  for i, c in enumerate(block)]
    for field in ("symbol", "gecko_id", "perp", "mexc_symbol"):
        values = [getattr(c, field) for c in coins]
        if len(set(values)) != len(values):
            raise _fail(f"register.coins.{field}", "tekrarlanan değer")
    symbols = {c.symbol for c in coins}
    for t in triggers:
        unknown = [a for a in t.affects if a != ALL and a not in symbols
                   and a not in {str(x.get("symbol")) for x in root.get("meme_excluded") or [] if isinstance(x, Mapping)}]
        if unknown:
            raise _fail(f"register.triggers.{t.id}.affects", f"kayıtta olmayan sembol {unknown}")
    for kind in KINDS:
        ranks = [c.rank for c in coins if c.kind == kind and c.rank is not None]
        if len(set(ranks)) != len(ranks):
            raise _fail(f"register.{kind}.rank", "tekrarlanan sıra")
    for key in ("not_scored", "meme_excluded"):
        block = root.get(key, [])
        if not isinstance(block, list) or not all(isinstance(x, Mapping) for x in block):
            raise _fail(f"register.{key}", "nesne listesi bekleniyor")
        _finite_values(block, f"register.{key}")
    return Register(path=path, sha256=sha256, schema=SCHEMA, version=version, report_date=report_date,  # type: ignore[arg-type]
                    data_as_of=data_as_of, report=report, rules=rules, triggers=triggers, coins=tuple(coins),
                    not_scored=tuple(root.get("not_scored", [])), meme_excluded=tuple(root.get("meme_excluded", [])))


def load(path: Path | str = DEFAULT_PATH) -> Register:
    """Read and validate the register. Any problem raises RegisterError; nothing partial is returned."""
    file = Path(path)
    try:
        raw = file.read_bytes()
    except OSError as exc:
        raise RegisterError(f"kayıt dosyası okunamadı: {file.name}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RegisterError(f"kayıt dosyası JSON değil: {file.name}") from exc
    return parse(data, path=file, sha256=hashlib.sha256(raw).hexdigest())
