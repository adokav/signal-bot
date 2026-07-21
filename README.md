# Signal Bot ACCE — Unified Engine v4

## Tek çekirdek mimarisi

Bu depo artık diğer kripto botlarının **kod yığını değil, doğrulanmış
yeteneklerinin birleştiği tek sermaye otoritesidir**:

- `signal-bot`: sinyal, rejim, risk, pozisyon planı ve tek işlem kapısı.
- `pricemonitorx_bot`: MEXC'in en likit 100 spot paritesini ve Long İlk 5'i
  çıkaran çok-zaman-dilimli keşif; yalnızca ek kanıt.
- `phenomenonx_bot`: MEXC resmî New Listings + Spot teyit radarı; yeni coinleri
  otomatik işlem evrenine eklemez.
- `mm_trading`: t+1 icra, maliyet ve out-of-sample doğrulama disiplini.
- `theassembly`: bu mimarinin dışında ve bağımsızdır; bu entegrasyonda değiştirilmez.

Varsayılan işlem evreni:

```text
BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT, ONDOUSDT,
RENDERUSDT, PYTHUSDT, BONKUSDT, POPCATUSDT
```

Geniş radar adayları bu listeyi kendiliğinden büyütemez. Unified Engine
varsayılan olarak `SHADOW` çalışır ve hiçbir radar adayı emir yetkisi taşımaz.
Telegram'da `/longs` MEXC Likit 100 içinden Long İlk 5'i; `/radar` genel
piyasa özeti ile PhenomenonX MEXC yeni listeleme radarını birlikte; `/listings`
doğrulanmış yeni adayları, `/filtered`
İzleme Havuzunu, `/fundamentals` temel metrikleri ve `/watch` süresiz takip
listesini gösterir.

```text
UNIFIED_ENGINE_ENABLED=1
UNIFIED_ENGINE_MODE=SHADOW
UNIFIED_CEX_RADAR_ENABLED=1
UNIFIED_LISTING_RADAR_ENABLED=1
UNIFIED_SCAN_INTERVAL_SECONDS=120
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
# En az bir gerçek topluluk kaynağı gerekir. GDELT yalnız haber bağlamıdır.
X_BEARER_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=server:adokav-signal-bot:v4.8 (by /u/adokav)
UNIFIED_FUNDAMENTAL_RADAR_ENABLED=true
UNIFIED_FUNDAMENTAL_CACHE_TTL_SECONDS=1800
UNIFIED_FUNDAMENTAL_MAX_ASSETS=100
# Optional: keyless CoinGecko works at lower limits.
COINGECKO_DEMO_API_KEY=
COINGECKO_PRO_API_KEY=
UNIFIED_LISTING_CANDIDATE_TTL_HOURS=72
```

## Telegram kontrol yüzeyi

Reply keyboard değişmeyen dört ana bölüme ayrılır:

```text
🏠 Panel        | 🔎 Fırsatlar
📂 Pozisyonlar  | 🛡️ Güvenlik
```

Değişken eylemler mesaj altındaki inline kontrol yüzeyinde açılır:

```text
💧 Likit 100 · Long İlk 5 (N)
🆕 Yeni · Güçlü N | 🟡 Yeni · İzleme N
🧬 Temel N | 📣 Sosyal N
⭐ Takibim N | 🧭 Piyasa
🔄 Şimdi tara
⬅️ Kontrol paneli
```

Liste düğmeleri coin, fırsat puanı ve bağımsız temel puanı gösterir; beşli
sayfalama daha düşük
sıralı adayları da erişilebilir tutar. Coin seçilince tek bir detay ekranı
açılır; kalıcı takibe alma/çıkarma yalnız bu ekranda yapılır. Callback
navigasyonu yeni mesaj göndermek yerine mevcut kontrol mesajını düzenler.
Yeni adaylar 72 saat boyunca yeniden puanlanır. Kalıcı takip yalnız doğrulanmış
yeni adayın detayından veya o aday için `/watch_add NOVA` ile açılır; eski bir
spot coin bu yolla Yeni Listeleme penceresine sokulamaz.
Takipteki aday filtreyi geçtiğinde veya `BUILDING/HOT` aşamasına yükseldiğinde
bildirim, varsayılan olarak yalnız gerçek topluluk kanıtı `PASS` ise üretilir.
En az dört görüş, üç benzersiz yazar ve bir topluluk platformu aranır. Yetersiz
kanıt `WAIT`; kopya/eşgüdümlü ilgi, ağır olumsuzluk veya aşırı kalabalıklaşma
`BLOCK`; topluluk kaynağı yokluğu `UNAVAILABLE` olur. Haber sayısı bu kapıyı
açamaz. Aynı adaya tekrar alarm için varsayılan altı saat beklenir. Bu akış
işlem evrenini değiştirmez ve emir yetkisi vermez.

