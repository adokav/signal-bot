# Yeni coinler: CoinMarketCap + güvenlik taraması

Kod: `acce_unified/new_coins.py`. Bot: `/new` ve paneldeki "🆕 Yeni Coinler (CMC)".

**Kullanıcı kararı (2026-10-05):**
- "Yeni Listeler" menüsü artık MEXC yerine CoinMarketCap'in yeni eklediği coinleri gösteriyor.
- Her coin bir dolandırıcılık taramasından geçiyor.
- MEXC yeni listeleme radarı kapatıldı (`UNIFIED_LISTING_RADAR_ENABLED=0`). Kodu şimdilik duruyor.

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
| GoPlus `token_security` | EVM zincirleri: honeypot, vergi, yetkiler, cüzdanlar, havuz kilidi | yok |
| RugCheck `tokens/{mint}/report` | Solana: basım ve dondurma yetkisi, cüzdanlar, havuz kilidi, rug işareti | yok |
| DexScreener `tokens/{adres}` | Bütün zincirler: DEX likiditesi, havuz yaşı, 24 saatlik alış ve satış sayısı | yok |

**CMC anahtarı:**
1. pro.coinmarketcap.com adresinde ücretsiz Basic plan açın. Ayda 10.000 kredi veriyor.
2. Anahtarı yalnızca Render panelinde `CMC_API_KEY` olarak girin. Sohbete, koda ya da loga yazmayın.
3. Anahtar istekte başlık (header) olarak gider, adreste görünmez. Hata metinleri yalnızca hata türünü taşır.

**Kredi bütçesi:**
- Her 30 dakikada bir tarama yapılır ve her tarama 1 kredi harcar; ayda yaklaşık 1.500 kredi eder.
- Sonuç ölçümleri günde birkaç kredi harcar.

**Desteklenen zincirler:** Ethereum, BSC, Base, Arbitrum, Polygon, Avalanche, Optimism ve Solana.
- Diğer zincirlerdeki coinler (TON, Tron, Sui…) ❔ VERİ EKSİK olur.
- Kontratı olmayan coinler (ana ağ coinleri) de ❔ VERİ EKSİK olur.

Bütün dış metinler güvenilmez veridir. İsim, sembol, etiket ve sağlayıcı alanları ekranda kaçışlanır (escape edilir) ve hiçbir zaman komut olarak yorumlanmaz.

## Kontroller

Her kontrolün sonucu ✅ geçti, ⚠️ dikkat, ❌ kırmızı bayrak ya da ❔ bilinmiyor olur. Eşikler yaygın kullanılan kaba kurallardır. **Veriden öğrenilmedi.**

| Grup | ❌ kırmızı bayrak | ⚠️ dikkat |
|---|---|---|
| Satılabilirlik | Honeypot. Tamamı satılamıyor. Alım ya da satış vergisi ≥ %10. Vergi sonradan artırılabilir ya da cüzdana özel vergi konabilir. Solana'da dondurma (freeze) yetkisi açık ya da transfer ücreti ≥ %10. 24 saatte en az 20 alış var ama hiç satış yok. | Vergi ≥ %5. Transfer durdurulabilir. Kara liste yetkisi var. İşlem bekleme süresi var. |
| Yetkiler | Kaynak kodu doğrulanmamış. Yeni token basılabilir. Sahip bakiyeleri değiştirebilir. Gizli sahip var. Sahiplik geri alınabilir. Kontrat kendini yok edebilir. | Proxy (kod değiştirilebilir). Dış kontrata bağımlı. Solana'da isim ve sembol değiştirilebilir. |
| Dağılım | İlk 10 cüzdan ≥ %50 (EVM). Yaratıcı ya da sahip payı ≥ %20. RugCheck'in kendi yoğunlaşma uyarısı "danger" seviyesinde. | İlk 10 cüzdan ≥ %30. Yaratıcı payı ≥ %5. İlk 10'da en az 3 birbirine bağlı (insider) hesap. |
| Likidite | Havuzun %50'den azı kilitli ya da yakılmış. DEX likiditesi < 10.000 $. RugCheck "rug pull olmuş" diyor. | Havuzun %90'dan azı kilitli. Likidite < 50.000 $. En eski havuz 1 günden genç. |
| Kimlik | — | Kontrattaki sembol CMC'dekiyle aynı değil. Aynı sembolde başka bir yeni coin var (taklit olabilir). |
| Hacim | — | 24 saatlik hacim likiditenin 50 katından fazla (şişirilmiş hacim olabilir). |
| Arz | — | Dolaşımdaki arz toplamın %20'sinden az. |

