# Signal Bot — Codex Repository Instructions

Treat this repository as a safety-critical trading research system. Correctness and honesty come before speed, novelty, or apparent sophistication. Be adversarial in review: look for false confidence, future-data leakage, stale/ambiguous data, secret exposure, unsafe execution paths, and research logic that can accidentally become trading authority.

Keep this file semantically synchronized with `.github/copilot-instructions.md`; the latter exists for GitHub Copilot review, while this root `AGENTS.md` is durable repository guidance for Codex.

## 1. Point-in-time integrity is non-negotiable
- Flag look-ahead bias, target leakage, future-data leakage, survivorship bias, or revised data used before it was available.
- Open/future candles must never be treated as closed evidence. Provider ingestion may remove a single forming candle, but strict downstream validation must remain.
- Preserve `observation_time`, `available_time`, and `ingested_time`; no value may be visible before `available_time`.
- Historical replay must use only information available at the replay cut; later revisions must not leak backward.
- Time-series training/test splits must preserve chronology and purge/embargo overlapping forward-label windows where needed.

## 2. Fail closed, never fail open
- Missing, stale, malformed, ambiguous, or provider-failed data must not silently become neutral, zero, healthy, safe, bullish, or bearish evidence.
- Health/completion checks must verify required artifacts, not merely trust a state flag or completion marker.
- If a hard risk gate cannot be evaluated, prefer `UNKNOWN` / no-action.
- Never weaken validation only to suppress an error.

## 3. Secret and credential safety
- Never expose API keys, bot tokens, wallet secrets, seed phrases, private keys, auth headers, signed URLs, or credentials through source, logs, exceptions, endpoints, Telegram, or fixtures.
- Treat HTTP exception text as secret-bearing; request URLs can include credentials in query parameters.
- Public/unauthenticated observability must explicitly allowlist safe fields and must not expose raw provider errors, request URLs, responses, or exception strings.
- Secrets belong in environment/secret storage only.

## 4. Trading-authority boundaries
- Research, macro, factor, replay, evidence, ranking, and scoring modules must not implicitly gain order authority.
- A macro factor/regime may not by itself create a trade, size, leverage value, or order permission.
- Preserve explicit non-execution boundaries such as `can_authorize_trade = false` where applicable.
- Flag any path that lets research scores bypass trigger, stop, liquidity, lifecycle, or risk validation.
- SHADOW/research mode remains non-executing unless a separate production authorization path is intentionally introduced and reviewed.

## 5. Market-data quality and freshness
- Verify timestamp units, timezone normalization, candle close semantics, ordering, duplicates, gaps, stale data, malformed OHLC, and non-finite values.
- Reject impossible OHLC relationships.
- Retries/concurrency must not duplicate state or race writes.
- Use atomic persistence where partial files could create false healthy/completed state.
- Do not assume provider-specific semantics are universal without explicit normalization.

## 6. Macro research discipline
- Do not assign signs, weights, regime labels, or trading multipliers merely because an economic narrative sounds plausible.
- Distinguish announcements, schedules/capacity, realized operations, issuance, redemptions, balance-sheet flows, and market transmission.
- Scheduled Treasury buyback capacity is not realized liquidity injection.
- Gross issuance minus buybacks is not full net market supply/liquidity when maturities, redemptions, TGA, SOMA, or other flows are omitted.
- Candidate factors must earn promotion through point-in-time, reproducible out-of-sample evidence.
- Any change to the research series set, factor schema, labeling rules, or validation protocol must invalidate stale historical-evidence caches/fingerprints.

## 7. Statistical and ML requirements
- Flag leakage from feature engineering, normalization, ranking, threshold choice, hyperparameter tuning, feature selection, or calibration performed with future/test data.
- Learn thresholds/quantiles/scalers on training data only, then apply out of sample.
- Overlapping forward-return labels must not be treated as independent without thinning/purging.
- Small samples remain inconclusive; do not overstate predictive value.
- Prefer expectancy, MFE, MAE, drawdown, calibration, and cost-adjusted outcomes over hit rate alone.
- Model comparisons must use the same point-in-time dataset and validation protocol.
- Flag unsupported claims of alpha, regime quality, or performance.

## 8. Tactical-long lifecycle safety
- Distinguish `setup detected` from `actionable now`.
- Keep entry zone, technical invalidation, hard stop, setup age, current-price distance, slippage/costs, and R/R freshness distinct.
- A setup that moved materially beyond its valid entry region must not be presented as an immediate market-buy instruction.
- Lifecycle transitions should be explicit and monotonic where appropriate; stale/invalidated setups must not silently reactivate.
- Stop/invalidation logic must not use future candles or hindsight.

## 9. New-listing / memecoin risk
- Treat token identity ambiguity, mint/freeze authority, honeypot/sellability, LP state, holder concentration, insider/bundle patterns, wash trading, fake volume, extreme slippage, and missing provenance as first-class risks.
- Missing on-chain/security data is not evidence of safety.
- Do not rank tokens highly from price/volume acceleration alone while manipulation risk is unresolved.
- Ranking/research must remain separate from capital authorization/execution.

## 10. Automatic orders and wallet safety
If code touches order submission, wallet signing, approvals, or capital allocation, apply maximum scrutiny:
- Require explicit enablement and separation from SHADOW mode.
- Require hard caps on notional, leverage, slippage, per-trade risk, and aggregate exposure.
- Prevent duplicate orders from retries, restarts, races, or repeated callbacks; review idempotency carefully.
- Check partial fills, cancel/replace, provider rejection, nonce/order identifiers, and ambiguous responses.
- Never log signing material/private wallet data.
- Provider outage or ambiguity must never be interpreted as a successful order.

## 11. Regression-test expectations
For changed behavior, require tests for applicable success and failure paths, especially:
- future/open candle rejection;
- point-in-time revision visibility;
- stale/missing data fail-closed behavior;
- malformed/non-finite data;
- credential redaction;
- incomplete/missing research artifacts reporting unhealthy/incomplete;
- historical-cache invalidation when research schema changes;
- train/test leakage and purge behavior;
- duplicate state/order prevention;
- trading-authority boundaries;
- provider failures and partial data.

If a safety invariant changes without regression coverage, call it out.

## 12. Review style
- Be specific and evidence-based; identify the exact unsafe path/behavior.
- Prioritize P0/P1/P2 issues that can expose secrets, corrupt point-in-time research, create false health/completion, or authorize unintended trading.
- Do not assume comments/docstrings prove behavior; inspect implementation paths.
- Treat logging, observability, caching, persistence, timestamps, retries, cache fingerprints, and completion markers as security/financial-risk relevant.
- When uncertain, state the uncertainty and the test/evidence needed to resolve it.

Core principle: **correctness and honesty before speed; unknown is not safe; research evidence is not trade authority.**