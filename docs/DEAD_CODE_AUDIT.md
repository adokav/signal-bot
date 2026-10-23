# Dead Code Audit — Signal Bot v5 Core

Bu belge, `bot.py` / `run_service.py` üretim akışından **fiilen tüketilmeyen**
modüllerin envanteridir. Yalnız testlerde çağrılan veya hiç çağrılmayan kod,
`docs/EVOLUTION_ROADMAP.md` Faz 0'ın "temizlik" adımında silinecektir.

Silme kararı ayrı bir commit'e bırakılmıştır ki geri dönüş kolay olsun.

## Kesinlikle ölü (silinecek)

| Modül | Satır | Test dosyası | Not |
|---|---:|---|---|
| `acce_unified/anti_trap.py` | 168 | `tests/test_new_listing_anti_trap.py` | `assess_new_listing` engine ve bot tarafından **hiç** çağrılmıyor. |
| `acce_unified/long_alerts.py` | 158 | `tests/test_long_alerts.py` | `LongAlertTracker` kullanılmıyor; `bot.py::tactical_scan_once` kendi alert mantığını yazıyor. |
| `acce_unified/long_opportunity.py` | 127 | `tests/test_long_opportunity.py` | `LongFunnelDecision`, `classify_setup` — çağıran yok. |
| `acce_unified/validation.py` | 100 | `tests/test_unified_validation.py` | `ValidationPolicy`, `evaluate_promotion` — çağıran yok. |
| `acce_unified/research.py` | 404 | `tests/test_research_core.py`, `tests/test_replay_causality.py` | `ResearchStore`, `Decision`, `Evidence`, `ReplayClock` — `__init__.py`'de export ediliyor, çağıran yok. |
| `memecoin_radar/contracts.py` | 135 | `tests/test_memecoin_radar_contracts.py` | Solana memecoin gate şeması; provider yok, entegrasyon yok. |
| `tests/test_build_trade_plan.py` | ~250 | — | `bot.build_trade_plan` çağırıyor; bu fonksiyon `bot.py`'de yok, test main branch'te de kırık. |
| `engine.py::format_snapshot_brief` | ~20 | `tests/test_unified_engine.py` içinde? | `bot.py` kendi formatlayıcılarını yazdı. |
| `engine.py::format_liquid_long_report` | ~65 | " | " |
| `engine.py::format_listing_report` | ~60 | " | " |
| `engine.py::format_filtered_listing_report` | ~35 | " | " |
| `engine.py::attach_snapshot_to_results` | ~30 | — | Çağıran yok. |
| `engine.py::UnifiedRadarRuntime` | ~50 | — | Non-blocking runner; `bot.py` kendi `scanner_loop`'unu yazıyor. |
| `RadarSnapshot.cex_candidates` | — | `tests/test_unified_cex.py` | Engine hesaplıyor, bot UI'da göstermiyor. |
| `RadarSnapshot.social_candidates` | — | `tests/test_social_radar.py` | " |
| `RadarSnapshot.fundamental_candidates` | — | `tests/test_fundamental_radar.py` | " |
| `RadarSnapshot.listing_filtered_candidates` | — | — | Engine hesaplıyor, bot UI'da göstermiyor. |
| `acce_unified/cex.py` | 143 | `tests/test_unified_cex.py` | Yalnız `rank_cex_tickers` engine tarafından çağrılıyor, ama sonucu bot UI'da yok. `is_stable_or_synthetic`, `is_leveraged_token`, `opportunity_proxy` hala `liquid_long.py` tarafından kullanılıyor → **sadece `rank_cex_tickers` ve `cex_candidates` alanı silinecek**. |

**Toplam:** ~2,000-2,200 satır Python kaynak + ~400 satır test.

## Şüpheli (silmeden önce karar gerekli)

| Modül | Satır | Durum | Karar |
|---|---:|---|---|
| `macro_*.py` (11 dosya) | ~2,500 | `run_service.py` daemon olarak koşuyor; radar skorlarını etkilemiyor; sadece `/macro-research` endpointinde observability. | Faz A backtest harness'ının input feature'ı olarak yeniden bağlanabilir. Silme yerine "beklet" seçildi. |
| `research/` klasörü | ~600 | Ayrı Faz 0 sanity pipeline; canlı bota bağlı değil. | Faz A backtest harness'ı bunu genişletecek. **Tut.** |
| `acce_unified/observation_archive.py` | 602 | Prod flag açıkken SQLite'a yazıyor; kimse okumuyor. | Faz A backtest harness'ında okuma tarafı yazılacak. **Tut, oku tarafı ekle.** |
| `acce_unified/social.py` (870) + `SocialIntelligenceProvider` | 870 | Metadata'ya iliştirmek için var, bot UI'da `community_gate` küçük etiketi kullanıyor. | Faz A'da feature olarak test edilecek. **Tut ama entegrasyonu netleştir.** |
| `acce_unified/fundamentals.py` + `listing_fundamentals.py` | ~570 | Bot UI'da new listing card'ında görünüyor. | Aktif; **tut**. |

