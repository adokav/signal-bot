"""Map live radar output onto the §38 gate and §40 statuses.

The radars detect setups by design rules and produce no probability, so
calibration stays UNKNOWN. There is no account or execution engine
(SHADOW), so portfolio and drawdown checks are UNKNOWN and execution
health FAILS.

Out-of-sample evidence:

- The Liquid-100 list was replayed over six years of a survivorship-free
  Binance spot universe (``docs/LIQUID_REPLAY_REPORT.md``); the result is
  read from ``research/evidence/liquid_replay.json`` under the same
  fingerprint rule. A top-3 list without positive excess return at any
  tested horizon fails the out-of-sample and net-EV questions → REJECT.
- Tactical setups were replayed over six years of Binance spot history
  (``docs/TACTICAL_REPLAY_REPORT.md``). The per-family result is read from
  ``research/evidence/tactical_replay.json`` and applied **only** when its
  engine fingerprint matches the code running now; otherwise it describes
  a different engine and is treated as unknown. A NEGATIVE or NO_EDGE
  family fails the out-of-sample and net-EV questions → REJECT.
  Unreadable or inconsistent evidence is never guessed at: UNKNOWN.

A stale snapshot is a FAIL on data freshness and therefore REJECT: an old
plan must not be presented as current (AGENTS.md §8).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from acce_unified.forward_ledger import summarize
from trading.risk.checks import Check, CheckStatus, failed, passed, unknown
from trading.risk.trade_gate import DISQUALIFYING_EVIDENCE, GateDecision, evaluate_gate


NO_BACKTEST = "bu setup ailesi hiç backtest edilmedi; OOS beklenti bilinmiyor"
NO_PROBABILITY = "radar olasılık üretmiyor; kalibrasyon yapılamaz"
NO_EV = "başarı olasılığı olmadan EV hesaplanamaz; R/R tek başına EV değildir"

REPO_ROOT = Path(__file__).resolve().parents[1]
REPLAY_EVIDENCE_FILE = REPO_ROOT / "research" / "evidence" / "tactical_replay.json"
REPLAY_SCHEMA = "tactical-replay-evidence/v1"
REPLAY_VERDICTS = ("PASS_CANDIDATE", "NO_EDGE", "NEGATIVE", "INSUFFICIENT")
NO_EDGE_VERDICTS = ("NEGATIVE", "NO_EDGE")


# ---------------------------------------------------------------------------
# Tactical replay evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyEvidence:
    setup: str
    verdict: str
    resolved: int
    hit_rate: float
    hit_rate_ci: tuple[float, float]
    breakeven_hit_rate: float
    mean_r: float
    mean_r_ci_family: tuple[float, float]


@dataclass(frozen=True)
class ReplayEvidence:
    """Per-family replay result; ``status`` is OK, STALE, INVALID or MISSING."""

    status: str
    detail: str
    trial_id: str = ""
    families: Mapping[str, FamilyEvidence] = field(default_factory=dict)

    def family(self, setup: Any) -> FamilyEvidence | None:
        if self.status != "OK":
            return None
        return self.families.get(str(setup or ""))


def _finite_number(value: Any, what: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{what} is not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} is not finite")
    return number


def _interval(value: Any, what: str) -> tuple[float, float]:
    if not isinstance(value, Sequence) or isinstance(value, str) or len(value) != 2:
        raise ValueError(f"{what} must be a [low, high] pair")
    low, high = (_finite_number(v, what) for v in value)
    if low > high:
        raise ValueError(f"{what} is inverted")
    return low, high


def _family(setup: str, row: Mapping[str, Any]) -> FamilyEvidence:
    verdict = str(row["verdict"])
    if verdict not in REPLAY_VERDICTS:
        raise ValueError(f"{setup}: unknown verdict {verdict!r}")
    resolved = row["resolved"]
    if isinstance(resolved, bool) or not isinstance(resolved, int) or resolved < 0:
        raise ValueError(f"{setup}: resolved must be a non-negative integer")
    family = FamilyEvidence(
        setup=setup,
        verdict=verdict,
        resolved=resolved,
        hit_rate=_finite_number(row["hit_rate"], "hit_rate"),
        hit_rate_ci=_interval(row["hit_rate_ci"], "hit_rate_ci"),
        breakeven_hit_rate=_finite_number(row["breakeven_hit_rate"], "breakeven_hit_rate"),
        mean_r=_finite_number(row["mean_r"], "mean_r"),
        mean_r_ci_family=_interval(row["mean_r_ci_family"], "mean_r_ci_family"),
    )
    # A verdict must agree with its own numbers; a hand-edited or corrupt
    # file is rejected rather than trusted (AGENTS.md §2).
    low, high = family.mean_r_ci_family
    if verdict == "NEGATIVE" and not high < 0:
        raise ValueError(f"{setup}: NEGATIVE verdict with an interval reaching zero")
    if verdict == "PASS_CANDIDATE" and not (low > 0 and resolved >= 100):
        raise ValueError(f"{setup}: PASS_CANDIDATE without a positive interval and 100 outcomes")
    if verdict == "NO_EDGE" and resolved < 100:
        raise ValueError(f"{setup}: NO_EDGE needs at least 100 outcomes")
    return family


def load_replay_evidence(
    path: Path = REPLAY_EVIDENCE_FILE,
    *,
    current_fingerprint: str | None = None,
) -> ReplayEvidence:
    """Read the replay evidence; never raises, never applies evidence to another engine."""

    try:
        payload = json.loads(Path(path).read_text("utf-8"))
    except FileNotFoundError:
        return ReplayEvidence("MISSING", "replay kanıt dosyası yok")
    except (OSError, ValueError):
        return ReplayEvidence("INVALID", "replay kanıt dosyası okunamadı")
    try:
        if payload.get("schema") != REPLAY_SCHEMA or payload.get("can_authorize_trade") is not False:
            raise ValueError("unexpected schema")
        fingerprint = str(payload["engine_fingerprint"])
        families = {
            str(setup): _family(str(setup), row)
            for setup, row in dict(payload["families"]).items()
        }
        trial_id = str(payload["trial_id"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return ReplayEvidence("INVALID", "replay kanıt dosyası tutarsız")
    if current_fingerprint is None:
        try:
            from trading.backtest.tactical_replay import engine_fingerprint

            current_fingerprint = engine_fingerprint()
        except Exception:
            return ReplayEvidence("INVALID", "motor parmak izi hesaplanamadı")
    if fingerprint != current_fingerprint:
        return ReplayEvidence(
            "STALE", "replay kanıtı mevcut motor sürümüne ait değil; yeniden koşulmalı", trial_id
        )
    return ReplayEvidence("OK", "replay kanıtı mevcut motorla eşleşiyor", trial_id, families)


@lru_cache(maxsize=1)
def default_replay_evidence() -> ReplayEvidence:
    return load_replay_evidence()


# ---------------------------------------------------------------------------
# Liquid-100 replay evidence
# ---------------------------------------------------------------------------


LIQUID_EVIDENCE_FILE = REPO_ROOT / "research" / "evidence" / "liquid_replay.json"
LIQUID_SCHEMA = "liquid-replay-evidence/v1"
LIQUID_LIST_GROUPS = ("TOP3@4h", "TOP3@24h")
# Must equal trading.backtest.liquid_replay.FINGERPRINT_FILES (a test checks
# both the list and the digest). Duplicated because that module needs numpy,
# which the production image does not install.
LIQUID_FINGERPRINT_FILES = (
    "acce_unified/liquid_long.py",
    "acce_unified/cex.py",
    "acce_unified/models.py",
    "trading/data/binance_universe.py",
    "trading/backtest/liquid_replay.py",
)


def code_fingerprint(files: Sequence[str], repo_root: Path = REPO_ROOT) -> str:
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode())
        digest.update((repo_root / name).read_bytes())
    return digest.hexdigest()[:16]


@dataclass(frozen=True)
class ListEvidence:
    group: str
    verdict: str
    n: int
    mean_net_pct: float
    mean_excess_pct: float
    excess_ci_family: tuple[float, float]


@dataclass(frozen=True)
class LiquidEvidence:
    """Per-group Liquid-100 replay result; ``status`` is OK, STALE, INVALID or MISSING."""

    status: str
    detail: str
    trial_id: str = ""
    groups: Mapping[str, ListEvidence] = field(default_factory=dict)


def _list_group(name: str, row: Mapping[str, Any]) -> ListEvidence:
    verdict = str(row["verdict"])
    if verdict not in REPLAY_VERDICTS:
        raise ValueError(f"{name}: unknown verdict {verdict!r}")
    n = row["n"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError(f"{name}: n must be a non-negative integer")
    group = ListEvidence(
        group=name, verdict=verdict, n=n,
        mean_net_pct=_finite_number(row["mean_net_pct"], "mean_net_pct"),
        mean_excess_pct=_finite_number(row["mean_excess_pct"], "mean_excess_pct"),
        excess_ci_family=_interval(row["excess_ci_family"], "excess_ci_family"),
    )
    low, high = group.excess_ci_family
    if verdict == "NEGATIVE" and not high < 0:
        raise ValueError(f"{name}: NEGATIVE verdict with an interval reaching zero")
    if verdict == "PASS_CANDIDATE" and not (low > 0 and n >= 300 and group.mean_net_pct > 0):
        raise ValueError(f"{name}: PASS_CANDIDATE without a positive interval, net return and 300 picks")
    if verdict == "NO_EDGE" and n < 300:
        raise ValueError(f"{name}: NO_EDGE needs at least 300 picks")
    return group


def load_liquid_evidence(
    path: Path = LIQUID_EVIDENCE_FILE,
    *,
    current_fingerprint: str | None = None,
) -> LiquidEvidence:
    """Read the Liquid-100 replay evidence; never raises, never applies it to other code."""

    try:
        payload = json.loads(Path(path).read_text("utf-8"))
    except FileNotFoundError:
        return LiquidEvidence("MISSING", "Likit-100 kanıt dosyası yok")
    except (OSError, ValueError):
        return LiquidEvidence("INVALID", "Likit-100 kanıt dosyası okunamadı")
    try:
        if payload.get("schema") != LIQUID_SCHEMA or payload.get("can_authorize_trade") is not False:
            raise ValueError("unexpected schema")
        fingerprint = str(payload["engine_fingerprint"])
        groups = {str(name): _list_group(str(name), row) for name, row in dict(payload["groups"]).items()}
        trial_id = str(payload["trial_id"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return LiquidEvidence("INVALID", "Likit-100 kanıt dosyası tutarsız")
    if current_fingerprint is None:
        try:
            current_fingerprint = code_fingerprint(LIQUID_FINGERPRINT_FILES)
        except OSError:
            return LiquidEvidence("INVALID", "Likit-100 kod parmak izi hesaplanamadı")
    if fingerprint != current_fingerprint:
        return LiquidEvidence("STALE", "Likit-100 kanıtı mevcut kod sürümüne ait değil; yeniden koşulmalı", trial_id)
    return LiquidEvidence("OK", "Likit-100 kanıtı mevcut kodla eşleşiyor", trial_id, groups)


@lru_cache(maxsize=1)
def default_liquid_evidence() -> LiquidEvidence:
    return load_liquid_evidence()


def _horizon(group: str) -> str:
    return group.split("@")[-1].replace("h", " saat")


def _liquid_checks(evidence: LiquidEvidence) -> dict[str, Check]:
    if evidence.status != "OK":
        return {"oos_expectancy_positive": unknown("oos_expectancy_positive", f"{NO_BACKTEST} ({evidence.detail})")}
    groups = [evidence.groups[name] for name in LIQUID_LIST_GROUPS if name in evidence.groups]
    if len(groups) != len(LIQUID_LIST_GROUPS):
        return {"oos_expectancy_positive": unknown("oos_expectancy_positive", "İlk 3 için replay sonucu eksik")}
    worst = min(groups, key=lambda g: g.excess_ci_family[1])
    if all(g.verdict in NO_EDGE_VERDICTS for g in groups):
        summary = (
            f"geçmiş test {worst.verdict}: İlk 3, {_horizon(worst.group)} sonra eşit ağırlıklı sepete göre "
            f"%{worst.mean_excess_pct:+.2f} (n={worst.n}, güven [%{worst.excess_ci_family[0]:+.2f}, "
            f"%{worst.excess_ci_family[1]:+.2f}])"
        )
        return {
            "oos_expectancy_positive": failed("oos_expectancy_positive", summary),
            "net_ev_positive": failed("net_ev_positive", f"maliyet sonrası ort. net getiri %{worst.mean_net_pct:+.2f}"),
            "ev_margin_sufficient": failed("ev_margin_sufficient", "pozitif EV yok, marj olamaz"),
        }
    if any(g.verdict == "PASS_CANDIDATE" for g in groups) and not any(g.verdict == "NEGATIVE" for g in groups):
        return {"oos_expectancy_positive": passed(
            "oos_expectancy_positive", "geçmiş test adayı (teknik katman); canlı doğrulama bekleniyor"
        )}
    return {"oos_expectancy_positive": unknown("oos_expectancy_positive", "replay sonucu kararsız")}


def liquid_evidence_line(evidence: LiquidEvidence | None = None) -> str:
    evidence = evidence or default_liquid_evidence()
    if evidence.status != "OK":
        return f"Geçmiş test: YOK ({evidence.detail})"
    parts = [
        f"{_horizon(g.group)} sonra sepete göre %{g.mean_excess_pct:+.2f} (n={g.n}) {g.verdict}"
        for name in LIQUID_LIST_GROUPS if (g := evidence.groups.get(name))
    ]
    return "Geçmiş test (Binance spot 2020-2026, teknik katman, maliyet sonrası): İlk 3 → " + " · ".join(parts)


# ---------------------------------------------------------------------------
# New-listing replay evidence (a descriptive base rate; changes no gate)
# ---------------------------------------------------------------------------


LISTING_EVIDENCE_FILE = REPO_ROOT / "research" / "evidence" / "listing_replay.json"
LISTING_SCHEMA = "listing-replay-evidence/v1"
LISTING_PANEL_GROUP = "24h@30d"    # closest to how the radar is used: a candidate within its first days
LISTING_VERDICTS = frozenset({"AVOID_CONFIRMED", "NO_CLAIM", "INSUFFICIENT", "POSITIVE_SURPRISE"})
LISTING_MIN_N = 100
# Must equal trading.backtest.listing_replay.FINGERPRINT_FILES (a test checks
# both the list and the digest); duplicated because that module needs numpy.
LISTING_FINGERPRINT_FILES = (
    "acce_unified/cex.py",
    "trading/data/binance_vision.py",
    "trading/data/binance_universe.py",
    "trading/data/binance_history_identity.py",
    "trading/data/binance_listings.py",
    "trading/backtest/listing_replay.py",
)


@dataclass(frozen=True)
class ListingGroupEvidence:
    group: str
    verdict: str
    n: int
    mean_gross_pct: float
    median_gross_pct: float
    share_beating_btc: float
    mean_excess_pct: float
    gross_ci_family: tuple[float, float]
    excess_ci_family: tuple[float, float]


@dataclass(frozen=True)
class ListingEvidence:
    """New-listing replay result; ``status`` is OK, STALE, INVALID or MISSING."""

    status: str
    detail: str
    trial_id: str = ""
    window: str = ""
    groups: Mapping[str, ListingGroupEvidence] = field(default_factory=dict)


def _listing_group(name: str, row: Mapping[str, Any]) -> ListingGroupEvidence:
    verdict = str(row["verdict"])
    if verdict not in LISTING_VERDICTS:
        raise ValueError(f"{name}: unknown verdict {verdict!r}")
    n = row["n"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise ValueError(f"{name}: n must be a non-negative integer")
    share = _finite_number(row["share_beating_btc"], "share_beating_btc")
    if not 0.0 <= share <= 1.0:
        raise ValueError(f"{name}: share_beating_btc outside [0, 1]")
    group = ListingGroupEvidence(
        group=name, verdict=verdict, n=n,
        mean_gross_pct=_finite_number(row["mean_gross_pct"], "mean_gross_pct"),
        median_gross_pct=_finite_number(row["median_gross_pct"], "median_gross_pct"),
        share_beating_btc=share,
        mean_excess_pct=_finite_number(row["mean_excess_pct"], "mean_excess_pct"),
        gross_ci_family=_interval(row["gross_ci_family"], "gross_ci_family"),
        excess_ci_family=_interval(row["excess_ci_family"], "excess_ci_family"),
    )
    (g_low, g_high), (e_low, e_high) = group.gross_ci_family, group.excess_ci_family
    if verdict == "INSUFFICIENT" and n >= LISTING_MIN_N:
        raise ValueError(f"{name}: INSUFFICIENT with {n} listings")
    if verdict != "INSUFFICIENT" and n < LISTING_MIN_N:
        raise ValueError(f"{name}: {verdict} needs at least {LISTING_MIN_N} listings")
    if verdict == "AVOID_CONFIRMED" and not (g_high < 0 and e_high < 0):
        raise ValueError(f"{name}: AVOID_CONFIRMED with an interval reaching zero")
    if verdict == "POSITIVE_SURPRISE" and not (g_low > 0 and e_low > 0):
        raise ValueError(f"{name}: POSITIVE_SURPRISE without positive intervals")
    if verdict == "NO_CLAIM" and g_low > 0 and e_low > 0:
        raise ValueError(f"{name}: NO_CLAIM although both intervals are above zero")
    return group


def load_listing_evidence(
    path: Path = LISTING_EVIDENCE_FILE,
    *,
    current_fingerprint: str | None = None,
) -> ListingEvidence:
    """Read the new-listing replay evidence; never raises, never changes a radar decision."""

    try:
        payload = json.loads(Path(path).read_text("utf-8"))
    except FileNotFoundError:
        return ListingEvidence("MISSING", "yeni listeleme kanıt dosyası yok")
    except (OSError, ValueError):
        return ListingEvidence("INVALID", "yeni listeleme kanıt dosyası okunamadı")
    try:
        if payload.get("schema") != LISTING_SCHEMA or payload.get("can_authorize_trade") is not False:
            raise ValueError("unexpected schema")
        fingerprint = str(payload["engine_fingerprint"])
        groups = {str(name): _listing_group(str(name), row) for name, row in dict(payload["groups"]).items()}
        trial_id, window = str(payload["trial_id"]), str(payload["window"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return ListingEvidence("INVALID", "yeni listeleme kanıt dosyası tutarsız")
    if current_fingerprint is None:
        try:
            current_fingerprint = code_fingerprint(LISTING_FINGERPRINT_FILES)
        except OSError:
            return ListingEvidence("INVALID", "yeni listeleme kod parmak izi hesaplanamadı")
    if fingerprint != current_fingerprint:
        return ListingEvidence("STALE", "yeni listeleme kanıtı mevcut kod sürümüne ait değil", trial_id)
    return ListingEvidence("OK", "yeni listeleme kanıtı mevcut kodla eşleşiyor", trial_id, window, groups)


@lru_cache(maxsize=1)
def default_listing_evidence() -> ListingEvidence:
    return load_listing_evidence()


def listing_evidence_line(evidence: ListingEvidence | None = None) -> str:
    """Historical base rate for the listing panel; descriptive, never a per-candidate probability."""

    evidence = evidence or default_listing_evidence()
    group = evidence.groups.get(LISTING_PANEL_GROUP) if evidence.status == "OK" else None
    if group is None:
        detail = evidence.detail if evidence.status != "OK" else "panel grubu kanıtta yok"
        return f"Geçmiş test: YOK ({detail})"
    low, high = group.gross_ci_family
    line = (
        f"Geçmiş test (Binance yeni listelemeler {evidence.window}, n={group.n}): ilk işlemden 24 saat sonra "
        f"alıp 30 gün tutmak → medyan %{group.median_gross_pct:+.1f}, BTC'yi geçen %{group.share_beating_btc * 100:.0f}, "
        f"ortalama %{group.mean_gross_pct:+.1f} [%{low:+.1f}, %{high:+.1f}] → {group.verdict}"
    )
    if group.mean_gross_pct - group.median_gross_pct > 10:
        line += "; ortalama ile medyan arasındaki büyük fark sonucun birkaç büyük kazanana bağlı olduğunu gösterir"
    return line + ". MEXC listelemeleri için test yok."


def _replay_checks(setup: Any, evidence: ReplayEvidence) -> dict[str, Check]:
    family = evidence.family(setup)
    if family is None:
        reason = evidence.detail if evidence.status != "OK" else "bu setup ailesi replay'de yok"
        return {"oos_expectancy_positive": unknown("oos_expectancy_positive", reason)}
    summary = (
        f"geçmiş test {family.verdict}: ort. {family.mean_r:+.2f}R maliyet sonrası "
        f"(n={family.resolved}, %99 güven [{family.mean_r_ci_family[0]:+.2f}, "
        f"{family.mean_r_ci_family[1]:+.2f}])"
    )
    if family.verdict in NO_EDGE_VERDICTS:
        return {
            "oos_expectancy_positive": failed("oos_expectancy_positive", summary),
            "net_ev_positive": failed("net_ev_positive", f"aile düzeyi net EV {family.mean_r:+.2f}R"),
            "ev_margin_sufficient": failed("ev_margin_sufficient", "pozitif EV yok, marj olamaz"),
        }
    if family.verdict == "PASS_CANDIDATE":
        return {
            "oos_expectancy_positive": passed(
                "oos_expectancy_positive", summary + "; canlı ileri kayıt doğrulaması bekleniyor"
            )
        }
    return {
        "oos_expectancy_positive": unknown(
            "oos_expectancy_positive", f"replay örneği yetersiz (n={family.resolved})"
        )
    }


def evidence_line(setup: Any, evidence: ReplayEvidence | None = None) -> str:
    """One line of family-level history for the panel; never a calibrated per-signal probability."""

    evidence = evidence or default_replay_evidence()
    family = evidence.family(setup)
    if family is None:
        reason = evidence.detail if evidence.status != "OK" else "bu setup ailesi için replay sonucu yok"
        return f"Geçmiş test: YOK ({reason})"
    low, high = family.hit_rate_ci
    return (
        f"Geçmiş test (aile, Binance spot 2020-2026): hedef-önce-stop %{family.hit_rate * 100:.0f} "
        f"[%{low * 100:.0f}–%{high * 100:.0f}] · başabaş %{family.breakeven_hit_rate * 100:.0f} · "
        f"ort. {family.mean_r:+.2f}R (n={family.resolved}) → {family.verdict}"
    )


def family_disqualified(setup: Any, evidence: ReplayEvidence | None = None) -> bool:
    family = (evidence or default_replay_evidence()).family(setup)
    return family is not None and family.verdict in NO_EDGE_VERDICTS


def evidence_status_text(
    evidence: ReplayEvidence | None = None,
    liquid_evidence: LiquidEvidence | None = None,
    listing_evidence: ListingEvidence | None = None,
) -> str:
    evidence = evidence or default_replay_evidence()
    liquid_evidence = liquid_evidence or default_liquid_evidence()
    listing_evidence = listing_evidence or default_listing_evidence()
    if liquid_evidence.status == "OK":
        verdicts = sorted({liquid_evidence.groups[n].verdict for n in LIQUID_LIST_GROUPS if n in liquid_evidence.groups})
        liquid = f"Likit-100 replay — İlk 3: {', '.join(verdicts) or '?'}"
    else:
        liquid = f"Likit-100 replay uygulanamıyor ({liquid_evidence.detail}) → azami WATCH"
    if listing_evidence.status == "OK":
        counts: dict[str, int] = {}
        for group in listing_evidence.groups.values():
            counts[group.verdict] = counts.get(group.verdict, 0) + 1
        listing = "Yeni listeleme replay — " + ", ".join(f"{v} {c}/{len(listing_evidence.groups)}"
                                                      for v, c in sorted(counts.items()))
    else:
        listing = f"Yeni listeleme replay uygulanamıyor ({listing_evidence.detail})"
    liquid = f"{liquid} · {listing}"
    if evidence.status != "OK":
        return f"Kanıt durumu: taktik replay uygulanamıyor ({evidence.detail}) → azami WATCH · {liquid}"
    groups: dict[str, list[str]] = {}
    for family in evidence.families.values():
        groups.setdefault(family.verdict, []).append(family.setup)
    parts = [f"{verdict}: {', '.join(sorted(names))}" for verdict, names in sorted(groups.items())]
    return f"Kanıt durumu: taktik replay — {' · '.join(parts)} · {liquid}"


def live_vs_replay_text(records: Sequence[Any], evidence: ReplayEvidence | None = None) -> str:
    """Forward-ledger outcomes next to the replay expectation (spec §32, §34).

    Descriptive only: a gap is a reason to investigate, never to retune.
    """

    evidence = evidence or default_replay_evidence()
    parts = []
    for row in summarize(records)[1:]:
        if row.resolved == 0:
            continue
        family = evidence.family(row.setup)
        expected = f"replay {family.mean_r:+.2f}R" if family else "replay yok"
        live = f"{row.mean_r:+.2f}R" if row.mean_r is not None else "?"
        parts.append(f"{row.setup} canlı {live} (n={row.resolved}) vs {expected}")
    if not parts:
        return "Canlı vs replay: henüz çözümlenmiş canlı kayıt yok"
    return "Canlı vs replay: " + " · ".join(parts) + " — n<30 iken fark anlamlı değildir"


def _shared_unknowns() -> dict[str, Check]:
    return {
        "oos_expectancy_positive": unknown("oos_expectancy_positive", NO_BACKTEST),
        "parameters_in_tested_zone": unknown("parameters_in_tested_zone", "parametreler test edilmedi"),
        "probabilities_calibrated": unknown("probabilities_calibrated", NO_PROBABILITY),
        "net_ev_positive": unknown("net_ev_positive", NO_EV),
        "ev_margin_sufficient": unknown("ev_margin_sufficient", NO_EV),
        "correlation_acceptable": unknown("correlation_acceptable", "portföy durumu yok (SHADOW)"),
        "drawdown_within_limits": unknown("drawdown_within_limits", "hesap yok (SHADOW)"),
        "execution_healthy": failed("execution_healthy", "execution motoru yok (SHADOW)"),
    }


def _freshness(generated_at: Any, now: int, max_age_seconds: int) -> Check:
    try:
        ts = int(generated_at)
    except (TypeError, ValueError):
        return unknown("data_fresh", "tarama zamanı okunamadı")
    age = now - ts
    if ts <= 0 or age < 0:
        return failed("data_fresh", "tarama zamanı geçersiz")
    if age > max_age_seconds:
        return failed("data_fresh", f"veri {age} sn eski (limit {max_age_seconds} sn)")
    return passed("data_fresh", f"veri {age} sn önce")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def tactical_gate(
    item: Mapping[str, Any],
    *,
    now: int,
    max_age_seconds: int,
    evidence: ReplayEvidence | None = None,
) -> GateDecision:
    symbol = str(item.get("symbol") or "?")
    answers = _shared_unknowns()
    answers["data_fresh"] = _freshness(item.get("decision_at"), now, max_age_seconds)
    plan = item.get("plan") or {}
    if plan:
        answers.update(_replay_checks(item.get("setup"), evidence or default_replay_evidence()))
        answers["parameters_in_tested_zone"] = unknown(
            "parameters_in_tested_zone", "yalnızca mevcut parametre noktası test edildi; pertürbasyon yok"
        )
    structure = str(item.get("structure_4h") or "")
    if not plan:
        reason = ", ".join(item.get("reasons") or ["NO_VALID_SETUP"])
        answers["regime_compatible"] = (
            failed("regime_compatible", "4H yapı ayı")
            if structure == "BEARISH"
            else failed("regime_compatible", f"geçerli setup yok ({reason})")
        )
        answers["liquid_enough"] = (
            failed("liquid_enough", "spread kapısı geçilemedi")
            if "SPREAD_TOO_WIDE" in (item.get("reasons") or [])
            else passed("liquid_enough", "MEXC spread kapısı")
        )
        answers["stop_at_invalidation"] = unknown("stop_at_invalidation", "plan yok")
        return evaluate_gate(symbol, answers)
    answers["liquid_enough"] = passed("liquid_enough", "MEXC spread kapısı geçti")
    answers["regime_compatible"] = (
        passed("regime_compatible", f"tasarım kuralı: 4H {structure} (kanıtla doğrulanmadı)")
        if structure in {"BULLISH", "RANGE_OR_TRANSITION"}
        else failed("regime_compatible", f"4H yapı {structure or 'bilinmiyor'}")
    )
    stop = _finite(plan.get("hard_stop"))
    invalidation = _finite(plan.get("technical_invalidation"))
    entry_low = _finite(plan.get("entry_low"))
    if None in (stop, invalidation, entry_low):
        answers["stop_at_invalidation"] = unknown("stop_at_invalidation", "plan fiyatları okunamadı")
    elif stop < invalidation < entry_low:
        answers["stop_at_invalidation"] = passed(
            "stop_at_invalidation", "hard stop teknik geçersizliğin altında"
        )
    else:
        answers["stop_at_invalidation"] = failed(
            "stop_at_invalidation", "stop teknik geçersizlik seviyesine bağlı değil"
        )
    return evaluate_gate(symbol, answers)


def liquid_long_gate(
    item: Mapping[str, Any],
    *,
    market_regime: str | None,
    generated_at: Any,
    now: int,
    max_age_seconds: int,
    evidence: LiquidEvidence | None = None,
) -> GateDecision:
    symbol = str(item.get("symbol") or "?")
    answers = _shared_unknowns()
    answers.update(_liquid_checks(evidence or default_liquid_evidence()))
    answers["parameters_in_tested_zone"] = unknown(
        "parameters_in_tested_zone", "yalnızca mevcut ağırlıklar test edildi; pertürbasyon yok"
    )
    answers["data_fresh"] = _freshness(generated_at, now, max_age_seconds)
    spread = _finite((item.get("metadata") or {}).get("spread_bps"))
    answers["liquid_enough"] = (
        passed("liquid_enough", f"Likit-100 evreni, spread {spread:.1f} bp")
        if spread is not None
        else unknown("liquid_enough", "spread verisi yok")
    )
    regime = str(market_regime or "UNKNOWN")
    if regime == "RISK_ON":
        answers["regime_compatible"] = passed("regime_compatible", "tasarım kuralı: RISK_ON (kanıtla doğrulanmadı)")
    elif regime in {"RISK_OFF", "CAPITULATION"}:
        answers["regime_compatible"] = failed("regime_compatible", f"rejim {regime}: long momentum için uygun değil")
    else:
        answers["regime_compatible"] = unknown("regime_compatible", f"rejim {regime}")
    answers["stop_at_invalidation"] = unknown("stop_at_invalidation", "stop/geçersizlik planı üretilmiyor")
    return evaluate_gate(symbol, answers)


def status_line(decision: GateDecision) -> str:
    missing = [
        label for key, label in (
            ("oos_expectancy_positive", "OOS test"),
            ("probabilities_calibrated", "kalibrasyon"),
            ("net_ev_positive", "EV"),
        )
        if any(c.key == key and not c.passed for c in decision.checks)
    ]
    blocking = [
        c.detail for c in decision.checks
        if c.key in {"data_fresh", "liquid_enough", "regime_compatible", "stop_at_invalidation"}
        and c.status.value == "FAIL"
    ]
    disqualifying = [
        c.detail for c in decision.checks
        if c.key in DISQUALIFYING_EVIDENCE and c.status is CheckStatus.FAIL
    ]
    text = f"Durum: {decision.status}"
    if blocking:
        text += " — " + "; ".join(blocking)
    elif disqualifying:
        text += " — " + disqualifying[0]
    elif missing:
        text += " — kanıt yok: " + ", ".join(missing)
    return text
