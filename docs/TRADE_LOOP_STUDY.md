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

## Ön-tarih testi: 2018-09 → 2020-09 (ek, 2026-10-01, sonuçtan önce)

**Neden:**

- Keşif, "trend bozulunca çık" kuralının (D1_20_10) asıl değeri taşıdığını
  gösterdi, ama aday kuralı sağlanmadı (yukarıda).
- Kullanıcı, bu hipotezin el değmemiş verilerde test edilmesini onayladı
  (2026-10-01).
- Hipotez **keşif sonucu görüldükten sonra** seçildi; bu açıkça kayıtlıdır.
- Test penceresi, bu döngü için hiç kullanılmamış eski bir dönemdir. 2017–2020
  verisi daha önce yalnızca kısa vadeli dönüş testinde (#26) kullanıldı.
- Planlanan 2017–2024 atlası bu dönemi de görecek. Bu yüzden test,
  **atlastan önce** yapılır.

**Test edilenler (k = 2, α = 0.025):**

| Döngü | Tanım |
|---|---|
| D1_20_10 | Keşifteki kural, değişmeden |
| D1_20_10_VOL | Aynı işlemler; her işlemin payı 1/N × min(1, %50 / coinin son 30 günlük yıllık oynaklığı) |

D1_20_10_VOL'un oynaklık hedefi literatürden gelir, keşifte hiç
denenmedi. Oynaklığı bilinmeyen işlem bu portföye girmez ve sayılır.

**Evren:**

- Majör evreni, **perp şartı olmadan**: Binance'te 2019-09'dan önce perp
  yoktu.
- Diğer kurallar aynı: BTC ve ETH, 30 günlük hacme göre ilk 10, en büyük meme
  coin, olgunluk ve süreklilik.
- 2018-09'dan önce 12 üyeli ay yok. Şubat 2018'deki uzun borsa kesintisi
  süreklilik kuralı gereği bütün çiftleri "yeniden listelenmiş" sayar. Bu
  yüzden pencere 2018-09'da başlar.

**Pencere ve veri:**

- Pencere 2018-09-01 → 2020-10-01. Yarılar 2019-09-01'de ayrılır.
- Veri 2020-10-01 itibarıyla mühürlü. Bu, keşif penceresinin başlangıcıdır;
  iki pencere örtüşmez.

**Karar:**

- "Doğrulama karar kuralları" bölümündeki kurallar aynen uygulanır: PASS /
  ZAMANLAMA_YOK / RİSK_AZALTIR / NO_EFFECT / INCOMPLETE_DATA.
- Asıl soru **RİSK_AZALTIR**: Döngü bir bütün olarak, risk-ayarlı al-tut'tan
  iyi mi? Ölçüler: Sharpe hem tüm dönemde hem iki yarıda ≥ al-tut, en büyük
  düşüş al-tut'unkinin en fazla yarısı.
- Sharpe farkının güven aralığı da raporlanır: 30 günlük bloklarla
  bootstrap. Bu yalnızca bilgi içindir, kararı değiştirmez.

**Sonucun etkisi:**

- **İki pencerede de geçerse:** Bu dönem ve keşif, hipotezi destekler. Son söz
  yine 2024-09 → 2026-08 doğrulamasınındır; o pencere atlastan çıkan
  hipotezlerle birlikte, bir kez açılır.
- **Burada kalırsa:** "Trend bozulunca çık" kuralının keşifteki başarısı
  2021–2022'ye özgü sayılır.
- **Her durumda:** Emir yetkisi yoktur.

### Ön-tarih testi sonucu (trial `83c3e26b5ea98c95`, bir kez koşuldu)

**Koşu bilgileri:**

- Komut: `python -m trading.backtest.trade_loop prehistory`.
- Kod parmak izi `fdb566a67b26e79e`. Ön-kayıt `3a00484`, koşudan önce gönderildi.
- Veri 2020-10-01 itibarıyla mühürlü.
- Kapsam: 25 ay, 200 işlem. Bilinmeyen işlem yok, atlanan giriş yok.

| | D1_20_10 | D1_20_10_VOL | Al-tut sepeti |
|---|---:|---:|---:|
| Toplam getiri | **+%64** | +%49 | −%19 |
| En büyük düşüş | −%30 | **−%21** | −%75 |
| Sharpe (1. / 2. yarı) | 0.79 (0.58 / 1.00) | **0.88** (0.81 / 0.96) | 0.33 (0.03 / 0.60) |
| Piyasada kalma | %37 | %23 | %100 |
| 2018 (Eylül–Aralık) | −%20 | −%10 | −%50 |
| 2019 | +%31 | +%22 | +%6 |
| 2020 (Ocak–Eylül) | +%57 | +%36 | +%51 |
| **Karar** | **RİSK_AZALTIR** | **RİSK_AZALTIR** | |

**Okuma:**

- **Keşifteki desen bağımsız bir dönemde tekrarlandı.** Trend bozulunca
  çıkmak:
  - düşüşü yarının altına indirdi;
  - Sharpe'ı iki yarıda da al-tut'un üstüne taşıdı;
  - 2018 ayısında kaybı −%50'den −%20'ye (oynaklık ayarıyla −%10'a)
    indirdi.
- **Giriş zamanı yine rastgeleden iyi değil:** Fark +0.2 puan, [−3.0, +3.2].
  Değeri çıkış kuralı taşıyor; giriş anı değil.
- **Oynaklık ayarı:**
  - Düşüşü −%30'dan −%21'e indirdi ve Sharpe'ı biraz artırdı (0.79 → 0.88).
  - Karşılığında güçlü yükselişlerde daha az kazandırdı (2020: +%36 / +%57).
  - Bu bir risk tercihi.
- **Dürüst sınırlar:**
  - Sharpe farkının güven aralığı sıfırı içeriyor: [−0.64, +1.47] ve
    [−0.60, +1.59]. Tek başına 2 yıl, istatistiksel kesinlik için kısa.
  - İşlem başına net getirinin güven aralığı da sıfırı içeriyor:
    [−1.6, +10.0].
  - Güçlü olan, iki bağımsız dönemde aynı yönde ve aynı büyüklükte sonuç
    çıkması.
  - Son söz, 2024-09 → 2026-08 doğrulamasının.
- Emir yetkisi yok.

**İnceleme (Codex) sonrası eklenen iki koruma:**

- **Payı hesaplanamayan işlem:** Oynaklığı bilinmediği için oynaklık ayarlı
  portföye giremeyen işlem artık "bilinmez" payına sayılıyor.
- **Veri seti doğrulaması:** Her koşudan önce manifest kontrol ediliyor. Şema,
  son tarih, ay penceresi, bütün adayların indirilmiş olması ve dosyaların
  varlığı bakılıyor; eksik ya da yabancı bir veri seti reddediliyor.

Koşu bu korumalardan önce, kayıtlı kodla (`fdb566a67b26e79e`) yapıldı.
Korumalar sonradan aynı veride denetlendi; sonuç değişmiyor:

- payı hesaplanamayan işlem 0;
- bilinmeyen işlem payı 0;
- manifest geçerli;
- kararlar ve rakamlar aynı.

Kod değiştiği için kayıtlı deneme bu kodla yeniden koşulamaz (`prehistory`
reddeder). Bu istenen davranış: test bir kez yapıldı.

**Not (2026-10-01, atlas incelemesi):**

- Evren kuralı, veri setinin ilk gününde işlem gören her coini 90 günlük geçmişi tamamlamış sayıyordu. Keşif verisi 2020-10-01'de başladığı için 2020-11 ve 2020-12 evrenlerine 90 günden genç coinler girdi:
  - 2020-11: DOT, UNI, YFI, YFII;
  - 2020-12: UNI.
- Bu, 46 keşif ayının 2'sinde, toplam yaklaşık 600 üye-aydan 5'inde görülür. Keşif sonucu zaten aday çıkarmamıştı; kayıtlı sonuçlar değiştirilmedi.
- Doğrulama penceresi etkilenmez. Doğrulama verisi 2024-06'da başlıyor ve ilk evren 2024-09'da kuruluyor. Veri setinin başında görülen her coin o tarihte en az 92 günlüktür.
- Atlas, coin yaşını veri setleri arasında taşıyarak bu durumu düzeltir (`monthly_universe(..., history_start=...)`, `docs/ATLAS.md`).
