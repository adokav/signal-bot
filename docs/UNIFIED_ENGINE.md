# ACCE Unified Engine

## Karar

Birleşmenin merkezi `signal-bot`tur. Diğer depolar bağımsız emir veren botlar
olarak çalıştırılmaz; yalnızca aşağıdaki sınırlandırılmış yetenekler taşınır.

| Kaynak | Taşınan yetenek | Yeni rol | İşlem yetkisi |
|---|---|---|---|
| `signal-bot` | Rejim, sinyal, risk, ACCE gate, plan, paper execution | Tek çekirdek | Yalnızca mevcut ACCE kapıları üzerinden |
| `pricemonitorx_bot` | MEXC Likit 100, çok-zaman-dilimli Long İlk 5, geç-pump/düşüş kapısı | `CORE_CONFLUENCE` veya `DISCOVERY_ONLY` | Yok |
| `phenomenonx_bot` | MEXC New Listings, Spot teyidi, hacim ve tamamlanmış 5dk ivmesi | `DISCOVERY_ONLY` / `CORE_CONFLUENCE` | Yok |
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
MEXC public tickers ──> Likit 100 ─> 15m/1s/4s + arz ─> Long İlk 5 ─┐
                                                                    ├─> SHADOW SNAPSHOT
MEXC New Listings ───> yeni Spot olayı + arz ─> PhenomenonX ────────┘       │
MEXC exchangeInfo ───> iki taramalı yeni-parite doğrulaması ─────────┘       └─X─> emir

Mevcut market features ─> Signal Brain ─> Risk/ACCE gates ───────> paper plan
```

## Değişmez güvenlik kuralları

1. `RadarCandidate.execution_eligible` her kaynak için `False`tur.
2. Snapshot seviyesinde `can_authorize_trade=False`tur.
3. `attach_snapshot_to_results` yalnızca `unified_radar` metadata'sı ekler;
   `signal`, `score` ve `actionable` alanlarını değiştirmez.
4. Sermaye evreni yalnızca `TRADE_UNIVERSE` ile operatör tarafından değiştirilir.
5. Yeni listeleme adayı yüksek skorda dahi işlem evrenini büyütemez. Spot teyidi,
   hacim, makas ve tamamlanmış 5dk ivmesi yalnızca keşif kanıtıdır; `CROWDED`
   ilk pump adayları Top listeye alınmaz fakat gerekçesiyle filtre görünümünde
   kalır.
6. Likit 100 yalnız `venue=MEXC` satırlarından kurulur. Binance fallback genel
   piyasa radarını yaşatabilir fakat MEXC Long İlk 5 üretemez.
7. Dolaşan arz ile toplam veya azami arz doğrulanmadan güçlü yeni listeleme ya
   da Long İlk 5 adayı üretilemez.
8. Unified tarama tek worker'da, ana trade döngüsünün dışında çalışır. Tam servis
   kesintisi son iyi snapshot'ı silmez.
9. Evren değiştirildiğinde çıkarılmış bir sembolde açık pozisyon varsa bot o
   sembolü kapanana kadar `MONITORING_ONLY` izler. Stop/TP, mark-to-market,
   paper reconciliation ve sinyal-değişimi çıkışı sürer; yeni giriş ve onay
   kuyruğu kesin olarak kapalı kalır.

## Varsayılan çalışma modu

```text
UNIFIED_ENGINE_ENABLED=1
UNIFIED_ENGINE_MODE=SHADOW       # SHADOW veya ADVISORY; LIVE kabul edilmez
UNIFIED_CEX_RADAR_ENABLED=1
UNIFIED_LISTING_RADAR_ENABLED=1
UNIFIED_SCAN_INTERVAL_SECONDS=120
UNIFIED_CEX_TOP_N=12
UNIFIED_CEX_MIN_QUOTE_VOLUME=500000
UNIFIED_LIQUID_UNIVERSE_SIZE=100
UNIFIED_LIQUID_ENRICHMENT_SIZE=100
UNIFIED_LIQUID_FUNDAMENTAL_SIZE=100
UNIFIED_LIQUID_LONG_TOP_N=5
UNIFIED_LIQUID_MIN_QUOTE_VOLUME=1000000
UNIFIED_LIQUID_MAX_24H_DRAWDOWN_PCT=-8
UNIFIED_LIQUID_MAX_24H_GAIN_PCT=25
UNIFIED_LIQUID_MAX_SPREAD_BPS=35
UNIFIED_LIQUID_MIN_LONG_SCORE=64
UNIFIED_LIQUID_REQUIRE_SUPPLY_DATA=true
UNIFIED_LISTING_TOP_N=5
UNIFIED_LISTING_MIN_SCORE=52
UNIFIED_LISTING_MAX_CANDIDATES=20
UNIFIED_LISTING_SEEN_FILE=mexc_seen_symbols.json
UNIFIED_LISTING_CANDIDATE_FILE=mexc_listing_candidates.json
UNIFIED_LISTING_CONFIRMATION_SCANS=2
UNIFIED_LISTING_MAX_DRAWDOWN_PCT=-8
UNIFIED_LISTING_REQUIRE_OPEN_SPOT=true
UNIFIED_LISTING_REQUIRE_SUPPLY_DATA=true
UNIFIED_SOCIAL_RADAR_ENABLED=true
UNIFIED_SOCIAL_REDDIT_ENABLED=true
UNIFIED_SOCIAL_GDELT_ENABLED=true
UNIFIED_SOCIAL_CACHE_TTL_SECONDS=600
UNIFIED_SOCIAL_MAX_ASSETS=6
UNIFIED_SOCIAL_WINDOW_HOURS=6
UNIFIED_SOCIAL_MIN_MENTIONS=4
UNIFIED_SOCIAL_MIN_UNIQUE_AUTHORS=3
UNIFIED_SOCIAL_MIN_PLATFORMS=1
UNIFIED_SOCIAL_MIN_SENTIMENT=-10
UNIFIED_SOCIAL_ALERT_GATE_REQUIRED=true
UNIFIED_SOCIAL_ALERT_COOLDOWN_SECONDS=21600
X_BEARER_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=server:adokav-signal-bot:v4.8 (by /u/adokav)
UNIFIED_FUNDAMENTAL_RADAR_ENABLED=true
UNIFIED_FUNDAMENTAL_CACHE_TTL_SECONDS=1800
UNIFIED_FUNDAMENTAL_MAX_ASSETS=100
COINGECKO_DEMO_API_KEY=
COINGECKO_PRO_API_KEY=
UNIFIED_LISTING_CANDIDATE_TTL_HOURS=72
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

