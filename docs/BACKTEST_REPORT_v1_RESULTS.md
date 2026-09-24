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