`📣 Topluluk Radarı` X ve/veya Reddit'teki görüşleri; görüş/yazar sayısı,
ivme, boğa/ayı yönü, etkileşim, güvenilir yazar, kopya metin, yeni hesap
yoğunluğu ve yazar yoğunlaşmasıyla değerlendirir. GDELT sonuçları ayrı bir
`haber bağlamı` alanında tutulur. Bağlı topluluk kaynağı yokken bot artık
`0/100 · Sessiz` puanı üretmez; eksikliği açıkça gösterir.

`🧬 Temel Radar` CoinGecko'nun tek toplu sorgusundan piyasa değeri, FDV,
dolaşımdaki/toplam/azami arz, global hacim/PD, ATH mesafesi ve MEXC hacim yoğunlaşması
ölçümlerini çıkarır. Aynı sembollü birden fazla proje başlıktan kesin
eşleştirilemiyorsa bot birini tahmin etmez; `AMBIGUOUS` gösterir. Arz verisi
Long İlk 5 ve güçlü Yeni Listeleme için zorunlu kapıdır; veri eksikse aday
gerekçesiyle bekletilir.

## İki ayrı fırsat penceresi

- **Likit 100 / Long İlk 5:** yalnız MEXC spot hacmiyle ilk 100'ün tamamı;
  15 dakikalık mumlar mum dönemi boyunca önbelleğe alınır. Günlük büyük
  düşüş, uzamış hareket, geniş makas, aşağı 1s/4s momentum, bozuk EMA yapısı,
  aşırı RSI/ATR ve eksik arz önce elenir. Kalanlar teknik `%65`, arz kalitesi
  `%25`, piyasa rejimi `%10` ile sıralanır.
- **Yeni Listeleme:** yalnız yeni resmî Spot başlığı veya iki ardışık
  `exchangeInfo` gözlemi. İlk kurulum eski başlıkları/snapshot'ı baz alır;
  futures, perpetual, pause→reopen ve eski katalog kayıtları yeni olay sayılmaz.

İki pencere de araştırma amaçlıdır ve otomatik emir yetkisi taşımaz.

`Güvenlik` merkezi çalışma modunu, gerçek emir kapısını, API hata sayısını ve
bekleyen onayları görünür tutar. Telegram üzerinden tek dokunuşla LIVE veya
otomasyon açan bir eylem yoktur. Eski klavye etiketleri ve slash komutları
geriye dönük çalışır; yeni `/panel`, `/opportunities` ve `/safety` komutları da
aynı merkezleri açar. Komut dinleyicisi varsayılan olarak beş saniyede bir
kontrol edilir (`TELEGRAM_COMMAND_POLL_INTERVAL_SECONDS=5`).

Ayrıntılı tasarım, geçiş ve geri alma kuralları:
[`docs/UNIFIED_ENGINE.md`](docs/UNIFIED_ENGINE.md).

Bu sürüm Telegram mesajlarını okunabilir karar raporu formatına çevirir.

Build Command:
```bash
pip install -r requirements.txt
```

Start Command:
```bash
python bot.py
```

Yeni env:
```text
TELEGRAM_REPORT_STYLE=HUMAN
TELEGRAM_FULL_HEARTBEAT=0
```

Debug rapora dönmek için:
```text
TELEGRAM_REPORT_STYLE=DEBUG
```


## Decision-change Telegram mode

Bu sürümde Telegram durum raporu varsayılan olarak yalnızca karar değiştiğinde gönderilir.

```text
TELEGRAM_DECISION_CHANGE_ONLY=1
TELEGRAM_FORCE_HEARTBEAT_SECONDS=0
```

Bu modda tekrar eden WAIT / NEUTRAL / aynı risk durumu mesajları gönderilmez.

Eski periyodik heartbeat davranışına dönmek için:

```text
TELEGRAM_DECISION_CHANGE_ONLY=0
```

Nadir zorunlu heartbeat istersen örneğin 6 saatte bir:

```text
TELEGRAM_FORCE_HEARTBEAT_SECONDS=21600
```


## Sprint 9 — Long Signal Sensitivity & Quality Upgrade

Bu sürüm long yönlü sinyal hassasiyetini artırır, fakat trade kalitesi için ek kalite kapısı koyar.

Eklenenler:

```text
LONG_SETUP_ENGINE_ENABLED=1
LONG_CONFLUENCE_MIN_SETUP=45
LONG_CONFLUENCE_MIN_TRADE=68
LONG_LATE_ENTRY_FILTER_ENABLED=1
LONG_MEME_MIN_VOLUME_RATIO=1.8
```

Yeni long durumları:

