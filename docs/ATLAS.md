# Majör coin atlası, 2017-09 → 2024-08

Bu atlas, majör coinlerin 7 yıllık davranışını ay ay anlatır. Üç katmanı vardır: piyasa, makro ve takvim.

**Atlas bir sinyal değildir.**
- Eşik öğrenmez, sıralama yapmaz, karar vermez.
- Emir yetkisi yoktur (`can_authorize_trade = false`).
- Görevi, kullanıcıyla birlikte **hipotez** çıkarmaktır.
- Her hipotez önce ön-kayda girer. Sonra atlasın hiç göstermediği veride, bir kez test edilir.

Kod: `trading/research/atlas.py`. Makro tablo: `.github/workflows/atlas_macro.yml`.

## Mühür

- **Atlas 2024-08-31'den sonrasını okumaz ve göstermez.**
  - 2024-09 → 2026-08 doğrulama penceresi mühürlüdür.
  - O pencere, "trend bozulunca çık" döngüsünün ve atlastan çıkacak hipotezlerin tek seferlik son testine ayrılmıştır.
- **Kod, mühürü zorla korur.**
  - `verify_datasets`, en yeni veri seti 2024-08-31'de bitmiyorsa çalışmayı reddeder.
  - FRED isteği 2024-08-31'de kesilir.
- **Sıra.** Ön-tarih testi (2018-09 → 2020-09, `docs/TRADE_LOOP_STUDY.md`) atlastan **önce** yapıldı. Atlas o dönemi gösterdikten sonra o dönem "el değmemiş" sayılmazdı.

**Mühür veri için geçerli, bilgi için değil.**
- Kullanıcı da Claude da 2024-09 → 2026-08 arasında piyasada kabaca ne olduğunu biliyor: 2025 tarifeleri, 2025–26 Orta Doğu gerilimi gibi.
- Bu yüzden hipotez seçimini etkileyen her bilgi kaydedilir.
- Discovery'de sabitlenmiş mekanik kurallar (D1 "trend bozulunca çık" gibi) bu pencerede doğrulanabilir.
- Pencerede olan olaylar konuşulduktan **sonra** seçilen bir faktör ise orada doğrulanamaz. Bu faktörü kullanan bir hipotez, seçimden sonraki veride ileriye dönük olarak test edilir.
  - Brent ve USD/CNY 2026-10-02'de bu şekilde eklendi. Bu iki faktör için doğrulama 2026-10'da başlar (`CONFIRMATION_FROM`).
  - Bu bilgi `build` çıktısında `confirmation_from` alanında da yazar.

## Katmanlar

### 1. Piyasa (Binance spot)

**Kaynak.** Kapanmış 15 dakikalık mumlar günlük muma çevrilir. Günün kapanışı, o günün bilinen son 15 dakikalık kapanışıdır. Hiç verisi olmayan gün bilinmez kalır.

**Evren.** Her ay için, o ayın başında görülebilen verilerle majör kuralı uygulanır (`docs/MAJORS_STUDY.md`):
- Sabit üyeler: BTC ve ETH.
- 30 günlük hacme göre ilk 10 coin.
- En büyük meme coin.
- Bir coinin evrene girmesi için en az 90 günlük geçmişi olmalı.

Üç fark vardır:
- **Perp şartı yoktur.** Binance'te perp 2019-09'da başladı.
- **Borsa çapındaki duruşlar kırılma sayılmaz.** Bir coindeki boşluk BTC'de de aynı anda varsa (±1 gün), bu borsanın durmasıdır, token değişimi değildir. Örnek: Binance, 2018-02-08..10, 54 saat. Coine özgü bir boşluk ise yeni bir token gibi ele alınır. O boşluğun üzerinden getiri, trend ya da ortalama hesaplanmaz. Aynı kural iki veri setinin birleştiği yerde de uygulanır.
- **Coin yaşı veri setleri arasında taşınır.** 90 günlük geçmiş, coinin ilk işlem gördüğü ya da son kırılmadan döndüğü tarihten sayılır; yeni veri setinin başlangıcından sayılmaz. İlk kez yeni veri setinde görülen bir coin, temkinli olarak orada listelenmiş sayılır. Bu düzeltme olmasaydı 2020-11 evrenine DOT, UNI, YFI ve YFII, 2020-12 evrenine UNI 90 günden genç olarak girerdi.

