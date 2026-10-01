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

### Spec uyum matrisi — yeniden denetim (2026-10-01, koddan doğrulandı)

Durum: **✅** yapıldı · **◐** kısmi · **✗** yapılmadı (gerekçesiyle).
İlk matristen farklar **kalın** yazıldı: araç var diye ✅ verilen ama hiçbir
canlı sinyalde kullanılmayan maddeler ◐'ye indirildi; replay kanıtıyla
güncellenenler belirtildi.

| § | Konu | Durum | Kodda ne var / eksik ne |
|---|---|---|---|
| 1 | Rol ve öncelik | ✅ | Emir yolu yok; denenen iki strateji ailesi (TSMOM, taktik setup'lar) kanıtla elendi, hiçbiri canlıya taşınmadı |
| 2 | Güven ≠ olasılık | ✅ | Radar puanı "kalibre edilmemiş sıralama"; **panel artık aile düzeyi geçmiş isabeti (replay) ile sinyal bazında kalibre olasılığı (YOK) ayrı gösteriyor** |
| 3 | Üç temel soru | ✅ | `trade_gate.py`; **taktik ve Likit-100 kapıları replay kanıtıyla besleniyor: negatif olanlar REJECT** (`radar_gate.py`, `research/evidence/`) |
| 4 | Veri katmanı | ◐ | OHLCV (MEXC canlı, Binance vision perp + spot), funding; OI yalnız araştırma adaptöründe (`binance_perp.py`). Tick, likidasyon, derinlik, CVD, dominance, TOTAL3, on-chain, sosyal zaman damgalı veri yok. Zaman damgası uyuşmazlığı reddedilir (`features.require_aligned`) |
| 5 | Hesaplar deterministik | ✅ | Tüm göstergeler Python; kod tabanında LLM çağrısı yok |
| 6 | Göreli ölçüler | ◐ | `features.py` z-score/persentil var; Likit-100 ve taktik motor hâlâ mutlak eşikli (RSI 45–72, ATR > %6, ATR çarpanları) |
| 7 | Önce rejim | ◐ | Betimleyici trend×vol (`regime.py`); taktik replay rejim bazında değil yıl/sembol bazında kırıldı; 11 rejimin çoğu eksik veri ister |
| 8 | Evren filtresi | ◐ | Likit-100: hacim, spread, stable/kaldıraçlı token dışlama; derinlik ve manipülasyon skoru yok |
| 9 | Huni | ◐ | Aşama 1–2 ve 5 var; aşama 3 (AI) ve 4 (meta-model) yok |
| 10 | Hızlı AI rolü | ✗ | LLM entegrasyonu yok; edge kanıtlanmadan eklenmesi gürültü ve maliyet ekler |
| 11 | Kalibrasyon | **◐** | Araçlar hazır (`calibration.py`: Brier, reliability, isotonic, Platt); **hiçbir canlı sinyal kalibre olasılık üretmiyor** |
| 12 | Meta-model / EV | ◐ | EV fonksiyonu var; **taktik aileler için maliyet sonrası aile EV'si ölçüldü (negatif)**; meta-model yok |
| 13 | Sürekli skor | ◐ | Likit-100 puanı sürekli, ağırlıklar elle seçilmiş; **6 yıllık replay'de İlk 3 NEGATIVE** (sepete göre −0.23…−0.33%, `docs/LIQUID_REPLAY_REPORT.md`) |
| 14 | Önce hipotez | ✅ | `report.py`, `tsmom_dossier.py`, `tactical_dossier.py`; ön-kayıt `research/trials/registry.jsonl` |
| 15 | Parametre hassasiyeti | ◐ | TSMOM'da pertürbasyon var; **taktik motorun sabitleri satır içi, pertürbasyon yapılmadı** |
| 16 | Backtest tasarımı | ◐ | Purge + embargo walk-forward (TSMOM); taktik replay'de parametre fit edilmediği için tüm pencere örneklem dışı; resmi final holdout yok; yalnız BTC/ETH (genellenemez) |
| 17 | Overfitting kontrolü | ✅ | Deneme kaydı, DSR, bootstrap, Monte Carlo, maliyet stresi, parmak izi (değişen kod = yeni deneme); saniye düzeyi gecikme stresi yok |
| 18 | Metrikler | ✅ | `metrics.py`; replay: isabet + Wilson CI, başabaş isabet, ort. R, PF, ardışık kayıp, DD |
| 19 | Decay | **◐** | TSMOM için ≥1 saat ufuklar; **taktik setup'lar için decay ölçülmedi** |
| 20 | Relative strength | ✅ | `features.relative_strength`; ETHBTC zayıflık bayrağı |
| 21 | Breakout tipleri | ✗ | OI, funding, order flow eşzamanlı verisi gerekli; BREAKOUT_RETEST tek tip ve negatif |
| 22 | Spot vs perp | ✗ | Spot ve perp verisi birleştirilmedi |
| 23 | Boyutlandırma | ◐ | `sizing.py` değerlendirici var; **bağlı hesap/işlem yok** |
| 24 | Korelasyon riski | ◐ | `portfolio.py` (bilinmeyen korelasyon = korele); korelasyon matrisi kaynağı yok |
| 25 | Portföy limitleri | ◐ | `portfolio.py` değerlendirici; bağlı değil |
| 26 | Drawdown'da risk azaltma | ◐ | `drawdown.py` merdiveni, martingale reddi; bağlı değil |
| 27 | Execution engine | ✗ | Bilinçli olarak yok: kanıtlanmış edge yok (AGENTS.md §10) |
| 28 | Bot ↔ borsa durumu | ✗ | Execution yok |
| 29 | Stop/exit | ◐ | Taktik planlarda giriş/geçersizlik/stop/hedef/süre var; ileri kayıtta 48 saat zaman çıkışı |
| 30 | Kill switch | ◐ | `kill_switch.py` değerlendirici + mandal; bağlanacak emir yolu yok |
| 31 | API güvenliği | ✅ | Secret'lar env'de, hata metni maskeleniyor, public endpoint allowlist, emir/çekim anahtarı yok |
| 32 | Paper trading | ◐ | Gölge ileri kayıt (MEXC) çalışıyor; **REJECT setup'lar da kaydediliyor ve /status "canlı vs replay" karşılaştırması gösteriyor** |
| 33 | Kademeli live | ✗ | Hiçbir strateji Stage 0'ı geçecek kanıta sahip değil |
| 34 | Drift analizi | ◐ | **Canlı vs replay karşılaştırması (betimleyici, otomatik ayar yok)**; 20/50/100/250 kayan pencere yok |
| 35 | Gelişmiş AI rolü | ✅ | Araştırmacı/denetçi; üretim kuralları yalnızca PR + CI ile değişir |
| 36 | Günlük rapor | ✗ | İşlem olmadığı için yok; `/status` kısmi |
| 37 | Trade log | ◐ | İleri kayıtta hash'li, değiştirilemez karar state'i; işlem logu yok |
| 38 | Nihai kapı | ✅ | 12 soru; **soru 4 ve 7 taktik setup'lar için artık kanıtla cevaplanıyor** |
| 39 | Aday çıktısı | ◐ | `CandidateReport` kontratı; panel bir kısmını gösteriyor |
| 40 | Statü | ✅ | REJECT / WATCH / QUALIFIED / EXECUTION_READY; **REJECT setup'lar Telegram'a push edilmez (`TACTICAL_REJECTED_ALERTS_ENABLED=1` ile açılabilir)** |
| 41 | Canlı öncesi testler | ✅ | `promotion.py`; backtest tek başına terfi ettiremez; hiçbir strateji ilk aşamayı geçmedi |
| 42 | Kırılganlık soruları | ✅ | Her backtest ve replay raporunda |
| 43 | WHAT COULD BLOW UP THIS ACCOUNT? | ✅ | Her raporun son bölümü |

### Sonraki adımlar (öncelik sırasıyla)

1. ✅ **Düzeltilmiş v2 yeniden koşusu** — run #54: NO-GO (zayıf pozitif,
   DSR 0.642, Sharpe 0.53 < B&H 0.80).
