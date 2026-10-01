"""Hypothesis, fragility answers and blow-up scenarios for the tactical Long radar.

Written before the historical replay (``trading.backtest.tactical_replay``)
was run, and regenerated into every replay report so the text cannot drift
away from the evidence it sits next to.
"""

from __future__ import annotations

from trading.research.report import BlowUpScenario, StrategyHypothesis


TACTICAL_HYPOTHESIS = StrategyHypothesis(
    name="BTC/ETH taktik Long radarı (4 setup ailesi, spot, 5 dakikalık tarama)",
    market_logic=(
        "Dört ayrı iddia, hiçbiri bu sistemde daha önce test edilmedi: "
        "(1) TREND_PULLBACK — 4H yükselen yapıda 1H EMA20 değer bölgesine geri "
        "çekilme, trend devamından önce likidite toplama noktasıdır; "
        "(2) BREAKOUT_RETEST — en az iki kez test edilmiş direnç kırıldıktan sonra "
        "yeniden test, eski satıcıların alıcıya dönüştüğü bölgedir; "
        "(3) RANGE_RECLAIM — destek altına sarkıp geri alınan aralık, başarısız "
        "kırılım nedeniyle tuzağa düşen satıcıların kapatmasıyla yukarı döner; "
        "(4) LIQUIDITY_SWEEP_RECLAIM — onaylanmış swing dibinin altındaki stop "
        "likiditesi süpürülüp kapanışla geri alınınca kısa vadeli dip oluşur. "
        "Bunlar yaygın teknik analiz anlatılarıdır; akademik dayanakları zayıftır ve "
        "kripto spotta maliyet sonrası kenarları olduğu varsayılmamalıdır. Replay, "
        "anlatının değil motorun ürettiği uyarıların sonucunu ölçer."
    ),
    feature_rationale={
        "4h_structure": (
            "Onaylanmış H4 swing'lerinden sınıflanan yapı; BEARISH/INSUFFICIENT_DATA "
            "durumunda hiç Long önerilmez. Rejim filtresi olarak kullanılır, ayrıca "
            "doğrulanmadı."
        ),
        "1h_zones_and_ema": (
            "1H swing kümeleri destek/direnç bölgeleri, EMA20/EMA50 trend değeri; giriş "
            "bölgesi, invalidasyon ve hedefler bunlardan türetilir."
        ),
        "m15_m5_confirmation": (
            "Gövde/aralık >= 0.45, önceki mumun tepesi üstünde kapanış ve hacim oranı "
            ">= 0.85 'onay' sayılır; TRIGGERED ile READY arasındaki farkı belirler."
        ),
        "ethbtc_relative_strength": (
            "Yalnızca ETH için risk bayrağı (EMA20 < EMA50 ve fiyat EMA20 altında); "
            "setup'ı engellemez, sonuç üzerindeki etkisi ölçülmedi."
        ),
    },
    parameter_rationale={
        "ATR çarpanları (0.05-0.75)": (
            "Elle seçildi, optimize edilmedi ve hiçbir veri üzerinde doğrulanmadı; "
            "satır içi sabitler oldukları için bu replay parametre pertürbasyonu "
            "yapamaz — sağlamlık bilinmiyor."
        ),
        "minimum net R/R 1.5 (T1) ve 2.0 (T2)": (
            "Planın kabulü için alt sınır; motorun kendi maliyet tahmini (%0.04-0.06) "
            "ile hesaplanır, replay ise kendi maliyet modelini ayrıca uygular."
        ),
        "geçerlilik süreleri 4-12 saat": (
            "Setup başına sabit; limit bu sürede dolmazsa NOT_FILLED. Seçim gerekçesi "
            "belgelenmedi, sonuçla ayarlanmadı."
        ),
        "48 saat zaman çıkışı": (
            "Canlı forward_ledger kuralı; replay aynı kodu kullanır, T2 modellenmez."
        ),
    },
)

