# Yeni coinler: CoinMarketCap + güvenlik taraması

Kod: `acce_unified/new_coins.py`. Bot:
- `/new` ve paneldeki "🆕 Yeni Coinler (CMC)";
- `/check <adres>` ve paneldeki "🔎 Kontrat Kontrol": istediğin bir kontratı aynı taramadan geçirir (aşağıda).

**Kullanıcı kararı (2026-10-05):**
- "Yeni Listeler" menüsü artık MEXC yerine CoinMarketCap'in yeni eklediği coinleri gösteriyor.
- Her coin bir dolandırıcılık taramasından geçiyor.
- MEXC yeni listeleme radarı kapatıldı (`UNIFIED_LISTING_RADAR_ENABLED=0`). Kodu şimdilik duruyor.

**Sürüm 2 (2026-10-05, aynı gün):**
- İlk sürüm (v1) yayına girdiği gün sağlayıcıların gerçek cevaplarıyla denendi. Bu ortamın ağ izni o gün açıldı.
- Gerçek cevaplar belgelerden farklı çıktı:
  - GoPlus vergi alanlarını boş bırakıyor ve `cannot_sell_all` alanını hiç göndermiyor.
  - Birçok havuzun LP tokeni yok: Uniswap V3/V4 ve Solana'daki yoğunlaşmış likidite havuzları.
  - LP tokenleri bazen token kontratının kendisinde duruyor.
- Bu yüzden v1 PEPE, BRETT ve BONK gibi yerleşik coinleri ya ❔ ya da yanlışlıkla ❌ sayıyordu.
- v2 bu farklara göre düzeltildi:
  - Satılabilirlik gerçek bir alım-satım simülasyonundan (honeypot.is) ölçülüyor.
  - Havuz kilidinde kırmızı bayrak, LP'nin çoğunun sıradan cüzdanlarda olması.
  - Solana'da RugCheck'in hesap etiketleri kullanılıyor.
- Ayarlar yalnızca cevapların biçimine göre yapıldı, hiçbir coinin sonucuna bakılmadı. v1 kaydı değerlendirilmeyecek.

**Ekler (2026-10-06, kullanıcı onayı):**
- `/check <adres>`: Memecoinler DEX'te CMC'den çok önce işlem görür. Kullanıcı gördüğü bir tokenin adresini gönderir, bot aynı taramayı yapar.
- Kimlik ve köken uyarıları: yerleşik bir coinin adını ya da sembolünü taşıyan yeni coin (taklit olabilir) ve CMC'de web sitesi ya da sosyal hesabı olmayan coin.
- Bu ekler **yalnızca ⚠️ verir**; kararı (AĞIR RİSK / VERİ EKSİK / BAYRAK YOK) değiştirmez. Bu yüzden ön-kayıtlı ileriye dönük test (trial `b57b9eb0a6073085`) aynen sürer. Bir test bunu zorlar.

## Bu tarama nedir, ne değildir

- **Nedir:** Bilinen dolandırıcılık kalıplarını arar:
  - satılamayan token (honeypot, yüksek vergi, dondurma yetkisi);
  - geliştirici yetkileri (yeni token basma, bakiye değiştirme, gizli sahip);
  - birkaç cüzdanda toplanmış arz;
  - çekilebilir likidite (rug pull);
  - sığ ya da şişirilmiş piyasa.
- **Ne değildir:**
  - "Güvenilir" ya da "yükselecek" demek değildir.
  - 1000x potansiyeli ölçmez; bunu ölçen kanıtlanmış bir yöntem yok.
  - %80 gibi bir isabet oranı yoktur; isabet henüz ölçülmedi. Aşağıdaki ileriye dönük kayıt bunu ölçmek için tutuluyor.
- **Taban oran:** Binance'te 400 yeni listelemeyi kapsayan test (`docs/LISTING_REPLAY_REPORT.md`) şunu gösterdi:
  - Tipik yeni listeleme 30 ile 90 günde değer kaybetti; medyan −%16 ile −%48 arasındaydı.
  - Yalnızca %15–28'i BTC'yi geçti.
  - Menünün altında bu satır gösterilir.
- **Emir yetkisi yoktur** (`can_authorize_trade = false`). Bildirim gönderilmez; menü yalnızca açılınca okunur.

## Kaynaklar

