# Atılım kohortu — 2× öncesinde ne vardı? (protokol v1, sonuçtan önce)

**Tarih:** 2026-10-23. **Durum:** protokol. Kod yok, koşu yok, sonuç yok.
Kullanıcı onayından sonra harness yazılır (`trading/backtest/breakout_cohort.py`),
keşif bir kez koşulur, adaylar ön-kayda girer, doğrulama bir kez koşulur.

**Bağlam:** `docs/POSITION_LONG.md` (uzun vadeli long pozisyon sistemi; pozisyonlar
vadeli, kullanıcı kararı 2026-10-23). Kullanıcının isteği: "Atılım yapma
kalıplarını geçmişe dönük iyi analiz edelim; coin, kripto piyasası ve makro
göstergelerini düzgün seçelim." Bu belge, hangi göstergenin seçileceğine
**sonuçtan önce yazılmış kuralların** karar vermesini sağlar.

## 1. Soru

Binance USDT vadeli (perp) evreninde, her (coin, ay başı) gözlemi için:

1. Fiyat sonraki **24 ayda** en az **2×** oldu mu (günlük kapanışla)?
2. Ay başında **bilinebilen** hangi koşullar (coin, piyasa, makro) bu oranı
   belirgin ve **iki ayrı pencerede tutarlı** biçimde değiştiriyor?
3. 2× olanların yolu nasıldı: kaç ayda, 2×'e varmadan en derin düşüş ne,
   en derin **fitil** (gün içi dip) ne? Olmayanlar nereye kadar düştü?

Çıktı bir tahmin modeli ya da işlem kuralı **değildir**: koşullu taban
oranlar (güven aralıklarıyla), yol istatistikleri ve en fazla 5 ön-kayıtlı
gösterge adayının mühürlü pencerede tek koşuda sınanması. Hiçbir sonuç emir
yetkisi vermez (AGENTS.md §4).

## 2. Evren ve veri

