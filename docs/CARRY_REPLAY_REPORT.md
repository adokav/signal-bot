# Funding carry (spot long + perp short) — tarihsel replay protokolü

**Ön-kayıt tarihi:** 2026-10-01. Bu belge ve karar kuralları **hiçbir sonuç
görülmeden** yazıldı.
**Deneme:** `funding_carry_spot_perp` ailesi, trial `e8d38a2af0f661de`
(`research/trials/registry.jsonl`), kod parmak izi `06e4a9834b8ba328`.
**Kod:** `trading/data/binance_carry_universe.py`, `trading/backtest/carry_replay.py`,
`trading/strategies/carry_dossier.py`, workflow `carry_replay`.

Bu bir araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Hipotez

Perpetual kontratlarda kaldıraçlı long talebi, funding'i ortalamada pozitif
yapar. Spot alıp aynı miktar perp satan delta-nötr bir pozisyon, fiyat
yönünden bağımsız olarak bu ödemeyi toplar. Literatür primin var olduğunu ama
çökme riski taşıdığını raporluyor. Bu deneyin sorusu şu: dört bacaklı işlem
maliyetinden sonra prim kalıyor mu?

## Zaman sırası (geleceğe bakmama)

- Karar, her 8 saatlik funding sınırında (00/08/16 UTC) alınır.
- Karar yalnızca o ana kadar **ödenmiş** funding'i ve **kapanmış** mumları
  görür.
- Pozisyon, sınırdan sonra başlayan mumun açılışında açılır. Böylece ilk
  funding geliri bir sonraki ödemedir; kararı veren funding pozisyona yazılmaz.

## Survivorship'ten arındırılmış evren

1. data.binance.vision'daki bütün USDⓈ-M perp'ler ve spot çiftler listelenir
   (borsadan kalkanlar dahil).
2. USDT perp'ler, stabil coin değilse spot karşılığına eşlenir
   (`1000PEPEUSDT → PEPEUSDT`; getiri oran olduğu için kontrat çarpanı önemsiz).
3. Günlük perp hacim sırası ≤ 40 olan aylar için 8 saatlik perp ve spot
   mumları ile funding indirilir.
4. Her karar anında evren, son 30 günün perp hacmine göre ilk 20 olarak
   yeniden hesaplanır. Verisi biten çift o mumun kapanışından çıkar.

## Varyantlar

| Varyant | Kural |
|---|---|
| **STATIC_CARRY** | Evrendeki 20 perp'in hepsinde carry tutulur |
| **SIGNED_CARRY** | Aynı evren; yalnızca son 7 günün funding toplamı > 0 olan çiftler (tam bir haftalık funding geçmişi şart) |

İki varyantın farkı, işaret filtresinin katkısını doğrudan ölçer.

## Protokol (sabit)

| Konu | Seçim |
|---|---|
| Veri | Binance USDⓈ-M perp + Binance spot, 8 saatlik mumlar, funding; 72 ay |
| Ağırlık | Her dönem aktif pozisyonlar arasında eşit nominal |
| Getiri | Tek bacağın nominaline göre: spot getirisi − perp getirisi + alınan funding |
| Maliyet | Açılış veya kapanış başına: spot 7.5 bp + perp 5 bp komisyon + bacak başına 2 bp slippage (%0.165). Stres: komisyon ×1.5, slippage ×2 (%0.2675) |
| Güven aralığı | Günlük getiriler üzerinde 10 günlük blok bootstrap |

## Karar kuralları (ön-kayıtlı)

İki test (STATIC, SIGNED), Bonferroni α = 0.05 / 2 = **0.025**.

Bir varyant **PASS_CANDIDATE** olur, ancak ve ancak:

1. ≥ **500** pozisyon taşınan gün;
2. ortalama günlük net getirinin blok bootstrap **%97.5 alt sınırı > 0**;
3. stres maliyetiyle yıllık getiri **> 0**;
4. kronolojik **iki yarının** yıllık getirisi ayrı ayrı **> 0**.

Diğer durumlar: üst sınır < 0 → **NEGATIVE**; < 500 gün → **INSUFFICIENT**;
kalan → **NO_EDGE**.

