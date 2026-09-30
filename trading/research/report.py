"""Strategy report contract (spec §14, §42, §43).

A backtest number without its market logic, its failure questions and its
concrete blow-up scenarios is not a research result. This module refuses to
render a report unless:

- the hypothesis states the market mechanism and a rationale for every
  feature and parameter (§14);
- every §42 fragility question has an answer — "not measured yet" is an
  acceptable honest answer, silence is not;
- at least three concrete blow-up scenarios exist, each with cause, early
  warning, loss mechanism and protection (§43).

The length check is a floor against empty boilerplate; it cannot judge
whether a scenario is specific. That judgement stays with the reviewer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping


MIN_TEXT_LENGTH = 25
MIN_BLOW_UP_SCENARIOS = 3

FRAGILITY_QUESTIONS: dict[str, str] = {
    "regime_that_kills_edge": "Hangi market rejimi bu edge'i yok eder?",
    "most_important_feature": "En önemli feature hangisi?",
    "performance_without_top_feature": "O feature çıkarılırsa performans ne olur?",
    "possible_data_leakage": "Sonuç data leakage olabilir mi?",
    "is_it_just_beta": "Getiri aslında yalnızca BTC beta olabilir mi?",
    "microstructure_risk": "Exchange microstructure live edge'i yok edebilir mi?",
    "slippage_risk": "Slippage beklenenden yüksek olabilir mi?",
    "hidden_correlation": "Korelasyon sandığımızdan yüksek olabilir mi?",
    "shared_risk_factor": "Birden çok coin aslında aynı risk faktörünü mü taşıyor?",
    "liquidity_shock": "Likidite şoku sistemi bozabilir mi?",
    "funding_anomaly": "Funding anomalisi edge'i tersine çevirebilir mi?",
}


def _require_text(value: str, what: str) -> None:
    if not isinstance(value, str) or len(value.strip()) < MIN_TEXT_LENGTH:
        raise ValueError(f"{what} must be a specific statement (>= {MIN_TEXT_LENGTH} chars)")


@dataclass(frozen=True)
class StrategyHypothesis:
    name: str
    market_logic: str
    feature_rationale: Mapping[str, str]
    parameter_rationale: Mapping[str, str]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("hypothesis needs a name")
        _require_text(self.market_logic, "market_logic")
        if not self.feature_rationale or not self.parameter_rationale:
            raise ValueError("every feature and parameter needs a rationale")
        for key, text in {**self.feature_rationale, **self.parameter_rationale}.items():
            _require_text(text, f"rationale for {key}")


@dataclass(frozen=True)
class BlowUpScenario:
    title: str
    cause: str
    early_warning: str
    loss_mechanism: str
    protection: str

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("scenario needs a title")
        for name in ("cause", "early_warning", "loss_mechanism", "protection"):
            _require_text(getattr(self, name), f"{self.title}: {name}")


@dataclass(frozen=True)
class StrategyReport:
    hypothesis: StrategyHypothesis
    fragility_answers: Mapping[str, str]
    blow_up_scenarios: tuple[BlowUpScenario, ...]
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = [key for key in FRAGILITY_QUESTIONS if key not in self.fragility_answers]
        if missing:
            raise ValueError(f"unanswered fragility questions: {missing}")
        for key in FRAGILITY_QUESTIONS:
            _require_text(self.fragility_answers[key], f"answer to {key}")
        if len(self.blow_up_scenarios) < MIN_BLOW_UP_SCENARIOS:
            raise ValueError(f"need at least {MIN_BLOW_UP_SCENARIOS} blow-up scenarios")

    def render_markdown(self) -> str:
        h = self.hypothesis
        lines = [f"# Strateji raporu — {h.name}", "", "## Hipotez (piyasa mantığı)", "", h.market_logic, ""]
        lines += ["### Feature gerekçeleri", ""]
        lines += [f"- **{k}** — {v}" for k, v in h.feature_rationale.items()]
        lines += ["", "### Parametre gerekçeleri", ""]
        lines += [f"- **{k}** — {v}" for k, v in h.parameter_rationale.items()]
        if self.evidence:
            lines += [
                "",
                "## Kanıt (makine çıktısı)",
                "",
                "```json",
                json.dumps(self.evidence, indent=2, ensure_ascii=False, default=str),
                "```",
            ]
        lines += ["", "## Stratejiyi bozabilecek unsurlar", ""]
        for key, question in FRAGILITY_QUESTIONS.items():
            lines += [f"**{question}**", "", self.fragility_answers[key], ""]
        lines += ["## WHAT COULD BLOW UP THIS ACCOUNT?", ""]
        for index, s in enumerate(self.blow_up_scenarios, 1):
            lines += [
                f"### {index}. {s.title}",
                "",
                f"- **Olası sebep:** {s.cause}",
                f"- **Erken uyarı sinyali:** {s.early_warning}",
                f"- **Muhtemel zarar mekanizması:** {s.loss_mechanism}",
                f"- **Koruyucu önlem:** {s.protection}",
                "",
            ]
        return "\n".join(lines).rstrip() + "\n"
