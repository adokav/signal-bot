"""Hypothesis, fragility answers and blow-up scenarios for TSMOM (spec §14, §42, §43).

Kept next to the strategy so the report is regenerated with every backtest
run instead of drifting in a hand-edited document.
"""

from __future__ import annotations

from trading.research.report import BlowUpScenario, StrategyHypothesis


TSMOM_FAMILY = "tsmom_long_single_symbol"

TSMOM_HYPOTHESIS = StrategyHypothesis(
    name="TSMOM long-only + volatility targeting (tek sembol, Binance USDⓈ-M)",
    market_logic=(
        "Zaman serisi momentumu: yatırımcılar yeni bilgiye yavaş tepki verir, "
        "ardından trend takibi ve kaldıraçlı talep hareketi uzatır; bu yüzden "
        "son 60 günlük getirinin işareti sonraki haftaların getiri işaretini "
        "zayıf ama pozitif biçimde öngörür (Moskowitz-Ooi-Pedersen 2012, "
        "Liu-Tsyvinski 2018). Volatilite hedefleme, volatilite kümelenmesi "
        "nedeniyle sakin dönemlerde risk başına getirinin daha yüksek olduğu "
        "gözlemine dayanır (Moreira-Muir 2017). UYARI: literatürdeki TSMOM "
        "pozisyonu sinyal dönene kadar tutar; bu uygulama ise girişi kısa "
        "vadeli bir ATR çıkış merdiveniyle birleştirir. Merdivenin trend "
        "takibi için ekonomik gerekçesi yoktur ve kazananları keser — bu test "
        "saf TSMOM'u değil, 'TSMOM girişi + swing çıkışı' kombinasyonunu ölçer."
    ),
    feature_rationale={
        "lookback_return_60d": (
            "Tek giriş filtresi: 60 günlük getiri > 0 ise LONG. Momentum etkisinin "
            "raporlandığı 1-3 aylık bandın ortası; optimize edilmedi."
        ),
        "realized_vol_30d": (
            "Yalnızca boyutlandırma: hedef vol / gerçekleşen vol oranı pozisyonu "
            "ölçekler; yön kararına katılmaz."
        ),
        "atr_14d": (
            "Stop ve hedef mesafelerini güncel fiyat aralığına göre ölçekler; "
            "sabit yüzde eşiklerinin varlıklar arası taşınamaması nedeniyle."
        ),
    },
    parameter_rationale={
        "lookback_days=60": "Literatürdeki 1-3 ay bandının ortası; v1 öncesi sabitlendi, sonuca göre ayarlanmadı.",
        "realized_vol_lookback_days=30": "AQR tarzı vol hedeflemede standart bir aylık pencere.",
        "target_annualized_vol_pct=40": "BTC'nin uzun dönem gerçekleşen volünün (%50-60) altında; ölçek çoğu zaman 1x altında kalır.",
        "max_leverage=1.0": "v1'de 3x tavan + çakışan pozisyonlar -%122 DD üretti; kaldıraç kaldırıldı (v2 seçimi v1'i gördükten sonra yapıldı, deneme kaydında sayılır).",
        "time_stop=7d": "48 saat trend takibi için çok kısaydı (v1: 578 işlem); bir haftalık tutma en az bir salınımı kapsar.",
        "stop/T1/T2 = 1.5/1.0/2.0 ATR": "Ekonomik gerekçesi ZAYIF: swing-trade konvansiyonu, momentum literatüründen türetilmedi; en şüpheli parametre grubu budur.",
    },
)

TSMOM_FRAGILITY_ANSWERS = {
    "regime_that_kills_edge": (
        "60 günlük getirinin sıfır etrafında sık işaret değiştirdiği yatay/chop "
        "dönemleri ve V-dönüşleri. Long-only olduğu için ayı piyasasında düz kalır "
        "ama geçiş bölgelerinde tekrarlı stop + maliyet üretir."
    ),
    "most_important_feature": (
        "60 günlük getiri işareti — tek giriş filtresi. Vol ölçekleme yalnızca "
        "boyutu değiştirir, hangi günlerde işlem açıldığını değiştirmez."
    ),
    "performance_without_top_feature": (
        "Ölçülmedi. Filtre çıkarılırsa strateji 'her gün long + aynı çıkış "
        "merdiveni' olur; bu kontrol koşusu yapılmadan sinyalin katkısı "
        "ayrıştırılamaz. Bir sonraki deneme olarak planlandı."
    ),
    "possible_data_leakage": (
        "Giriş, sinyali üreten günlük mumun kapanış fiyatından yapılıyor — hafif "
        "iyimser; +1s/+4s giriş gecikmesi stres testi bunu ölçer. Funding satırları "
        "(entry, exit] aralığıyla sınırlı. Parametre fit edilmediği için fold "
        "sızıntısı yok, ama v2 parametreleri v1 sonucu görüldükten sonra seçildi."
    ),
    "is_it_just_beta": (
        "Büyük olasılıkla evet: tek sembol, long-only BTC. Decay raporundaki "
        "excess_mean_pct (koşulsuz ileri getiriye göre fark) ve B&H karşılaştırması "
        "bunu ayırır; excess sıfıra yakınsa sinyal beta'dan başka bir şey eklemiyor."
    ),
    "microstructure_risk": (
        "Günlük kapanışta BTC perp derin, giriş etkisi küçük. Asıl risk çıkışta: "
        "harness stop/hedefin tam seviyeden dolduğunu varsayar; gap'te stop çok "
        "daha aşağıdan dolar. Aynı saatlik mumda hem stop hem hedef varsa stop "
        "önce sayılır (muhafazakâr)."
    ),
    "slippage_risk": (
        "2 bp/taraf BTC için < $1M boyutta makul; volatilite patlamasında 5-10 bp "
        "görülebilir. Slippage ×2 ve komisyon ×1.5 senaryoları rapora eklenir."
    ),
    "hidden_correlation": (
        "Tek sembolde yok. ETH/SOL'a genişletilirse BTC ile 0.8+ korelasyon "
        "nedeniyle üç ayrı pozisyon fiilen tek bir kripto-beta bahsidir."
    ),
    "shared_risk_factor": (
        "Evet — tüm büyük kripto varlıkları ortak bir piyasa faktörü taşır; "
        "çoklu sembol sürümünde portföy beta limiti (spec §24-25) zorunlu olur."
    ),
    "liquidity_shock": (
        "Flash crash'lerde (2021-05-19, 2022-06, 2024-08-05) saatlik mumlar "
        "intrabar dibi gösterir ama dolum sırası bilinmez; stop dolum varsayımı "
        "gerçek kaybı küçük gösterebilir."
    ),
    "funding_anomaly": (
        "Evet: momentum long'da olduğu anlar tam da funding'in yüksek olduğu "
        "coşku dönemleridir (2021'de 8 saatte %0.1'e kadar ≈ yıllık %100+). "
        "Funding ×2 stres senaryosu bu taşıma maliyetini ölçer."
    ),
}