**Aylık ölçüler:**

| Ölçü | Tanım |
|---|---|
| `btc_ret_pct`, `basket_ret_pct` | BTC'nin ve eşit ağırlıklı majör sepetin ay getirisi |
| `alts_minus_btc_pct` | BTC dışındaki üyelerin ortalaması eksi BTC |
| `dispersion_pct` | Üyelerin ay getirilerinin dağılımı |
| `btc_drawdown_pct` | BTC'nin 2017-09'dan beri gördüğü zirveden uzaklığı |
| `btc_vol_pct`, `basket_vol_pct` | Yıllıklaştırılmış günlük oynaklık |
| `avg_correlation` | Üyelerin günlük getirileri arasındaki ortalama korelasyon |
| `breadth_above_ema50` | 50 günlük ortalamanın üstünde kapanan üyelerin payı |
| `btc_trend` | Aşağıdaki tabloya bakın |
| `btc_vol_state` | SAKİN (< %40), NORMAL, FIRTINALI (> %80) |
| `btc_volume_share` | BTC'nin evren hacmindeki payı |

`btc_trend` şöyle belirlenir. EMA200, son 200 kapanışın en az %90'ı biliniyorsa hesaplanır; bir kırılmadan sonra sıfırdan başlar.

| Durum | Koşul |
|---|---|
| YÜKSELİŞ | BTC EMA200'ün üstünde ve EMA200 son 20 günde yükseliyor |
| DÜŞÜŞ | BTC EMA200'ün altında ve EMA200 yükselmiyor |
| GEÇİŞ | Diğer durumlar |

Eşikler (%40 / %80, EMA200, 20 gün) yaygın kullanılan sabitlerdir. **Veriden öğrenilmedi.**

**Sepet getirisi, ay başındaki bütün üyeleri tutar.** Yalnızca sona kadar yaşayanların ortalamasını almak hayatta kalma yanlılığı olurdu. Bir üye başarısız olursa önceden belirlenmiş şu kurallar uygulanır:

| Durum | İşlem |
|---|---|
| Ay içinde bir daha açılmamak üzere kapanan çift | Son kapanışından satılır. LUNA 2022-05'te yaklaşık −%100 sayılır; eksik sayılmaz. |
| Coine özgü bir boşluktan sonra geri dönen çift (olası token değişimi) | Eski token, boşluktan önceki son kapanışından satılır. |
| Ay başından önceki gün hiç işlem görmemiş çift | Ay başında alınamaz. Bu karar anında görülebilen bir bilgidir, sonradan bilinen değil. Çift sepete alınmaz ve `not_trading_at_start` olarak sayılır. Örnek: 2019-12'de BCHABC. |
| Ay sonunda kapanışı olmayan ama kırılmasız işlem görmeye devam eden çift | Sonuç bilinmez (`unknown_returns`). O ayın sepet getirisi n/a olur. |

Bilinen sınır: 2018-11'deki BCH çatallanmasında BCC, çatallanma öncesindeki son kapanışından satılmış sayılır. Sahiplerine verilen ABC ve SV coinlerinin sonraki çöküşü bu hesaba girmez.

**Eksik veri eksik kalır.**
- Sepet ölçüleri en az 8 üye ister.
- Oynaklık ve korelasyon en az 20 gün ister.
- Eksik olan ölçü n/a olarak gösterilir, asla 0 olarak gösterilmez.
- 2018-08'den önce Binance'te 8'den az USDT çifti vardı, o yüzden sepet ölçüleri yoktur. BTC ölçüleri 2017-10'dan, BTC trendi 2018-04'ten başlar.

### 2. Makro (FRED)

Seriler GitHub Actions'ta indirilir, çünkü bu konteyner FRED'e erişemiyor. API anahtarı ya da sır gerekmez.

