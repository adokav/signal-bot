# Kısa vadeli kesitsel geri dönüş — örneklem dışı test protokolü (2017–2020)

**Ön-kayıt tarihi:** 2026-10-01. Bu belge ve karar kuralları **hiçbir sonuç
görülmeden** yazıldı.
**Deneme:** `cross_sectional_reversal_spot` ailesi, trial `6a2bc19afcf3dd25`
(`research/trials/registry.jsonl`), kod parmak izi `bd5caac74ad2ef19`.
**Kod:** `trading/backtest/reversal_replay.py`, `trading/strategies/reversal_dossier.py`,
workflow `reversal_replay`.

Bu bir araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Neden bu test, neden bu dönem

Likit-100 replay'inde (2020-10 → 2026-08) en güçlü yükselen coinler sonraki
12–72 saatte eşit ağırlıklı sepetin gerisinde kaldı. Bu bulguyu **aynı
veride** strateji olarak test etmek döngüsel olur: hipotez o veriden çıktı.

Bu yüzden test, bu depodaki hiçbir denemenin bakmadığı **2017-09 → 2020-09**
Binance spot verisinde yapılır.
- Evren oluşturucu 2020-09-30'dan sonra yayımlanan hiçbir veriyi okumaz.
- Test, veri bu tarihi aşarsa çalışmayı reddeder.

## Kural

- Her gün 00:00 UTC'de, son 24 saatin hacmine göre ilk 100 USDT çifti seçilir:
  ≥ 1M USD hacim, canlı Likit-100 kimlik kuralları, borsadan kalkanlar dahil.
- 30'dan az likit çift olan günlerde işlem yapılmaz.
- Çiftler son 24 saatlik getiriye göre sıralanır.
- **Alt %10** (en çok düşenler, en az 3 coin) eşit ağırlıkla alınır. Giriş,
  00:00'dan sonraki ilk 15 dakikalık mumun açılışından yapılır.
- Pozisyon **1 gün** (günlük kohort) ya da **3 gün** tutulur. 3 günlük
  kohortlar üç günde bir kurulur, yani üst üste binmez.
- Her kohort tam bir gidiş-dönüş maliyeti öder: taraf başına 7.5 bp komisyon
  + 5 bp slippage = %0.25. Stres senaryosu: komisyon ×1.5, slippage ×2 = %0.425.
- Borsadan kalkan coin, son işlem fiyatından çıkar.

## Karar kuralları (ön-kayıtlı)

Karar grupları **LOSERS@1g** ve **LOSERS@3g** (uzun vadede uygulanabilir
olanlar). Bonferroni α = 0.05 / 2 = **0.025**.

Bir grup **PASS_CANDIDATE** olur, ancak ve ancak:

1. ≥ **200** kohort;
2. ortalama net fazla getirinin (maliyet sonrası getiri − aynı penceredeki
   eşit ağırlıklı evren getirisi) 10 kohortluk blok bootstrap **%97.5 alt
   sınırı > 0**;
3. stres maliyetiyle ortalama fazla getiri **ve** ortalama net getiri **> 0**
   (piyasayı yenip yine de para kaybetmek geçmez);
4. kronolojik **iki yarının** ortalama net fazla getirisi ayrı ayrı **> 0**.

Diğer durumlar: üst sınır < 0 → **NEGATIVE**; < 200 kohort →
**INSUFFICIENT**; kalan → **NO_EDGE**.

**WINNERS** (en çok yükselen %10) yalnızca tanılayıcıdır. Kaynak bulgu doğruysa
sepetin gerisinde kalmaları beklenir. Yıl kırılımı da tanılayıcıdır.

## Sonucun anlamı

- **PASS_CANDIDATE:** Geri dönüş etkisi, hipotezi doğuran veriden bağımsız
  bir dönemde de maliyet sonrası var. Sıradaki adım, bugünkü veride kâğıt
  üzerinde ileri kayıttır. 2017–2020 piyasası bugünkünden farklıdır.
- **NO_EDGE / NEGATIVE:** Likit-100'deki bulgu genellenemez. Ya o döneme
  özgüydü ya da maliyet onu siler.

## Bilinen sınırlar

- **Piyasa dönemi:** 2017–2018 ICO dönemiydi; likidite düşüktü ve 2018 sert
  bir ayı piyasasıydı.
- **Uygulama yeri:** Binance spot kullanıldı; bugünkü uygulama MEXC'de olur.
- **Maliyet:** Her kohort tam gidiş-dönüş öder. Aynı coin ertesi gün de
  seçilse bile yeniden alınmış sayılır; bu muhafazakâr bir varsayım.
- **Kayma:** En çok düşen coinlerde gerçek kayma 5 bp'den yüksek olabilir.

## Değişmezlik

Parmak izi şu dosyalardan hesaplanır: `acce_unified/liquid_long.py`, `cex.py`,
`models.py`, `trading/data/binance_vision.py`, `binance_universe.py`,
`trading/backtest/liquid_replay.py`, `reversal_replay.py`. Bunlardan biri
değişirse `tests/test_reversal_replay.py::test_reversal_replay_is_pre_registered_for_the_current_code`
kırılır.

## Sonuç

_Henüz koşulmadı._
