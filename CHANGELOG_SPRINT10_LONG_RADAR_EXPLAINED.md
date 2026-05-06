# Sprint 10 — Long Radar Explanation Upgrade

## Added
- `format_long_radar_explanation(results)`
- Human-readable long setup state names.
- Radar threshold and trade threshold explanation.
- Positive factors / missing factors blocks.
- Long Radar action line.

## Changed
- Bot decision reason for LONG_SETUP_FORMING is now clearer.
- Heartbeat now shows a detailed Long Radar block instead of a one-line status.
- `PASS` is no longer used ambiguously; the report distinguishes radar readiness from trade readiness.
