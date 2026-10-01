"""Hypothesis, fragility answers and blow-up scenarios for the Liquid-100 long radar.

Written before the historical replay (``trading.backtest.liquid_replay``) was
run and regenerated into every replay report.
"""

from __future__ import annotations

from trading.research.report import BlowUpScenario, StrategyHypothesis


LIQUID_HYPOTHESIS = StrategyHypothesis(
    name="MEXC Likit-100 Long İlk 3 (teknik katman, 15 dakikalık mumlar)",
    market_logic=(
        "İddia: en likit 100 çift içinde kısa vadeli trendi yukarı (fiyat > EMA20 > EMA50, "
        "eğim pozitif), 1-4 saatlik momentumu ılımlı pozitif, RSI 45-72 aralığında, hacmi "
        "artan ve aşırı uzamamış coinler sonraki saatlerde evrenin geri kalanından daha "
        "iyi getiri verir (kısa vadeli momentum devamı + katılım). Kripto kesitinde kısa "
        "vadeli momentum literatürde karışık sonuç verir; 1 günden kısa ufuklarda "
        "ters dönüş de raporlanmıştır. Bu yüzden önsel beklenti zayıftır. Replay, "
        "anlatıyı değil canlı fonksiyonların seçtiği coinlerin sonucunu ölçer."
    ),
    feature_rationale={
        "trend (EMA20/EMA50, eğim)": (
            "Trend devamı varsayımı; engel olarak kullanılır (trend teyitsizse aday olmaz) "
            "ve puanın ~%30'unu oluşturur."
        ),
        "momentum (1s, 4s, RSI)": (
            "Ilımlı pozitif momentum ödüllendirilir, aşırısı çan eğrisiyle cezalanır; "
            "hedef değerler (1s %1.2, 4s %4, RSI 58) elle seçildi."
        ),
        "katılım (hacim oranı, aralık konumu)": (
            "Son 1 saatlik hacmin önceki 6 saatlik medyana oranı; yükselişin hacimle "
            "desteklendiği varsayımı."
        ),
        "likidite sırası ve spread": (
            "Daha likit coinlere ek puan; replay'de spread sabit varsayıldığı için bu "
            "bileşen yalnızca sıraya göre değişir."
        ),
        "risk/FOMO (24s değişim, ATR, EMA uzaklığı, zirveden düşüş)": (
            "Aşırı uzamış veya aşırı oynak coinleri eler/cezalandırır; eşikler mutlak "
            "değerlerdir (ATR > %6 engel), göreli normalizasyon yoktur."
        ),
    },
    parameter_rationale={
        "ağırlıklar ve çan hedefleri": (
            "Tamamı elle seçildi, hiçbir veri üzerinde fit edilmedi ve test edilmedi; "
            "replay bunları değiştirmeden ölçer, pertürbasyon yapmaz."
        ),
        "evren 100, min hacim 1M USD": "Canlı render.yaml ayarı; replay aynı değerleri kullanır.",
        "24s filtre −%8 / +%25": "Canlı ayar; düşen bıçak ve aşırı FOMO'yu dışlama amaçlı, gerekçesi belgelenmedi.",
        "ufuklar 4s ve 24s": (
            "Radar stop/hedef üretmediği için sabit süreli çıkış ölçülür; 4s kısa "
            "momentum, 24s günlük taşıma için önceden seçildi."
        ),
    },
)

