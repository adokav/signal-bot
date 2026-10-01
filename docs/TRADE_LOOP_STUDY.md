# İşlem döngüsü — doğru zamanda gir, doğru zamanda çık, tekrarla

**Durum:** Protokol. Bu belge herhangi bir sonuçtan **önce** yazıldı
(2026-10-01).

**Kullanıcının hedefi:** "Amaç; doğru zamanda pozisyon alıp, yine doğru
zamanda pozisyonu kapatmak. Sonrasında da bunu tekrarlamak."

**Kod:** `trading/backtest/trade_loop.py`.

Araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §10).

## Neden

Şimdiye kadarki testlerin çoğu **girişi tek başına** ölçtü: Bir uyarıdan
sonra 24 saat, 72 saat ya da 7–30 gün tutunca ne oluyor? Bir trader böyle
işlem yapmaz.

- **İşlem bir bütündür:** Giriş ve çıkış birlikte çalışır.
- **Çıkış kuralı fark yaratabilir:** Zayıf bir giriş, kârı koşturup zararı
  kesen bir çıkışla kârlı olabilir.
- **Döngü tekrarlanır:** Çıkıştan sonra yeni giriş koşulu oluşursa yeniden
  girilir.

Bu çalışma tam işlem döngülerini test eder. Her döngünün iki sorusu vardır:

1. **Giriş zamanı değerli mi?** Aynı çıkış kuralıyla rastgele günlerde
   girmekten daha mı iyi?
2. **Döngü bir bütün olarak değerli mi?** Majör sepetini hep tutmaktan (al-tut)
   risk-ayarlı olarak daha mı iyi?

**Daha önce bilinen:**

- Faz A'da yalnızca BTC üzerinde saf trend takibi (TSMOM) test edildi.
- Sonuç: Sharpe al-tut'la aynı (0.62 / 0.59), düşüş daha küçük (−%46 /
  −%77).
- Ama 6 yılda yalnızca 49 işlem vardı; istatistiksel güç yoktu ve kârın
  %43'ü tek işlemden geliyordu.
- Majörlerde (12–13 coin, her ay o günkü veriyle) işlem sayısı birkaç kat
  artar. Aynı sorunun güçlü bir testi ilk kez mümkün.

## Evren, zaman ve maliyet

- **Evren:** `docs/MAJORS_STUDY.md` ile aynı. Her ay o günkü veriyle kurulur:
  - BTC ve ETH;
  - 30 günlük hacme göre ilk 10;
  - en büyük meme coin;
  - olgunluk ve canlı perp şartları.

  Evren yalnızca **yeni girişleri** belirler. Açık pozisyon, coin evrenden
  çıksa bile kendi çıkış kuralıyla yönetilir.
- **Mumlar:** Günlük mumlar, kapanmış 15 dakikalık spot mumlarından kurulur
  (00:00 UTC).
  - Gün kapanışı: Günün son 15 dakikalık mumunun kapanışı. Eksikse o gün
    bilinmez.
  - Yüksek / düşük: Günün 15 dakikalık mumlarının en yükseği / en düşüğü.
- **Karar:** Her gün 00:00 UTC'de, yalnızca kapanmış günlük mumlarla.
- **İşlem fiyatı:** Karardan sonraki ilk 15 dakikalık mumun açılışı.
- **Acil durum stopu:** Gün içinde 15 dakikalık mumlarla izlenir. Fiyat stopa
  değerse çıkış fiyatı stop seviyesidir; açılış stopun altında gerçekleşirse
  açılış fiyatıdır.
- **Maliyet:** Gidiş-dönüş %0.20, stres testi %0.35.
- **Pozisyon:** Coin başına en fazla bir açık pozisyon. Çıkıştan sonra yeni
  giriş koşulu oluşursa yeniden girilir.
- **Borsadan kalkan coin:** Son işlem fiyatından çıkar.
- **Süreklilik kırılması:** Token değişimi gibi, 24 saatten uzun bir boşluk
  sonrası devam eden veri. İşlem süresince olursa işlemin sonucu bilinmez;
  dışarıda bırakılır ve sayılır.
- **Pencere sonunda açık kalan işlem:** Son kapanışla değerlenir ve ayrıca
  sayılır.

## Döngüler (sonuçtan önce sabit; toplam 4)

ATR = 20 günlük ortalama gerçek aralık. EMA = üssel hareketli ortalama.

### D1 — Kanal kırılımı (trend takibi)

| Varyant | Giriş | Çıkış (formasyon bozuldu) | Acil durum stopu |
|---|---|---|---|
| D1_20_10 | Günlük kapanış önceki **20** günün en yüksek kapanışının üstünde | Günlük kapanış önceki **10** günün en düşük kapanışının altında | Giriş − 3 × ATR |
| D1_55_20 | Günlük kapanış önceki **55** günün en yüksek kapanışının üstünde | Günlük kapanış önceki **20** günün en düşük kapanışının altında | Giriş − 3 × ATR |

Klasik "Turtle" sistemlerinin kapanış fiyatıyla çalışan sade hâli.

### D2 — Trendde geri çekilme

- **Trend:** Kapanış > EMA50 ve EMA50, 10 gün önceki değerinden yüksek.
- **Geri çekilme:** Son 5 günün en az birinde kapanış ≤ EMA20.
- **Giriş:** Trend devam ederken kapanışın yeniden EMA20'nin üstüne çıktığı
  ilk gün.
- **Acil durum stopu:** Son 5 günün en düşük fiyatı − 1 × ATR.