**Yalnızca yayımlandıktan sonra revize edilmeyen seriler kullanılır.**
- FRED her zaman serinin güncel sürümünü verir.
- Revize edilen bir seri, mühürlü 2024-09 → 2026-08 penceresinde yayımlanmış düzeltmeleri geçmişe taşır. Hipotez seçimine test döneminin bilgisi sızar.
- Bu yüzden M2, ticaret ağırlıklı dolar endeksi, GDP, CPI ve istihdam gibi seriler atlasta yoktur. Ancak 2024-08-31 itibarıyla geçerli ALFRED sürümleriyle eklenebilirler.
- Kod bunu zorlar: `REVISED_SERIES` listesindeki bir seri istenirse `macro` komutu çalışmayı reddeder.
- `build`, bu kodun şemasına uymayan bir makro tabloyu reddeder. Örneğin Brent ve USD/CNY eklenmeden önce üretilmiş bir tablo. Bir ayı eksik olan tablo da reddedilir.

| Alan | FRED | Birim | Neden revize edilmez |
|---|---|---|---|
| ABD 2 yıllık / 10 yıllık faiz | DGS2 / DGS10 | % | Piyasa faizi (H.15) |
| 10 yıllık reel faiz | DFII10 | % | Piyasa faizi (H.15) |
| Fed politika faizi (üst sınır) | DFEDTARU | % | Politika kararı |
| Fed bilançosu | WALCL | milyon $ | H.4.1 kaydı |
| Hazine hesabı (TGA) | WTREGEN | milyar $ | H.4.1 kaydı |
| Ters repo (RRP) | RRPONTSYD | milyar $ | NY Fed işlem sonucu |
| EUR/USD | DEXUSEU | $ / € | Piyasa kuru (H.10). Değer düşerse dolar güçleniyor demektir. |
| USD/CNY | DEXCHUS | ¥ / $ | Piyasa kuru (H.10). Ticaret savaşının kanalı (2018–2019 tarifeleri, 2019-08 devalüasyonu). Doğrulama 2026-10'da başlar. |
| Brent petrol | DCOILBRENTEU | $ / varil | Spot fiyat (EIA). Orta Doğu geriliminin piyasaya geçtiği kanal. Doğrulama 2026-10'da başlar. |
| VIX | VIXCLS | endeks | Piyasa verisi |
| Nasdaq / S&P 500 | NASDAQCOM / SP500 | endeks | Piyasa verisi |

H.4.1 kayıtlarında nadir düzeltmeler olabilir. Bu küçük bir artık risktir.

**Türetilen alanlar:**
- **Net likidite** = Fed bilançosu − TGA − RRP. Birimler milyar $'a çevrilir.
- **Getiri eğrisi** = 10 yıllık − 2 yıllık.
- **Fed hamlesi** = Ay içindeki politika faizi değişikliği, baz puan.

**Sınırlar:**
- Ay sonu değeri, o güne kadarki son gözlemdir. 40 günden eski bir gözlem kullanılmaz, eksik sayılır.
- Net likidite, likiditenin kaba bir ölçüsüdür. Hazine ihracı, itfa ve SOMA vadeleri gibi akışları içermez (AGENTS.md §6).

### 3. Takvim

- **Olay anında bilinenler:** Bitcoin halvingleri ve Fed faiz değişiklikleri.
- **Sonradan bilinenler:** Kısa bir kripto şokları listesi; örneğin LUNA, FTX, SVB, spot ETF onayı.
  - Atlasta "(sonradan bilinen)" etiketiyle yalnızca bağlam olarak gösterilir.
  - **Hiçbir hesaba girdi olmaz.**
  - Bir şoku önceden bilmek mümkün değildir. Bu yüzden şoklar bir hipotezin girdisi olamaz. Hipotez ancak şokun ardından görülen gözlenebilir bir durumu kullanabilir (örneğin "oynaklık FIRTINALI'ya geçti").

## Dönemler

`phases` fonksiyonu, BTC trend durumu aynı kalan ardışık ayları tek bir dönemde birleştirir. Her dönem için şunları verir:
- BTC'nin ve sepetin bileşik getirisi;
- ortalama oynaklık ve ortalama korelasyon;
- reel faizdeki, net likiditedeki, dolardaki ve Nasdaq'taki değişim;
- dönemdeki Fed hamlelerinin toplamı;
- dönemdeki olaylar.

## Çalıştırma

