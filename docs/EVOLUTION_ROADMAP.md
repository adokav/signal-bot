# Signal Bot Evrim Yol Haritası

Bu belge, mevcut SHADOW/research radarlarının **kanıta-dayalı** olarak Binance
USDⓈ-M perpetual üzerinde long-ağırlıklı sistematik execution'a dönüştürülmesi
için 6-8 haftalık plandır. AGENTS.md disipliniyle uyumludur: research skoru
işlem yetkisi değildir; forward-edge kanıtlanmadan execution'a geçilmez.

## Hedef

- **Piyasa:** Binance USDⓈ-M perpetual (BTC, ETH, majör altlar).
- **Yön:** Long-ağırlıklı sistematik girişler.
- **Exit modeli:** T1 partial + stop-to-entry + T2 ATR-trail + time-stop
  (48h expiry). Parametreler faz A çıktısıyla revize edilecektir.
- **Sermaye modeli:** Per-trade risk ≤ %0.5, aggregate max risk ≤ %2,
  leverage ≤ 3x (isolated margin).
- **GO/NO-GO kriteri:** OOS cost-adjusted Sharpe > 0.8 **ve** BTC B&H'nin
  Sharpe'ını geçmesi **ve** max DD B&H'nin %60'ının altında olması.

## Chassis kararı

**Freqtrade** (`https://www.freqtrade.io`) altyapı olarak seçildi. Neden:
- Aktif geliştirilen, futures desteği olan Python framework'ü.
- Order manager, idempotency, position mode, partial fill handling, backtest
  engine, hyperopt, walk-forward CV, Telegram, deployment hazır.
- ~4 haftalık execution altyapı işini kazandırır.
- Bizim `TacticalLongEngine`'imiz Freqtrade `IStrategy` sınıfına sarılarak
  çalıştırılabilir; R/R invariantları korunur.

Freqtrade'in **hazır stratejileri kullanılmaz** — sadece chassis olarak
kullanılır. Edge tarafı akademik hipotezlerden başlar, OOS'ta doğrulanır.

## Baş hipotezler (Faz A test edilecek)

Kripto'da belgelenmiş sınıflar:

1. **Time-series momentum** (Moskowitz-Ooi-Pedersen 2012, Liu-Tsyvinski 2018
   kripto uyarlaması) — 30/60/90 günlük getirinin işareti ile long/flat.
2. **Volatility targeting** (AQR TSMOM stili) — hedef vol seviyesi tutmak
   için pozisyon boyutu ölçeklenir. Sharpe'ı yükseltir, max DD'yi düşürür.
3. **Cross-sectional momentum** (Binance perp evreninden aylık top-N long).

Baş test kombinasyonu: TSMOM + vol targeting + BTC top-of-book filter.

## Fazlar

### Faz 0 — Temizlik ve iskelet (Hafta 1)

Şu anda burada.

- [x] `bot.py:43` `trade_universe` bug fix (bugün).
- [x] `bot.py:_api` Telegram HTTPError sanitize (AGENTS.md §3).
- [x] README ↔ kod tutarsızlıklarını gider.
- [x] Bu belge + `docs/DEAD_CODE_AUDIT.md`.
- [x] `trading/` paket iskeleti.
- [ ] Ölü kod silme (audit'te listelenen). Ayrı commit.
- [ ] Freqtrade dev dependency ekleme (`requirements-trading.txt`).

### Faz A — Kanıt (Hafta 2-3)

- [ ] Binance perpetual data adapter (`trading/data/binance_perp.py`):
      klines + funding history + OI history + aggTrades → parquet.
- [ ] Cost model bütünleşimi: funding accrual + taker fee tier + slippage
      tahmini. `research/cost_model.py` içindeki snapshot mantığını canlı
      cost function'a çevir.
- [ ] Freqtrade strategy sarmalayıcı: `TacticalLongEngine.analyze()`
      çıktısını `IStrategy.populate_entry_trend` / `populate_exit_trend`
      kararlarına çevir.
- [ ] TSMOM + vol targeting strategy'sini `trading/strategies/tsmom.py`
      olarak yaz.
- [ ] Purged + embargoed walk-forward CV harness'ı (`trading/backtest/`).
- [ ] OOS raporlama: cost-adjusted Sharpe, expectancy, MFE/MAE, max DD,
      calibration.

### Faz B — GO/NO-GO karar noktası (Hafta 4)

- [ ] İlk OOS raporu (`docs/BACKTEST_REPORT_v1.md`).
- [ ] Karar:
  - **GO:** en az bir hipotez cost-adjusted OOS Sharpe > 0.8 **ve** BTC B&H'yi
    Sharpe/DD kombinasyonunda geçiyor. → Faz C'ye geç.
  - **NO-GO:** düzeltmeler, ek feature keşfi, veya kombinasyon. Faz
    A'ya geri dön.

Bu karar noktasında dürüst raporlama zorunludur: sonuç "iyi değil"se
"iyi" gibi sunulmaz.

### Faz C — Execution katmanı (Hafta 5-6, sadece GO ise)

- [ ] Binance testnet order manager (Freqtrade native destekle):
      idempotent, position-mode aware, partial-fill safe.
- [ ] Sizing: risk-per-trade %0.5, aggregate cap, leverage cap, drawdown
      circuit breaker.
- [ ] Katmanlı exit: T1 partial + stop-to-entry + T2 ATR-trail + time-stop.
- [ ] AGENTS.md §10 gerekleri: notional/leverage/slippage cap'leri kod
      düzeyinde zorunlu; duplicate order koruması; provider outage'da
      "successful order" varsayımı yok.

### Faz D — Testnet + mikro live (Hafta 7-8)

- [ ] Binance testnet paper trade, 200-500 sinyal.
- [ ] Live/backtest divergence < %15 metriği.
- [ ] Yeşilse mikro kaybedilebilir sermaye ($100-500) ile 2 hafta.
- [ ] Metrik "kâr" değil, **testnet ↔ live divergence** ve slippage
      dağılımı.

## Kesin çizgiler

- OOS validation atlanmaz. Faz A ↔ Faz C arasında bir "kanıt eşiği" vardır.
- Testnet aşaması atlanmaz. Faz C ↔ mikro-live arasında bir "sistem eşiği"
  vardır.
- Sinyal grupları, copy trading leaderboard'ları, marketplace stratejileri
  edge kaynağı olarak kullanılmaz (survivorship + overfit + incentive
  misalignment).
- AGENTS.md tüm fazlarda geçerlidir; özellikle §2 (fail closed), §3 (secret
  safety), §4 (trading-authority boundaries), §7 (statistical rigor),
  §10 (order safety).

## Şu anki durum

`Faz 0` içindeyiz. Bir sonraki iş: dead code audit'i onaylat, silme
commit'ini yap, sonra `trading/` paketi + Freqtrade dev bağımlılığı ekle.
