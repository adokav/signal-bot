# Sinyal kalitesi — özellik tablosu ve filtre protokolü

**Durum:** Adım 1 (keşif). Bu belgenin protokol ve karar kuralları bölümleri,
keşif sonuçlarından **önce** yazıldı (2026-10-01).
**Kod:** `trading/backtest/signal_quality.py`, `trading/data/universe_funding.py`,
workflow `signal_quality`.

Araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Neden

Mevcut iki long radarı ön-kayıtlı testlerde kaybettirdi:

- **Likit-100 İlk 3:** NEGATIVE. Maliyet öncesi fazla getiri 1–4 saatte
  ≈ 0, sonra kötüleşiyor (`docs/LIQUID_REPLAY_REPORT.md`).
- **BTC/ETH taktik:** üç kurulum NEGATIVE, biri INSUFFICIENT. Risk ≈ %0.5
  olduğundan maliyet tek başına ≈ 0.25–0.3R yiyor
  (`docs/TACTICAL_REPLAY_REPORT.md`).

Kaliteyi artırmanın dürüst yolu, kötü sinyalleri ayıklayan bir filtre
bulmaktır. Ancak aynı veride çok sayıda filtre denemek, şans eseri iyi
görünen bir filtre üretir. Bu yüzden iş üç adıma ayrıldı:

1. **Keşif tablosu (bu adım):** Her sinyal için uyarı anındaki özellikler ve
   sonrasında olanlar. Yalnızca keşif penceresi.
2. **Ön-kayıt:** Dört filtre ailesinden en fazla üç filtre seçilir. Eşikler
   yalnızca keşif verisinden belirlenir. Hepsi doğrulamadan önce kayıt
   defterine (`research/trials/registry.jsonl`) yazılır.
3. **Doğrulama:** Ön-kayıtlı filtreler doğrulama penceresinde bir kez
   çalıştırılır. Yalnızca testi geçen filtre canlıya alınır.

## Pencereler ve mühür

| Pencere | Aralık | Ne zaman görülür |
|---|---|---|
| Keşif | 2020-10 → 2024-08 | Şimdi |
| Doğrulama | 2024-09 → 2026-08 | Filtreler ön-kayda alındıktan sonra, **bir kez** |

- **Mühür:** Keşif verisi, 2024-09-01 00:00 UTC'de indirilmiş gibi kurulur.
  Bu tarihten sonra kapanan hiçbir mum indirilmez. CLI, sınırdan sonra
  kapanan tek bir mum bile görürse durur (`SealError`).
- **Uyarı penceresi:** Uyarı keşif penceresinde olmalı. 72 saatlik sonucu
  sınırı aşan uyarılar çözümsüz sayılır.
- **Bilinen kirlenme:** Likit-100 ve taktik replay'lerinin yıllık sonuçları
  (2024–2026 dahil) daha önce görüldü: filtresiz sinyal her yıl negatifti.
  Ancak herhangi bir filtrenin doğrulama penceresindeki **koşullu** etkisi
  hiç görülmedi.

## Sinyaller

- **Likit-100:** Replay'in her 15 dakikalık adımındaki İlk 3 listesi
  (`liquid_replay` faz 1, değiştirilmeden). Canlı uyarı kuralı uygulanır
  (`long_alerts.can_open`): aynı coin için açık kayıt yoksa ve son uyarıdan
  12 saat geçtiyse uyarı. Canlıdaki arz puanı ve min puan eşiği geçmişe
  dönük uygulanamıyor (Likit-100 raporundaki sınır).
- **Taktik:** Forward-ledger kayıtları, yani her READY/TRIGGERED uyarısı
  (`tactical_replay` faz 1 + kayıt, değiştirilmeden). Uyarının radar
  kaydına girip girmeyeceği (`can_open`) ayrıca işaretlenir.

## Özellikler (uyarı anında, yalnızca kapanmış mumlarla)

Likit-100:

| Aile | Özellikler |
|---|---|
| Piyasa rejimi | canlı rejim etiketi; BTC 24s/7g/30g getiri; BTC'nin 20 günlük ortalamaya uzaklığı; evren medyanı 24s ve 7g getiri; 20 günlük ortalamasının üstündeki coin oranı (genişlik) |
| Aşırı uzama | coinin 1s/4s/24s/7g değişimi; EMA20 uzaklığı ve eğimi; RSI14; 96 mumluk aralıktaki konumu; zirveden uzaklığı; 20 günlük ortalamaya uzaklığı; evrene göre 24s/7g göreli getiri |
| Likidite / oynaklık | hacim sırası; log 24s hacim; hacim oranı; 15 dk ATR %; 1 saatlik ATR %; stop mesafesi |
| Kalabalık | son ödenen perp funding oranı ve 3 günlük ortalaması |
| Diğer | teknik puan; İlk 3 içindeki sıra; yapı zaten bozuk mu |

