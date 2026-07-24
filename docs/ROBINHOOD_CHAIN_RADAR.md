# Robinhood Chain × MEXC Early Opportunity Radar

The radar is a push-only, discovery-only sidecar for Signal Bot. It does not
read Telegram updates, so it cannot create a second `getUpdates` consumer. It
never changes `TRADE_UNIVERSE`, creates orders, signs transactions, or grants
execution authority.

## Data sources and flow

1. Verify the public Robinhood Chain RPC identity (`chainId=4663`).
2. Read Robinhood Chain `new_pools`, 1-hour/24-hour `trending_pools`, and
   top-volume pools from GeckoTerminal's keyless API.
3. Read active MEXC USDT Spot markets from the public MEXC endpoints:
   - `/api/v3/exchangeInfo`
   - `/api/v3/ticker/24hr`
4. Intersect the Robinhood Chain pool symbols with active MEXC USDT Spot base
   assets. Tokens that are not listed on MEXC are excluded before ranking.
5. Reject or block weak MEXC markets using MEXC 24-hour quote volume and compare
   the Robinhood Chain pool price with the MEXC price to reduce same-symbol,
   different-token false matches.
6. Score Chain liquidity, pool age, volume/liquidity turnover, transaction
   participation, buy/sell balance, momentum, FDV/liquidity, MEXC volume, and
   DEX/MEXC price agreement.
7. Persist the last stage/score under `/data`, then alert only on a new qualified
   token, a stage upgrade, or a material score jump after cooldown.

MEXC is authoritative for exchange availability. The adapter has no Binance or
other-exchange fallback. If MEXC data cannot be read, the radar fails closed and
does not produce a GeckoTerminal-only alert.

## Stages

- `WATCH`: insufficient evidence.
- `EARLY`: promising but below the alert threshold.
- `BUILDING`: qualified early-opportunity alert.
- `HOT`: strongest evidence and the highest FOMO risk.
- `BLOCKED`: Chain liquidity, valuation, MEXC volume, or price-match gate failed.

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

ROBINHOOD_RADAR_REQUIRE_MEXC=1
ROBINHOOD_RADAR_MEXC_BASE_URL=https://api.mexc.com
ROBINHOOD_RADAR_MIN_MEXC_QUOTE_VOLUME_USD=100000
ROBINHOOD_RADAR_MAX_MEXC_PRICE_GAP_PCT=60
```

The existing `TOKEN` and `CHAT_ID` values are reused only for Telegram
`sendMessage`. The radar never calls `getUpdates`.

## Production startup

The existing `python bot.py` start command stays unchanged. When `bot.py`
imports `acce_unified`, the package starts one optional daemon radar thread.
The sidecar fails closed: a startup or provider error is logged and never blocks
the main Signal Bot. Set `ROBINHOOD_RADAR_ENABLED=0` to disable it.

## Safety notes

MEXC matching starts with the exact base symbol and is strengthened by DEX/MEXC
price agreement. It is not a cryptographic contract-address match because the
public MEXC Spot market endpoints do not provide a canonical contract identity
for every market. A high score is therefore not a contract audit. Before
considering a trade, independently check the exact deposit network and contract,
holder concentration, deployer privileges, transfer restrictions, sellability,
taxes, and pool-lock status. The radar's output is evidence for review, not
permission to trade.
