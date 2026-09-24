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

### Faz 0 — Temizlik ve iskelet (Hafta 1) — TAMAMLANDI

- [x] `bot.py:43` `trade_universe` bug fix.
- [x] `bot.py:_api` Telegram HTTPError sanitize (AGENTS.md §3).
- [x] README ↔ kod tutarsızlıklarını gider.
- [x] Bu belge + `docs/DEAD_CODE_AUDIT.md`.
- [x] `trading/` paket iskeleti.
- [x] Ölü kod silme birinci dalga (~4400 satır, PR #100).
- [x] `requirements-trading.txt` (Faz A dev deps).

### Faz A — Kanıt (Hafta 2-3) — CLOSED: NO-GO

Hipotez çürütüldü. Detaylı belge: `docs/BACKTEST_REPORT_v1_RESULTS.md`.
v1 Sharpe -0.31 (agresif parametreler) → v2 Sharpe -0.03 (muhafazakar).
İki bağımsız parametre setinde negatif Sharpe → parametre değil hipotez
sorunu. Kripto tek-sembol TSMOM long-only edge'i 2020 sonrası büyük
ölçüde arbitraj edildi.

`trading/` chassis'i silinmez — gelecekteki hipotez denemeleri için
altyapı olarak durur (`docs/BACKTEST_REPORT_v1_RESULTS.md` "Chassis
geleceği" bölümüne bakın).

- [x] Binance perpetual data adapter (`trading/data/binance_perp.py`):
      klines + funding history + OI history → parquet.
- [x] `trading/data/binance_vision.py` — CI'da 451 çözümü, historical
      zip dumps.
- [x] Cost model (`trading/backtest/cost_model.py`): funding accrual +
      taker fee + slippage.
- [x] TSMOM + vol targeting strategy (`trading/strategies/tsmom.py`).
- [x] Purged + embargoed walk-forward CV harness
      (`trading/backtest/walk_forward.py`).
- [x] Backtest runbook (`docs/BACKTEST_REPORT_v1.md`) + GO/NO-GO eşiği.
- [x] **v1 backtest çalıştırıldı** — GitHub Actions workflow_dispatch.
      Sonuç `docs/BACKTEST_REPORT_v1_RESULTS.md`: **NO-GO** (Sharpe -0.31,
      578 trade, cost drag %70, cumulative DD -%122).

### Faz A2a — Küçük düzeltmeler (Hafta 3-4) — TAMAMLANDI, VERDICT: NO-GO (v2)

Cost drag ve overlapping trades'i adres aldı. Yapısal fix'ler
hedeflediği sorunları çözdü ama edge açılmadı:

- [x] Single-position mode (aynı anda 1 açık pozisyon).
- [x] `max_leverage: 3.0 → 1.0` — vol targeting kalır ama cap 1x.
- [x] `time_stop_seconds: 48h → 7 gün` — whipsaw azaltma.
- [x] `horizon_hours default: 96 → 168`.
- [x] Backtest tekrar koştur → v2 sonucu (Sharpe -0.03, 189 trade).
- [x] `docs/BACKTEST_REPORT_v1_RESULTS.md` v2 sonucu ile güncellendi
      (v1 + v2 tek belgede karşılaştırıldı).

v1 → v2 delta: Sharpe -0.31 → -0.03, trade 578 → 189 (%67 azalma), max
DD -%122 → -%46. Üç yapısal fix işini yaptı ama Sharpe hâlâ sıfıra
yakın — "hiç trade yapmakla aynı". İki bağımsız parametre setinde de
negatif Sharpe → parametre değil hipotez sorunu.

### Faz A2b — Cross-sectional momentum — ERTELENDİ

Tek-sembol TSMOM yerine Binance top-N perp evreninde aylık rebalance +
top-3 uzun denemesi. Faz A hipotezi çürütüldükten sonra bu da aynı
akademik momentum ailesinden geldiği için düşük öncelik. Ertelendi:

- [ ] `trading/strategies/tsmom_cs.py`.
- [ ] Vision adapter'ı N sembol için genişlet.
- [ ] Backtest → v3 sonucu.

Gelecekte kullanılırsa yeni bir "Faz E: Alternative strategies research"
altında açılır. `trading/` chassis üzerinde çalışır — sadece bir strategy
modülü + `walk_forward` yönlendirmesi.

### Faz B / Faz C / Faz D — İPTAL

TSMOM edge'ine bağımlı fazlardı. Edge çıkmadığı için execution katmanı
(order manager, sizing, exit ladder) ve testnet + mikro live aşamaları
şu an anlamsız. `trading/` chassis'inin altyapısı `docs/BACKTEST_REPORT_v1_RESULTS.md`
"Chassis geleceği" bölümündeki şekilde beklemede kalır.

Gelecekteki bir hipotez GO alırsa Faz C+D o zaman planlanır. Bugün
üzerinde çalışılmıyor.

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

Faz 0 ve Faz A tamamlandı. Faz A çürütüldü (NO-GO), Faz A2b ertelendi,
Faz B/C/D iptal. `trading/` chassis'i gelecekteki hipotez denemeleri için
altyapı olarak bekliyor.

**Sonraki iş:** signal-bot v5 core geliştirmesine geri dön. Ölü kod dalga 2
(RadarSnapshot slot temizliği) → v5 core iyileştirmeleri (UX, gate
mantıkları, manipülasyon riski metrikleri, alert paterni). Detay:
`docs/DEAD_CODE_AUDIT.md` "Wave 2" bölümü.
