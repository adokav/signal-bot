# Faz A — Backtest v1 Sonucu (BTCUSDT, ilk çalışma)

**Tarih:** 2026-09-23
**Sembol:** BTCUSDT
**Adapter:** `trading.data.binance_vision` (data.binance.vision monthly zip dumps)
**Hipotez:** TSMOM (60d lookback) + volatility targeting (%40 hedef, 3x leverage cap)
**Exit:** T1 (ATR × 1) + stop-to-entry + T2 (ATR × 2), 48h time-stop
**Overlap:** Bağımsız trade simülasyonu (aynı gün birden fazla long stack olabilir)

## Sonuç

```
strategy : net=-0.061%  sharpe=-0.31  hit=51.9%  maxdd=-122.57%
benchmark: net=+182.40% sharpe= 0.80  maxdd= -75.45%  (1095d B&H)
verdict  : NO-GO — sharpe>0.8:FAIL · beats_bnh:FAIL · dd<60%_bnh:FAIL · consistency:0/4
```

**Toplam 578 trade** — 3 yılda ~2 günde bir trade.

## Analiz

Dört GO/NO-GO kriterinin dördü de fail. Kırmızı senaryo. Kök nedenler:

### 1. Trade frekansı × cost drag = edge yok

- Round-trip cost = ~0.12% (fee 0.08% + slip 0.04%) + funding
- 578 trade × 0.12% = **cost-only drag ~%70 cumulative**
- Gross getiri tahminen ~%35 → net ~-%35 → mean/trade -0.061%

Sinyal doğru bile olsa exit paterni cost'u ödeyemiyor.

### 2. Overlapping trades → cumulative -%122 DD

`walk_forward` her günkü daily close'da LONG sinyali gelirse yeni bir trade
açıyor, açık pozisyonu göz ardı ediyor. 5-10 üst üste binen long → hepsi
aynı sermayeyi kullansa DD zaten -%100 civarında sınırlanır; kod bunları
bağımsız event olarak topladığı için cumulative equity curve -%122'ye
düştü. Matematiksel olarak "aynı sermayenin fazlasını risk et" varsayımı.

### 3. Vol targeting × 3x cap volatilite spike'ında patlıyor

40% vol hedefi / 15% realized BTC vol = 2.66× → cap 3.0 ile 2.66× açık.
Bir kötü hafta → aynı hafta içindeki 3-4 open trade × 2.66× kayıp katlanır.

### 4. Fold consistency 0/4

Hiçbir fold'da Sharpe > 0.5 değil. Dönem-spesifik bir edge yok, yapısal
bir sorun var (yukarıdaki 3 madde).

## Faz A2a — küçük düzeltmeler

Yeni parametreler:

| Parametre | v1 | v2a | Neden |
|---|---|---|---|
| `single_position` | False (overlap) | **True** | Overlap ban, cumulative DD gerçekçi olur |
| `max_leverage` | 3.0 | **1.0** | Vol targeting hala scale yapar ama cap 1x, patlama yok |
| `time_stop_seconds` | 48h | **7 gün** | Trend takibinde 48h çok kısa, whipsaw ve cost drag |
| `horizon_hours` | 96 | **168** | Time-stop 7 güne çıktı, hourly window uyumlu |

Beklenen etki:
- Trade sayısı ~578 → ~150-200 (yaklaşık %70 azalma)
- Cost drag ~%70 → ~%20-25
- Cumulative DD -%122 → -%30-50 (leverage 1x + tek pozisyon)
- Sharpe -0.31 → +0.2 ile +0.5 aralığı (tahmin)

**Hâlâ B&H'yi (Sharpe 0.80) geçmeyebilir.** Geçmezse Faz A2b → cross-sectional momentum.

## v2 sonucu — A2a fix'lerinden sonra (2026-09-23 21:20 UTC)

Aynı sembol (BTCUSDT), aynı 3 yıl vision data, single-position + max_leverage=1.0 +
7-day time-stop:

```
strategy : sharpe=-0.03  net=-0.009%/trade × 189 trades  maxdd=-46.12%  hit=55.6%
benchmark: sharpe= 0.80  net=+182%                       maxdd=-75.45%  (1095d B&H)
verdict  : NO-GO — sharpe>0.8:FAIL · beats_bnh:FAIL · dd<60%_bnh:FAIL · consistency:0/4
```

Exit reason breakdown:

| Reason | Count | Share |
|---|---:|---:|
| SERIES_END (data window expired) | 64 | 34% |
| SERIES_END_RESIDUAL | 28 | 15% |
| STOP | 40 | 21% |
| TARGET_2 | 34 | 18% |
| RESIDUAL_STOP_BREAKEVEN | 23 | 12% |