## MEXC Likit 100 / Long İlk 5

Evren MEXC Spot `quoteVolume` sırasıyla kurulur; stable/sentetik ve kaldıraçlı
tokenlar çıkarılır. İlk 100'ün tamamı için 97 adet 15 dakikalık mum istenir;
aynı mum dönemi içinde sonuç önbellekten okunur. Son, henüz kapanmamış mum
hesaplamaya girmez. Önce şu hard-gate'ler uygulanır:

- 24s değişim `%−8` altı veya `%+25` üstü;
- makas `35 bps` üstü;
- fiyat > EMA20 > EMA50 ve pozitif EMA20 eğimi yokluğu;
- 1s `%−1.5` veya 4s `%−3` altı momentum;
- RSI `45–72` dışında, ATR `%6` üstü veya EMA20 uzaklığı `%6` üstü;
- doğrulanmış dolaşan arz ile toplam/azami arz yokluğu;
- `HIGH_RISK` temel aşaması ya da piyasa `CAPITULATION` rejimi.

Geçen adayın nihai puanı teknik `%65`, arz kalitesi `%25`, rejim `%10` ağırlığı
ile hesaplanır ve yalnız ilk beş gösterilir. Bu sıralama keşif metadata'sıdır;
`execution_eligible=False` değişmez.

## MEXC yeni listeleme fırsat hunisi

PhenomenonX her gözlemi önce puanlar, sonra iki görünüm üretir:

- `listing_candidates` / **Güçlü Adaylar**: yeni olay, açık Spot, arz verisi,
  düşüş ve kalite kapılarını geçen adaylar;
- `listing_filtered_candidates` / **İzleme Havuzu**: eşik altında veya
  `CROWDED` olan adaylar ve bunların açık filtre gerekçeleri.
