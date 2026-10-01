# Long uyarıları, stop seviyeleri ve radar kaydı

**Tarih:** 2026-10-01
**Kod:** `acce_unified/long_alerts.py`; entegrasyon `bot.py`; testler
`tests/test_long_alerts.py`.

Bu bir araştırma ve risk aracıdır. Emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §8, §10).

## Uyarı politikası

Kullanıcının kararı (2026-10-01): **bütün long sinyalleri, etiketli olarak
push edilir.**

- **Likit-100 İlk 3:** Bir coin listeye girdiğinde uyarı gelir. Aynı coin
  için açık kayıt varsa ya da son 12 saatte kayıt açılmışsa yeni uyarı
  gelmez.
- **BTC/ETH taktik radarı:** Kurulum READY veya TRIGGERED olduğunda uyarı
  gelir. Geçmiş testi negatif olan (REJECT) kurulumlar da artık gönderiliyor.

Her uyarıda şunlar açıkça yazar:
- kapı durumu ve geçmiş test sonucu (bugün hepsi NEGATIVE / REJECT);
- uyarı fiyatı;
- teknik geçersizlik;
- hard stop ve stop mesafesi;
- %1 hesap riski için pozisyon payı.

### Mesaj biçimi (2026-10-01, kullanıcı isteği: "tablo biçimine sok")

Uyarı, stop uyarısı ve `/radar` kaydı tablo halinde gelir. Telegram'da
eş aralıklı (`<pre>`) blok kullanılır. Satırlar telefonda kaymasın diye
≤ 32 karakterdir. Uyarı tablosunun bölümleri:

1. **Kapı durumu:** REJECT / WATCH. Likit-100'de radar puanı; taktikte
   kurulum ve 4 saatlik yapı. Tablonun hemen altında kapının **kendi
   nedeni** yazar (`status_line`): bayat veri, rejim, stop geometrisi ya da
   negatif geçmiş. Her REJECT'e tek bir anlam yüklenmez.
2. **Seviyeler:** fiyat, (taktikte giriş bölgesi), geçersizlik, hard stop ve
   mesafesi, (taktikte hedefler ve R/R), ATR, pozisyon payı.
3. **Arz ve ATH/ATL:** dolaşan/toplam/max arz, ATH/ATL fiyatı, uzaklığı ve
   tarihi.

Tablonun altında kapı nedeni, tek satırlık açıklama, geçmiş test sonucu ve
"emir yetkisi yok" notu yer alır. `/radar` kaydında her satır, uyarı anındaki
kapı durumunu da gösterir. Sağlayıcıdan gelen bütün metinler HTML olarak kaçışlanır.
Telegram biçimlendirmeyi reddederse aynı mesaj düz metin olarak yeniden
gönderilir. Bağlantı hatası ise biçim hatası sayılmaz.

### Kalite etiketi (F1, 2026-10-01)

Likit-100 uyarılarında "Kalite (F1)" satırı var. Ön-kayıtlı ve örneklem
dışında doğrulanmış filtreden gelir (`docs/SIGNAL_QUALITY.md`, KAYBI_AZALTIR):

- **KAÇIN:** Oynak (15 dk ATR > %1) ya da kovalanan (24 saatte evren
  medyanından 5 puandan fazla yükselmiş). 2024-26'da bu uyarılar, geçenlerden
  ortalama 1.25 puan daha kötüydü.
- **geçti:** Zararı daha az. Kâr ettiği gösterilmedi.
- **bilinmiyor:** Girdi eksik.

Kanıt dosyası mevcut kodla eşleşmezse etiket uygulanmaz.

**KAÇIN uyarıları susturuldu (kullanıcı kararı, 2026-10-01):**

- KAÇIN etiketli Likit-100 uyarıları Telegram'a gönderilmez.
- Radar kaydına yine girer ve takip edilir. `/radar`'da "sessiz" diye
  işaretlenir ve son 30 günün F1 karşılaştırmasına girer. Böylece filtre
  canlıda denetlenmeye devam eder.
- Açılış uyarısı gönderilmediği için stop uyarısı da gönderilmez.
- "bilinmiyor" etiketi KAÇIN değildir; gönderilir.
- Yeniden açmak için Render'da `LIQUID_AVOID_ALERTS_ENABLED=1`.

**REJECT taktik uyarıları susturuldu (kullanıcı kararı, 2026-10-01):**

