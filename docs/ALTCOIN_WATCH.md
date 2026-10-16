# Altcoin izleme: uzun vadeli temel tarama

Kod: `acce_unified/altcoin_watch.py`. Bot: `/altcoin`, `/altcoin <sembol>` ve paneldeki "🔭 Altcoin İzleme".

**Kullanıcı kararı (2026-10-16):** Kullanıcı iki karar aracının altcoin seçim modelini getirdi ve sentezini istedi. Bu modül o sentezin uygulamasıdır.
- **Ufuk:** 24–36 ay. Kullanıcının 1–7 günlük işlem döngüsünün (D1) yerine geçmez; ayrı bir araştırma aracıdır.
- **Emir yetkisi yoktur** (`can_authorize_trade = false`). Liste alım önerisi değildir. Bildirim gönderilmez; menü yalnızca açılınca okunur.

## Sentezden gelen kararlar

İki model de arz baskısını, değer aktarımını, kullanımı ve değerlemeyi elle seçilmiş ağırlıklarla puanlıyordu. Bu modül, ücretsiz ve tekrarlanabilir veriyle ölçülebileni tutar, ölçülemeyeni bırakır.

| Konu | Karar | Neden |
|---|---|---|
| 100 puanlık ağırlıklar | Kullanılmıyor | Ağırlıklar veriden öğrenilmedi. Veri yokken eşit ağırlık, optimize edilmiş ağırlık kadar iyi çalışır. Bizim Likit-100 testimizde de uzman ağırlıklı puan sepetin gerisinde kaldı (NEGATIVE). |
| Arz | Stok ölçüsü değil akış ölçüsü | Dolaşım/maksimum oranı, kalan arzın ne zaman açılacağını söylemez. |
| Stake telafisi | Kullanılmıyor | Stake edilen token likit staking tokenına dönüşüp işlem görebilir. |
| Kategori liderliği, kurumsal ortaklık, ekip, katalizör | Puanlanmıyor | Ancak dış metinden okunabilir. Dış metin güvenilmez veridir (§31) ve anlatı ağırlık vermek için kanıt değildir (AGENTS.md §6). Kartta "elle bakılacaklar" olarak listelenir. |
| 2x–7x ATH bandı | Puan değil, bilgi | Eşik kanıtsız; ATH'ye yakın coin hedefe mekanik olarak kolay ulaşır. |
| "ATH'ye dönüş" hedefi | Ana ölçü değil | İleriye dönük kayıt BTC'ye göre fazla getiriyi ölçer. |
| Yakımın çift sayılması | Önlendi | Yakım hem arzı azaltır hem token sahibi geliridir. Net getiri ölçüsünde yalnızca bir kez sayılır. |

## Veri kaynakları

| Kaynak | Ne için | Anahtar |
|---|---|---|
| CoinGecko `coins/markets` | Evren: piyasa değerine göre sıra, fiyat, arz, FDV, ATH | İsteğe bağlı (aşağıda) |
| CoinGecko `coins/markets?category=` | Evrenden çıkarılacak kategoriler | İsteğe bağlı |
| CoinGecko `coins/list?include_platform=true` | Coinin kontrat adresi ve zinciri | İsteğe bağlı |
| CoinGecko `market_chart` (365 gün, günlük) | 30 günlük medyan hacim, 12 aylık seyrelme, ATH günündeki piyasa değeri | İsteğe bağlı |
| CoinGecko `simple/price` | BTC fiyatı ve 6/12/24 ay sonraki fiyatlar | İsteğe bağlı |
| DefiLlama `lite/protocols2`, `v2/chains` | DefiLlama kayıtlarını CoinGecko kimliğine bağlama (protokol, üst protokol, zincir) | yok |
| DefiLlama `overview/fees` (`dailyFees`, `dailyRevenue`, `dailyHoldersRevenue`) | Ücret, protokol geliri, token sahibine giden gelir | yok |
| DefiLlama `hacks` | Hack geçmişi | yok |
| GoPlus `token_security` | EVM tokenlerinde kontrat yetkileri | yok |
| RugCheck `tokens/{mint}/report` | Solana tokenlerinde basım ve dondurma yetkisi | yok |

