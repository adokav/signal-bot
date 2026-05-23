# Faz 0 — Sanity Backtest Raporu

**Tarih:** 21 Mayıs 2026
**Branch:** `claude/crypto-signal-bot-TpflY`
**Ortam:** GitHub Codespaces (Python 3.11.x, pandas 2.2.3, numpy 1.26.4, vectorbt 0.26.2, plotly 5.24.1)

## Amaç

Faz 0'ın amacı **edge bulmak değil**, vectorbt + pandas + parquet pipeline'ının uçtan uca çalıştığını kanıtlamak. PROJECT.md kuralı: "B&H BTC'yi yenmeyen strateji = harcanmış zaman" — SMA crossover'ın B&H'yi yenmesi beklenmiyor.

## Veri

| | |
|---|---|
| Kaynak | Binance public klines (REST) |
| Coin'ler | BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT |
| Timeframe | 1h, 4h, 1d |
| Dönem | 2021-01-01 → 2026-05-21 |
| Bar sayısı (1h) | 47.183 |
| Bar sayısı (4h) | 11.800 |
| Bar sayısı (1d) | 1.967 |
| Depolama | Parquet, `research/data/<SYMBOL>_<TF>.parquet`, `data/` gitignore'da |

## Strateji (sanity)

- BTCUSDT 1h
- SMA(20) ile SMA(50) crossover, long-only
- Komisyon: 0.10% (taker)
- Slippage: 0.05%
- Başlangıç sermayesi: $10.000

## Sonuçlar

### SMA(20/50) crossover, long-only

| Metrik | Değer |
|---|---|
| Period | 2021-01-01 → 2026-05-21 |
| Bars | 47.183 |
| Total Return | **-68.66%** |
| Sharpe (annualized) | **-0.35** |
| Max Drawdown | **-78.09%** |
| Trades | 562 |
| Win rate | 30.6% |
| Avg trade PnL | -$12.22 |

### Buy & Hold benchmark

| Metrik | Değer |
|---|---|
| Period | 2021-01-01 → 2026-05-21 |
| Bars | 47.183 |
| Total Return | **+165.51%** |
| Sharpe (annualized) | **0.60** |
| Max Drawdown | **-77.20%** |
| Trades | 1 |
| Win rate | 100.0% |

## Yorum

1. **Pipeline çalışıyor.** Veri akışı (Binance → parquet → vectorbt), backtest motoru, metrik raporlama (Sharpe, MaxDD, WR, trade sayısı) ve benchmark karşılaştırması uçtan uca tutarlı.

2. **SMA cross beklendiği gibi para kaybediyor.** Kripto gibi gürültülü piyasada whipsaw fazla, 562 trade × ortalama -$12 = -$6.870 brüt kayıp + komisyon/slippage = -68.66% net. Beklenen.

3. **B&H Sharpe 0.60 → gerçek çıta.** PROJECT.md "Sharpe ≥ 0.8" minimumunu koymuş ama gerçek geçer not **B&H'yi yenmek**. Faz 2'de denenecek time-series momentum hem Sharpe ≥ 0.8 vermeli hem de B&H Sharpe 0.60'ı geçmeli — aksi takdirde edge yok.

4. **MaxDD ikisinde de ~77%** → Faz 5 (risk yönetimi) için kritik veri. B&H tek başına -77% drawdown'a katlanmayı gerektirir; bunu tolere edemeyen yatırımcı için "buy and hold" pratikte uygulanamaz. Vol targeting + drawdown circuit breaker'ın değeri bu sayıdan ortaya çıkıyor.

## Faz 0 Çıkış Kriteri

- [x] vectorbt + pandas + pyarrow stack ayakta
- [x] Binance public API'den 4 coin × 3 TF × ~5 yıl OHLCV indirildi
- [x] Parquet → backtest → metrik raporlama uçtan uca çalışıyor
- [x] Komisyon + slippage modellemesi dahil
- [x] B&H benchmark referansı raporlanıyor

**Sonuç:** Faz 0 kapandı.

## Sonraki adım

**Faz 1 — Veri kütüphanesi + maliyet modeli.** Detaylar PROJECT.md'de:
- Funding rate (Binance fapi)
- Open Interest (Binance fapi)
- BTC dominance (CoinGecko free tier)
- `cost_model.py` — order book derinliği × pozisyon boyutu → slippage tahmini

Bu Faz 2'de gerçek bir edge (time-series momentum) ararken hazır olması gereken altyapıdır.
