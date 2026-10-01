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

### Faz A — Kanıt (Hafta 2-3) — v1 NO-GO; düzeltilmiş v2 NO-GO; Faz A3 (saf TSMOM + kontrol) NO-GO

> **Run #54 (2026-09-30):** düzeltilmiş harness ile EV +%0.249/işlem,
> Sharpe 0.53 (B&H 0.80), DSR 0.642, max DD −%40 → `REJECTED_AT_BACKTEST`.
> Zayıf pozitif, anlamlı değil, B&H'nin gerisinde. Ayrıntı:
> `docs/BACKTEST_REPORT_v1_RESULTS.md`.

> **Düzeltme (2026-09-30):** v2 sonucu iki harness hatasıyla ölçüldü
> (horizon 96s < time-stop 168s; maliyet vol-ölçeğiyle çarpılmıyordu) ve
> test edilen tasarım saf TSMOM değil "TSMOM girişi + kısa ATR swing
> merdiveni" idi. v1 NO-GO geçerli; v2 "−0.03" referans alınmamalı.
> Ayrıntı: `docs/BACKTEST_REPORT_v1_RESULTS.md` "Düzeltme" bölümü.

Aşağıdaki ilk yorum düzeltme öncesi yazıldı ve artık fazla kesin:
v1 Sharpe -0.31 (agresif parametreler) → v2 Sharpe -0.03 (muhafazakar).
İki parametre setinde negatif Sharpe → "parametre değil hipotez sorunu"
denmişti; oysa iki set de aynı swing çıkış merdivenini paylaşıyordu.

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

## Faz Q — Quant spec revizyonu (2026-09-30)

43 maddelik quant araştırma/risk spec'ine göre yapılan revizyon. İlke:
kanıt yoksa statü yükselmez, bilinmeyen güvenli değildir, araştırma çıktısı
işlem yetkisi değildir.

| PR | Kapsam |
|---|---|
| #112 | `trading/research/`: metrikler, kalibrasyon, EV, sağlamlık (maliyet stresi, pertürbasyon, bootstrap, Monte Carlo, deflated Sharpe, deneme kaydı), decay, göreli özellikler, betimleyici rejim, terfi kontrol listesi, strateji rapor kontratı. İki harness hatası düzeltildi. |
| #113 | `trading/risk/`: boyutlandırma, drawdown merdiveni, portföy/korelasyon limitleri, kill switch, 12 soruluk kapı, statü sınıflandırması, aday kontratı |
| #114 | Canlı radar dürüst etiketler (azami WATCH), bayat plan → REJECT; güvenli ve yanlış-sağlıklı olmayan health |
| #115 | Taktik setup'lar için ileriye dönük gölge kayıt (paper kanıt) |

### Spec uyum matrisi

Durum: **✅** yapıldı · **◐** kısmi · **✗** bilinçli olarak yapılmadı.