| Kaynak | Ne için | Anahtar |
|---|---|---|
| CoinMarketCap `listings/latest` (`sort=date_added`) | En yeni 200 coin: eklenme zamanı, zincir ve kontrat adresi, fiyat, hacim, arz, etiketler | `CMC_API_KEY` |
| CoinMarketCap `quotes/latest` | 7, 30 ve 90 gün sonraki fiyat | `CMC_API_KEY` |
| GoPlus `token_security` | EVM zincirleri: honeypot işareti, satış yetkileri, kontrat yetkileri, cüzdanlar, havuz kilidi | yok |
| honeypot.is `IsHoneypot` | EVM zincirleri: gerçek alım-satım simülasyonu, alım/satış/transfer vergisi | yok |
| RugCheck `tokens/{mint}/report` | Solana: basım ve dondurma yetkisi, cüzdanlar, hesap etiketleri (havuz, kilit, yaratıcı), havuz kilidi, risk listesi, rug işareti, piyasa likiditesi | yok |
| DexScreener `tokens/{adres}` | Bütün zincirler: DEX likiditesi, havuz yaşı, 24 saatlik alış ve satış sayısı. `/check`'te ayrıca: adresin hangi zincirde olduğu, ad, sembol, fiyat, hacim, proje profili (site, sosyal hesap) | yok |
| CoinMarketCap `listings/latest` (`sort=market_cap`) | Piyasa değerine göre ilk 500 coin: taklit kontrolünün referansı (sembol, ad, ana kontrat) | `CMC_API_KEY` |
| CoinMarketCap `v2/cryptocurrency/info` (`aux=urls`) | Yeni coinin web sitesi ve sosyal hesapları | `CMC_API_KEY` |

**CMC anahtarı:**
1. pro.coinmarketcap.com adresinde ücretsiz Basic plan açın. Ayda 10.000 kredi veriyor.
2. Anahtarı yalnızca Render panelinde `CMC_API_KEY` olarak girin. Sohbete, koda ya da loga yazmayın.
3. Anahtar istekte başlık (header) olarak gider, adreste görünmez. Hata metinleri yalnızca hata türünü taşır.

**Kredi bütçesi:**
- Her 30 dakikada bir tarama yapılır ve her tarama 1 kredi harcar; ayda yaklaşık 1.500 kredi eder.
- Sonuç ölçümleri günde birkaç kredi harcar.
- Taklit referansı (ilk 500 coin) günde bir kez yenilenir: 3 kredi. Başarısız olursa bir saat sonra yeniden denenir.
- Proje bağlantıları her coin için bir kez sorulur (100 coin başına 1 kredi). Yeni coin yoksa kredi harcanmaz.
- Toplam, ücretsiz planın ayda 10.000 kredisinin rahatça altında kalır.
- `/check` CMC kredisi harcamaz.

**Desteklenen zincirler:** Ethereum, BSC, Base, Arbitrum, Polygon, Avalanche, Optimism ve Solana.
- Diğer zincirlerdeki coinler (TON, Tron, Sui…) ❔ VERİ EKSİK olur.
- Kontratı olmayan coinler (ana ağ coinleri) de ❔ VERİ EKSİK olur.

Bütün dış metinler güvenilmez veridir. İsim, sembol, etiket ve sağlayıcı alanları ekranda kaçışlanır (escape edilir) ve hiçbir zaman komut olarak yorumlanmaz.

## Kontroller

Her kontrolün sonucu ✅ geçti, ⚠️ dikkat, ❌ kırmızı bayrak ya da ❔ bilinmiyor olur. Eşikler yaygın kullanılan kaba kurallardır. **Veriden öğrenilmedi.**

