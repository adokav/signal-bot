# Yeni listeleme kohortu: kayıptan kaçınma testi (Binance, 2020-10 →)

**Ön-kayıt tarihi:** 2026-10-01. Bu belge ve karar kuralları **hiçbir sonuç
görülmeden** yazıldı.
**Deneme:** `new_listing_avoidance_spot` ailesi, trial `2381d2ac70c38a52`
(`research/trials/registry.jsonl`), kod parmak izi `a7ca652e76ee1c02`.
**Kod:** `trading/data/binance_history_identity.py`,
`trading/data/binance_listings.py`, `trading/backtest/listing_replay.py`,
`trading/strategies/listing_dossier.py`, workflow `listing_replay`.

Bu bir araştırma ölçümüdür. Sonucu ne olursa olsun emir yetkisi vermez
(`can_authorize_trade = false`, AGENTS.md §4, §9).

## Soru

Canlı MEXC yeni listeleme radarı adaylara `HOT / BUILDING / WATCH` etiketi
veriyor (`acce_unified/listings.py`). Bu etiketler hiç tarihsel kanıtla
sınanmadı. Uygulayıcı raporları ise yeni listelemeyi ilk günlerde alanların
aylar içinde paranın büyük kısmını kaybettiğini söylüyor
(`docs/EDGE_RESEARCH.md`, B2).

Test edilen iddia bir long stratejisi değil, bir **kaçınma** iddiası:

> Gerçekten yeni bir Binance USDT listelemesini, ilk işlemden sabit bir süre
> sonra alıp 30 veya 90 gün tutmak ortalamada para kaybettiriyor ve aynı
> sürede BTC tutmanın gerisinde kalıyor mu?

## Veri

- **Kaynak:** data.binance.vision. Borsadan kalkan çiftler dahil, yani
  survivorship yok.
- **Evren:** İlk işlem ayı 2020-10 ile veri sonu arasında olan USDT
  çiftleri. Şu çiftler dışlanır:
  - Coin daha önce başka bir kotasyonla (BTC, BUSD, BNB…) listelenmişse.
    USDT çifti sonradan eklenmiştir, bu yeni bir listeleme değildir.
  - Stabil coin'ler, fiat ve altına bağlı tokenlar, kaldıraçlı tokenlar ve
    başka bir varlığa bağlı tokenlar (BNSOL, WBETH…). Canlı kurallara ek
    olarak `binance_history_identity`.
  - **Ticker değişiklikleri:** 32 proje. Örnekler: MATIC→POL, FTM→S,
    EOS→A, RNDR→RENDER, KLAY→KAIA. Her birinde eski çiftin bittiği ay, yeni
    çiftin başladığı ay ya da bir önceki ay olmalı. Bu, liste yazılırken
    S3 listelemesiyle doğrulandı ve her derlemede yeniden kontrol edilir.
- **Ön inceleme (smoke test, 2026-10-01):** Yalnızca sayımlar ve veri
  kalitesi incelendi, getiri hesaplanmadı.
  - Pencere 2020-10 → 2026-08: 400 yeni listeleme.
  - Dışlananlar: 60'ı daha önce başka bir kotasyonla listelenmiş, 31'i
    ticker değişikliği, 16 kaldıraçlı, 14 stabil coin, 4 başka varlığa
    bağlı token, 1 emtia tokeni.
  - 32 ticker değişikliğinin hepsi veriyle "OK".
  - Son aylarda listelenenlerin 30/90 günlük penceresi henüz dolmadığı
    için bu coin'ler ilgili testlere girmez.
- **Mumlar:** Her listeleme için ilk ay dahil 5 aylık 1 saatlik mum.
  Karşılaştırma için tüm pencerede BTCUSDT 1 saatlik mum.
- **Veri sonu:** BTCUSDT'nin yayımlanmış son ayının son saniyesi. Sonrası
  okunmaz.

## Kural

- **İlk işlem anı:** Hacmi sıfırdan büyük ilk 1 saatlik mumun açılışı.
- **Girişler (3):** İlk işlem + 1 saat, + 24 saat, + 7 gün. Giriş fiyatı o
  saatin mumunun açılışı.
