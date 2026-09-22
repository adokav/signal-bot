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

## Silme sıralaması (bir sonraki commit)

1. `acce_unified/anti_trap.py` + test.
2. `acce_unified/long_alerts.py` + test.
3. `acce_unified/long_opportunity.py` + test.
4. `acce_unified/validation.py` + test.
5. `acce_unified/research.py` + testler (`test_research_core.py`, `test_replay_causality.py`).
6. `memecoin_radar/` + test.
7. `tests/test_build_trade_plan.py` (broken reference).
8. `engine.py` içindeki 4 formatlayıcı + `attach_snapshot_to_results` +
   `UnifiedRadarRuntime`.
9. `RadarSnapshot` içindeki 4 kullanılmayan alan + `cex.py::rank_cex_tickers`.
10. `acce_unified/__init__.py` içindeki `__all__` ve import'ları güncelle.
11. `.github/workflows/tests.yml` — silinen testlere referans varsa temizle.

Her adım kendi commit'i olmalı ki bisect kolay olsun.
