"""Hypothesis, fragility answers and blow-up scenarios for the short-term reversal test.

Written before the holdout replay (``trading.backtest.reversal_replay``) was run.
"""

from __future__ import annotations

from trading.research.report import BlowUpScenario, StrategyHypothesis


REVERSAL_HYPOTHESIS = StrategyHypothesis(
    name="Kısa vadeli kesitsel geri dönüş: likit 100 içinde son 24 saatin en çok düşenleri (spot long)",
    market_logic=(
        "Kısa vadede likidite talebi (zorunlu satış, kaldıraç tasfiyesi, panik) fiyatı temel değerinden "
        "uzaklaştırır; likidite sağlayanlar bu sapmanın geri dönmesinden prim alır (Jegadeesh 1990, "
        "Lehmann 1990; kriptoda karışık bulgular). Bu depodaki Likit-100 replay'inde (2020-10 → 2026-08) "
        "son saatlerin en güçlü yükselenleri sonraki 12-72 saatte sepetin gerisinde kaldı. Hipotez, aynı "
        "olgunun tersini — en çok düşenlerin sepeti geçmesini — daha önce hiç bakılmamış 2017-2020 "
        "verisinde sınar. Yön, sepete göre fazla getiri ve mutlak getiri birlikte ölçülür."
    ),
    feature_rationale={
        "son 24 saatlik getiri sırası": (
            "Tek sinyal; evren içinde göreli sıralama, mutlak eşik yok. Kaynak bulgunun 12-72 saatlik "
            "ufkuna en yakın, tek günlük doğal pencere."
        ),
        "likit 100 evren": "Canlı Likit-100 kuralları; işlem maliyetini ve manipülasyon riskini sınırlar.",
    },
    parameter_rationale={
        "alt %10 (en az 3 coin)": "Kesitsel çalışmalardaki standart decile; optimize edilmedi.",
        "tutma 1 ve 3 gün": "Kaynak bulgunun 12-72 saatlik penceresini kapsayan iki ufuk; önceden seçildi.",
        "maliyet 7.5 + 5 bp/taraf": "2017-2020 Binance spot taker ücreti (BNB indirimli) ve küçük coinlerde slippage.",
        "en az 30 çiftlik evren": "2017'de likit USDT çifti azdı; decile'ın anlamlı olması için alt sınır.",
    },
)

REVERSAL_FRAGILITY_ANSWERS = {
    "regime_that_kills_edge": (
        "Kalıcı trendler ve haber şokları: düşen coin gerçek kötü haberle düşüyorsa (hack, delisting, "
        "kilit açılımı) geri dönmez, düşmeye devam eder."
    ),
    "most_important_feature": "Tek özellik: son 24 saatlik göreli getiri sırası.",
    "performance_without_top_feature": (
        "Özellik çıkarılırsa strateji rastgele 10 coin tutmaya döner; beklenen fazla getirisi maliyet kadar negatiftir."
    ),
    "possible_data_leakage": (
        "Sıralama yalnızca kararın anından önce kapanmış mumlarla, giriş bir sonraki mumun açılışında. "
        "Evren verisi 2020-09-30 sonrasını hiç okumaz; test bunu reddeder."
    ),
    "is_it_just_beta": (
        "Düşen coinler yüksek betalı olabilir; bu yüzden ana ölçü eşit ağırlıklı evrene göre fazla getiridir."
    ),
    "microstructure_risk": (
        "En çok düşen coinlerde spread ve kayma büyüktür; 5 bp slippage düşük kalabilir. Stres senaryosu x2 uygular."
    ),
    "slippage_risk": (
        "Günlük tam gidiş-dönüş (%0.25) yıllık %90'a yakın maliyettir; fazla getiri bunu aşmak zorunda."
    ),
    "hidden_correlation": "Aynı gün düşenler çoğunlukla aynı sektör/haberden düşer; 10 coin tek bahis olabilir.",
    "shared_risk_factor": "Altcoin betası ve likidite krizleri; kaskad günlerinde tüm decile birlikte düşer.",
    "liquidity_shock": (
        "Borsa çaplı tasfiyede en çok düşenler likiditesi en zayıflardır; çıkış fiyatı replay'den kötü olabilir."
    ),
    "funding_anomaly": "Spot strateji; funding etkisi yok.",
}

REVERSAL_BLOW_UP_SCENARIOS = (
    BlowUpScenario(
        title="Düşen bıçağı tutmak",
        cause="Coin gerçek bir olayla (hack, delisting duyurusu, çöküş) düşüyor; geri dönüş beklenirken sıfıra gidiyor.",
        early_warning="Hacim patlaması + duyuru; aynı coin üst üste birkaç gün en çok düşenler listesinde.",
        loss_mechanism="Tek coin kohortun %10'u; birkaç böyle olay haftalarca kazancı siler.",
        protection="Duyuru/delisting vetosu, coin başına tavan, üst üste kaybeden coini dışlama (yeni ön-kayıtla).",
    ),
    BlowUpScenario(
        title="Piyasa çapında çöküş",
        cause="Tüm piyasa düşerken en çok düşenler daha da düşer (2018, Mart 2020).",
        early_warning="BTC'de sert düşüş, pozitif genişlik %20 altında.",
        loss_mechanism="Long-only kohort piyasa betasıyla birlikte değer kaybeder.",
        protection="Rejim filtresi, toplam altcoin maruziyet tavanı, günlük zarar limiti.",
    ),
    BlowUpScenario(
        title="Maliyetlerin primi yemesi",
        cause="Günlük yeniden kurulan kohort her gün tam gidiş-dönüş öder.",
        early_warning="Brüt fazla getirinin maliyetin altında kalması.",
        loss_mechanism="Pozitif brüt etki net negatife döner.",
        protection="Daha uzun tutma veya maker emirler — ancak yeni ön-kayıtlı denemeyle.",
    ),
)
