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

## Katmanlar

### 1. Piyasa (Binance spot)

**Kaynak.** Kapanmış 15 dakikalık mumlar günlük muma çevrilir. Günün kapanışı, o günün bilinen son 15 dakikalık kapanışıdır. Hiç verisi olmayan gün bilinmez kalır.

**Evren.** Her ay için, o ayın başında görülebilen verilerle majör kuralı uygulanır (`docs/MAJORS_STUDY.md`):
- Sabit üyeler: BTC ve ETH.
- 30 günlük hacme göre ilk 10 coin.
- En büyük meme coin.
- Bir coinin evrene girmesi için en az 90 günlük geçmişi olmalı.

İki fark vardır:
- **Perp şartı yoktur.** Binance'te perp 2019-09'da başladı.
- **Borsa çapındaki duruşlar kırılma sayılmaz.** Bir coindeki boşluk BTC'de de aynı anda varsa (±1 gün), bu borsanın durmasıdır, token değişimi değildir. Örnek: Binance, 2018-02-08..10, 54 saat. Coine özgü bir boşluk ise yeni bir token gibi ele alınır. O boşluğun üzerinden getiri, trend ya da ortalama hesaplanmaz. Aynı kural iki veri setinin birleştiği yerde de uygulanır.

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

**Eksik veri eksik kalır.**
- Sepet ölçüleri en az 8 üye ister.
- Oynaklık ve korelasyon en az 20 gün ister.
- Eksik olan ölçü n/a olarak gösterilir, asla 0 olarak gösterilmez.
- 2018-08'den önce Binance'te 8'den az USDT çifti vardı, o yüzden sepet ölçüleri yoktur. BTC ölçüleri 2017-10'dan, BTC trendi 2018-04'ten başlar.

### 2. Makro (FRED)

Seriler GitHub Actions'ta indirilir, çünkü bu konteyner FRED'e erişemiyor. API anahtarı ya da sır gerekmez.

| Alan | FRED | Birim |
|---|---|---|
| ABD 2 yıllık / 10 yıllık faiz | DGS2 / DGS10 | % |
| 10 yıllık reel faiz | DFII10 | % |
| Fed politika faizi (üst sınır) | DFEDTARU | % |
| Fed bilançosu | WALCL | milyon $ |
| Hazine hesabı (TGA) | WTREGEN | milyar $ |
| Ters repo (RRP) | RRPONTSYD | milyar $ |
| Dolar endeksi (geniş) | DTWEXBGS | endeks |
| VIX | VIXCLS | endeks |
| Nasdaq / S&P 500 | NASDAQCOM / SP500 | endeks |
| M2 | M2SL | milyar $ |

**Türetilen alanlar:**
- **Net likidite** = Fed bilançosu − TGA − RRP. Birimler milyar $'a çevrilir.
- **Getiri eğrisi** = 10 yıllık − 2 yıllık.
- **Fed hamlesi** = Ay içindeki politika faizi değişikliği, baz puan.

**Sınırlar:**
- Ay sonu değeri, o güne kadarki son gözlemdir. 40 günden eski bir gözlem kullanılmaz, eksik sayılır. M2 aylık bir seri olduğu için onda bu sınır 60 gündür.
- **FRED'in en son sürümü kullanılır. Bu nokta-zamanlı değildir.**
  - M2 ve bilanço gibi seriler sonradan revize edilir.
  - M2 bir ay sonra yayımlanır.
  - Açıklama yapmak için bu yeterlidir. Ama bu verilerden çıkan bir hipotez, revizyon geçmişini tutan `macro_backfill` verisiyle test edilmelidir.
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

## Sonuçlar

Makro tablo çıktıktan sonra eklenecek.