| Grup | ❌ kırmızı bayrak | ⚠️ dikkat |
|---|---|---|
| Satılabilirlik | Simülasyonda satış başarısız (honeypot). Simülasyonda alım, satış ya da transfer vergisi ≥ %10. GoPlus honeypot diyor. Vergi sonradan artırılabilir ya da cüzdana özel vergi konabilir. Solana'da dondurma (freeze) yetkisi açık ya da transfer ücreti ≥ %10. 24 saatte en az 20 alış var ama hiç satış yok. | Vergi ≥ %5. Transfer durdurulabilir. Kara liste yetkisi var. İşlem bekleme süresi var. |
| Yetkiler | Kaynak kodu doğrulanmamış. Yeni token basılabilir. Sahip bakiyeleri değiştirebilir. Gizli sahip var. Sahiplik geri alınabilir. Kontrat kendini yok edebilir. | Proxy (kod değiştirilebilir). Dış kontrata bağımlı. Solana'da isim ve sembol değiştirilebilir. |
| Dağılım | İlk 10 cüzdan ≥ %50 (EVM; borsa cüzdanları dahil). Yaratıcı ya da sahip payı ≥ %20. Solana'da RugCheck'in kendi yoğunlaşma uyarısı "danger" seviyesinde. | İlk 10 cüzdan ≥ %30. Yaratıcı payı ≥ %5. İlk 10'da en az 3 birbirine bağlı (insider) hesap. |
| Likidite | EVM'de LP'nin (V2 tokeni ya da V3/V4 pozisyonu) en az %50'si sıradan cüzdanlarda: çekilebilir. DEX likiditesi < 10.000 $. Solana'da RugCheck "rug pull olmuş" ya da "LP kilitsiz"/"düşük likidite" ("danger") diyor. | LP'nin %90'ından azı kilitli ya da yakılmış; geri kalanı kontratlarda, kilit doğrulanamıyor. Likidite < 50.000 $. En eski havuz 1 günden genç. |
| Kimlik | — | Kontrattaki sembol CMC'dekiyle aynı değil. Aynı sembolde başka bir yeni coin var (taklit olabilir). CMC'nin ilk 500 coininden biriyle aynı sembol ya da ad (taklit ya da köprü sürümü olabilir). CMC'de web sitesi yok. CMC'de sosyal hesap (X, Telegram/Discord, Reddit) yok. Bu kontroller yapılamadıysa o da ⚠️ olarak yazılır. |
| Hacim | — | 24 saatlik hacim likiditenin 50 katından fazla (şişirilmiş hacim olabilir). |
| Arz | — | Dolaşımdaki arz toplamın %20'sinden az. |

**Cüzdan payları ham miktarlardan hesaplanır.** Pay, tutulan miktarın toplam arza oranıdır. Böylece bir sağlayıcının yüzde birimi (0,1 mi, %10 mu) yanlış okunamaz.

Hesaba girmeyen hesaplar:
- havuz hesapları (EVM'de GoPlus'ın havuz adresleri; Solana'da RugCheck'in AMM etiketli hesapları);
- kilitli hesaplar (Solana'da RugCheck'in LOCKER etiketli hesapları);
- yakma adresleri;
- etiketli kontratlar (havuz, kilit kontratı).

**Solana havuz kilidi:**
- Yalnızca LP tokeni olan havuzlarda ölçülür. pump.fun bağlanma eğrisi de buna dahildir.
- Sonuç, havuzların likiditesine göre ağırlıklandırılır.
- Yoğunlaşmış likidite havuzlarında (Orca, Meteora DLMM…) kilitlenecek LP tokeni yoktur; bu havuzlar hesaba girmez.
- Bizim hesabımız en fazla ⚠️ verir. ❌, RugCheck'in kendi risk listesinden gelir.

**Likidite yedeği:** DexScreener bir havuzun likiditesini vermiyorsa, örneğin pump.fun eğrisinde, RugCheck'in ölçtüğü piyasa likiditesi kullanılır.

## Kimlik ve köken (yalnız ⚠️)

- **Taklit kontrolü:** CMC'nin piyasa değerine göre ilk 500 coini günde bir kez okunur.
  - Yeni coinin sembolü ya da adı bunlardan biriyle aynıysa ⚠️ "CMC #30 ile aynı sembol: taklit ya da köprü olabilir".
  - Sembolde `$` ve boşluklar, adda büyük/küçük harf ve noktalama yok sayılır ("$WIF" = "WIF").
  - Listede adı ve sembolü olan en az 400 farklı coin yoksa liste kullanılmaz. Tekrarlanan ya da adsız satırlar sayılmaz; eksik liste kontrolü sessizce zayıflatırdı.
  - Liste iki günden eskiyse kullanılmaz; kart "taklit kontrolü yapılamadı" der.