### v1 → v2 karşılaştırması

| Metric | v1 | v2 | Δ |
|---|---:|---:|---|
| Trades | 578 | 189 | -67% ✓ single-position + 7d time-stop |
| Sharpe | -0.31 | -0.03 | +0.28 ✓ ama hâlâ sıfıra yakın |
| Mean net/trade | -0.061% | -0.009% | 7× ✓ cost drag yenildi |
| Hit rate | 51.9% | 55.6% | +3.7 pp coinflip'e yakın |
| Max DD | -122% | -46% | ✓ leverage cap işini yaptı |
| Consistency | 0/4 | 0/4 | Fold-fold hâlâ yapısal |

### Yorum

Üç yapısal fix (single-position, leverage 1x, 7d time-stop) hedeflediği
sorunları çözdü. Ama **edge açılmadı** — Sharpe -0.03 istatistiksel olarak
"hiç trade yapmakla aynı". BTC son 3 yıl B&H'yi geçmek TSMOM long-only
single-symbol yaklaşımıyla mümkün değil.

## Karar: Faz A CLOSED — NO-GO

Faz A hipotezi (Time-series momentum + volatility targeting, tek sembol,
long-only, Binance perp) **çürütüldü**:

- v1 (agresif parametreler, overlapping trades): Sharpe -0.31
- v2 (muhafazakar parametreler, tek pozisyon): Sharpe -0.03

İki bağımsız parametre setinde de negatif Sharpe → parametre ayarı meselesi
değil, hipotez meselesi. Akademik literatürle tutarlı: 2020 sonrası kripto
tek-sembol TSMOM edge'i büyük ölçüde arbitraj edildi (institutional +
market-maker akışı, funding market'in olgunlaşması).

## Düzeltme (2026-09-30) — v2 sonucu karar için geçersiz

Quant spec revizyonu sırasında harness'ta v2 sonucunu etkileyen iki hata ve
bir tasarım karışıklığı bulundu. Yukarıdaki "CLOSED — NO-GO" kararı bu
yüzden **yeniden koşu yapılana kadar askıdadır**:

1. **Horizon < time-stop.** Workflow varsayılanı `horizon_hours=96` idi,
   strateji time-stop'u 168 saat. İşlemlerin %49'u (`SERIES_END` 64 +
   `SERIES_END_RESIDUAL` 28) stratejinin kendi kuralıyla değil, veri
   penceresinin kesilmesiyle kapandı. Harness artık bu durumda hata veriyor
   (fail closed); workflow varsayılanı 168.
2. **Maliyet ölçeklenmiyordu.** `simulate_trade` getiriyi vol-targeting
   ölçeği (`scale`) ile çarpıyor ama fee + slippage + funding'i tam notional
   üzerinden düşüyordu. v2'de `scale ≤ 1` olduğu için maliyet olduğundan
   fazla gösterildi — sonuç stratejinin aleyhine çarpıktı. Artık
   `net = (fiyat getirisi − birim maliyet) × scale`.
3. **Sharpe yıllıklandırması.** `sqrt(365 / ortalama tutma)` stratejinin
   hep pozisyonda olduğunu varsayıyordu; tek pozisyonlu ve arada düz kalan
   bir strateji için Sharpe büyüklüğünü şişirir. Artık gözlenen işlem
   frekansı (`n / takvim yılı`) kullanılıyor.
4. **Test edilen hipotez saf TSMOM değildi.** Literatürdeki TSMOM pozisyonu
   sinyal dönene kadar tutar. Bu harness girişi kısa vadeli bir ATR
   merdiveniyle (T1 = 1 ATR, T2 = 2 ATR, stop = 1.5 ATR) birleştiriyor; bu
   merdivenin trend takibi için ekonomik gerekçesi yok ve sağ kuyruğu
   kesiyor. Çürütülen şey en fazla "TSMOM girişi + swing çıkışı"
   kombinasyonudur. Saf TSMOM (sinyal-tabanlı çıkış) hiç test edilmedi.

Dürüst durum: v1 NO-GO'su geçerli (−%122 DD yapısal olarak kabul edilemez).
v2 "Sharpe −0.03" rakamı artık referans alınmamalı. Düzeltilmiş harness ile
aynı parametreler yeniden koşulmalı; bu **yeni bir deneme sayılmaz**
(`research/trials/registry.jsonl` içinde aynı `trial_id`). Saf TSMOM
denemesi yapılırsa o yeni bir denemedir ve deflated Sharpe'ta N artar.

