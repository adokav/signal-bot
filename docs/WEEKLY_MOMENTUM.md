# Haftalık momentum — Likit-100 evreninde kesitsel test

**Durum:** Keşif tamamlandı. **Altı varyantın hiçbiri etki göstermedi.**
Hiçbiri ön-kayda alınmadı; doğrulama penceresi bu aile için mühürlü kaldı
(ayrıntı: "Keşif sonuçları").

Bu belgenin protokol ve karar kuralları keşif sonuçlarından **önce** yazıldı
(2026-10-01, kullanıcı onayı: "önerdiğin her iki adımı da uygulayalım").
Sonradan eklenen tek kural, veri sürekliliği kuralıdır. Nedeni ve nasıl
seçildiği "Keşif sonuçları" bölümünde açıkça yazıyor.
**Kod:** `trading/backtest/weekly_momentum.py`.

Araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Neden

Likit-100 radarı kısa vadede (24 saat) çok yükselmiş coinleri alıyor. Bu,
sonraki günlerde geri veriliyor. Uyarıyı daha uzun tutmak, sepete göre farkı
büyütüyor (keşif penceresi, bütün uyarılar, maliyet sonrası; sepet: o an
24 saatlik hacmi ≥ 1M USD olan bütün çiftler, eşit ağırlık; veri sürekliliği
kuralıyla düzeltilmiş tanılama, karar vermez):

| Tutma süresi | 24 saat | 72 saat | 7 gün | 14 gün | 30 gün |
|---|---:|---:|---:|---:|---:|
| Sepete göre (puan) | −0.64 | −0.96 | −1.11 | −1.64 | −2.62 |

İki yarıda da negatif. Sohbette önce bildirilen −3.3 / −3.9 / −8.6 (7/14/30
gün) yanlıştı: Sepet, token değişimi yapan coinlerin 1000 katlık sahte
sıçramasını içeriyordu (aşağıda "Veri sürekliliği").

Kullanıcının sorusu: "Ufuk mu kısa?"

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
  - **İşlem fiyatı:** Karar pazartesi 00:00'da kapanan mumla verilir. Alım,
    satım ve haftalık değerleme **bir sonraki mumun açılışıyla** yapılır
    (inceleme üzerine; kararı veren kapanıştan işlem yapılamaz).
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
- **Veri sürekliliği (keşfin ilk koşusundan sonra eklendi):** Verinin
  sonradan devam ettiği **24 saatten uzun** bir boşluk süreklilik kırılmasıdır.
  - Kırılmanın üstünden getiri hesaplanmaz: o üye-haftası bilinmez sayılır.
  - Kırılmanın üstünden sıralama yapılmaz: coin o hafta sıralanamaz.
  - Kohortta kırılma, kohortun kuruluşundan sonra olduysa üye kohortun geri
    kalanında bilinmez sayılır.
  - Birkaç saatlik borsa bakımı kırılma değildir.
- **Tanılayıcı (karar vermez):** En düşük %20 ve en yüksek − en düşük
  farkı (momentum mu, ters dönüş mü?).

Toplam **6 varyant** (3 L × 2 H) keşifte incelenir. Bunlardan **en fazla
ikisi** ön-kayda alınır.

## Pencereler

| Pencere | Aralık | Ne zaman görülür |
|---|---|---|
| Keşif | 2020-10 → 2024-08 (≈ 200 hafta) | Şimdi; veri 2024-09-01 itibarıyla mühürlü |
| Doğrulama | 2024-09 → 2026-08 (≈ 104 hafta) | Ön-kayıttan sonra, **bir kez**. Ön-kayıt yapılmadı, kullanılmadı |

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
  - Getirisi hesaplanamayıp düşen haftalar ve onların fiyatlanamayan üyeleri
    de sayılır (inceleme üzerine). Hiç hafta yoksa da karar verilmez.

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

Koşu: `python -m trading.backtest.weekly_momentum discover`. Veri 2024-09-01
itibarıyla mühürlü (`SealError` kontrolü). 442 aday çift; tarihsel kimlik
katmanı stabil/sarılı olanları çıkarır. 204 pazartesi. Keşif yarıları 2022-09-01'de ayrılır.
α = 0.05, 4 haftalık blok bootstrap.

### Veri sürekliliği: ilk koşu geçersiz

İlk koşu saçma sonuç verdi: Sepetin haftalık ortalaması +%7.5, L1_H1
portföyününki +%35. Tek bir hafta (2021-01-18) sepeti +%1396 yaptı.

- **Neden:** COCOS 2021-01'de token değişimi yaptı. İşlem 4 gün durdu, aynı
  sembolle 1000 kat yüksek fiyattan yeniden başladı. Kod bunu +%138.825
  getiri saydı.
- **Benzerleri:** SUN 2021-06 (1000 kat düşük), DREP 2021-03, QUICK 2023-07,
  BNX 2023-02, VIDT 2022-10, STRAX 2024-03; LUNA 2022-05 (LUNA 2.0 aynı
  sembolü aldı, 110.000 kat).
- **Mevcut kimlik katmanı neden yakalamadı:** Yalnızca sembol değişikliklerini
  (MATIC → POL) tanıyor; aynı sembolle yapılan token değişimini tanımıyor.

**Kural verideki boşluk uzunluklarına göre seçildi, getirilere göre değil.**
Keşif verisindeki 2.448 boşluğun ölçümü:

