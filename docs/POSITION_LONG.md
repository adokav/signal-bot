# Uzun vadeli long pozisyon sistemi — kriterler, stop ve kâr alma (tasarım, sürüm 1)

**Tarih:** 2026-10-23. **Durum:** tasarım. Kod yok, kayıt yok, ön-kayıt yok.
Kullanıcı onayından sonra §11'deki sırayla uygulanır.

**Kaynaklar:** kullanıcının getirdiği "Kripto ve Meme Coin Seçim Sistemi v3.2 —
Nihai Rapor" (23 Ekim 2026; aşağıda **v3.2**) ve bu depodaki kanıt:
`docs/TRADE_LOOP_STUDY.md` (D1 çıkış ailesi), `docs/ALTCOIN_WATCH.md` (mevcut
temel tarama), `docs/EVOLUTION_ROADMAP.md` (elenen stratejiler).

## 0. Bu belge ne söyler

Bot, coin **arama motorundan**, seçilmiş coin ve meme coinler için **pozisyon
planı üreten bir gölge kayıt sistemine** dönüşür. Dört soruya cevap verir:

| Soru | Katman | Cevap |
|---|---|---|
| Hangi coin? | Kayıt: v3.2 raporunun kendi listesi (`research/register/`) | Raporun çekirdek sepeti; bot seçmez, yükseltmez, yalnız düşürür |
| Ne zaman, ne kadar? | Rejim (v3.2 K0) | Dilim durumu: BEKLE / DİLİM 1 / DİLİM 2 / AZALT |
| Hangi referansla? | Pozisyon planı | Haftalık karar fiyatı, pay, likidite üst sınırı |
| Ne zaman çıkılır? | Çıkış kuralları | Tez geçersizliği, trend durumu, hedef, yeniden puanlama, süre |

**Düzeltme (2026-10-23, kullanıcı):** ilk taslak "hangi coin" sorusunu bota yeniden
cevaplatıyordu (150 coinlik evrende v3.2 kapıları ve puanı). Yanlıştı. Rapor seçimi
yapmıştır ve kendisi "tarama motoru değil, filtre; adaylar dışarıdan gelir" der.
Botun işi, raporda **adıyla verilen** coin ve meme coinler için long takibidir: rejim
dilimi, vadeli fitil diplerine göre stop/kaldıraç, 2×/4× kısmi satış, tez geçersizliği.
Seçim motoru planı (eski PR D) iptal; yerine kayıt (§3.5) geldi.

Sınırlar, baştan:

- **Emir yok.** `can_authorize_trade = false`. Plan bilgi içindir; karar kullanıcınındır (AGENTS.md §4, §8).
- **Kanıt yok.** v3.2 kendi kendine "yatırım tavsiyesi değil, metodoloji" der; olasılıkları kalibre edilmemiştir, walk-forward testi yoktur. Bu sistemin hiçbir kuralı kanıtlı değildir. Tek istisna: D1 çıkış ailesinin **majörlerde ve günlük ölçekte** RİSK_AZALTIR etiketi (trial `1fcc8f2ef5d92516`). Haftalık ölçekte ve bu evrende o da test edilmedi (§8.1).
- **Dış metin veri değildir** (§31): ekip, ortaklık, katalizör gibi anlatılar bot tarafından okunmaz; yalnızca kaynaklı ve tarihli olarak elle girilen kayıt defterinden (§3.5) alınır.
- **Bilinmeyen güvenli değildir:** doğrulanamayan kapı çekirdeğe girmez; bilinmeyen rejim dilim vermez.

## 1. Ufuk değişince stop ve hedef neden değişir

Kısa vadeli taktik mantık şuydu: stop fiyat gürültüsünün hemen altına (BTC'de
≈ %0,4–%5), hedef stop mesafesinin katı (R), süre saatler–günler. Bu depoda o
mantık dört setup ailesi ve Likit-100 listesiyle test edildi ve kaybetti
(ortalama −0,27R; `docs/TACTICAL_REPLAY_REPORT.md`, `docs/LIQUID_REPLAY_REPORT.md`).

24–36 aylık bir tezde aynı mantık kendi kendini yok eder. Aşağıdaki tablo,
**beklenen getirisi sıfır** olan bir varlıkta (v3.2'nin de kullandığı geometrik
Brown hareketi, yansıma ilkesi; sürüklenme ν = −σ²/2) 2,5 yıl içinde fiyatın
belirli bir seviyeye **en az bir kez** değme olasılığıdır. Yani stop, hiçbir
tez yanlış olmasa bile sırf gürültüyle ne sıklıkla vurulur:

| Yıllık σ | −%15 | −%30 | −%50 | −%70 | −%80 | 2×'e dokunma |
|---|---|---|---|---|---|---|
| 0,6 (BNB, AAVE) | 0,92 | 0,82 | 0,63 | 0,35 | 0,19 | 0,31 |
| 0,9 (UNI, PEPE) | 0,97 | 0,92 | 0,82 | 0,65 | 0,50 | 0,41 |
| 1,2 (PENDLE, WIF) | 0,98 | 0,96 | 0,91 | 0,81 | 0,72 | 0,45 |
| 1,8 (genç meme) | 1,00 | 0,99 | 0,98 | 0,95 | 0,92 | 0,49 |

Tek bir çeyrekte bile σ = 0,9 için −%15'e dokunma 0,77, −%30'a 0,51. v3.2'nin
kendi risk tablosu (rejim ortalamalı pozitif sürüklenme ve σᵢ = 0,6σ ile)
daha iyimserdir: 0,5×'e dokunma 0,39–0,64. Sıfır getiri tabanı dürüst alt
sınırdır; hangisi alınırsa alınsın sonuç aynıdır:

1. **Fiyat stop'u bu ufukta pozisyonu korumaz.** −%30'luk bir stop, tez doğru olsa da on pozisyondan dokuzunda vurulur; −%50 bile çoğunlukta. Stop ne kadar sıkıysa sistem o kadar çok küçük zarar biriktirir ve 2×'i hiç görmez.
2. **Pozisyonu büyüklük korur.** v3.2'nin kendi kuralı: "−%50 ile −%80 arası düşüşte taşınabilir olmayan pozisyon alınmaz." Boyut, pozisyonun −%80'ini bile sermaye için kabul edilebilir kılacak kadar küçük tutulur (§4.2).
3. **Çıkış fiyattan bağımsız tanımlanır:** tez geçersizliği (kapılar), rejim ve yeniden puanlama. Fiyata bağlı tek çıkış, coinin **kendi oynaklığıyla ölçeklenen** trend-durumu çıkışıdır (§5.2): sabit yüzde değil, haftalık kanal.
4. **Kâr alma ana çıkış değildir.** Hedef değişken 2× ama v3.2'nin medyan çarpanları 1,2–1,6×: çoğu pozisyon 2×'i hiç görmez. 2×'te kısmi satış, sermayeyi geri alma kuralıdır; kalan pay yine tez, trend ve yeniden puanlama ile çıkar (§5.3).

## 2. v3.2'den ne alındı, ne değişti