- **Tutma süreleri (2):** 30 gün ve 90 gün. Çıkış fiyatı, çıkış saatindeki
  mumun açılışı.
- **Bakım boşlukları:** O saatte mum yoksa, 6 saat içinde açılan ilk mum
  kullanılır. Daha uzun boşluk "çözümsüz" sayılır ve raporlanır.
  - Bu kural ön-kayıttan önce, yalnızca veri kalitesine bakılarak eklendi.
  - Smoke test verisinde 261 boşluğun 257'si en fazla 6 saat; medyan 2 saat.
  - Getiri hesaplanmadı.
- **Borsadan kalkma:** Çıkıştan önce verisi biten coin son kapanış
  fiyatından çıkar.
- **Eksik pencere:** Çıkış saati veri sonundan sonraya düşen gözlem
  alınmaz; kırpılmaz. Bu sayılar raporlanır.
- **Karşılaştırma:** Aynı giriş ve çıkış saatlerinde BTCUSDT getirisi.

## Karar kuralları (ön-kayıtlı)

Altı test var: 3 giriş × 2 tutma süresi. Bonferroni α = 0.05 / 6.

**Kararlar brüt getiriyle verilir: komisyon ve kayma yok.** Bir şeyin para
kaybettirdiğini iddia ederken maliyeti yok saymak muhafazakârdır, çünkü
maliyet sonucu yalnızca kötüleştirir. Net değerler ayrıca raporlanır:
taraf başına 7.5 bp komisyon + 25 bp kayma; stres senaryosunda ×1.5 ve ×2.

Bir test **AVOID_CONFIRMED** olur, ancak ve ancak:

1. En az **100** listeleme varsa;
2. ortalama brüt getirinin, listeleme ayına göre küme bootstrap'lı aile
   güven aralığının **üst sınırı < 0** ise;
3. ortalama BTC'ye göre fazla getirinin aynı güven aralığının **üst
   sınırı < 0** ise;
4. kronolojik **iki yarıda** hem ortalama brüt getiri hem ortalama fazla
   getiri ayrı ayrı **< 0** ise.

Diğer durumlar:
- < 100 listeleme → **INSUFFICIENT**;
- iki alt sınır da > 0 → **POSITIVE_SURPRISE**. Bu bir long sinyali
  değildir. Ancak maliyetli, ayrı bir ön-kayıtlı testle sınanabilir.
- kalan → **NO_CLAIM**.

**Tanılayıcılar (karar vermez):**
- yıl kırılımı;
- +24 saat ve +7 gün girişlerinde ilk pompa bandı. Canlı radarın
  eşikleriyle: %80 üstü CROWDED, %−35 altı ağır satış;
- girişten önceki hacim bandı (< 5M, 5–50M, > 50M USD);
- medyan getiri ve pozitif getiri oranı.

## Canlı sisteme etkisi (ön-kayıtlı)

- **+1 saat veya +24 saat girişlerinden en az biri AVOID_CONFIRMED olursa:**
  Canlı yeni listeleme radarı `HOT / BUILDING` adaylarını fırsat olarak
  göstermez. Aday ancak "kanıta göre kaçın" notuyla, en fazla izleme
  düzeyinde görünür.
  - Bu, Likit-100 ve taktik kapılarıyla aynı yolla yapılır: parmak izi
    eşleşen bir kanıt dosyası, ayrı bir PR'da.
  - Kanıt dosyası olmadan veya parmak izi eşleşmezse durum UNKNOWN olur
    (fail closed).
- **Diğer sonuçlar:** Radarın mevcut davranışı değişmez. Ama "etiketler
  kanıtsız" notu kalır.

## Bilinen sınırlar

- **Borsa farkı:** Binance listelemeleri ölçülüyor; canlı radar MEXC'de
  çalışıyor. MEXC daha erken ve daha riskli coin'leri listeler, taban
  oranın orada daha kötü olması beklenir. Ama bu test edilmedi.
