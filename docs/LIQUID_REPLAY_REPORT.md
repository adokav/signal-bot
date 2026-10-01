# Likit-100 Long radarı — tarihsel replay protokolü

**Ön-kayıt tarihi:** 2026-10-01. Bu belge ve karar kuralları **hiçbir sonuç
görülmeden** yazıldı.
**Deneme:** `liquid_100_long_radar` ailesi, trial `69f6387eaf32ed4f`
(`research/trials/registry.jsonl`), kod parmak izi `d809bd2bc219e681`.
**Kod:** `trading/data/binance_universe.py`, `trading/backtest/liquid_replay.py`,
`trading/strategies/liquid_dossier.py`, workflow `liquid_replay`.

Bu bir araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Ne ölçülüyor

Canlı `/longs` listesi (MEXC Likit-100 Long İlk 3), elle seçilmiş ağırlıklarla
çalışıyor ve hiç test edilmedi. Replay, canlı fonksiyonları
(`select_liquid_universe`, `build_market_context`, `select_enrichment_universe`,
`calculate_long_metrics`, `score_technical_long`) değiştirmeden geçmiş veride
çalıştırır ve seçilen coinlerin sonrasında ne yaptığını ölçer.

Radar stop/hedef üretmediği için ölçü **sabit süreli getiri**dir: kararın
ardından gelen 15 dakikalık mumun açılışından H saat sonraki kapanışa kadar.
İki sayı birlikte okunur:

- **net getiri** = brüt getiri − gidiş-dönüş maliyet;
- **fazla getiri** = net getiri − aynı pencerede eşit ağırlıklı evrenin
  (o anki ilk 100) brüt getirisi. Bu, seçim becerisini piyasa betasından
  ayırır (spec §20, §42).

## Survivorship'ten arındırılmış evren

1. data.binance.vision'daki **bütün** spot çiftler listelenir (borsadan
   kalkanlar dahil). USDT çiftlerinden stabil coinler ve kaldıraçlı tokenlar
   canlı radarın kurallarıyla çıkarılır.
2. Aylık 1d mumlarla günlük hacim sırası çıkarılır. Bir gün ilk 150'de ve
   ≥ 1M USD hacimdeyse o ay için aday olur. Bu adım yalnızca hangi verinin
   indirileceğini belirler.
3. Aday aylar ve bir önceki/sonraki ay için 15 dakikalık mumlar indirilir.
4. Her 15 dakikalık kapanışta gerçek ilk 100, **son 24 saatin** hacmiyle
   yeniden hesaplanır. Borsadan kalkan bir coin son işlem fiyatından çıkar.
   Boşluk veya indirme sınırı tahmin edilmez; o gözlem çözümsüz sayılır.

İndirmede 404 dışındaki her hata yeniden denenir. Yine başarısız olursa iş
durur: evrende sessiz bir delik bırakılmaz.

## Protokol (sabit)

| Konu | Seçim |
|---|---|
| Veri | Binance spot USDT çiftleri, data.binance.vision, 72 ay |
| Karar anı | Her 15 dakikalık kapanış (canlı tarama 2 dakika; metrikler zaten 15 dakikalık kapanmış mumlardan) |
| Canlı ayarlar | Evren 100, min hacim 1M USD, 24s filtre −%8/+%25, spread kapısı 35 bp, ilk 3 |
| Varsayımlar | Spread sabit 10 bp (geçmiş emir defteri yok); arz puanı ve min puan 64 uygulanamaz (aşağıda) |
| Olay | **TOP3**: coin ilk 3'e yeni girdiğinde; **ALL_READY**: coin teknik kapıların tamamını yeni geçtiğinde. CAPITULATION rejiminde liste boş (canlı gibi) |
| İnceltme | Aynı coin, açık gözlemi kapanmadan yeniden sayılmaz (ufuk başına) |
| Maliyet | Taban: taraf başına 5 bp komisyon + 5 bp slippage (gidiş-dönüş %0.20). Stres: komisyon ×1.5, slippage ×2 (%0.35) |
| Güven aralığı | Gün bazlı küme bootstrap: aynı gün seçilen coinler tek küme (ortak piyasa hareketi) |

## Karar kuralları (ön-kayıtlı)

Karar grupları: **TOP3@4s, TOP3@24s, ALL_READY@4s, ALL_READY@24s** = 4 test.
Çoklu test düzeltmesi: Bonferroni, α = 0.05 / 4 = **0.0125**.

Bir grup **PASS_CANDIDATE** olur, ancak ve ancak:

1. ≥ **300** inceltilmiş gözlem;
2. ortalama net fazla getirinin gün-kümeli bootstrap **%98.75 alt sınırı > 0**;
3. stres maliyetiyle ortalama fazla getiri **ve** ortalama net getiri **> 0**
   (piyasayı yenmek ama yine de para kaybetmek geçmez);
4. kronolojik **iki yarının** ortalama net fazla getirisi ayrı ayrı **> 0**.

Diğer durumlar: üst sınır < 0 → **NEGATIVE**; < 300 gözlem →
**INSUFFICIENT**; kalan → **NO_EDGE**. Rejim, yıl ve 1/12/72 saatlik ufuklar
yalnızca tanılayıcıdır (`DIAGNOSTIC_ONLY`).

## PASS_CANDIDATE ne demek, ne demek değil

- **Demek:** radarın **teknik katmanı** seçim becerisi gösteriyor. Canlıdaki
  arz katmanı ve min puan eşiği ayrıca ileri kayıtla doğrulanmadan sonuç
  canlıya genellenemez.
- **Demek değil:** emir yetkisi, pozisyon boyutu, stop/hedef planı.
- **NO_EDGE / NEGATIVE ise:** Likit-100 listesi canlıda REJECT/WATCH olarak
  etiketlenir. Sonuca bakarak ağırlıkları değiştirip aynı veride yeniden
  denemek yeni bir denemedir ve kayıt defterinde sayılır.

## Bilinen sınırlar (her raporda tekrarlanır)

- **Borsa farkı:** MEXC'nin ilk 100'ü, Binance'tekinden farklı coinler
  içerebilir. MEXC'ye özgü küçük coinler bu testte yok.
- **Test edilmeyen katman:** Arz puanı (CoinGecko, canlı nihai puanın %25'i)
  geçmişe dönük yok. Bu yüzden sıralama teknik puana göre yapılıyor ve min puan
  64 eşiği uygulanmıyor.
- **Spread:** Sabit varsayıldı; spread kapısı test edilemedi.
- **Ön-filtre yaklaşıklığı:** Günlük sırada 150'nin dışında kalıp gün içinde
  ilk 100'e giren nadir coinler kaçabilir.
- **Giriş fiyatı:** Bir sonraki mumun açılışından; hızlı yükselen coinlerde
  gerçek dolum daha kötü olabilir.

## Değişmezlik

Parmak izi şu dosyaların içeriğinden hesaplanır: `acce_unified/liquid_long.py`,
`acce_unified/cex.py`, `acce_unified/models.py`,
`trading/data/binance_universe.py`, `trading/backtest/liquid_replay.py`.
Bunlardan biri değişirse
`tests/test_liquid_replay.py::test_liquid_replay_is_pre_registered_for_the_current_code`
kırılır.

## Sonuç

_Henüz koşulmadı._
