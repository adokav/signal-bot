# Signal Bot — Codex / Copilot Review Instructions

Review this repository as a safety-critical trading research system. Do not optimize for speed, novelty, or apparent sophistication at the expense of correctness. Be adversarial: try to find ways the code could create false confidence, leak future information, mis-handle stale data, expose secrets, or accidentally authorize trading.

## 1. Point-in-time integrity is non-negotiable

- Flag any look-ahead bias, target leakage, future data leakage, survivorship bias, or use of revised data that would not have been available at the decision timestamp.
- Open/future candles must never be used as closed evidence. If a provider includes the currently forming candle, ingestion should exclude it while preserving strict downstream guards.
- Historical macro/fundamental data must preserve `observation_time`, `available_time`, and `ingested_time` semantics. A value must not be visible before `available_time`.
- Historical replay must use only information available at the replay cut. Revisions published later must not leak backward.
- Training/test separation for time series must respect chronology. Purge/embargo overlapping forward-label windows where needed.

## 2. Fail closed, never fail open

- Missing, stale, malformed, ambiguous, or provider-failed data must not silently become neutral, zero, healthy, or bullish/bearish evidence.
- A health endpoint must not report healthy solely because a status flag says completed; required artifacts must exist, be readable, and satisfy expected structural checks.
- If a hard risk gate cannot be evaluated, prefer `UNKNOWN` / no-action over assuming safety.
- Do not weaken validation merely to suppress runtime errors.

## 3. Secret and credential safety

- Never expose API keys, bot tokens, wallet secrets, seed phrases, private keys, auth headers, signed URLs, or credentials through logs, exceptions, health endpoints, Telegram messages, test fixtures, or committed files.
- Treat exception strings from HTTP libraries as potentially secret-bearing because prepared request URLs can contain query parameters such as API keys.
- Public/unauthenticated observability endpoints must use explicit allowlists of safe fields. Do not return raw provider responses, request URLs, or raw exception text.
- Secrets must come from environment/secret storage, not source control.

## 4. Trading authority boundaries

- Research, macro, factor, replay, evidence, and scoring modules must not implicitly gain order authority.
- A macro factor or regime must not by itself create a trade, position size, leverage value, or order permission.
- Preserve explicit boundaries such as `can_authorize_trade = false` where applicable.
- Flag any path where a research score can bypass trigger, risk, stop, liquidity, or lifecycle validation.
- SHADOW/research mode must remain non-executing unless a separate, explicit production authorization path is intentionally introduced and reviewed.

## 5. Market-data quality and freshness

- Verify timestamp units, timezone normalization, exchange close-time semantics, ordering, duplication, gaps, stale data, and malformed OHLC.
- Reject impossible OHLC relationships and non-finite numeric values.
- Provider retries/concurrency must not duplicate state or race writes.
- Persisted state and research artifacts should be written atomically when partial writes could create false healthy state.
- Do not treat provider-specific semantics as universal without explicit normalization.

## 6. Macro research discipline

- Do not assign bullish/bearish signs, weights, regime labels, or trading multipliers merely because an economic narrative sounds plausible.
- Distinguish policy announcement, scheduled capacity, realized operation, issuance, redemptions, balance-sheet flows, and market transmission.
- Scheduled Treasury buyback capacity is not the same as realized liquidity injection.
- Gross issuance minus buybacks must not be mislabeled as full net market supply/liquidity when maturities, redemptions, TGA, SOMA, or other relevant flows are omitted.
- Candidate macro factors must earn promotion through point-in-time, out-of-sample evidence.

## 7. Statistical and ML review requirements

- Flag leakage from feature engineering, normalization, ranking, threshold selection, hyperparameter tuning, and feature selection performed with test/future data.
- Thresholds/quantiles/scalers must be learned on training data only and then applied out of sample.
- Overlapping forward-return labels must not be counted as independent observations without appropriate thinning/purging.
- Require enough observations before claiming predictive value. Small-sample results should remain explicitly inconclusive.
- Prefer expectancy, MFE, MAE, drawdown, calibration, and cost-adjusted outcomes over raw hit rate alone.
- Any model comparison must use the same point-in-time dataset and validation protocol.
- Flag claims of alpha, regime quality, or performance that are not supported by reproducible out-of-sample evidence.

## 8. Tactical long / signal lifecycle safety

- Distinguish `setup detected` from `actionable now`.
- Entry zone, technical invalidation, hard stop, setup age, current-price distance, slippage/costs, and R/R freshness should not be conflated.
- A triggered setup that has moved materially beyond its valid entry region must not be presented as an immediate market-buy instruction.
- Lifecycle transitions should be explicit and monotonic where appropriate; stale/invalidated setups should not silently reactivate.
- Stop/invalidation logic must not use future candles or hindsight.

## 9. New-listing / memecoin risk review

- Treat token identity ambiguity, mint/freeze authority, honeypot/sellability, LP state, holder concentration, insider/bundle patterns, wash trading, fake volume, extreme slippage, and missing provenance as first-class risks.
- Missing security/on-chain data is not evidence of safety.
- Do not rank a token highly just because headline volume, acceleration, or recent price movement is strong if manipulation risk is unresolved.
- Research ranking must remain distinct from capital authorization and automatic execution.

## 10. Automatic order and wallet safety

If code touches order submission, wallet signing, approvals, or capital allocation, apply maximum scrutiny:

- Require explicit environment/config enablement and clear separation from SHADOW mode.
- Require hard caps on notional, leverage, slippage, per-trade risk, and aggregate exposure.
- Prevent duplicate orders from retries, restarts, race conditions, or repeated Telegram callbacks.
- Check idempotency, nonce/order identifiers, partial fills, cancel/replace behavior, and exchange rejection handling.
- Never log signing material or private wallet data.
- A provider outage or ambiguous response must not be interpreted as a successful order.

## 11. Testing expectations

For changed behavior, look for regression tests that cover both success and failure paths. Prioritize tests for:

- future/open candle rejection;
- point-in-time revision visibility;
- stale/missing data fail-closed behavior;
- malformed/non-finite input;
- API-key/token redaction;
- incomplete research artifacts reporting unhealthy;
- train/test leakage and purge behavior;
- duplicate state/order prevention;
- trading-authority boundaries;
- provider failures and partial data.

If a change modifies a safety invariant without a regression test, call it out.

## 12. Review style

- Be specific and evidence-based. Quote the exact behavior/path that is unsafe.
- Prioritize P0/P1/P2 issues that can leak secrets, corrupt point-in-time research, create false healthy state, or authorize unintended trading.
- Do not praise code merely for being complex or quantitative.
- Do not assume comments/docstrings prove behavior; inspect the implementation path.
- Treat apparently harmless observability, logging, caching, persistence, timestamp, and retry changes as security/financial-risk relevant.
- When uncertain, state the uncertainty and explain what evidence or test would resolve it.

Core principle: **correctness and honesty before speed; unknown is not safe; research evidence is not trade authority.**
