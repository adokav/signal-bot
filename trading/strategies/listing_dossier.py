"""Hypothesis, fragility answers and blow-up scenarios for the new-listing cohort study.

Written before the study (``trading.backtest.listing_replay``) was run.
"""

from __future__ import annotations

from trading.research.report import BlowUpScenario, StrategyHypothesis


LISTING_HYPOTHESIS = StrategyHypothesis(
    name="Yeni listeleme kohortu: yeni Binance USDT listelemesini almak para kaybettirir mi? (kayıptan kaçınma)",
    market_logic=(
        "Yeni listelemede talep geçici ve yoğundur: listeleme heyecanı, piyango tercihi, kısa vadeli işlemciler. "
        "Arz ise zamanla artar: airdrop alıcıları, erken yatırımcılar ve kilit açılımları satar. Uygulayıcı "
        "raporları ilk gün alanların aylar içinde paranın büyük kısmını kaybettiğini söylüyor (yöntemleri ve "
        "survivorship durumları belirsiz). Canlı MEXC yeni listeleme radarı adaylara HOT/BUILDING/WATCH etiketi "
        "veriyor; bu etiketler hiç tarihsel kanıtla sınanmadı. Bu çalışma bir long stratejisi değil, kaçınma "
        "iddiasını sınar: yeni listelemeyi sabit bir anda alıp tutmak ortalamada para kaybettiriyor ve BTC'nin "
        "gerisinde kalıyor mu?"
    ),
    feature_rationale={
        "ilk işlemden geçen süre (+1s / +24s / +7g)": (
            "Radarın adayı gösterebileceği üç an: listelemenin hemen ardından, ilk günün sonunda, ilk haftanın "
            "sonunda. Önceden seçildi."
        ),
        "ilk pompa (tanılayıcı)": (
            "Giriş fiyatının ilk işlem açılışına oranı; canlı radarın %80 üstü CROWDED ve %-35 altı ağır satış "
            "kurallarının geçmişte bir şey ayırıp ayırmadığını görmek için. Karar vermez."
        ),
        "girişten önceki hacim (tanılayıcı)": "Sabit bantlar (5M / 50M USD); yalnızca girişten önce kapanmış mumlar.",
    },
    parameter_rationale={
        "tutma 30 ve 90 gün": "Raporların baktığı ufuklar; kısa vadeli gürültünün ötesi. Önceden seçildi.",
        "karar brüt getiriyle": (
            "Kayıp iddiasında maliyeti yok saymak muhafazakârdır; net değerler (7.5 + 25 bp/taraf) ayrıca raporlanır."
        ),
        "en az 100 listeleme": "Ağır kuyruklu getirilerde ortalamanın anlamlı olması için alt sınır.",
        "listeleme ayına göre küme bootstrap": "Aynı ay listelenen coinler aynı piyasa hareketini paylaşır.",
    },
)

LISTING_FRAGILITY_ANSWERS = {
    "regime_that_kills_edge": (
        "Güçlü altcoin boğası (2021 başı, 2024 sonu): yeni listelemeler de yükselir. Bu yüzden karar iki "
        "kronolojik yarıda ayrı ayrı negatiflik ister; yıl kırılımı raporlanır."
    ),
    "most_important_feature": "Tek koşul: coinin borsada yeni olması ve girişin listelemeden sabit süre sonra olması.",
    "performance_without_top_feature": (
        "Yeni olma koşulu kalkarsa bu, rastgele bir altcoin tutmaktır; o durumun karşılaştırması BTC getirisidir."
    ),
    "possible_data_leakage": (
        "Giriş zamanı yalnızca ilk işlem anına bağlı; tanılayıcı bölmeler girişten önce kapanmış mumları kullanır. "
        "Borsadan kalkanlar dahil (survivorship yok). Çıkışı veri sonundan sonraya düşen gözlem alınmaz, kırpılmaz."
    ),
    "is_it_just_beta": (
        "Yeni coinler yüksek betalıdır; bu yüzden karar mutlak getiriye ek olarak BTC'ye göre fazla getiri ister."
    ),
    "microstructure_risk": (
        "İlk saatlerde spread çok geniştir; +1s girişinin fiyatı gerçekte ulaşılamayabilir. Kaçınma iddiasını "
        "etkilemez (maliyet sonucu yalnızca kötüleştirir)."
    ),
    "slippage_risk": "Karar brüt getiriyle verilir; kayma yalnızca net değerleri kötüleştirir.",
    "hidden_correlation": (
        "Aynı haftada listelenen coinler aynı piyasa hareketini paylaşır; güven aralığı ay kümeleriyle hesaplanır."
    ),
    "shared_risk_factor": "Altcoin piyasa rejimi ve likidite döngüleri; listeleme sayısı boğada artar.",
    "liquidity_shock": "Kalkan coin son işlem fiyatından çıkar; gerçekte son günlerde satmak daha da zor olabilir.",
    "funding_anomaly": "Spot çalışma; funding etkisi yok.",
}

LISTING_BLOW_UP_SCENARIOS = (
    BlowUpScenario(
        title="Yeni listelemeyi kovalamak",
        cause="Radarın HOT etiketi kanıtlanmış bir fırsat gibi okunur; listeleme pompasının tepesinde alınır.",
        early_warning="İlk saatlerde yüzde onlarca yükseliş, sosyal medyada yoğun ilgi, ince emir defteri.",
        loss_mechanism="Arz açıldıkça (airdrop, kilit açılımı) fiyat aylarca düşer; ortalama kayıp büyüktür.",
        protection="Kaçınma kanıtı varsa radar adayı fırsat olarak göstermez; emir yetkisi zaten yok.",
    ),
    BlowUpScenario(
        title="Sağ kuyruğa güvenmek",
        cause="Birkaç coinin 10 kat yükselmesi ortalamayı pozitif gösterebilir; tek tek alımlarda çoğunluk kaybeder.",
        early_warning="Medyan getiri ile ortalama arasında büyük fark; pozitif getiri oranının %50'nin çok altında olması.",
        loss_mechanism="Küçük bir sepetle büyük kazanan yakalanmazsa portföy medyan sonuca yakın kaybeder.",
        protection="Medyan ve pozitif oran ortalamayla birlikte raporlanır; karar yalnızca ortalamaya dayanmaz.",
    ),
    BlowUpScenario(
        title="Borsadan kalkma ve manipülasyon",
        cause="Yeni coin aylar içinde delist edilir, likiditesi kaybolur ya da fiyatı manipüle edilir.",
        early_warning="İzleme etiketi, hacimde ani düşüş, kilit açılımı takvimi, tek borsada yoğun hacim.",
        loss_mechanism="Çıkış fiyatı son işlem fiyatından da kötü olabilir; pozisyon satılamayabilir.",
        protection="Kalkanlar dahil ölçülür; canlı tarafta tuzak riski kuralları ve sermaye evreni dışı yasak sürer.",
    ),
)