| Konu | v3.2 | Bu tasarım | Neden |
|---|---|---|---|
| Dört katman (kapı, puan, olasılık, rejim) | Var | Kapı, puan ve olasılık raporun çıktısı olarak **kayıttan okunur**; bot yeniden hesaplamaz. Rejim mekanik. | P(≥2×) coin başına elle atanan senaryo medyanlarına dayanır; v3.2 kendisi "kalibre edilmemiş" der. Mekanikleştirilirse uydurma sayı çıktı olur. Yerine §3.4'teki mekanik yol riski gösterilir. |
| Birleşik skor S = 0,6·Puan + 0,4·P | Sıralama ölçütü | Raporun sırası kayıttan; bot sıralamaz (S yalnız doğrulanır) | Aynı neden. Kullanıcı P değerini kayıt defterine girerse kartta "elle, senaryo türevli" etiketiyle görünür, sıraya girmez. |
| Kapı eşikleri (G1–G4, M1–M3) | Var | Raporun kapı sonucu kayıtta; botun günlük kontrolleri (hack, kontrat, hacim) yalnız **düşürür** (§5.1) | Eşikler v3.2'den; veriden öğrenilmedi, bu etiketle gösterilir. |
| Puan ağırlıkları (A20 B20 C15 D15 E10 F10 G5 H5; K1–K6) | Var | Kayıtta; bot puanlamaz (ağırlıklar yalnız doğrulama sınırı) | `docs/ALTCOIN_WATCH.md` eşit ağırlık seçmişti; v3.2 kullanıcının nihai çerçevesi. Ağırlıklar yine kanıtsızdır; ileri kayıt ikisini de ölçebilsin diye eşit ağırlıklı sıra ikincil olarak da kaydedilir. |
| Doğrulanamayan alt metrik 0,2 | Var | Raporda; koşullu coin (BNB, INJ) koşul bayrağı kalkana kadar sinyal almaz | AGENTS.md §2 ile aynı. |
| Arz defteri (24 ay), değer aktarım defteri | Elle hesaplandı | Raporda; bot yeniden hesaplamaz. Tetikleyici bayrakları (§3.5) elle, PR ile | Kilit açılış takvimi ücretsiz veride yok. |
| Rejim kuralı (endeks, BTC.D) | Var | Aynen, mekanik; ilk haftalar BİLİNMİYOR | Kendi haftalık arşivimiz oluşana kadar "düşüyor/yükseliyor" bilinmez → dilim yok. |
| Pozisyon kuralları (hacmin %10'u, dilim, −%80 taşınabilirlik) | Var | Aynen, sayısallaştırıldı (§4) | |
| İzleme tetikleyicileri | Coin bazlı liste | Kurala çevrildi (§5.1) | |
| Stop ve kâr alma | Tanımlı değil | §5 | Kullanıcının asıl sorusu. |
| D1 çıkış ailesi | Yok | Trend-durumu çıkışı olarak haftalık analoğu | Depodaki tek kanıtlı çıkış ailesi; haftalıkta test şart (§8.1). |

## 3. Katmanlar

### 3.1 K0 Rejim — ne zaman, ne kadar

Haftada bir, Pazartesi 00:00 UTC'den sonra hesaplanır ve kendi arşivimize
yazılır (ilk görüldüğü hali; revize edilmez).

| Gösterge | Kaynak | Tanım |
|---|---|---|
| BTC dominansı (BTC.D) | CoinGecko `global` | `market_cap_percentage.btc`, haftalık kapanış değeri |
| Altcoin sezonu vekili (AS) | CoinGecko `market_chart` (90 gün) | Piyasa değerine göre ilk 50 altcoin (stabil, sarılı ve §3.2'deki dışlamalar hariç) içinde 90 günlük getirisi BTC'ninkini geçenlerin payı × 100. Blockchaincenter endeksine yakındır, aynısı değildir. |
| Stablecoin arzı 30 günlük değişim | DefiLlama `stablecoincharts/all` | Bilgi satırı |
| Korku-Açgözlülük | alternative.me | Bilgi satırı |

Kural (v3.2 §9, aynen):

| Durum | Koşul | Anlamı |
|---|---|---|
| **DİLİM 1** | AS > 50 **ve** BTC.D 4 hafta öncesinden düşük | Çekirdek coinlerde ilk dilim alınabilir |
| **DİLİM 2** | DİLİM 1 **ve** BTC.D haftalık kapanış < 55 | İkinci dilim alınabilir |
| **AZALT** | BTC.D > 60 **ve** 4 hafta öncesinden yüksek | Yeni dilim yok; açık pozisyonlar yarıya iner (§5.5) |
| **BEKLE** | Diğer her durum ve her BİLİNMİYOR | Yeni dilim yok; açık pozisyonlar kurallarıyla izlenir |

- "4 hafta öncesi" kendi arşivimizden okunur; arşiv 4 haftadan kısaysa yön BİLİNMİYOR → BEKLE.
- Rejim coin sırasını değiştirmez (v3.2 ilke 6).
- Atlastaki "altcoin girişini BTC yükselişteyken yap" önerisi (`docs/ATLAS.md`) bu sürümde **bilgi satırıdır**: BTC haftalık kapanışı önceki 10 haftalık kapanışın en yükseğinin üstündeyse YÜKSELİŞ, en düşüğünün altındaysa DÜŞÜŞ, arası YATAY. Kapı değildir; ileri kayıt yön ayrımını ölçer.

### 3.2 K1 Kapılar — kim elenir

**Altcoin evreni:** CoinGecko'da piyasa değerine göre ilk 150 altcoin; `docs/ALTCOIN_WATCH.md`'deki dışlamalar (stabil, sarılı, likit (re)staking, köprülenmiş, tokenize altın ve ürünler, sentetik dolar, dolar sabitli fiyat). Meme kategorisindekiler altcoin değil meme modelinde değerlendirilir (§6).

| Kapı | v3.2 eşiği | Mekanik kaynak | Elle (kayıt defteri) | ❔ |
|---|---|---|---|---|
| **G1 Spot likidite** | Günlük spot hacim ≥ 10 M$ | CoinGecko `market_chart`: son 31 tam UTC gününün medyanı (mevcut kural) | — | 20 günden az, en yeni gün 3 günden eski |
| **G2 Arz defteri** | 24 aylık satış ağırlıklı net dolaşım artışı ≤ %5 **ve** 12 aylık kilit açılımı ≤ float'ın %5'i | Üst sınır: basılmış-dolaşmayan pay ≤ %5 ve azami arza ulaşılmışsa **mekanik geçti** (açılacak bir şey yok) | Emisyon, kilit takvimi, satış katsayıları | Kilitli pay > %5 ve defterde kayıt yok |
| **G3 Çalışan ürün + akış** | Son 4 çeyrekte zincir üstü doğrulanabilir gelir **ve** tokene akış mekanizması | DefiLlama `summary/fees/{protokol}`: son 4 çeyreğin her birinde gelir > 0; `dailyHoldersRevenue` > 0 mekanizmanın mekanik kanıtı | Mekanizma türü ve durumu (yakım, geri alım, dağıtım; çalışıyor / durdu, tarihli) | DefiLlama eşleşmesi yok; mekanizma kaydı yok |
| **G4 Güvenlik / yönetişim** | Çözümsüz kritik açık yok; sınırsız basım yok; honeypot yok; doğrulanmış dolandırıcılık yok | DefiLlama `hacks` (180 gün ❌, 730 gün ⚠️); GoPlus (EVM) / RugCheck (Solana) yalnız asıl zincirde (mevcut kurallar) | Çözümsüz açık, doğrulanmış dolandırıcılık | Kontrat okunamadı; asıl zincir desteklenmiyor |

Sonuç kuralı `docs/ALTCOIN_WATCH.md` ile aynı: herhangi bir ❌ → ELENDİ; herhangi bir ❔ → İNCELEME GEREKLİ (sepet: izleme / büyüme-seyrelme havuzu / doğrulanamadı); hepsi ✅ → **ÇEKİRDEK ADAYI**. Yalnız çekirdek adayları puanlanır ve pozisyon planı alır. ATH mesafesi kapı değildir (v3.2 ilke 5).

### 3.3 K2 Puan — kim daha iyi

Her alt metrik bant çıpaları arasında doğrusal enterpolasyonla 0–1; grup puanı = ağırlık × alt metrik ortalaması. Her veri tek grupta sayılır (v3.2 ilke 7).

| Grup | Ağırlık | Bant (v3.2) | Kaynak |
|---|---|---|---|
| A Arz baskısı | 20 | 24 aylık satış ağırlıklı dilüsyon ≤ %1 → 1; %5 → 0,4; ≥ %10 → 0 | Kayıt defteri; yoksa 12 aylık **gerçekleşen** seyrelme (CoinGecko serisi) "gerçekleşen, takvim değil" etiketiyle ve 0,2 tavanıyla |
| B Değer aktarımı | 20 | Yıllık gerçekleşen akış / piyasa değeri: mekanizma yok 0; var ama akış yok 0,2; %1 → 0,5; %2 → 0,7; ≥ %4 → 1 | DefiLlama `dailyHoldersRevenue` (yakım + dağıtım) + kayıt defterindeki hazineye geri alım × 0,5 |
| C Kullanım / gelir trendi | 15 | Düzleştirilmiş gelir (0,5 × son çeyrek × 4 + 0,5 × son 12 ay) yıllık değişimi ≤ −%60 → 0,2; düz 0,6; ≥ +%50 → 1 | DefiLlama günlük gelir, 24 ay; yoksa 0,2 |
| D İleriye dönük değerleme | 15 | P/R ≤ 5 → 1; 15 → 0,7; 40 → 0,4; 100 → 0,2; ≥ 300 → 0,05. ATH referansı: fiyat çarpanı > 12× → −0,2; < 2× ve BTC zirvesinin ≥ %25 altındayken → +0,1 | CoinGecko piyasa değeri, ATH; DefiLlama gelir |
| E Likidite ve türev kırılganlığı | 10 | Spot hacim/pd %0,5 → 0,2; %3 → 0,9. OI/pd %3–12 → 1; ≥ %20 → 0,3. Perp/spot 1–4 → 1; 6 → 0,5; 10 → 0,2 | Spot: CoinGecko. OI ve perp hacmi: **MEXC vadeli, tek borsa vekili** (Coinglass ve DefiLlama türev toplamı ücretli, §9); MEXC'de yoksa alt metrik 0,2 |
| F Güvenlik, yönetişim, refleksif teminat | 10 | Son 24 ay olay; yönetici yetkileri; token kendi protokolünde teminat mı | DefiLlama hacks, GoPlus/RugCheck; teminat: kayıt defteri |
| G Somut katalist | 5 | Tarihli, etkisi ölçülebilir katalist; dikeyinde 1.–2. sıra | Yalnız kayıt defteri; yoksa 0 |
| H Veri güvencesi | 5 | Zincir üstü gelir açık mı, OI/hacim ayrımı var mı, unlock takvimi yayımlanmış mı, kaynak sayısı | Mekanik: eşleşme ve kayıt defteri alanlarının doluluğu |

- **Çekirdek sepet:** çekirdek adayları Puan'a göre sıralanır; ilk N (varsayılan 5) pozisyon planı alır. Eşitlikte piyasa değeri büyük olan önde.
- Eşik uçurumu yok: sıra sürekli puandan gelir (v3.2 K2 gerekçesi).
- Sıralama doğrulanmamıştır ve menüde bu etiketle gösterilir.

### 3.4 K3 Yol riski — mekanik, tahmin değil

Her çekirdek adayı için, **beklenen getiri sıfır** varsayımıyla (ν = −σ²/2) ve coinin son 365 günlük günlük log getirilerinden hesaplanan yıllık σ ile, T = 2,5 yıl ufkunda yansıma ilkesiyle:

- 2×'e dokunma olasılığı;
- 0,5×'e dokunma olasılığı;
- en kötü %5 sonuç ve koşullu kayıp (CVaR₅).

Bunlar "ne kadar katlanır" tahmini **değildir**; "bu oynaklıkta yol ne kadar sarsar" ölçüsüdür. Kartta "yol riski (sıfır getiri varsayımı)" etiketiyle görünür ve §4.2'deki boyut kuralına girer. v3.2'nin senaryo medyanlı P(≥2×) değeri hesaplanmaz.

### 3.5 Kayıt — raporun listesi, veri olarak

`research/register/v3_2_2026-10-23.json` (şema `v32-register/v1`), depoda, yalnız PR ile
değişir. Raporun kendi çıktısıdır: her coin için sepet (çekirdek, koşullu, izleme, büyüme
havuzu, doğrulanamadı, başarısız; meme için spekülatif havuz, likidite sınırı), rapor sırası,
grup puanları ve toplam, senaryo medyanları (ayı/baz/boğa), P(≥2×), S, risk çıktıları (σ,
medyan çarpan, 2× ve 0,5× dokunma olasılıkları, en kötü %5, CVaR₅, likidite üst sınırı),
rapor anındaki anlık değerler (fiyat, piyasa değeri, arz, ATH/ATL, spot hacim, OI/pd,
perp/spot), tetikleyici bağları ve rapor sayfası. Kaynak PDF'in sha256'sı kayıtta; kaydın
kendi sha256'sı her defter olayına yazılır (hangi listeyle karar verildi).

- `acce_unified/register.py` kaydı okur ve doğrular: şema, benzersiz sembol/CoinGecko
  kimliği/perp sembolü, sepet adı, koşul yalnız koşullu sepette, grup puanı ≤ ağırlık,
  toplam = grup toplamı ± 2 (rapor yuvarlar), S = 0,6·Puan/100 + 0,4·P ± 0,01, olasılıklar
  [0, 1], ayı ≤ baz ≤ boğa, sonlu sayılar, tanımlı tetikleyiciler. Tek alan bozuksa hiçbir
  şey yüklenmez (`RegisterError`); yarım kayıt coin düşürür ya da yanlış etiketler.
- **Bot kaydı doldurmaz, yükseltmez, dış metin okumaz.** Sepet raporundur; botun günlük
  kontrolleri (§5.1) yalnız düşürür. Koşullu coin (BNB: kazanç verisi; INJ: emisyon) koşul
  bayrağı PR ile kaldırılana kadar dilim sinyali almaz.
- Raporun tetikleyicileri (`triggers`): BTC.D 55 haftalık kapanış ve "OI/pd > %20 ve
  perp/spot > 6" mekanik (bot ölçer); AAVE geri alımı, SKY spot 10 M, BNB/INJ veri, ETF
  kararı, LINK hazine, HYPE kilit takvimi, PENGU unlock elle (PR ile `status` değişir).
  SKY hacmi de ölçülebilir ama sepet değişikliği yine rapor/PR işidir.
- Kayıt raporun tarihini taşır; 180 günden eskiyse "bayat" (⚠️, yeni dilim yok). Raporun
  çeyreklik yeniden puanlaması yeni bir kayıt sürümü olarak gelir; eski sürüm silinmez.

## 4. Pozisyon planı

### 4.1 Giriş dilimleri

- **Karar zamanı:** haftada bir, Pazartesi 00:00 UTC'den sonra (günlük serinin son noktası yayımlanınca). Her çekirdek coin için o haftanın referans fiyatı CoinGecko'nun o andaki fiyatıdır; **ilk görülen fiyat** kaydedilir.
- **Dilimler:** rejim DİLİM 1 ise hedef payın %40'ı, DİLİM 2 ise kalan %60'ı. Oranlar kullanıcı bütçesidir (`POSITION_SLICE_1_PCT`, varsayılan 40), sistem parametresi değildir (v3.2 §9).
- Bir pozisyonun ilk dilimi, coinin çekirdek sepete girdiği **ilk** karar haftasında değil, **ikinci** ardışık karar haftasında da çekirdekteyse açılır (tek haftalık geçici giriş-çıkışı ayıklar). Bu, v3.2'de olmayan tek giriş zamanlama kuralıdır ve D1 çalışmasının "giriş zamanlaması rastgeleden iyi değil" bulgusuyla tutarlı olarak yalnız gürültü filtresi iddiasındadır.
- Bildirim "şimdi al" değildir: referans fiyat karar anının fiyatıdır; mesaj bunu ve bildirim gecikmesini açıkça yazar (`docs/TREND_LOOP_LIVE.md` "geç bildirim" kuralı aynen).

### 4.2 Büyüklük

Bot sermayeyi bilmez; yüzdeleri ve dolar cinsinden likidite üst sınırını yazar.

| Kural | Varsayılan | Kaynak |
|---|---|---|
| **Kayıp bütçesi:** pozisyonun −%80'inde sermaye kaybı | altcoin %1, meme %0,5 (`POSITION_LOSS_BUDGET_PCT`) | v3.2 "−%80 taşınabilirlik"; kullanıcı bütçesi |
| **Azami pay** = kayıp bütçesi ÷ 0,80 | altcoin %1,25, meme %0,625 | Türetilmiş |
| **Oynaklık düzeltmesi:** azami pay × min(1, 0,9 / σ) | σ > 0,9 olan coinde pay küçülür | D1_20_10_VOL ailesi (RİSK_AZALTIR); 0,9 hedef σ'sı v3.2 çekirdeğinin tipik σ'sı |
| **Likidite üst sınırı:** pozisyon ≤ 30 günlük medyan günlük spot hacmin %10'u | Dolar olarak yazılır | v3.2 §12 |
| **Sepet üst sınırı:** en fazla N pozisyon / sepet | 5 altcoin, 5 meme | v3.2 çıktısı "ilk 5" |
| **Kaldıraç:** izole marj, varsayılan 1×, kod sınırı 2× | 1× | §13.1: 2× ve üstü gürültüyle likide olur |
| **Likidasyon fiyatı ≤ fitil referansı × 0,8** (fitil referansı = son 365 günün en derin fitili, tek günlük en derin fitil ve 730 günlük döngü dibinden en düşüğü) | Her kararda yeniden hesaplanır; referans bilinmiyorsa pozisyon yok | §13.2 |
| **Funding:** `FUND90` > yıllık %20 iken yeni dilim yok; birikmiş funding kartta ve hedef düzeltmesinde | — | §13.4 |
| **Korelasyon notu:** v3.2'nin kendi uyarısı: çekirdek coinler fiyat olarak yüksek korelasyonlu, kaldıraç güdümlüler (perp/spot > 4, OI/pd > %12) daha küçük tutulmalı | Kart uyarısı; E grubundan okunur | v3.2 §10.3 |

### 4.3 Yaşam döngüsü (tek yönlü)

```
ADAY → PLAN → AÇIK(dilim 1) → AÇIK(dilim 2) → KISMİ(2×) → KISMİ(4×) → KAPALI(neden)
```

- Geriye dönüş yok. Kapanan pozisyon yeniden açılmaz; şartlar yeniden sağlanırsa **yeni kayıt** açılır (AGENTS.md §8: bayat/geçersiz setup sessizce yeniden aktive olmaz).
- Kapanış nedenleri: TEZ_GEÇERSİZ, TREND_ÇIKIŞI, YENİDEN_PUANLAMA, SÜRE, REJİM_AZALT (yarım), KISMİ_SATIŞ (parça), LİKİDASYON_KORUMA (yalnız 1× üstü kaldıraçta, §13.3).
- Her olay `/data/position_long_ledger.jsonl` defterine yalnız ekleme ile, fsync ile yazılır (`trend_loop.Ledger` yeniden kullanılır); her olay `trial` ve `can_authorize_trade: false` taşır. Önce defter, sonra durum.

## 5. Çıkış kuralları

### 5.1 Tez geçersizliği — fiyattan bağımsız, önce gelir

Kapılar pozisyon açıkken de kontrol edilir. Bir kapı ❌ olursa pozisyon **bir sonraki karar referansıyla** tamamen kapanır; bildirim o gün gider.

| Tetik | Kontrol sıklığı | Eylem |
|---|---|---|
| Hack: protokolde (ana ağ coininde zincirde) net ≥ 1 M$ yeni olay | Günlük (DefiLlama `hacks`) | TEZ_GEÇERSİZ, tam çıkış |
| Kontrat: asıl zincirde yeni ❌ bayrağı (honeypot, bakiye değiştirme, gizli sahip, kendini yok etme; Solana'da dondurma) | Haftalık | TEZ_GEÇERSİZ |
| Likidite: 30 günlük medyan spot hacim < 10 M$ **iki ardışık haftalık kontrolde** | Haftalık | TEZ_GEÇERSİZ (histerezis: tek hafta ⚠️) |
| Akış: son çeyrek geliri 0 **ya da** kayıt defterinde mekanizma "durdu" | Aylık / kayıt değişince | TEZ_GEÇERSİZ |
| Arz: kayıt defterine 12 aylık kilit açılımı > float'ın %5'i girilirse | Kayıt değişince | TEZ_GEÇERSİZ |
| Kayıt bayatladı (180 gün) | Çeyreklik | Yeni dilim yok; ⚠️; yeniden puanlamada ❔ → §5.4 |
| Meme M2/M3 (§6) | Haftalık | TEZ_GEÇERSİZ (M3 iki ardışık hafta) |

Çıkış, tetiğin doğru olmasını ister, fiyatın düşmüş olmasını değil. Bir coin %300 yükselmişken hack olursa da çıkılır.

**Uygulama (2026-10-23):** hack, kontrat, likidite ve faaliyet satırlarını kapı bekçisi (`acce_unified/altcoin_watch.py` v2, günlük) üretir; `/long` haftalık kararda `gate_verdict` ile okur: hack/kontrat/faaliyet ❌ hemen, likidite ❌ iki ardışık kararda sinyali keser; kontrol yoksa ya da 3 günden eskiyse "kapı kontrolü bilinmiyor". Açık pozisyonun kapatılması pozisyon defteriyle (§8.2) gelir.

### 5.2 Trend-durumu çıkışı — fiyata bağlı tek kural

v3.2'de fiyat tabanlı çıkış tanımlı değildir. Bu depoda tek kanıtlı çıkış ailesi D1'dir: günlük kapanış, önceki 10 günlük kapanışın en düşüğünün altına inince çık; majörlerde iki bağımsız testte düşüşü al-tut'un yarısının altında tuttu, getiri kanıtı yok. Pozisyon ufkunda aynı aile **haftalık ölçekte** uygulanır:

- **Haftalık bar:** Pazartesi 00:00 UTC → sonraki Pazartesi 00:00 UTC; kapanış = Pazar günlük kapanışı (CoinGecko günlük serisi).
- **Çıkış:** haftalık kapanış < önceki **10 haftalık** kapanışın en düşüğü → pozisyonun tamamı bir sonraki karar referansıyla kapanır (TREND_ÇIKIŞI). Kontrol yalnız haftalık kapanışta; gün içi uçlar ve günlük kapanışlar sayılmaz.
- **Yeniden giriş:** coin çekirdekte, rejim DİLİM 1/2 ve haftalık kapanış > önceki 10 haftalık kapanışın en yükseği → yeni kayıt (ilk dilimden başlar, §4.1 ikinci hafta kuralı uygulanmaz; trend teyidi onun yerine geçer).
- **Genişlik:** bu kanal sabit yüzde değildir; coinin kendi oynaklığıyla açılır. σ = 0,9'da 10 haftalık dip tipik olarak zirvenin ≈ %40–50 altındadır, σ = 0,6'da ≈ %35, σ = 1,8'de ≈ %70. §1'deki tabloyla okunursa: sıradan dalgalanmada değil, trend dönünce tetiklenir.
- **Felaket stop'u yok.** D1'in gün içi 3 × ATR(20) stop'unun haftalık analoğu (3 × ATR(20 hafta) ≈ σ = 0,9'da entry'nin %50–60 altı) kanalın altında kalır ve neredeyse hiç bağlayıcı olmaz; ani çöküşleri (exploit) §5.1'in günlük hack kontrolü yakalar. Bir kural daha eklemenin parametre maliyeti faydasını aşar. Haftalık majör testi (§8.1) bu seçimi de sınar: stop'lu ve stop'suz iki varyant.
- **Bedeli:** V dönüşlerde yeniden giriş eski seviyeden olur. Bu, trend takibinin bilinen maliyetidir; D1'de de ödendi ve yine de düşüş yarıya indi.
- **Kanıt etiketi:** "D1 ailesi günlükte RİSK_AZALTIR; haftalık ölçekte ve bu evrende test edilmedi." §8.1 sonucu NO_EFFECT ya da daha kötüyse bu kural ileri kayıt başlamadan tasarımdan çıkar.

### 5.3 Hedefte kısmi satış

- Hedef değişken 2× (v3.2 §2). Pozisyonun ağırlıklı ortalama girişinin **2 katında** payın bir kısmı, **4 katında** bir kısmı daha satılır; kalan "koşucu" yalnız §5.1, §5.2, §5.4, §5.5 ile kapanır.
- Varsayılan oranlar kullanıcı bütçesidir: altcoin 1/3 + 1/3; meme 1/2 + 1/4 (kalın kuyruk ve ölüm oranı daha yüksek). Env: `POSITION_SCALE_OUT_1`, `POSITION_SCALE_OUT_2`.
- Kontrol haftalık kapanışta; referans o haftanın karar fiyatı.
- 2×'te 1/3 satmak, −%80 bütçesiyle sızılmış pozisyonda ilk sermayenin 2/3'ünü geri alır; bu bir "kâr hedefi" değil, kalan payın taşınabilirlik bütçesini yenilemesidir.

### 5.4 Çeyreklik yeniden puanlama ve 36 ay süre sınırı

- Her çeyreğin ilk karar haftasında tam puanlama yenilenir (v3.2 §12 takvimi: haftalık rejim ve türev; aylık arz ve likidite; çeyreklik gelir ve tam puan).
- Pozisyonu açık bir coin, yeniden puanlamada **ilk N'nin dışına** düşerse ya da ❔ olursa, bir sonraki karar referansıyla kapanır (YENİDEN_PUANLAMA). Bu, değerlemeye dayalı kâr almanın kuralıdır: P/R 300'ü geçen coinin D puanı 0,05'e düşer ve sırası kayar; ayrı bir "pahalı" eşiği yoktur.
- İlk dilimden **36 ay** sonra pozisyon kapanır (SÜRE). Aynı hafta coin hâlâ çekirdekteyse yeni kayıt açılabilir (yeniden taahhüt; eski kayıt uzatılmaz).

### 5.5 Rejim azaltması

- Rejim **AZALT** olduğunda (BTC.D > 60 ve yükseliyor) açık her pozisyon bir sonraki karar referansıyla **yarıya** iner (REJİM_AZALT). Aynı rejim dönemi içinde bir kez uygulanır; pay yeniden yalnız DİLİM 1/2 ile artar.
- v3.2 §9 "risk azalt" kuralının sayısallaştırılmasıdır; kanıtı yoktur.

### Özet

| # | Çıkış | Tetik | Sıklık | Eylem | Kanıt |
|---|---|---|---|---|---|
| 1 | Tez geçersizliği | Kapı ❌ | Günlük/haftalık | Tam | v3.2 kapıları; kanıt yok |
| 2 | Trend durumu | Haftalık kapanış < 10 haftalık dip | Haftalık | Tam | D1 ailesi günlükte RİSK_AZALTIR; haftalıkta §8.1 |
| 3 | Hedef | 2× / 4× | Haftalık | Kısmi | Bütçe kuralı |
| 4 | Yeniden puanlama / süre | İlk N dışı, ❔, 36 ay | Çeyreklik | Tam | v3.2 takvimi |
| 5 | Rejim | AZALT | Haftalık | Yarım | v3.2 §9; kanıt yok |

Bütün çıkışlar bir sonraki karar referansıyla "dolar"; gerçek dolum, kayma ve komisyon dahil değildir ve mesaj bunu yazar.

## 6. Meme coin uyarlaması

Meme coinin nakit akışı yoktur; fiyatı dikkat × likidite × dağılımdır (v3.2 §8). Aynı katmanlar, farklı kapılar ve puan.

- **Evren:** CoinGecko meme kategorisi, piyasa değerine göre ilk 100; Binance-Peg ve sarılı kopyalar hariç. CoinMarketCap çapraz kontrolü bu sürümde yok (kimlik için `new_coins` CMC referansı kullanılabilir, §9).
- **Kapılar:**

| Kapı | v3.2 eşiği | Mekanik kaynak | ❔ |
|---|---|---|---|
| M1 Hayatta kalma (Lindy) | Yaş ≥ 18 ay **ve** ≥ %80 düşüşü atlatıp üst kümede kalmış | coins.llama.fi fiyat geçmişi: ilk fiyat tarihi; koşan zirveden ≥ %80 düşüş yaşanmış; bugün ilk 100'de (tanım gereği) | Geçmiş < 18 ay (→ spekülatif havuz, çekirdeğe girmez) |
| M2 Dağılım temizliği | Dolaşım ≥ %90; 12 aylık kilit açılımı ≤ float'ın %5'i; tek içeriden cüzdan ≤ %10 | CoinGecko arz; GoPlus/RugCheck ilk 10 cüzdan (sağlayıcının borsa ve kontrat etiketleri hariç tutulur) | Kilit: kayıt defteri; cüzdan verisi okunamadı |
| M3 Spot likidite | Günlük spot hacim ≥ 10 M$ **ve** hacim/piyasa değeri ≥ %2 | CoinGecko 30 günlük medyan | 20 günden az gün |

- **Puan (100):** K1 spot likidite ve piyasa kalitesi 20 (hacim/pd %0,5 → 0,1; %2 → 0,6; %5 → 0,9; %10 → 1; spot < 10 M → en fazla 0,3; hacim sürekliliği 7/30 gün); K2 dağılım ve içeriden risk 20; K3 dikkat sürekliliği 20 (30/90 günlük hacim trendi; sosyal pay yok, hacim vekil); K4 türev kırılganlığı 15 (OI/pd %3–12 → 1; %20 → 0,5; %30 → 0,3; %60 → 0,1; perp/spot ≤ 4 → 0,9; 6 → 0,6; 10 → 0,3; MEXC tek borsa vekili); K5 erişim, kültür, katalist ve nakit akışı 15 (yalnız kayıt defteri: ETF/düzenlenmiş ürün, büyük CEX, aracı kurum listeleri, varsa ücret → yakım); K6 kontrat ve veri güvencesi 10.
- **Çok durumlu hayatta kalma** (p_z, p_ö) yargısaldır; hesaplanmaz. Kartta M1 yaşı ve en derin düşüş gösterilir.
- **Pozisyon ve çıkış:** aynı kurallar; kayıp bütçesi yarım (§4.2), kısmi satış daha ağır (§5.3), M3 iki ardışık hafta ❌ → TEZ_GEÇERSİZ, K3 dikkat çöküşü (30 günlük hacim < 90 günlük hacmin yarısı) ⚠️ ve puana girer, tek başına çıkış değildir.
- **Dürüst sınır:** v3.2'nin kendi sözü: meme getirisi neredeyse tamamen beta ve dikkat döngüsüdür; sektör dönmezse hiçbir seçim katlanmaz. Rejim katmanı bu yüzden meme sepetinde de bağlayıcıdır.

## 7. Komutlar ve bildirimler

| Komut | İçerik |
|---|---|
| `/long` | Rejim satırı; altcoin çekirdek ilk N (puan, kapı durumu, plan durumu); meme çekirdek ilk N; açık gölge pozisyonlar (giriş referansı, dilim, 10 haftalık çıkış seviyesi, 2×/4× seviyeleri, süre) |
| `/long <sembol>` | Kart: kapılar, puan grupları, yol riski, pay ve likidite üst sınırı, kayıt defteri alanları ve tarihi, "elle bakılacaklar" |
| `/puan` | İki kategorinin rapor puanları (Puan, P(≥2×), S) ve botun o coindeki durumu (sinyal ya da eksik ilk koşul); bot puanlamaz |
| `/scan` ve "🔄 Şimdi Tara" | Yeni coin taraması + long **ara taraması** (karar değil: seviyeler ve koşullar yenilenir, yeni sinyal ilan edilmez); bitince `/puan` tabloları gönderilir |
| `/rejim` | K0 göstergeleri, 4 haftalık yön, dilim durumu, BTC trend bilgisi |
| `/check <adres>` | Mevcut kontrat taraması (kalır) |
| `/d1` | D1 gölge kaydı (kalırsa, §10) |
| `/status`, `/scan`, `/panel` | Sağlık, yeniden tarama, panel |

Bildirimler (kullanıcı kararı 2026-10-23: "sinyal geldiği anda uyarı"; uygulandı):

- **🟢 LONG SİNYALİ** — haftalık kararda bir coin ilk kez DİLİM 1/2 alınca, karar anında tek mesaj: karar fiyatı (perp kapanış), fitil referansı, likidasyon 1× ve L_max, marj stopu, 2×/4×, pay ve likidite üst sınırı, funding, OI/pd, kapılar, rejim satırı; "referans fiyat karar anının fiyatıdır, alım talimatı değildir" uyarısı. Aynı coin için haftada bir; süren sinyal yeniden duyurulmaz.
- **🔴 SİNYAL DÜŞTÜ** — bir karar sinyali artık sağlanmayınca (sonraki kararda ya da ara taramada) ve günlük kapı kontrolünde hack/kontrat/faaliyet ❌ görülünce **aynı gün**; neden yazılır. Haftada bir.
- Ara tarama (düğme) yeni sinyal üretmez: haftalık karar ilkesi korunur; yalnız düşen sinyal bildirilir.
- `LONG_WATCH_ALERTS_ENABLED=0` mesajları deftere yazar, göndermez ("sent: false").
- Planlanan, henüz yok: haftalık özet, aylık özet, `/rapor`.
- Her mesajda: referans fiyatın karar anı fiyatı olduğu, gecikme varsa süresi, "KANIT YOK — ileri kayıt sürüyor" etiketi ve "Gölge kayıt: emir yok, karar senin." (kaldırılan `long_alerts` modülünün altbilgi geleneği; `trend_loop` aynı altbilgiyi kullanır).
- Teslim güvencesi `trend_loop` ile aynı: mesaj olayıyla deftere yazılır, `DELIVERED` kaydı gelene kadar yeniden denenir.

## 8. Kanıt planı

### 8.1 Önce: haftalık D1 analoğu, majörlerde (ön-kayıt taslağı)

§5.2 kuralı canlı tasarıma girmeden test edilir; parametreler **bu belgeyle** sabitlenmiştir, hiçbir koşu yapılmadı.

- **Aile:** `trade_loop_majors_weekly`. **Evren:** mevcut aylık majör evreni (`majors_signals`: BTC, ETH, hacimce ilk 10, en büyük meme). **Veri:** Binance vision spot; haftalık barlar günlük barlardan kurulur.
- **Döngüler:** `W_10_10` (haftalık kapanış > önceki 10 haftalık kapanışın en yükseği → gir; < en düşüğü → çık; stop yok) ve `W_10_10_ATR` (aynı + 3 × ATR(20 hafta) gün içi stop). Pay 1/N; ayrıca oynaklık ayarlı varyant bilgi olarak.
- **Pencereler:** 2018-09 → 2020-09, 2020-11 → 2024-08, 2024-09 → 2026-08; her biri ayrı raporlanır, hiçbiri parametre seçmek için kullanılmaz (parametre zaten sabit).
- **Ölçü ve karar kuralları:** trial `1fcc8f2ef5d92516` ile aynı (işlem başına net sonuç, portföy düşüşü ve Sharpe, aynı evrenin al-tut'u ve rastgele giriş kontrolü ile karşılaştırma; PASS / ZAMANLAMA_YOK / RİSK_AZALTIR / NO_EFFECT / INCOMPLETE_DATA; maliyet %0,20, stres %0,35).
- **Güç:** haftalık ölçekte işlem sayısı günlüğün onda biri; INCOMPLETE_DATA çıkma ihtimali yüksek. Bu durumda §5.2 "test edilemedi" etiketiyle kalır mı yoksa çıkar mı — **kullanıcı kararı** (§12).
- **Kullanımı:** RİSK_AZALTIR → §5.2 kalır. NO_EFFECT ya da daha kötü → §5.2 tasarımdan çıkar; fiyata bağlı çıkış kalmaz, pozisyon yalnız tez, hedef, yeniden puanlama ve süre ile kapanır.

### 8.2 Sonra: ileri kayıt ön-kaydı (taslak)

- **Aile:** `position_long_forward`, şema `position-long/v1`. Hiçbir kayıt oluşmadan, ilk karar haftasından önce `research/trials/registry.jsonl`'e girer.
- **Birim:** aylık kohort (o ay açılan pozisyonlar) ve aylık seçim kohortu (o ayın çekirdek adayı listesi; `docs/ALTCOIN_WATCH.md` kuralıyla).
- **Ölçüm:** giriş ve çıkış referansı perp fiyatıdır (MEXC vadeli günlük mumlar); sonuçlar gerçekleşen funding'den nettir (nominal × settle edilen oranlar); komisyon ve kayma ayrıca sabit bir maliyetle (%0,1 tek yön) düşülür.
- **Birincil iddia (12 ay):** kurallarla yönetilen pozisyonların portföy düşüşü, aynı coinlerin aynı referanslarla eşit ağırlıklı al-tut'unun %60'ının altında kalır (D1 ölçütü). Karar: RİSK_AZALTIR / NO_EFFECT / DAHA_KÖTÜ / INCOMPLETE_DATA; %95 blok bootstrap (kohort üzerinde, blok 3, 2000 çekiliş, tohum 0).
- **İkincil:** çekirdek ilk N'nin BTC'ye ve evrenin eşit ağırlıklı sepetine göre 182/365/730 günlük getirisi; v3.2 ağırlıklı sıra ile eşit ağırlıklı sıranın karşılaştırması; rejim BEKLE'de açılamayan planların (sayaç) sonraki getirisi (rejim katmanının bedeli); BTC trend bilgisine göre ayrım.
- **Zaman:** 12 aylık birincil sonuç en erken ilk karar haftasından 15 ay sonra; 24/36 aylık ikinciller sonra. Düşük güç: bir piyasa döngüsü.
- **Değişiklik kuralı:** evren, kapı, puan bandı, dilim, çıkış ya da sonuç kuralı değişirse yeni şema ve yeni trial. Test bunu zorlar (mevcut `altcoin_watch` testi gibi).
- **Canlı etkisi:** hiçbir sonuç modülü otomatik değiştirmez; ayrı PR ve kullanıcı onayı.

### 8.3 Mevcut deneme kayıtlarının akıbeti

| Trial | Modül | Öneri |
|---|---|---|
| `b6e0fec86714efc2` altcoin_watch_forward | `altcoin_watch` | Kapı ve puan v3.2'ye geçince şema değişir → **SUPERSEDED** notuyla kapanır; sonuç bakılmadı (en fazla 1–2 kohort). v1 defteri silinmez. |
| `1fcc8f2ef5d92516` D1 gölge kaydı | `trend_loop` | **Sürer** (kullanıcı kararı §12). Kayıt 2027-10'dan önce değerlendirilmez. |
| `b57b9eb0a6073085` yeni coin güvenlik taraması | `new_coins` `/new` | `/new` kaldırılırsa **ABANDONED** (ürün yönü değişti; sonuç bakılmadı). `/check` kalır. |

## 9. Veri kaynakları ve sınırlar

Doğrulandı (2026-10-23, ücretsiz, anahtarsız): CoinGecko `global` (BTC.D), meme kategorisi `coins/markets` (30g/200g/1y değişimlerle), DefiLlama `overview/open-interest` (protokol bazında OI), `stablecoincharts/all`, alternative.me korku-açgözlülük. **Ücretli:** DefiLlama türev hacmi (`overview/derivatives`, 402), kilit açılış takvimi (402), Coinglass toplam OI.

| İhtiyaç | Kaynak | Sınır |
|---|---|---|
| Evren, fiyat, arz, ATH, hacim | CoinGecko (Demo anahtarı Render'da) | 365 günlük geçmiş; ATH günü arzı bilinmez |
| 18+ aylık fiyat geçmişi (M1, σ) | coins.llama.fi `chart/coingecko:<id>` | Günlük; doğrulanacak |
| Ücret, gelir, token sahibi geliri (çeyreklik) | DefiLlama `summary/fees/{protokol}` | Sonradan düzeltilebilir; kayıt ilk görüleni saklar |
| OI, perp hacmi (coin bazında) | MEXC vadeli (`contract.mexc.com`) | **Tek borsa vekili**; etiketle gösterilir; yoksa 0,2 |
| Hack | DefiLlama `hacks` | Köprü hack'leri her zaman coine bağlanmaz |
| Kontrat yetkileri, cüzdan dağılımı | GoPlus (EVM), RugCheck (Solana) | Yalnız asıl zincir; TON/Tron/Sui ❔ |
| Kilit takvimi, emisyon, mekanizma durumu, katalist | Kayıt defteri (elle, PR ile) | Bayatlar; 180 gün |
| Derinlik | Yok | H grubunda "ölçülmedi" |

Çağrı bütçesi (haftalık): CoinGecko ≈ 150 + 100 (meme) + 50 (AS) + çekirdek adayı başına 1 ≈ 350; aylık ≈ 1.500 — Demo sınırı 10.000/ay.

## 10. Temizlik planı

"Bunun haricindeki modüller": v3.2 yönüne hizmet etmeyen, kısa vadeli taktik mantığa ait ya da üretimde çalışmayan kod. Raporlar, kanıt dosyaları ve deneme kaydı **silinmez**; her kapanmış çalışmanın raporuna harness'ın son commit'i yazılır.

| Modül | Satır | Karar | Neden |
|---|---:|---|---|
| `acce_unified/tactical_long.py`, `tactical_long_data.py`, `tactical_long_engine.py`, `forward_ledger.py` | ~1.330 | **Kaldır** | BTC/ETH taktik radar: 5 dakikalık yapı, replay NEGATIVE, REJECT ve susturulmuş. Kullanıcı bu mantıktan uzaklaşmak istiyor. |
| `acce_unified/radar_gate.py`, `long_alerts.py` | ~1.480 | **Kaldır**; `esc`, `table`, `_num`, `_clock` küçük bir `text.py`'ye taşınır | Taktik ve Likit-100 kapısı/stop'u. Yazı yardımcıları `altcoin_watch`, `new_coins`, `trend_loop` tarafından kullanılıyor. |
| Likit-100 zinciri: `engine.py`, `liquid_long.py`, `providers.py`, `listings.py`, `fundamentals.py`, `listing_fundamentals.py`, `social.py`, `observation_archive.py`, `observation_runtime.py`, `config.py`, `models.py` | ~4.300 | **Kaldır** | 4 ve 24 saatlik liste; replay NEGATIVE, REJECT. MEXC yeni listeleme radarı zaten kapalı. Sosyal ve temel zenginleştirme yalnız bu zincire hizmet ediyordu. |
| `acce_unified/cex.py` | 61 | Kalır (`is_stable_or_synthetic`, `is_leveraged_token`); `opportunity_proxy` gider | `altcoin_watch`, `trend_loop`, `trading/data` kullanıyor |
| `run_service.py`, `macro_*.py` (11 dosya), `scripts/build_macro_history.py`, `scripts/summarize_macro_evidence.py` | ~2.860 | **Kaldır** | Üretim `bot.py` ile başlıyor; makro boru hattı çalışmıyor. Atlas makro katmanı aday üretmedi (`docs/ATLAS.md`); v3.2 rejimi piyasa verisiyle çalışır, FRED ile değil. |
| `trading/backtest/{tactical_replay, liquid_replay, signal_quality, reversal_replay, weekly_momentum}.py`, `trading/strategies/{tactical,liquid,reversal}_dossier.py`; workflow'lar `tactical_replay`, `liquid_replay`, `signal_quality`, `signal_quality_confirm`, `reversal_replay` | ~4.160 | **Kaldır** | Kaldırılan canlı modülleri import eden, kapanmış çalışmaların harness'ları. Raporlar (`docs/*_REPORT.md`, `SIGNAL_QUALITY.md`, `WEEKLY_MOMENTUM.md`) ve `research/evidence/*.json` kalır. |
| Bunların testleri (`test_tactical_*`, `test_forward_ledger`, `test_radar_gate`, `test_long_alerts`, `test_liquid_*`, `test_observation_*`, `test_fundamental_radar`, `test_social_radar`, `test_unified_{engine,listings,providers}`, `test_listing_report_details`, `test_signal_quality*`, `test_reversal_replay`, `test_weekly_momentum`, `test_macro_*`) | ~6.600 | **Kaldır** | |
| `bot.py` | 1.260 → ~650 | Küçülür | `/tactical`, `/longs`, `/radar`, tarayıcı döngüleri, radar kaydı, ileri kayıt gider |
| `acce_unified/new_coins.py` `/new` tarayıcısı (CMC yeni coinler, 72 saatlik pencere, defter, trial) | ~600 | **Bu dalgada kaldı; karar bekliyor** | v3.2 meme evreni 18 aydan genç coini zaten eler (M1); kaldırılırsa trial `b57b9eb0a6073085` ABANDONED olur. `SecurityProviders`, kimlik referansı ve `/check` her durumda **kalır** (G4, K6, M2). |
| `acce_unified/trend_loop.py`, `/d1` | 827 | **Kaldı (karar bekliyor; öneri: kalsın)** | Depodaki tek kanıtlı çıkış ailesi; canlı gölge kaydı 2026-10-02'de kullanıcı onayıyla başladı; `Ledger` sınıfı yeni deftere de hizmet eder. |
| `acce_unified/altcoin_watch.py` | 1.206 → ~750 | **Daraltıldı (v2)** | 150 coinlik tarama, sıralama, ölçütler ve kohort kaydı kalktı; dört kapı yalnız kayıt coinlerinde günlük çalışır ve `/long`'a tez geçersizliği girdisi verir (§5.1). Trial `b6e0fec86714efc2` SUPERSEDED (`docs/ALTCOIN_WATCH.md`). |
| `trading/risk/*`, `trading/research/*`, `trading/data/*`, `trading/backtest/{trade_loop, majors_signals, walk_forward, benchmark, cost_model, carry_replay, carry_audit, listing_replay, atlas}` ve dossier'ları | ~9.000 | Kalır, ikinci dalga kararı | Botta çalışmaz; §8.1 testi `trade_loop` ve `majors_signals` altyapısını kullanır. Kapanmış çalışmalar (carry, listing, TSMOM, atlas) yeni sistem çalışır hale gelince ayrı kararla elenir. |

Toplam: ≈ 44.600 satırdan ≈ 20.000 satır gider. `render.yaml`'dan `UNIFIED_*`, `LIQUID_*`, `TACTICAL_*` ayarları; `README.md`, `docs/DEAD_CODE_AUDIT.md`, `docs/EVOLUTION_ROADMAP.md` güncellenir. Silinen her modülün son commit'i ilgili rapora yazılır; git geçmişi tam kalır.

**Durum (2026-10-23):** uygulandı (PR A, `docs/DEAD_CODE_AUDIT.md` üçüncü dalga). `/new` ve `/d1` bu dalgada kaldı; kararları §12'de.

## 11. Uygulama sırası

| PR | İş | Çıktı |
|---|---|---|
| A ✅ | Temizlik (§10) | Küçük bot: `/long` henüz yok; `/altcoin`, `/new`, `/check`, `/d1`, `/status` çalışır. Trial kayıtları değişmedi. |
| B | Haftalık D1 analoğu majör testi (§8.1) | Ön-kayıt → workflow → tek koşu → rapor. §5.2'nin kaderi belli olur. |
| C ✅ | **Kayıt** (raporun listesi, `acce_unified/register.py`) + `acce_unified/long_watch.py`: MEXC vadeli veri katmanı (fitil dipleri, funding, OI, sürdürme marjı), CoinGecko (piyasa, toplu OI, BTC.D, ALT90), haftalık kanal, rejim K0, `/long`, `/long <sembol>`, karar defteri | Liste coinleri kartta: sepet, fitil referansı, likidasyon fiyatı, L_max, funding, OI/pd, kanal, eksik koşullar; haftalık BTC.D arşivi birikmeye başlar (yön 4 hafta sonra). |
| D ✅ | `/altcoin` daraltma: tarama kalktı, kapı bekçisi yalnız kayıt coinlerinde, günlük; `gates_of` → `long_watch.gate_verdict` (§5.1 histerezisiyle) | `b6e0fec86714efc2` SUPERSEDED. ~~Seçim motoru v2~~ iptal (§0 düzeltmesi). |
| E | Pozisyon planı, çıkışlar, defter, bildirimler; ileri kayıt ön-kaydı (§8.2) | İlk karar haftası; gölge kayıt başlar. |

Her PR Codex incelemesinden geçer; kanıt dosyaları ve parmak izleri mevcut
kurallarla korunur (değişen kod = yeni deneme).

## 12. Kullanıcıdan beklenen kararlar

**Verilen kararlar (2026-10-23):** temizlik uygulandı (PR #163); `/new` ve `/d1` kalır; pozisyonlar vadeli; en kötü senaryo = likidasyon fitil dipleri (§13); atılım kohortu protokolü yazıldı (`docs/BREAKOUT_COHORT.md`); **liste raporun kendisidir, bot seçmez** (§0 düzeltmesi; kayıt §3.5).

**Açık kararlar:**

1. ~~Temizlik kapsamı~~ — verildi.
2. **Çıkış modeli (§5):** beş kural; fiyata bağlı tek kuralın 10 haftalık kanal olması ve ayrı felaket stop'u olmaması. §8.1 INCOMPLETE_DATA çıkarsa kanal "test edilemedi" etiketiyle kalsın mı, çıksın mı.
3. **Bütçe parametreleri (§4, §5.3):** kayıp bütçesi %1 / %0,5; dilim 40/60; kısmi satış 1/3@2× + 1/3@4× (meme 1/2 + 1/4); sepet başına 5 pozisyon. Bunlar sistem parametresi değil, senin risk bütçendir.
4. **Olasılık katmanı:** P(≥2×) ve S sıralamada yok (§2). Onay.
5. **Kayıt defteri (§3.5):** kilit takvimi, mekanizma ve katalist verisi elle, kaynaklı ve PR ile girilir; girilmeyen coin çekirdeğe giremez. Alternatif: ücretli unlock verisi (Tokenomist/CryptoRank) — ayrı karar.
6. **Rejim kuralı yorumu (§3.1):** v3.2 "Endeks 50 üstü **ve** BTC.D düşüyorsa ilk dilim" der; "bugün iki koşuldan biri sağlanıyor → ilk dilim" cümlesini "dilim 1 koşulu sağlandı, dilim 2 sağlanmadı" diye okudum. Doğru mu?

## 13. Vadeli pozisyon ekleri (kullanıcı kararı 2026-10-23)

Kullanıcının cevapları: pozisyonlar **vadeli (USDT-M perp)**; "en kötü senaryo"
= likidasyon kaskadındaki **fitil dipleri**; `/new` ve `/d1` kalır; atılım
kohortu yazılır (`docs/BREAKOUT_COHORT.md`). Vadeli pozisyon, §1'deki yol
matematiğine iki şey ekler: likidasyon ve funding. İkisi de tezden bağımsız,
fiyattan ve zamandan gelen maliyetlerdir.

### 13.1 Kaldıraç: gürültü likidasyonu

İzole marjda likidasyon mesafesi ≈ 1/kaldıraç − sürdürme marjı (burada %5).
Beklenen getirisi sıfır bir yolda, **yalnız kapanışlarla** (fitiller hariç)
likidasyon seviyesine dokunma olasılığı:

| σ | 1× (−%95) | 1,5× (−%62) | 2× (−%45) | 3× (−%28) | 5× (−%15) |
|---|---|---|---|---|---|
| 0,6 · 2,5 yıl | 0,01 | 0,48 | 0,68 | 0,83 | 0,92 |
| 0,9 · 2,5 yıl | 0,13 | 0,73 | 0,85 | 0,92 | 0,97 |
| 1,2 · 2,5 yıl | 0,38 | 0,86 | 0,92 | 0,96 | 0,98 |
| 1,8 · 2,5 yıl | 0,78 | 0,96 | 0,98 | 0,99 | 1,00 |
| 0,9 · 1 yıl | 0,00 | 0,44 | 0,66 | 0,82 | 0,92 |
| 0,9 · 1 çeyrek | 0,00 | 0,05 | 0,24 | 0,54 | 0,77 |

Fitiller bu tabloyu kötüleştirir: likidasyon kaskadında perp fiyatı spotun
çok altına iner ve likidasyon motoru fitili görür, kapanışı değil. Sonuç:
24–36 aylık bir tezde **2× ve üstü kaldıraç, tez doğru olsa bile çoğu
pozisyonu likide eder.** Kurallar:

- **Varsayılan kaldıraç 1×, izole marj** (teminat = nominal). Üst sınır
  `POSITION_MAX_LEVERAGE = 2` (kod düzeyinde sabit; daha yükseği
  reddedilir). 1×'in üstü yalnız kullanıcının açık ayarıyla ve kartta
  yukarıdaki olasılık satırıyla.
- Çapraz marj yok: bir coinin fitili sepetin tamamını likide edemez.

### 13.2 Fitil kuralı: likidasyon fiyatı, en kötü fitilin altında

"En kötü senaryoda fiyat ne olur" sorusunun vadeli cevabı, son fitil
dipleridir. Her coin için ay başında (ve her haftalık kararda):

- `WICK365` = son 365 günün en düşük gün içi dibi / bugünkü fiyat − 1
  (perp mumlarından; MEXC vadeli, tek borsa);
- `FLASH365` = son 365 günde tek günde en derin fitil: min(düşük / önceki
  kapanış) − 1;
- `CYCLE_LOW` = son 730 günün en düşük günlük kapanışı / bugünkü fiyat − 1.
- **Fitil referansı** = giriş × (1 + min(`WICK365`, `FLASH365`, `CYCLE_LOW`)).
  Üçü de tanımsızsa (geçmiş kısa) referans BİLİNMİYOR → pozisyon yok.

**Kural:** likidasyon fiyatı, fitil referansının en az **%20 altında**
olmalı. Likidasyon fiyatı / giriş ≈ 1 − 1/L + sürdürme marjı olduğundan
(§13.1'deki yaklaşım), `q = 0,8 × (1 + min(WICK365, FLASH365, CYCLE_LOW))`
ile şart `1 − 1/L + marj ≤ q`, yani:

```text
L_max = 1 / (1 + sürdürme marjı − 0,8 × (1 + min(WICK365, FLASH365, CYCLE_LOW)))
```

ve `POSITION_MAX_LEVERAGE` ile kesilir. Örnek: en derin fitil −%55 ise
q = 0,8 × 0,45 = 0,36 → likidasyon girişin %36'sından aşağıda olmalı →
L_max = 1 / (1,05 − 0,36) ≈ 1,45; 1× varsayılanıyla likidasyon −%95'te
(girişin %5'i), kuralın çok altında. Kod, kaldıraç sınırını bu formülle
hesaplar ve kartta likidasyon fiyatını fitil referansıyla birlikte yazar. Fitil referansı,
`docs/BREAKOUT_COHORT.md` §6.4'teki `SIGMA90` → `WICK_MIN_90` /
`FLASH_MAX_24` tablosuyla sınanır: tarihsel dağılımda bir yıllık en derin
fitilin ötesine giden pay raporlanır ve %20 tamponu bu paya göre yeniden
değerlendirilir (sonuçtan önce: %20 sabit).

### 13.3 Stop emirleri ve fitiller

- §5'teki çıkışlar **kapanışla** karar verilir ve bir sonraki karar
  referansıyla uygulanır. Teknik seviyelere **bekleyen stop emri konmaz**:
  fitil, bekleyen stopu kaskadın dibinde doldurur ve pozisyon tez bozulmadan
  en kötü fiyattan kapanır.
- Bekleyen tek emir, varsa, **marj koruma stopu**dur: fitil referansının
  altında, likidasyon fiyatının üstünde. 1× pozisyonda gerekmez; 1×'in
  üstünde zorunludur. Bu stop tezin bir parçası değildir; likidasyon
  ücretinden (ve sıfıra kadar gitme riskinden) kaçınmak içindir.
- Fitiller ayrıca **sinyal**dir: zorunlu satış sonrası toparlanma,
  `docs/EDGE_RESEARCH.md` 2. aday hipotez; atılım kohortunda `WICK90` ve
  `FLASH90` göstergeleri bunu ölçer.

### 13.4 Funding: tezin karşısındaki maliyet

Perp long, her 8 saatte funding öder (pozitifse). Alt coinlerde boğada
yıllık %8–15, çılgınlık dönemlerinde çok daha fazla; 2025–26'da negatif
(`docs/CARRY_REPLAY_REPORT.md`). 24–36 ayda bu, 2× hedefinin önemli bir
kısmıdır.

- **Kart:** son 90 günün yıllıklandırılmış ortalama funding'i (`FUND90`,
  MEXC vadeli funding geçmişi) ve pozisyon açıkken **birikmiş ödenen
  funding** (nominal × gerçekleşen oranlar).
- **Yeni dilim yok:** `FUND90` > yıllık %20 iken o coine dilim açılmaz
  (kalabalık long; v3.2 E/K4 ile aynı yönde). Açık pozisyon kalır; funding
  kartta birikir.
- **Hedef düzeltmesi:** 2× / 4× kısmi satış seviyeleri, birikmiş funding ile
  düzeltilmiş giriş üzerinden hesaplanır (`giriş × (1 + birikmiş funding
  / nominal)`), yani funding ödeyen pozisyon hedefe gerçekten 2× ile varır.
- **İleri kayıt:** sonuçlar gerçekleşen funding'den net; giriş/çıkış
  referansı perp fiyatıdır (MEXC vadeli günlük mumlar), spot değil.

### 13.5 Veri (MEXC vadeli; 2026-10-23'te doğrulandı)

`contract.mexc.com/api/v1/contract/` anahtarsız: `kline/<SEMBOL>?interval=Day1`
tek istekte 800+ günlük mum (gün içi düşük dahil), `funding_rate/history`
sayfalı (UNI için 1.620 ödeme), `ticker` (holdVol = OI, amount24 = 24 saatlik
USDT hacmi, anlık funding), `detail` (sürdürme marjı oranı, kontrat büyüklüğü;
PEPE_USDT ×10 M, 1000BONK_USDT ×10 000, FLOKI_USDT ×100 000, SHIB_USDT ×1 000).
Hepsi tek borsa; kartta "MEXC" etiketi. Binance geçmişi (data.binance.vision)
yalnız araştırmada (atılım kohortu) ve `POSITION_VENUE=binance` seçilirse
günlük dosyalardan (bir gün gecikme; funding yalnız aylık). Ayrıntı §14.1.

### 13.6 Bu eklerin §4–§5'e etkisi

- §4.2 tabloya üç satır eklenir: kaldıraç (1×, en fazla 2×), likidasyon
  fiyatı ve fitil referansı, `FUND90`.
- §5'e ayrı bir çıkış **eklenmez**: funding ve likidasyon, çıkış değil
  boyut ve maliyet kuralıdır. Marj koruma stopu dolarsa kayıt LİKİDASYON_KORUMA
  nedeniyle kapanır (yeni neden; §4.3 listesine eklenir).
- §8.2 ileri kayıt ön-kaydı funding'i ve perp fiyatını ölçüm tanımına alır.

## 14. Uygulama tasarımı: `/long` (2026-10-23, §0 düzeltmesinden sonra)

Girdi kayıttır (§3.5). Bot listedeki her coin için aşağıdaki seriyi toplar,
haftalık kararda durumu yazar ve karar olaylarını deftere ekler. Hiçbir
sağlayıcı hatası sıfır, nötr ya da güvenli sayılmaz: eksik seri BİLİNMİYOR
olur ve o coinin sinyali düşer (AGENTS.md §2).

### 14.1 Seriler ve kaynaklar (hepsi anahtarsız; 2026-10-23'te doğrulandı)

| Seri | Kaynak | Not |
|---|---|---|
| Perp günlük mum, 730 gün → `WICK365`, `FLASH365`, `CYCLE_LOW`, haftalık kapanışlar | MEXC `contract/kline` Day1 (iki istek) | Varsayılan borsa; `POSITION_VENUE=binance` ile data.binance.vision günlük dosyaları (bir gün gecikme). Sembol haritası kayıtta. |
| Funding, 90 gün (≈ 270 ödeme) → `FUND90` (yıllık) | MEXC `funding_rate/history` | Binance'ta yalnız aylık dosya (bir ay gecikme) |
| OI, 24 saatlik perp hacmi, sürdürme marjı | MEXC `ticker`, `contract/detail` | Tek borsa; L_max için sürdürme marjı: max(borsanın oranı, `POSITION_MAINTENANCE_MARGIN` = %5) — muhafazakâr olan |
| Toplu OI ve perp hacim (rapor E/K4 ölçüsü; `LEVERAGE_BUILDUP` tetiği) | CoinGecko `/derivatives`, perpetual sözleşmeler, **sabit borsa listesi** (`binance_futures`, `bybit`, `okex_swap`, `bitget_futures`, `gate_futures`, `hyperliquid`, `mexc_futures`, `kucoin_futures`, `htx_futures`) | Raporun kaynağı DefiLlama token sayfası (ücretli API); eşdeğerlik iddia edilmez, kart kaynağı ve borsa sayısını yazar. Doğrulama (2026-10-23): tüm borsalar toplamı PEPE için 339 M$ (rapor 22 Ekim: OI/pd %9,4) — liste daraltılmadan eşik anlamsız. |
| Piyasa değeri, spot hacim, fiyat | CoinGecko `/coins/markets` (kaydın gecko_id'leri, tek istek) | spot hacim = CoinGecko `total_volume` (türev hariç) |
| BTC.D, toplam piyasa değeri | CoinGecko `/global` | Geçmişi ücretsiz değil → haftalık arşiv botta birikir; "düşüyor / yükseliyor" 4 haftalık arşivden, ondan önce BİLİNMİYOR |
| Altcoin sezonu endeksi `ALT90` | İlk 50 altcoin (stabil, sarılı, stake türevleri hariç) içinde 90 günlük getirisi BTC'yi geçenlerin payı; CoinGecko `market_chart` (365 gün) | Blockchaincenter tanımının kendi hesabımız; `/coins/markets` 90 günlük değişim vermiyor |
| Korku-açgözlülük | alternative.me | Yalnız gösterge; kuralda yok |
| VIX (son kapanış) | FRED `VIXCLS` anahtarsız CSV | Yalnız bilgi (kullanıcı isteği 2026-10-23); rejim satırında; alınamazsa "?" ve not, karara ve sağlığa etkisi yok |
| Perp primi: anlık (son / endeks − 1) ve 90 günlük ortalama (günlük kapanış / endeks kapanışı − 1) | MEXC `ticker` (lastPrice, indexPrice) ve `kline/index_price` Day1 | Yalnız bilgi; "vadeli > spot" sürekli hâliyle funding olarak ödenir ve `FUND90` kapı olarak kalır. Kohorttaki `PREM90` farklı bir ölçüdür: Binance Premium Index (etki alış/satış fiyatlarının endekse primi), betimleyici. |
| Kapı durumu (arz, faaliyet, güvenlik, likidite) | `altcoin_watch` v2 anlık görüntüsü (`gates_of`), günlük | ❌ → sinyal yok (§5.1); kontrol yok ya da > 3 gün → BİLİNMİYOR → sinyal yok |

### 14.2 Haftalık karar

Pazartesi 00:00 UTC'den sonraki ilk tick'te (Pazar günlük mumu kapanmış):
her coin için seriler çekilir, §13.2 fitil referansı, likidasyon fiyatı ve
`L_max`, `FUND90`, OI/pd ve perp/spot, haftalık kanal durumu (§14.3) ve rejim
dilimi (§3.1) hesaplanır; kart yenilenir; karar olayı deftere yazılır (kayıt
sha256, trial, `can_authorize_trade: false`). Hafta içi `/long` son kararı
gösterir ve yaşını yazar; fiyat satırı anlıktır ve "karar fiyatı değil"
etiketi taşır.

### 14.3 Coin durum makinesi — fiyata bağlı tek kural

Haftalık kapanış serisi günlük mumlardan kurulur (Pazartesi 00:00 → Pazar
kapanışı). Kanal, D1 ailesinin haftalık analoğudur (§5.2):

- **YUKARI:** haftalık kapanış > önceki 10 haftanın en yüksek kapanışı
  (kırılım); durum, kapanış < önceki 10 haftanın en düşük kapanışı olana kadar
  YUKARI kalır.
- **AŞAĞI:** tersi. Başlangıç durumu 104 haftalık geçmiş yürütülerek bulunur;
  20 haftadan az veri → BİLİNMİYOR.
- **DİLİM sinyali** = sepet çekirdek **ve** koşul yok **ve** rejim DİLİM 1/2
  **ve** kanal YUKARI **ve** fitil referansı biliniyor **ve** `FUND90` ≤ %20
  **ve** `LEVERAGE_BUILDUP` (OI/pd > %20 ve perp/spot > 6) tetiklenmemiş.
  Sağlanmayan her koşul kartta adıyla yazılır; "BEKLE" bir sinyal değildir.
- Kanıt etiketi: KANIT YOK. §8.1 haftalık majör testi ve atılım kohortu
  (`docs/BREAKOUT_COHORT.md`) bu filtreyi değiştirebilir; parametreler
  (10 hafta, %20 funding) deneme kaydına girer.

### 14.4 Kart (`/long <sembol>`)

Sepet ve rapor sırası, koşul ve tetikleyiciler (durumuyla); fiyat (anlık,
etiketli) ve haftalık karar fiyatı; fitil referansı üçlüsü ve en düşüğü;
1× likidasyon fiyatı, `L_max`, marj koruma stop seviyesi (yalnız 1× üstü);
`FUND90` ve birikmiş funding (pozisyon varsa); anlık ve 90 günlük perp primi
(bilgi); OI/pd ve perp/spot (iki kaynak, etiketli); kanal durumu ve 10 haftalık seviyeler; 2× / 4× seviyeleri
(funding düzeltmeli); raporun risk satırı (σ, medyan çarpan, 2× ve 0,5×
dokunma, en kötü %5, likidite üst sınırı); "elle bakılacaklar". Altbilgi:
"Gölge kayıt: emir yok, karar senin. KANIT YOK — ileri kayıt sürüyor."

### 14.5 Soğuk başlangıç ve kapalı hata

| Eksik | Sonuç |
|---|---|
| Kayıt yüklenemiyor | `/long` hata; hiçbir coin gösterilmez (kısmi liste yok) |
| Perp mum < 365 gün | fitil referansı BİLİNMİYOR → sinyal yok; kart gösterir |
| Perp mum < 20 hafta | kanal BİLİNMİYOR → sinyal yok |
| Funding < 60 ödeme | `FUND90` BİLİNMİYOR → sinyal yok |
| OI / piyasa değeri eksik | tetik değerlendirilemez → sinyal yok (bilinmeyen güvenli değil) |
| Toplu OI'de bir borsanın satırı bozuk | o coinin toplamı BİLİNMİYOR (eksik toplam küçük gösterir) → sinyal yok |
| Borsanın sürdürme marjı alınamadı | L_max ve likidasyon BİLİNMİYOR (kural max(borsa oranı, %5) ister) → sinyal yok; fitil değerleri görünür |
| Kayıt 180 günden eski | ⚠️ "kayıt bayat", hiçbir coine yeni dilim; yeni rapor sürümü PR ile |
| BTC.D arşivi < 4 hafta | rejim yönü BİLİNMİYOR → dilim yok |
| CoinGecko hatası | ilgili satırlar BİLİNMİYOR; önceki karar yaşıyla gösterilir |
| Kapı kontrolü yok ya da 3 günden eski | "kapı kontrolü bilinmiyor" → sinyal yok |
| Herhangi bir seri başarısız | anlık görüntü "eksik" sayılır: `/` sağlık kontrolü tazelikle birlikte eksiksizliği ister |

### 14.6 Durum ve ayarlar

Uygulandı (aynı PR): kayıt ve doğrulayıcı, `acce_unified/long_watch.py`
(MEXC ve CoinGecko istemcileri, seriler, fitil/likidasyon/kanal/funding
hesapları, rejim, karar defteri `long_watch_ledger.jsonl`), `/long` ve
`/long <sembol>`, panel düğmesi, durum satırı ve sağlık kontrolü. Testler:
oluşan mumun reddi, imkânsız OHLC, eksik seri → BİLİNMİYOR, kısa/delikli
geçmiş, L_max örnekleri (−%55 → 1,45; sığ fitilde 2× tavanı; −%95'te 1× bile
yetmez), kanal durum makinesi, sinyal koşullarının her birinin tek başına
düşürmesi, sağlayıcı hatasında URL sızmaması, haftada bir karar kaydı,
piyasa verisi hatasında boş anlık görüntü yerine hata.

Ortam değişkenleri (`render.yaml`): `LONG_WATCH_ENABLED`, `LONG_WATCH_LEDGER_FILE`,
`LONG_WATCH_REGISTER` (varsayılan depodaki kayıt), `POSITION_MAINTENANCE_MARGIN`
(0,05; borsanın oranı yüksekse o), `POSITION_FUNDING_MAX` (0,20),
`POSITION_LOSS_BUDGET_ALTCOIN` (0,01), `POSITION_LOSS_BUDGET_MEME` (0,005).
Kaldıraç tavanı 2× kod sabitidir. Pozisyon açma/kapama, kısmi satış ve
birikmiş funding takibi (§4.3, §5.3) ileri kayıt ön-kaydıyla (§8.2) gelir;
şimdilik kart yalnız seviyeleri gösterir. Sonra: `/altcoin` daraltma.
