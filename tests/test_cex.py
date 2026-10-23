from __future__ import annotations

from acce_unified.cex import is_leveraged_token, is_stable_or_synthetic


def test_stable_and_synthetic_dollar_symbols_are_recognised():
    assert is_stable_or_synthetic("USDT") and is_stable_or_synthetic("usde") and is_stable_or_synthetic("EURC")
    assert is_stable_or_synthetic("USD1") and is_stable_or_synthetic("XUSD")
    assert not is_stable_or_synthetic("BTC") and not is_stable_or_synthetic("AAVE")


def test_leveraged_tokens_need_their_base_asset_in_the_universe():
    bases = {"BTC", "ETH", "BTCUP", "ETH3L", "UP"}
    assert is_leveraged_token("BTCUP", bases) and is_leveraged_token("ETH3L", bases)
    assert not is_leveraged_token("UP", bases)          # a suffix alone is a symbol, not a leveraged token
    assert not is_leveraged_token("SOLUP", bases)       # SOL is not in the universe: unknown, not leveraged