```bash
# 1) Makro tablo (GitHub Actions → atlas_macro → Run workflow).
#    CSV, logda ATLAS_MACRO_BEGIN / ATLAS_MACRO_END arasında ve artifact olarak çıkar.
python -m trading.research.atlas macro --out research/data/atlas_macro.csv

# 2) Atlas. Veri setleri en eskiden yeniye verilir; aralarında boşluk olmamalıdır.
python -m trading.research.atlas build \
  --spot-dir <2017-09..2020-09 liquid-universe> \
  --spot-dir <2020-10..2024-08 liquid-universe> \
  --macro research/data/atlas_macro.csv --out atlas.json
```

`build`, aşağıdaki durumlarda çalışmayı reddeder:
- bir manifest eksikse ya da yarım kalmışsa;
- dosyalar eksikse;
- veri setlerinin arasında boşluk varsa;
- veri setleri mühürlü pencereye uzanıyorsa.

`--macro` verilmezse makro sütunları eksik kalır, sıfır yazılmaz.

## Sonuçlar: piyasa katmanı (2026-10-01)

Aylık veri `research/atlas/market_monthly.csv` dosyasındadır: 84 ay, evren, ölçüler ve takvim. Makro katman, `atlas_macro` iş akışı çalıştıktan sonra eklenecek.

**Bu bölümdeki her şey keşiftir, kanıt değildir.**
- 83 ay, aslında 2 büyük döngü ve birkaç bağımsız bölümdür.
- Aşağıdaki her karşılaştırma bir "bakış" sayılır.
- Buradan çıkan bir fikir yalnızca ön-kayda girip 2024-09 → 2026-08 penceresinde bir kez test edilirse kanıt olur.

### Dönem kimlik kartları

BTC'nin ay sonundaki trend durumuna göre gruplanmıştır. Durum ay sonunda ölçülür, yani o ayın hareketini de içerir; bu tablo yalnızca açıklama içindir.

| Dönem | Ay | BTC | Sepet | BTC oynaklığı | Korelasyon | Olaylar (sonradan bilinen) |
|---|---:|---:|---:|---:|---:|---|
| (trend ölçülemiyor) 2017-09 → 2018-03 | 7 | n/a | n/a | %115 | n/a | Çin ICO yasağı, CME vadelileri ve BTC zirvesi, Coincheck |
| DÜŞÜŞ 2018-05 → 2019-03 | 11 | −%56 | n/a | %57 | 0.78 | BCH çatallanması ve çöküş |
| YÜKSELİŞ 2019-04 → 2019-08 | 5 | +%134 | −%6 | %83 | 0.69 | Binance hack, Libra |
| DÜŞÜŞ 2019-11 → 2019-12 | 2 | −%21 | −%29 | %50 | 0.76 | |
| DÜŞÜŞ 2020-03 | 1 | −%25 | −%33 | %207 | 0.97 | COVID çöküşü |
| YÜKSELİŞ 2020-05 → 2021-04 | 12 | +%569 | +%1344 | %65 | 0.60 | Halving, MicroStrategy, PayPal, Tesla, Coinbase |
| DÜŞÜŞ 2021-05 → 2021-06 | 2 | −%39 | −%48 | %106 | 0.85 | Çin baskısı |
| YÜKSELİŞ 2021-08 → 2021-11 | 4 | +%37 | +%85 | %67 | 0.54 | El Salvador, Çin yasağı |
| DÜŞÜŞ 2021-12 → 2022-02 | 3 | −%24 | −%44 | %67 | 0.76 | |
| DÜŞÜŞ 2022-04 → 2022-12 | 9 | −%64 | −%80 | %60 | 0.71 | LUNA, Celsius/3AC, Merge, FTX |
| YÜKSELİŞ 2023-01 → 2023-07 | 7 | +%77 | +%17 | %44 | 0.64 | SVB, BlackRock ETF başvurusu |
| YÜKSELİŞ 2023-10 → 2024-07 | 10 | +%140 | +%95 | %49 | 0.53 | Spot ETF onayı, halving |

Tabloda olmayan dönemler 1–2 aylık GEÇİŞ dönemleridir (2018-04, 2019-09/10, 2020-02, 2020-04, 2021-07, 2022-03, 2023-09, 2024-08) ve 2020-01, 2023-08 gibi tek aylık dönemlerdir. Hepsi `market_monthly.csv` dosyasında.

### Ay başında bilinen durum → o ayın getirisi

