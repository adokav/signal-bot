# signal-bot research

Mevcut `bot.py`'den **bağımsız** araştırma pipeline'ı. Buradaki kod canlı bota dokunmaz; amaç istatistiksel olarak doğrulanabilir edge'ler bulmak.

Faz tanımları için repo kökündeki `PROJECT.md`'ye bak (henüz commit edilmediyse kişisel notlarındadır).

## Kurulum

Tercihen GitHub Codespaces (browser'da, repo'yu otomatik klonlar):
- Repo sayfasında **Code → Codespaces → + New with options** → branch: `claude/crypto-signal-bot-TpflY`

Lokalde de aynı:
```bash
cd research
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Dizin yapısı

```
research/
├── README.md
├── requirements.txt
├── sanity_report.md              # Faz 0 sonuc kaydi
├── download_binance_data.py      # Faz 0: OHLCV
├── backtest_sanity.py            # Faz 0: SMA cross sanity
├── download_funding.py           # Faz 1: funding rate
├── download_oi.py                # Faz 1: open interest (son 30 gun)
├── cost_model.py                 # Faz 1: order book -> slippage
└── data/                         # parquet/json (gitignore)
```

## Faz 0 — Sanity ✅

1. **OHLCV indir** (~5 yıl, 4 coin, 3 TF; idempotent):
   ```bash
   python download_binance_data.py
   ```
2. **Sanity backtest** (BTC 1h, SMA 20/50, fee + slippage dahil):
   ```bash
   python backtest_sanity.py
   ```

Sonuç: `sanity_report.md`. SMA cross B&H'yi yenmiyor (beklenen); pipeline ucundan ucuna çalışıyor.

## Faz 1 — Veri kütüphanesi + maliyet modeli 🚧

### 1. Funding rate (5 yıl, sorunsuz)

```bash
python download_funding.py
```
Çıktı: `data/<SYMBOL>_funding.parquet`. Funding 8 saatte bir tahakkuk; coin başına ~5400 kayıt.

### 2. Open Interest (sadece son 30 gün — kısıt)

```bash
python download_oi.py
```
Çıktı: `data/<SYMBOL>_oi.parquet`. **UYARI:** Binance public OI history endpoint sadece son ~30 günü dönüyor. 5 yıllık tarihsel OI ücretsiz yok; gerekiyorsa Coinalyze/Coinglass/CryptoQuant gibi ücretli sağlayıcılar lazım. v1 yaklaşımı: son 30 günü kaydet, ileride biriktirici cron'la büyüt.

### 3. Cost model (anlık snapshot)

```bash
python cost_model.py
```
Çıktı:
- Terminalde her coin için spread + farklı pozisyon büyüklüklerinde buy/sell slippage tablosu
- `data/cost_model_snapshot.json`

İki kullanım:
- **Script olarak:** snapshot al + JSON kaydet
- **Modül olarak:** Faz 2 backtest'inde `from cost_model import estimate_slippage_bps` → `estimate_slippage_bps("BTCUSDT", "buy", 5000) -> bps`. Cache varsa onu kullanır, yoksa canlı çağırır.

**KISIT:** Tarihsel order book ücretli/ağır (Tardis.dev vb.). v1 statik snapshot. Küçük pozisyonlarda iyi yaklaşıklık; kapasite limitine yaklaşınca yanılır.

### BTC dominance — şimdilik kapsam dışı

CoinGecko free tier sadece anlık dominance veriyor; tarihsel için ücretli plan veya scraping gerekiyor. Faz 2'de gerçekten lazım olursa o zaman çözeriz — disiplin: kanıtlanmamış feature için altyapı yatırımı yok.

## Faz 1 çıkış kriteri

- [ ] `download_funding.py` çalıştı, 4 coin için parquet üretti
- [ ] `download_oi.py` çalıştı (son 30 gün; kısıtın farkındayız)
- [ ] `cost_model.py` çalıştı, tablo bastı, JSON kaydetti
- [ ] `estimate_slippage_bps()` import ile çalışıyor

Hepsi tamam olunca → Faz 2 (tek edge'i doğrula: time-series momentum).
