# Atılım kohortu — 2× öncesinde ne vardı? (protokol v1, sonuçtan önce)

**Tarih:** 2026-10-23 (ilk taslak); aynı gün inceleme bulgularıyla yeniden
kuruldu (örtüşen etiketler, blok uzunluğu, güncel sürümlü seriler —
`docs/DEAD_CODE_AUDIT.md` tarzı dürüstlükle aşağıda §5 ve §6'da). **Durum:**
protokol. Kod yok, koşu yok, sonuç yok. Kullanıcı onayından sonra harness
yazılır (`trading/backtest/breakout_cohort.py`), keşif bir kez koşulur,
adaylar ön-kayda girer, doğrulama bir kez koşulur.

**Bağlam:** `docs/POSITION_LONG.md` (uzun vadeli long pozisyon sistemi;
pozisyonlar vadeli, kullanıcı kararı 2026-10-23). Kullanıcının isteği:
"Atılım yapma kalıplarını geçmişe dönük iyi analiz edelim; coin, kripto
piyasası ve makro göstergelerini düzgün seçelim." Bu belge, hangi
göstergenin seçileceğine **sonuçtan önce yazılmış kuralların** karar
vermesini sağlar.

## 1. Soru

Survivorship'ten arındırılmış Binance USDT spot evreninde, her (coin, kohort
başlangıcı) gözlemi için:

1. Fiyat sonraki **12 ayda** en az **2×** oldu mu (günlük kapanışla)?
   24 aylık 2× (tezin ufku) betimleyici olarak yanında raporlanır.
2. Kohort başlangıcında **bilinebilen** hangi **coin** koşulları, aynı
   kohortun içinde (aynı piyasa dönemi sabitken) 2× oranını belirgin ve
   **her keşif kohortunda aynı yönde** değiştiriyor?
