"""Research, risk and (future) execution chassis.

- ``trading.data`` / ``trading.backtest`` / ``trading.strategies`` —
  Binance USDⓈ-M research data and the walk-forward harness. Heavy
  dependencies (pandas, pyarrow) are imported lazily and live in
  ``requirements-trading.txt``, never in the production image.
- ``trading.research`` / ``trading.risk`` — standard-library-only
  evaluators. The production ``bot.py`` may import them to label radar
  output honestly; they cannot place or authorize orders.
- ``trading.execution`` — intentionally empty.

No module in this package may authorize an order without an explicit,
separately reviewed enablement path (AGENTS.md §4, §10).
"""
