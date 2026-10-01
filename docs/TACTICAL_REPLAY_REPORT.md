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

## Sonuç

_Henüz koşulmadı._
