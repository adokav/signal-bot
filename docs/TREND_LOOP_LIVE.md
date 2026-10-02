# D1 döngüsü canlı gölge kaydı

`acce_unified/trend_loop.py`, `bot.py` (`/d1`, panelde "📈 D1 Döngü").

Bu belge, doğrulanmış D1_20_10 döngüsünün botta nasıl çalıştığını anlatır.
Döngünün kanıtı ve kuralları `docs/TRADE_LOOP_STUDY.md` belgesindedir.
Burada yalnız canlı uygulama ve araştırmadan farkları var.

- **Kanıt etiketi:** RİSK_AZALTIR (trial `1fcc8f2ef5d92516`). İki bağımsız
  testte (2018–20 ve 2024–26) düşüşü al-tut'un yarısının altında tuttu.
- **Getiri kanıtı yok.** Giriş zamanlaması rastgeleden iyi değil.
- **Gölge kayıt:** Bot emir vermez, emir yetkisi yoktur
  (`can_authorize_trade = false`). Bildirimler bilgi içindir; karar
  kullanıcınındır.
- **Kullanıcı onayı:** Bildirim biçimi 2026-10-02'de onaylandı.

## Kural (dondurulmuş, araştırmayla aynı)

- **Giriş:** Günlük kapanış, önceki 20 günün en yüksek kapanışının üstünde.
  Giriş, ertesi açılışta (00:00 UTC) yapılır.
- **Çıkış:** Günlük kapanış, önceki 10 günün en düşük kapanışının altında.
  Çıkış ertesi açılışta yapılır. Çıkış, girişten sonraki günden itibaren
  kontrol edilir.
- **Acil stop:** Giriş fiyatı − 3 × ATR(20). ATR, karar gününü de içeren son
  20 günün ortalama gerçek aralığıdır.
  - Stop gün içinde kontrol edilir.
  - Mum stop'un altında açılırsa çıkış, açılış fiyatından olur.
- **Pozisyon sayısı:** Her coinde tek pozisyon.
  - Sinyal çıkışından sonra yeni giriş, ertesi günün kararından itibaren
    serbesttir.
  - Stop'tan sonra yeni giriş, stop gününün kararından itibaren serbesttir.
- **Pay:** Ayın evren büyüklüğüne göre 1/N. Oynaklık ayarlı pay ayrıca
  gösterilir: 1/N × min(1, %50 / 30 günlük oynaklık).
- **Evren:** Her ay başında kurulur. BTC ve ETH, 30 günlük hacme göre ilk 10
  coin, ilk 10'da meme yoksa en büyük meme. En az 90 gün işlem geçmişi
  aranır. Stabil, kaldıraçlı, sarmalanmış ve emtia tokenları dışarıda kalır.
- **Evren şartı yalnız girişte aranır.** Açık pozisyon, coin evrenden çıksa
  da çıkış kuralıyla izlenir.

Bir test, canlı göstergeleri araştırmadaki `trade_loop` ile gün gün
karşılaştırır (`tests/test_trend_loop.py`). Sabitler ve kimlik listeleri de
testle eşit tutulur.

## Zamanlama

- **Günlük karar:** 00:00 UTC (03:00 TSİ) kapanışından sonra verilir.
  - Bot, son saatlik mumun yayınlanması için 2 dakika bekler.
  - Bir coinin verisi eksikse 15 dakika boyunca yeniden dener.
  - Veri hâlâ eksikse o coin için o gün karar yoktur: BİLİNMİYOR.
- **Karar sırası:** Önce gece boyunca vurulan stop'lar, sonra çıkışlar, en son
  girişler.
- **Acil stop kontrolü:** 5 dakikada bir, 15 dakikalık mumlarla yapılır.
  Henüz kapanmamış mumun o ana kadarki dibi de sayılır.

## Bildirimler

- **📈 GİRİŞ:** Şunları içerir:
  - kapanış ve 20 günlük zirve;
  - giriş referansı (00:00 UTC açılışı);
  - çıkış seviyesi (10 günlük dip);
  - acil stop ve yüzdesi;
  - pay (1/N ve oynaklık ayarlı);
  - kanıt etiketi ve "Gölge kayıt: emir yok, karar senin."
