# Sinyal kalitesi — özellik tablosu ve filtre protokolü

**Durum:** Adım 2 tamam: keşif bitti, iki test ön-kayda alındı. Doğrulama
(adım 3) bekliyor. Protokol ve karar kuralları keşif sonuçlarından **önce**
yazıldı (2026-10-01). Keşiften sonra yapılan değişiklikler ayrıca
işaretlendi.
**Kod:** `trading/backtest/signal_quality.py`, `trading/data/universe_funding.py`,
workflow'lar `signal_quality` (keşif) ve `signal_quality_confirm` (doğrulama).

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
| Kalabalık / türev talebi | son ödenen perp funding oranı ve 3 günlük ortalaması; **baz** (perp fiyatı / spot fiyatı − 1) son kapanmış saatte ve son 24 saatin ortalaması |
| Diğer | teknik puan; İlk 3 içindeki sıra; yapı zaten bozuk mu |

Taktik: kurulum; sembol; 4 saatlik yapı; planlanan risk %; maliyet/risk
oranı; T1 ödül/risk; uyarı fiyatının giriş bölgesine uzaklığı; 1 saatlik ATR
%; motor stopu ve ATR stopu mesafeleri; 4s/24s/7g/30g değişim; coinin ve
BTC'nin 20 günlük ortalamaya uzaklığı; 4 saatlik 50 mumluk ortalamaya
uzaklık; BTC/ETH perp bazı ve funding'i.

