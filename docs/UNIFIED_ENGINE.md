# ACCE Unified Engine

## Karar

Birleşmenin merkezi `signal-bot`tur. Diğer depolar bağımsız emir veren botlar
olarak çalıştırılmaz; yalnızca aşağıdaki sınırlandırılmış yetenekler taşınır.

| Kaynak | Taşınan yetenek | Yeni rol | İşlem yetkisi |
|---|---|---|---|
| `signal-bot` | Rejim, sinyal, risk, ACCE gate, plan, paper execution | Tek çekirdek | Yalnızca mevcut ACCE kapıları üzerinden |
| `pricemonitorx_bot` | Likidite tabanlı geniş CEX keşfi, geç-pump cezası | `CORE_CONFLUENCE` veya `DISCOVERY_ONLY` | Yok |
| `phenomenonx_bot` | Solana/Base genç havuz ve ivme skoru | `RESEARCH_ONLY` | Yok |
| `mm_trading` | t+1 icra, maliyet farkındalığı, OOS ölçüm yaklaşımı | Doğrulama kapısı | Yok |
| `theassembly` | Hiçbir şey taşınmadı | Bağımsız ürün | Kapsam dışı |

Kaynak/izin bildirimi için [`../THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)
dosyasına bakın.

## Neden depo birleştirmesi yapılmadı?

Dört uygulamayı aynı çalışma zamanına kopyalamak; üç Telegram döngüsü, iki ayrı
state modeli, farklı veri sağlayıcıları ve birden fazla karar otoritesi üretirdi.
Bu tasarım yalnızca saf skorlayıcıları ve doğrulama kurallarını taşır. Ağ
sağlayıcıları read-only'dir; radar hatası ana sinyal döngüsünü durdurmaz.

```text
MEXC public tickers ──> PriceMonitorX CEX scorer ──┐
                                                   ├─> SHADOW SNAPSHOT ─> ACCE raporu
DexScreener ─────────> PhenomenonX DEX scorer ─────┘          │
                                                              └─X─> emir

Mevcut market features ─> Signal Brain ─> Risk/ACCE gates ───────> paper plan
```

## Değişmez güvenlik kuralları

1. `RadarCandidate.execution_eligible` her kaynak için `False`tur.
2. Snapshot seviyesinde `can_authorize_trade=False`tur.
3. `attach_snapshot_to_results` yalnızca `unified_radar` metadata'sı ekler;
   `signal`, `score` ve `actionable` alanlarını değiştirmez.
4. Sermaye evreni yalnızca `TRADE_UNIVERSE` ile operatör tarafından değiştirilir.
5. DEX verisi kontrat yetkileri, honeypot/tax, LP lock ve holder yoğunluğunu
   kanıtlamadığı için yüksek skorda dahi `RESEARCH_ONLY` kalır.
6. Unified tarama tek worker'da, ana trade döngüsünün dışında çalışır. Tam servis
   kesintisi son iyi snapshot'ı silmez.

## Varsayılan çalışma modu

```text
UNIFIED_ENGINE_ENABLED=1
UNIFIED_ENGINE_MODE=SHADOW       # SHADOW veya ADVISORY; LIVE kabul edilmez
UNIFIED_CEX_RADAR_ENABLED=1
UNIFIED_DEX_RADAR_ENABLED=1
UNIFIED_SCAN_INTERVAL_SECONDS=1800
UNIFIED_CEX_TOP_N=12
UNIFIED_CEX_MIN_QUOTE_VOLUME=500000
UNIFIED_DEX_TOP_N=8
UNIFIED_DEX_MIN_LIQUIDITY=35000
UNIFIED_DEX_MIN_H1_TRANSACTIONS=24
UNIFIED_DEX_MAX_AGE_HOURS=1080
UNIFIED_VALIDATION_MIN_TOTAL_TRADES=40
UNIFIED_VALIDATION_MIN_OOS_TRADES=16
UNIFIED_VALIDATION_OOS_FRACTION=0.40
UNIFIED_VALIDATION_MIN_PROFIT_FACTOR=1.20
UNIFIED_VALIDATION_MIN_EXPECTANCY_R=0.05
UNIFIED_VALIDATION_MAX_DRAWDOWN_R=6.0
```

`TRADE_UNIVERSE` örneği:

```text
TRADE_UNIVERSE=BTCUSDT:CORE,ETHUSDT:CORE,SOLUSDT:HIGH_BETA
```

Geçersiz veya boş override, incelenmiş dokuz varlıklı varsayılana geri döner.

## Doğrulama ve terfi

`acce_unified.validation`, açılış anında CEX radar konfluansı kaydedilmiş paper
trade'lerin gerçekleşen R sonuçlarının kuyruk bölümünü out-of-sample kabul
eder. Radar görmemiş eski/baz işlemler terfi örneklemine karıştırılmaz.
Varsayılan terfi şartları:

- en az 40 toplam trade;
- en az 16 OOS trade;
- OOS profit factor ≥ 1.20;
- OOS expectancy ≥ +0.05R;
- OOS maksimum düşüş ≤ 6R.

Başarılı sonuç yalnızca `PROMOTE_TO_ADVISORY` üretir. Otomatik emir yetkisi
vermez. Strateji parametresi aynı veri üzerinde seçilip aynı veri üzerinde
onaylanmamalıdır.

## Replay nedensellik düzeltmesi

Birleştirme sırasında tarihsel replay iki noktada sıkılaştırıldı:

- 15m/1h/4h mumları yalnızca gerçekten kapandıktan sonra özelliklere girer;
- t barında oluşan sinyal, aynı kapanıştan değil t+1 5m bar açılışından uygulanır.

Bu nedenle yeni replay sonuçları eski raporlarla doğrudan kıyaslanmamalıdır;
yeni mod etiketi `historical_replay_v2_closed_bars`tır.

## Yayına alma ve geri alma

1. Önce tüm birim/karakterizasyon testlerini çalıştır.
2. En az bir tam tarama boyunca `SHADOW` snapshot ve `/radar` çıktısını gözle.
3. Paper modda OOS terfi kapısını tamamla.
4. Donör depoları ancak ana PR birleştirilip üretim doğrulandıktan sonra
   arşivle. `theassembly` arşivlenmez ve değiştirilmez.

Hızlı geri alma:

```text
UNIFIED_ENGINE_ENABLED=0
```

Bu ayar mevcut Signal Bot karar ve risk akışını değiştirmeden yalnızca birleşik
radarı kapatır.
