# `trading/` — Freqtrade tabanlı execution ve backtest chassis

Bu paket, mevcut SHADOW radarlarını Binance USDⓈ-M perpetual üzerinde
sistematik long-ağırlıklı execution'a çevirmek için gereken **chassis**
kodudur. `bot.py` production akışı bu klasöre bağımlı değildir; ayrı bir
namespace olarak durur.

Yol haritası: `docs/EVOLUTION_ROADMAP.md`.

## Yapı

```
trading/
├── README.md
├── data/          # Binance perp data adapter (klines, funding, OI, aggTrades)
├── strategies/    # Freqtrade IStrategy sınıfları (TSMOM, tactical_long wrapper)
├── backtest/      # Purged walk-forward CV, cost-adjusted metrics
└── execution/     # Order manager (Faz C'de yazılacak; şimdilik boş)
```

## Bağımlılıklar

Freqtrade **ana `bot.py` üretim servisinden ayrık** bir dependency setidir.
`requirements-trading.txt` içinde tanımlanır. Render `bot.py` servisine
yüklenmez; sadece dev/backtest ortamında kurulur.

## AGENTS.md uyumu

- Point-in-time integrity: `data/` adaptörleri `available_time` /
  `close_time` invariantlarını `acce_unified.tactical_long` disipliniyle
  aynı korur.
- Trading-authority boundaries: `strategies/` çıktıları `execution/` katmanı
  olmadan **hiçbir** emir üretmez. `execution/` katmanı Faz C'ye kadar
  boştur.
- Statistical rigor: `backtest/` walk-forward, purged, embargoed;
  cost-adjusted metrikler (funding + fee + slippage) zorunludur.

## Kurulum (dev)

Henüz aktif değildir. Faz A'ya geçildiğinde:

```bash
python -m venv .venv-trading
source .venv-trading/bin/activate
pip install -r requirements-trading.txt
```

Freqtrade'in `user_data/` dizin gereksinimi `trading/` altında değil,
`~/.freqtrade/` altında tutulacaktır (repo dışı).