```text
NO_LONG_SETUP
LONG_SETUP_FORMING
LONG_TRIGGER_READY
LONG_TRADE_ALLOWED
LONG_TRADE_BLOCKED
```

Amaç: Daha erken radar, daha seçici tetik, daha sağlam long trade.


## Sprint 10 — Long Radar Explanation Upgrade

Bu sürümde Long Radar açıklaması sadeleştirildi ve detaylandırıldı.

Yeni format:
- En güçlü aday coin
- Teknik statü + insan diliyle açıklama
- Long Confluence / Radar eşiği / Trade eşiği
- Olumlu taraflar
- Eksik taraflar
- Net aksiyon

Artık `LDOUSDT long radarında | confluence 58.35 | PASS` gibi belirsiz ifade yerine:
`LDOUSDT radar’a girdi; setup eşiği geçti, trade eşiği henüz geçilmedi` mantığı kullanılır.


## Sprint 11 — Volume Surge & Relative Strength Radar

Bu sürüm, piyasa sakin olsa bile tekil coinde hacim patlaması ve BTC’ye göre relatif güçlenmeyi erken yakalamak için Coin Atak Radarı ekler.

Yeni env:
```text
VOLUME_SURGE_RADAR_ENABLED=1
VOLUME_SURGE_MIN_SCORE=55
VOLUME_SURGE_STRONG_SCORE=70
VOLUME_SURGE_TRIGGER_SCORE=80
VOLUME_SURGE_REL_STRENGTH_MIN=0.70
```

Önemli: Bu radar trade açmaz; sadece coin atağını erken yakalar. Trade için retest/trigger ve ACCE Trade Brain onayı gerekir.


## Sprint 12 — Rational FOMO Early Warning Engine

Bu sürüm `Rasyonel Önsezi Uyarısı` ekler.

Amaç:
```text
FOMO'yu yakalamak,
FOMO'ya kapılmamak.
```

Yeni durumlar:
```text
NO_FOMO
FOMO_SEED
FOMO_BUILDING
FOMO_ACCELERATING
FOMO_CHASE_RISK
```

Yeni env:
```text
RATIONAL_FOMO_ENGINE_ENABLED=1
FOMO_SEED_SCORE=40
FOMO_BUILDING_SCORE=55
FOMO_ACCELERATING_SCORE=70
FOMO_CHASE_RISK_SCORE=85
```

Bu modül trade açmaz. Trade için retest/trigger ve ACCE Trade Brain onayı gerekir.


## Sprint 13 — HTF EMA21 Trend Quality Filter

Weekly EMA21 tek başına sinyal üretmez. Long sinyal kalitesini ve risk çarpanını ayarlar.

Yeni env:
```text
HTF_EMA21_FILTER_ENABLED=1
HTF_EMA21_PERIOD=21
HTF_EMA21_KLINE_LIMIT=80
HTF_EMA21_CACHE_TTL_SECONDS=21600
HTF_EMA21_NEAR_PCT=2.0
HTF_EMA21_OVEREXTENDED_PCT=18.0
```

Etkileri:
- Long Confluence puanına +/− katkı verir.
- Trade eşiğini dinamik sıkılaştırır/gevşetir.
- Risk multiplier üretir.
- Telegram raporuna Haftalık Trend Filtresi bölümü ekler.


## Sprint 14 — Liquidation Hunt Defense Engine

Bu sürüm kaldıraç temizliği / stop avı riskine karşı savunma katmanı ekler.

Yeni env:
```text
LIQ_HUNT_DEFENSE_ENABLED=1
LIQ_HUNT_WARN_SCORE=55
LIQ_HUNT_BLOCK_SCORE=70
LIQ_HUNT_MIN_LIQ_TO_STOP=3.0
```

Modül şunları yapar:
- FOMO_CHASE + funding/basis/spread risklerini tespit eder.
- Long confluence puanını cezalandırır.
- Yüksek riskte ACCE Trade Gate içinde trade'i bloklar.
- Sweep-reclaim olasılığını radar olarak işaretler.
- Telegram raporuna `Liquidation Hunt Defense` bölümü ekler.

Prensip:
```text
Onların temizlediği yerde durma.
Temizlik bittikten sonra, fiyat reclaim ederse değerlendir.
```


## Sprint 15 — Account-Aware Position Sizing

Bu sürüm botun hesaptaki USDT/USDC bakiyeyi read-only okuyarak trade planı üretmesini sağlar.

