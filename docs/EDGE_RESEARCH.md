# Edge araştırması: sağlam, sürdürülebilir ve kârlı bir avantaj nerede olabilir?

**Tarih:** 2026-10-01
**Durum:** Literatür ve uygulanabilirlik taraması. Bu belge hiçbir stratejiyi
onaylamaz ve emir yetkisi vermez (`can_authorize_trade = false`, AGENTS.md §4).
Buradaki her aday, test edilmeden önce `research/trials/registry.jsonl`
dosyasına ön-kayıtla girer.

## Kısa cevap

"Mutlaka kârlı bir strateji vardır" sorusunun cevabı **evet**. Ama kriptoda
kalıcı kâr üç kaynaktan birinden gelir ve her birinin bir bedeli vardır:

1. **Risk primi:** Kâr, başkalarının taşımak istemediği riski taşımanın
   karşılığıdır. Örnekler: BTC betası, funding/basis carry, opsiyon satmak.
   Bu kâr gerçektir ama "bedava" değildir; çöküşlerde toplu olarak geri
   alınır.
2. **Yapısal avantaj:** Kâr, hızın, altyapının veya sermayenin karşılığıdır.
   Örnekler: piyasa yapıcılık, borsalar arası arbitraj, MEV. Perakende bir
   REST API bağlantısıyla bu yarışı kazanmak pratikte mümkün değil.
3. **Davranışsal anomali:** Kâr, başka katılımcıların sistematik hatasından
   gelir. Örnekler: piyango coin'lere aşırı fiyat biçmek, yeni listelemeleri
   kovalamak, zorunlu satışların fiyatı aşırı düşürmesi. Bizim
   konumumuzdan erişilebilen sınıf budur.
   - Literatüre göre bu etkiler hızla zayıflar, likit olmayan coin'lerde
     yoğunlaşır ve çoğu açığa satış gerektirir.

Bu yüzden bizim için en güvenilir avantaj, sistematik kaybedenlerin
yaptığını **yapmamaktır**. İkinci sırada, zorunlu satış gibi **yapısal bir
nedeni** olan dar etkiler gelir. "Anlatısı güzel" sinyaller sonra gelir.

## Bu belgenin doğrulama düzeyi

- Yayıncı sitelerinin çoğu (ScienceDirect, Springer, arXiv, SSRN, Quantpedia,
  CoinDesk) bu ortamın ağ politikası tarafından engellendi. Aşağıdaki
  sayıların çoğu **arama sonucu özetlerinden** alındı; makalelerin tam
  metni okunmadı.
- **[özet]** ile işaretli sayılar ikincil kaynaktır. Bir aday ön-kayda
  girmeden önce kaynağın tam metni okunmalıdır.
- **[bellek]** ile işaretli bilgiler bu ortamda doğrulanmadı.
- **[doğrulandı]** ile işaretli bilgiler bu ortamda doğrudan kontrol edildi.
- Uygulayıcı raporları (borsa blogları, analiz firmaları) akademik değildir.
  Survivorship ve yöntem bilgisi çoğu zaman belirsizdir.

## Çıta: "edge" ne demek

Bir aday ancak şu koşulları birlikte sağlarsa edge sayılır:

1. **Maliyetten sonra** pozitif. MEXC spot'ta maker %0, taker %0.05 [özet].
   Replay'lerimiz taraf başına 7.5 bp komisyon + 5 bp kayma kullandı. MEXC'de
   komisyon daha düşük ama derinlik daha az, kayma daha yüksek olabilir.
2. **Bir karşılaştırmayı geçer.** Yönlü stratejiler için eşit ağırlıklı
   likit sepet veya BTC al-tut, delta-nötr stratejiler için dolar risksiz
   getirisi.
3. Çoklu test düzeltmesinden ve iki kronolojik yarıdan **geçer**.
4. Mümkünse hipotezi doğuran veriden **bağımsız bir dönemde** de geçer.
5. Kâğıt üzerinde **ileri kayıtta** da görülür.

Bilinen taban oran: Hisse anomalileri yayımlandıktan sonra ortalama
yaklaşık %58 zayıflıyor (McLean & Pontiff 2016) [bellek]. Kripto için
yapılan 2024–2026 çalışmaları da benzer bir tablo çiziyor:

