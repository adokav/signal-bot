from __future__ import annotations

import hashlib
import json

import pytest

from acce_unified import register as rg


def _raw() -> dict:
    return json.loads(rg.DEFAULT_PATH.read_text(encoding="utf-8"))


def _write(tmp_path, data: dict):
    path = tmp_path / "register.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_the_committed_register_is_the_reports_list():
    reg = rg.load()
    assert reg.version == "v3.2" and reg.report_date.isoformat() == "2026-10-23"
    assert reg.sha256 == hashlib.sha256(rg.DEFAULT_PATH.read_bytes()).hexdigest()
    assert [c.symbol for c in reg.core(rg.ALTCOIN)] == ["UNI", "PENDLE", "AAVE", "RENDER"]
    assert [c.symbol for c in reg.basket(rg.CORE_CONDITIONAL)] == ["BNB", "INJ"]
    assert [c.symbol for c in reg.core(rg.MEME)] == ["PEPE", "BONK", "DOGE", "WIF", "MOODENG"]
    assert [c.symbol for c in reg.basket(rg.WATCH)] == ["SKY"]
    assert {c.symbol for c in reg.basket(rg.GROWTH_POOL)} == {"HYPE", "LINK", "AVAX"}
    assert [c.symbol for c in reg.basket(rg.SPECULATIVE_POOL)] == ["MUBARAK"]
    assert len(reg.coins) == 22 and len(reg.by_kind(rg.ALTCOIN)) == 13 and len(reg.by_kind(rg.MEME)) == 9
    # slice signals come only from the core basket; a condition keeps BNB and INJ out until it clears
    assert {c.symbol for c in reg.signal_eligible()} == {"UNI", "PENDLE", "AAVE", "RENDER", "PEPE", "BONK", "DOGE", "WIF", "MOODENG"}
    assert not reg.find("bnb").signal_eligible and reg.find("BNB").condition
    assert reg.find("nope") is None


def test_scores_and_combined_scores_reproduce_the_report():
    reg = rg.load()
    for coin in reg.coins:
        weights = reg.rules["weights"][coin.kind]
        assert set(coin.score) == set(weights) and all(0 <= coin.score[g] <= weights[g] for g in weights)
        assert abs(sum(coin.score.values()) - coin.total) <= rg.SCORE_ROUNDING
        if coin.s is not None:
            assert abs(0.6 * coin.total / 100 + 0.4 * coin.p_2x - coin.s) <= rg.S_TOLERANCE
    uni = reg.find("UNI")
    assert uni.total == 76 and uni.p_2x == 0.40 and uni.s == 0.617 and uni.rank == 1
    assert uni.risk["sigma"] == 0.9 and uni.position_cap_usd == 25e6
    assert uni.scenario_medians == {"bear": 0.54, "base": 1.67, "bull": 3.92}
    pepe = reg.find("PEPE")
    assert pepe.e_m == 2.09 and pepe.scenario_medians is None and pepe.position_cap_usd == pytest.approx(10.55e6)
    assert reg.find("SHIB").position_cap_usd is None                    # no spot volume: unknown, not zero


def test_perp_symbols_follow_the_contract_multiplier():
    reg = rg.load()
    assert reg.find("PEPE").perp == "1000PEPEUSDT" and reg.find("PEPE").perp_multiplier == 1000
    assert reg.find("DOGE").perp == "DOGEUSDT" and reg.find("DOGE").perp_multiplier == 1
    assert len({c.perp for c in reg.coins}) == len(reg.coins)
    assert reg.find("PEPE").mexc_symbol == "PEPE_USDT" and reg.find("PEPE").mexc_contract_size == 1e7
    assert reg.find("BONK").mexc_symbol == "1000BONK_USDT" and reg.find("BONK").mexc_contract_size == 1e4
    assert reg.find("BONK").mexc_price_multiplier == 1000 and reg.find("PEPE").mexc_price_multiplier == 1
    assert len({c.mexc_symbol for c in reg.coins}) == len(reg.coins)


