"""Research evaluation layer: metrics, calibration, EV, robustness, decay.

Every function in this package is deterministic, side-effect free (except
the explicit append-only registries) and non-executing. Nothing here can
size, place, or authorize an order (AGENTS.md §4, §10). Outputs are
evidence for a human reviewer; they are never trade permission.
"""