**Baz (2026-10-01'de kullanıcı isteğiyle eklendi):** Perp ve spot için aynı
saat kullanılır: uyarı anında kapanmış son 1 saatlik mum. Perp fiyatı kontrat
çarpanına bölünür (`1000PEPE` → PEPE). Perp/spot oranı 0.8–1.25 dışındaysa
farklı varlık ya da birim demektir; değer `MISMATCH` olur, kullanılmaz.

**Eksik veri:** Hesaplanamayan özellik `None` olarak kalır ve tablolarda
kendi kovasında (`n/a`) görünür. Asla sıfır ya da nötr sayılmaz. Funding
için durumlar ayrıdır: `NO_PERP` (perp yok), `STALE` (son ödeme 12 saatten
eski), `NOT_LOADED`. Baz için de aynı durumlar ve `MISMATCH` vardır. Açık pozisyon (OI) geçmişi yalnızca günlük dosyalarda
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
- **Keşiften sonra eklenen not:** Oynaklık, adlandırılmış dört aileden biri
  değildi. Ancak tablo görülmeden yazılan beklenen yönlerde (madde 3) vardı.
  F1'de aşırı uzama ile birlikte kullanılıyor.

## Doğrulama karar kuralları (ön-kayıtlı, sonuçlardan önce)

Ön-kayıtlı test sayısı `k = 2`'dir (F1, F2). Bonferroni düzeltmesi
α = 0.05 / 2 = **0.025**. Güven aralıkları gün-kümeli bootstrap ile
hesaplanır (4000 örnek). Fark güven aralığı, iki grubun günleri birlikte
yeniden örneklenerek hesaplanır. Doğrulama penceresinin yarıları sabittir:
2024-09 → 2025-08 ve 2025-09 → 2026-08.

Bir filtre **PASS** olur, ancak ve ancak filtreden geçen uyarılarda:

1. ≥ 100 uyarı;
2. ortalama net sonucun alt sınırı > 0;
3. fazla getirinin alt sınırı > 0;
4. stres maliyetiyle (komisyon ×1.5, slippage ×2) ortalama net sonuç > 0;
5. iki yarıda hem net sonuç hem fazla getiri > 0.

Sonuç ve fazla getiri tanımları:

- **F1 (Likit-100):** Sonuç, canlı stop + 72 saat kuralının net % getirisidir.
  Fazla getiri, aynı tutma süresinde eşit ağırlıklı ilk 100'e göre
  ölçülür.
- **F2 (taktik):** Sonuç, ATR stopu + 72 saat kuralının R'sidir. Fazla getiri,
  aynı penceredeki **rastgele saat girişlerine** göre ölçülür. Bunlar aynı
  stop kuralıyla ve aynı filtreyle (perp < spot) her saat başı yapılan
  girişlerdir. Böylece uyarının, piyasanın kendisinden daha iyi bir an seçip
  seçmediği test edilir.

PASS değilse, şu iki koşul birlikte sağlanırsa **KAYBI_AZALTIR** olur:

- (geçen − elenen) ortalama net sonuç farkının alt sınırı > 0;
- bu fark iki yarıda da > 0.

Bu, filtrenin kötü sinyalleri ayırdığını ama kalan sinyallerin para
kazandırdığının gösterilmediğini söyler. Yarı koşulu keşiften sonra, doğrulama
verisi görülmeden eklendi; ölçütü sıkılaştırır. Değeri bilinmeyen uyarılar
(özellik eksik) iki gruba da girmez, ayrıca sayılır.

Diğer durumlar **NO_EFFECT**.

**Canlıya etkisi:**

- **PASS:** Filtreden geçen uyarı, kanıt satırıyla "filtreyi geçti"
  etiketi alır. Emir yetkisi yine yoktur.
- **KAYBI_AZALTIR:** Filtreden elenen uyarılar "kaçın" etiketi alır.
  Hiçbir uyarı "iyi" olarak işaretlenmez.
- **NO_EFFECT:** Değişiklik yok.

## Likit-100 için önceden beklenen yönler

Likit-100 keşif tablosu görülmeden, taktik tablosu görüldükten sonra
yazıldı (2026-10-01):

1. **Aşırı uzama:** 24 saatlik getirisi, evrene göre göreli getirisi ve
   RSI'ı en yüksek olan uyarılar en kötü fazla getiriyi verir. Gerekçe:
   Likit-100 replay'inde İlk 3, tüm hazırlardan daha kötüydü; bu, kesitsel
   kısa vadeli ters dönüşe işaret ediyor.
2. **Kalabalık:** Funding'i en yüksek olan uyarılar daha kötüdür. Gerekçe:
   yüksek funding, kaldıraçlı long kalabalığı demektir.
3. **Oynaklık:** ATR'si en yüksek olan uyarılar daha kötüdür. Gerekçe:
   piyango benzeri coinler fazla ödenir (MAX etkisi).
4. **Rejim:** Rejim, net getiriyi etkiler. Fazla getiriyi daha az etkiler,
   çünkü fazla getiri piyasa hareketini zaten çıkarır.
5. **Baz (yön önceden varsayılmıyor):** İki rakip hipotez var.
   - Kullanıcının hipotezi: Perp fiyatının spottan yüksek olması talebin
     canlı olduğunu gösterir; yüksek bazlı uyarılar daha iyidir.
   - Karşı hipotez: Yüksek baz, kaldıraçlı long kalabalığıdır; funding gibi
     ileride zayıf getiriye işaret eder.

   Baz ve funding birbirine bağlıdır: funding, primin ortalamasından
   hesaplanır. Hangi yönün geçerli olduğuna yalnızca keşif verisi karar
   verir. Bir filtre olması için etki iki yarıda da aynı yönde olmalıdır
   (AGENTS.md §6: anlatı makul göründüğü için işaret atanmaz).

Bu yönlerin tersini gösteren bir filtre ancak keşifte iki yarıda da güçlü
ve tutarlıysa seçilir.

## Keşif sonuçları (2020-10 → 2024-08, yalnızca keşif penceresi)

Tablolar yerelde, mühürlü veriyle üretildi: 2024-09-01 itibarıyla kurulan
veride sonrasına ait hiçbir mum yok. Aynı tablo `signal_quality` workflow'u
ile yeniden üretilebilir.

### BTC/ETH taktik: 4.961 uyarı

| Ölçü | Ortalama |
|---|---:|
| Ledger R (limit dolumu, T1 stoptan önce mi) | −0.25R (replay ile tutarlı) |
| Uyarı fiyatı + motor stopu + 72 saat | −0.13R |
| Uyarı fiyatı + ATR stopu + 72 saat | −0.03R |
| **Kontrol:** her saat başı rastgele giriş, aynı ATR stopu | +0.02R |

- **Uyarılar, rastgele bir saatten daha iyi bir giriş anı seçmiyor.**
- BTC 20 günlük ortalamasının üstündeyken uyarılar +0.05R, rastgele saatler
  +0.11R. İyileşme sinyalden değil, piyasanın yönünden geliyor (Faz A'da test
  edilen TSMOM).
- **Baz:**
  - Perp ≥ spot iken uyarılar −0.20R, perp < spot iken +0.04R. İki yarıda da
    aynı yön.
  - Rastgele saatlerde bu ayrım yok (+0.02 / +0.01).
  - Kullanıcının hipotezinin **tersi**: perp'in spottan yüksek olması
    uyarılar için kötü.
  - Funding'de de aynı tablo var.

### Likit-100: 33.333 uyarı

Canlı uyarı kuralı uygulandı (İlk 3 + `can_open`). Tarihsel kimlik katmanı 7
sembolü çıkardı: AUD, PAX, UST, PAXG, WBTC, BETH, WBETH.

Tüm uyarılar: stop72 net −0.68%, fazla getiri −0.55%. Her rejimde negatif.

Fazla getiri (stop + 72 saat, maliyet sonrası). Parantez içi: keşfin iki
yarısı.

| Özellik | En iyi beşte bir | En kötü beşte bir |
|---|---:|---:|
| 15 dk ATR | +0.10 (+0.23 / +0.03) | −2.04 (−1.87 / −2.27) |
| Evrene göre 24 saatlik göreli getiri | +0.11 | −2.13 (−2.04 / −2.23) |
| Hacim sırası (1–20 / 81–100) | −0.11 | −1.20 |
| 24 saatlik zirveden uzaklık | +0.09 | −1.86 |
| Teknik puan (yüksek / düşük) | −0.26 | −1.42 |
| Funding, baz (perp'i olan coinler) | düz; yarılar tutarsız | |

**Önceden yazılan beklentilerle karşılaştırma:**

- **1 Aşırı uzama, 3 Oynaklık:** doğrulandı (iki yarıda da güçlü).
- **2 Funding:** perp'i olan altlarda bilgi yok.
- **4 Rejim:** net getiriyi etkiliyor, fazla getiriyi değil.
- **5 Baz:** altlarda bilgi yok. BTC/ETH uyarılarında kullanıcının
  hipotezinin tersi.

**Perp'i olmayan coinler:** Uyarı anında perp'i olmayan coinler −1.76%
fazla getiri veriyor; olanlar −0.16%. Ancak "hiç perp'i olmayan" grubu
(−3.1%) kısmen geriye bakış içeriyor: çöken coinler hiç perp almamış. Canlıda
Binance futures erişimi de belirsiz. Test edilmedi.

**En iyi bileşim yalnızca başa baş:** ATR ≤ %1 ve göreli 24 saat ≤ 5 puan:

| | Uyarıların payı | Fazla getiri | Net |
|---|---:|---:|---:|
| Geçen | %28 | +0.12% | −0.10% |
| Elenen | %72 | −0.82% | −0.91% |

Eşik ±%20–50 değiştiğinde sonuç aynı yönde kalıyor. Filtreler kötü
uyarıları ayırıyor; kalanların para kazandırdığına dair kanıt yok.

## Ön-kayıtlı testler (adım 2)

| Test | Kural | Neden |
|---|---|---|
| **F1** Likit-100 sakin ve kovalamayan | 15 dk ATR(14) ≤ **%1.0** **ve** 24 saatlik değişim − evren medyanı ≤ **5 puan** ise uyarı geçer | Önceden beklenen iki yön (madde 1 ve 3), en güçlü ve tutarlı ayrım |
| **F2** Taktik, perp < spot | Uyarı anında son kapanmış saatte perp/spot − 1 **< 0** ise geçer | Kullanıcının baz sorusu; keşifte ters yönde, iki yarıda tutarlı |

**Seçilmeyenler:**

- **ATR stopu ile motor stopu karşılaştırması (taktik):** Keşifte 4 yılda
  bile güven aralığı sıfırı içeriyor (+0.10R [−0.01, +0.21]). 2 yıllık
  doğrulamada gücü çok düşük olurdu ve diğer testlerin α payını küçültürdü.
- **Perp'in varlığı:** Geriye bakış kirliliği ve canlı veri belirsizliği
  (yukarıda).
- **Rejim kapısı:** Fazla getiriyi değiştirmiyor. Taktikte etkisi rastgele
  saatlerle aynı.

**Beklenen sonuç (dürüst):**

- **F1:** Keşifte geçen uyarıların net sonucu −0.10%. PASS olası değil.
  Gerçekçi en iyi sonuç KAYBI_AZALTIR.
- **F2:** Gücü düşük. Keşifteki fark +0.24R; güven aralığının alt sınırı
  ancak sıfırın üstündeydi. Doğrulama penceresi yarı uzunlukta olduğu için,
  etki gerçek olsa bile NO_EFFECT çıkabilir.

**Ön-kayıt:** `signal_quality_filters` ailesi, trial `a2a9af1b364c9ac7`,
kod parmak izi `140bd27fc1a3cc31` (`research/trials/registry.jsonl`,
2026-10-01). Kayıt, doğrulama penceresinin verisi indirilmeden ve
görülmeden yapıldı. Doğrulama workflow'u: `signal_quality_confirm`.

**Değişmezlik:**

- Parmak izi (`logic_fingerprint`) sinyal, özellik ve sonuç üreten bütün
  dosyaları kapsar.
- `long_alerts.py`'den yalnızca stop/takip kurallarını ve sabitlerini kapsar.
  Telegram metni değişirse deneme bozulmaz.
- Doğrulama komutu, kayıt defterinde bu kodun denemesi yoksa çalışmayı
  reddeder.