LIQUID_FRAGILITY_ANSWERS = {
    "regime_that_kills_edge": (
        "Ani risk-off dönüşleri ve altcoin sezonunun bittiği haftalar: trendi yukarı "
        "görünen coinler piyasayla birlikte düşer; rejim kırılımı yıl ve rejim "
        "tablolarında ayrıca gösterilir."
    ),
    "most_important_feature": (
        "Trend engeli (fiyat > EMA20 > EMA50) — adayların tamamını belirler. Hangi "
        "bileşenin katkı verdiği bu replay'de ayrıştırılmadı (ablation yok)."
    ),
    "performance_without_top_feature": (
        "Ölçülmedi. ALL_READY grubu teknik filtrenin bütününü, TOP3 ise sıralamayı "
        "ölçer; bileşen bazlı ablation sonraki deneme olur."
    ),
    "possible_data_leakage": (
        "Evren ve metrikler yalnızca kapanmış mumlarla hesaplanır; aday ön-filtresi "
        "tüm pencerenin günlük hacmini kullanır ama yalnızca hangi verinin indirileceğini "
        "belirler, karar anında gelecek görülmez. Gelecek-mum zehirleme testi bunu doğrular."
    ),
    "is_it_just_beta": (
        "Olabilir: radar yalnızca Long önerir. Bu yüzden ana ölçü eşit ağırlıklı evren "
        "getirisine göre fazla getiridir; mutlak getiri ayrıca raporlanır."
    ),
    "microstructure_risk": (
        "MEXC'de küçük coinlerin derinliği Binance'ten zayıftır; giriş bir sonraki "
        "mumun açılışından varsayılır. Gerçek dolum, özellikle hızlı yükselen coinde, "
        "daha kötü olabilir."
    ),
    "slippage_risk": (
        "Taban 5 bp/taraf; momentumla yükselen altcoinlerde 10-30 bp görülebilir. Stres "
        "senaryosu komisyon x1.5 ve slippage x2 uygular; fazla getiri küçükse maliyet "
        "onu tamamen silebilir."
    ),
    "hidden_correlation": (
        "İlk 3 çoğu zaman aynı anlatıdaki coinlerden oluşur (aynı sektör/ekosistem); "
        "aynı gün seçilenler bağımsız değildir, bu yüzden güven aralığı gün bazında "
        "küme bootstrap ile hesaplanır."
    ),
    "shared_risk_factor": (
        "Evet — altcoinler ortak kripto ve altcoin betası taşır; üç ayrı Long fiilen "
        "tek bir altcoin-beta pozisyonudur."
    ),
    "liquidity_shock": (
        "Borsa kaynaklı çöküşlerde (2022 LUNA, FTX) altcoinler saatler içinde %30-90 "
        "düşebilir; kalkan coinler son işlem fiyatından çıkar, gerçek çıkış daha kötü "
        "olabilir."
    ),
    "funding_anomaly": (
        "Spot replay'de funding yok; aynı seçim perpetual ile uygulanırsa coşku "
        "dönemlerinde yüksek funding fazla getiriyi eritebilir."
    ),
}

LIQUID_BLOW_UP_SCENARIOS = (
    BlowUpScenario(
        title="Momentum coin'inin tepeden alınması",
        cause="Radar 1-4 saatlik yükselişi ödüllendirir; alım çoğu zaman hareketin sonuna denk gelir.",
        early_warning="Seçilen coinlerin 1-4 saatlik fazla getirisi negatif; 24s değişimi +%15-25 bandında yoğunlaşma.",
        loss_mechanism="Geri çekilmede stop olmadığı için zarar büyür; birkaç seçim aynı anda geri verir.",
        protection="Önceden tanımlı invalidasyon ve stop, 24s uzama filtresinin sıkılaştırılması (ancak yeni ön-kayıtlı denemeyle).",
    ),
    BlowUpScenario(
        title="Delisting veya borsa çöküşü",
        cause="Likit görünen bir coin (LUNA, FTT örnekleri) günler içinde değersizleşir veya borsadan kalkar.",
        early_warning="Hacim patlaması ile birlikte sert düşüş, borsa duyuruları, stablecoin çıpasından sapma.",
        loss_mechanism="Pozisyon yüzde 50-100 değer kaybeder; çıkış likiditesi kaybolur.",
        protection="Tek coin maruziyet tavanı, haber/duyuru vetosu, kalkan coinleri içeren survivorship'siz test.",
    ),
    BlowUpScenario(
        title="Korele altcoin çöküşü",
        cause="İlk 3'teki coinler aynı sektörden; sektör anlatısı çöktüğünde hepsi birlikte düşer.",
        early_warning="İlk 3'ün aynı ekosistemden olması, BTC dominansının hızla yükselmesi.",
        loss_mechanism="Üç pozisyon tek bir bahis gibi davranır; günlük zarar limiti tek olayda aşılır.",
        protection="Sektör ve korelasyon kümesi başına tek pozisyon (trading/risk/portfolio.py), toplam altcoin-beta tavanı.",
    ),
    BlowUpScenario(
        title="MEXC mikro yapısı ve geniş spread",
        cause="MEXC'de küçük coinlerin derinliği sığdır; replay Binance fiyatları ve sabit spread varsayar.",
        early_warning="Canlı MEXC spread'i 20-35 bp'ye yaklaşıyor, emir defteri derinliği pozisyon boyutunun birkaç katı değil.",
        loss_mechanism="Giriş ve çıkış maliyeti fazla getiriyi aşar; ölçülen kenar canlıda yoktur.",
        protection="Canlı spread kapısı, pozisyon boyutunu derinliğe göre sınırlama, canlı ileri kayıtla karşılaştırma.",
    ),
    BlowUpScenario(
        title="Arz katmanının yanıltması",
        cause="Canlı puanın %25'i CoinGecko arz verisi; bu katman test edilmedi ve yanlış/eksik veri sıralamayı değiştirebilir.",
        early_warning="Arz verisi eksik coin sayısında artış, CoinGecko hata oranı.",
        loss_mechanism="Test edilmemiş bir katman canlı sıralamayı replay'den farklılaştırır; replay sonucu canlıya genellenemez.",
        protection="Arz katmanı için ayrı ileri kayıt; replay sonucu yalnızca teknik katman için geçerli sayılır.",
    ),
)
