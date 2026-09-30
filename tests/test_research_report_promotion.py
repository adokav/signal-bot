from __future__ import annotations

import pytest

from trading.research.promotion import (
    BACKTEST_CHECKS,
    FAIL,
    NOT_APPLICABLE,
    NOT_RUN,
    PASS,
    PromotionCheck,
    assess_promotion,
)
from trading.research.report import (
    FRAGILITY_QUESTIONS,
    BlowUpScenario,
    StrategyReport,
)
from trading.strategies.tsmom_dossier import (
    TSMOM_BLOW_UP_SCENARIOS,
    TSMOM_FRAGILITY_ANSWERS,
    TSMOM_HYPOTHESIS,
)


def _checks(state: str) -> dict[str, PromotionCheck]:
    return {name: PromotionCheck(name, state, "detail") for name in BACKTEST_CHECKS}


def test_backtest_alone_can_never_promote_to_live():
    readiness = assess_promotion(_checks(PASS))
    assert readiness.stage == "BACKTEST_PASSED_PAPER_REQUIRED"
    assert readiness.promotable_to_live is False
    assert {c.name for c in readiness.checks if c.state == NOT_RUN} == {
        "paper_trading", "small_live_deployment", "kill_switch_test"
    }


def test_any_fail_rejects_and_not_run_is_incomplete():
    checks = _checks(PASS)
    checks["deflated_sharpe"] = PromotionCheck("deflated_sharpe", FAIL, "low")
    assert assess_promotion(checks).stage == "REJECTED_AT_BACKTEST"
    checks = _checks(PASS)
    checks["parameter_perturbation"] = PromotionCheck("parameter_perturbation", NOT_RUN, "skipped")
    assert assess_promotion(checks).stage == "INCOMPLETE_EVIDENCE"
    checks = _checks(PASS)
    checks["probability_calibration"] = PromotionCheck("probability_calibration", NOT_APPLICABLE, "n/a")
    assert assess_promotion(checks).stage == "BACKTEST_PASSED_PAPER_REQUIRED"


def test_checklist_must_be_complete():
    checks = _checks(PASS)
    checks.pop("walk_forward")
    with pytest.raises(ValueError):
        assess_promotion(checks)


def test_tsmom_dossier_renders_with_blow_up_section_last():
    report = StrategyReport(
        hypothesis=TSMOM_HYPOTHESIS,
        fragility_answers=TSMOM_FRAGILITY_ANSWERS,
        blow_up_scenarios=TSMOM_BLOW_UP_SCENARIOS,
        evidence={"trades": 1},
    )
    text = report.render_markdown()
    last_h2 = [line for line in text.splitlines() if line.startswith("## ")][-1]
    assert last_h2 == "## WHAT COULD BLOW UP THIS ACCOUNT?"
    for question in FRAGILITY_QUESTIONS.values():
        assert question in text
    assert "Erken uyarı sinyali" in text


def test_report_refuses_missing_answers_and_thin_scenarios():
    answers = dict(TSMOM_FRAGILITY_ANSWERS)
    answers.pop("funding_anomaly")
    with pytest.raises(ValueError, match="unanswered"):
        StrategyReport(TSMOM_HYPOTHESIS, answers, TSMOM_BLOW_UP_SCENARIOS)
    with pytest.raises(ValueError, match="at least"):
        StrategyReport(TSMOM_HYPOTHESIS, TSMOM_FRAGILITY_ANSWERS, TSMOM_BLOW_UP_SCENARIOS[:2])
    with pytest.raises(ValueError):
        BlowUpScenario(title="x", cause="market", early_warning="price", loss_mechanism="loss", protection="stop")