- Geçmiş testi negatif olan taktik setup'lar (REJECT) Telegram'a gönderilmez.
  Gerekçe: Seviyeler 5 dakikalık yapıdan geliyor (BTC'de stop ≈ %0.4).
  1–7 günlük bir tutuşta bu stop gürültüyle tetiklenir. Ailenin geçmiş testi
  de negatif (−0.28R).
- İleri kayda ve radar kaydına yine girer, `/radar`'da "sessiz" görünür.
  Böylece kapının doğruluğu canlıda izlenmeye devam eder.
- Açılış uyarısı gönderilmediği için stop uyarısı da, "formasyon bozuldu"
  uyarısı da gönderilmez.
- WATCH setup'lar (yeterli geçmiş veri yok) gönderilmeye devam eder.
- Yeniden açmak için Render'da `TACTICAL_REJECTED_ALERTS_ENABLED=1`.

Bütün Likit-100 uyarılarını kapatmak için Render'da
`LIQUID_LONG_ALERTS_ENABLED=0`.

## Stop kuralı

Kural sabittir. Uyarıdan sonra hiçbir seviye kaydırılmaz.

- **Likit-100:** Yalnızca kapanmış 1 saatlik MEXC mumları kullanılır.
  - Teknik geçersizlik = son 12 kapanmış saatin en düşük fiyatı.
  - Hard stop = bu dip − 0.5 × ATR(14, 1 saat). Ama uyarı fiyatının en az
    1.5 × ATR altında, yani stop tek bir mumun gürültüsüne konmaz.
  - Mum verisi yetersizse stop hesaplanmaz ve uyarı bunu söyler: "bu
    sinyalle işlem yapılmamalı".
- **Taktik:** Taktik motorun kendi teknik geçersizlik ve hard stop
  seviyeleri kullanılır. Bunlar `/tactical` panelindekiyle aynıdır.
- **Pozisyon payı:** %1 ÷ stop mesafesi.
  - Örnek: stop %5 aşağıdaysa pozisyon payı sermayenin %20'si olur; stop
    tetiklenirse kayıp sermayenin %1'i kadardır.
  - Stop mesafesi %12'yi geçerse uyarı "çok geniş" der.

## Arz ve ATH/ATL verileri

Radara giren coinlerde şu satırlar gösterilir:
- **Arz:** dolaşan, toplam ve azami arz. Azami arz açıklanmamışsa
  "açıklanmamış/sınırsız" yazar. Dolaşım oranı, dolaşan arzın toplam veya
  azami arzın büyük olanına oranıdır.
- **ATH ve ATL:** fiyatları, bugünkü fiyatın bunlara uzaklığı ve tarihleri.

Nerelerde görünür:
- **Likit-100:** Uyarıda ve `/longs` panelinde. Veri, aday seçilirken zaten
  çekilen CoinGecko kaydından gelir. Uyarı anındaki kopya radar kaydına da
  yazılır.
- **BTC/ETH taktik:** Uyarıda ve `/tactical` panelinde. Aynı sağlayıcıdan
  önbellekli olarak çekilir. Kimlik eşleşmesi "Bitcoin (BTC)" ve "Ethereum
  (ETH)" başlıklarıyla yapılır, böylece aynı sembolü kullanan sahte
  tokenlarla karışmaz.
- **Veri yoksa:** Durum adıyla yazılır (örneğin "Arz ve ATH/ATL:
  PROVIDER_COOLDOWN"). Hiçbir eksik değer sıfır olarak gösterilmez.

Bu veriler bilgi amaçlıdır; sinyalin kapı kararını değiştirmez.

## Radar kaydı (`/radar`)

Her sinyal, stop seviyeleriyle birlikte kayda girer. Kayıt durum dosyasında
(`/data/core_state.json`) tutulur; en fazla 200 kayıt saklanır, açık
kayıtların hepsi korunur.

- **Stop takibi:**
  - Uyarıdan **sonra** açılan kapanmış 15 dakikalık mumların en düşük
    fiyatı hard stop'a değerse kayıt "stop" olarak kapanır ve ayrıca uyarı
    gelir.
  - Çıkış fiyatı stop seviyesidir. Mum stop'un altında açıldıysa (boşluk)
    açılış fiyatı kullanılır.
- **72 saat** dolunca kayıt son kapanış fiyatıyla kapanır. Takip verisi hiç
  gelmezse 6 saat sonra son bilinen fiyatla kapanır ve not düşülür.
- **`/radar` ekranı:** Son 72 saatte radara giren coinleri, giriş ve stop
  seviyelerini, şu anki durumlarını ve son 30 günün özetini (kapanan
  kayıtlar, stop sayısı, ortalama ve medyan sonuç) gösterir.

## Bilinmesi gerekenler

- **Stop beklentiyi değiştirmez.** Bu sinyallerin geçmiş testi negatif.
  Stop ve pozisyon büyüklüğü zararı sınırlar, ama sinyali kârlı yapmaz.
- **Gerçek çıkış daha kötü olabilir.** Stop kontrolü 15 dakikalık mumlarla
  yapılır; gerçek bir stop emri ani fitillerde stop'tan daha kötü bir
  fiyattan dolabilir. Komisyon ve kayma sonuçlara dahil değildir.
- **Erken sonuçlar anlamsız.** Radar kaydındaki özet, az sayıda kayıtla
  istatistiksel olarak bir şey söylemez. Anlamlı bir karşılaştırma için
  yüzlerce kayıt gerekir.