**Cüzdan payları ham miktarlardan hesaplanır.** Pay, tutulan miktarın toplam arza oranıdır. Böylece bir sağlayıcının yüzde birimi (0,1 mi, %10 mu) yanlış okunamaz.

Hesaba girmeyen hesaplar:
- havuz hesapları;
- kilitli hesaplar;
- yakma adresleri;
- etiketli kontratlar (havuz, kilit kontratı).

## Karar

1. Herhangi bir ❌ varsa → **❌ AĞIR RİSK**. Menüde "uzak dur" bölümünde, nedeniyle gösterilir.
2. Yoksa, kritik gruplardan (satılabilirlik, yetkiler, dağılım, likidite, kimlik) birinde ❔ varsa → **❔ VERİ EKSİK**. Eksik veri asla güvenli sayılmaz.
3. Yoksa → **✅ BARİZ KIRMIZI BAYRAK YOK**. Menüde "güvenilir demek değildir" notuyla gösterilir.

**Hata ve sınır durumları:**
- Sağlayıcıya ulaşılamazsa, cevap bozuksa ya da bir alan boşsa o kontrol ❔ olur.
- **Zorunlu alanlar:** Kırmızı bayrak üretebilen her alan gelmeden grup ✅ olamaz.
  - Satılabilirlik: honeypot, tamamı satılamama, alım vergisi, satış vergisi, vergi değiştirme, cüzdana özel vergi.
  - Yetkiler: kaynak kodu, basım, bakiye değiştirme, gizli sahip, sahipliği geri alma, kendini yok etme.
  - Dağılım: en az bir sayılabilen cüzdan. Boş liste ❔ olur.
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
- **`VERDICT`:** Kararın sonradan değiştiği an.
- **`OUTCOME`:** 7, 30 ve 90 gün sonraki fiyat. Her vade, vadesinden sonraki 1 gün içinde ölçülür.
  - O pencerede CMC'ye sorulduysa ama fiyat yoksa `NO_QUOTE` yazılır. Sorulduğunu bir `QUOTE_GAP` kaydı kanıtlar.
  - Bot o pencerede kapalıysa vade `MISSED` (eksik) olur.
  - Kaçırılan bir vade, sonraki bir fiyatla asla doldurulmaz. Sıfır da yazılmaz.
- Her kayıt `trial` ve `can_authorize_trade: false` taşır.

**Ön-kayıt:** Trial `3deff6cb87ad4e38`, `research/trials/registry.jsonl`. Hiçbir kayıt oluşmadan, 2026-10-05'te yapıldı.
- Birleşmemiş ilk taslak `71a4ac3276e13694` incelemede iki noktada değişti: zorunlu alanlar eklendi ve kaçırılan vadeler artık doldurulmuyor.
- Bu değişiklik de henüz hiçbir kayıt yokken yapıldı.
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

- **Sağlayıcı cevapları canlıda doğrulanmadı.** Kod yazılırken bu ortamdan CMC, GoPlus, RugCheck ve DexScreener'a erişilemedi. Ayrıştırıcılar belgelere göre yazıldı ve örnek veriyle test edildi.
  - Bir alan beklenenden farklı gelirse sonuç ❔ olur, ✅ olmaz.
  - İlk dağıtımdan sonra `/new` çıktısı kontrol edilmeli.
- **Solana'da havuz hesapları her zaman ayrılamayabilir.** Bu yüzden kendi hesabımız Solana'da en fazla ⚠️ verir. ❌, RugCheck'in kendi yoğunlaşma uyarısından gelir.
- **Uniswap v3 tarzı havuzlarda kilit verisi çoğu zaman yok.** Bu havuzlarda likidite NFT pozisyonlarıyla tutulur. Bu yüzden bu coinler ❔ VERİ EKSİK olur.
- **EVM'de borsa cüzdanları ayrılmıyor.** Arzı bir borsada tutulan bir coin yanlışlıkla ❌ alabilir. Bu hata temkinli yöndedir.
- **CMC'nin arz verisi çoğu yeni coinde projenin kendi beyanıdır.** Bu yüzden arz kontrolü kritik değildir.
- **Kapsam sınırı:** Bir taramada en yeni 200 coin okunur. 72 saatte 200'den fazla coin eklenirse eskileri pencereden düşer.
- **Kırmızı bayrağı olmayan bir proje de çökebilir.** Pazarlama ile yükselip sonra satış baskısıyla düşmek kontrat hilesi gerektirmez. Bu tarama yalnızca bilinen tuzakları eler.
