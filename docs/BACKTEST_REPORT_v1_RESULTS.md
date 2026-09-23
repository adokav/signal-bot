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

## Kaynak

- Backtest artifact: `research/data/backtest_BTCUSDT.json`
  (GitHub Actions run #52, 2026-09-23 21:07 UTC, 3 yıl BTCUSDT vision data)
- Workflow log ZIP: yerelde saklanan `8982f5a2-logs_97269602992.zip`
