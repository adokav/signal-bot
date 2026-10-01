# Majör coinler — funding, alıcı-satıcı akışı ve OI sinyalleri

**Durum:** Protokol. Bu belge herhangi bir sonuçtan **önce** yazıldı
(2026-10-01, kullanıcı onayı: "Tavsiyen mantıklı, bu doğrultuda
ilerleyelim").
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

Henüz yok.
