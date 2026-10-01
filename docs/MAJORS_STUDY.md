# Majör coinler — funding, alıcı-satıcı akışı ve OI sinyalleri

**Durum:** Keşif tamamlandı. **Aday seçme kuralını hiçbir sinyal sağlamadı.**
Ön-kayıt yapılmadı; doğrulama penceresi bu aile için mühürlü kaldı (ayrıntı:
"Keşif sonuçları").

Protokol herhangi bir sonuçtan **önce** yazıldı (2026-10-01, commit
`f0b0553`; kullanıcı onayı: "Tavsiyen mantıklı, bu doğrultuda ilerleyelim").
Sonuç görülmeden önce yapılan tek ek, evrendeki iki kimlik düzeltmesidir
("Evren" bölümündeki ek).
**Kod:** `trading/backtest/majors_signals.py`, `trading/data/majors_data.py`.

Araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Neden

Kullanıcının önerisi: Çok sayıda coin yerine majörlere (BTC, ETH, SOL, ilk 10,
bir meme coin) odaklanmak.

- **Dar evren tek başına edge yaratmadı:** Faz A yalnızca BTC'de NO-GO; taktik
  replay yalnızca BTC/ETH'de her yıl negatif (`docs/TACTICAL_REPLAY_REPORT.md`).
  Sorun sinyallerde.
- **Majörlerin avantajı:** Düşük maliyet, az manipülasyon, temiz veri ve hepsinde
  perp piyasası var. Bu, Likit-100'de kullanamadığımız **perp verisini**
  (funding, alıcı-satıcı akışı, açık pozisyon) sinyal olarak denemeyi mümkün
  kılıyor.
- **Bugünkü ilk 10 kullanılmaz:** SOL ve DOGE 2020 sonunda ilk 10'da değildi.
  Bugünün listesiyle 2020'den test etmek, sonradan bilinen yükselişi teste
  gömer (survivorship). Evren her ay, **o günkü** veriyle kurulur.
- **Funding carry yeniden test edilmez:** `carry_replay` zaten "30 günlük
  hacme göre ilk 20 perp" üzerinde, yani majörlerde test edildi (SIGNED_CARRY
  PASS_CANDIDATE, son 20 ay negatif; `docs/CARRY_REPLAY_REPORT.md`). Aynı testi
  daraltıp tekrarlamak yalnızca deneme sayısını artırır.

## Evren (her ay, o günkü veriyle)

Her ayın 1'i, 00:00 UTC'de kurulur ve ay boyunca sabit kalır.

- **Aday:** Likit-100 verisindeki USDT spot çiftleri. Tarihsel kimlik katmanı
  stabil, sarılı ve kaldıraçlı tokenları çıkarır.
- **Uygunluk (hepsi o ana kadarki veriyle):**
  - Son 30 günün en az 28 gününde spot verisi var.
  - **Olgunluk:** İlk spot mumu en az 90 gün önce, ya da verinin başladığı gün
    zaten işlem görüyor. Yeni listelenen ve hacmi şişen coinler (ARB, SUI, WLD
    gibi) ilk 90 gün majör sayılmaz.
  - Tek anlamlı eşlenen bir USDⓈ-M perp var ve ilk funding ödemesi en az 30
    gün önce yapılmış.
