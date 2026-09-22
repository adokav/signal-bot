"""Freqtrade-based execution and backtest chassis (skeleton).

This package is intentionally decoupled from the production `bot.py` service.
It will host Binance USDⓈ-M perpetual data adapters, Freqtrade strategy
wrappers, purged walk-forward backtest harnesses and, later, the isolated
execution layer.

No module in this package may authorize an order without an explicit,
separately reviewed enablement path (AGENTS.md §4, §10).
"""
