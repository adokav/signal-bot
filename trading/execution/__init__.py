"""Execution layer (empty until Faz C).

AGENTS.md §10: Order submission code, if ever added here, requires:
- Explicit enablement flag, separately reviewed from SHADOW.
- Hard caps on notional, leverage, slippage, per-trade risk, aggregate exposure.
- Duplicate order protection under retries, restarts and races.
- Partial fill handling, provider rejection paths, ambiguous responses.
- Provider outage MUST NOT be interpreted as a successful order.

This module is intentionally empty during Faz 0/A/B. It exists as a namespace
marker so any future execution code lands here and cannot silently leak into
research or radar modules.
"""