Raporlanan tanılayıcılar (karar vermez):
- yıllık getiri, oynaklık, Sharpe, en büyük düşüş, en kötü gün;
- getirinin funding / baz / maliyet ayrıştırması;
- yıl bazında sonuç;
- negatif funding payı, zorunlu çıkışlar, funding boşlukları;
- **teminat stresi:** perp fiyatının girişin 1.5 ve 2 katına ulaştığı olaylar.

## PASS_CANDIDATE ne demek, ne demek değil

- **Demek:** carry primi bu protokolde maliyet sonrası istatistiksel olarak
  pozitif. Sıradaki adım, kullanıcının borsasında (MEXC spot + vadeli) kâğıt
  üzerinde ileri kayıttır.
- **Demek değil:** emir yetkisi. Canlıya geçmeden önce şunlar gerekir:
  - teminat yönetimi;
  - borsa başına sermaye tavanı;
  - kill switch;
  - borsa ile bot durumu arasında mutabakat (spec §27–§30).
- **NO_EDGE / NEGATIVE ise:** carry bu haliyle elenir. Sonuca bakarak
  eşik veya pencere değiştirip aynı veride yeniden denemek yeni bir denemedir
  ve kayıt defterinde sayılır.

## Bilinen sınırlar (her raporda tekrarlanır)

- **Borsa farkı:** Binance verisi; MEXC funding'i ve komisyonları farklıdır.
- **Tasfiye modellenmedi:** Kısa perp bacağı yükselişte teminat ister.
  Tasfiye hesaba katılmadı; bunun yerine teminat stresi olayları sayılıyor.
- **Sermaye gereksinimi:** Getiri tek bacağın nominaline göre. 1x teminatlı
  hedge iki katı sermaye ister, yani sermayeye göre yıllık getiri yarıya iner.
- **Yeniden dengeleme:** Dönem içi ağırlık sapması ve yeniden dengeleme
  maliyeti modellenmedi.
- **Karşı taraf riski:** İki bacak aynı borsada. Borsa iflası (FTX tipi)
  bu replay'de görünmez.

## Değişmezlik

Parmak izi şu dosyalardan hesaplanır: `acce_unified/cex.py`,
`trading/data/binance_vision.py`, `trading/data/binance_universe.py`,
`trading/data/binance_carry_universe.py`, `trading/backtest/carry_replay.py`.
Bunlardan biri değişirse
`tests/test_carry_replay.py::test_carry_replay_is_pre_registered_for_the_current_code`
kırılır.

## Sonuç — carry_replay run #1 (2026-10-01)

Koşu: GitHub Actions `carry_replay` #1, commit `0c4432e`, parmak izi
`06e4a9834b8ba328` (ön-kayıtlı trial ile eşleşti).

**Veri:** 469 perp/spot çifti; 62'sinin verisi pencere bitmeden duruyor
(borsadan kalkanlar dahil). 6.483 sekiz saatlik dönem, ≈ 2.130 gün. Bozuk
satır yok.

Getiriler **tek bacağın nominaline göre** ve yıllıktır. 1x teminatla gereken
sermaye bunun iki katıdır, yani sermayeye göre getiri yarıya iner.

| | STATIC_CARRY | **SIGNED_CARRY** |
|---|---:|---:|
| Pozisyonlu gün | 2.131 | 2.128 |
| Yıllık net getiri | −7.47% | **+7.76%** |
| %97.5 güven (yıllık) | [−16.34, +0.36] | **[+3.62, +12.35]** |
| Stres maliyetiyle | −9.12% | +3.13% |
| 1. yarı / 2. yarı | −0.14 / −14.80 | +12.38 / +3.14 |
| Oynaklık, Sharpe | 4.75%, −1.57 | 2.26%, 3.43 |
| En büyük düşüş, en kötü gün | **−82.98%**, −5.20% | −8.81%, −0.57% |
| Funding / baz / maliyet | −2.35 / −2.47 / 2.65 | +14.21 / +1.00 / **7.45** |
| Ort. pozisyon, giriş sayısı | 20.0, 948 | 14.4, 1.597 |
| Negatif funding payı | %25 | %12 |
| Teminat stresi (1.5× / 2×) | 155 / 74 | 163 / 68 |
| **Karar** | **NO_EDGE** | **PASS_CANDIDATE** |

