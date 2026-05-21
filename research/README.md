# signal-bot research

Mevcut `bot.py`'den **bağımsız** araştırma pipeline'ı. Buradaki kod canlı bota dokunmaz; amaç istatistiksel olarak doğrulanabilir edge'ler bulmak.

Faz tanımları için repo kökündeki `PROJECT.md`'ye bak (henüz commit edilmediyse kişisel notlarındadır).

## Kurulum (lokal makinede)

```bash
cd research
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Faz 0 — Sanity

1. **Veri indir** (~5 yıl, 4 coin, 3 TF; idempotent — tekrar çalıştırınca sadece yeniyi çeker):
   ```bash
   python download_binance_data.py
   ```
   Çıktı: `research/data/<SYMBOL>_<TF>.parquet`

2. **Sanity backtest** çalıştır (BTC 1h, SMA 20/50 crossover, fee+slippage dahil):
   ```bash
   python backtest_sanity.py
   ```
   Çıktı: Sharpe, MaxDD, win rate, trade sayısı, ayrıca buy-and-hold karşılaştırması.

**Geçer not:** Sayısal değerler basılıyor + B&H ile karşılaştırma görünüyor. SMA'nın B&H'yi yenmemesi beklenen davranıştır — bu pipeline testi, edge arayışı değil.

## Dizin yapısı

```
research/
├── README.md
├── requirements.txt
├── download_binance_data.py
├── backtest_sanity.py
└── data/                      # parquet'ler (gitignore'da)
```

## Faz 0 çıkış kriteri

- [x] `download_binance_data.py` çalışıyor, parquet'leri üretiyor
- [x] `backtest_sanity.py` çalışıyor, Sharpe/MaxDD/WR raporluyor
- [ ] Lokalde bir kez baştan sona çalıştırıldı, sayılar makul

Hepsi tamam olunca → Faz 1 (veri kütüphanesi: funding rate, OI, BTC dominance, maliyet modeli).