Her backtest koşusu artık `backtest_SYMBOL.md` strateji raporunu da üretir:
hipotez + feature/parametre gerekçeleri, 11 kırılganlık sorusu ve zorunlu son
bölüm "WHAT COULD BLOW UP THIS ACCOUNT?" (`trading/strategies/tsmom_dossier.py`).

## Düzeltilmiş v2 yeniden koşusu — run #54 (2026-09-30 22:37 UTC)

Parametreler aynı (aynı `trial_id`, N=2), harness düzeltilmiş (horizon 168
saat, maliyet ölçekle çarpılıyor, Sharpe gözlenen işlem frekansıyla
yıllıklandırılıyor), parametre pertürbasyonu açık:

```
strategy : EV/trade=+0.249%  PF=1.19  sharpe=0.53  maxdd=-40.12%  win=66.7%  n=141 (ADEQUATE)
benchmark: sharpe=0.80  net=+182.40%  maxdd=-75.45%
cost     : COST_ROBUST    latency: LATENCY_ROBUST
DSR      : 0.642 (N=2)
stage    : REJECTED_AT_BACKTEST  (promotable_to_live=False)
```

### Yorum

- **Düzeltmeler tabloyu değiştirdi.** Yanlı v2 −0.009%/işlem gösteriyordu;
  düzeltilmiş koşu +0.249%/işlem. Kararı askıya almak doğruydu.
- **Pozitif ama yetersiz.** Sharpe 0.53, hem 0.8 eşiğinin hem de B&H'nin
  (0.80) altında → `out_of_sample` FAIL. Deflated Sharpe 0.642 < 0.95:
  iki denemeden sonra bu sonuç şansla açıklanabilir.
- **Risk-ayarlı olarak B&H'nin gerisinde.** Yaklaşık 50 işlem/yıl ×
  %0.249 ≈ yıllık %12 (toplamsal, ≤1x) ve max DD −%40 → Calmar ≈ 0.3.
  B&H ≈ yıllık %60 ve −%75 → ≈ 0.8. Strateji daha az düşüyor ama çok
  daha az kazandırıyor.
- **%66.7 isabet oranı edge göstergesi değil.** PF 1.19 ile ortalama
  kazanç/kayıp oranı ≈ 0.6. Yüksek isabet, merdivenin geometrisinden
  geliyor (T1 = 1 ATR hedef, stop = 1.5 ATR).
- **Execution sorunu değil.** Maliyet ×1.5/×2 ve +1s/+4s giriş gecikmesi
  sonucu bozmuyor. Sorun sinyal ve çıkış tasarımında.
- **Henüz görülemeyenler.** Beta ayrışması (decay `excess_vs_baseline`),
  yıl ve rejim dağılımı, walk-forward fold tutarlılığı, bootstrap aralığı
  ve pertürbasyon kararı bu koşunun log'unda yoktu (yalnızca Summary
  sayfasında ve artifact'taydı). CLI artık bunları log'a da basıyor.

**Karar:** "TSMOM girişi + swing merdiveni" (Faz A2a) **NO-GO**, bu kez
doğru gerekçeyle: zayıf pozitif, istatistiksel olarak anlamlı değil,
B&H'nin gerisinde. Saf TSMOM (sinyal dönene kadar tutma) hâlâ test
edilmedi; bu yeni bir deneme olur (N=3).

## Faz A3 — Ön-kayıtlı varyant karşılaştırması (protokol, sonuçlardan önce yazıldı)

Amaç: iki soruyu sonuçlar görülmeden önce sabitlenmiş kurallarla yanıtlamak.
(1) Saf TSMOM (sinyal dönene kadar tut) swing merdiveninden farklı mı?
(2) 60 günlük momentum filtresi herhangi bir şey katıyor mu?

**Koşular** (Actions → backtest; hepsi `BTCUSDT`, `lookback_months=72`,
folds 4, embargo 3, horizon 168, perturb açık):

| variant | Ne test ediyor |
|---|---|
| `ladder` | Faz A2a tasarımı (72 ay; aynı `trial_id`, yeni deneme değil) |
| `signal_exit` | Saf TSMOM: sinyal dönüşünde çık, yalnızca 4×ATR14 felaket stop'u |
| `always_long` | Kontrol: momentum filtresi kapalı, ladder ile aynı çıkışlar |

**Deneme sayısı:** iki varyant `research/trials/registry.jsonl`'a sonuçlardan
önce kaydedildi. Ailede N=4 (v1, ladder, signal_exit, always_long); deflated
Sharpe her koşuda N=4 ile hesaplanır.

**Önceden sabitlenmiş karar kuralları:**