- **Radar etiketleri yeniden üretilemiyor:** Radarın HOT/BUILDING
  etiketleri sosyal ve temel veriye dayanıyor. Bu verinin geçmişi yok, bu
  yüzden etiketler geçmişte tek tek üretilemez. Test, etiketlerin seçtiği
  havuzun (yeni listelemeler) taban oranını ölçer. Yalnızca fiyata dayalı
  tuzak kuralları tanılayıcı olarak bakılır.
- **İlk saatlerin fiyatı:** İlk saatlerde spread çok geniştir ve +1 saat
  giriş fiyatına gerçekte ulaşılamayabilir. Brüt karar bunu yok sayar;
  kaçınma iddiasını yalnızca güçlendirir.
- **Ortalama ve kuyruk:** Ağır sağ kuyruk ortalamayı medyandan çok yukarı
  çekebilir. Karar ortalamaya dayanır, yani bütün listelemeleri eşit
  ağırlıkla alan bir portföye. Medyan ayrıca raporlanır.
- **Aynı ay içindeki sıra:** "Daha önce başka kotasyonla listelenmiş" kontrolü
  ay düzeyindedir. Aynı ay içinde önce BTC, sonra USDT çifti açılmışsa coin
  yeni listeleme sayılır; ilk işlem anı USDT çiftinden alınır. Binance yeni
  coin'lerde çiftleri genellikle aynı anda açar.
- **Ticker değişiklik listesi:** Elle, bilgiye dayanarak yazıldı ve veriyle
  doğrulandı. Listede olmayan bir isim değişikliği "yeni listeleme"
  sayılır. Bu, sonucu sıfıra doğru çeker, yani kaçınma iddiasının aleyhine
  işler.

## Değişmezlik

Parmak izi şu dosyalardan hesaplanır:

- `acce_unified/cex.py`
- `trading/data/binance_vision.py`
- `trading/data/binance_universe.py`
- `trading/data/binance_history_identity.py`
- `trading/data/binance_listings.py`
- `trading/backtest/listing_replay.py`

Bunlardan biri değişirse
`tests/test_listing_replay.py::test_listing_replay_is_pre_registered_for_the_current_code`
kırılır.

## Sonuç — listing_replay run #1 (2026-10-01): **NO_CLAIM (6/6)**

Koşu: GitHub Actions `listing_replay` #1, commit `aacde29`, parmak izi
`a7ca652e76ee1c02` (ön-kayıtlı trial ile eşleşti).

**Veri:**
- 400 yeni listeleme. Dışlananlar ön incelemeyle aynı: 60'ı daha önce
  başka bir kotasyonla listelenmiş, 31 ticker değişikliği, 16 kaldıraçlı,
  14 stabil, 4 başka varlığa bağlı, 1 emtia.
- Bozuk satır yok, çözümsüz gözlem yok.
- 1 coin 7 gün dolmadan bitti.
- Hiçbir coin tutma süresi içinde borsadan kalkmadı.
- Çıkışı veri sonundan sonraya düşen gözlemler alınmadı: 30 günde 12–21,
  90 günde 70–71 gözlem.

Getiriler yüzde, brüt (maliyetsiz). "Fazla" = coin getirisi − aynı
saatlerde BTC getirisi. Güven aralıkları %99.2 (aile), listeleme ayına
göre küme bootstrap.

| Test | n | Ortalama | Medyan | Pozitif | BTC | **Fazla** | BTC'yi geçen | Brüt güven | Fazla güven | 1./2. yarı (brüt) | 1./2. yarı (fazla) | Karar |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|---|---|---|
| +1s @30g | 388 | −9.0 | −26.7 | %27 | +3.7 | −12.7 | %24 | [−23.4, +8.9] | [−25.2, +4.1] | −2.0 / −16.0 | −7.0 / −18.4 | NO_CLAIM |
| +1s @90g | 329 | −10.0 | −48.1 | %22 | +12.7 | −22.7 | %15 | [−34.4, +23.0] | [−36.8, −3.7] | +16.7 / −36.5 | −4.0 / −41.3 | NO_CLAIM |
| +24s @30g | 388 | −5.0 | −20.3 | %30 | +3.9 | −8.9 | %24 | [−19.3, +14.0] | [−21.4, +9.3] | +2.8 / −12.9 | −2.5 / −15.3 | NO_CLAIM |
| +24s @90g | 329 | −4.7 | −44.9 | %25 | +12.9 | −17.6 | %19 | [−31.1, +29.2] | [−33.2, +2.7] | +24.8 / −33.9 | +3.9 / −39.0 | NO_CLAIM |
| +7g @30g | 378 | +0.4 | −16.2 | %36 | +4.0 | −3.6 | %28 | [−12.4, +15.0] | [−12.8, +7.0] | +8.5 / −7.8 | +3.0 / −10.3 | NO_CLAIM |
| +7g @90g | 329 | +0.7 | −33.4 | %28 | +11.9 | −11.2 | %22 | [−27.3, +39.5] | [−26.7, +10.4] | +30.1 / −28.6 | +10.3 / −32.6 | NO_CLAIM |