- **📉 ÇIKIŞ:** Kapanış 10 günlük dibin altına indiğinde gönderilir.
  - Mesajda kapanış, seviye, giriş, çıkış (00:00 UTC açılışı) ve sonuç yer
    alır.
  - Sonuç yüzdesi maliyet hariçtir.
- **🛑 ACİL STOP:** Gün içinde stop'a değildiğinde gönderilir.
  - Mesajda stop fiyatı (ya da mum altında açıldıysa açılış), stop'un saati ve
    sonuç yer alır.
- **Geç bildirim:** Karar ya da çıkış, fiyat noktasından 1 saatten uzun süre
  sonra bildirilirse mesaj bunu söyler.
  - Örnek: bot kesintiden sonra açıldıysa.
  - Gölge kayıt kuralın dolumunu (00:00 UTC açılışı) kullanır.
  - Mesaj, giriş referansının geçmiş fiyat olduğunu ve "şimdi al" talimatı
    olmadığını yazar.
- **/d1:**
  - Ayın evrenini gösterir.
  - Açık gölge pozisyonları gösterir: giriş, bugünkü çıkış seviyesi, acil
    stop.
  - Veri eksiklerini, kontrol edilemeyen stop'ları ve son karar zamanını
    gösterir.
  - Döngü güncel değilse uyarı ekler.
  - Döngü yüklenemediyse boş rapor yerine "pozisyonlar bilinmiyor" der.

## Canlı ile araştırma arasındaki farklar (dürüst liste)

1. **Veri kaynağı: MEXC spot.** Araştırmada Binance kullanıldı.
   - Günlük mumlar MEXC'nin kapanmış saatlik mumlarından kurulur.
   - 24 saatlik mumun hepsi yoksa gün bilinmez (yüksek, düşük ya da kapanış
     eksik kalır). O gün hiçbir karara girmez.
   - MEXC yoksa başka bir borsaya geçilmez; veri eksik sayılır.
2. **Evren MEXC hacmiyle kurulur; vadeli (perp) şartı yoktur.**
   - 2018–20 ön-tarih testinde de perp şartı yoktu ve desen tekrarlandı.
   - MEXC hacmi Binance'ten farklıdır. Evren, araştırmadakinden farklı coinler
     içerebilir.
   - **Aday ön-filtresi:** Evren kurulurken önce MEXC'nin o anki 24 saatlik
     hacmine göre ilk 40 USDT çifti alınır (BTC ve ETH her zaman dahil). Sonra
     ay başından önceki 30 günün hacmine göre sıralanır.
   - Evren ay başından sonra kurulursa ön-filtre, ay başından sonraki hacmi
     görür. İlk ay (2026-10) için evren 2 Ekim'de kuruldu.
   - 90 günlük işlem geçmişi şartı, MEXC'deki ilk günlük mumla ölçülür.
3. **Acil stop daha sık kontrol edilir.**
   - Araştırmada stop günlük dip ile kontrol edildi. Canlıda 15 dakikalık
     mumlarla, 5 dakikada bir kontrol edilir.
   - Stop seviyesi ve dolum kuralı aynıdır.
4. **Giriş referansı gerçek dolum değildir.**
   - Giriş referansı 00:00 UTC açılış fiyatıdır.
   - Gerçek alımda kayma ve komisyon vardır. Mesajlardaki sonuçlar maliyet
     hariçtir.
5. **Döngü boş başlar.**
   - Araştırmadaki gibi geçmişten pozisyon devralmaz.
   - İlk girişler, ilk karar gününden itibaren kurala göre gelir.
6. **Kaçırılan günler geriye dönük karara bağlanmaz.**
   - Bot kapalıyken kaçırılan kapanışlar için DECISION_GAP kaydı yazılır.
   - Açık pozisyonlar "takipte boşluk" ile işaretlenir; bunlar o günlerde
     kaçmış olabilecek bir çıkışı bildirir.
   - Stop kontrolü, en son kontrol edilen mumdan devam eder ve aradaki bütün
     15 dakikalık mumları parça parça okur.
   - Birikim bitmeden günlük karar gelirse o pozisyon için o gün çıkış
     kararı verilmez (BİLİNMİYOR). Stop önce gelmiş olabilir.