- Örneklem dışında boyut etkisi kayboluyor [özet].
- Anormal getiriler çoğunlukla boğa piyasalarında oluşuyor ve zamanla
  sönüyor [özet].
- Büyük coin'lerde momentum maliyetli; alfası çoğunlukla açık
  pozisyonlardan geliyor [özet].
- Teorik anomali kazancının büyük kısmı örneklem dışında
  gerçekleşmiyor. En büyük kaybı açığa satış kısıtı yaratıyor [özet,
  hisse].

## Şimdiye kadar test ettiklerimiz ve literatürle uyumu

| Deneme | Sonuç | Literatürle uyum |
|---|---|---|
| BTC TSMOM (4 varyant) | NO-GO. Saf TSMOM'da Sharpe 0.62 (al-tut 0.59), düşüş −46% (al-tut −77%) | Uyumlu. Trend takibi BTC'de alfa değil, düşüşü azaltan bir risk katmanı. |
| Taktik long kurulumları | NEGATIVE | Uyumlu. Teknik kurulumlar maliyetten sonra boğa dışı dönemlerde sönüyor. |
| Likit-100 long radarı | NEGATIVE. En çok yükselenler 12–72 saatte sepetin gerisinde kaldı. | Kısmen. MAX ("piyango") literatürü karışık: bir kısmı negatif MAX etkisi, bir kısmı "MAX momentum" buluyor [özet]. Bizim 2020–2026 likit verimiz negatif tarafı destekliyor. |
| Funding carry (SIGNED) | PASS_CANDIDATE ama canlıya yetersiz. Son 20 ay negatif. | Uyumlu. Funding yıllık ≈ %11 (2024) → %4.9 (2025) → %2.2 (2026, Ağustos'a kadar) [özet]. Carry, çöküş riskiyle bağlantılı (BIS WP 1087) [özet]. |
| Kısa vadeli geri dönüş (2017–2020) | **Koşuyor** | Önceden yazılan beklenti aşağıda. |

**Geri dönüş testi için beklenti (sonuç görülmeden yazıldı):**

- Zaremba ve diğ. (2021), 3.600'den fazla coin'de günlük geri dönüş buldu.
  Ama etki likit olmayan coin'lerden geliyor; en büyük ve en likit coin'lerde
  günlük **momentum** var [özet].
- Kozlowski, Puleo ve Zhou da etkinin en güçlü olduğu yerin küçük ve likit
  olmayan coin'ler olduğunu buldu [özet].
- Bizim testimiz likit ilk 100 içinde. Literatür bu yüzden LOSERS@1g için
  **zayıf bir beklenti** veriyor. NO_EDGE sonucu sürpriz olmaz.

## Aday kataloğu

Her aday için şu sorular soruldu:

- **Kim, neden öder?** (mekanizma)
- Kanıt ne kadar güçlü?
- Zamanla zayıflıyor mu?
- Bizim kısıtlarımızla uyumlu mu? Kısıtlar: long-only spot, MEXC'de
  uygulama, REST gecikmesi, emir yetkisi yok.
- Veri anlık (point-in-time) olarak elde edilebilir mi?

### A. Risk primleri (gerçek, ama "edge" değil)

**A1. BTC/ETH betası + oynaklık hedefleme / trend filtresi**
- **Mekanizma:** Risk taşıma primi.
  - Oynaklık hedefleme, oynaklık yükseldiğinde beklenen getirinin aynı
    oranda artmamasından yararlanır (Moreira & Muir) [bellek].
  - Bir BTC çalışmasında risk yönetimli strateji Sharpe'ı 0.72'den 1.21'e
    çıkardı [özet].
- **Bizdeki kanıt:** Saf TSMOM, al-tut'a benzer Sharpe'ı daha küçük
  düşüşle verdi.
- **Değerlendirme:** Sermayenin bir kısmıyla kripto riski taşımak
  isteniyorsa en dürüst yol bu. Ama bu bir alfa değil, bir **tercih**.
  Geçmişteki beta getirisi geleceği garanti etmez.
- **Öncelik:** Strateji değil, portföy kararı. Ayrı bir deneme gerekmez.

**A2. Funding / basis carry**
- **Mekanizma:** Kaldıraçlı long talebi yüksek, arbitraj sermayesi kıt
  (BIS WP 1087) [özet].
- **Kanıt:** Prim 2024'ten beri daralıyor [özet]. 10 Ekim 2025'te 24 saatte
  yaklaşık 19 milyar dolarlık pozisyon tasfiye edildi. Auto-deleveraging
  (ADL), hedge'in kârlı bacağını da zorla kapattı [özet].
- **Yeni gelişme:** Binance hisse perp'lerinde funding Mayıs–Ağustos 2026'da
  yıllık ortalama ≈ %17.5 [özet]. Ama hedge bacağı (hissenin kendisi) kripto
  borsasında yok. Dayanak piyasa kapalıyken (hafta sonu) fiyat boşluğu
  riski var.
- **Öncelik:** Kripto carry için maker emirli, histerezisli yeni bir deneme
  ancak ileri kayıt funding'in risksiz getirinin üstüne çıktığını
  gösterirse. Hisse perp carry'si yalnızca izlenir, test edilmez.

**A3. Opsiyon satmak (oynaklık risk primi)**
- **Mekanizma:** BTC'nin ima edilen oynaklığı sistematik olarak
  gerçekleşen oynaklığın üstünde. Örnek: 7 günlük gerçekleşen ≈ %71,
  ima edilen ≈ %85–94 [özet].
- **Sorun:** Prim, sıçrama (crash) riskinin bedeli. MEXC'de opsiyon yok.
- **Öncelik:** Önerilmez. Spec'in "WHAT COULD BLOW UP" ilkesiyle
  çelişiyor.

### B. Davranışsal anomaliler

**B1. Kısa vadeli geri dönüş (en çok düşenleri almak)**
- Test ediliyor. Beklenti zayıf (yukarıda).

**B2. Yeni listeleme sonrası düşüş (kayıptan kaçınma)**
- **Mekanizma:** Listeleme heyecanı, piyango talebi ve erken yatırımcıların
  satışı.
- **Kanıt:** Uygulayıcı raporları çok güçlü ve aynı yönde:
  - Binance'te ilk işlem gününde alanların ortalama getirisi −%71.7
    (Şubat 2025'e kadar) [özet].
  - 2025'te yeni listelemelerin yalnızca %12'si kârda; medyan getiri
    −%82 [özet].
  - 2024'te Binance listelemeleri piyasanın −%31.2 gerisinde kaldı [özet].
  - Bunlar akademik değil; yöntem ve survivorship bilgisi belirsiz.
- **Bizim için:** Long-only bir sistem bu etkiden kâr edemez (açığa satış
  gerekir), ama **kaçınarak** fayda sağlar.
  - Canlı sistemdeki MEXC yeni listeleme radarı bugün adaylara
    `HOT/BUILDING/WATCH` etiketi veriyor (`acce_unified/listings.py`).
  - Bu etiketler hiç tarihsel olarak test edilmedi. MEXC, Binance'ten daha
    erken ve daha riskli coin'leri listeler; taban oranın orada daha kötü
    olması beklenir.
- **Öncelik: YÜKSEK.** Ucuz, kanıtı güçlü ve canlı sistemi doğrudan
  ilgilendiriyor. Ön-kayıt taslağı aşağıda.

**B3. Token unlock öncesi düşüş (kayıptan kaçınma)**
- **Kanıt:** 16.000'den fazla olay incelendi [özet].
  - Olayların yaklaşık %90'ı satış baskısı üretti.
  - Unlock'tan önceki bir ayda −%14.7 sürüklenme; sonraki bir ayda benzer
    coin'lere göre medyan −%4.85.
  - Etki erken aşamadaki, dolaşımdaki arzı düşük coin'lerde yoğunlaşıyor.
    Likit büyük coin'lerde anlamlı değil.
  - En kötüsü ekip unlock'ları (−%25).
- **Sorun:** Anlık (point-in-time) unlock takvimi için ücretli bir sağlayıcı
  gerekiyor ve takvimler sonradan revize ediliyor.
- **Öncelik:** Orta. Risk filtresi olarak, veri kaynağı netleşince.

**B4. Piyango (MAX) coin'lerinden kaçınma**
- **Kanıt:** Literatür karışık [özet]. Bizim Likit-100 verimiz negatif
  tarafı destekliyor.
- **Bizim için:** Long-only uygulama "en çok yükselen dilimi sepetten
  çıkarmak" olur. Çıkarılan dilim %10 ise sepete katkısı küçüktür.
- **Öncelik:** Düşük. Ayrı bir strateji olarak değil, mevcut REJECT
  kararlarının gerekçesi olarak.

**B5. Haftalık kesitsel momentum (1–4 hafta)**
- **Kanıt:** Liu, Tsyvinski & Wu'da anlamlı [özet], ama:
  - Ağır çöküşleri var; bir çalışmada momentum −%255 çöktü [özet].
  - 2021 sonrasında zayıf [özet].
  - Büyük coin'lerde alfası açık pozisyonlardan geliyor [özet].
- **Öncelik:** Düşük. Mevcut veriyle ucuz ama beklenti zayıf.

### C. Takvim ve mikroyapı

**C1. BTC saat etkisi**
- **Kanıt:** Getiri 22:00–23:00 UTC saatlerinde yoğunlaşıyor. Önerilen
  strateji yıllık %40.6 ve −%22.7 düşüş raporluyor [özet].
- **Sorunlar:**
  - 24 saatten en iyisini seçmek tek başına bir çoklu test.
  - Saat etiketi belirsiz: mumun açılışı mı, kapanışı mı? Özetler
    çelişiyor.
  - Taker maliyetiyle (günde 2 işlem) yıllık maliyet yükü getiriyi
    büyük olasılıkla siler.
- **Değeri:** Yayın sonrası dönem doğal bir örneklem dışı test sunar.
- **Öncelik:** Orta-düşük. Ucuz, ama önce kaynağın tam metni okunmalı.

**C2. Saat başı emir dengesizliği**
- **Kanıt:** Kim & Hansen, Temmuz 2026 [özet].
  - Kripto vadelilerinde saat ve çeyrek saat başında algoritmik işlem
    patlamaları var.
  - Saat başındaki emir dengesizliği 4–12 saatlik getiriyi tahmin
    ediyor.
- **Değeri:** Yeni bir bulgu, yani henüz az arbitraj edilmiş olabilir.
- **Sorunlar:** Altı kontratla sınırlı. Maliyet sonrası kârlılık özette
  yok. İşlem düzeyinde veri (aggTrades) gerekiyor; veri büyük.
- **Öncelik:** İzle. Tam metin okunmadan test tasarlanmaz.

**C3. Pazartesi Asya açılışı, gün içi momentum, eşleştirilmiş işlem**
- **Kanıt:** Yüksek frekanslı etkiler [özet]. 5 dakikalık eşleştirilmiş
  işlemde maliyet getiriyi tamamen siliyor; günlükte getiri marjinal
  [özet].
- **Öncelik:** Düşük.

### D. Türev piyasası konumlanması

**D1. Zorunlu satış (deleveraging) sonrası toparlanma**
- **Mekanizma:** Yapısal.
  - Tasfiye edilen pozisyonlar fiyata bakmadan satılır. Likidite sağlayan
    taraf bir prim kazanır.
  - Bu, hisse piyasalarındaki "zorunlu satış" literatürünün kripto
    karşılığı [bellek].
  - Anlatıya değil, zorunlu bir akışa dayanıyor. Bu yüzden diğer
    adaylardan daha sağlam bir gerekçesi var.
- **Kanıt:** Çoğunlukla uygulayıcı kaynaklı.
  - Uç negatif funding, yerel diplerle çakışmış [özet].
  - Akademik tarafta BitMEX funding'i ile sonraki 8 saatlik BTC getirisi
    arasında hafif negatif ilişki [özet].
  - Bu yüzden kanıt **zayıf**.
- **Veri [doğrulandı]:** `data.binance.vision`'da USDT-M için 5 dakikalık
  `metrics` var: açık pozisyon, long/short oranları, taker al/sat oranı.
  BTCUSDT için 2020-09-01'den itibaren. Funding geçmişi ve spot 15 dakikalık
  mumlar zaten kullanılıyor. `liquidationSnapshot` listesi boş, yani
  tasfiye verisi yok.
- **Riskler:**
  - Tasfiye dalgaları piyasa genelinde aynı günlere toplanır. Bağımsız gün
    sayısı küçük (onlarca), sonuç INSUFFICIENT çıkabilir.
  - Çöküş anında kayma çok büyük. 10 Ekim 2025'te bazı coin'ler sıfıra
    yakın fitil attı [özet].
- **Öncelik: YÜKSEK.** Ön-kayıt taslağı aşağıda.

### E. Bizim için kapalı olanlar

| Aday | Neden kapalı |
|---|---|
| Piyasa yapıcılık | MEXC maker ücreti %0 cazip, ama kâr gecikme ve envanter yönetiminden gelir. Ters seçim (adverse selection) REST hızındaki bir botu ezer. |
| Borsalar arası arbitraj | Büyük coin'lerde kapandı [bellek: Makarov & Schoar 2020]. Küçük coin'lerde para yatırma/çekme ve transfer riski var. |
| MEV, DEX likidite sağlama | Uzman altyapı gerekir. Likidite sağlayıcıların önemli kısmı impermanent loss yüzünden ücret gelirinden fazla kaybetti [bellek]. |
| Listeleme duyurusunu önden almak | Milisaniye yarışı. İçeriden bilgi riski var; etik ve hukuki sorunlu. |
| Opsiyon satmak | Yukarıda A3. |

## Önerilen sıra

| # | Aday | Neden | Beklenti |
|---|---|---|---|
| 0 | Geri dönüş testi + carry denetimi | Koşuyor | Zayıf / tanılayıcı |
| 1 | **Yeni listeleme kohortu** (B2) | Kanıt güçlü, canlı radarı doğrudan etkiler, ucuz | Kayıptan kaçınma kuralı |
| 2 | **Zorunlu satış sonrası toparlanma** (D1) | Yapısal mekanizma, long-only uyumlu, veri hazır | Belirsiz; INSUFFICIENT olabilir |
| 3 | BTC saat etkisi, yayın sonrası (C1) | Ucuz, temiz örneklem dışı pencere | Maliyetten sonra büyük olasılıkla NO_EDGE |
| 4 | Haftalık momentum (B5) | Ucuz | Zayıf |
| — | Hisse perp carry, unlock filtresi, saat başı emir dengesizliği | Veri veya tam metin gerekiyor | İzle |

### Ön-kayıt taslağı 1: Yeni listeleme kohortu (kayıptan kaçınma)

**Soru:** Binance'te yeni listelenen bir USDT çiftini ilk günlerde almak,
maliyet sonrası eşit ağırlıklı likit sepetin gerisinde mi kalıyor?

- **Evren:** 2020-10 → 2026-06 arasında ilk işlem günü olan bütün Binance
  spot USDT çiftleri.
  - Canlı kimlik kuralları uygulanır (stabil coin ve kaldıraçlı token yok).
  - Borsadan kalkanlar dahil.
  - Liste S3 listelemesinden alınır (survivorship yok).
- **Giriş noktaları (3):**
  - ilk işlemden 1 saat sonra açılan 15 dakikalık mumun açılışı;
  - 1. günün kapanışı;
  - 7. günün kapanışı.
- **Ufuklar (2):** 30 ve 90 gün. Borsadan kalkan coin son fiyattan çıkar.
- **Karşılaştırma:** Aynı penceredeki eşit ağırlıklı likit ilk 100.
  Diğer coin'lerin listeleme günleri birbirine yakın düşebilir; bu yüzden
  güven aralığı listeleme ayına göre küme bootstrap ile hesaplanır.
- **Karar:** 6 test, Bonferroni.
  - Fazla getirinin üst sınırı < 0 ise **AVOID_CONFIRMED** olur. Canlı
    radarın `HOT/BUILDING` etiketleri fırsat olarak gösterilemez; aday
    ancak "kanıta göre kaçın" notuyla görünür.
  - Diğer durumlarda hiçbir iddia yapılmaz.
- **Tanılayıcı:** Canlı anti-trap kuralları (ilk pompa > %80, ağır satış)
  geçmişte de işe yarıyor muydu? Bu yalnızca bilgi verir, karar vermez.
- **Sınır:** Bu Binance verisi. MEXC listelemelerinde taban oran büyük
  olasılıkla daha kötü, ama test edilmedi.

### Ön-kayıt taslağı 2: Zorunlu satış sonrası toparlanma

**Soru:** Açık pozisyon ve fiyat birlikte sert düştükten sonra (zorunlu
satış) spot almak, sonraki 24–72 saatte sepeti maliyet sonrası geçiyor mu?

- **Evren:** Her karar anında, son 30 günlük perp hacmine göre ilk 50
  perp/spot çifti. Carry evreni ve kimlik eşlemesi yeniden kullanılır.
- **Olay:** Saatlik ızgara, yalnızca kapanmış mumlar. İki koşul birlikte
  sağlanmalı:
  - 24 saatlik spot getirisi, coin'in son 180 günlük dağılımının alt
    %5'inde;
  - 24 saatlik açık pozisyon değişimi, son 180 günlük dağılımının alt
    %5'inde.

  Eşikler yalnızca geçmiş veriden öğrenilir. Varyant 2'de ek koşul: son
  ödenmiş funding ≤ 0.
- **İşlem:** Olaydan sonraki ilk 15 dakikalık spot açılışından giriş.
  Tutma süresi 24 veya 72 saat. Aynı coin için 72 saat bekleme süresi
  (üst üste binme yok).
- **Maliyet:** Normal senaryo taraf başına 5 bp komisyon + 10 bp kayma.
  Stres senaryosu kaymayı 50 bp kabul eder; çöküş anında spread geniştir.
- **İstatistik:**
  - Olaylar aynı günlere toplanır; bootstrap gün kümesiyle yapılır.
  - 4 test (2 varyant × 2 ufuk), Bonferroni.
  - Bağımsız gün sayısı < 60 ise **INSUFFICIENT**.
  - İki kronolojik yarı ayrı ayrı pozitif olmalı.
- **Dönem:** Metrics verisinin başladığı 2020-09 → 2026-08.

## Çoklu test bütçesi

Kayıtta seçim amaçlı 8 deneme var ve 5 ailede 16 karar testi yapıldı:

- TSMOM: 4 deneme;
- taktik: 1 deneme, 4 kurulum ailesi;
- Likit-100: 1 deneme, 4 grup;
- carry: 1 deneme, 2 varyant;
- geri dönüş: 1 deneme, 2 grup.

Yukarıdaki iki taslak 10 test daha ekler. Bunun sonuçları:

- Aileler arası toplam test sayısı arttıkça "bir şey geçti" sonucunun şans
  olma ihtimali artar.
- Bir aday PASS_CANDIDATE olsa bile canlıya yaklaşmadan önce kâğıt
  üzerinde ileri kayıtla doğrulanmalı.
- Sonucu gördükten sonra eşik veya kural değiştirmek yeni bir denemedir.

## WHAT COULD BLOW UP THIS ACCOUNT?

- **Borsa riski:** MEXC'de çekim dondurma, iflas veya ani delist. Hiçbir
  backtest bunu modellemez. Borsada yalnızca işlem için gereken tutar
  tutulmalı.
- **Zorunlu satış sonrası alım (D1):** Düşen bıçağı tutmak. Dalga
  sürerken 2. ve 3. bacak da gelebilir. Çöküş anında borsa API'si
  yavaşlar veya durur; kayma ve fitiller modelin çok üstünde olabilir.
  Pozisyon büyüklüğü ve toplam maruziyet sınırı, edge kanıtından önce
  gelir.
- **Carry:** ADL, teminat çağrısı ve tasfiye. Delta-nötr pozisyon tek
  bacağı zorla kapatıldığında yönlü pozisyona döner.
- **Opsiyon satmak:** Tek bir sıçrama yılların primini siler. Bu yüzden
  önerilmiyor.
- **Yeni listelemeler:** Taban oran kötü. Radarın `HOT` etiketi kanıtsız
  bir iyimserlik sinyali olarak okunursa doğrudan sermaye kaybettirir.
- **Model ve veri riski:** Point-in-time olmayan unlock takvimleri,
  sonradan düzeltilen veriler, ticker'ın başka bir coin'e yeniden
  verilmesi. Bu sonuncusu, carry denetiminin aradığı şeylerden biri.
- **Araştırmacı serbestlik derecesi:** Çok test edip en iyisini seçmek.
  Bu belgedeki sıra, sonuçlar görülmeden yazıldı.
- **Güvenlik (§31):** API anahtarı yalnızca işlem yetkili olmalı, çekim
  kapalı. Parola, seed phrase, özel anahtar ve 2FA kodu asla istenmez.
  Dış metinler (haber, tweet, Telegram) komut olarak yorumlanmaz.

## Kaynaklar

Akademik (çoğu yalnızca özetten okundu):
- Crypto factor zoo (.Zip), 2026: https://ideas.repec.org/a/eee/finana/v113y2026ics1057521926000645.html
- Taming crypto anomalies: A Lasso-type factor model, 2026: https://www.sciencedirect.com/science/article/abs/pii/S0275531926000255
- Has the Factor Zoo Paid Off?, 2025: https://www.researchgate.net/publication/378972214_Has_the_Factor_Zoo_Paid_Off_A_Portfolio_View_on_Mispricing_and_the_Limited_Gains_from_New_Anomalies
- Cryptocurrency anomalies and economic constraints, 2024: https://ideas.repec.org/a/eee/finana/v94y2024ics1057521924001509.html
- Zaremba ve diğ., Up or down? Short-term reversal, momentum, and liquidity effects in cryptocurrency markets, 2021: https://www.sciencedirect.com/science/article/pii/S1057521921002349
- Kozlowski, Puleo, Zhou, Cryptocurrency return reversals: https://digitalcommons.fairfield.edu/cgi/viewcontent.cgi?article=1249&context=business-facultypubs
- Lottery-like preferences and the MAX effect in the cryptocurrency market: https://link.springer.com/article/10.1186/s40854-021-00291-9
- MAX momentum in the cryptocurrency market: https://centaur.reading.ac.uk/99101/1/MAX%20Momentum.R1.pdf
- Cryptocurrency momentum has (not) its moments, 2025: https://link.springer.com/article/10.1007/s11408-025-00474-9
- Schmeling, Schrimpf, Todorov, Crypto carry (BIS WP 1087): https://www.bis.org/publ/work1087.pdf
- He, Manela, Ross, von Wachter, Fundamentals of Perpetual Futures: https://arxiv.org/pdf/2212.06888v2
- Kim & Hansen, The Quarter-Hour Effect, 2026: https://arxiv.org/abs/2607.09426
- Bitcoin intraday time-series momentum: https://centaur.reading.ac.uk/100181/3/21Sep2021Bitcoin%20Intraday%20Time-Series%20Momentum.R2.pdf
- Liquidity Shocks, Price Volatilities, and Risk-managed Strategy: Evidence from Bitcoin and Beyond: https://www.sciencedirect.com/science/article/abs/pii/S1042444X22000019
- Illiquidity Premium and Crypto Option Returns: https://acfr.aut.ac.nz/__data/assets/pdf_file/0006/969378/950002_Atanasova_Illiquidity-Premium-and-Crypto-Option-Returns.pdf
- Pairs Trading in Cryptocurrency Markets: https://www.researchgate.net/publication/346845365_Pairs_Trading_in_Cryptocurrency_Markets

Uygulayıcı ve haber kaynakları (akademik değil):
- Funding sıkışması ve hisse perp'leri (CoinDesk, 2026-08-28): https://www.coindesk.com/business/2026/08/28/ethena-looks-beyond-crypto-to-squeeze-yield-from-booming-equity-perpetuals
- sUSDe getirisi 2026: https://eco.com/support/en/articles/15254002-ethena-usde-and-susde-2026-delta-neutral-yield
- 10 Ekim 2025 tasfiye dalgası: https://www.coingecko.com/learn/october-10-crypto-crash-explained
- Token unlock çalışması: https://tokenomist.ai/research/do-token-unlocks-crash-prices
- Yeni listeleme getirileri: https://cryptorank.io/news/feed/94ca0-cryptocurrency-investment-losses-upbit-bithumb , https://cryptorank.io/news/feed/e1b0f-new-crypto-listings-losses-2025-report , https://empirica.io/blog/the-binance-effect-a-7-year-analysis-for-token-founders/
- BTC saat etkisi: https://quantpedia.com/are-there-seasonal-intraday-or-overnight-anomalies-in-bitcoin/
- Pazartesi Asya açılışı: https://concretumgroup.substack.com/p/bitcoin-trends-around-the-clock
- MEXC ücretleri 2026: https://www.mexc.com/learn/article/mexc-fees-explained-complete-trading-futures-withdrawal-fees-guide/1