3. Piyasa ve makro koşullara göre 2× taban oranı dönemden döneme nasıl
   değişti? (Betimleyici: §6.3'teki nedenle burada istatistiksel karar yok.)
4. 2× olanların yolu nasıldı: kaç ayda, 2×'e varmadan en derin düşüş ne,
   en derin **fitil** (gün içi dip) ne; olmayanlar nereye kadar düştü?

Çıktı bir tahmin modeli ya da işlem kuralı **değildir**: koşullu taban
oranlar (güven aralıklarıyla), yol istatistikleri ve en fazla 5 ön-kayıtlı
**coin göstergesi** adayının mühürlü pencerede tek koşuda sınanması. Hiçbir
sonuç emir yetkisi vermez (AGENTS.md §4).

## 2. Evren ve veri

| Parça | Kaynak | Not |
|---|---|---|
| Evren | `data.binance.vision` spot aylık arşiv listesi, USDT çiftleri, delist edilenler dahil (`binance_universe.list_spot_symbols`) | Stabil, kaldıraçlı, sabitlenmiş ve bilinen sembol göçleri `binance_history_identity` kurallarıyla dışarıda. Spot seçildi, çünkü perp arşivi 2020'de başlıyor; spot 2017-08'den iki tam döngü verir (§5). |
| Spot günlük mumlar | Aylık `1d` kline dosyaları: açılış, en yüksek, en düşük, kapanış, quote hacmi; 2017-08 → veri sonu | Fiyat ve spot fitilleri. |
| Perp katmanı (2020'den) | Perp'i olan çiftler için USDT-M `1d` kline ve aylık `fundingRate` dosyaları (`binance_carry_universe.map_perps_to_spot` eşlemesi; çarpanlı kontratlar oranla) | **Yalnız betimleyici:** perp fitil derinliği ve funding. Keşif penceresinde yok ya da eksik (§5), aday olamaz. |
| BTC trend geçmişi | BTCUSDT spot günlük mumlar, 2017-08'den | Betimleyici piyasa katmanı. |
| Stablecoin arzı | DefiLlama `stablecoincharts/all` (günlük toplam) | **Güncel sürüm** (geçmiş revize edilebilir): yalnız betimleyici, aday olamaz. |
| Korku-Açgözlülük | alternative.me, günlük, 2018-02'den | Revize edilmez; yine de piyasa katmanı betimleyicidir (§6.3). |
| Makro | `research/atlas/macro_monthly.csv`, `macro_daily.csv` (depoda; 2017-09 → 2024-08, mühürlü) | Betimleyici. 2024-08 sonrası bilinmez. |

**Zaman kuralları (geleceğe bakmama):**
- Kohort başlangıcı = ayın 1'i 00:00 UTC. Göstergeler yalnız kapanışı başlangıçtan **önce** olan mumları kullanır.
- Giriş referansı = başlangıç gününün günlük mumunun **açılışı**.
- Etiketler başlangıçtan itibaren ileriye bakar.
- Veri sonu = kurulum anındaki son tam ay (bu protokol 2026-08'i varsayar). Bir gözlem yalnız etiket penceresi veri sonundan önce bitiyorsa o etiket için kohorttadır.
- Sembol göçü ve bir günden uzun boşluk süreklilik kırığıdır (`market_data.continuity_breaks` kuralı): kırık içeren bir pencere, o gözlemi bilinmeyen yapar, getiri yapmaz.

**Üyelik (başlangıçta):** çiftin başlangıçtan önce ≥ 90 tam günlük mumu var; son gün işlem görmüş; son 30 günün medyan günlük quote hacmi ≥ 1 M$; bu şartı sağlayanlar içinde medyan hacme göre ilk 150. BTC ve ETH üyedir; rapor "BTC/ETH hariç" kesitini de verir. (Hacim tabanı erken yılların hacmine göre seçildi; 2018'de 5 M$ evreni boşaltırdı.)

## 3. Etiketler (her gözlem için)

| Etiket | Tanım | Rol |
|---|---|---|
| `M12` | Sonraki 365 günün en yüksek **kapanışı** / giriş referansı | |
| `HIT2X_12` | `M12 ≥ 2,0` | **Birincil (karar)** |
| `M24`, `HIT2X_24` | Aynısı 730 günde | Betimleyici (tezin ufku; §6.3'teki bağımlılık nedeniyle karar yok) |
| `T2X` | 2×'e ilk kez kapanan güne kadar geçen gün (12 ay içinde; 24 ay ayrıca) | Yol |
| `DD_BEFORE_2X` | Girişten `T2X`'e kadar kapanışlarla en derin düşüş (2× olmadıysa pencere boyunca) | Yol |
| `DD_MAX` | Pencerede koşan zirveden en derin düşüş (kapanışlarla; giriş dahil) | Yol |
| `WICK_MIN` | Pencerede en düşük gün içi dip / giriş − 1 (spot; perp varsa perp ayrıca) | Yol |
| `FLASH_MAX` | Pencerede tek günde en derin fitil: min(düşük / önceki kapanış) − 1 | Yol |
| `R12`, `R24` | Pencere sonu kapanışı / giriş − 1 (delist edildiyse son kapanışla) | Betimleyici |
| `BEATS_BTC_12` | `R12` > BTC'nin aynı penceredeki getirisi | İkincil |
| `DELISTED` | Veri, pencere bitiminden 7 günden fazla önce kesiliyor: çıkış son kapanışla; satır **kalır** ve sayılır (survivorship) | |

2× kapanışla ölçülür; 2×'e değen bir fitil "oldu" sayılmaz.

## 4. Göstergeler (başlangıçta bilinen; tam tanımlar)

Bilinmeyen gösterge o gözlemi o göstergenin kovalarından dışarıda bırakır;
sıfır sayılmaz.

### 4.1 Coin göstergeleri — aday olabilir (kohort içi karşılaştırma)

| # | Gösterge | Tanım | Kovalar (şimdi sabitlendi) |
|---|---|---|---|
| C1 | `R90` | Son kapanış / 90 gün önceki kapanış − 1 | < −%30 · −%30…+%30 · > +%30 |
| C2 | `RS90` | `R90` − BTC `R90` (puan) | < −20 · ±20 · > +20 |
| C3 | `DIST_HIGH365` | Son kapanış / son 365 günün en yüksek kapanışı − 1 (geçmiş 365 günden kısaysa mevcut geçmiş, en az 90 gün) | < −%70 · −%70…−%30 · > −%30 |
| C4 | `DIST_LOW730` | Son kapanış / son 730 günün en düşük kapanışı − 1 (en az 180 gün geçmiş) | < +%50 · +%50…+%200 · > +%200 |
| C5 | `VOL_TREND` | Son 30 günün medyan quote hacmi / son 90 günün medyanı | < 0,8 · 0,8…1,25 · > 1,25 |
| C6 | `SIGMA90` | Son 90 günlük log getirilerin yıllıklandırılmış standart sapması | < 0,8 · 0,8…1,3 · > 1,3 |
| C7 | `AGE` | İlk spot mumundan bu yana gün | < 180 · 180…730 · > 730 |
| C8 | `WICK90` | Son 90 günün en düşük gün içi dibi / son kapanış − 1 (spot) | < −%40 · −%40…−%20 · > −%20 |
| C9 | `FLASH90` | Son 90 günde tek günde en derin fitil: min(düşük / önceki kapanış) − 1 (spot) | < −%25 · −%25…−%10 · > −%10 |
| C10 | `R365` | Son kapanış / 365 gün önceki kapanış − 1 (geçmiş yetersizse bilinmez) | < −%50 · −%50…+%50 · > +%50 |
| C11 | `VOL_RANK` | Üyeler içinde 30 günlük medyan hacim sırası (büyüklük vekili) | İlk 30 · 31–90 · 91–150 |

### 4.2 Perp katmanı — yalnız betimleyici (2020'den; aday olamaz)

| # | Gösterge | Tanım |
|---|---|---|
| D1 | `FUND90` | Son 90 günün ortalama funding oranı × 3 × 365 (yıllık %) |
| D2 | `PWICK90`, `PFLASH90` | C8 ve C9'un perp dipleriyle hesaplanmış hâli |
| D3 | `PWICK_MIN`, `PFLASH_MAX` | Pencerede perp dipleriyle `WICK_MIN` ve `FLASH_MAX` |

Neden aday olamaz: keşif kohortlarının yalnız sonuncusunda (2021-09) perp
verisi tam; "her keşif kohortunda aynı yön" kuralı sağlanamaz. Perp fitil
dağılımı pozisyon sisteminin likidasyon tamponu için yine de raporlanır
(§6.5); funding ve kalabalık ilişkisi de.

### 4.3 Piyasa göstergeleri — yalnız betimleyici (aday olamaz)

| # | Gösterge | Tanım | Kovalar (rapor için) |
|---|---|---|---|
| P1 | `BTC_TREND` | BTC spot haftalık kapanış: önceki 10 haftalık kapanışın en yükseğinin üstünde YÜKSELİŞ, en düşüğünün altında DÜŞÜŞ, arası YATAY | DÜŞÜŞ · YATAY · YÜKSELİŞ |
| P2 | `BTC_R90` | BTC spot 90 günlük getiri | < −%20 · ±%20 · > +%20 |
| P3 | `BTC_DIST_ATH` | BTC kapanış / geçmişteki en yüksek kapanış − 1 | < −%50 · −%50…−%20 · > −%20 |
| P4 | `BREADTH90` | Üyeler içinde `R90` > BTC `R90` olanların payı | < %33 · %33…%66 · > %66 |
| P5 | `BREADTH_POS` | Üyeler içinde `R90` > 0 olanların payı | < %33 · %33…%66 · > %66 |
| P6 | `STABLE_90` | Stablecoin toplam arzının 90 günlük değişimi (**güncel sürüm**) | < 0 · 0…+%5 · > +%5 |
| P7 | `FNG30` | Son 30 günün ortalama Korku-Açgözlülük endeksi | < 30 · 30…60 · > 60 |
| P8 | `UNIVERSE_SIZE` | Üye sayısı | Rapor |

Neden aday olamaz: bir piyasa göstergesi kohort içinde sabittir; ancak
kohortlar arasında test edilebilir ve örtüşmeyen 12 aylık kohort sayısı
pencere başına 4'tür (§5). Dört gözlemle güven aralığı yoktur; dönem
kartları olarak raporlanır (`docs/ATLAS.md` ile aynı statü).

### 4.4 Makro — yalnız betimleyici

| # | Gösterge | Tanım |
|---|---|---|
| M1 | `REAL_10Y` | Önceki ay sonu 10 yıllık reel faiz (atlas) ve 90 günlük değişimi |
| M2 | `NET_LIQ_90` | Net likidite (Fed bilançosu − TGA − RRP) 90 günlük değişimi |
| M3 | `VIX` | Başlangıçta bilinen VIX (günlük tablo, gecikmeli) |
| M4 | `NASDAQ_90` | Nasdaq 90 günlük değişimi (günlük tablo) |

Aylık makro tablo yayın gecikmesini taşımaz (atlas belgesindeki uyarı);
mühür 2024-08'de biter: 2024-09 ve 2025-09 kohortlarında makro bilinmez.

## 5. Pencereler, bölme ve mühür

**Neden spot ve neden yıllık kohort.** Sonuç etiketi 12 ay ileriye bakar.
Aylık kohortlarda ardışık iki kohort etiket aylarının 11'ini paylaşır; bu
gözlemler bağımsız değildir ve kısa bloklu bootstrap bağımlılığı yok sayar.
Keşif ile doğrulama arasında da etiket pencereleri örtüşmemelidir (purge):
keşif kohortlarının son etiket ayı, doğrulamanın ilk başlangıcından önce
bitmelidir. 24 aylık etiketle ve 2020'de başlayan perp verisiyle bu bölme
mümkün değildi (ilk taslağın hatası); 2017-08'de başlayan spot verisi ve 12
aylık etiketle mümkündür.

**Çıkarım kohortları (karar bunlarla):** her yıl 1 Eylül 00:00 UTC
başlangıçlı, 12 aylık etiket, örtüşmez.

| Pencere | Kohortlar | Etiket aralığı |
|---|---|---|
| Keşif | 2018-09, 2019-09, 2020-09, 2021-09 | 2018-09 → 2022-08 |
| Doğrulama | 2022-09, 2023-09, 2024-09, 2025-09 | 2022-09 → 2026-08 |

Keşifte ayı (2018), toparlanma (2019), boğa başlangıcı (2020) ve zirve
(2021) var; doğrulamada ayı dibi (2022), toparlanma (2023), yeni zirve
(2024) ve sonrası (2025). Keşif etiketleri 2022-08'de biter, doğrulama
2022-09'da başlar: **purge sağlanır**. 2017-09 kohortu yoktur: BTCUSDT
2017-08-17'de başladı, 90 günlük geçmiş şartı sağlanmaz.

**Betimleyici aylık kohortlar:** 2017-11 → 2025-09 arası her ay başı, 12
aylık etiket (son kohortun penceresi tam veri sonunda biter); 24 aylık
etiket 2024-09'a kadar. Yalnız §6.4 yol istatistikleri
ve §6.3 dönem kartları için; aday seçmek ya da karar vermek için **asla**.

**Mühür:** veri kurulumu bütün ayları indirir (keşif etiketleri 2022-08'e
kadar mum ister). Mühür analiz düzeyindedir: `discover` yalnız keşif
kohortlarını ve 2022-09'dan önceki aylık kohortları okur; `confirm` yalnız
kayıt defterinde kod parmak izi ve aday listesiyle kayıtlı bir trial varsa
çalışır ve doğrulama kohortlarını o zaman okur.

**Dürüst sınır:** pencere **veri** olarak mühürlüdür, **bilgi** olarak
değil: 2022–2026 piyasa yolunun genel seyri biliniyor. Kovalar ve eşikler
bu yüzden sonuçtan önce, bu belgede sabitlendi; keşifte de değiştirilmez.

**Çapa duyarlılığı (betimleyici):** aynı hesap 1 Mart çapasıyla keşif
penceresinde tekrarlanır (2019-03, 2020-03, 2021-03; etiketleri 2022-02'de
biter, purge korunur) ve yalnız raporlanır: aday seçimine ve karara girmez.

## 6. Yöntem

### 6.1 Koşulsuz taban oran

Her çıkarım kohortu için `HIT2X_12` payı (üye sayısıyla); aylık kohortlarda
aynı pay zaman serisi olarak (betimleyici). 24 aylık pay yanında.

### 6.2 Coin göstergeleri: kohort içi karşılaştırma

Bir coin göstergesi kohort içinde coinden coine değişir; aynı kohortun
üyeleri aynı piyasa dönemini yaşar. Bu yüzden karşılaştırma **kohort
içinde** yapılır ve piyasa dönemi sabitlenmiş olur:

- Kohort `c`, gösterge `X` için: `LIFT_c` = P(`HIT2X_12` | üst kova) −
  P(`HIT2X_12` | alt kova), yalnız `c`'nin üyeleriyle. Bir kovada 10'dan az
  coin varsa `LIFT_c` bilinmez.
- **Havuz:** `LIFT` = bilinen `LIFT_c` değerlerinin ortalaması (kohortlar
  eşit ağırlıklı).
- **Güven aralığı:** tabakalı bootstrap — her kohortun üyeleri kendi
  içinde yerine koyarak yeniden örneklenir (2.000 çekiliş, tohum 0), `LIFT`
  yeniden hesaplanır; %95 aralık yüzdeliklerle. Kohortlar yeniden
  örneklenmez: dört kohort bir blok bootstrap'a yetmez; kohortlar arası
  tutarlılık aşağıdaki işaret kuralıyla aranır.
- **Sınır (açıkça):** aynı kohortun coinleri bağımsız değildir (sektör,
  beta). Kohort içi fark ortak piyasa bileşenini düşürür ama sektör
  bağımlılığını düşürmez; aralıklar bu kadarıyla iyimserdir ve rapor bunu
  yazar.

### 6.3 Piyasa ve makro göstergeleri: yalnız dönem kartları

Pencere başına dört örtüşmeyen kohort vardır. Dört gözlemle güven aralığı
ya da karar yoktur. Rapor, her çıkarım kohortu için başlangıçtaki piyasa ve
makro durumu ile o kohortun 2× taban oranını ve yol istatistiklerini **dönem
kartı** olarak verir; aylık kohortlarla aynı ilişki zaman serisi olarak
gösterilir. Bu katmandan **aday çıkmaz**; rejim katmanı (K0) v3.2
kuralıyla kalır ve ileriye dönük kayıtla test edilir.

### 6.4 Yol istatistikleri

Her kova için (ve bütün evren için): 2× olanlarda `T2X`, `DD_BEFORE_2X`,
`WICK_MIN` medyanları; herkeste `DD_MAX`, `WICK_MIN`, `FLASH_MAX`
dağılımı (medyan, %25/%75, en kötü %5). `SIGMA90` kovalarına göre
`WICK_MIN` ve `FLASH_MAX` dağılımı ayrıca tablolaşır; perp katmanı
olan kohortlarda (2020'den) perp dipleriyle aynı tablo. Pozisyon
sisteminin likidasyon tamponu (`docs/POSITION_LONG.md` §13.2) bu tablodan
okunur: bir yıllık en derin fitilin ötesine geçen gözlem payı.

### 6.5 Aday kuralı (önceden yazıldı)

Bir **coin göstergesi** aday olur ancak ve ancak keşifte:

1. en az 3 kohortta `LIFT_c` biliniyor;
2. bilinen bütün `LIFT_c` değerleri aynı işaretli;
3. havuz |`LIFT`| ≥ 15 puan;
4. havuz `LIFT` aralığı sıfırı dışlıyor.

En fazla **5** aday; sıralama |`LIFT`| / aralık genişliği. Yön her gösterge
için önceden söylenmez; kuralı sağlayan yön kaydedilir. Perp, piyasa ve
makro katmanları aday olamaz.

### 6.6 Ön-kayıt ve doğrulama (bir kez)

Adaylar, yönleri, kovalar, kod parmak izi ve veri sonu
`research/trials/registry.jsonl`'e girer; doğrulama ancak bundan sonra
koşar. Aynı kovalar ve istatistik, doğrulama kohortlarıyla. Aile alfası
0,05 / aday sayısı. Aday başına karar:

- `CONFIRMED`: bilinen `LIFT_c` değerlerinin en az 3'ü keşifle aynı
  işaretli, havuz `LIFT` aynı işaretli ve aralık (aile alfasında) sıfırı
  dışlıyor;
- `NO_EFFECT`: aralık sıfırı içeriyor ya da işaretler karışık;
- `REVERSED`: havuz `LIFT` ters işaretli ve aralık sıfırı dışlıyor;
- `INCOMPLETE_DATA`: 3'ten az kohortta `LIFT_c` biliniyor ya da etiketlerin
  > %5'i bilinmiyor.

### 6.7 Kontrol

Rastgele gösterge (her gözleme tohumlu rastgele kova) aynı boru hattından
geçirilir; keşifte 6.5 kuralını sağlama sıklığı raporlanır (yöntemin kendi
gürültüsü; 1.000 tekrar).

## 7. Sonuç ne işe yarar, ne yaramaz

| Sonuç | Kullanımı |
|---|---|
| `CONFIRMED` coin göstergesi | Pozisyon sisteminde zamanlama filtresi **adayı** (kullanıcı onayıyla, ayrı PR; ileriye dönük kayıt test eder) |
| Dönem kartları (piyasa, makro) | Rejim katmanının (K0) varsayımlarını okumak için; kural değiştirmez |
| Yol istatistikleri | Likidasyon tamponu kuralı ve kart beklentileri (`T2X`, 2× öncesi düşüş, fitil derinliği) |
| Her şey `NO_EFFECT` | Zamanlama filtresi eklenmez; K0 v3.2 kuralıyla kalır. Bu da bir sonuçtur. |

Hiçbir sonuç modülü otomatik değiştirmez; hiçbir sonuç emir yetkisi değildir.

## 8. Bilinen sınırlar

- Binance spot evreni; 2017-08'den. Erken kohortlar küçüktür (2018-09'da onlarca çift); bir kovada 10 coin şartı bazı göstergeleri erken kohortlarda bilinmez bırakır.
- Pencere başına dört bağımsız dönem: piyasa ve makro katmanı için test yoktur, yalnız tasvir. Coin göstergeleri kohort içinde test edilir; aynı kohortun coinleri sektörle bağımlıdır, aralıklar iyimserdir.
- 12 aylık etiket tezin 24–36 aylık ufkundan kısadır; 24 ay betimleyicidir. Daha uzun etiketle örtüşmeyen bölme bu veriyle yapılamaz.
- Perp fitilleri ve funding 2020'den vardır; adaya giremez.
- Stablecoin serisi güncel sürümdür (revize olabilir); makro aylık tablo yayın gecikmesini taşımaz: ikisi de betimleyici.
- Delist edilen coinler son kapanışla sayılır; gerçek çıkış daha kötü olabilir.
- Temel veri (gelir, arz takvimi) yoktur: o gün bilinen haliyle elde edilemez.
- Çok sayıda gösterge: aday sayısı 5 ile sınırlı, aile alfası bölünür; keşif örneklem içidir, kanıt yalnız doğrulamadan gelir.
- Pencere bilgi olarak mühürlü değildir (2022–2026 bilinir); eşikler bu yüzden önceden sabit.

## 9. Uygulama (harness yazıldı; hiçbir koşu yapılmadı)

- `trading/backtest/breakout_cohort.py`: `build` (spot 1d, perp 1d + funding, stablecoin, Korku-Açgözlülük; atlas depodan okunur), `discover`, `register`, `confirm`. Her eşik, pencere ve kural bu belgedeki sabitlerle modülün başında durur; `trial_params` onları deneme kaydına yazar.
  - Günlük seri yoğun gün ızgarasındadır; eksik gün NaN. Bir pencerede iki ya da daha fazla ardışık eksik gün (bir günden uzun boşluk) o göstergeyi ya da etiketi **bilinmez** yapar; tek eksik gün ileri doldurulur (90 günlük pencerede en fazla 2, 365'te 5, 730'da 10 tek gün).
  - Etikette veri, pencere bitiminden 7 günden fazla önce kesilip bir daha gelmiyorsa `DELISTED` (son kapanışla); kesilip yeniden geliyorsa bilinmez.
  - `confirm`, keşif raporunun kod parmak izini ve adayları alır; aynı parmak izi ve aday listesiyle kayıtlı bir trial yoksa doğrulama kohortlarını okumadan durur.
- `tests/test_breakout_cohort.py` (16 test): purge ve pencereler, etiketler (2× günü, 2× öncesi düşüş, fitil, tek günlük fitil, delist, boşluk), üyelik (90 gün, son gün, hacim tabanı, ilk 150), göstergelerin yalnız geçmişi okuması (gelecek mumlar değişince aynı), kırık kuralı, kova eşikleri, BTC haftalık kanal, funding/stablecoin/Korku-Açgözlülük/makro yardımcıları, kohort içi lift ve tabakalı bootstrap (tohumlu), aday kuralı ve uygunluk (perp katmanı asla), rastgele kontrol, kararlar, sentetik evrende uçtan uca keşif ve mühür, kayıt, manifest özet denetimi.
- Workflow `breakout_cohort.yml` (`stage: discover | confirm`). Keşif raporu `research/evidence/breakout_discovery.json` olarak depoya girer; `register` adayları kayıt defterine yazar; `confirm` ancak o kayıtla çalışır. Kullanıcı her aşamayı bir kez tetikler.
- Sonuçlar bu belgeye eklenir: keşif → aday listesi → ön-kayıt → doğrulama.