def test_triggers_resolve_and_the_leverage_trigger_is_mechanical():
    reg = rg.load()
    ids = {t.id for t in reg.triggers}
    for coin in reg.coins:
        assert set(coin.triggers) <= ids
    lev = reg.trigger("LEVERAGE_BUILDUP")
    assert lev.mechanical and lev.thresholds == {"oi_mcap_gt": 0.20, "perp_spot_gt": 6.0}
    assert {t.id for t in reg.triggers_for("AAVE")} >= {"BTCD_55_WEEKLY", "AAVE_BUYBACK_RESUMES", "AAVE_INJ_ETF_DECISION", "LEVERAGE_BUILDUP"}
    assert reg.rules["position"]["risk_reduce"] == {"oi_mcap_gt": 0.20, "perp_spot_gt": 6.0, "effect": "boğa hedefini beklemeden risk azaltma"}


@pytest.mark.parametrize("mutate, where", [
    (lambda d: d.update(schema="v31-register/v1"), "schema"),
    (lambda d: d["altcoins"][0].update(basket="çekirdek"), "basket"),
    (lambda d: d["altcoins"][0].update(symbol="PENDLE"), "tekrarlanan"),
    (lambda d: d["altcoins"][0].update(p_2x=1.4), "p_2x"),
    (lambda d: d["altcoins"][0].update(s=0.9), ".s"),
    (lambda d: d["altcoins"][0]["score"].update(A=25), "score.A"),
    (lambda d: d["altcoins"][0].update(total=90), "total"),
    (lambda d: d["altcoins"][0].update(condition="veri eksik"), "condition"),       # core may not carry a condition
    (lambda d: d["altcoins"][3].update(condition=None), "condition"),               # conditional must
    (lambda d: d["altcoins"][0].update(perp="UNI-PERP"), "perp"),
    (lambda d: d["memes"][0].update(perp_multiplier=1), "perp_multiplier"),
    (lambda d: d["memes"][0].pop("mexc"), "mexc"),
    (lambda d: d["memes"][0]["mexc"].update(symbol="PEPEUSDT"), "mexc.symbol"),
    (lambda d: d["memes"][0]["mexc"].update(contract_size=0), "contract_size"),
    (lambda d: d["altcoins"][0].update(triggers=["NOPE"]), "triggers"),
    (lambda d: d["altcoins"][0]["snapshot"].update(price_usd=float("nan")), "snapshot"),
    (lambda d: d["altcoins"][0]["risk"].update(p_touch_2x=2), "p_touch_2x"),
    (lambda d: d["altcoins"][0]["scenario_medians"].update(bear=5.0), "scenario_medians"),
    (lambda d: d["memes"][0].pop("e_m"), "e_m"),
    (lambda d: d["rules"]["weights"]["altcoin"].update(A=25), "ağırlıklar"),
    (lambda d: d["triggers"][0].update(affects=["XYZ"]), "affects"),
    (lambda d: d["report"].update(data_as_of="2027-01-01"), "data_as_of"),
    (lambda d: d.update(memes=[]), "memes"),
])
def test_a_malformed_register_loads_nothing(tmp_path, mutate, where):
    data = _raw()
    mutate(data)
    with pytest.raises(rg.RegisterError) as info:
        rg.load(_write(tmp_path, data))
    assert where in str(info.value)


def test_missing_or_unparseable_files_fail_closed(tmp_path):
    with pytest.raises(rg.RegisterError):
        rg.load(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(rg.RegisterError):
        rg.load(bad)


def test_the_bot_cannot_promote_a_coin_by_editing_in_memory():
    reg = rg.load()
    coin = reg.find("BNB")
    with pytest.raises(Exception):
        coin.basket = rg.CORE                                             # frozen dataclass
    assert reg.find("BNB").basket == rg.CORE_CONDITIONAL
    assert reg.summary()["core"] == ["UNI", "PENDLE", "AAVE", "RENDER", "PEPE", "BONK", "DOGE", "WIF", "MOODENG"]