| § | Konu | Durum | Nerede / neden |
|---|---|---|---|
| 1 | Rol ve öncelik sırası | ✅ | Kapı sırası: veri → kanıt → portföy → execution; AGENTS.md |
| 2 | Güven ≠ olasılık | ✅ | `calibration.py`; EV kalibre olmayan olasılığı reddeder; radar "YOK" yazar |
| 3 | Üç temel soru | ✅ | `trade_gate.py` edge_observable / edge_validated / reward_sufficient |
| 4 | Veri katmanı | ◐ | OHLCV + funding (Binance vision), MEXC spot + book ticker, OI yalnız canlı adaptörde. Tick, likidasyon, order book derinliği, CVD, dominance, TOTAL3, on-chain yok. Zaman damgası uyuşmazsa birleştirme reddedilir (`features.require_aligned`). |
| 5 | Sayısal hesap deterministik kodda | ✅ | Tüm göstergeler Python; LLM yok |
| 6 | Göreli ölçüler | ◐ | `features.py` z-score/persentil; radar kuralları hâlâ mutlak eşikli (bu yüzden WATCH) |
| 7 | Önce rejim | ◐ | Betimleyici trend×vol ve rejim bazlı performans raporu; 11 rejimin çoğu eksik veri gerektiriyor |
| 8 | Evren filtresi | ◐ | Likit-100 (hacim, spread); derinlik ve manipülasyon skoru yok |
| 9 | Huni | ◐ | Aşama 1-2 ve 5 (risk evaluator) var; aşama 3 (AI) ve 4 (meta-model) yok |
| 10 | Hızlı AI (Jev) rolü | ✗ | LLM entegrasyonu yok. Ön koşulları hazır: kayıt ve kalibrasyon |
| 11 | Kalibrasyon | ✅ | Araç hazır; kalibre edilecek veri henüz yok |
| 12 | Meta-model / EV | ◐ | EV fonksiyonu hazır; meta-model için veri yok |
| 13 | Sürekli skor | ◐ | Radar puanları sürekli ama ağırlıklar öğrenilmedi → "kalibre edilmemiş sıralama" etiketi |
| 14 | Önce hipotez | ✅ | `report.py` + `tsmom_dossier.py` |
| 15 | Parametre hassasiyeti | ✅ | Pertürbasyon; FRAGILE ve izole tepe tespiti |
| 16 | Backtest tasarımı | ◐ | Purge + embargo walk-forward; ayrı final holdout resmileştirilmedi; tek sembol (survivorship riski düşük ama genellenemez) |
| 17 | Overfitting kontrolü | ✅ | Deneme kaydı, DSR, bootstrap, MC, maliyet ve giriş gecikmesi stresi (1 saat çözünürlük; saniye düzeyi yok) |
| 18 | Metrikler | ✅ | `metrics.py` |
| 19 | Decay | ✅ | ≥1 saat ufuklar; dakika ufukları mevcut veriyle ölçülemez |
| 20 | Relative strength | ✅ | `features.relative_strength`; radarda ETHBTC zayıflık bayrağı |
| 21 | Breakout tipleri | ✗ | OI, funding ve order flow verisi gerekiyor |
| 22 | Spot vs perp | ✗ | MEXC spot ile Binance perp verisi birleştirilmedi |
| 23 | Boyutlandırma | ✅ | `sizing.py` (evaluator) |
| 24 | Korelasyon riski | ✅ | `portfolio.py`; bilinmeyen korelasyon korele sayılır. Korelasyon matrisi kaynağı henüz yok |
| 25 | Portföy limitleri | ✅ | `portfolio.py` |
| 26 | Drawdown'da risk azaltma | ✅ | `drawdown.py`; martingale reddedilir |
| 27 | Execution engine | ✗ | Bilinçli olarak yok: edge kanıtlanmadan emir yolu yalnızca risk ekler; AGENTS.md §10 ayrı ve incelenmiş bir yol ister |
| 28 | Bot ↔ borsa durumu | ✗ | Execution yok; kill switch girdisi hazır |
| 29 | Stop/exit mantığı | ◐ | Taktik planlar giriş/geçersizlik/stop/hedef/bitiş içeriyor; ileri kayıtta 48 saat time-exit |
| 30 | Kill switch | ✅ | `kill_switch.py` (evaluator + mandal); bağlanacak execution yok |
| 31 | API güvenliği | ✅ | Secret'lar env'de; hata metni maskeleniyor; public endpoint allowlist; emir/çekim anahtarı yok |
| 32 | Paper trading | ◐ | İleriye dönük gölge kayıt başladı (#115) |
| 33 | Kademeli live | ✗ | Terfi listesinde paper/live/kill-switch-drill hep NOT_RUN |
| 34 | Drift analizi | ◐ | Temel ileri kayıtta; kayan pencere karşılaştırması için veri yok |
| 35 | Gelişmiş AI rolü | ✅ | Araştırmacı/denetçi; production kuralları yalnızca PR + CI ile değişir |
| 36 | Günlük rapor | ✗ | İşlem yok; `/status` kısmi |
| 37 | Trade log | ◐ | İleri kayıt: hash'li, değiştirilemez karar state'i |
| 38 | Nihai kapı | ✅ | `trade_gate.py` |
| 39 | Aday çıktısı | ◐ | `CandidateReport` kontratı; bot bazı alanları gösteriyor, kalanlar "YOK" |
| 40 | Statü | ✅ | REJECT / WATCH / QUALIFIED / EXECUTION_READY |
| 41 | Canlı öncesi testler | ✅ | `promotion.py`; backtest tek başına terfi ettiremez |
| 42 | Kırılganlık soruları | ✅ | Her backtest raporunda |
| 43 | WHAT COULD BLOW UP THIS ACCOUNT? | ✅ | Her backtest raporunun son bölümü |

### Sonraki adımlar (öncelik sırasıyla)

1. ✅ **Düzeltilmiş v2 yeniden koşusu** — run #54: NO-GO (zayıf pozitif,
   DSR 0.642, Sharpe 0.53 < B&H 0.80).
2. ✅ **Adil TSMOM testi (Faz A3)** — run #57, 72 ay, N=4 ön-kayıtlı: ladder,
   saf TSMOM ve kontrolün **üçü de NO-GO**. Saf TSMOM en iyisi (Sharpe 0.62
   vs B&H 0.59, daha küçük DD) ama walk-forward tutarsız, bootstrap CI sıfırı
   içeriyor, kârın %43'ü tek işlemden. Ayrıntı: `docs/BACKTEST_REPORT_v1_RESULTS.md`.
3. ⏳ **Taktik setup'ların tarihsel replay'i** — `TacticalLongEngine`
   Binance vision spot M5–D1 verisiyle, canlı tarama ve ileri kayıt
   kurallarıyla geçmişte çalıştırılıyor. Protokol ve karar kuralları
   sonuçtan önce kayda girdi (trial `cff97d5d6f5b5c5d`):
   `docs/TACTICAL_REPLAY_REPORT.md`, workflow `tactical_replay`.
4. **Order-flow proxy** — vision kline'larındaki taker-buy hacmi ile CVD
   yaklaşığı ve OI metrics dump'ları (§4, §21, §22).
5. **Kapı entegrasyonu** — ileri kayıt veya replay ≥100 çözümlenmiş
   örneğe ulaştığında `oos_expectancy_positive` ve EV bu kanıttan beslensin.

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

Faz 0 tamamlandı. Faz A ailesinin dört denemesinin (v1, ladder, saf TSMOM,
kontrol) hepsi NO-GO: tek sembol BTC long-only yönlü stratejiler al-tut'u
risk-ayarlı olarak anlamlı biçimde geçemedi. Faz Q (quant spec revizyonu) araştırma, risk
ve dürüst etiketleme katmanlarını ekledi. Canlı radar SHADOW'da ve azami
statü WATCH. Emir yolu yok; kanıt oluşmadan eklenmeyecek.

**Sonraki iş:** taktik setup replay'i (ön-kayıtlı, `docs/TACTICAL_REPLAY_REPORT.md`) koşulup
önceden ilan edilen kurallara göre değerlendirilecek. Her yeni hipotez deneme kaydına önceden girer.