**CoinGecko anahtarı (önerilir):**
- Anahtarsız genel API çok sık "429" (hız sınırı) verir. Tarama yine çalışır ama yavaş olur; bazı coinler ❔ kalabilir.
- coingecko.com/en/api adresinden ücretsiz Demo anahtarı alınır: ayda 10.000 çağrı, dakikada 30.
- Anahtar yalnızca Render panelinde `COINGECKO_DEMO_API_KEY` olarak girilir. Sohbete, koda ya da loga yazılmaz.
- Anahtar istekte başlık (header) olarak gider. Hata metinleri yalnızca hata türünü taşır; anahtar ve adres hiçbir zaman görünmez.
- Haftalık bir tarama yaklaşık 50 CoinGecko çağrısı harcar; ayda 200–250 çağrı eder.

**Ücretsiz veride olmayanlar (dürüst liste):**
- **Kilit açılış takvimleri.** DefiLlama'nın unlock verisi ücretli (HTTP 402). Tokenomist, CryptoRank ve Messari de ücretli. Bu yüzden arz kapısı yalnızca üst sınırla çalışır (aşağıda).
- **ATH günündeki dolaşım arzı.** CoinGecko'nun ücretsiz geçmişi son 365 günle sınırlı. ATH'si daha eski coinlerde "ATH günündeki piyasa değeri" bilinmez ve kart bunu açıkça yazar.

## Evren

- CoinGecko'da piyasa değerine göre ilk **150 altcoin**. BTC evrende değildir; karşılaştırma ölçütüdür.
- **Çıkarılanlar:**
  - CoinGecko kategorileri: stabil coinler, sarılı tokenler, likit staking ve restaking tokenleri, köprülenmiş tokenler, tokenize altın, tokenize ürünler, sentetik dolarlar.
  - Botun stabil sembol kuralına uyan semboller.
  - Fiyatı 1 dolara %3'ten yakın ve 24 saatlik aralığı %0,5'ten dar olanlar: kategorilerin kaçırdığı dolar sabitli tokenler.
- **RWA kategorisi çıkarılmaz.** Çünkü LINK, XLM, ONDO gibi protokol tokenlerini de kapsıyor.

## Kapılar

Her kapı ✅ geçti, ⚠️ dikkat, ❌ kırmızı bayrak ya da ❔ bilinmiyor olur. Eşikler iki modelden alınan kaba kurallardır, **veriden öğrenilmedi**.

| Kapı | ✅ | ❌ | ❔ (inceleme gerekli) |
|---|---|---|---|
| **Arz** | Basılmış ama dolaşımda olmayan arz, dolaşımın en fazla %5'i | — | Bu pay %5'ten büyük: ne zaman açılacağı ücretsiz veride yok. Arz verisi eksik. |
| **Faaliyet** | DefiLlama'da son 30 günde ücret geliri var | Eşleşen kayıtta 30 günlük ücret sıfır | DefiLlama'da eşleşen kayıt yok |
| **Güvenlik** | Ana ağ coini (gaz tokeni ya da kontratsız coin); kontratta tehlikeli yetki yok | Son 180 günde net ≥ 1 milyon $ hack. EVM: honeypot, bakiye değiştirme, gizli sahip, kendini yok etme, sahipliği geri alma. Solana: dondurma yetkisi açık. | Kontrat verisi alınamadı ya da alan eksik. Proxy kontrat: yetkiler uygulama kontratında, GoPlus okuyamıyor (AAVE, RENDER). Tokenin asıl zinciri desteklenmiyor (TON, Tron, Sui…). |
| **Likidite** | Son 30 tam günün medyan hacmi ≥ 10 milyon $ | Medyan hacim < 10 milyon $ | Hacim serisi eksik |

**Uyarılar (⚠️, kapıyı kapatmaz):**
- son 2 yılda net ≥ 1 milyon $ hack;
- EVM'de basım fonksiyonu, proxy (kod değiştirilebilir), transfer durdurma, kara liste;
- Solana'da basım yetkisi açık.

Büyük yönetişim tokenlerinde basım fonksiyonu çoğu zaman yönetişim denetimindedir, ama GoPlus bunu ayırt edemez. Bu yüzden ❌ değil ⚠️.

**Kontrat ve hack eşleme kuralları:**
- **Yalnızca asıl zincirdeki kontrat kontrol edilir.** CoinGecko asıl platformu listede ilk sırada verir. Başka zincirdeki köprü kopyası farklı bir kontrattır ve farklı yetkileri vardır. Örneğin BONK'un asıl kontratı Solana'dadır; Ethereum kopyası kontrol edilmez.
  - Asıl zinciri desteklenmeyen bir token, köprü kopyasından kontrol edilmez; ❔ olur.
