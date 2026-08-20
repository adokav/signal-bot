# Memecoin Early Opportunity Radar — Integration v1

## Objective

Integrate a Solana-first memecoin intelligence module into the existing Signal Bot without turning the Telegram process into a monolith and without allowing research code to sign or submit transactions.

The module is not a "100x finder". Its first job is to reject or defer unsafe, illiquid, manipulated or insufficiently observed tokens. Opportunity scoring only starts after survival gates pass.

## System boundary

Signal Bot remains one product and one Telegram control surface, but the memecoin radar is an isolated package with its own discovery, evidence, qualification and lifecycle state.

Target architecture:

```text
Signal Bot / Telegram
        |
        +-- Liquid 100 Long
        +-- BTC/ETH Tactical Long
        +-- Verified New Listings
        +-- Memecoin Early Opportunity
                    |
                    +-- Discovery
                    +-- Identity
                    +-- Contract evidence
                    +-- Liquidity / exit evidence
                    +-- Ownership / cluster evidence
                    +-- Manipulation evidence
                    +-- Wallet / deployer intelligence
                    +-- Opportunity setup

Shared services:
- point-in-time observation archive
- health/status reporting
- alert de-duplication
- future global risk policy
- future execution gateway
```

## Non-negotiable decision order

```text
DISCOVER
  -> IDENTITY
  -> CONTRACT
  -> LIQUIDITY
  -> OWNERSHIP
  -> MANIPULATION
  -> DATA_QUALITY
  -> EXECUTION_FEASIBILITY
  -> WATCH / QUALIFIED
  -> ENTRY_SETUP
```

A FAIL in any hard gate produces REJECT. An UNKNOWN in any hard gate can produce only REJECT or DATA_INSUFFICIENT. Rejected or incomplete tokens never receive an opportunity score.

## Identity

The canonical identity is `SOLANA + mint_address`. Ticker/name are display metadata only. This prevents same-symbol and fake-contract collisions.

## Evidence model

Every claim must preserve:

- source/provider;
- observed_at;
- available_at;
- explicit PASS / FAIL / UNKNOWN state;
- human-readable reason.

Provider labels are evidence, not ground truth. For example a third-party `insider` or `bundler` label is stored as a provider claim. Where practical, the later Helius/on-chain adapter will attach independent transfer/funding/timing evidence.

## Provider plan

### Discovery / market
- DexScreener public API: pair/pool identity, liquidity, price, volume, transactions, pair age.
- Birdeye (candidate provider): new-pair discovery and deeper Solana holder/trader metadata after endpoint/plan validation.

### On-chain verification
- Helius/Solana RPC (candidate provider): mint/freeze/token-program state, transaction history, deployer/funding relationships and transfer evidence.

### Research/backfill
- Dune only where historical research or replay adds value; do not place credit-heavy SQL calls in the hot path unless measured and justified.

No undocumented scraping is part of v1.

## Telegram contract

The future main-panel entry should be a dedicated `Memecoin Radar` action. The module has four user-facing views:

1. `ENTRY SETUP` — qualified tokens with a valid technical entry thesis.
2. `WATCH` — survival gates passed but no entry setup yet.
3. `REJECTED` — recent hard-gate failures with explicit reasons.
4. `DATA / HEALTH` — provider coverage, stale data, API failures and unknown-gate counts.

A Telegram message is an alert/control surface only. It does not possess signing authority.

## Execution maturity

Execution is deliberately separated from research:

```text
SHADOW -> CONFIRM -> AUTO
```

This PR locks memecoin integration to SHADOW.

CONFIRM requires a separately reviewed execution gateway that can prepare a transaction but cannot execute until an explicit user approval event is validated.

AUTO requires a later review of global portfolio limits, memecoin-specific limits, slippage, liquidity, daily-loss limits, open-position caps, idempotency, nonce/blockhash handling, transaction simulation and kill-switch behavior.

## Phantom policy

The user's ordinary Phantom wallet must not be treated as application credentials.

Never store or request in:
- GitHub;
- repository files;
- Telegram;
- application logs;
- JSON state;
- issue/PR comments;

any seed phrase, private key or exported secret.

If automated execution is eventually approved, use a dedicated, limited-balance execution wallet and an explicit signing architecture. Phantom can remain the user's observation/manual wallet. Wallet integration is not implemented by this PR.

## Data funnel and cost control

Expensive enrichment should run only after cheap filters pass. Exact thresholds will be learned from observation/replay data, not invented as permanent constants.

Illustrative funnel only:

```text
new pairs
 -> identity + minimum market viability
 -> contract/security
 -> liquidity/exit
 -> holder/manipulation
 -> deep wallet/deployer intelligence
 -> WATCH
 -> ENTRY_SETUP
```

Do not encode the illustrative counts from design discussions as production thresholds.

## Required research before live qualification

Before a token can be marked QUALIFIED by a live provider-backed engine:

1. validate provider field semantics against documentation;
2. measure latency/staleness and missing-field behavior;
3. record 50–100+ point-in-time token histories;
4. label 30m/1h/4h/24h outcomes, MFE, MAE and survival/rug-like outcomes;
5. measure false-positive and false-negative patterns;
6. only then calibrate hard thresholds and opportunity features.

## Initial user setup

No wallet secret is required for this PR.

Later provider integration may require:
- Birdeye API key, if selected after coverage/cost validation;
- Helius API key for Solana RPC/indexed history;
- optional Dune API key for historical research.

These belong in deployment secrets/environment variables, never committed to GitHub.

DexScreener account credentials are not required for public API-based market reads.

## Acceptance criteria for this PR

- domain contracts are Solana/mint-address based;
- all seven survival gates are mandatory;
- FAIL cannot be averaged away;
- UNKNOWN cannot become WATCH/QUALIFIED/ENTRY_SETUP;
- rejected/incomplete tokens cannot carry an opportunity score;
- future-unavailable evidence is rejected;
- trade authorization is impossible;
- execution policy is forced to SHADOW with kill switch engaged;
- no provider or wallet credential is introduced.
