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

## Sonuç

_Henüz koşulmadı._
