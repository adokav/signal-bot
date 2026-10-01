"""Identity rules for Binance spot *history*, on top of the live rules.

The live identity rules (``acce_unified/cex.py``) are written for today's
MEXC tickers and are part of the Likit-100 evidence fingerprint, so they are
not changed for history-specific cases. Binance history has tickers those
rules cannot know about:

- leveraged tokens without a coin stem (``BULL``/``BEAR`` were 3x BTC);
- stablecoins and fiat/commodity-pegged tokens that the live list misses;
- tokens pegged to another crypto asset (staked or wrapped);
- **migrations**: a project that changed its ticker (MATIC -> POL). The new
  ticker is not a new asset. Treating it as a new listing would mix an
  established coin into a new-listing cohort.

Every migration below was checked against the data.binance.vision listing:
the old USDT pair's last month is the new pair's first month or the month
before (``verify_migrations`` repeats that check on every build). The list
was written before any new-listing result was seen; anything not on it is
treated as a genuinely new asset, which is a documented limitation.

Research only; no order authority (AGENTS.md §4).
"""

from __future__ import annotations

from typing import Iterable, Mapping

from acce_unified.cex import is_leveraged_token, is_stable_or_synthetic


HISTORY_STABLE = frozenset({
    "PAX",    # Paxos Standard (USD), renamed USDP in 2021
    "UST",    # TerraUSD
    "AUD",    # Australian dollar pair
    "BKRW",   # Korean-won stablecoin
    "U",      # USD stablecoin listed 2026-01 (daily closes 0.9996-1.0007)
    "KGST",   # Kyrgyz-som stablecoin (constant ~0.0114 USD)
})
HISTORY_COMMODITY = frozenset({"PAXG", "XAUT"})            # gold-backed tokens
HISTORY_LEVERAGED = frozenset({"BULL", "BEAR"})            # Binance 3x BTC tokens (2020-01..03)
HISTORY_PEGGED = frozenset({"BNSOL", "WBETH", "BETH", "WBTC"})   # staked/wrapped claims on another asset

# new ticker -> old ticker (same project, ticker change or token swap)
KNOWN_MIGRATIONS: Mapping[str, str] = {
    "AAVE": "LEND", "STRAX": "STRAT", "FIRO": "XZC", "PUNDIX": "NPXS", "BTTC": "BTT", "XNO": "NANO",
    "T": "KEEP", "MULTI": "ANY", "REI": "GXS", "EPX": "EPS", "OOKI": "BZRX", "POLYX": "POLY",
    "HIFI": "MFT", "COMBO": "COCOS", "BEAMX": "MC", "VIC": "TOMO", "VANRY": "TVK", "PDA": "PLA",
    "G": "GAL", "RENDER": "RNDR", "POL": "MATIC", "KAIA": "KLAY", "LUMIA": "ORN", "S": "FTM",
    "D": "DAR", "HEI": "LIT", "FORM": "BNX", "A": "EOS", "AWE": "STPT", "NOM": "OMNI",
    "FRAX": "FXS", "MANTRA": "OM",
}


def exclusion_reason(base: str, all_bases: Iterable[str]) -> str | None:
    """Why ``base`` is not an ordinary crypto asset for a historical study; None if it is."""

    base = base.upper()
    bases = {b.upper() for b in all_bases}
    if is_stable_or_synthetic(base) or base in HISTORY_STABLE:
        return "STABLE"
    if base in HISTORY_COMMODITY:
        return "COMMODITY"
    if is_leveraged_token(base, bases) or base in HISTORY_LEVERAGED:
        return "LEVERAGED"
    if base in HISTORY_PEGGED:
        return "PEGGED"
    return None


def migration_source(base: str) -> str | None:
    return KNOWN_MIGRATIONS.get(base.upper())


def _month_index(ym: str) -> int:
    year, month = (int(x) for x in ym.split("-"))
    return year * 12 + month - 1


def verify_migrations(months_by_pair: Mapping[str, list[str]], *, quote: str = "USDT") -> dict[str, str]:
    """Check each listed migration against the data: {new: 'OK' | reason}.

    OK means the old pair's last month is the new pair's first month or the
    month before. A failed check does not re-admit the ticker as a new asset;
    it is reported so the list can be corrected in a new trial.
    """

    out = {}
    for new, old in KNOWN_MIGRATIONS.items():
        new_months, old_months = months_by_pair.get(new + quote) or [], months_by_pair.get(old + quote) or []
        if not new_months or not old_months:
            out[new] = "MISSING_PAIR"
            continue
        gap = _month_index(new_months[0]) - _month_index(old_months[-1])
        out[new] = "OK" if gap in (0, 1) else f"GAP_{gap}_MONTHS"
    return out
