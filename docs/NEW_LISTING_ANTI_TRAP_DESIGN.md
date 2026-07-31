# New Listing Anti-Trap Design

## Problem

The current new-listing score rewards announcement recency, open Spot trading,
headline volume and short-window volume acceleration. Those variables can describe
real demand, but they can also describe a manufactured pump. A high score must not
mean "price is moving fast"; it must mean "the setup has survived manipulation and
implementation-risk tests".

## Strategic principle

Sun Tzu is used here as a design metaphor, not as empirical proof:

- do not attack strength displayed for bait;
- verify terrain before committing capital;
- preserve optionality when intelligence is incomplete;
- prefer avoiding a bad engagement to forcing a trade.

Every rule below must later be validated on point-in-time historical cohorts.

## Architecture change

Replace one-dimensional ranking with two independent axes:

1. `opportunity_score`: evidence for sustainable post-listing demand.
2. `trap_risk_score`: evidence for crowding, manipulation, poor liquidity or
   asymmetric supply risk.

A candidate may be highly interesting and highly dangerous at the same time. The
Telegram report must show both numbers. Ranking uses conservative opportunity,
not raw excitement:

```text
conservative_score = opportunity_score - trap_risk_penalty
```

A hard trap gate can still veto the candidate regardless of score.

## First anti-trap evidence set

### Hard gates

- identity or Spot status unresolved;
- missing circulating supply when supply verification is required;
- spread above configured maximum;
- 24h drawdown below configured loss boundary;
- extreme first-pump extension;
- fundamental stage `HIGH_RISK`;
- social stage `SUSPICIOUS` or `NEGATIVE_EVENT`.

### Trap-risk components

- price extension: large positive 24h move;
- acceleration without depth: high 5m acceleration with low quote volume;
- thin-book proxy: wide spread;
- dilution: low circulation percentage / low market-cap-to-FDV ratio;
- concentration proxy: MEXC volume dominates global volume;
- history warning: current price very near a recent ATH after an abrupt move;
- missing intelligence: social/fundamental/provider cooldown must increase
  uncertainty, never opportunity.

### Opportunity components

- verified new Spot event;
- sufficient but not explosive liquidity;
- persistent multi-window participation rather than one 5m spike;
- healthy circulation and dilution ratios;
- organic multi-author social confirmation;
- pullback/base/reclaim structure after initial price discovery;
- executable spread and capacity.

## State machine

A new listing should move through states rather than jump directly to `HOT`:

```text
DISCOVERED -> PRICE_DISCOVERY -> BASE_BUILDING -> CONFIRMED
                         \-> CROWDED / TRAP_RISK / INVALID
```

`HOT` must not be assigned from one scan. It requires persistence across multiple
completed scan windows and no hard trap gate.

## Immediate behavior change

Until historical validation is complete:

- rename the user-facing list from "Patlama Radarı" to "Yeni Listeleme Araştırma";
- show `Opportunity`, `Trap risk`, and machine-readable reasons separately;
- never rank a `HIGH_RISK`, `SUSPICIOUS`, `CROWDED`, or incomplete-data candidate
  as a positive opportunity;
- provider cooldown produces `INTELLIGENCE_INCOMPLETE`, not a high-confidence
  candidate;
- retain all rejected candidates for research.

## Validation plan

Build point-in-time cohorts by listing event and measure forward returns after
fees/slippage at 15m, 1h, 4h, 24h and 72h. Track MFE, MAE, drawdown, probability
of a 20%+ adverse excursion and probability of a 50%+ favourable excursion.

Use walk-forward validation and bootstrap confidence intervals. Any threshold
must be stable across time periods and listing cohorts. A rule that merely fits a
small set of famous pumps is rejected.