| Parça | Kaynak | Not |
|---|---|---|
| Evren | `data.binance.vision` USDT-M perp aylık arşiv listesi (delist edilenler dahil; 2026-10-23'te 847 sembol klasörü) | Stabil, kaldıraçlı, sabitlenmiş ve bilinen sembol göçleri `binance_history_identity` kurallarıyla dışarıda. `1000PEPE` gibi çarpanlı kontratlar aynı varlık sayılır (oranlarla çalışılır). |
| Perp günlük mumlar | Aylık `1d` kline dosyaları: açılış, en yüksek, en düşük, kapanış, quote hacmi | Fitil = günlük en düşük. Aylık arşiv 2020 başından itibaren (BTCUSDT 2019-09 dosyası yok). |
| Funding | Aylık `fundingRate` dosyaları | 8 saatlik oranlar. |
| BTC trend geçmişi | BTCUSDT **spot** günlük mumlar, 2017-08'den | Perp geçmişi kısa; BTC trend durumu için spot. |
| Stablecoin arzı | DefiLlama `stablecoincharts/all` (günlük toplam) | Güncel sürüm; küçük revizyon olabilir, raporda yazılır. |
| Korku-Açgözlülük | alternative.me, günlük, 2018-02'den | Revize edilmez. |
| Makro | `research/atlas/macro_monthly.csv`, `macro_daily.csv` (depoda; 2017-09 → 2024-08, mühürlü) | Yalnız **betimleyici** (ay sonu değerleri yayın gecikmesiyle nokta-zamanlı değil). Günlük tablo (VIX, Nasdaq) gecikmeli ve nokta-zamanlı. |

**Zaman kuralları (geleceğe bakmama):**
- Ay başı = ayın 1'i 00:00 UTC. Göstergeler yalnız kapanışı ay başından **önce** olan mumları kullanır.
- Giriş referansı = ayın ilk günlük mumunun **açılışı**.
- Etiketler ay başından itibaren ileriye bakar.
- Veri sonu = kurulum anındaki son tam ay. Bir gözlem yalnız 24 aylık penceresi veri sonundan önce bitiyorsa kohorttadır (12 aylık etiket için ayrı kural).
- Çarpanlı kontrat değişimi (örn. `PEPE` → `1000PEPE`) aynı varlıkta süreklilik kırığı sayılır: `market_data.continuity_breaks` kuralı (bir günden uzun boşluk = kırık, getiri değil).

**Üyelik (ay başında):** perp'in ay başından önce ≥ 90 tam günlük mumu var; son gün işlem görmüş; son 30 günün medyan günlük quote hacmi ≥ 5 M$ (işlem yapılamayacak boyutlar dışarıda). BTC ve ETH üyedir; rapor "BTC/ETH hariç" kesitini de verir.

## 3. Etiketler (her gözlem için)

| Etiket | Tanım |
|---|---|
| `M24` | Sonraki 730 günün en yüksek **kapanışı** / giriş referansı |
| `HIT2X_24` | `M24 ≥ 2,0` (birincil) |
| `HIT2X_12`, `M12` | Aynısı 365 günde (ikincil) |
| `T2X` | 2×'e ilk kez kapanan güne kadar geçen gün |
| `DD_BEFORE_2X` | Girişten `T2X`'e kadar kapanışlarla en derin düşüş (2× olmadıysa 24 ay boyunca) |
| `DD_MAX_24` | 24 ayda koşan zirveden en derin düşüş (kapanışlarla; giriş dahil) |
| `WICK_MIN_24`, `WICK_MIN_90` | 24 ayda / ilk 90 günde en düşük **gün içi dip** / giriş − 1 |
| `FLASH_MAX_24` | 24 ayda tek günde en derin fitil: min(düşük / önceki kapanış) − 1 |
| `R24` | +730. gün kapanışı / giriş − 1 (delist edildiyse son kapanışla) |
| `BEATS_BTC_24` | `R24` > BTC'nin aynı penceredeki getirisi (ikincil) |
| `DELISTED` | Veri, pencere bitiminden 7 günden fazla önce kesiliyor: çıkış son kapanışla; satır **kalır** ve sayılır (survivorship) |

2× kapanışla ölçülür; 2×'e değen bir fitil "oldu" sayılmaz.

## 4. Göstergeler (ay başında bilinen; tam tanımlar)

Bilinmeyen gösterge o gözlemi o göstergenin kovalarından dışarıda bırakır; sıfır sayılmaz.

### Coin (perp günlük mumlarından)

| # | Gösterge | Tanım | Kovalar (şimdi sabitlendi) |
|---|---|---|---|
| C1 | `R90` | Son kapanış / 90 gün önceki kapanış − 1 | < −%30 · −%30…+%30 · > +%30 |
| C2 | `RS90` | `R90` − BTC `R90` (puan) | < −20 · ±20 · > +20 |
| C3 | `DIST_HIGH365` | Son kapanış / son 365 günün en yüksek kapanışı − 1 (geçmiş 365 günden kısaysa mevcut geçmiş, en az 90 gün) | < −%70 · −%70…−%30 · > −%30 |
| C4 | `DIST_LOW730` | Son kapanış / son 730 günün en düşük kapanışı − 1 (en az 180 gün geçmiş) | < +%50 · +%50…+%200 · > +%200 |
| C5 | `VOL_TREND` | Son 30 günün medyan quote hacmi / son 90 günün medyanı | < 0,8 · 0,8…1,25 · > 1,25 |
| C6 | `SIGMA90` | Son 90 günlük log getirilerin yıllıklandırılmış standart sapması | < 0,8 · 0,8…1,3 · > 1,3 |
| C7 | `AGE` | İlk perp mumundan bu yana gün | < 180 · 180…730 · > 730 |
| C8 | `WICK90` | Son 90 günün en düşük gün içi dibi / son kapanış − 1 | < −%40 · −%40…−%20 · > −%20 |
| C9 | `FLASH90` | Son 90 günde tek günde en derin fitil: min(düşük / önceki kapanış) − 1 | < −%25 · −%25…−%10 · > −%10 |
| C10 | `FUND90` | Son 90 günün ortalama funding oranı × 3 × 365 (yıllık %) | < 0 · 0…%10 · > %10 |
| C11 | `R365` | Son kapanış / 365 gün önceki kapanış − 1 (geçmiş yetersizse bilinmez) | < −%50 · −%50…+%50 · > +%50 |

### Piyasa (ay başında)

| # | Gösterge | Tanım | Kovalar |
|---|---|---|---|
| P1 | `BTC_TREND` | BTC spot haftalık kapanış: önceki 10 haftalık kapanışın en yükseğinin üstünde YÜKSELİŞ, en düşüğünün altında DÜŞÜŞ, arası YATAY (haftalık bar Pazartesi 00:00 UTC) | DÜŞÜŞ · YATAY · YÜKSELİŞ |
| P2 | `BTC_R90` | BTC spot 90 günlük getiri | < −%20 · ±%20 · > +%20 |
| P3 | `BTC_DIST_ATH` | BTC kapanış / geçmişteki en yüksek kapanış − 1 | < −%50 · −%50…−%20 · > −%20 |
| P4 | `BREADTH90` | Üyeler içinde `R90` > BTC `R90` olanların payı (altcoin sezonu vekili) | < %33 · %33…%66 · > %66 |
| P5 | `BREADTH_POS` | Üyeler içinde `R90` > 0 olanların payı | < %33 · %33…%66 · > %66 |
| P6 | `STABLE_90` | Stablecoin toplam arzının 90 günlük değişimi | < 0 · 0…+%5 · > +%5 |
| P7 | `FNG30` | Son 30 günün ortalama Korku-Açgözlülük endeksi | < 30 · 30…60 · > 60 |
| P8 | `FUND_MKT` | Üyelerin `FUND90` medyanı (kalabalık) | < 0 · 0…%10 · > %10 |
| P9 | `UNIVERSE_SIZE` | Üye sayısı (listeleme patlaması vekili) | Keşif penceresinin üçte birlikleri |

### Makro (yalnız betimleyici; ön-kayda giremez)

| # | Gösterge | Tanım |
|---|---|---|
| M1 | `REAL_10Y` | Önceki ay sonu 10 yıllık reel faiz (atlas) ve 90 günlük değişimi |
| M2 | `NET_LIQ_90` | Net likidite (Fed bilançosu − TGA − RRP) 90 günlük değişimi |
| M3 | `VIX` | Ay başında bilinen VIX (günlük tablo, gecikmeli) |
| M4 | `NASDAQ_90` | Nasdaq 90 günlük değişimi (günlük tablo) |

Makro tablonun aylık değerleri yayın gecikmesini taşımadığı için (atlas
belgesindeki aynı uyarı) bu grup yalnız tanılayıcıdır: aday olamaz. Mühür
2024-08'de bittiği için doğrulama penceresinin son kohortlarında makro
bilinmez; rapor bunu yazar.

## 5. Pencereler ve mühür

- **Keşif:** ay başı 2020-04 → 2022-08 olan kohortlar (29 ay; 24 aylık etiketleri 2024-08'e kadar tamamlanır). İki yarı: 2020-04 → 2021-06 (boğa) ve 2021-07 → 2022-08 (zirve ve ayı).
- **Doğrulama:** ay başı 2022-09 → 2024-08 olan kohortlar (24 ay; etiketleri 2026-08'e kadar tamamlanır). `HIT2X_12` için 2025-08'e kadar uzatılır (ikincil).
- **Mühür:** veri kurulumu bütün ayları indirir (keşif etiketleri 2024-08'e kadar mum ister). Mühür analiz düzeyindedir: `discover` yalnız ay başı 2022-09'dan önceki kohortları okur; `confirm` yalnız kayıt defterinde kod parmak izi ve aday listesiyle kayıtlı bir trial varsa çalışır ve doğrulama kohortlarını o zaman okur.
- **Dürüst sınır:** pencere **veri** olarak mühürlüdür, **bilgi** olarak değil: 2022–2026 piyasa yolunun genel seyri biliniyor. Kovalar ve eşikler bu yüzden sonuçtan önce, bu belgede sabitlendi; keşifte de değiştirilmez.

## 6. Yöntem

1. **Koşulsuz taban oran:** her kohort için `HIT2X_24` payı; yıl bazında; kohort-ağırlıklı ortalama (her ay eşit) ve gözlem-ağırlıklı ortalama ayrı.
2. **Koşullu oranlar:** her gösterge için üç kovada `P(HIT2X_24)`, %95 güven aralığı. Aralık, **kohort ayları üzerinde** hareketli blok bootstrap ile (blok 3 ay, 2.000 çekiliş, tohum 0): bir ay yeniden örneklendiğinde o ayın bütün gözlemleri birlikte gelir; böylece aynı ay içindeki bağımlılık (aynı piyasa) korunur.
3. **Fark:** `LIFT` = P(üst kova) − P(alt kova), aynı bootstrap ile güven aralığı. Yön her gösterge için önceden söylenmez; iki yarıda aynı yönde çıkması aranır.
4. **Yol istatistikleri (kova başına):** 2× olanlarda `T2X`, `DD_BEFORE_2X`, `WICK_MIN_24` medyanları; herkeste `DD_MAX_24`, `WICK_MIN_24`, `FLASH_MAX_24` dağılımı (medyan, %25/%75, en kötü %5). `SIGMA90` kovalarına göre `WICK_MIN_90` ve `FLASH_MAX_24` dağılımı ayrıca tablolaşır: pozisyon sisteminin likidasyon tamponu bu tablodan okunur (`docs/POSITION_LONG.md` §13).
5. **Aday kuralı (önceden yazıldı):** bir gösterge aday olur ancak ve ancak keşifte (a) |`LIFT`| ≥ 15 puan, (b) `LIFT` aralığı sıfırı dışlıyor, (c) iki yarıda `LIFT` aynı işaretli. En fazla **5** aday; sıralama |`LIFT`| / aralık genişliği. Makro grubu aday olamaz.
6. **Ön-kayıt:** adaylar, kovalar, kod parmak izi ve veri sonu `research/trials/registry.jsonl`'e girer; doğrulama ancak bundan sonra koşar.
7. **Doğrulama (bir kez):** aynı kovalar, aynı istatistik, doğrulama kohortları. Aile alfası 0,05 / aday sayısı. Aday başına karar:
   - `CONFIRMED`: `LIFT` keşifle aynı işaretli ve aralık (aile alfasında) sıfırı dışlıyor;
   - `NO_EFFECT`: aralık sıfırı içeriyor;
   - `REVERSED`: ters işaretli ve aralık sıfırı dışlıyor;
   - `INCOMPLETE_DATA`: doğrulama kohortu < 12, bir kovada gözlem < 100 ya da etiketlerin > %5'i bilinmiyor.
8. **Kontrol:** rastgele gösterge (her gözleme tohumlu rastgele kova) aynı boru hattından geçirilir; keşifte "aday" çıkma sıklığı raporlanır (yöntemin kendi gürültüsü).

## 7. Sonuç ne işe yarar, ne yaramaz

| Sonuç | Kullanımı |
|---|---|
| `CONFIRMED` piyasa göstergesi | K0 rejim katmanına **aday** kural (kullanıcı onayıyla, ayrı PR) |
| `CONFIRMED` coin göstergesi | Pozisyon sisteminde zamanlama filtresi **adayı** (aynı şart) |
| Yol istatistikleri | Likidasyon tamponu kuralı ve kart beklentileri (`T2X`, 2× öncesi düşüş) |
| Her şey `NO_EFFECT` | K0 v3.2 kuralıyla kalır; zamanlama filtresi eklenmez. Bu da bir sonuçtur. |

Hiçbir sonuç modülü otomatik değiştirmez; hiçbir sonuç emir yetkisi değildir.

## 8. Bilinen sınırlar

- Yalnız Binance perp evreni; perp geçmişi 2020'den. Keşif penceresine 2020–21 boğası hâkim; iki pencere yaklaşık bir buçuk döngüdür.
- Gözlemler örtüşür: aynı coin ardışık aylarda, 24 aylık pencereler iç içe. Etkin örneklem satır sayısından çok küçüktür; blok bootstrap bunu kısmen karşılar, tamamen değil.
- 2×'in taban oranı rejime çok bağlıdır; koşullu oran bir tahmin değil, geçmişin tasviridir.
- Funding Binance'inkidir; kalabalık borsaya göre değişir.
- Stablecoin ve Korku-Açgözlülük serileri güncel sürümdür; makro grubu betimleyicidir.
- Temel veri (gelir, arz takvimi) yoktur: o gün bilinen haliyle elde edilemez.
- Delist edilen coinler son kapanışla sayılır; gerçek çıkış daha kötü olabilir.
- Çok sayıda gösterge: aday sayısı 5 ile sınırlı, aile alfası bölünür; yine de keşif örneklem içidir, kanıt yalnız doğrulamadan gelir.

## 9. Uygulama

- `trading/backtest/breakout_cohort.py`: `build` (veri), `discover`, `register`, `confirm`, `report`; `market_data.py` yardımcılarını kullanır.
- `tests/test_breakout_cohort.py`: etiketler (2× günü, 2× öncesi düşüş, fitil, delist), geleceğe bakmama (ay başından sonraki mumlar değişince göstergeler değişmez), kova kuralları, blok bootstrap, mühür (`confirm` kayıtsız çalışmaz), üyelik (90 gün, hacim tabanı), sembol göçü kırığı.
- Workflow `breakout_cohort.yml`: `discover` işi; `confirm` işi kayıt ister. Kullanıcı bir kez tetikler.
- Sonuçlar bu belgeye eklenir: keşif → aday listesi → ön-kayıt → doğrulama.