Bu bölüm hindsight içermez. Önceki ayın sonundaki durum, ay başında bilinen bilgidir.

| Ay başındaki BTC durumu | Ay | Sepet ort. | Sepet medyan | Pozitif ay | BTC ort. |
|---|---:|---:|---:|---:|---:|
| YÜKSELİŞ | 39 | +%8.1 | −%0.7 | 17 / 39 | +%8.2 |
| GEÇİŞ | 8 | +%1.6 | +%6.4 | 4 / 7 | −%2.1 |
| DÜŞÜŞ | 29 | −%2.3 | −%1.2 | 12 / 27 | +%1.1 |

**Gözlemler:**

1. **Asimetri tipik ayda değil, uçlardadır.**
   - Tipik ayda trend durumu neredeyse hiçbir şey söylemiyor: YÜKSELİŞ'te bile medyan ay negatif.
   - Ama en iyi 5 sepet ayının **hepsinden** önce durum YÜKSELİŞ'ti: 2021-01 +%108, 2021-02 +%77, 2021-04 +%70, 2019-05 +%51, 2020-11 +%46.
   - En kötü 5 sepet ayının **hiçbirinden** önce durum YÜKSELİŞ değildi:
     - DÜŞÜŞ'ten sonra gelenler: 2022-05 −%38, 2018-11 −%36, 2022-01 −%33.
     - GEÇİŞ'ten sonra gelenler: 2020-03 −%33, 2022-04 −%31.
   - Bu, "trend bozulunca çık" döngüsünün keşif ve ön-tarih testlerindeki sonucuyla uyumlu. Girişin zamanlaması rastgeleden iyi değildi; değer, büyük çöküşlerin dışında kalmaktan geliyordu.
2. **Düşüşte çeşitlendirme çalışmıyor.** Ay başındaki duruma göre, o ayın majörler arası ortalama korelasyonu: YÜKSELİŞ'ten sonra 0.61 (39 ay), GEÇİŞ'ten sonra 0.71 (7 ay), DÜŞÜŞ'ten sonra 0.74 (27 ay). Aynı ayın etiketine göre bakıldığında bu değerler 0.60 ve 0.76 olur; ama o etiket ayın kendi hareketini içerdiği için yalnızca açıklama içindir. COVID ayında korelasyon 0.97'ye çıktı. Çöküşte her şey birlikte düşüyor.
3. **Altcoin sezonu nadir ve kısa.** Altcoinlerin BTC'yi 15 puandan fazla geçtiği ay sayısı 8:
   - 2021'de 4 ay: 2021-01, 2021-02, 2021-04, 2021-08;
   - 2019-02 ve 2019-03;
   - 2020-07 ve 2024-05.
   - BTC'nin hacim payı 2021'de %32'ye indi; 2024'te %39, diğer yıllarda %52–58.
   - YÜKSELİŞ'te ortalama ay "altcoinler eksi BTC" −0.1 puan, medyan −4.0 puan. Yani altcoinler tipik ayda BTC'nin gerisinde kalıyor, fark birkaç uç ayda kapanıyor.
4. **Derin dip bölgesi.** Ay başında BTC zirveden %70'ten fazla aşağıdaysa, sonraki ay sepet ortalaması +%14 ve 11 ayın 7'si pozitif. Ama bu 11 ay aslında iki bölüm (2018-12 → 2019-05 ve 2022-07 → 2023-01). **Kanıt değil.**
5. **Sakin oynaklık.** BTC oynaklığı SAKİN (< %40) olan aylardan sonra BTC ortalaması +%8.5, 10 ayın 6'sı pozitif. 10 ay çok az. **Kanıt değil.**

### Sınırlar

- 2018-08'den önce Binance'te 8'den az USDT çifti vardı. Bu yüzden 2017-09 → 2018-07 arasında sepet ölçüsü yok. 2017 balonu yalnızca BTC üzerinden görülüyor (2017-12'de BTC oynaklığı %155).
- Trend durumu ve oynaklık eşikleri yaygın kullanılan sabitlerdir, veriden öğrenilmedi. Yine de bunlara bakarak bir eşik "seçmek", keşif sayılır.
- Ortalamalar birkaç uç ay tarafından sürükleniyor. Medyanlar ve pozitif ay sayıları bu yüzden yanlarında verildi.