- **Ana ağ coini yalnızca zincirin kendisine yapılan saldırıyla elenir.** DefiLlama bazı uygulamaları zincirin üst kaydı altında tutar. Örnek: NEAR Intents, NEAR Protocol altında. Bir uygulamanın hack'i zincirin coinini tehlikeye atmaz.
  - Tokenler ise kendi protokollerinin bütün hack'leriyle değerlendirilir.

**Arz kapısı neden ❌ değil ❔ veriyor:** Kilitli arzın büyüklüğü biliniyor, ama açılış zamanı bilinmiyor. Zamanı bilinmeyen bir şeye "elendi" demek de, "geçti" demek de uydurma olur. Kartta kilitli pay yüzde olarak gösterilir.

**Kapı sırası ve ağır kontroller:**
- Arz, faaliyet ve hack kontrolü bütün evrene uygulanır.
- Kontrat yetkileri, hacim ve seyrelme yalnızca arz ve faaliyetten geçen, son 180 günde hack'i olmayan coinlere bakılır. Bu en fazla 80 coindir; sıraya giremeyen ❔ olur.
- Bu coinler için CoinGecko'ya coin başına bir çağrı gider.
- Kontrolü yapılmayan kapılar kartta "değerlendirilmedi" olarak görünür ve coin "İNCELEME GEREKLİ" kalır.

**Sonuç:**
1. Herhangi bir ❌ → **ELENDİ**.
2. Yoksa, herhangi bir ❔ ya da yapılmamış kapı → **İNCELEME GEREKLİ**. Eksik veri asla geçti sayılmaz.
3. Yoksa → **KAPILARI GEÇTİ**. Yalnızca bu coinler sıralanır.

## Ölçüler ve sıralama (yalnızca kapıları geçenler)

| Ölçü | Tanım | Yön |
|---|---|---|
| **Net getiri** | DefiLlama'nın 1 yıllık token sahibi geliri / piyasa değeri − max(0, 12 aylık seyrelme) | Yüksek iyi |
| **Değerleme** | FDV / DefiLlama'nın 1 yıllık protokol geliri. FDV yoksa piyasa değeri kullanılır. | Düşük iyi |
| **Kullanım ivmesi** | Son 30 günün ücreti / son 1 yılın aylık ortalama ücreti − 1 | Yüksek iyi |

- **Seyrelme:** 365 günlük günlük seride ima edilen arz (piyasa değeri / fiyat), son 7 noktanın medyanının ilk 7 noktanın medyanına oranı − 1.
  - Seri bir yıldan kısaysa ya da tek bir günde arz %15'ten fazla sıçradıysa seyrelme bilinmez. Sıçrama, CoinGecko'nun dolaşım arzı tanımını değiştirmesinden de gelebilir; gerçek açılışla ayırt edilemez.
  - Emisyon, kilit açılışı ve yakım bu tek sayıda birlikte görünür.
- **Yakım bir kez sayılır:** Arz azalırsa seyrelme sıfır kabul edilir. Yakımın değeri zaten token sahibi gelirinde var.
- **Sıralama:**
  - Her ölçü kapıları geçenler arasında yüzdelik sıraya çevrilir; eşitlikler ortalanır.
  - **Bilinmeyen ölçü en son sıraya konur.** İyimser varsayım yapılmaz; bu, iki modelin de istediği "veri yoksa yüksek puan yok" kuralıdır.
  - Bileşik puan, üç yüzdeliğin eşit ağırlıklı ortalamasıdır.
  - Eşitlikte önce piyasa değeri büyük olan, sonra CoinGecko kimliği önce gelir.
  - Üçte birlik gruplar: üst floor(n/3), alt floor(n/3), gerisi orta.
- **Sıralama doğrulanmamıştır.** Menüde bu etiketle gösterilir.

## Bilgi satırları (puana girmez)

Kartta `/altcoin <sembol>` gösterilir:
- **ATH'ye dönüş için gereken artış.**
- **ATH fiyatının piyasa değeri:**
  - bugünkü dolaşımla;
  - 24 ay sonraki tahmini arzla (son 12 ayın seyrelme hızıyla);
  - tam seyreltilmiş arzla.