1. *Filtrenin katkısı* = `ladder` EV − `always_long` EV (72 ay). Fark ≤ 0
   ise 60 günlük filtre edge eklemiyor; getiri merdiven + BTC betasıdır.
2. *Saf TSMOM* ancak terfi aşaması `BACKTEST_PASSED_PAPER_REQUIRED` olursa
   paper aşamasına aday olur. 30'dan az işlem (`INSUFFICIENT`) sonucu,
   Sharpe ne çıkarsa çıksın **sonuçsuz** sayılır. Beklenti: 6 yılda
   yaklaşık 10-30 işlem, yani büyük olasılıkla sonuçsuz.
3. `ladder`'ın 36 ve 72 aylık sonuçları farklıysa ikisi de raporlanır; hangisi
   daha iyi görünüyorsa o seçilmez.
4. Sonuçları gördükten sonra yapılan her parametre veya kural değişikliği yeni
   bir deneme olarak kayda girer (N artar).

### Ara koşular (protokol tamamlanmadan)

| Run | variant | Pencere | n | EV/işlem | PF | Sharpe | B&H Sharpe | DSR (N=4) | Aşama |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| #55 | ladder | 36 ay | 136 | +0.227% | 1.17 | 0.48 | 0.75 | 0.401 | REJECTED |
| #56 | always_long | 72 ay | 459 | +0.031% | 1.02 | 0.08 | 0.59 | 0.192 | REJECTED |

