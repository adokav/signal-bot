# Fundamental Metrics Radar

## What changed

- Added a batched CoinGecko market-fundamental provider for MEXC candidates.
- Added separate market-cap, FDV, circulation, turnover, supply and ATH metrics.
- Added explicit identity confidence and refused ambiguous symbol matches.
- Added provider caching and a shared cooldown for HTTP 429 responses.
- Added `🧬 Temel Radar`, compact list scores and detailed per-coin cards.

## Safety invariant

Fundamental metrics do not change the opportunity score, listing filter,
trade-universe membership or execution authority. Missing data remains pending;
it is never converted into a zero-quality verdict. Every payload carries
`can_authorize_trade: false`.
