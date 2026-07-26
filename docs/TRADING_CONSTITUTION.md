# Trading Constitution v1

## Status

This document defines the non-negotiable decision principles for the research,
risk, portfolio and execution layers of `signal-bot`.

The strategic inspiration is Sun Tzu's *The Art of War*. The source is treated as
a framework for disciplined decision-making, not as empirical proof. Every
principle translated into trading must remain testable, falsifiable and
compatible with mathematics, probability and statistics.

## Mission

Build a trading research and decision system that:

1. preserves capital before seeking return;
2. acts only when estimated edge survives uncertainty and implementation cost;
3. separates evidence, decision, risk permission and execution;
4. records every decision so that claims can be tested and rejected;
5. prevents emotional overrides at the moment of execution.

## Core doctrine

### 1. Survival precedes return

No strategy, signal or operator may expose the portfolio to an unacceptable
probability of ruin.

Required consequences:

- risk is budgeted before opportunities are ranked;
- a single trade, day, strategy or venue must not be able to destroy the system;
- unknown or stale data causes abstention, not fabricated confidence;
- recovery of past losses is never a valid reason to increase risk.

### 2. No battle without advantage

The system is allowed to return zero opportunities.

A top-ranked candidate is not necessarily a valid trade. A trade may proceed
only when its conservative, cost-adjusted expected value is positive:

```text
lower_confidence_bound(expected_value_after_costs) > 0
```

The exact estimator and confidence method must be versioned and validated.

### 3. Know the terrain

A signal is not a decision. Market regime, liquidity, spread, order-book depth,
slippage, funding, venue health and event risk are part of the opportunity.

The same feature pattern in different market microstructure conditions is a
different opportunity.

### 4. Speed is not haste

The system should react quickly after evidence is complete, but it must not
trade on incomplete candles, unconfirmed listings, stale snapshots or
unverified provider data.

### 5. Concentrate force; do not scatter it

Capital is allocated to independent risk opportunities, not merely to symbols.
Correlated positions share a common risk budget.

Position size must depend on:

- estimated edge and its uncertainty;
- stop distance or loss distribution;
- liquidity and slippage capacity;
- portfolio correlation and concentration;
- current drawdown and regime risk.

### 6. Risk has veto authority

The decision chain is:

```text
Market State -> Opportunity Evidence -> Cost Feasibility
             -> Risk Permission -> Portfolio Allocation -> Execution
```

The Risk Engine may veto an opportunity. No upstream module may override that
veto during live operation.

### 7. Evidence must not be counted twice

Correlated features must not create artificial confidence through repeated
weighting. Feature groups and dependency checks are required before a scoring
model is accepted.

Missing data is neutral or confidence-reducing. It is never rewarded.

### 8. Every claim must be falsifiable

Every strategy rule must specify before testing:

- hypothesis;
- target outcome and horizon;
- required data;
- rejection criterion;
- transaction-cost model;
- validation split;
- regime scope;
- expected failure modes.

A rule without a defined failure condition is not eligible for production.

### 9. The past may train; it may not leak

Historical replay must reproduce what was knowable at each timestamp.
Forbidden forms of leakage include:

- future candles or revised data;
- survivor-only universes;
- using later listing metadata at discovery time;
- tuning on the final evaluation period;
- selecting only favorable regimes after observing results.

### 10. Costs are part of the strategy

Evaluation must include, where applicable:

- fees;
- spread;
- slippage;
- funding;
- market impact;
- latency;
- failed or partial fills;
- capital lock-up.

Gross edge is not tradable edge.

### 11. Adaptation requires evidence

No rule is sacred, but no rule changes without evidence. Model, feature,
threshold and risk changes must be versioned and evaluated out of sample.

Parameter stability is preferred to a single optimized peak.

### 12. Human control exists outside the trade moment

Humans may research, approve deployments and lower risk. During live decision
execution they may not:

- widen a stop because of hope;
- raise size to recover a loss;
- bypass a veto because of FOMO;
- reinterpret a failed rule after seeing the outcome.

Emergency actions may reduce or close risk, never increase it.

## Decision states

Every evaluated opportunity ends in exactly one state:

- `ENGAGE`: evidence, risk and implementation requirements are satisfied;
- `OBSERVE`: potentially useful evidence exists but is incomplete;
- `ABSTAIN`: edge is absent, uncertainty is excessive or risk is unacceptable;
- `INVALID`: data integrity or model validity is compromised.

## Scientific standard

Backtests and research reports must use methods appropriate to the hypothesis,
including as needed:

- chronological replay;
- out-of-sample and walk-forward evaluation;
- purging and embargo for overlapping labels;
- bootstrap confidence intervals;
- multiple-testing controls;
- Monte Carlo trade-order stress tests;
- regime-specific analysis;
- parameter-sensitivity analysis;
- realistic transaction-cost modelling.

No single metric is sufficient. At minimum, reports must include expectancy,
sample size, uncertainty interval, drawdown, turnover, cost sensitivity and
performance by regime.

## Governance

Every material change must document:

1. problem;
2. hypothesis;
3. proposed mechanism;
4. data and test design;
5. success and rejection criteria;
6. risks and complexity cost;
7. rollback plan.

This constitution may be amended only through a reviewed pull request that
states why the amendment improves scientific validity, risk control or system
clarity.
