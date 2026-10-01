"""Hypothesis, fragility answers and blow-up scenarios for the spot/perp funding carry.

Written before the replay (``trading.backtest.carry_replay``) was run and
regenerated into every replay report.
"""

from __future__ import annotations

from trading.research.report import BlowUpScenario, StrategyHypothesis


CARRY_HYPOTHESIS = StrategyHypothesis(
    name="Funding carry: spot long + USDⓈ-M perp short (en likit 20 perp, 8 saatlik)",
    market_logic=(
        "Perpetual kontratlarda kaldıraçlı long talebi kısa taraftan fazladır; funding "
        "mekanizması bu dengesizliği longların shortlara ödeme yapmasıyla kapatır. Delta-nötr "
        "bir pozisyon (spot al, perp sat) fiyat yönünden bağımsız olarak bu ödemeyi toplar. "
        "Arbitraj sermayesi sınırlı olduğu ve pozisyon borsa/teminat riski taşıdığı için prim "
        "tamamen kapanmaz (Schmeling-Schrimpf-Todorov 2023, 'Crypto carry'; He-Manela vd. 2022). "
        "Literatür primin büyük ama çökme riskli olduğunu, coşku dönemlerinde yoğunlaştığını "
        "raporlar; dört bacaklı işlem maliyetinden sonra kalıp kalmadığı bu replay'in sorusudur."
    ),
    feature_rationale={
        "30 günlük perp hacmi (evren)": (
            "Primi toplamak için derin piyasa gerekir; en likit 20 perp hem maliyeti hem "
            "tasfiye/boşluk riskini sınırlar. Sıralama her 8 saatte geçmiş veriyle yapılır."
        ),
        "7 günlük funding toplamı (SIGNED varyant)": (
            "Funding kalıcıdır (otokorelasyon yüksektir); son haftası negatif olan çiftte "
            "pozisyon funding ödemeye devam edeceği için taşınmaz. Tek pencere, optimize edilmedi."
        ),
    },
    parameter_rationale={
        "evren 20, 30 gün hacim": "Literatürdeki büyük-likit evren uygulamalarıyla uyumlu; sonuca göre seçilmedi.",
        "7 günlük funding penceresi": "Haftalık funding döngüsünü kapsayan en kısa doğal pencere; tek nokta, pertürbasyon raporlanmaz.",
        "maliyet 7.5 + 5 bp + 2 bp/bacak": (
            "Binance spot (BNB indirimli) ve perp taker komisyonları; likit çiftlerde dar slippage. "
            "Stres senaryosu komisyon x1.5, slippage x2."
        ),
        "8 saatlik karar aralığı": "Binance funding ödeme aralığıyla aynı; daha sık işlem yalnızca maliyet ekler.",
    },
)

CARRY_FRAGILITY_ANSWERS = {
    "regime_that_kills_edge": (
        "Ayı piyasası ve kaldıraç tasfiyesi sonrası dönemler: funding uzun süre sıfır/negatif "
        "kalır; STATIC varyant bu dönemde funding öder, SIGNED nakitte bekler."
    ),
    "most_important_feature": (
        "Funding'in kendisi — getirinin tamamı funding'den gelmeli; baz (spot−perp) getirisi "
        "uzun vadede sıfıra yakın olmalıdır. Ayrıştırma her raporda gösterilir."
    ),
    "performance_without_top_feature": (
        "STATIC varyant işaret filtresi olmadan aynı evreni taşır; iki varyantın farkı "
        "filtrenin katkısını doğrudan ölçer."
    ),
    "possible_data_leakage": (
        "Karar anında yalnızca o ana kadar ödenmiş funding ve kapanmış mumlar kullanılır; giriş "
        "sonraki mumun açılışındadır, ilk funding geliri bir sonraki ödemedir. Gelecek-veri "
        "zehirleme testi bunu doğrular."
    ),
    "is_it_just_beta": (
        "Hayır olmalı: pozisyon delta-nötrdür. Bazın kendisi korele risk taşır (çöküşlerde perp "
        "spotun altına iner); günlük getirinin BTC ile korelasyonu ayrıca incelenmeli."
    ),
    "microstructure_risk": (
        "Spot ve perp aynı anda dolmazsa kısa süreli yönlü risk oluşur; replay iki bacağın aynı "
        "mumun açılışında dolduğunu varsayar."
    ),
    "slippage_risk": (
        "Dört bacak (iki giriş, iki çıkış) maliyeti funding gelirinin önemli kısmını yer; "
        "STATIC varyantta evren değişimi sık işlem doğurur. Stres senaryosu bunu ölçer."
    ),
    "hidden_correlation": (
        "Funding tüm altcoinlerde aynı anda negatife döner (piyasa çapında tasfiye); 20 pozisyon "
        "fiilen tek bir 'kaldıraç talebi' bahsidir."
    ),
    "shared_risk_factor": (
        "Evet — kripto kaldıraç döngüsü ve borsa riski. Tek borsada (Binance/MEXC) tutulan tüm "
        "bacaklar aynı karşı taraf riskini taşır."
    ),
    "liquidity_shock": (
        "Ani yükselişte kısa perp bacağı teminat ister; spot kârı başka cüzdanda olduğundan "
        "zamanında aktarılmazsa tasfiye olur. Replay tasfiyeyi modellemez, stres olaylarını sayar."
    ),
    "funding_anomaly": (
        "Doğrudan risk: funding'in uzun süre negatif kalması pozisyonu ödeme yapan tarafa çevirir; "
        "negatif funding payı raporlanır."
    ),
}

