# Sprint 9 — Long Signal Sensitivity & Quality Upgrade

## Added
- Long Setup State Machine
- Long Confluence Score
- Late Entry / Wait Pullback filter
- MEME long quality gate
- Long block reason logger: `long_block_reasons.jsonl`
- Heartbeat Long Radar field

## Behavior
- Bot can detect long setup earlier as `LONG_SETUP_FORMING`.
- Trade is still blocked unless confluence, entry, execution and ACCE gate pass.
- Stretched/FOMO entries become `WAIT_PULLBACK` / `WAIT_RETEST`.
- MEME trades need stronger confluence and volume.
