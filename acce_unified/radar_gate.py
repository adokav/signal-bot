"""Map live radar output onto the §38 gate and §40 statuses.

The radars detect setups by design rules; none of those setups has been
backtested, no probability is produced, so expectancy, calibration and EV
are UNKNOWN and the best honest status is WATCH. There is no account or
execution engine (SHADOW), so portfolio and drawdown checks are UNKNOWN
and execution health FAILS.

A stale snapshot is a FAIL on data freshness and therefore REJECT: an old
plan must not be presented as current (AGENTS.md §8).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from trading.risk.checks import Check, failed, passed, unknown
from trading.risk.trade_gate import GateDecision, evaluate_gate


NO_BACKTEST = "bu setup ailesi hiç backtest edilmedi; OOS beklenti bilinmiyor"
NO_PROBABILITY = "radar olasılık üretmiyor; kalibrasyon yapılamaz"
NO_EV = "başarı olasılığı olmadan EV hesaplanamaz; R/R tek başına EV değildir"


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


def tactical_gate(item: Mapping[str, Any], *, now: int, max_age_seconds: int) -> GateDecision:
    symbol = str(item.get("symbol") or "?")
    answers = _shared_unknowns()
    answers["data_fresh"] = _freshness(item.get("decision_at"), now, max_age_seconds)
    plan = item.get("plan") or {}
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
) -> GateDecision:
    symbol = str(item.get("symbol") or "?")
    answers = _shared_unknowns()
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
    text = f"Durum: {decision.status}"
    if blocking:
        text += " — " + "; ".join(blocking)
    elif missing:
        text += " — kanıt yok: " + ", ".join(missing)
    return text