## Kayıt ve dayanıklılık

- **Kayıt defteri:** `/data/trend_loop_ledger.jsonl` (`TREND_LOOP_LEDGER_FILE`).
  - Yalnız ekleme yapılır. Her satır fsync ile diske yazılır.
  - Her olay kimliği bir kez yazılır.
  - Olaylar: `UNIVERSE`, `ENTRY`, `EXIT`, `STOP`, `UNKNOWN`, `DECISION_GAP`,
    `DAY`, `DELIVERED`.
  - Her olay `trial` ve `can_authorize_trade: false` taşır.
- **Bildirim kaybolmaz.**
  - Mesaj, olayıyla birlikte deftere yazılır.
  - Teslim kaydı (`DELIVERED`) gelene kadar bekler.
  - Telegram hatası olursa mesaj sırasıyla bir sonraki dakikada yeniden
    denenir.
  - Yeniden başlatmada bekleyen mesaj kaybolmaz; en kötü durumda bir kez
    tekrarlanır.
- **Durum dosyası kaybolursa açık pozisyonlar defterden geri kurulur.**
  - Geri kurulan pozisyon "takipte boşluk" ile işaretlenir.
  - Stop'u girişten itibaren yeniden kontrol edilir.
  - Defterde kapanmış görünen pozisyon, eski durum dosyası onu açık gösterse
    de yeniden açılmaz.
  - Son karar günü, `DAY` kaydından geri alınır. Aynı gün iki kez karara
    bağlanmaz.
  - Ayın evreni, `UNIVERSE` kaydından geri alınır. Sonraki verilerle yeniden
    kurulmaz.
- **Önce kayıt, sonra durum:** Giriş ve kapanış önce deftere yazılır.
  Yazılamazsa pozisyon hafızada açılmaz ya da kapanmaz; takip sürer.
- **Bozuk kayıt:** Çökme sırasında yarım kalan son satır silinir. Ortadaki
  bozuk bir satır döngüyü durdurur (fail closed). Hata `/status` ekranında ve
  sağlık ucunda görünür.

## Sağlık ve ayarlar

- **`/status`:**
  - D1 satırı: açık gölge pozisyon sayısı, evren ve son karar günü.
  - Varsa hata, adres ve gizli bilgi temizlenmiş olarak gösterilir.
- **Sağlık ucu (`/`):** `trend_loop_fresh` alanı. Şu koşullarda `ok` false
  olur:
  - Dünün kapanışı (00:00 UTC'den 1 saat 17 dakika sonrasından itibaren)
    karara bağlanmamışsa.
  - Açık pozisyon varken son 15 dakikada stop kontrolü yapılmamışsa ya da bir
    pozisyonun stop'u kontrol edilememişse.
- **Ayarlar (`render.yaml`):**

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| `TREND_LOOP_ENABLED` | `1` | Döngüyü çalıştırır |
| `TREND_LOOP_ALERTS_ENABLED` | `1` | Bildirimleri gönderir; `0` iken yalnız kayda yazar (`DELIVERED`, `sent: false`) |
| `TREND_LOOP_LEDGER_FILE` | `/data/trend_loop_ledger.jsonl` | Kayıt defteri |
| `TREND_LOOP_STOP_INTERVAL_SECONDS` | `300` | Acil stop kontrol aralığı |

## Değerlendirme

- Kural dondurulmuştur. Herhangi bir parametre değişikliği yeni bir kayıt ve
  yeni bir trial başlatır.
- Gölge kayıt, 12 aydan önce (2027-10) değerlendirilmez. Ölçüler
  doğrulamadakilerle aynıdır: işlem başına net sonuç, portföy düşüşü ve
  Sharpe, aynı evrenin al-tut'uyla karşılaştırma.
- 12 ay ve birkaç düzine işlem düşük güçtür. Sonuç tek başına kanıt etiketini
  değiştirmez.