## Aktif olarak kullanılan (dokunulmayacak)

- `bot.py`, `acce_unified/__init__.py`, `config.py`, `engine.py` (core), `models.py`
- `acce_unified/liquid_long.py`, `listings.py`, `providers.py`, `listing_fundamentals.py`, `fundamentals.py`
- `acce_unified/tactical_long.py`, `tactical_long_data.py`, `tactical_long_engine.py`
- `acce_unified/observation_runtime.py`
- `run_service.py` (macro kısmı Faz 0'da tutulacak; sonra karar)

## Silme sıralaması

### Birinci dalga — tamamlandı

1. ✅ `acce_unified/anti_trap.py` + test.
2. ✅ `acce_unified/long_alerts.py` + test.
3. ✅ `acce_unified/long_opportunity.py` + test.
4. ✅ `acce_unified/validation.py` + test.
5. ✅ `acce_unified/research.py` + testler (`test_research_core.py`, `test_replay_causality.py`).
6. ✅ `memecoin_radar/` + test.
7. ✅ `tests/test_build_trade_plan.py` (broken reference) + `tests/golden/*.json` fixture'ları.
8. ✅ `tests/test_legacy_position_monitoring.py` (broken reference).
9. ✅ `tests/test_telegram_command_center.py` (broken reference, kaldırılmış komutlara ait).
10. ✅ `engine.py` içindeki 4 formatlayıcı + `attach_snapshot_to_results` +
    `UnifiedRadarRuntime` + `acce_unified/__init__.py::__all__` senkronu.
11. ✅ `.github/workflows/tests.yml` — silinen testlerin ve `memecoin_radar` compile
    girişinin temizliği.

**Toplam:** ~5000+ satır Python kaynak + test silindi (dead code + broken tests +
orphan golden fixtures).

### İkinci dalga — tamamlandı

**Orphan scripts:**

- ✅ `scripts/backtest_summary.py` — silindi. Eski `historical_replay_report.json`
  formatını raporluyordu; artık kimse üretmiyor.
- `scripts/build_macro_history.py`, `scripts/summarize_macro_evidence.py`
  — macro pipeline hâlâ `run_service.py` içinde çalışıyor ama bu
  script'lerin çağıranı yok; bir sonraki temizlikte değerlendirilecek.

**RadarSnapshot alanları + test bağımlılıkları:**

1. ✅ `RadarSnapshot.cex_candidates` alanı + `acce_unified/cex.py::rank_cex_tickers`
   silindi. `test_unified_cex.py` opportunity_proxy ve trade universe testlerine
   indirgendi. `cex.py`'de yalnız `liquid_long.py`'nin kullandığı üç yardımcı
   (`is_stable_or_synthetic`, `is_leveraged_token`, `opportunity_proxy`) kaldı.
2. ✅ `RadarSnapshot.social_candidates` alanı silindi. Test `listing_candidates`
   üzerinde metadata doğrulamasına dönüştürüldü.
3. ✅ `RadarSnapshot.fundamental_candidates` alanı silindi. Test aynı şekilde
   `listing_candidates` üzerinde metadata doğrulamasına dönüştürüldü.
4. ✅ `RadarSnapshot.listing_filtered_candidates` alanı silindi. Engine artık
   `partition_mexc_listings` yerine `rank_mexc_listings` çağırıyor —
   eşik-altı adaylar snapshot'a hiç girmiyor. `test_listing_report_details.py`
   fixture'ları güncellendi. `docs/UNIFIED_ENGINE.md` "yeni listeleme fırsat
   hunisi" bölümü tek çıktı modeline yeniden yazıldı.

`bot.py` UI hiçbirini göstermiyordu; hiçbir davranışsal regresyon yok.

### Üçüncü dalga — tamamlandı (2026-10-23)

Kullanıcı kararı: bot, kısa vadeli taktik radarlardan uzun vadeli long pozisyon
sistemine dönüşüyor (`docs/POSITION_LONG.md`). Bu yöne hizmet etmeyen, kanıtı
negatif ya da üretimde çalışmayan kod silindi. Silinen her şey `main`'in
`ce953eb` commit'inde duruyor; raporlar, `research/evidence/*.json` ve deneme
kaydı değişmedi.

| Silinen | Neden |
|---|---|
| `acce_unified/tactical_long.py`, `tactical_long_data.py`, `tactical_long_engine.py`, `forward_ledger.py` | BTC/ETH taktik radar: replay NEGATIVE, REJECT, susturulmuş |
| `acce_unified/radar_gate.py`, `long_alerts.py` | Taktik ve Likit-100 kapısı, stop'u ve radar kaydı. Yazı yardımcıları (`esc`, `table`, `_num`, `_clock`) aynen `acce_unified/text.py`'ye taşındı |
| `acce_unified/engine.py`, `liquid_long.py`, `providers.py`, `listings.py`, `fundamentals.py`, `listing_fundamentals.py`, `social.py`, `observation_archive.py`, `observation_runtime.py`, `config.py`, `models.py` | Likit-100 zinciri: replay NEGATIVE, REJECT; MEXC yeni listeleme radarı zaten kapalıydı |
| `acce_unified/cex.py::opportunity_proxy` | Yalnız Likit-100 kullanıyordu; `is_stable_or_synthetic` ve `is_leveraged_token` kaldı |
| `run_service.py`, `macro_*.py` (11), `scripts/` | Üretim `bot.py` ile başlıyor; makro boru hattı çalışmıyordu; atlas makro katmanı aday üretmedi |
| `trading/backtest/{tactical_replay, liquid_replay, signal_quality, reversal_replay, weekly_momentum}.py`, `trading/strategies/{tactical,liquid,reversal}_dossier.py`, 5 workflow | Kaldırılan canlı modülleri import eden kapanmış çalışma harness'ları |
| 34 test dosyası | Yukarıdakilerin testleri; `tests/test_cex.py` iki kalan yardımcıyı test eder |
| `CHANGELOG_*.md` (15), `COMPILE_STATUS.txt` | Silinmiş v4 botunun değişiklik günlükleri |
| `docs/LONG_ALERTS.md`, `UNIFIED_ENGINE.md`, `NEW_LISTING_ANTI_TRAP_DESIGN.md`, `memecoin_radar_integration_v1.md`, `RESEARCH_CORE_ARCHITECTURE.md`, `MACRO_REGIME_ENGINE_V1.md` | Artık var olmayan modüllerin tasarım belgeleri |

**Paylaşılan parçalar `trading/backtest/market_data.py`'ye taşındı.** `trade_loop`,
`majors_signals` ve `atlas`, silinen `liquid_replay`, `signal_quality` ve
`weekly_momentum` harness'larından piyasa matrisi (`Market`, `build_market`,
`load_likit_market`, `forward_returns`), mühürlü pencere sabitleri, blok
bootstrap (`block_bootstrap_means`, `diff_ci`) ve süreklilik (`continuity_breaks`,
`continuous`) yardımcılarını kullanıyordu. Bunlar satırı satırına taşındı;
`tests/test_trade_loop.py` ve `tests/test_majors_signals.py` aynı sonuçları
doğruluyor. **Parmak izi notu:** `majors_signals.FINGERPRINT_FILES` artık silinen
dosyalar yerine `market_data.py`'yi sayıyor; bu yüzden `trade_loop.code_fingerprint()`
değeri değişti. Kural değişmedi (`tests/test_trend_loop.py` canlı D1'i araştırma
döngüsüyle gün gün karşılaştırır). Kapanmış denemeler (`83c3e26b5ea98c95`,
`1fcc8f2ef5d92516`) kayıt anındaki parmak izleriyle durur; canlı D1 gölge kaydı
etkilenmez. Yeni bir `trade_loop` koşusu zaten yeni bir deneme kaydı ister.