| Varyant | Çıkış (formasyon bozuldu) |
|---|---|
| D2_EMA50 | Günlük kapanış EMA50'nin altında: trend bozuldu |
| D2_DIP | Günlük kapanış geri çekilme dibinin altında: girişteki son 5 günün en düşüğü |

## Ölçüm

**İşlem bazında:**

- Net % (maliyet sonrası) ve R. R, girişteki acil durum stopu mesafesine
  göre hesaplanır.
- Tutma süresi; en iyi ve en kötü işlem.

**Giriş zamanı kontrolü:**

- Her işlem için, aynı coinde, aynı ay içinde, coinin evrende olduğu
  **rastgele 20 gün** seçilir.
- Bu günlerde girilip **aynı çıkış kuralı ve aynı stop mantığı**
  uygulanır.
- Fark = işlemin neti − bu 20 rastgele girişin ortalama neti.
- Rastgele seçim sabit tohumla yapılır; sonuç yeniden üretilebilir.

**Portföy (döngü bir bütün olarak):**

- Her ay, evrendeki her coine sermayenin 1/N'i ayrılır. Coin pozisyondaysa
  o pay coinin günlük getirisini alır, değilse nakitte bekler.
- Giriş ve çıkış günlerinde maliyet düşülür.
- **Kıyas:** Aynı evrenin eşit ağırlıklı al-tut sepeti.
- **Ölçüler:** Toplam getiri, en büyük düşüş, yıllık Sharpe (günlük
  getirilerden, √365) ve piyasada kalma oranı.

**İstatistik:**

- Güven aralığı, **giriş gününe göre 7 günlük takvim bloklarıyla** dairesel
  blok bootstrap ile hesaplanır (4000 örnek). Aynı hafta girilen işlemler
  birlikte örneklenir.
- Keşif yarıları 2022-09-01'de, doğrulama yarıları 2025-09-01'de ayrılır.

## Pencereler

| Pencere | Karar günleri | Ne zaman görülür |
|---|---|---|
| Keşif | 2020-11-01 → 2024-08 | Şimdi; veri 2024-09-01 itibarıyla mühürlü |
| Doğrulama | 2024-09-01 → 2026-08 | Ön-kayıttan sonra, **bir kez**, GitHub Actions'ta |

**Bilinen kirlenme:** Majörlerin 2024–26 genel seyri biliniyor. Bu dört
döngünün doğrulama penceresindeki sonucu hiç görülmedi.

## Ön-kayda aday seçme kuralı (sonuçtan önce)

Bir döngü, keşifte **hepsini** sağlarsa adaydır:

1. ≥ 150 çözümlenmiş işlem;
2. işlem başına netin alt sınırı > 0;
3. rastgele girişe göre farkın alt sınırı > 0;
4. iki yarıda hem net hem fark > 0;
5. portföy Sharpe'ı al-tut sepetininkinden ≥.

**Seçim:**

- En fazla **2** aday seçilir, her aileden (D1, D2) en fazla biri.
- Sıralama, farkın alt sınırına göre yapılır.
- **Aday yoksa ön-kayıt yapılmaz** ve doğrulama penceresi açılmaz.

## Doğrulama karar kuralları (ön-kayıtlı, sonuçtan önce)

`k` ön-kayıtlı aday sayısıdır. α = 0.05 / k. Aynı blok bootstrap kullanılır.

**PASS:** Hepsi sağlanmalı:

- ≥ 60 çözümlenmiş işlem;
- net alt sınır > 0;
- rastgele girişe göre fark alt sınırı > 0;
- stres maliyetiyle ortalama net > 0;
- iki yarıda net ve fark > 0;
- portföy Sharpe'ı ≥ al-tut.

**Diğer kararlar:**

- **ZAMANLAMA_YOK:** Net alt sınır > 0 ama rastgele girişten anlamlı biçimde
  iyi değil. Kazancı giriş zamanı değil, çıkış kuralı ya da piyasanın genel
  yükselişi açıklıyor.
- **RİSK_AZALTIR:** PASS değil; ama portföy Sharpe'ı ≥ al-tut, en büyük
  düşüş al-tut'unkinin en fazla yarısı ve iki yarıda da Sharpe ≥ al-tut.
  Getiri üretmiyor, riski azaltıyor.
- **NO_EFFECT:** Diğer durumlar.
- **INCOMPLETE_DATA (karar yok):** Şunlardan biri aşılırsa:
  - sonucu bilinmeyen işlem > %5;
  - günlük mumu bilinmeyen coin-gün > %5;
  - BTC/ETH'siz ay var;
  - hiç işlem yok.

## Canlıya etkisi (yalnızca doğrulamadan sonra)

- **PASS:**
  - Majörler için "döngü sinyali" bildirimi eklenir: giriş, çıkış, acil
    durum stopu ve kanıt etiketi.
  - Bot pozisyonun durumunu takip eder: açık, çıkış sinyali, stop.
  - Emir yetkisi yoktur.
- **RİSK_AZALTIR:** Aynı bildirim, "getiri kanıtı yok, risk azaltır" notuyla
  gönderilir.
- **ZAMANLAMA_YOK / NO_EFFECT:** Değişiklik yok.

**Güç (dürüst not):**

- Kanal kırılımı sistemleri çok sayıda küçük kayıp ve az sayıda büyük kazanç
  üretir.
- Ortalamayı birkaç büyük işlem belirler; bu yüzden güven aralıkları geniş
  olacaktır.
- 2 yıllık doğrulamada yalnızca güçlü bir etki anlamlı çıkar.

## Keşif sonuçları

Henüz yok.