- `fundamental_candidates` / **Temel Radar**: kimliği doğrulanmış adayların
  piyasa değeri, FDV, dolaşım, arz açıklığı ve global devir metrikleri.

Yeni olay yalnız ilk kurulumdan sonra görülmüş resmî Spot başlığı veya iki
ardışık `exchangeInfo` taramasında görülen yeni paritedir. Futures/perpetual
başlıkları, eski duyurular, eski katalog formatı ve pause→reopen hareketleri
olay üretmez.

Temel sağlayıcı sembolleri tek CoinGecko çağrısında toplar. Sembol çakışmasında
başlık + sembol tek bir kimliği doğrulamıyorsa `AMBIGUOUS`; sağlayıcı verisi
yoksa `NOT_FOUND/DATA_PENDING` döner ve puan uydurmaz. 429 yanıtı tüm sağlayıcı
için cooldown başlatır. Temel/arz verisi araştırma filtresine bağlıdır fakat
`TRADE_UNIVERSE` değerini veya emir kararını değiştiremez.

## Topluluk görüşü kapısı

Sosyal katman iki farklı kanıt türünü birbirine karıştırmaz:

- **Topluluk:** X ve Reddit gönderileri; görüş, benzersiz yazar, yön,
  etkileşim ve organiklik ölçümüne girer.
- **Haber:** GDELT başlıkları; anlatı ve kaynak bağlamı sağlar fakat topluluk
  görüşü, sessizlik veya talep teyidi sayılmaz.

Kapı durumları şunlardır:

- `PASS`: asgari görüş/yazar/platform kanıtı var, ilgi organik ve aşama
  `SEED`, `EMERGING` veya `CONFIRMED`;
- `WAIT`: sağlayıcı çalışıyor fakat veri eşiği veya ivme henüz yeterli değil;
- `BLOCK`: manipülasyon, olumsuz olay, aşırı kalabalıklaşma veya duygu eşiği
  ihlali;
- `UNAVAILABLE`: X/Reddit bağlı değil ya da bağlı sağlayıcıya ulaşılamıyor.

Varsayılan `UNIFIED_SOCIAL_ALERT_GATE_REQUIRED=true` iken takip alarmı yalnız
`PASS` ile açılır. Kararlı olay imzası ve altı saatlik cooldown aynı adayın
puan dalgalanmasıyla tekrar tekrar bildirilmesini engeller. Bu kapı yalnız
bildirim filtresidir; otomatik işlem veya sermaye evrenine ekleme yetkisi yoktur.

Yeni adaylar 72 saatlik katalogda yeniden puanlanır. Telegram listesi yalnız
aday seçtirir; kalıcı takip eylemi aday detayında açılır ve süre sınırını
kaldırır. Aday filtreden geçtiğinde veya `BUILDING/HOT` aşamasına yükseldiğinde
yalnızca takip alarmı üretilir. Bu üç durumun hiçbiri `TRADE_UNIVERSE` değerini
veya emir yetkisini değiştirmez.

Telegram reply keyboard yalnız Panel, Fırsatlar, Pozisyonlar ve Güvenlik
bölümlerini taşır. Adaylar, raporlar ve onaylar inline ekranlardır; callback
geçişleri aynı mesajı düzenler ve aday listeleri beşli sayfalanır. Güvenlik
ekranı mevcut modu gösterir fakat
LIVE/otomasyon açan bir callback sunmaz. Gelecekteki otomatik icra; risk bütçesi,
kill-switch ve çift onay tamamlanmadan bu yüzeye bağlanmamalıdır.

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
2. En az bir tam tarama boyunca `SHADOW` snapshot, `/longs`, `/radar` ve `/listings`
   çıktısını gözle.
3. Paper modda OOS terfi kapısını tamamla.
4. Donör depoları ancak ana PR birleştirilip üretim doğrulandıktan sonra
   arşivle. `theassembly` arşivlenmez ve değiştirilmez.

Hızlı geri alma:

```text
UNIFIED_ENGINE_ENABLED=0
```

Bu ayar mevcut Signal Bot karar ve risk akışını değiştirmeden yalnızca birleşik
radarı kapatır.
