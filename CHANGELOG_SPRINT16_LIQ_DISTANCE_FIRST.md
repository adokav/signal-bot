# Sprint 16 — Liquidation Distance First Leverage Policy

## Added
- Target liquidation-to-stop ratio by coin group
- Required collateral ratio calculation
- Max notional constrained by liquidation geometry
- Max effective leverage by group
- Liquidation distance first sizing plan
- Hard block if liquidation geometry is unsafe

## Principle
Exchange leverage is not the risk driver. Real risk is:
- stop distance
- notional
- stable collateral allocation
- liquidation distance relative to stop
