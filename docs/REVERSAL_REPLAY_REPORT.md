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

## Sonuç — reversal_replay run #1 (2026-10-01): **NEGATIVE**

Koşu: GitHub Actions `reversal_replay` #1, commit `0256c81`, parmak izi
`bd5caac74ad2ef19` (ön-kayıtlı trial ile eşleşti).

**Veri:**
- 165 aday çift; 8'inin verisi pencere bitmeden duruyor. Bunlara borsadan
  kalkanlar ve coin değişimiyle kapananlar dahil: BCC, BCHABC, BCHSV, VEN,
  ERD, STORM, BULL, BEAR.
- 733 bozuk/tekrarlı mum satırı atıldı.

**Etkin dönem pencereden kısa.** 2019-04'e kadar Binance'te 1M USD hacmi
geçen 30 USDT çifti yoktu, bu yüzden ilk işlem günü **2019-04-03**.
- Ön-kayıtlı kural, 30'dan az likit çift olan günleri atlıyor.
- Sonuç olarak test fiilen 2019-04 → 2020-09 dönemini kapsıyor: 368 işlem
  günü (2019: 111, 2020: 257).
- Evren ortalaması 49 çift; uç dilimde 4–5 coin var.

Fazla getiri = net getiri − aynı penceredeki eşit ağırlıklı evren getirisi
(yüzde, kohort başına).

| Grup | Kohort | Brüt | Net | Sepet | **Fazla** | %97.5 güven | Stres fazla | 1. yarı | 2. yarı | Karar |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---|
| LOSERS@1g | 368 | −0.29 | −0.54 | +0.13 | **−0.671** | [−1.05, −0.29] | −0.85 | −0.52 | −0.83 | **NEGATIVE** |
| LOSERS@3g | 123 | +0.78 | +0.53 | +0.32 | +0.217 | [−0.98, +1.61] | +0.04 | +1.27 | −0.82 | **INSUFFICIENT** |
| WINNERS@1g (tanı) | 368 | +0.20 | −0.05 | +0.13 | −0.185 | [−0.81, +0.41] | −0.36 | −0.27 | −0.10 | — |
| WINNERS@3g (tanı) | 123 | −0.29 | −0.54 | +0.32 | −0.858 | [−2.30, +0.45] | −1.03 | −1.05 | −0.67 | — |

**Yıl bazında LOSERS@1g (tanılayıcı):** 2019 −0.88 [−1.62, −0.09], 2020
−0.58 [−0.99, −0.15]. İki yıl da negatif.

### Ön-kayıtlı kurallara göre değerlendirme

- **LOSERS@1g: NEGATIVE.**
  - En çok düşenleri ertesi gün almak, maliyet sonrası sepetin günde
    yaklaşık %0.67 gerisinde kaldı. Güven aralığının üst sınırı sıfırın
    altında.
  - Maliyet öncesi bile fark yaklaşık −%0.42/gün: düşenler düşmeye devam
    ediyor.
- **LOSERS@3g: INSUFFICIENT.** 200 kohort eşiğinin altında (123). Nokta
  tahmini pozitif ama aralık çok geniş ve iki yarı zıt işaretli. Hiçbir
  iddia yapılmaz.
  - Not: Etkin dönem 368 gün olunca üst üste binmeyen 3 günlük kohort sayısı
    en fazla yaklaşık 123 olabilir. Yani eşik bu veriyle zaten
    ulaşılamazdı. Bu, sonuçtan sonra fark edildi. Eşik değiştirilmez.

### Veri kontrolü: kimlik kuralından kaçan semboller

Sonuçtan sonra evren listesinde, canlı kimlik kurallarının yakalamadığı üç
sembol bulundu:

- **BULLUSDT / BEARUSDT:** Binance'in BTC için 3x long / 3x short
  kaldıraçlı tokenları (2020-01 → 2020-03). Adlarında "BTC" kökü olmadığı
  için `is_leveraged_token` bunları tanımadı.
- **PAXUSDT:** Paxos Standard, dolar stabil coini (sonradan adı USDP oldu).
  `is_stable_or_synthetic("PAX")` false döndürüyor. Piyasa sert düştüğü
  günlerde WINNERS'a, sert yükseldiği günlerde LOSERS'a düşüyor.

Etkilenen kohortlar: LOSERS@1g'de 49 (BULL/BEAR) ve 40 (PAX).

**Yerel yeniden üretim ve kontrol (tanılayıcı, karar değiştirmez):**
- Veri aynı kodla (S3 uç noktasından) yerelde yeniden indirildi. CI'daki
  tablo **birebir** üretildi.
- Bu üç sembol evrenden çıkarılarak koşu tekrarlandı:

| Grup | Kohort | Fazla | %97.5 güven | Karar |
|---|---:|---:|---|---|
| LOSERS@1g (üçü çıkarılmış) | 346 | −0.699 | [−1.09, −0.30] | NEGATIVE |
| LOSERS@3g (üçü çıkarılmış) | 116 | −0.011 | [−1.12, +1.29] | INSUFFICIENT |

Kirlenme sonucu belirlemiyor. Kayıtlı karar CI koşusununki olarak kalır.

- **Canlı kurallar neden değiştirilmedi:**
  - `acce_unified/cex.py` canlı Likit-100 radarının ve Likit-100 kanıt
    parmak izinin parçası. Değişirse canlı kanıt UNKNOWN olur ve kapı
    REJECT'ten WATCH'a gevşer.
  - Ayrıca bugün MEXC'de "BULL"/"BEAR" adlı gerçek coin'ler olabilir;
    çıplak adı kaldıraçlı saymak onları yanlışlıkla dışlar.
  - Bu Binance-geçmişine özgü istisnalar sonraki denemelerde ayrı, kendi
    parmak izi olan bir veri katmanında ele alınacak.

### Sonucun anlamı

- **Likit-100'deki "yükselenler geride kalıyor" bulgusu bu dönemde ayna
  olarak çalışmıyor.** Likit coin'lerde 1 günlük ufukta geri dönüş yok,
  tersine düşenlerde devam (momentum) var. Bu, literatürün beklentisiyle
  uyumlu: Zaremba ve diğ. (2021), günlük geri dönüşün likit olmayan
  coin'lerden geldiğini, en likit coin'lerde günlük momentum olduğunu
  buldu. Beklenti `docs/EDGE_RESEARCH.md` içinde sonuçtan önce yazılmıştı.
- **WINNERS (tanılayıcı) bu dönemde net değil.** 1 ve 3 günde nokta tahmini
  negatif, ama aralıklar sıfırı içeriyor.
- **Long-only sistem için çıkarım: kaçınma.** Likit evrende son 24 saatin
  en çok düşenlerini ertesi gün için "dipten almak" bu dönemde zarar
  ettirdi. Ters yönü (düşenleri açığa satmak) emir yetkisi olmayan bu
  sistemin kapsamı dışında.
- Bu dönemden çıkan "düşenler düşmeye devam eder" gözlemi yeni bir
  hipotezdir. Test edilecekse başka bir dönemde (2020-10 sonrası) ve
  ön-kayıtla yapılmalı.
