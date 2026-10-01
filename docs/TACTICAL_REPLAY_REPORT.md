# Taktik Long radarı — tarihsel replay protokolü

**Ön-kayıt tarihi:** 2026-10-01 — bu belge ve karar kuralları **hiçbir sonuç
görülmeden** yazıldı.
**Deneme:** `tactical_long_engine` ailesi, trial `cff97d5d6f5b5c5d`
(`research/trials/registry.jsonl`), kod parmak izi `c27c0a510eb76779`.
**Kod:** `trading/backtest/tactical_replay.py`,
`trading/strategies/tactical_dossier.py`, workflow `tactical_replay`.

Bu bir araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Ne ölçülüyor

Canlı radarın (`TacticalLongEngine`) ürettiği uyarılar geçmişte ne sonuç
verirdi? Ölçülen büyüklük, canlı ileri kaydın (`acce_unified/forward_ledger.py`)
ölçtüğü şeyin aynısıdır: **limit dolduktan sonra T1'e stop'tan önce ulaşma
(48 saat içinde)** ve buna karşılık gelen maliyet sonrası R.

## Protokol (sabit)

| Konu | Seçim |
|---|---|
| Veri | Binance spot, data.binance.vision aylık dump; BTCUSDT, ETHUSDT, ETHBTC; 5m/15m/1h/4h/1d |
| Pencere | 72 ay (workflow'da girdi yok; farklı pencere = yeni deneme) |
| Karar anı | Her M5 kapanışı (canlı 5 dakikalık tarama), düzenli ızgara |
| Görünürlük | Her zaman diliminde kapanışı karar anında veya öncesinde olan son 240 mum |
| Bayat veri | Bir zaman diliminin son kapanışı 2 periyottan eskiyse adım atlanır (canlı adaptör kuralı) |
| Kotasyon | Son kapanmış M5 kapanışı ± 1 bp (2 bp spread varsayımı) |
| Kayıt | Sembol `state:setup` değişip READY/TRIGGERED'a girdiğinde (canlı bot kuralı), sembol başına tek açık kayıt |
| Dolum | Limit `entry_high`; fiyat değince `min(entry_high, açılış)`; süre dolarsa NOT_FILLED |
| Çıkış | Stop önce (aynı mumda ikisi de varsa stop), T1, ya da 48 saat sonra kapanıştan TIME_EXIT |
| Boşluk | Mum akışında boşluk → UNRESOLVABLE (tahmin yok) |
| Maliyet | Taban: taraf başına 5 bp komisyon + 2 bp slippage (gidiş-dönüş %0.14). Stres: komisyon ×1.5, slippage ×2 (%0.23) |
| R | Net getiri / (entry_high'tan hard_stop'a planlanan risk + maliyet) |

Motorun kendi maliyet tahmini (%0.04–0.06) yalnızca planın kabulünde
kullanılır; sonuç ölçümünde replay'in kendi maliyet modeli uygulanır.

## Karar kuralları (ön-kayıtlı)

Karar grupları: **havuz (ALL)** ve **dört setup ailesi** (TREND_PULLBACK,
BREAKOUT_RETEST, RANGE_RECLAIM, LIQUIDITY_SWEEP_RECLAIM) = 5 test.
Çoklu test düzeltmesi: Bonferroni, α = 0.05 / 5 = **0.01**.

Bir grup **PASS_CANDIDATE** olur, ancak ve ancak:

1. ≥ **100** çözümlenmiş kayıt (WIN_T1 / LOSS_STOP / TIME_EXIT);
2. ortalama R'nin blok bootstrap (blok = 5, 4000 örnek) **%99 alt sınırı > 0**;
3. stres maliyetiyle ortalama R **> 0**;
4. kronolojik **iki yarının** ortalama R'si ayrı ayrı **> 0**.

Diğer durumlar: %99 üst sınır < 0 → **NEGATIVE**; < 100 çözümlenmiş →
**INSUFFICIENT**; geri kalan → **NO_EDGE**. Sembol ve yıl kırılımları yalnızca
tanılayıcıdır (`DIAGNOSTIC_ONLY`), karar vermez.

## PASS_CANDIDATE ne demek, ne demek değil

- **Demek:** o setup ailesi canlı ileri kayıtta izlenmeye değer bir aday;
  radar etiketi WATCH'tan bir üst kanıt düzeyine ancak canlı ileri kayıt da
  ≥100 çözümlenmiş örnekte aynı yönde sonuç verirse çıkabilir.
- **Demek değil:** emir yetkisi, pozisyon boyutu ya da canlı sermaye. Replay
  Binance verisiyle, iyimser limit dolumu varsayımıyla ve parametre
  pertürbasyonu yapılmadan (motor sabitleri satır içi) koşulur.
- **NO_EDGE / NEGATIVE ise:** o setup ailesi radarda en fazla WATCH olarak
  kalır; sonuç görüldükten sonra motor ayarlanıp aynı veride yeniden
  denenirse bu yeni bir denemedir ve kayıt defterinde sayılır.

## Bilinen sınırlar (her raporda tekrarlanır)

- Binance spot, canlıda kullanılan MEXC spot'un yerine geçer; fitil ve
  derinlik farkları stop/hedef dokunuşlarını değiştirebilir.
- Geçmiş emir defteri yok; spread sabit varsayılır.
- Limit dolumu kuyruk sırası olmadan varsayılır → dolumlar iyimser
  (ters seçilim, bkz. raporun "WHAT COULD BLOW UP THIS ACCOUNT?" bölümü).
- T2 modellenmez; yalnızca T1-önce-stop ölçülür.
- BTC ve ETH kayıtları korele olabilir; blok bootstrap bunu kısmen hesaba katar.
- Beta ayrıştırması (koşulsuz long kontrolü) bu denemede yok.

## Değişmezlik

Parmak izi şu dosyaların içeriğinden hesaplanır: `acce_unified/tactical_long.py`,
`tactical_long_data.py`, `tactical_long_engine.py`, `forward_ledger.py` ve
`trading/backtest/tactical_replay.py`. Bunlardan biri değişirse
`tests/test_tactical_replay.py::test_replay_trial_is_pre_registered_for_the_current_code`
kırılır: değişiklik yeni bir denemedir ve koşulmadan önce kayda girmelidir.

## Sonuç — tactical_replay run #1 (2026-10-01): **NEGATIVE**

Koşu: GitHub Actions `tactical_replay` #1, commit `e372778`, parmak izi
`c27c0a510eb76779` (ön-kayıtlı trial ile eşleşti: "pre-registered").
Veri: Binance spot, 2020-10 → 2026-08 (2161 gün); ilk 40 gün ısınma.

**Veri kalitesi:** 622.368 karar adımı; 610.531 değerlendirildi, 11.519 ısınma,
318 eksik/bayat veri nedeniyle atlandı (%0.05), motor hatası 0, bozuk satır 0.

| Grup | Kayıt | Dolmadı | Çözüm. | İsabet | İsabet %95 | Başabaş isabet | Ort. R | %99 güven (Bonferroni) | Stres R | 1. yarı | 2. yarı | Karar |
|---|---:|---:|---:|---:|---|---:|---:|---|---:|---:|---:|---|
| **ALL** | 8397 | 601 | 7786 | 0.24 | [0.23, 0.25] | 0.40 | **−0.272** | [−0.31, −0.24] | −0.364 | −0.25 | −0.30 | **NEGATIVE** |
| BREAKOUT_RETEST | 4080 | 256 | 3818 | 0.18 | [0.17, 0.20] | 0.35 | −0.278 | [−0.33, −0.23] | −0.370 | −0.25 | −0.31 | **NEGATIVE** |
| LIQUIDITY_SWEEP_RECLAIM | 3662 | 304 | 3354 | 0.31 | [0.30, 0.33] | 0.47 | −0.255 | [−0.30, −0.21] | −0.346 | −0.23 | −0.28 | **NEGATIVE** |
| TREND_PULLBACK | 620 | 32 | 588 | 0.22 | [0.19, 0.26] | 0.37 | −0.334 | [−0.46, −0.20] | −0.431 | −0.30 | −0.37 | **NEGATIVE** |
| RANGE_RECLAIM | 35 | 9 | 26 | 0.42 | [0.26, 0.61] | 0.51 | −0.127 | [−0.87, +0.61] | −0.230 | +0.55 | −0.81 | INSUFFICIENT |

Tanılayıcı kırılımlar (karar vermez): BTCUSDT −0.321R (4294 çözümlenmiş),
ETHUSDT −0.211R (3492); yıllar 2020 −0.23, 2021 −0.20, 2022 −0.27, 2023 −0.32,
2024 −0.23, 2025 −0.33, 2026 −0.31 — **hiçbir yıl ve hiçbir sembol pozitif değil.**

### Ön-kayıtlı kurallara göre değerlendirme

- Havuz ve üç setup ailesi **NEGATIVE**: %99 güven aralığının üst sınırı bile
  sıfırın altında. İsabet oranı, maliyet sonrası başabaş için gereken orandan
  12–17 puan düşük. Sonuç yıllara ve sembollere göre tutarlı; tek bir dönemin
  eseri değil.
- RANGE_RECLAIM 6 yılda yalnızca 26 çözümlenmiş örnek üretti → **INSUFFICIENT**;
  kenarı olduğuna dair kanıt yok.
- **Hiçbir aile PASS_CANDIDATE değil.** Protokole göre dört setup ailesinin
  hiçbiri WATCH'tan yukarı çıkamaz; üçü için kanıt, uyarıların takip
  edilmesinin işlem başına yaklaşık −0.25…−0.33R kaybettireceği yönünde.

### Sonucu nasıl okumalı

- **Varsayımlar iyimserdi, sonuç yine negatif.** Limit dolumu fiyat değince
  kabul edildi (ters seçilim yok sayıldı). Gerçek dolumlarla sonuç büyük
  olasılıkla daha kötü olur. Muhafazakâr seçimler (aynı mumda stop önce, dolum
  mumunda yalnızca stop) M5 çözünürlüğünde küçük etkilidir ve 12–17 puanlık
  isabet açığını açıklayamaz.
- **Maliyet büyük pay, ama tek neden değil.** Taban ve stres maliyetindeki R
  farkından kaba bir geri hesap (tüm işlemlerde ortak risk varsayımıyla, ölçüm
  değil): planlanan risk ortalama ≈ %0.5, yani %0.14 gidiş-dönüş maliyet ≈
  0.2–0.25R. Aynı kaba hesapla maliyetsiz senaryoda bile ortalama ≈ −0.06R;
  yani maker ücretleri sıfır olsa bile pozitif beklenti görünmüyor (kesin
  ölçüm için işlem bazında brüt R gerekir).
- **Aşırı uyarı:** 6 yılda 8397 kayıt (iki sembolde günde ≈ 4). Yukarıdaki
  ≈ %0.5'lik risk tahmini doğruysa stoplar M5/M15 gürültüsüne göre dar ve
  maliyet bu dar riske oranla pahalı.
- Sonuç gördükten sonra motor ayarlanıp aynı veride yeniden denenirse bu yeni
  bir denemedir (parmak izi değişir, test kırılır) ve bu denemeyle birlikte
  sayılır.

### Canlı sisteme etkisi

- Sonuç `research/evidence/tactical_replay.json` dosyasına işlendi. Canlı
  kapı (`acce_unified/radar_gate.py`) bu dosyayı **yalnızca motor parmak izi
  eşleştiğinde** uygular; motor, ledger veya replay kodu değişirse kanıt
  "başka bir motora ait" sayılır ve setup'lar UNKNOWN → en fazla WATCH olur
  (yeni ön-kayıtlı koşu gerekir).
- NEGATIVE aileler (BREAKOUT_RETEST, LIQUIDITY_SWEEP_RECLAIM, TREND_PULLBACK)
  §38 soru 4 ve 7'de FAIL → **REJECT**. RANGE_RECLAIM (INSUFFICIENT) WATCH kalır.
- REJECT setup'lar Telegram'a push edilmez (`TACTICAL_REJECTED_ALERTS_ENABLED=1`
  ile açılabilir); `/tactical` panelinde kanıtıyla görünür ve ileri kayıt
  onları da kaydeder. `/status`, canlı (MEXC) ileri kayıt sonuçlarını replay
  beklentisiyle yan yana gösterir; fark yalnızca araştırma sebebidir, otomatik
  ayar yapılmaz.