- **Sıralama:** Son 30 günün spot hacmi (kapanmış 15 dk mumlar).
- **Üyeler:**
  - BTC ve ETH her zaman (2020'den beri hep ilk 2; seçim geriye dönük değil).
  - Kalan uygun coinlerden hacimce ilk 10.
  - Bu 10'un içinde meme coin yoksa, hacimce en büyük meme coin eklenir.
- **Meme listesi (sabit, sonuçtan önce):** DOGE, SHIB, PEPE, FLOKI, BONK, WIF,
  BOME, MEME, PEOPLE, TURBO, NEIRO, 1000SATS, 1MBABYDOGE, DOGS, PNUT, ACT,
  TRUMP, PENGU.
- **12 üyeden az:** O ay için bir eksiklik sayılır (aşağıda).

**Ek (2026-10-01, veri denetiminden sonra, hiçbir sinyal sonucu görülmeden):**
İlk evren listesinde iki kimlik sorunu çıktı.

- **LUNAUSDT, Ekim 2022:** Bu sembol o tarihte token değişimi sonrası LUNA
  2.0'dı. Eski LUNA'nın perp'i Mayıs 2022'de kapanmıştı.
- **FTTUSDT, Aralık 2023:** Eylül 2023'te uzun bir işlem durmasından sonra
  yeniden başlamıştı.

Bu yüzden iki kural netleştirildi:

- **Olgunluk:** 24 saatten uzun bir süreklilik kırılmasından sonra çift
  yeniden listelenmiş sayılır. 90 gün o andan itibaren hesaplanır.
- **Perp "var" demek:** Perp en az 30 gün önce funding ödemeye başlamış
  olmalı ve ay başından önceki 24 saatte de ödemiş olmalı. Kapanmış bir perp
  sayılmaz.

İki kural da testli. Etkisi: Keşif evreninde yalnızca bu iki coin-ay
değişti.

## Karar anı ve sonuç

- **Karar:** Her gün 00:00 UTC, her üye coin için. Özellikler yalnızca o ana
  kadar kapanmış veriyle hesaplanır.
- **Giriş:** Karardan sonraki ilk 15 dk spot mumunun açılışı.
- **Çıkış:** H sonra kapanan mumun kapanışı. **H ∈ {24 saat, 72 saat}.**
- **Net:** Brüt − %0.20 gidiş-dönüş (stres: %0.35).
- **Fazla getiri:** Net − aynı aralıkta eşit ağırlıklı majör sepetinin brüt
  getirisi.
- **Borsadan kalkan coin:** Son işlem fiyatından çıkar.
- **Veri boşluğu:** Sonradan devam eden boşluk, indirme sınırı ya da 24
  saatten uzun süreklilik kırılması (token değişimi; `docs/WEEKLY_MOMENTUM.md`)
  sonucu bilinmez yapar; sonuç sayılır, tahmin edilmez.

## Sinyaller (keşifte incelenecek, eşikler sonuçtan önce sabit)

Eşikler veriden öğrenilmez; aşağıdaki sabit değerler kullanılır.

### F — Funding seviyesi

- **Özellik:** Son 72 saatte ödenen funding oranlarının toplamı × 8/72; yani
  8 saatlik eşdeğer ortalama oran. 4 ve 8 saatlik aralıkların ikisinde de
  doğru çalışır.
- **Geçerlilik:** Ödemeler arasında, pencere başında ve sonunda 8 saat 1
  dakikadan uzun boşluk olmamalı. Aksi halde bilinmez.
- **Gruplar:**
  - `F_NEGATIVE`: < 0 (short'lar ödüyor).
  - `F_BASE`: 0 ile %0.03 arası.
  - `F_HIGH`: ≥ %0.03 (taban oranın 3 katı; long'lar kalabalık).
- **Önceden beklenen yön:** F_HIGH sonrası getiri daha düşük, F_NEGATIVE
  sonrası daha yüksek (kalabalık tarafın tasfiyesi). Kanıt karışık; emin
  değiliz.

### T — Alıcı-satıcı akışı (perp, agresif alıcı payı)

- **Özellik:** Son 24 kapanmış 1 saatlik perp mumunda: agresif alıcı hacmi /
  toplam hacim (taker buy quote / quote volume).
- **Normalleştirme:** Aynı coinin önceki 30 günündeki, aynı saatteki 24
  saatlik paylarına göre z-skoru. En az 20 geçerli gün gerekir. 24 mumun
  hepsi olmalı.
- **Gruplar:**
  - `T_BUYING`: z ≥ +1.
  - `T_SELLING`: z ≤ −1.
  - `T_NEUTRAL`: arası.
- **Önceden beklenen yön:** Belirsiz. Agresif alım devam da edebilir, tükeniş
  de olabilir.

### O — Açık pozisyon (OI) ve fiyat

- **Özellik:** Son 24 saatte perp OI'sinin (kontrat adedi, `sum_open_interest`;
  değer değil, çünkü değer fiyatla mekanik olarak değişir) ve spot fiyatın
  yönü.
- **OI anlık görüntüsü:** Karardan en az 5 dakika önce oluşturulmuş en son
  kayıt. Bir saatten eski ise bilinmez.
- **Gruplar:**
  - `O_LONGS_BUILDING`: fiyat ↑, OI ↑.
  - `O_SHORT_COVERING`: fiyat ↑, OI ↓.
  - `O_SHORTS_BUILDING`: fiyat ↓, OI ↑.
  - `O_LONG_LIQUIDATION`: fiyat ↓, OI ↓.
- **Kapsam:** Binance OI verisi çoğu coin için 2021-12'de başlıyor. Bu aile
  2022-01-01'den itibaren değerlendirilir (BTC dahil, örneklem tutarlı olsun
  diye).
- **Önceden beklenen yön:** Belirsiz. LONG_LIQUIDATION sonrası tepki yükselişi
  bir uygulayıcı inancı; akademik kanıtı zayıf.

Toplam: 3 aile, 10 grup, 2 ufuk.

## Pencereler

| Pencere | Karar günleri | Ne zaman görülür |
|---|---|---|
| Keşif | 2020-11-01 → 2024-08 (O ailesi 2022-01'den) | Şimdi; veri 2024-09-01 itibarıyla mühürlü (`SealError`) |
| Doğrulama | 2024-09-01 → 2026-08 | Ön-kayıttan sonra, **bir kez**, GitHub Actions'ta |

- **Keşif yarıları:** 2022-09-01'de ayrılır.
- **Doğrulama yarıları:** 2025-09-01'de ayrılır.

**Bilinen kirlenme:**

- Majörlerin 2024–26 genel fiyat seyri biliniyor.
- Sinyal kalitesi F1 doğrulaması 2024–26 Likit-100 uyarılarını gördü.
- Bu üç sinyal ailesinin doğrulama penceresindeki sonucu hiç görülmedi.

## Keşif çıktısı

Her aile × grup × ufuk için, keşif penceresinde:

- **Sayı:** Coin-gün sayısı.
- **Getiri:** Ortalama net ve fazla getiri; %95 güven aralığı.
- **Kararlılık:** İki yarı.
- **Ayırma:** Grup − aynı ailenin geri kalanı (fazla getiri farkı).

Güven aralığı: **7 günlük takvim blokları**yla dairesel hareketli blok
bootstrap (4000 örnek). Aynı günün coinleri ve üst üste binen 72 saatlik
sonuçlar birlikte örneklenir.

Kıyas için **her zaman long** satırı da gösterilir: bütün coin-günler.

## Ön-kayda aday seçme kuralı (sonuçtan önce sabit)

Bir (aile, grup, ufuk) üçlüsü, keşifte şunlardan birini sağlarsa adaydır.

**LONG adayı:**

1. ≥ 300 coin-gün;
2. fazla getirinin alt sınırı > 0;
3. fazla getiri iki yarıda da > 0;
4. ortalama net > 0.

**KAÇIN adayı:**

1. ≥ 300 coin-gün;
2. grup − geri kalan fazla getiri farkının üst sınırı < 0;
3. fark iki yarıda da < 0.

**Seçim:**

- En fazla **2** aday ön-kayda alınır, her aileden en fazla bir tane.
- Sıralama, ilgili sınırın sıfırdan uzaklığına göre yapılır: LONG için fazla
  getirinin alt sınırı, KAÇIN için farkın üst sınırı.
- **Aday yoksa ön-kayıt yapılmaz** ve doğrulama penceresi açılmaz.

## Doğrulama karar kuralları (ön-kayıtlı, sonuçtan önce)

`k` ön-kayıtlı aday sayısıdır. α = 0.05 / k. Aynı blok bootstrap kullanılır.

**LONG adayı:**

- **PASS:** Hepsi sağlanmalı:
  - ≥ 200 coin-gün;
  - net alt sınır > 0;
  - fazla getiri alt sınırı > 0;
  - stres maliyetiyle ortalama net > 0;
  - iki yarıda net ve fazla getiri > 0.
- **SEPETİ_YENER:** PASS değil; ama fazla getirinin alt sınırı > 0 ve iki
  yarıda > 0.
- **TERS:** Fazla getirinin üst sınırı < 0.
- **NO_EFFECT:** Diğer durumlar.

**KAÇIN adayı:**

- **KAYBI_AZALTIR:** Grup − geri kalan farkının üst sınırı < 0 ve iki yarıda
  < 0.
- **NO_EFFECT:** Diğer durumlar.

**INCOMPLETE_DATA (karar yok):** Şunlardan biri aşılırsa.

- Ailenin kapsamındaki coin-günlerde bilinmeyen özellik payı > %5.
- Bilinmeyen sonuç payı > %5.
- 12'den az üyeli ay payı > %5.
- Hiç coin-gün yoksa.

## Canlıya etkisi (yalnızca doğrulamadan sonra)

- **PASS / SEPETİ_YENER:**
  - Majörler için ayrı bir "majör sinyal" bildirimi eklenir.
  - Bildirim kanıt etiketiyle gelir; SEPETİ_YENER'de "kâr kanıtı yok" yazar.
  - Emir yetkisi yoktur.
- **KAYBI_AZALTIR:** O coin için gelen taktik ve Likit-100 uyarılarına "KAÇIN"
  benzeri bir etiket eklenir (F1'deki gibi).
- **Diğerleri:** Değişiklik yok.

**Güç (dürüst not):**

- Majörler birbirine çok bağlı hareket eder. Aynı günün 12 coini pratikte
  birkaç bağımsız gözlem eder.
- 2 yıllık doğrulamada yalnızca günde ≈ %0.2–0.3'ün üstündeki etkiler anlamlı
  çıkabilir.
- Gerçek ama küçük bir etki NO_EFFECT görünebilir.

## Keşif sonuçları

**Koşu:** `python -m trading.backtest.majors_signals discover`.

- **Veri:** 2024-09-01 itibarıyla mühürlü.
- **Kapsam:** 46 ay, 16.981 coin-gün, 67 farklı coin.
- **Eksik veri:** Bilinmeyen özellik payı F %0.1, T %0.3, O %0.9. Eksik
  sonuç %0.1–0.3. Her ay 12–13 üye var. Eksik veri kapısı hiçbir grupta
  tetiklenmedi.
- **Yarılar:** 2022-09-01'de ayrılır. α = 0.05, 7 günlük blok bootstrap.

Tablolar maliyet sonrası % gösterir. **Fazla** = net − majör sepeti. "Hep
long" satırının fazla getirisi tanım gereği −0.20'dir (sepetin kendisi eksi
maliyet). Bir grubun sepeti yenmesi için maliyeti de aşması gerekir.

### 72 saat

| Grup | Coin-gün | Net | Fazla (%95 güven) | Grup − diğerleri (%95 güven) | Fark 1. / 2. yarı |
|---|---:|---:|---|---|---|
| Hep long | 16.936 | +0.34 | −0.20 | — | — |
| F_NEGATIVE | 3.643 | −0.08 | −0.37 [−0.62, −0.13] | −0.21 [−0.54, +0.10] | −0.27 / −0.16 |
| F_BASE | 10.852 | +0.11 | −0.03 [−0.16, +0.17] | +0.49 [+0.11, +1.02] | +0.77 / +0.19 |
| F_HIGH | 2.441 | +2.01 | −0.72 [−1.51, −0.17] | −0.61 [−1.54, +0.03] | −0.79 / −0.21 |
| T_SELLING | 3.248 | −0.18 | −0.26 [−0.53, +0.02] | −0.07 [−0.40, +0.27] | −0.25 / +0.10 |
| T_NEUTRAL | 10.786 | +0.39 | −0.20 [−0.31, −0.09] | +0.00 [−0.30, +0.31] | +0.14 / −0.13 |
| T_BUYING | 2.877 | +0.74 | −0.14 [−0.59, +0.30] | +0.07 [−0.48, +0.61] | +0.04 / +0.10 |
| O_LONGS_BUILDING | 3.028 | −0.28 | −0.22 [−0.39, −0.05] | −0.02 [−0.25, +0.21] | +0.24 / −0.11 |
| O_SHORT_COVERING | 2.592 | −0.16 | −0.17 [−0.34, +0.01] | +0.04 [−0.17, +0.26] | −0.09 / +0.08 |
| O_SHORTS_BUILDING | 2.582 | −0.97 | −0.28 [−0.47, −0.10] | −0.10 [−0.35, +0.14] | −0.19 / −0.07 |
| O_LONG_LIQUIDATION | 3.388 | −0.30 | −0.16 [−0.30, −0.00] | +0.07 [−0.13, +0.28] | +0.03 / +0.08 |

### 24 saat

| Grup | Coin-gün | Net | Fazla (%95 güven) | Grup − diğerleri (%95 güven) | Fark 1. / 2. yarı |
|---|---:|---:|---|---|---|
| Hep long | 16.962 | −0.03 | −0.20 | — | — |
| F_NEGATIVE | 3.657 | −0.15 | −0.30 [−0.41, −0.19] | −0.12 [−0.26, +0.01] | −0.18 / −0.06 |
| F_BASE | 10.864 | −0.09 | −0.13 [−0.18, −0.06] | +0.20 [+0.05, +0.38] | +0.31 / +0.09 |
| F_HIGH | 2.441 | +0.45 | −0.38 [−0.62, −0.19] | −0.20 [−0.49, +0.02] | −0.24 / −0.14 |
| T_SELLING | 3.251 | −0.24 | −0.32 [−0.46, −0.19] | −0.15 [−0.32, +0.02] | −0.30 / +0.01 |
| T_NEUTRAL | 10.809 | +0.02 | −0.18 [−0.24, −0.12] | +0.05 [−0.10, +0.21] | +0.08 / +0.03 |
| T_BUYING | 2.877 | +0.01 | −0.14 [−0.30, +0.04] | +0.08 [−0.12, +0.28] | +0.20 / −0.05 |
| O_LONGS_BUILDING | 3.032 | −0.38 | −0.18 [−0.26, −0.09] | +0.03 [−0.09, +0.15] | +0.05 / +0.03 |
| O_SHORT_COVERING | 2.598 | −0.36 | −0.17 [−0.27, −0.05] | +0.04 [−0.09, +0.19] | +0.02 / +0.05 |
| O_SHORTS_BUILDING | 2.592 | −0.51 | −0.29 [−0.45, −0.16] | −0.11 [−0.32, +0.05] | −0.27 / −0.06 |
| O_LONG_LIQUIDATION | 3.394 | +0.03 | −0.18 [−0.26, −0.09] | +0.03 [−0.09, +0.16] | +0.19 / −0.02 |

### Okuma

- **LONG adayı yok.** Hiçbir grup maliyet sonrası majör sepetini yenmiyor.
  Fazla getirisi en iyi olan grup bile sıfırın altında ya da sıfırı içeriyor.
- **KAÇIN adayı yok.** En yakın olanlar sınırı kıl payı geçemedi:
  - F_HIGH 72 saat: −0.61, üst sınır +0.03.
  - F_NEGATIVE 24 saat: −0.12, üst sınır +0.01.
  - T_SELLING 24 saat: 2. yarıda fark pozitif.
- **OI–fiyat rejimleri bir şey söylemiyor.** Dört rejimin farkları sıfırın
  iki yanında.
- **Alıcı-satıcı akışı bir şey söylemiyor.** Agresif alım ya da satım
  sonrası getiri, sepetten ayırt edilemiyor.
- **Yüksek funding sonrası net getiri yüksek ama sepetten düşük.** Net
  getiri +2.0, ama sepete göre −0.72. Yüksek funding boğa günlerinde olur;
  o günlerde bütün majörler yükselir, kalabalık coin ise daha az yükselir.
- **Hep long (majör sepeti)** 72 saatte +0.34. Bu, dönemin genel yükselişi;
  güven aralığı sıfırı içeriyor.

**Kayda değer ama test edilmemiş bir gözlem:**

- Her iki funding ucu da (negatif ve yüksek) sepetten kötü. Taban funding
  (F_BASE), diğerlerinden iki ufukta ve iki yarıda da anlamlı biçimde iyi:
  - 72 saat: +0.49 [+0.11, +1.02].
  - 24 saat: +0.20 [+0.05, +0.38].
- Ama bu bir "uç funding'de KAÇIN" kuralına karşılık gelir ve protokolde
  önceden tanımlanmış bir aday değildi; sonuç görüldükten sonra fark edildi.
- 20 karşılaştırma yapıldı. Bonferroni düzeltmesiyle (α = 0.0025) 24 saatlik
  alt sınırın (+0.05) dayanması beklenmez.
- **Bu yüzden ön-kayda alınmadı.** İleride ayrı bir protokolle, yeni veride
  test edilebilecek bir hipotez olarak not edildi.

### Karar: ön-kayıt yok, doğrulama penceresi açılmadı

- Protokoldeki kural önceden yazılmıştı: "Aday yoksa ön-kayıt yapılmaz ve
  doğrulama penceresi açılmaz." Kural uygulandı.
- `REGISTERED` boş kalıyor. `confirm` ve `build-confirm` kayıt olmadan
  çalışmayı reddediyor (testli). Doğrulama için iş akışı eklenmedi.
- **Canlıya etkisi:** Yok. Emir yetkisi yok.

**Kullanıcının sorusuna cevap:**

- Majörlere daralmak veriyi temizledi; ama funding, agresif akış ve OI–fiyat
  sinyallerinin hiçbiri majör sepetini maliyet sonrası yenmedi.
- Bu, önceki BTC/ETH testleriyle (Faz A, taktik replay) tutarlı. Sinyalsiz
  bir majör sepeti tutmak, test edilen bütün sinyalli versiyonlardan daha iyi
  ya da onlara eşit sonuç verdi.

**Not (2026-10-01, atlas incelemesi):**

- Evren kuralı, veri setinin ilk gününde işlem gören her coini 90 günlük geçmişi tamamlamış sayıyordu. Keşif verisi 2020-10-01'de başladığı için 2020-11 ve 2020-12 evrenlerine 90 günden genç coinler girdi:
  - 2020-11: DOT, UNI, YFI, YFII;
  - 2020-12: UNI.
- Bu, 46 keşif ayının 2'sinde, toplam yaklaşık 600 üye-aydan 5'inde görülür. Keşif sonucu zaten aday çıkarmamıştı; kayıtlı sonuçlar değiştirilmedi.
- Doğrulama penceresi etkilenmez. Doğrulama verisi 2024-06'da başlıyor ve ilk evren 2024-09'da kuruluyor. Veri setinin başında görülen her coin o tarihte en az 92 günlüktür.
- Atlas, coin yaşını veri setleri arasında taşıyarak bu durumu düzeltir (`monthly_universe(..., history_start=...)`, `docs/ATLAS.md`).
