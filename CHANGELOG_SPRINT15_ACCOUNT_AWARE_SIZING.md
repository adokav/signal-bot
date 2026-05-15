# Sprint 15 — Account-Aware Position Sizing

## Added
- MEXC read-only spot account sync
- USDT/USDC collateral balance reader
- Account-aware position sizing
- Effective portfolio leverage
- Recommended exchange leverage
- Approx liquidation price model
- Liq/Stop safety ratio
- Telegram account-aware risk line

## Safety
- No live order sending.
- Uses SPOT_ACCOUNT_READ only.
- Falls back to ACCOUNT_SIZE_USD if account sync fails or keys are absent.

## Principle
Exchange leverage is not the real risk. Real risk is:
- account equity
- available stable collateral
- notional size
- stop distance
- liquidation buffer