| Boşluk | Sayı | Uzunluk | Boşluğun iki yanındaki fiyat oranı |
|---|---:|---|---|
| Kısa (borsa bakımı; 168–325 coin aynı anda) | 2.350 | en fazla 19 mum (≈ 5 saat) | en fazla 1.22 kat |
| Uzun (token değişimi, durdurma, çıkarılıp yeniden listelenme) | 98 | en az 384 mum (4 gün) | 1000 kata kadar |

Arada hiç boşluk yok. Eşik 24 saat (`DAY_BARS`, borsadan kalkma kuralında
da kullanılan sabit). Kural `continuity_breaks` / `continuous` içinde ve
testli (`test_a_token_swap_gap_is_a_break_not_a_return`; kural olmadan bu
test başarısız oluyor).

**Diğer çalışmalara etkisi:**

- **Sinyal kalitesi F1 (doğrulanmış KAÇIN etiketi): etkilenmiyor.** 72 saatlik
  çıkış, keşifteki 4 günlük duruşların içine düşer; sonuç "çözümlenemez"
  sayılır. Doğrulamadaki karar, geçen ve elenen uyarıların **net** sonuç
  farkına dayanır; bu hesapta sepet yok. 1000 kat yukarı bir sıçrama tek
  başına grup ortalamasını +15 puana çıkarırdı; görülen ortalamalar −0.12 ve
  −1.37. 1000 kat aşağı bir sıçrama ortalamayı en fazla 0.015 puan oynatır;
  farkın alt sınırı +0.73.
- **7/14/30 günlük tutma tanılaması (sohbette bildirilmişti): etkilenmiş.**
  Düzeltilmiş hali yukarıda, "Neden" bölümünde.

### Sonuçlar (düzeltilmiş koşu)

Haftalık %, maliyet sonrası. Fazla = net − eşit ağırlıklı ilk 100. Yarılar:
2020-10 → 2022-08 ve 2022-09 → 2024-08.

İnceleme (Codex) üzerine iki düzeltmeyle yeniden koşuldu: İşlem kararı veren
kapanıştan değil, sonraki açılıştan yapılıyor; düşen haftalar eksik veri
kapısına sayılıyor. Sayılar en fazla 0.01 puan değişti, kararlar aynı.

| Varyant | Hafta | Net | Fazla (%95 güven) | Fazla 1. yarı | Fazla 2. yarı | En yüksek − en düşük | Karar |
|---|---:|---:|---|---:|---:|---:|---|
| L1_H1 | 201 | +0.54 | −0.11 [−1.03, +1.09] | +0.43 | −0.61 | +0.51 | NO_EFFECT |
| L1_H4 | 201 | +0.70 | +0.06 [−0.44, +0.70] | +0.43 | −0.31 | +0.20 | NO_EFFECT |
| L2_H1 | 200 | +0.29 | −0.38 [−1.13, +0.50] | +0.24 | −0.97 | +0.08 | NO_EFFECT |
| L2_H4 | 200 | +0.58 | −0.09 [−0.57, +0.42] | +0.18 | −0.33 | −0.17 | NO_EFFECT |
| L4_H1 | 198 | +0.39 | −0.36 [−1.03, +0.34] | −0.04 | −0.66 | +0.23 | NO_EFFECT |
| L4_H4 | 198 | +0.52 | −0.23 [−0.77, +0.34] | −0.16 | −0.29 | −0.59 | NO_EFFECT |

- **Sepet:** Haftada ortalama +0.65 ile +0.75 arası (2021 boğa piyasası).
- **Veri:** Bilinmeyen üye-haftası %0.04–0.10. Portföy kurulamayan ve
  getirisi hesaplanamayan hafta 2–5 / 203 (ilk haftalarda geriye bakış
  verisi yok).
- **Net getiri pozitif, ama sepetin kendisi kadar.** Hiçbir varyant sepeti
  yenmedi.

**Okuma:**

- Likit ilk 100'de, son 1–4 haftanın kazananları sonraki 1–4 haftada sepeti
  **yenmiyor**.
- 2022-09 sonrası yarıda altı varyantın altısında da fazla getiri negatif.
  Yalnızca 2021 boğasında L1/L2 hafif pozitif.
- Ters dönüş de anlamlı değil: Hiçbir güven aralığının üst sınırı sıfırın
  altında değil.
- Literatürdeki etki (çoğu 2014–2018, küçük coinler dahil) bu evrende ve bu
  dönemde görülmüyor.

### Karar: ön-kayıt yok, doğrulama penceresi açılmadı

Protokol "en fazla iki varyant" diyor; sıfır da buna dahil. Hiçbir varyant
keşifte destek kazanmadı: En iyisinin (L1_H4) fazla getirisi +0.06, güven
aralığı sıfırın iki yanında ve 2. yarıda negatif. Böyle bir varyantı
doğrulamaya göndermek, ≈ 104 haftalık pencereyi gücü olmayan bir teste
harcamak olurdu (AGENTS.md §6: aday faktör tanıtımı kanıtla kazanılır).

- `REGISTERED_VARIANTS` boş kalıyor. `confirm` ve `build-confirm` komutları
  kayıt olmadan çalışmayı reddediyor (testli). Doğrulama için iş akışı
  eklenmedi.
- **Kullanıcının sorusuna cevap:** Ufku haftalara uzatmak, likit coinlerde tek
  başına bir avantaj yaratmıyor. 24 saatte çok yükselen coini almak, tutma
  süresi uzadıkça sepete göre daha çok kaybettiriyor (yukarıdaki tablo).
- **Canlıya etkisi:** Yok. Emir yetkisi yok.