Net değerler (taraf başına 7.5 + 25 bp) brütten yaklaşık 0.65 puan düşük;
hiçbir kararı değiştirmiyor.

### Ön-kayıtlı kurallara göre değerlendirme

- **Altı testin hiçbiri AVOID_CONFIRMED değil.** Kural 2'yi (brüt
  ortalamanın güven aralığı sıfırın altında) hiçbir test geçemedi; hepsi
  sıfırı içeriyor.
  - +1s @90g'de BTC'ye göre fazla getirinin aralığı tamamen sıfırın
    altında: [−36.8, −3.7]. Ama brüt aralık sıfırı içeriyor ve ilk yarı
    pozitif, bu yüzden kural sağlanmıyor.
- **POSITIVE_SURPRISE da yok.** Yeni listelemeleri almak bir edge değil.
- **Canlı sisteme etkisi (ön-kayıtlı):** Radarın davranışı değişmez.
  HOT/BUILDING etiketlerinin tarihsel kanıtı yok; bu durum sürüyor.

### Sonucun anlamı (tanılayıcı, karar değil)

1. **Tipik yeni listeleme kaybettiriyor; ortalamayı nadir büyük kazananlar
   kurtarıyor.**
   - Medyan getiri bütün testlerde negatif: −16 ile −48 arası.
   - Listelemelerin yalnızca %22–36'sı kârda, %15–28'i BTC'yi geçiyor.
   - Ortalamanın sıfıra yakın kalması birkaç çok büyük kazanana bağlı.
     2020'nin son çeyreğindeki 22 listelemenin 90 günlük ortalaması
     +%182 ile +%255 arası.
   - Pratik anlamı: Az sayıda yeni listeleme seçen biri için olası sonuç
     kayıptır. Ortalamayı yakalamak için hepsini alıp nadir kazananı
     yakalamak gerekir.
2. **Son dönemde açıkça negatif.**
   - İkinci yarılar, yani yaklaşık 2024 başından sonrası, altı testin
     hepsinde negatif: brüt −7.8 ile −36.5, fazla −10.3 ile −41.3.
   - 2025 listelemeleri ayrı ayrı anlamlı biçimde negatif. Örnek:
     +1s @90g'de −45.0 [−65.4, −20.0], BTC'ye göre −43.3.
   - Bu bulgu sonuçtan sonra yapılan bir bölmedir, karar değildir. Test
     edilecekse ileriye dönük, ön-kayıtlı bir kayıtla yapılmalı.
3. **Canlı radarın fiyat tabanlı tuzak kuralı geçmişte bir şey ayırmıyor.**
   - +24s girişinde ilk pompası %80'in üstünde olanlar (radarda
     "CROWDED", n=155) ortalamada daha kötü değil: +2.2 / +6.2.
     Medyanları da benzer: −27.9 / −45.9. Karşılaştırma grubu (n=227):
     −8.5 / −13.9, medyan −16.0 / −41.8.
   - %−35 altı ağır satış grubunda yalnızca 6 gözlem var; sonuç çıkmaz.
4. **Borsa farkı:** Bunlar Binance listelemeleri. MEXC daha erken ve daha
   riskli coin'leri listeler; orada tablo büyük olasılıkla daha kötüdür.
   Ama bu test edilmedi.
