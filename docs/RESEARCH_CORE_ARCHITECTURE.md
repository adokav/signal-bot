# Research Core Architecture v1

## Purpose

The Research Core turns market observations into reproducible, testable
opportunities. It does not authorize live orders.

Its first responsibility is to answer:

> What did the system know at this timestamp, what decision did it make, and
> what happened afterward?

## Boundaries

The Research Core owns:

- timestamped observations;
- feature snapshots;
- market-state snapshots;
- opportunity records;
- decision journals;
- forward outcome labels;
- historical replay;
- research reports and model versions.

It does not own:

- exchange credentials;
- live order submission;
- discretionary operator overrides;
- portfolio accounting for real money.

## Canonical opportunity model

An opportunity is a decision event, not a coin.

```text
Opportunity
  id
  observed_at
  strategy_id
  strategy_version
  symbol
  venue
  market_state_id
  evidence_snapshot_id
  cost_snapshot_id
  decision_state
  confidence
  expected_value
  expected_value_lower_bound
  risk_estimate
  intended_horizon
  rejection_reasons[]
  created_at
```

The model must preserve raw evidence and derived values separately. Derived
scores may always be recomputed from the original snapshot and model version.

## Supporting records

### MarketStateSnapshot

```text
id
observed_at
trend_state
volatility_state
liquidity_state
breadth_state
leverage_state
macro_state
event_risk_state
source_versions
quality_flags[]
```

### EvidenceSnapshot

```text
id
observed_at
symbol
venue
feature_schema_version
features{}
missing_features[]
quality_flags[]
source_timestamps{}
```

### CostSnapshot

```text
id
observed_at
symbol
venue
fee_bps
spread_bps
estimated_slippage_bps
funding_bps
market_impact_bps
capacity_notional
quality_flags[]
```

### DecisionJournal

```text
id
opportunity_id
decided_at
decision_state
model_version
policy_version
reason_codes[]
human_intervention
human_intervention_type
notes
```

### OutcomeLabel

Outcome labels are generated only after the required horizon has elapsed.

```text
id
opportunity_id
label_version
horizon
entry_reference
max_favourable_excursion
max_adverse_excursion
net_return
return_r
realised_volatility
cost_assumption_version
labelled_at
```

## Data invariants

1. `observed_at` is the decision clock; ingestion time is stored separately.
2. No record may use a source timestamp later than `observed_at`.
3. Closed-candle strategies may reference closed candles only.
4. Missing data remains explicit; it is never silently imputed in storage.
5. Feature, policy, model, cost and label schemas are versioned.
6. Outcomes are append-only and must not mutate the original decision.
7. Replaying the same inputs and versions must reproduce the same decision.
8. `ENGAGE` requires a conservative edge lower bound above all estimated costs.
9. `ENGAGE` cannot depend on evidence marked missing, stale or invalid.
10. All decision timestamps are timezone-aware and persisted in UTC.

## Historical replay

Replay advances through historical time in deterministic steps.

At each step it must:

1. expose only data available at that timestamp;
2. reconstruct the eligible universe at that timestamp;
3. compute market state;
4. compute evidence and implementation cost;
5. create all opportunities, including rejected candidates;
6. record the decision journal;
7. continue without evaluating future outcome;
8. label outcomes only after their horizons mature.

### Required replay protections

- point-in-time symbol universe;
- delisted and failed asset retention;
- listing discovery time distinct from later metadata;
- configurable provider delay;
- deterministic clock and random seed;
- no cross-fold preprocessing leakage;
- purge/embargo support for overlapping outcome horizons.

## Initial storage choice

The first implementation uses SQLite because it is deterministic, portable,
inspectable and sufficient for the current scan volume. Storage interfaces avoid
exchange dependencies so a later migration does not alter research semantics.

The first physical table is `opportunities`. Market state, evidence, costs and
reason codes are written as deterministic JSON snapshots. This optimizes for
exact audit replay before analytical convenience. UPDATE and DELETE triggers
make the journal append-only.

Future normalized tables remain:

```text
market_state_snapshots
feature_snapshots
cost_snapshots
decision_journal
outcome_labels
model_versions
replay_runs
```

## Implemented slice

`acce_unified.research` provides:

- immutable `Evidence`, `MarketState`, `CostEstimate` and `Opportunity` records;
- `Decision`, `Direction` and `EvidenceStatus` enums;
- validation against future evidence and future market state;
- explicit missing/stale/invalid evidence handling;
- conservative cost-adjusted `ENGAGE` permission;
- `ResearchStore`, an append-only SQLite journal;
- chronological `list_as_of` access for replay-safe queries.

`tests/test_research_core.py` verifies the point-in-time, cost, missing-data,
uniqueness and append-only invariants. CI runs and compiles this new slice.

## Next implementation sequence

1. Outcome labels as separate append-only facts.
2. Deterministic replay clock and provider interfaces.
3. Scanner adapter that records every candidate and rejection reason.
4. Feature registry with provenance, unit and lookback declarations.
5. Research report with expectancy, uncertainty, drawdown and regime breakdown.

## Acceptance criteria for Research Core v1

- A scan can persist every evaluated candidate, not only the displayed top N.
- Every rejection has one or more machine-readable reason codes.
- Re-running a stored opportunity with the same versions reproduces its
  decision.
- A replay test proves that no source timestamp exceeds the simulated clock.
- Outcome labels cannot be generated before their horizon matures.
- Missing supply or market data cannot increase confidence.
- Existing SHADOW/RADAR behaviour remains unchanged.

## Deferred work

The following are explicitly outside v1:

- model-weight optimization;
- machine-learning ranking;
- automated live execution;
- adaptive online learning;
- high-frequency order-book reconstruction;
- leverage optimization.

They may begin only after the Research Core produces reproducible point-in-time
records and out-of-sample reports.