TACTICAL_FRAGILITY_ANSWERS = {
    "regime_that_kills_edge": (
        "Sert düşüş öncesi yatay-yükselen dönemler: 4H yapı henüz BEARISH'e dönmeden "
        "destek altına sarkmalar RANGE_RECLAIM ve LIQUIDITY_SWEEP sinyali üretir, "
        "ardından trend kırılır. Yıl bazlı tablo bunu ayrıştırmaya çalışır."
    ),
    "most_important_feature": (
        "4H yapı filtresi ve 1H destek bölgeleri — dört setup'ın tamamı bunlara "
        "dayanır. Hangisinin katkı verdiği bu replay'de ayrıştırılmadı (ablation yok)."
    ),
    "performance_without_top_feature": (
        "Ölçülmedi. Yapı filtresiz ya da rastgele zamanlı aynı geometrili bir kontrol "
        "grubu bu replay'in kapsamında değil; olumlu sonuç çıkarsa ilk ek deney budur."
    ),
    "possible_data_leakage": (
        "Her zaman diliminde yalnızca kapanışı karar anından önce olan mumlar kullanılır; "
        "swing'ler motor içinde sağ taraf onayı ister. Kotasyon son kapanmış M5 "
        "kapanışından türetilir (geleceğe bakmaz ama canlı bid/ask'tan farklıdır)."
    ),
    "is_it_just_beta": (
        "Kısmen olabilir: yalnızca Long ve yalnızca 4H yapı yükselirken. Aynı dönemde "
        "koşulsuz 'her M5'te al, 48 saat tut' getirisiyle karşılaştırma yapılmadı; "
        "pozitif sonuç beta ayrıştırılmadan kenar sayılmamalı."
    ),
    "microstructure_risk": (
        "Limit dolumu fiyat bölgeye değdiğinde varsayılır; kuyruk sırası yok sayılır. "
        "Gerçekte fiyatın limite tam değip döndüğü en iyi işlemler dolmayabilir "
        "(ters seçilim) — bu varsayım sonuçları iyimser gösterir."
    ),
    "slippage_risk": (
        "Taban 2 bp/taraf; stop çıkışlarında hızlı düşüşte 5-20 bp mümkündür. Stres "
        "senaryosu komisyon x1.5 ve slippage x2 uygular; stop dolumu "
        "min(stop, açılış) ile gap'i kısmen yansıtır."
    ),
    "hidden_correlation": (
        "BTC ve ETH kayıtları aynı saatlerde açılıp aynı haberle stop olabilir; "
        "birleşik örnek bağımsız sayılırsa güven aralığı daralır. Blok bootstrap (5) "
        "bunu kısmen hesaba katar, sembol bazlı tablolar ayrıca verilir."
    ),
    "shared_risk_factor": (
        "Evet — iki sembol de kripto piyasa faktörüne bağlı; ETH ayrıca BTC yapısı "
        "BEARISH iken engellenir. Fiilen tek bir Long-kripto bahsidir."
    ),
    "liquidity_shock": (
        "Flash crash'te (2021-05-19, 2022-06, 2024-08-05) M5 mumu stop'un çok altına "
        "açılabilir; ledger stop'u min(stop, açılış)'tan doldurur ama mum içi gerçek "
        "dolum daha da kötü olabilir."
    ),
    "funding_anomaly": (
        "Spot işlem olduğu için funding yok; ancak canlı uygulama perpetual ile "
        "yapılırsa coşku dönemlerinde funding maliyeti bu replay'de hiç yoktur."
    ),
}

TACTICAL_BLOW_UP_SCENARIOS = (
    BlowUpScenario(
        title="Limit dolumunda ters seçilim",
        cause="Limit emir yalnızca fiyat bölgenin içine düşmeye devam ettiğinde tam dolar; geri sekenler dolmaz.",
        early_warning="Canlı forward ledger'da dolum oranı replay'den belirgin düşük, dolan işlemlerin stop oranı yüksek.",
        loss_mechanism="Replay'in kazanan işlemlerinin bir kısmı gerçekte hiç açılmaz, kaybedenlerin tamamı açılır; beklenti negatife döner.",
        protection="Canlı ileri kayıtla dolum oranını karşılaştır; replay sonucu tek başına yetki vermez, kâğıt üzerinde en az 100 çözümlenmiş canlı kayıt gerekir.",
    ),
    BlowUpScenario(
        title="Korele çifte stop (BTC + ETH aynı anda)",
        cause="Piyasa çapında satış iki sembolde de aynı saatlerde Long setup üretir ve ikisini birlikte stop eder.",
        early_warning="İki sembolde aynı 1 saat içinde açık kayıt; BTC 4H yapısı NEUTRAL'e dönüyor.",
        loss_mechanism="İşlem başı risk ikiye katlanır; ardışık kayıp serisi tek sembol tahmininden hızlı derinleşir.",
        protection="Portföy düzeyinde toplam açık risk limiti (trading/risk/portfolio.py) ve korelasyon kümesi başına tek pozisyon.",
    ),
    BlowUpScenario(
        title="Kaskad sırasında stop kayması",
        cause="Kaldıraçlı long tasfiyesi fiyatı dakikalar içinde stop seviyesinin çok altına iter.",
        early_warning="Open interest zirvede, funding yüksek, M5 aralıkları ATR'nin birkaç katına çıkıyor.",
        loss_mechanism="Stop planlanandan 1R'nin çok ötesinde dolar; tek işlem birkaç işlemin kazancını siler.",
        protection="Borsa tarafında stop emri, işlem başı risk sınırı, volatilite patlamasında yeni giriş vetosu (kill switch).",
    ),
    BlowUpScenario(
        title="Borsa uyuşmazlığı (Binance verisi, MEXC uygulaması)",
        cause="Replay Binance spot fitillerini kullanır; canlı radar MEXC verisiyle çalışır ve fitiller farklı olabilir.",
        early_warning="Aynı zaman diliminde MEXC ve Binance M5 dip/tepe farkı stop mesafesinin anlamlı bir kısmına ulaşıyor.",
        loss_mechanism="Replay'de dokunulmayan stop canlıda tetiklenir veya hedef hiç görülmez; ölçülen kenar gerçek kenar değildir.",
        protection="Canlı forward ledger sonucu replay ile karşılaştırılmadan hiçbir setup WATCH'tan çıkmaz.",
    ),
    BlowUpScenario(
        title="Aşırı uyarı ve seçici uygulama",
        cause="Radar sık READY/TRIGGERED üretir; kullanıcı yalnızca bazılarını uygular ve seçim istatistiği bozar.",
        early_warning="Günde birden fazla yeni kayıt, kullanıcının işlem günlüğü ile ledger arasında belirgin fark.",
        loss_mechanism="Uygulanan alt küme ölçülen popülasyonu temsil etmez; beklenti sessizce kötüleşir.",
        protection="Yalnızca karar kuralından geçen setup'ları göster; uygulanan ve uygulanmayan işlemleri aynı ledger'da takip et.",
    ),
)