TSMOM_BLOW_UP_SCENARIOS = (
    BlowUpScenario(
        title="Gap ile stop'un altından dolum (likidasyon kaskadı)",
        cause="Borsa çapında kaldıraçlı long tasfiyesi; fiyat stop seviyesinin altına boşluk yapar.",
        early_warning="Open interest tarihi zirvede, funding 8 saatte %0.05 üstünde, basis geniş ve likidasyon hacmi artıyor.",
        loss_mechanism="Stop-market emri planlanan seviyenin çok altından dolar; kaldıraç varsa zarar katlanır ve teminat erir.",
        protection="Kaldıraç 1x tavan, isolated margin, işlem başı risk limiti, OI/funding persentili 95'in üstündeyken yeni giriş yok.",
    ),
    BlowUpScenario(
        title="Üst üste binen pozisyonlar (v1'de fiilen yaşandı)",
        cause="Motorun açık pozisyon varken yeni sinyalde tekrar long açması; retry/restart'ta çift emir.",
        early_warning="Toplam brüt maruziyet 1x notional'ı aşıyor, açık pozisyon sayısı > 1, bot kaydı ile borsa pozisyonu uyuşmuyor.",
        loss_mechanism="5-10 eşzamanlı long fiilen 5-10x kaldıraçtır; tek bir kötü hafta -%100'ün ötesinde kümülatif düşüş üretir (v1: -%122).",
        protection="Tek pozisyon değişmezi, kodda zorunlu brüt maruziyet tavanı, benzersiz emir kimliği, borsa-bot mutabakatı uyuşmazsa yeni işlem yok.",
    ),
    BlowUpScenario(
        title="Coşku döneminde funding kanaması",
        cause="Güçlü trendde long tarafı haftalarca pozitif funding öder.",
        early_warning="7 günlük ortalama funding yıllık %30'un üstünde; funding kendi 90 günlük dağılımının 90. persentilinin üstünde.",
        loss_mechanism="Taşıma maliyeti trend kazancını aşar; beklenen değer pozitif görünse bile net getiri negatife döner.",
        protection="Funding'i EV kapısına dahil et, funding ×2 stres testi, funding persentili yüksekken giriş veto.",
    ),
    BlowUpScenario(
        title="Yatay piyasada ardışık stop serisi",
        cause="60 günlük getiri sıfır etrafında salınır; her yeni long kısa sürede stop olur.",
        early_warning="30 günde birden fazla sinyal işaret değişimi, art arda kayıp sayısı Monte Carlo p95 seviyesine yaklaşıyor.",
        loss_mechanism="Küçük kayıplar + maliyetler birikir; ardışık kayıp serisi psikolojik ve sermaye olarak hesabı aşındırır.",
        protection="Drawdown merdiveni (%3/%5/%8'de risk azaltma, %10'da yeni işlem durdurma), ardışık kayıp limiti.",
    ),
    BlowUpScenario(
        title="Borsa/API kesintisi sırasında korumasız pozisyon",
        cause="Binance bakım, rate limit veya coğrafi erişim engeli (HTTP 451) sırasında çıkış emri gönderilemez.",
        early_warning="API hata oranı artışı, WebSocket kopması, veri tazeliği eşiğinin aşılması.",
        loss_mechanism="Bot tarafında tutulan stop tetiklenmez; pozisyon kontrolsüz açık kalır.",
        protection="Stop'ları borsa tarafında bekleyen emir olarak koy, kill switch veri bayatlığında yeni emirleri durdurur, yeniden başlatma açık onay ister.",
    ),
)