`bot.py` 1.260 satırdan ~560 satıra indi: `/tactical`, `/longs`, `/radar`,
tarayıcı döngüleri, radar kaydı ve ileri kayıt gitti; `/altcoin`, `/new`,
`/check`, `/d1`, `/status`, `/scan` kaldı. `render.yaml`'dan `UNIFIED_*`,
`LIQUID_*`, `TACTICAL_*` ayarları kaldırıldı. Eski durum dosyasındaki
`radar_log`, `snapshot`, `tactical_*` anahtarları yüklenmez; diskteki
`tactical_forward_ledger.json` dosyasına dokunulmadı.

**Bu dalgada kalanlar (karar bekliyor):** `/new` CMC tarayıcısı ve ileri
kayıt denemesi `b57b9eb0a6073085`; `/d1` D1 gölge kaydı (`1fcc8f2ef5d92516`).
`docs/POSITION_LONG.md` §10.

### Dördüncü dalga — yeni sistem çalışınca

`trading/` araştırma şasisinden kapanmış çalışmalar (`carry_*`,
`listing_replay`, `walk_forward`/`tsmom`, `atlas`) ve `research/` Faz 0
boru hattı. `trade_loop` ve `majors_signals` kalır: D1 canlı testi ve
`docs/POSITION_LONG.md` §8.1 haftalık testi bunları kullanır.
