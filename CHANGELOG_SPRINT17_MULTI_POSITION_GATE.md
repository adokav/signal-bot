# Sprint 17 — Multi-Position Expansion Gate

## Changed
- Coin universe updated:
  - BTCUSDT
  - ETHUSDT
  - SOLUSDT
  - LINKUSDT
  - ONDOUSDT
  - RENDERUSDT
  - PYTHUSDT
  - BONKUSDT
  - POPCATUSDT
- Removed:
  - AVAXUSDT
  - LDOUSDT
  - PEPEUSDT

## Added
- Multi-Position Expansion Gate
- Safe-stop path for second/third positions
- Existing stop safety checks
- Existing liquidation buffer checks
- Post-plan portfolio heat check
- Same group exposure limit
- Duplicate symbol block
- Telegram Multi-Position Gate section

## Principle
Second/third positions are allowed only when safe stop, liquidation buffer and portfolio heat remain acceptable.