CARRY_BLOW_UP_SCENARIOS = (
    BlowUpScenario(
        title="Kısa perp bacağının short squeeze'de tasfiyesi",
        cause="Coin saatler içinde %50-100 yükselir; perp teminatı spot kârı aktarılmadan tükenir.",
        early_warning="Perp fiyatı giriş fiyatının 1.5 katına yaklaşıyor, funding aşırı pozitif, OI hızla artıyor.",
        loss_mechanism="Short tasfiye edilir, spot bacak korumasız kalır; ardından düşüşte tüm kazanç geri gider.",
        protection="Düşük kaldıraç (1x), portföy teminatı, otomatik teminat aktarımı, tek coin nominal tavanı.",
    ),
    BlowUpScenario(
        title="Borsa iflası veya çekim dondurma",
        cause="Her iki bacak aynı borsada; borsa çökerse (FTX 2022) varlıkların tamamı risk altındadır.",
        early_warning="Borsa token'ında sert düşüş, çekim gecikmeleri, rezerv kanıtı tartışmaları.",
        loss_mechanism="Delta-nötr pozisyonun hiçbir önemi kalmaz; sermayenin tamamı kaybedilebilir.",
        protection="Borsa başına sermaye tavanı, düzenli çekim, karşı taraf riskini ayrı limitle yönetmek.",
    ),
    BlowUpScenario(
        title="Baz çöküşü ve delisting",
        cause="Coin borsadan kaldırılır ya da çöker (LUNA 2022); perp ve spot farklı anlarda durur.",
        early_warning="Spot-perp farkının olağan dışı açılması, delisting duyurusu, hacmin kuruması.",
        loss_mechanism="Bir bacak kapanırken diğeri açık kalır; baz kaybı birkaç aylık funding gelirini siler.",
        protection="Delisting/duyuru vetosu, baz farkı eşiğinde çıkış, en likit evrenle sınırlama.",
    ),
    BlowUpScenario(
        title="Uzun negatif funding dönemi",
        cause="Ayı piyasasında shortlar kalabalıklaşır; funding haftalarca negatif kalır.",
        early_warning="7 günlük funding toplamının çoğu çiftte negatife dönmesi.",
        loss_mechanism="STATIC carry funding öder; maliyetlerle birlikte yavaş ama sürekli kayıp.",
        protection="SIGNED filtresi, toplam funding negatifken yeni pozisyon açmama.",
    ),
    BlowUpScenario(
        title="Maliyetlerin primi yemesi",
        cause="Evren değişimi ve sinyal dönüşleri sık giriş-çıkış doğurur; her biri dört bacak maliyetidir.",
        early_warning="Yıllık maliyet payının funding gelirinin yarısını aşması.",
        loss_mechanism="Brüt pozitif carry net negatife döner.",
        protection="Daha seyrek yeniden dengeleme, histerezis — ancak yeni ön-kayıtlı denemeyle.",
    ),
)