Yeni env:
```text
MEXC_ACCOUNT_SYNC_ENABLED=1
ACCE_ACCOUNT_EQUITY_SOURCE=AUTO
ACCE_RISK_BUDGET_SOURCE=PLAN
ACCE_FIXED_RISK_PCT=0.01
ACCE_INITIAL_NOTIONAL_MAX_COLLATERAL_PCT=1.0
ACCE_DEFAULT_EXCHANGE_LEVERAGE_CORE=10
ACCE_DEFAULT_EXCHANGE_LEVERAGE_MAJOR_ALT=8
ACCE_DEFAULT_EXCHANGE_LEVERAGE_HIGH_BETA=5
ACCE_DEFAULT_EXCHANGE_LEVERAGE_MEME=3
ACCE_MIN_LIQ_TO_STOP_RATIO=3.0
```

Güvenlik:
- Bu sürüm emir göndermez.
- Sadece MEXC `SPOT_ACCOUNT_READ` yetkisiyle hesap bakiyesi okur.
- LIVE emir hâlâ kapalıdır.

Mantık:
- Stop seviyesi trade planından gelir.
- Risk bütçesi hesap equity'sine göre hesaplanır.
- Position notional risk ve available stable collateral ile sınırlandırılır.
- Effective leverage ve suggested exchange leverage ayrı gösterilir.


## Sprint 16 — Liquidation Distance First Leverage Policy

Bu sürümde kaldıraç mantığı şu şekilde düzeltilmiştir:

```text
Kaldıraç hedef değildir.
Önce stop mesafesi belirlenir.
Sonra liquidation seviyesinin stopun yeterince gerisinde kalması şart koşulur.
Pozisyon büyüklüğü risk bütçesi + available stable collateral + liquidation buffer ile hesaplanır.
Exchange leverage sadece teknik/marjin ayarıdır.
```

Yeni env:
```text
ACCE_LIQ_DISTANCE_FIRST_ENABLED=1
ACCE_TARGET_LIQ_TO_STOP_RATIO_CORE=3.0
ACCE_TARGET_LIQ_TO_STOP_RATIO_MAJOR_ALT=3.25
ACCE_TARGET_LIQ_TO_STOP_RATIO_HIGH_BETA=3.5
ACCE_TARGET_LIQ_TO_STOP_RATIO_MEME=4.0
ACCE_LIQ_DISTANCE_HARD_BLOCK=1
ACCE_LIQ_BUFFER_EXTRA_PCT=0.005
```

Telegram planında:
- effective leverage
- suggested exchange leverage
- approx liquidation price
- liq/stop ratio
- liquidation-distance-first sizing plan
gösterilir.


## Sprint 17 — Multi-Position Expansion Gate

Coin evreni güncellendi:
```text
BTCUSDT, ETHUSDT, SOLUSDT, LINKUSDT, ONDOUSDT, RENDERUSDT, PYTHUSDT, BONKUSDT, POPCATUSDT
```

Yeni çoklu pozisyon kuralı:
```text
Kâr yastığı yoksa otomatik yasak değil.
Ancak güvenli stop + güvenli liquidation buffer + portfolio heat uygun değilse yeni pozisyon yok.
```

Yeni env:
```text
ACCE_MULTI_POSITION_ENABLED=1
ACCE_ALLOW_SECOND_WITH_SAFE_STOP=1
ACCE_MAX_ACTIVE_POSITIONS=4
ACCE_MAX_OPEN_RISK_POSITIONS=2
ACCE_REQUIRE_ALL_EXISTING_STOPS_SAFE=1
ACCE_REQUIRE_ALL_EXISTING_LIQ_SAFE=1
ACCE_MAX_PORTFOLIO_HEAT_AFTER_NEW=0.12
ACCE_MAX_SAME_GROUP_POSITIONS=2
ACCE_BLOCK_DUPLICATE_SYMBOL=1
ACCE_BLOCK_NEW_IF_ANY_POSITION_WARNING=1
```


## Sprint 18 — Liquidation Cluster Radar

Bu modül piyasa genelindeki likidasyon birikimlerini şimdilik proxy olarak tahmin eder.

Gerçek heatmap sağlayıcısı yokken kullanılan proxy kaynakları:
- swing low / swing high
- round number seviyeleri
- son momentum
- volume ratio
- funding / basis
- spread kalitesi

Yeni env:
```text
LIQ_CLUSTER_RADAR_ENABLED=1
LIQ_CLUSTER_MODE=PROXY
LIQ_CLUSTER_NEAR_PCT=2.5
LIQ_CLUSTER_DOWNSIDE_SWEEP_WARN_PCT=2.0
LIQ_CLUSTER_UPSIDE_MAGNET_WARN_PCT=3.0
```

Modül şunu ayırır:
```text
Aşağı yakın long liquidation proxy = long için acele etme, sweep/reclaim bekle.
Yukarı yakın short liquidation proxy = long radar desteklenebilir, trigger bekle.
Sweep-reclaim proxy = long setup güçlenebilir, yine ACCE Gate şart.
```
