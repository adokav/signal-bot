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

## Sonuç

_Henüz koşulmadı._