- **#55 ladder (36 ay).** Out-of-sample, walk-forward (2/4 fold),
  pertürbasyon (FRAGILE; en kötü komşu +0.039%), bootstrap (CI
  [−0.30, +0.79]), Monte Carlo ve DSR **FAIL**; maliyet ve gecikme stresi
  PASS. Decay: sinyalin koşulsuz getiriye göre fazlası 12–72 saatte
  +0.04/+0.10%, 168 saatte −0.18%. Sinyal betanın ötesinde bir şey
  eklemiyor. Pencere bir ay kısalınca (#54 → #55) Sharpe 0.53'ten 0.48'e
  düştü.
- **#56 always_long (72 ay).** Pratikte sıfır edge (CI [−0.29, +0.36]).
  Maliyet ve gecikme stresinde de kırılgan. Yıllara göre: 2021 −0.20%,
  2022 −0.50%, 2023 +0.81%, 2025 −0.07%, 2026 −0.34%.
- Protokol kuralı 1 (filtrenin katkısı) için `ladder` da 72 ayla koşulmalı.
  #55 36 aylık olduğu için doğrudan karşılaştırılamaz.

### Ölçüm düzeltmesi (2026-10-01)

Al-tut max düşüşü günlük log-getirilerin toplamı üzerinden hesaplanıyordu.
Bu bir log-puandır: 200 → 50 düşüşü −%75 yerine −138.6 olarak raporlanıyordu
(#56'daki "B&H max DD −145%" bu yüzden). Strateji düşüşü ise basit yüzde.
Sonuç olarak "DD < B&H'nin %60'ı" ve Monte Carlo limiti **stratejinin
lehine** gevşek kalıyordu. Artık fiyat yolundan basit yüzde hesaplanıyor.
#54–#56'daki ilgili kontroller zaten FAIL olduğu için kararlar değişmiyor.
Protokolün nihai karşılaştırması, düzeltilmiş kodla tek bir `variant=all`
koşusundan alınacak (aynı pencere, aynı kod).

## Faz A3 sonucu — run #57 (2026-10-01, `variant=all`, 72 ay)

Tek koşuda, aynı pencerede (2020-09 → 2026-08, 2161 gün) ve aynı kodla
(düzeltilmiş B&H düşüşü dahil). Al-tut: Sharpe 0.59, net +%574, max DD −%76.7.

| | ladder | signal_exit (saf TSMOM) | always_long (kontrol) |
|---|---:|---:|---:|
| İşlem | 267 | 49 (SMALL) | 459 |
| EV/işlem (net) | +0.231% | +4.009% | +0.031% |
| Profit factor | 1.16 | 2.63 | 1.02 |
| Sharpe | 0.45 | **0.62** | 0.08 |
| Max DD | −49.6% | −46.2% | −73.1% |
| İsabet oranı | %64.8 | %34.7 | %61.0 |
| Bootstrap %95 CI (EV) | [−0.18, +0.64] | [−0.56, +9.63] | [−0.29, +0.36] |
| Walk-forward (fold Sharpe > 0.5) | 3/4 ✅ | 1/4 ❌ | 0/4 ❌ |
| Maliyet / gecikme stresi | ✅ / ✅ | ✅ / ✅ | ❌ / ❌ |
| Pertürbasyon | FRAGILE | **ROBUST** | FRAGILE |
| Monte Carlo p05 DD (limit −46%) | −86.5% ❌ | −88.4% ❌ | −161% ❌ |
| Deflated Sharpe (N=4) | 0.507 | 0.726 | 0.192 |
| Aşama | REJECTED | REJECTED | REJECTED |

### Önceden sabitlenmiş kurallara göre

1. **Filtrenin katkısı** = ladder EV − kontrol EV = **+0.200 puan/işlem > 0**.
   Kural filtreyi reddetmiyor; nokta tahmini filtrenin lehine. Ancak iki
   güven aralığı geniş ölçüde çakışıyor. Bu yüzden "filtre edge ekliyor"
   değil, "filtre olmadan merdiven para kazandırmıyor" demek doğru. Decay
   verisi de aynı yönde: LONG sinyali sonrasındaki getiri, koşulsuz
   getiriyi 24 saatte +0.09% (t=2.57) ve 72 saatte +0.22% (t=2.32) aşıyor.
   Bu etki küçük, ve 6 ufuk × 4 deneme düşünüldüğünde çoklu test
   düzeltmesine dayanıklı değil.
2. **Saf TSMOM** 49 işlemle 30 eşiğinin üstünde; yani "sonuçsuz" değil,
   ama terfi aşaması `REJECTED_AT_BACKTEST`. Paper adayı **değil**.
3. **ladder 36 ay vs 72 ay:** EV +0.227% / +0.231%, Sharpe 0.48 / 0.45.
   Tutarlı; ikisi de REJECTED. Seçim yapmaya gerek yok.
4. Sonuç görüldükten sonra hiçbir parametre değişmedi; N=4 kaldı.

### Yorum

- **Saf TSMOM dört denemenin en iyisi, ama edge kanıtı değil.**
  - Sharpe (0.62) al-tut'unkiyle (0.59) pratikte aynı; düşüşü daha küçük
    (−46% / −77%), getirisi ise çok daha düşük: yıllık ≈ %33 toplamsal,
    ≤1x. Literatürdeki TSMOM profiliyle uyumlu: alfa değil, risk yönetimi
    katmanı.
  - Kârın **%43'ü tek bir işlemden** geliyor (2020, +%84.6). O işlem
    çıkarılınca ortalama +%2.33.
  - Dönemler arasında tutarsız (fold Sharpe −0.33 / 0.06 / 0.79 / 0.12),
    bootstrap aralığı sıfırı içeriyor.
- **Ladder** zayıf pozitif ve kırılgan; sinyal betanın biraz ötesine
  geçiyor ama al-tut'u geçmiyor.
- **Kontrol** çıkış merdiveninin tek başına bir şey kazandırmadığını
  gösteriyor.

**Faz A3 kararı: dört deneme de NO-GO.** Tek sembol BTC long-only yönlü
stratejiler bu testlerde al-tut'u risk-ayarlı olarak geçemedi. Saf TSMOM
"daha az düşüşle benzer Sharpe" sunuyor. Bu bir risk tercihi olabilir, ama
spec'in aradığı anlamda istatistiksel bir edge değil.

## Chassis geleceği

`trading/` paketi silinmez — Faz A altyapısı gelecekteki hipotez denemeleri
için hazır kalır:

- `trading/data/binance_perp.py` + `binance_vision.py` — Binance USDⓈ-M
  perpetual veri adaptörleri (live REST + CDN zip dumps)
- `trading/backtest/cost_model.py` — funding + taker fee + slippage
  cost function
- `trading/backtest/walk_forward.py` — purged walk-forward CV harness
- `trading/backtest/benchmark.py` — B&H + otomatik GO/NO-GO verdict
- `.github/workflows/backtest.yml` — manuel-trigger backtest workflow

Bu chassis üzerine yeni bir hipotez (cross-sectional momentum, funding
carry, spot-perp basis, vs.) test etmek "Faz E: Alternative strategies
research" olarak istenildiği zaman açılabilir. Ana giriş noktası:
`trading/strategies/` altına yeni bir strategy modülü + `walk_forward`'ün
`evaluate_*` çağrısını yenisine yönlendir.

## Kaynak

- v1 backtest artifact: `research/data/backtest_BTCUSDT.json`
  (GitHub Actions run #52, 2026-09-23 21:07 UTC)
- v2 backtest artifact: aynı yol, sonraki koşu (2026-09-23 21:20 UTC)
- Workflow log ZIP: yerelde `8982f5a2-logs_97269602992.zip`
