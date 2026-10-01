# Haftalık momentum — Likit-100 evreninde kesitsel test

**Durum:** Adım 1 (keşif). Bu belgenin protokol ve karar kuralları keşif
sonuçlarından **önce** yazıldı (2026-10-01, kullanıcı onayı: "önerdiğin her
iki adımı da uygulayalım").
**Kod:** `trading/backtest/weekly_momentum.py`.

Araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Neden

Likit-100 radarı kısa vadede (24 saat) çok yükselmiş coinleri alıyor. Bu,
sonraki günlerde geri veriliyor (`docs/SIGNAL_QUALITY.md`: uyarıları 7–30
gün tutmak farkı büyütüyor). Kullanıcının sorusu: "Ufuk mu kısa?"

Cevap: Aynı sinyali uzun tutmak çözüm değil. Uzun vade için o vadeye göre
kurulmuş ayrı bir sinyal gerekir. Akademik çalışmalarda kriptoda **haftalık
kesitsel momentum** etkisi bildirildi:

- **Bildiren çalışma:** Liu, Tsyvinski ve Wu, 2022, *Journal of Finance*.
  Son 1–4 haftanın kazananları sonraki haftalarda da kazanıyor.
- **Bizim verimizde:** Bu etki test edilmedi. O çalışma çoğunlukla
  2014–2018 verisine dayanıyor ve küçük coinleri de içeriyor.

## Sinyal (keşifte incelenecek ızgara, sonuçtan önce sabit)

- **Evren:** Her pazartesi 00:00 UTC'de, son 24 saatin hacmine göre ilk 100
  USDT spot çifti.
  - Canlı radarla aynı `select_liquid_universe` kullanılır, min. 1M USD.
  - Survivorship'ten arındırılmış Binance verisi.
  - Tarihsel kimlik katmanı stabil/sarılı sembolleri çıkarır.
- **Sıralama:** Son **L hafta** getirisi, kapanmış mumlarla.
  - L ∈ {1, 2, 4}.
  - Getirisi hesaplanamayan coin sıralamaya girmez (nötr sayılmaz).
  - 50'den az coin sıralanabiliyorsa o hafta portföy kurulmaz.
- **Portföy:** En yüksek getirili **%20** (ilk 100'de 20 coin), eşit ağırlık,
  **H hafta** tutulur.
  - H ∈ {1, 4}.
  - H = 4 için her hafta yeni bir kohort kurulur. Haftalık getiri, aktif
    kohortların ortalamasıdır (Jegadeesh–Titman). Böylece haftalık seri
    üst üste binmez.
- **Kıyas:** Aynı hafta, eşit ağırlıklı ilk 100. Haftalık yeniden
  dengelenir.
- **Maliyet:**
  - Her kohort girişte ve çıkışta bir kez öder: gidiş-dönüş %0.20, haftaya
    yayılınca %0.20 / H.
  - Coinler arasında netleşme varsayılmaz, bu yüzden maliyet abartılı
    tarafta.
  - Stres maliyeti %0.35.
- **Borsadan kalkan coin:** Son işlem fiyatında donar. Verisi boşluktan
  sonra devam eden ya da indirme sınırına takılan coinin o haftası
  bilinmez; ortalamaya girmez ve sayılır.
- **Tanılayıcı (karar vermez):** En düşük %20 ve en yüksek − en düşük
  farkı (momentum mu, ters dönüş mü?).

Toplam **6 varyant** (3 L × 2 H) keşifte incelenir. Bunlardan **en fazla
ikisi** ön-kayda alınır.

## Pencereler

| Pencere | Aralık | Ne zaman görülür |
|---|---|---|
| Keşif | 2020-10 → 2024-08 (≈ 200 hafta) | Şimdi; veri 2024-09-01 itibarıyla mühürlü |
| Doğrulama | 2024-09 → 2026-08 (≈ 104 hafta) | Ön-kayıttan sonra, **bir kez**, GitHub Actions'ta |

**Bilinen kirlenme:** Likit-100 radarının yıllık sonuçları ve sinyal kalitesi
doğrulamasının sonuçları (2024–26) görüldü. Haftalık momentum portföyünün
doğrulama penceresindeki sonucu hiç görülmedi.

## Önceden beklenen yön (keşiften önce)

**Belirsiz.**

- Literatür 1–4 haftada momentum bildiriyor.
- Bizim günlük ufuktaki bulgumuz ise **ters dönüş** yönünde: 24 saatte çok
  yükselen coin geri veriyor.
- Likit coinlerde ve 2020 sonrasında momentumun zayıf ya da hiç olmaması
  makul bir beklenti.
- 1 haftalık sıralamada ters dönüş, 4 haftalıkta momentum görülmesi de
  mümkün.

## Doğrulama karar kuralları (ön-kayıtlı, sonuçlardan önce)

`k` ön-kayıtlı varyant sayısıdır (≤ 2). Bonferroni düzeltmesi α = 0.05 / k.
Güven aralıkları, **haftalar üzerinde 4 haftalık bloklarla** dairesel
hareketli blok bootstrap ile hesaplanır (4000 örnek). Doğrulama penceresinin
yarıları 2025-09-01'de ayrılır.

Bir varyant **PASS** olur, ancak ve ancak:

1. ≥ 80 hafta;
2. haftalık ortalama **net** getirinin (maliyet sonrası) alt sınırı > 0;
3. haftalık ortalama **fazla getirinin** (net − ilk 100) alt sınırı > 0;
4. stres maliyetiyle ortalama net getiri > 0;
5. iki yarıda hem net hem fazla getiri > 0.

Diğer kararlar:

- **SEPETİ_YENER:** PASS değil; ama fazla getirinin alt sınırı > 0 ve iki
  yarıda > 0. Seçim becerisi var, mutlak kâr gösterilmedi.
- **TERS_DÖNÜŞ:** Fazla getirinin **üst** sınırı < 0. Geçen haftaların
  kazananlarını almak sepete göre kaybettiriyor.
- **NO_EFFECT:** Diğer durumlar.
- **INCOMPLETE_DATA (karar yok):** Bilinmeyen üye-haftası %2'yi ya da
  portföy kurulamayan hafta %5'i aşarsa.

**Canlıya etkisi:**

- **PASS / SEPETİ_YENER:** Haftalık bir "momentum listesi" mesajı eklenir.
  Kanıt etiketiyle gelir; SEPETİ_YENER'de "kâr kanıtı yok" yazar. Emir
  yetkisi yoktur.
- **TERS_DÖNÜŞ:** Likit-100 belgelerine ve panele "geçen haftanın
  kazananını kovalamak sepete göre kaybettirdi" notu eklenir.
- **NO_EFFECT:** Değişiklik yok.

**Güç (dürüst not):** Doğrulama ≈ 104 hafta. Haftalık fazla getirinin
oynaklığı yüzde birkaç puan olduğundan, ancak haftada ≈ %0.5–1'lik büyük bir
etki anlamlı çıkar. Gerçek ama küçük bir etki NO_EFFECT görünebilir.

## Keşif sonuçları

Henüz yok.