2. ✅ **Adil TSMOM testi (Faz A3)** — run #57, 72 ay, N=4 ön-kayıtlı: ladder,
   saf TSMOM ve kontrolün **üçü de NO-GO**. Saf TSMOM en iyisi (Sharpe 0.62
   vs B&H 0.59, daha küçük DD) ama walk-forward tutarsız, bootstrap CI sıfırı
   içeriyor, kârın %43'ü tek işlemden. Ayrıntı: `docs/BACKTEST_REPORT_v1_RESULTS.md`.
3. ✅ **Taktik setup'ların tarihsel replay'i** — tactical_replay run #1,
   2020-10 → 2026-08, 7786 çözümlenmiş kayıt, ön-kayıtlı trial
   `cff97d5d6f5b5c5d`: havuz **NEGATIVE** (ort. −0.27R, %99 güven
   [−0.31, −0.24], isabet %24 vs başabaş %40). BREAKOUT_RETEST,
   LIQUIDITY_SWEEP_RECLAIM, TREND_PULLBACK NEGATIVE; RANGE_RECLAIM
   INSUFFICIENT (26 örnek). Her yıl ve her iki sembolde negatif. Ayrıntı:
   `docs/TACTICAL_REPLAY_REPORT.md`.
4. **Order-flow proxy** — vision kline'larındaki taker-buy hacmi ile CVD
   yaklaşığı ve OI metrics dump'ları (§4, §21, §22).