- **Köken:** CMC'nin proje bilgisinde web sitesi ya da sosyal hesap yoksa ⚠️.
  - CMC cevap vermezse ya da cevap bozuksa "proje bilgisi okunamadı" yazılır. Bu "bağlantı yok" demek değildir.
- **Neden yalnız ⚠️ (CMC coinlerinde):** Bir CMC coininin kimlik kanıtı ön-kayıtlı kontroldür: kontratı CMC'de kayıtlı ve kontrattaki sembolle uyumlu. Bu yeni kontroller o kanıtın üstüne yalnız uyarı ekler; çalışamadıklarında kırmızı bayrak gizlemiş olmazlar ve kart bunu açıkça yazar. Ayrıca bu kontroller ileriye dönük test ön-kayda alındıktan sonra eklendi. Taklit ya da eksik sosyal hesabın çöküşü öngördüğüne dair kanıt yok. ❌ verselerdi kararı değiştirip testi sıfırlarlardı. Uyarılar kayda (`warnings`) yazılır; ileride ayrı bir test için kullanılabilir.

## /check: tek adres kontrolü

- **Kullanım:** `/check <adres>` ya da EVM için zinciriyle `/check base 0x…`.
  - Zincir adları: solana (sol), ethereum (eth), bsc (bnb), base, arbitrum (arb), polygon (matic, pol), avalanche (avax), optimism (op).
  - Mesajdan yalnızca geçerli bir adres ve bilinen bir zincir adı okunur. Başka bir kelime varsa komut reddedilir; metin hiçbir zaman yorumlanmaz ya da geri yazılmaz.
- **Zincir:** Solana adresi biçiminden tanınır. EVM adresi için DexScreener'a bakılır:
  - Adresin havuzu desteklenen tek bir zincirdeyse o zincir kullanılır. PulseChain gibi Ethereum kopyası zincirler sayılmaz.
  - Birden çok zincirde havuz varsa ya da hiç yoksa bot tahmin etmez, zinciri yazmanı ister.