- **ATH günündeki piyasa değeri** ve bugünkü arzla bu değere denk gelen fiyat. Yalnızca ATH son 365 gün içindeyse; değilse "bilinmiyor".
- **Arz bilgisi:** dolaşım dışı pay, maksimum arza kadar henüz basılmamış pay, seyrelme notu.
- **Elle bakılacaklar:** ekip, ortaklıklar, yol haritası, yönetişim. Bot bunları puanlamaz.

## Zamanlama ve sağlık

- Tarama haftada bir yapılır (`ALTCOIN_WATCH_REFRESH_SECONDS`, varsayılan 7 gün). Bot 6 saatte bir kontrol eder.
- Bot yeniden başlarsa son tarama durum dosyasından okunur. Tarama bir haftadan eski değilse yeniden yapılmaz.
- **Bir ana kaynak başarısız olursa tarama başarısız sayılır:** CoinGecko evreni ya da DefiLlama'nın eşleme, ücret, gelir veya hack verisi. Eksik verili bir liste yayınlanmaz; son tarama "güncel değil" uyarısıyla kalır.
- Tek bir coinin çağrısı başarısız olursa yalnızca o coin ❔ olur.
- `/status`: "Altcoin izleme" satırı. CoinGecko anahtarı yoksa bunu söyler.
- Sağlık ucu: `altcoin_watch_fresh`. Tarama 15 günden eskiyse ya da son denemede hata varsa `ok` false olur.

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| `ALTCOIN_WATCH_ENABLED` | `1` | Modülü çalıştırır |
| `ALTCOIN_WATCH_REFRESH_SECONDS` | `604800` | Tarama aralığı (en az 1 gün) |
| `ALTCOIN_WATCH_LEDGER_FILE` | `/data/altcoin_watch_ledger.jsonl` | İleriye dönük kayıt defteri |
| `COINGECKO_DEMO_API_KEY` | boş | Ücretsiz CoinGecko anahtarı (önerilir) |

## İleriye dönük kayıt ve değerlendirme

**Kayıt:** `/data/altcoin_watch_ledger.jsonl`. Yalnızca ekleme yapılır, fsync ile yazılır, her kimlik bir kez yazılır.
- **Aylık kohort:** Her UTC ayının ilk tam taramasında evrendeki her coin bir `WATCH` kaydı olarak yazılır: durum, kapılar, ölçüler, sıra, üçte birlik grup, fiyat.
  - Ay, bir `MONTH` kaydıyla kapanır; kayıt o ayın BTC fiyatını taşır.
  - `MONTH` kaydı olmayan yarım kalmış bir kohort hiç ölçülmez.
- **`OUTCOME`:** 182, 365 ve 730 gün sonra coinin ve BTC'nin fiyatı. Her vade, vadesinden sonraki 2 gün içinde ölçülür.
  - O pencerede CoinGecko'ya sorulduysa ama fiyat yoksa `NO_QUOTE` yazılır. Sorulduğunu bir `QUOTE_GAP` kaydı kanıtlar.
  - Bot o pencerede kapalıysa vade `MISSED` (eksik) olur.
  - Kaçırılan bir vade, sonraki bir fiyatla asla doldurulmaz.
- Her kayıt `trial` ve `can_authorize_trade: false` taşır.

**Ön-kayıt:** Trial `research/trials/registry.jsonl` içinde; kimliği `acce_unified/altcoin_watch.py` dosyasındaki `TRIAL` sabitidir. Hiçbir kayıt ve hiçbir sonuç oluşmadan, 2026-10-16'da yapıldı.
- **İddia:** Kapıları geçenler içinde üst üçte bir, alt üçte birden daha iyi gider. Ölçü: 365 günlük getiri eksi BTC'nin 365 günlük getirisi.
- **Zaman:** 2028-11-01'den önce değerlendirilmez. O tarihte 365 günlük sonucu belli olan her kohort girer; beklenen aralık 2026-10 → 2027-10.
- **Yöntem:**
  - Her kohort için (üst üçte birin ortalama fazla getirisi) − (alt üçte birinki) hesaplanır.
  - İstatistik, kohortların ortalamasıdır.
  - %95 güven aralığı: kohortlar üzerinde 3'lük bloklarla hareketli blok bootstrap, 2000 çekiliş, tohum 0.
  - `NO_QUOTE` −%100 sayılır.
