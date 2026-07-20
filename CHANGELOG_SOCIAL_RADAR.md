# Social Intelligence Radar

## What changed

- Added independent attention, sentiment, manipulation and crowding signals.
- Added optional X recent-search collection and keyless GDELT coin-news scans.
- Added cached, bounded social scans for MEXC new-listing candidates.
- Added `📣 Sosyal Radar`, per-coin explanations and source buttons to Telegram.
- Added watch alerts when an already-followed coin enters `EMERGING` or
  `CONFIRMED` social stages.

## Safety invariant

Social attention never changes market score, filter eligibility, trade-universe
membership or execution authority. Every social payload carries
`can_authorize_trade: false`; suspicious and crowded attention is shown as risk,
not as a buy signal.