- **Tarama:** `/new` ile aynı kontroller ve aynı karar kuralı. Farklar:
  - Ad, sembol, fiyat ve hacim DexScreener'dan gelir. Arz verisi yoktur (❔, kritik değil).
  - **Kimlik:** Adres CMC'nin ilk 500 coininden birinin ana kontratıysa ✅ "CMC'de kayıtlı coin (#30)". Değilse ⚠️ "kimlik doğrulanamadı: adresi resmi kaynaktan teyit et"; yerleşik bir coinin sembolünü ya da adını taşıyorsa ayrıca taklit uyarısı.
  - **CMC listesi yoksa kimlik ❔ olur ve sonuç VERİ EKSİK'tir.** `/check` adresinin başka bir kimlik kanıtı yoktur; taklit kontrolü yapılamadan ✅ verilmez. Liste, yeni coin taraması tarafından kurulur: `NEW_COINS_ENABLED=1` ve `CMC_API_KEY` gerekir. Bot yeni açıldığında ilk tarama bitene kadar (birkaç dakika) `/check` VERİ EKSİK der.
  - **Köken:** DexScreener'da proje profili (site ya da sosyal hesap) yoksa ⚠️. Bu zayıf bir işarettir; profil ücretlidir.
- **Kayıt:** `/check` sonuçları ileriye dönük kayda **girmez**. Tokeni kullanıcı seçtiği için sonuçları taramanın isabetini ölçmez.
- **Sınırlar:** Aynı adresin sonucu 10 dakika saklanır. Yeni sorgular arasında en az 10 saniye olur (ücretsiz sağlayıcı sınırları).
- **Hata:** Bir sağlayıcıya ulaşılamazsa ilgili kontrol ❔ olur. Beklenmeyen bir hatada sohbete yalnızca hata türü yazılır.

## Karar

1. Herhangi bir ❌ varsa → **❌ AĞIR RİSK**. Menüde "uzak dur" bölümünde, nedeniyle gösterilir.
2. Yoksa, kritik gruplardan (satılabilirlik, yetkiler, dağılım, likidite, kimlik) birinde ❔ varsa → **❔ VERİ EKSİK**. Eksik veri asla güvenli sayılmaz.
3. Yoksa → **✅ BARİZ KIRMIZI BAYRAK YOK**. Menüde "güvenilir demek değildir" notuyla gösterilir.

**Hata ve sınır durumları:**
- Sağlayıcıya ulaşılamazsa, cevap bozuksa ya da bir alan boşsa o kontrol ❔ olur.
- **Zorunlu alanlar:** Kırmızı bayrak üretebilen her alan gelmeden grup ✅ olamaz.
  - Satılabilirlik (EVM):
    - Başarılı bir alım-satım simülasyonu: honeypot sonucu ile alım, satış ve transfer vergisi.
    - GoPlus'ta honeypot, vergi değiştirme ve cüzdana özel vergi alanları.
    - DEX'te havuzu olmayan bir token simüle edilemez; sonuç ❔ olur.
  - Yetkiler: kaynak kodu, basım, bakiye değiştirme, gizli sahip, sahipliği geri alma, kendini yok etme.
  - Dağılım: en az bir sayılabilen cüzdan. Boş liste ❔ olur.
  - Solana dağılımı: RugCheck'in yaratıcı bakiyesi (`creatorBalance`). Gelmezse ❔ olur.
  - Havuz: EVM'de payı %1 ya da daha fazla olan her LP sahibinin cüzdan mı kontrat mı olduğu bilinmeli. Solana'da LP tokenli havuzlar likiditenin %1'ini ya da fazlasını taşıyorsa ölçülebilir olmalı. Olmazsa ❔.
- Eklenme zamanı şimdiden sonra olan bir CMC satırı taramaya alınmaz.
- Bir taramada en fazla 40 coin kontrol edilir (sağlayıcı hız sınırları).
  - Sıraya giremeyen coin ❔ olur ve bir sonraki taramada kontrol edilir.
  - Kontrol edilen coin 6 saat sonra yeniden kontrol edilir; yetkiler ve havuz kilidi değişebilir.
- Tarama 90 dakikadan eskiyse menü uyarır.
  - Sağlık ucundaki `new_coins_fresh` false olur, `ok` da false olur.
  - Anahtar yoksa menü "henüz tarama yok, aday yok demek değildir" der.

## İleriye dönük kayıt ve değerlendirme

**Kayıt:** `/data/new_coins_ledger.jsonl` (yalnızca ekleme yapılır, fsync).
- **`FIRST_SEEN`:** Her coinin ilk görüldüğü andaki kararı, grupları ve fiyatı.
  - Kayıt kimlikleri şema etiketini taşır (`FIRST_SEEN:v2:…`, `OUTCOME:v2:…`).
  - Böylece v1 kayıtları v2'nin ilk görülmelerini ve sonuçlarını hiçbir zaman gölgelemez.
- **`VERDICT`:** Kararın sonradan değiştiği an.
- **`OUTCOME`:** 7, 30 ve 90 gün sonraki fiyat. Her vade, vadesinden sonraki 1 gün içinde ölçülür.
  - O pencerede CMC'ye sorulduysa ama fiyat yoksa `NO_QUOTE` yazılır. Sorulduğunu bir `QUOTE_GAP` kaydı kanıtlar.
  - Bot o pencerede kapalıysa vade `MISSED` (eksik) olur.
  - Kaçırılan bir vade, sonraki bir fiyatla asla doldurulmaz. Sıfır da yazılmaz.
- Her kayıt `trial` ve `can_authorize_trade: false` taşır.

**Ön-kayıt:** Trial `b57b9eb0a6073085` (`new-coins/v2`), `research/trials/registry.jsonl`. Hiçbir v2 kaydı ve hiçbir sonuç oluşmadan, 2026-10-05'te yapıldı.
- Yerini aldığı v1 trial'ı `3deff6cb87ad4e38` değerlendirilmeyecek. Neden yukarıda, "Sürüm 2" başlığında.
- v1'in birleşmemiş taslağı `71a4ac3276e13694` incelemede değişmişti. O değişiklik de henüz hiçbir kayıt yokken yapıldı.
- v2'nin birleşmemiş taslağı `49aa03675513c906` de incelemede değişti: ölçülemeyen önemli LP verisi ve eksik yaratıcı bakiyesi artık ❔ sayılıyor. Bu da henüz hiçbir kayıt yokken yapıldı.
- **İddia:** İlk görüldüğünde AĞIR RİSK olan coinler, BARİZ KIRMIZI BAYRAK YOK olanlardan daha sık çöker.
  - Çöküş: 30 günde −%90 ya da daha kötü, veya fiyat yok.
- **Zaman:** 2027-04-05'ten önce değerlendirilmez.
- **Yöntem:**
  - Güven aralığı: ilk görülme haftalarına göre bootstrap, %95.
  - Her grupta en az 30 coin gerekir.
- **Kararlar:**
  - `SCREEN_SEPARATES`: Fark pozitif ve aralık sıfırı içermiyor.
  - `NO_SEPARATION`: Aralık sıfırı içeriyor.
  - `REVERSED`: Fark negatif ve aralık sıfırı içermiyor.
  - `INCOMPLETE_DATA`: Gruplardan birinde 30'dan az coin var ya da 30 günlük sonuçların %20'sinden fazlası eksik (`MISSED`). `NO_QUOTE` eksik sayılmaz; çöküş sayılır.
- **Asıl sayı:** "Bariz kırmızı bayrak yok" denen coinlerin yüzde kaçı yine de çöktü? Bir isabet oranından söz edilecekse kaynağı bu sayı olur.
- **Değişiklik kuralı:** Eşik, kritik grup ya da karar kuralı değişirse yeni şema ve yeni trial gerekir. Bir test bunu zorlar.

## Bilinen sınırlar

- **Sağlayıcı cevaplarının doğrulanma durumu:**
  - GoPlus, honeypot.is, RugCheck ve DexScreener 2026-10-05'te gerçek cevaplarla doğrulandı.
  - Kırpılmış gerçek cevaplar `tests/fixtures/new_coins/` altında, regresyon testi olarak duruyor:
    - PEPE ve BRETT: kırmızı bayrak yok.
    - Henüz piyasaya çıkmamış, tek cüzdanlık BSC tokeni: ❌.
    - BONK: kırmızı bayrak yok.
    - Basım ve dondurma yetkisi açık bir pump tokeni: ❌.
  - CoinMarketCap cevabı bu ortamda anahtar olmadığı için denenemedi. CMC alanları belgelere göre okunuyor; ilk canlı `/new` çıktısı kontrol edilmeli.
- **EVM'de LP kontratlarda duruyorsa kilit doğrulanamıyor.** Örnekler: GoPlus'ın etiketlemediği kilit kontratları, token kontratının kendisi, pozisyon yöneticileri. Sonuç ⚠️ olur.
  - Dolandırıcı LP'yi kendi kontrol ettiği bir kontrata koyarsa bu tarama onu yakalamaz.
- **EVM'de borsa cüzdanları ayrılmıyor.** Arzı borsalarda tutulan bir coinde ilk 10 cüzdan payı yüksek görünür. Yerleşik coinlerde %33–35 civarında, yani ⚠️. %50'yi aşarsa yanlışlıkla ❌ alabilir; bu hata temkinli yöndedir.
- **Solana'da kendi cüzdan ve kilit hesabımız en fazla ⚠️ verir.** ❌, RugCheck'in kendi risk listesinden gelir.
- **pump.fun bağlanma eğrisindeki küçük tokenler** çoğunlukla 10.000 $'ın altında likiditeyle ❌ alır. Bu doğru bir risk işaretidir.
- **CMC'nin arz verisi çoğu yeni coinde projenin kendi beyanıdır.** Bu yüzden arz kontrolü kritik değildir.
- **Kapsam sınırı:** Bir taramada en yeni 200 coin okunur. 72 saatte 200'den fazla coin eklenirse eskileri pencereden düşer.
- **Taklit uyarısı yanlış alarm verebilir.** Köprülenmiş tokenler (ör. BSC'deki USDT) ve yaygın semboller (SUN, CAT…) de uyarı alır. Bu yüzden yalnız ⚠️.
- **CMC'nin proje bilgisi ve piyasa değeri listesi bu ortamda denenemedi.** Alanlar belgelere göre okunuyor; ilk canlı `/new` çıktısı kontrol edilmeli.
- **`/check` bir tokenin "gerçek" olduğunu kanıtlamaz.** Yalnızca CMC'nin ilk 500 coininin ana kontratlarını tanır.
- **Kırmızı bayrağı olmayan bir proje de çökebilir.** Pazarlama ile yükselip sonra satış baskısıyla düşmek kontrat hilesi gerektirmez. Bu tarama yalnızca bilinen tuzakları eler.