5. ✅ **Kapı entegrasyonu** — taktik kapı `research/evidence/tactical_replay.json`
   kanıtını okuyor; yalnızca motor parmak izi eşleşirse uygular, aksi halde
   UNKNOWN. Negatif aileler REJECT; REJECT uyarıları varsayılan olarak
   push edilmez, ileri kayıt ve panelde görünmeye devam eder.
6. ✅ **Likit-100 replay'i** — liquid_replay run #1, 642 aday çift (180'i
   erken biten), 2020-10 → 2026-08: TOP3 ve ALL_READY, 4 ve 24 saatte
   **NEGATIVE** (trial `69f6387eaf32ed4f`). Kanıt canlı kapıya bağlandı.
7. ✅ **Funding carry replay'i** — carry_replay run #1, 469 perp (62'si erken
   biten): SIGNED_CARRY **PASS_CANDIDATE** (son 20 ay negatif, sermayeye göre
   risksiz getirinin altında), STATIC_CARRY NO_EDGE. `docs/CARRY_REPLAY_REPORT.md`.

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

Taktik radarın dört setup ailesi de tarihsel replay'de geçemedi: üçü NEGATIVE
(maliyet sonrası işlem başına ≈ −0.25…−0.33R), biri INSUFFICIENT. Radar
uyarıları kanıta göre takip edilmemesi gereken sinyallerdir.

Radar bu kanıtı artık gösteriyor: negatif aileler REJECT ve push edilmiyor,
ileri kayıt canlı sonuçları replay ile karşılaştırıyor.

Likit-100 listesi de geçemedi: survivorship'ten arındırılmış 6 yıllık replay'de
İlk 3, eşit ağırlıklı sepete göre maliyet sonrası −0.23…−0.33% (dört karar
grubu da NEGATIVE, her yıl ve rejimde aynı yön). Canlı kapı artık REJECT
gösteriyor. Canlıda gösterilen radarların hiçbirinin kanıtlanmış bir kenarı yok.

Funding carry replay'i (trial `e8d38a2af0f661de`) ön-kayıtlı kuralları geçen
ilk deneme oldu: **SIGNED_CARRY PASS_CANDIDATE** (nominale göre +7.8%/yıl,
güven [+3.6, +12.4], Sharpe 3.4, en büyük düşüş −8.8%). STATIC_CARRY NO_EDGE.
Ancak prim 2025–2026'da negatif, sermayeye göre getiri (≈ +3.9%/yıl) risksiz
getirinin altında, maliyet funding'in yarısını yiyor ve 68 pozisyon 1x
teminatta tasfiye seviyesine ulaştı. Canlı sermaye için yeterli değil.

**Sonraki iş:** (1) carry veri denetimi (`carry_audit`, tanılayıcı; STATIC'in
−83% düşüşünün kaynağı); (2) kısa vadeli kesitsel geri dönüş hipotezinin
hiç dokunulmamış 2017-09 → 2020-09 verisinde ön-kayıtlı testi (trial
`6a2bc19afcf3dd25`, `docs/REVERSAL_REPLAY_REPORT.md`, workflow
`reversal_replay`). Sonra: haftalık kesitsel momentum ve maker emirli carry.
Her yeni hipotez deneme kaydına önceden girer; deneme sayısı arttıkça çıta
yükselir.
