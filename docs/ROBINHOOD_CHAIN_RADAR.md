# Robinhood Chain Early Opportunity Radar

The radar is a push-only, discovery-only sidecar for Signal Bot. It does not
read Telegram updates, so it cannot create a second `getUpdates` consumer. It
never changes `TRADE_UNIVERSE`, creates orders, signs transactions, or grants
execution authority.

## Data flow

1. Verify the public Robinhood Chain RPC identity (`chainId=4663`).
2. Read `new_pools`, 1-hour/24-hour `trending_pools`, and top-volume pools from
   GeckoTerminal's keyless API for network `robinhood`.
3. Normalize the non-quote token, merge duplicate pools, and keep the strongest
   pool for each contract.
4. Score liquidity, pool age, volume/liquidity turnover, transaction count,
   buy/sell balance, momentum, FDV/liquidity, and new/trending feed presence.
5. Persist the last stage/score under `/data`, then alert only on a new qualified
   token, a stage upgrade, or a material score jump after cooldown.

## Stages

- `WATCH`: insufficient evidence.
- `EARLY`: promising but below alert threshold.
- `BUILDING`: qualified early-opportunity alert.
- `HOT`: strongest evidence, also the highest FOMO risk.
- `BLOCKED`: hard liquidity or valuation/liquidity gate failed.

## Environment

```text
ROBINHOOD_RADAR_ENABLED=1
ROBINHOOD_RADAR_NETWORK_ID=robinhood
ROBINHOOD_RADAR_INTERVAL_SECONDS=180
ROBINHOOD_RADAR_PAGES_PER_FEED=2
ROBINHOOD_RADAR_TOP_N=8
ROBINHOOD_RADAR_MIN_LIQUIDITY_USD=25000
ROBINHOOD_RADAR_MIN_ALERT_SCORE=64
ROBINHOOD_RADAR_MAX_FDV_LIQUIDITY_RATIO=200
ROBINHOOD_RADAR_ALERT_COOLDOWN_SECONDS=21600
ROBINHOOD_RADAR_STATE_FILE=/data/robinhood_radar_state.json
ROBINHOOD_RADAR_SEND_STARTUP_REPORT=1
```

The existing `TOKEN` and `CHAT_ID` values are reused only for Telegram
`sendMessage`. The radar never calls `getUpdates`.

## Production startup

The existing `python bot.py` start command stays unchanged. When `bot.py`
imports `acce_unified`, the package starts one optional daemon radar thread.
The sidecar fails closed: a startup or provider error is logged and never blocks
the main Signal Bot. Set `ROBINHOOD_RADAR_ENABLED=0` to disable it.

## Safety notes

A high score is not a contract audit. Before considering a trade, independently
check contract verification, holder concentration, deployer privileges,
transfer restrictions, sellability, taxes, and pool-lock status. The radar's
output is evidence for review, not permission to trade.