**Yıl bazında (yıllık, nominale göre):**

| Varyant | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026* |
|---|---:|---:|---:|---:|---:|---:|---:|
| STATIC | +16.2 | +36.4 | −25.3 | −12.2 | +10.1 | −24.5 | −46.4 |
| SIGNED | +25.2 | +38.9 | −7.1 | +5.6 | +11.9 | −2.1 | −9.5 |

\* 2026 yalnızca Ocak–Ağustos.

### Ön-kayıtlı kurallara göre değerlendirme

- **SIGNED_CARRY: PASS_CANDIDATE.** Dört kuralın dördü de sağlandı: 500'den
  fazla gün, güven aralığının alt sınırı sıfırın üstünde, stres maliyetiyle
  pozitif, iki yarı da pozitif. Bu sistemde ön-kayıtlı kuralları geçen
  **ilk** deneme.
- **STATIC_CARRY: NO_EDGE.** Güven aralığı sıfırı içeriyor, ikinci yarı
  derin negatif. Her durumda carry taşımak işe yaramıyor; funding işareti
  filtresi belirleyici.

### Sonucu nasıl okumalı — PASS bir "git" değil

1. **Prim son dönemde kayboldu.** SIGNED'in getirisinin büyük kısmı 2020–2021
   boğa piyasasından (+25%, +39%) ve 2024'ten (+12%) geliyor. 2025 (−2.1%) ve
   2026'nın ilk 8 ayı (−9.5%) negatif. Kural "iki yarı da pozitif" diyor ve
   ikinci yarı pozitif (+3.1%), ama bu tamamen 2024'e dayanıyor; son 20 ay
   zarar.
   - Olası açıklama (kanıtlanmadı): 2024'ten beri delta-nötr carry'yi büyük
     ölçekte yapan ürünler ve fonlar primi daralttı. Bu bir hipotezdir.
2. **Sermayeye göre getiri risksiz getirinin altında.** Getiri sermayeye göre
   yaklaşık **+3.9%/yıl** (stres maliyetiyle ≈ +1.6%). Aynı dönemde dolar
   cinsinden risksiz getiri (hazine bonosu / stabil coin faizi) çoğu yıl
   %4–5 civarındaydı. Bu getiri, borsa ve teminat riskini almaya değmiyor.
3. **Maliyet funding'in yarısını yiyor.** Funding +14.2%, maliyet −7.5%.
   Yüksek giriş-çıkış (1.597 giriş) bunun ana sebebi.
4. **Teminat riski gerçek.** 68 pozisyonda perp fiyatı girişin 2 katına
   ulaştı; 1x teminatla bu tasfiye demek. Replay tasfiyeyi modellemedi,
   yani gerçek sonuç daha kötü olabilir.
5. **STATIC'in −83% düşüşü bir veri denetimi gerektiriyor.** Delta-nötr bir
   portföyde bu büyüklükte düşüş ya yoğun short-squeeze/negatif funding
   dönemlerinden ya da veri hatasından gelir. Olası veri hatası örnekleri:
   aynı sembolün farklı bir coine yeniden verilmesi, spot ve perp'in farklı
   anlarda durması. SIGNED'in en kötü günü −0.57% olduğu için bu ondan
   etkilenmiş görünmüyor, ama doğrulanmadı.

### Protokole göre sonraki adım

PASS_CANDIDATE, emir yetkisi değil. Sıradaki adımlar:

1. **Veri denetimi:** En kötü/en iyi pozisyon dönemleri ve spot/perp fiyat
   oranındaki sıçramalar incelenir. Tanılayıcıdır; deneme kodunu ve parmak
   izini değiştirmez.
2. **İleri kayıt:** Kullanıcının borsasında kâğıt üzerinde kayıt tutulur.
   Funding'in risksiz getirinin üstüne çıkıp çıkmadığı canlı izlenir.

Son dönemdeki zayıflık ve risksiz getiri karşılaştırması nedeniyle bu sonuç
**canlı sermaye için yeterli değil.**
