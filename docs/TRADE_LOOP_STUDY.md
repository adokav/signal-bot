# İşlem döngüsü — doğru zamanda gir, doğru zamanda çık, tekrarla

**Durum:** Keşif tamamlandı. **Aday seçme kuralını hiçbir döngü sağlamadı.**
Ön-kayıt yapılmadı; doğrulama penceresi açılmadı. Ayrıntı: "Keşif sonuçları".

Protokol herhangi bir sonuçtan **önce** yazıldı (2026-10-01, commit
`59a9314`); kod ve testler de sonuçtan önce commit edildi (`6468bbe`).

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

**Koşu:** `python -m trading.backtest.trade_loop discover`.

- **Veri:** 2024-09-01 itibarıyla mühürlü.
- **Kapsam:** 46 ay, 873 işlem. Bilinmeyen işlem yok. Verisi eksik olduğu için
  işleme dönüşemeyen giriş sinyali yok.
- **İnceleme (Codex) üzerine iki koruma eklendi ve keşif yeniden koşuldu:**
  - Evren değişince eski pozisyonlar paylarını koruyordu; bazı günler toplam
    pozisyon %100'ü aşabiliyordu. Artık o gün bütün pozisyonlar orantılı
    olarak %100'e indiriliyor; döngü hiçbir zaman kaldıraçlı değil.
  - Yalnızca o coine ait eksik 15 dakikalık mum, stopun gizlice geçilmiş
    olabileceği anlamına gelir. Böyle bir boşluğu kapsayan işlem bilinmez
    sayılır. Borsa genelindeki duruşlar bundan ayrılır; o sırada zaten işlem
    yoktur.
  - İşlem başına sonuçlar değişmedi. Portföy rakamları aşağıdaki son
    hâlidir.
- **Yarılar:** 2022-09-01'de ayrılır. α = 0.05, 7 günlük blok bootstrap.

### İşlem bazında (net %, maliyet sonrası)

| Döngü | İşlem | Ortanca gün | Kazanan | İşlem başına net (%95 güven) | Rastgele girişe göre fark (%95 güven) | Fark 1. / 2. yarı |
|---|---:|---:|---:|---|---|---|
| D1_20_10 | 340 | 13 | %40 | +12.0 [+3.3, +22.5] | +4.4 [−0.8, +11.2] | +11.4 / −0.9 |
| D1_55_20 | 161 | 22 | %35 | +17.7 [+1.2, +40.7] | −0.1 [−8.0, +8.9] | +1.9 / −1.9 |
| D2_EMA50 | 243 | 9 | %22 | +52.7 [+0.2, +146.1] | +10.6 [−47.4, +74.5] | +21.3 / +1.0 |
| D2_DIP | 129 | 15 | %9 | +28.5 [−16.1, +84.0] | −7.4 [−46.0, +23.9] | −16.0 / −1.0 |

**Ortalamalar birkaç dev işleme dayanıyor.** Hepsi gerçek 2021 boğa
hareketleri; veri hatası yok:

- BNB 33 → 512 dolar;
- DOGE 0.003 → 0.33 dolar;
- ADA, UNI, SOL.

En iyi 3 işlemin net toplamdaki payı: D1_20_10 %41, D1_55_20 %85,
D2_EMA50 %94. Bu üç işlem çıkarılınca işlem başına ortalama +7.1 / +2.7 /
+3.3.

### Portföy (döngü bir bütün olarak) ve al-tut

| Döngü | Toplam getiri | En büyük düşüş | Sharpe (1. / 2. yarı) | Piyasada kalma |
|---|---:|---:|---|---:|
| **Al-tut sepeti** | +%113 | −%92 | 0.67 (0.85 / 0.45) | %100 |
| D1_20_10 | **+%415** | **−%44** | **1.26** (2.01 / **0.29**) | %37 |
| D1_55_20 | +%116 | −%47 | 0.73 (0.91 / 0.53) | %27 |
| D2_EMA50 | +%201 | −%55 | 0.93 (1.59 / −0.23) | %27 |
| D2_DIP | +%44 | −%88 | 0.50 (0.87 / −0.06) | %81 |

**Yıl yıl, D1_20_10 ve al-tut:**

| Yıl | Döngü | Al-tut |
|---|---:|---:|
| 2020 (Kasım–Aralık) | +%29 | +%64 |
| 2021 | +%306 | +%485 |
| 2022 | **−%30** | **−%88** |
| 2023 | +%50 | +%93 |
| 2024 (Ağustos'a kadar) | −%6 | −%7 |

### Okuma

**"Doğru zamanda gir" kanıtlanmadı:**

- Hiçbir döngünün girişi, aynı çıkış kuralıyla rastgele günlerde girmekten
  anlamlı biçimde iyi değil.
- En iyisinde (D1_20_10) bile fark +4.4 puan. Güven aralığı sıfırı içeriyor
  ve 2. yarıda negatif.
- Rastgele girişler de aynı çıkışla kârlı: D1_20_10'da işlem başına +7.6.

**"Doğru zamanda çık" asıl değeri taşıyor:**

- Trend bozulunca çıkmak (kapanış son 10 günün dibinin altında), boğa
  yıllarında yükselişin yaklaşık %60'ını tuttu.
- Aynı kural, 2022 ayı piyasasında kaybı −%88'den −%30'a indirdi.
- 4 yıl boyunca toplam getiri al-tut'un yaklaşık 4 katı, en büyük düşüş
  yarısı.
- Bu, Faz A'da BTC'de görülen trend takibi profiliyle aynı: alfa değil, risk
  yönetimi.

**Ama 2. yarı zayıf:**

- 2022-09 → 2024-08'de D1_20_10'un Sharpe'ı (0.29) al-tut'unkinden (0.45)
  düşük.
- Avantajın büyük kısmı 2021'in dev trendlerinden ve 2022'nin çöküşünden
  kaçmaktan geliyor.

### Karar: önceden yazılmış kurala göre aday yok

- D1_20_10 beş koşulun üçünü sağladı (≥ 150 işlem, net alt sınır > 0,
  Sharpe ≥ al-tut).
- Rastgele girişe göre fark koşulunu ve iki yarı koşulunu sağlamadı.
- Diğer döngüler daha fazla koşulda kaldı.
- **Ön-kayıt yapılmadı; doğrulama penceresi açılmadı.** `REGISTERED` boş;
  `confirm` ve `build-confirm` kayıt olmadan çalışmayı reddediyor.
- **Canlıya etkisi:** Yok. Emir yetkisi yok.

**Not edilen hipotez (test edilmedi):** "Trend bozulunca çık" kuralı
(D1_20_10), majör sepetini risk-ayarlı olarak iyileştiriyor.

- Bu, protokolün RİSK_AZALTIR sorusu.
- Keşif sonucu görüldükten sonra seçilecek olursa, bu açıkça yazılarak ayrı
  bir ön-kayıtla ve kullanıcının onayıyla test edilmeli.