Taktik: kurulum; sembol; 4 saatlik yapı; planlanan risk %; maliyet/risk
oranı; T1 ödül/risk; uyarı fiyatının giriş bölgesine uzaklığı; 1 saatlik ATR
%; motor stopu ve ATR stopu mesafeleri; 4s/24s/7g/30g değişim; coinin ve
BTC'nin 20 günlük ortalamaya uzaklığı; 4 saatlik 50 mumluk ortalamaya
uzaklık.

**Eksik veri:** Hesaplanamayan özellik `None` olarak kalır ve tablolarda
kendi kovasında (`n/a`) görünür. Asla sıfır ya da nötr sayılmaz. Funding
için durumlar ayrıdır: `NO_PERP` (perp yok), `STALE` (son ödeme 12 saatten
eski), `NOT_LOADED`. Açık pozisyon (OI) geçmişi yalnızca günlük dosyalarda
ve 5 dakikalık çözünürlükte. Yüz binlerce dosya gerektirdiği için bu adımda
**yok**.

## Sonuçlar

- **stop72 (canlı radar kaydı kuralı):** Uyarı fiyatından giriş. Stop, canlı
  kuralla son 40 saatlik kapanmış 1 saatlik mumlardan hesaplanır. Sonra
  kapanmış 15 dakikalık mumlarla takip edilir (`long_alerts.track_entry`):
  stop değerse stop fiyatından çıkılır (gap varsa mumun açılışından). Değmezse
  72 saat sonra kapanıştan çıkılır. Verisi bitip devam etmeyen coin (borsadan
  kalkma) son fiyattan çıkar. Verisi boşluktan sonra devam eden coinin veya
  indirme sınırına takılan uyarının sonucu çözümsüzdür.
- **Sabit ufuk:** Bir sonraki mumun açılışından 4, 24 ve 72 saat sonraki
  kapanışa kadar getiri. Aynı pencerede eşit ağırlıklı evren getirisi
  (Likit-100 replay'iyle aynı tanım).
- **Fazla getiri:** Net getiri − aynı pencerede eşit ağırlıklı evren
  getirisi. stop72 için evren getirisi, uyarının gerçek çıkışına kadar
  ölçülür.
- **MFE/MAE:** 72 saat içindeki en yüksek ve en düşük fiyatın uyarı fiyatına
  uzaklığı.
- **Taktik:** Ledger R (limit dolumu, T1 stoptan önce mi, 48 saat). Ayrıca
  uyarı fiyatından iki çıkış:
  - motorun stopu + 72 saat (canlı radar kaydı);
  - ATR stopu (Likit kuralı) + 72 saat (filtre ailesi 3'ün alternatif stopu).

**Maliyet:** Likit-100 gidiş-dönüş %0.20, taktik %0.14 (replay'lerle aynı).

## Filtre seçim kuralları (adım 2)

- En fazla **üç** filtre, şu dört aileden:
  1. **Piyasa rejimi kapısı:** BTC ve alt sepetinin günlük trendi.
  2. **Aşırı uzama cezası:** Kısa sürede çok yükselmiş coine geç giriş.
  3. **Taktik:** Oynaklığa göre daha geniş stop ve günlük trend kapısı.
  4. **Kalabalık:** Funding.
- Eşikler yalnızca keşif verisinden alınır. Sabit sayı ya da keşif
  çeyrekliği olabilir.
- Bir filtre, keşifte iki kronolojik yarıda aynı yönde çalışmıyorsa
  seçilmez.
- Seçilen filtreler, parametreleri ve aşağıdaki kurallar, doğrulama
  koşulmadan önce kayıt defterine yazılır.

## Doğrulama karar kuralları (ön-kayıtlı, sonuçlardan önce)

`k` ön-kayıtlı filtre sayısıdır (≤ 3). Bonferroni düzeltmesi α = 0.05 / k.
Güven aralıkları gün-kümeli bootstrap ile hesaplanır.

Bir filtre **PASS** olur, ancak ve ancak filtreden geçen uyarılarda:

1. ≥ 100 uyarı;
2. ortalama net stop72 sonucunun alt sınırı > 0;
3. ortalama net stop72 fazla getirisinin alt sınırı > 0;
4. stres maliyetiyle (komisyon ×1.5, slippage ×2) ortalama net sonuç > 0;
5. doğrulama penceresinin iki kronolojik yarısında hem net sonuç hem fazla
   getiri > 0.

PASS değilse ve (geçen − elenen) ortalama net stop72 farkının alt sınırı > 0
ise **KAYBI_AZALTIR** olur. Bu, filtrenin kötü sinyalleri ayırdığını ama
kalan sinyallerin para kazandırdığının gösterilmediğini söyler.

Diğer durumlar **NO_EFFECT**.

**Canlıya etkisi:**

- **PASS:** Filtreden geçen uyarı, kanıt satırıyla "filtreyi geçti"
  etiketi alır. Emir yetkisi yine yoktur.
- **KAYBI_AZALTIR:** Filtreden elenen uyarılar "kaçın" etiketi alır.
  Hiçbir uyarı "iyi" olarak işaretlenmez.
- **NO_EFFECT:** Değişiklik yok.

## Keşif sonuçları

Henüz yok.
