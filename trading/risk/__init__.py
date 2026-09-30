"""Risk and decision evaluators (spec §3, §23-§26, §30, §38-§40).

Pure functions and small immutable records. They answer "would this pass
the risk rules?" — they never place, size on an exchange, or authorize an
order. Every decision object carries ``can_authorize_trade = False``
(AGENTS.md §4, §10). Anything that cannot be evaluated is ``UNKNOWN`` and
``UNKNOWN`` never counts as a pass.

These modules depend only on the standard library, so the production
radar service may import them for honest labelling without pulling in the
research stack.
"""
