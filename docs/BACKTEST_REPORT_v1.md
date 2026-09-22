# Faz A — Backtest v1 Runbook

Bu belge Faz A çıktısını üretme adımlarını, beklenen artefaktları ve
GO/NO-GO eşiğini açıklar. Backtest raporunun kendisi bu belgeye
`docs/BACKTEST_REPORT_v1_RESULTS.md` olarak eklenecek. Değer sonucu
şimdilik yok — bu commit hipotezi test edecek altyapıyı verir; sonucu
karar noktası olarak sen değerlendireceksin.

## Hipotez

Binance USDⓈ-M perpetual üzerinde günlük bar time-series momentum long-only
sinyali + AQR volatility targeting kombinasyonunun cost-adjusted OOS
Sharpe'ı BTC B&H'yi geçebilir. Formülasyon:

- Sinyal: 60 günlük log-getirinin işareti (long-only).
- Sizing: `target_annualized_vol=%40 / realized_annualized_vol_30d`,
  `max_leverage=3.0` ile capped.
- Exit: T1 = entry + 1×ATR14, stop-to-entry sonrası T2 = entry + 2×ATR14,
  hard_stop = entry − 1.5×ATR14, time_stop = 48h.
- Cost: taker fee 2×4bps + slippage 2×2bps + funding accrual boyunca long
  öder.

Parametreler `TsmomParams` içinde tanımlı; değiştirilirse commit'te
belirtilmeli.

## Çalıştırma

```bash
# 1. Dev deps
python -m venv .venv-trading
source .venv-trading/bin/activate
pip install -r requirements-trading.txt -r requirements-dev.txt

# 2. Data
python -m trading.data.binance_perp BTCUSDT --out research/data/binance_perp
python -m trading.data.binance_perp ETHUSDT --out research/data/binance_perp
python -m trading.data.binance_perp SOLUSDT --out research/data/binance_perp

# 3. Backtest
python -m trading.backtest.walk_forward BTCUSDT \
    --data-dir research/data/binance_perp \
    --out research/data/backtest_btc.json
python -m trading.backtest.walk_forward ETHUSDT \
    --data-dir research/data/binance_perp \
    --out research/data/backtest_eth.json
python -m trading.backtest.walk_forward SOLUSDT \
    --data-dir research/data/binance_perp \
    --out research/data/backtest_sol.json
```

Binance public API'sinden birkaç yıllık günlük veri (`/fapi/v1/klines`
limit 1500) ve yaklaşık son 30 günlük OI + tam funding geçmişi çekilir.
Kline+funding parquet'leri backtest'e girer; OI şu an sadece observability
için persist edilir, sinyal formülünde kullanılmaz (Faz A sonrası feature
adayı).

Her sembol için `backtest_*.json`:

- `folds`: her fold için `sharpe_annualized`, `mean_net_pct`,
  `hit_rate`, `max_drawdown_pct`, `mean_mfe_pct`, `mean_mae_pct`,
  `exit_reason_breakdown`.
- `aggregate`: tüm işlemlerin birleşik metrikleri.
- `can_authorize_trade: false` invariantı sabit.

## GO/NO-GO eşiği (`docs/EVOLUTION_ROADMAP.md` Faz B karar noktası)

Aşağıdaki üçünün de **hepsi** doğru olmalı ki Faz C testnet execution
katmanına geçelim:

1. Aggregate cost-adjusted `sharpe_annualized > 0.8`.
2. Aggregate Sharpe > aynı dönemin BTC B&H Sharpe'ı.
3. Aggregate `max_drawdown_pct` mutlak değeri, BTC B&H max DD'sinin
   %60'ından az.

Ayrıca fold-fold tutarlılık: en az 3/4 fold'da `sharpe_annualized > 0.5`
olmalı. Tek fold'ın taşıdığı aggregate Sharpe overfit sinyalidir.

## Beklenen sonuçlar

Bu koşuyu **sen kendi ortamında** yapacaksın (backtest sonuçları PR
review'ı beklerken paylaşılırsa daha iyi). Sonuç senaryoları:

- **Yeşil (üç kriter geçer):** Faz C planı yürürlüğe girer, testnet
  order manager yazılır.
- **Sarı (Sharpe borderline, tutarsız fold):** Parametreleri yeniden
  düşün, funding regime filter ekle (`funding > threshold` iken flat).
- **Kırmızı (Sharpe < 0.5 veya B&H'yi geçmez):** TSMOM hipotezi
  yeterli değil. Alternatifler: (a) cross-sectional momentum, (b)
  spot-perp basis carry, (c) funding-arbitrage delta-neutral. Yeni
  hipotez seçilir, aynı harness tekrar koşar.

Kırmızı senaryo utanç değil beklenen bir çıktı; edge keşfi Popper-vari
çürütmeye dayanır.

## Sınırlar (dürüst uyarılar)

- **Cost model'de order-book depth yok.** Slippage sabit 2bps/side; büyük
  notional'da yanılır. Faz C öncesinde live depth snapshot'ı ile
  kalibre edilmeli.
- **OI feed dahil değil.** Şu an sadece indirilip persist ediliyor; sinyal
  formülünde kullanılmıyor. Feature olarak eklendiğinde OOS baştan koşar.
- **Symbol universe küçük.** BTCUSDT + ETHUSDT + SOLUSDT ile başlıyoruz;
  cross-sectional momentum için evren genişletilmeli.
- **Funding regime filter yok.** Aşırı yüksek funding'de long açmak edge'i
  yer. Faz A raporunda funding dağılımına bak; regime filter Faz A2'de
  eklenebilir.
- **Look-ahead riski her satır kod'da.** Harness `evaluate_tsmom(daily[:i+1])`
  ile her karar günü kadar veriyi kullanır; ancak birleşik metrik
  hesaplamada yanlışlıkla ileri veri kaymaması için testler var:
  purged fold, embargo, `available_time` invariantları, open candle reddi.
  Yeni feature eklerken bu invariantlar korunmalı.

## Faz A çıktı sözleşmesi

Bu PR sadece harness'i verir. Faz A'nın **çıktısı** iki dosya:

1. `research/data/backtest_*.json` (JSON metrik raporu, sen üretirsin)
2. `docs/BACKTEST_REPORT_v1_RESULTS.md` — sen sonucu yorumlayıp GO/NO-GO
   kararını verirsin; bir sonraki PR bu belgeyi commit'ler.

Bu belge Faz B karar noktasının input'u olacak.
