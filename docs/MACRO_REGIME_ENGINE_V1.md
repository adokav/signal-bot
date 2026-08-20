# Global Liquidity & Macro Regime Engine v1

## Purpose

The macro engine is a shared context layer for the signal bot. It must not select coins, create entries, or authorize orders. Its job is to describe the macro regime, expose uncertainty and tail risk, and provide a bounded risk context to downstream research modules.

## Core outputs

- `regime`: `RISK_ON`, `NEUTRAL`, `RISK_OFF`, or `DATA_INSUFFICIENT`
- `long_permission`: `OPEN`, `RESTRICTED`, `CLOSED`, or `DATA_INSUFFICIENT`
- `risk_multiplier`: bounded context value; never an order instruction
- `confidence`: evidence completeness/quality, not predicted return probability
- `tail_risk`: separate veto layer that cannot be averaged away
- `reasons`: auditable evidence summary

## Required factor families

1. US nominal rates: 2Y, 10Y, 30Y, curve changes and rate velocity.
2. US real rates: especially 10Y real yield and changes.
3. Dollar conditions: broad USD/DXY proxies plus USDJPY context.
4. Treasury liquidity: buyback schedule/results, TGA, bill/coupon issuance and refunding context.
5. Japan carry: JGB curve, USDJPY, BOJ event/repricing context and carry-stress evidence.
6. Crypto leverage: open interest, funding, basis, liquidations and options/dealer-risk evidence when reliable.

Stablecoin liquidity is a research candidate, not a mandatory v1 factor until its incremental predictive value is demonstrated out of sample.

## Point-in-time rule

Every observation must preserve:

- `event_time`
- `release_time`
- `available_time`
- `ingested_time`
- `value`
- `source`
- `revision` when applicable

A decision at time T may use only information with `available_time <= T`. Revised macro data must not be silently substituted into historical decisions.

## Policy-event transmission

A policy event is not automatically bullish or bearish. Treasury buybacks, FOMC decisions, BOJ decisions and similar events must be separated from the market transmission that follows them.

For example, a Treasury buyback event should be evaluated through subsequent changes in long yields, real yields, USD conditions, risk assets and BTC rather than encoded as `BUYBACK = RISK_ON`.

## Tail-risk veto

Base factor scores and tail risk are separate. A high average macro score cannot erase an extreme carry/liquidity stress condition. The first implementation must support explicit `RESTRICTED` and `CLOSED` states even when the base regime is risk-on.

## Anti-overfit rules

- No hand-tuned permanent factor weights presented as empirically optimal.
- Initial sign assumptions must be documented as economic hypotheses.
- Weight calibration requires point-in-time walk-forward evaluation.
- No future/revised observations in historical replay.
- Thresholds must be evaluated across multiple regimes, not one bull market.
- Report forward return, drawdown, volatility and downstream setup hit-rate effects; do not optimize only total return.

## Provider plan

Prefer primary/official sources where practical:

- US Treasury: buyback schedule/results, issuance/refunding and TGA-related data.
- Federal Reserve/FRED: Treasury yields, real yields and related official macro series.
- Bank of Japan: policy calendar and official Japanese time series.
- Derivatives providers: crypto leverage inputs, with provider timestamp and coverage recorded.

Provider adapters must fail closed on stale, missing, future-dated or ambiguous observations.

## Integration boundary

The macro engine is shared by BTC/ETH Tactical Long, Liquid 100 Long, New Listing and Memecoin research modules, but it does not replace their technical, liquidity, security or execution checks.

Macro answers: `What regime and risk context are we in?`

Technical/microstructure engines answer: `Is there a valid setup now?`

Execution/risk infrastructure remains a separate boundary.

## Telegram target

A future `Macro Regime` view should show regime, confidence, factor states, tail risk, major evidence, next scheduled macro events and regime changes. Telegram output must distinguish observed facts from model interpretation.

## Validation sequence

1. Data contract and provider capability audit.
2. Point-in-time archive.
3. Deterministic factor transformations.
4. Historical replay covering multiple crypto regimes.
5. Walk-forward threshold/weight research.
6. Shadow integration with downstream long engines.
7. Only after evidence review: bounded influence on live risk context.

This v1 document deliberately does not claim that Treasury buybacks, BOJ policy or any single macro variable causes BTC moves. Those are hypotheses to test through observable transmission channels.