- **Kararlar:**
  - `SEPARATES`: Ortalama pozitif ve aralık sıfırı içermiyor.
  - `NO_SEPARATION`: Aralık sıfırı içeriyor.
  - `REVERSED`: Ortalama negatif ve aralık sıfırı içermiyor.
  - `INCOMPLETE_DATA`: Her üçte birlik grupta en az 3 coin olan kohort sayısı 9'dan az, ya da 365 günlük sonuçların %20'sinden fazlası `MISSED`.
- **İkincil:**
  - kapıları geçenlerin (eşit ağırlık) tüm evrene göre durumu, BTC'ye karşı;
  - elenenlerin tüm evrene göre durumu;
  - aynısı 182 ve 730 günde.
- **Güç düşük:** 12 aylık pencereleri çakışan 13 aylık kohort, aşağı yukarı tek bir piyasa döngüsüdür. Hiçbir sonuç modülü otomatik olarak değiştirmez; değişiklik ayrı bir PR ve kullanıcı onayı ister.
- **Değişiklik kuralı:** Evren, kapı, ölçü, sıralama ya da sonuç kuralı değişirse yeni şema ve yeni trial gerekir. Bir test bunu zorlar.

## Canlı deneme (2026-10-16, anahtarsız CoinGecko)

- **Süre:** Tarama 20 dakika sürdü; anahtarla birkaç dakikaya iner.
- **Sonuç:** 150 coinden 21'i kapıları geçti, 122'si inceleme gerekli, 7'si elendi.
- **İncelemeye kalma nedenleri:**
  - 90 coin: dolaşım dışı arz %5'ten fazla;
  - 26 coin: DefiLlama eşleşmesi yok;
  - 6 coin: kontrat doğrulanamadı.
- **Elenme nedenleri:** son 180 günde hack (THORChain, Raydium) ve 10 milyon doların altında hacim (KCS, XTZ, KAIA).
- **Bu denemeden çıkan üç düzeltme:** asıl zincir kuralı, ana ağ coinlerinde zincir hack'i kuralı, proxy açıklaması. Üçü de veri eşlemesiyle ilgilidir, hiçbir coinin sonucuna bakılmadı. Ön-kayıt bu düzeltmelerden sonra yapıldı.

## Bilinen sınırlar

- **Arz kapısı yalnızca üst sınırla çalışır.** Evrenin büyük kısmı ❔ olur, çünkü kilitli arzın açılış takvimi ücretsiz değil. Bu bir eksiklik değil, dürüstlüktür: zamanı bilinmeyen açılışı "yok" saymıyoruz.
- **DefiLlama eşlemesi her coini kapsamaz.** Ücreti DefiLlama'da izlenmeyen coinler (çoğu memecoin, bazı L1'ler) ❔ olur. Bu, "kullanım yok" demek değildir.
- **DefiLlama tanımları:**
  - Likit staking'de "ücret" bütün staking ödülleridir. "Gelir" protokolün payıdır; değerleme bu yüzden gelirle yapılır.
  - DefiLlama verileri sonradan düzeltilebilir. Kayıt, o gün görüleni saklar.
- **Token sahibi geliri her protokolde raporlanmaz.** Raporlanmayan coin, net getiri ölçüsünde en sona konur. Sıfır gelirle "raporlanmamış" ayırt edilemiyor; ihtiyatlı taraf seçildi.
- **Yakım ile emisyon birbirini götürürse** seyrelme sıfır görünür ve brüt emisyon cezalandırılmaz. Brüt emisyon ücretsiz veride yok.
- **CoinGecko'nun toplam hacmi bütün borsaları kapsar;** sahte hacmi tam ayıklayamaz.
- **Hack eşlemesi yalnızca DefiLlama'daki protokol bağlantısıyla yapılır.** Köprü hack'leri ilgili coine her zaman bağlanmaz.
- **"Asıl platform ilk sırada" kuralı CoinGecko'nun gözlenen davranışıdır,** belgelenmiş bir garanti değildir. Denetlenen örneklerin hepsinde doğruydu: BONK, JUP, PYTH ve WIF'te Solana; AAVE, PEPE ve UNI'de Ethereum.
- **BTC trend durumu (atlasın "ne zaman" katmanı) bu sürümde yok.** Bu modül "ne" sorusunu yanıtlar.
- **Geçmişe dönük test yapılmadı ve dürüstçe yapılamaz.** Sağlayıcıların geçmiş verisi o gün bilinen veri değildir: takvimler revize edilir, gelir serileri sonradan doldurulur. İsabeti yalnızca ileriye dönük kayıt ölçer.
