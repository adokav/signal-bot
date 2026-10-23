# Kapı bekçisi: v3.2 listesinin kapıları, günlük

Kod: `acce_unified/altcoin_watch.py` (v2, 2026-10-23). Bot: `/altcoin`, `/altcoin <sembol>` ve paneldeki
"🛡 Kapı Bekçisi".

**Kullanıcı düzeltmesi (2026-10-23):** liste kullanıcının v3.2 raporundan gelir ve depoda kayıtlıdır
(`research/register/`, `docs/POSITION_LONG.md` §3.5). Bu modül artık **coin seçmez, sıralamaz, evren
taramaz.** Kayıttaki 22 coini her gün dört mekanik kapıdan geçirir ve sonucu `/long`'a "tez geçersizliği"
girdisi olarak verir (`docs/POSITION_LONG.md` §5.1, §14.3).

- **Kapılar yalnız düşürür.** ❌ `/long`'daki dilim sinyalini keser. ❔ bilgidir: rapor o alanları (kilit
  takvimi, mekanizma durumu) elle değerlendirdi; ücretsiz veri bunları doğrulayamaz. Hiçbir kapı bir coini
  listeye ya da sepete **yükseltmez**.
- **Bilinmeyen güvenli değildir.** Eksik ya da alınamayan veri ❔ olur; `/long` kapı kontrolü yoksa ya da üç
  günden eskiyse "kapı kontrolü bilinmiyor" der ve sinyal vermez.
- **Emir yetkisi yoktur** (`can_authorize_trade = false`).

## v1'den v2'ye

| v1 (2026-10-16) | v2 (2026-10-23) |
|---|---|
| CoinGecko'nun ilk 150 altcoini (BTC, stabil, sarılı hariç), haftalık | Kayıttaki 22 coin (13 altcoin, 9 meme), günlük |
| Dört kapı + üç ölçü (net getiri, FDV/gelir, kullanım) + eşit ağırlıklı sıralama | Yalnız dört kapı; ölçü ve sıralama yok (puan raporun) |
| Aylık kohort kaydı ve 182/365/730 günlük sonuç ölçümü; ön-kayıtlı deneme `b6e0fec86714efc2` | Yalnız kapı değişikliği kaydı (`GATE` olayları). Deneme **SUPERSEDED**: evreni, sıralaması ve sonuç kuralı artık çalışmıyor; v1 defteri (`/data/altcoin_watch_ledger.jsonl`, `WATCH`/`MONTH` kayıtları) silinmedi; hiçbir sonuç okunmadı (en fazla bir kohort oluşmuştu). |
| Şema `altcoin-watch/v1` | `altcoin-watch/v2`; anlık görüntü `superseded_trial` alanını taşır |

v1'in tasarım gerekçeleri (ağırlıkların veriden öğrenilmemesi, arzın akış olarak ölçülmesi, yakımın bir kez
sayılması) `docs/POSITION_LONG.md` §2'de raporla karşılaştırmalı olarak duruyor.

## Veri kaynakları

| Kaynak | Ne için | Anahtar |
|---|---|---|
| CoinGecko `coins/markets?ids=` | Kayıt coinlerinin fiyat, piyasa değeri, arz, FDV (tek çağrı) | İsteğe bağlı |
| CoinGecko `coins/list?include_platform=true` | Coinin asıl zinciri ve kontrat adresi | İsteğe bağlı |
| CoinGecko `market_chart` (365 gün, günlük) | 30 günlük medyan spot hacim (coin başına bir çağrı) | İsteğe bağlı |
| DefiLlama `lite/protocols2`, `v2/chains` | DefiLlama kayıtlarını CoinGecko kimliğine bağlama | yok |
| DefiLlama `overview/fees` (`dailyFees`) | Son 30 günün ücreti | yok |
| DefiLlama `hacks` | Hack geçmişi | yok |
| GoPlus `token_security` | EVM tokenlerinde kontrat yetkileri | yok |
| RugCheck `tokens/{mint}/report` | Solana tokenlerinde basım ve dondurma yetkisi | yok |

Günlük bir kontrol ≈ 25 CoinGecko çağrısı (anahtarsız 12 saniye arayla ≈ 5 dakika; anahtarla bir dakika).
`COINGECKO_DEMO_API_KEY` Render panelinde girilir; başlık olarak gider, hata metinlerinde görünmez.

## Kapılar

Eşikler raporun eşikleridir (G1 spot likidite 10 M$, G4 güvenlik; meme için M3), veriden öğrenilmedi.

| Kapı | ✅ | ❌ (`/long` sinyalini keser) | ❔ (bilgi) | — |
|---|---|---|---|---|
| **Arz** | Basılmış ama dolaşımda olmayan arz, dolaşımın en fazla %5'i | — | Pay %5'ten büyük (açılış takvimi ücretsiz veride yok); arz verisi eksik; toplam arz dolaşımdan %0,5'ten fazla küçük (tutarsız) | |
| **Faaliyet** | DefiLlama'da son 30 günde ücret var | Eşleşen kayıtta 30 günlük ücret sıfır | DefiLlama'da eşleşme yok | Meme coinler: raporun gelir kapısı yok (M1–M3) |
| **Güvenlik** | Ana ağ coini; kontratta tehlikeli yetki yok | Son 180 günde net ≥ 1 M$ hack. EVM: honeypot, bakiye değiştirme, gizli sahip, kendini yok etme, sahipliği geri alma. Solana: dondurma yetkisi açık | Tutarı ya da tarihi bilinmeyen hack; kontrat verisi alınamadı; proxy (AAVE, RENDER: yetkiler uygulama kontratında); asıl zincir desteklenmiyor | |
| **Likidite** | Son 31 günün tam UTC günlerinde medyan spot hacim ≥ 10 M$ | Medyan < 10 M$ | 20 günden az gün, en yeni gün 3 günden eski ya da seri yok | |

Uyarılar (⚠️, kapıyı kapatmaz): son 2 yılda hack; EVM'de basım fonksiyonu, proxy, durdurma, kara liste ya da
bu alanlardan birinin okunamaması; Solana'da basım yetkisi açık.

Eşleme kuralları v1'den aynen: yalnız asıl zincirdeki kontrat kontrol edilir (CoinGecko ilk sırada verir;
köprü kopyası farklı kontrattır); ana ağ coini yalnız zincirin kendisine yapılan saldırıyla elenir; token kendi
protokolünün bütün hack'leriyle değerlendirilir. Son 180 günde hack'i olan coinde kontrat çağrısı yapılmaz
(zaten ❌); likidite yine ölçülür.

**Sonuç:** herhangi bir ❌ → **ELENDİ**; yoksa herhangi bir ❔ ya da yapılmamış kapı → **İNCELEME GEREKLİ**;
yoksa → **KAPILARI GEÇTİ**. "—" (uygulanmaz) sonucu etkilemez.

## `/long` ile bağ (tez geçersizliği)

`altcoin_watch.gates_of(snapshot)` her coin için durumu, ❌ kapıları nedenleriyle ve kontrol zamanını verir;
`long_watch.gate_verdict` bunu haftalık kararda okur:

| Durum | `/long` sonucu |
|---|---|
| Hack, kontrat ya da faaliyet ❌ | Hemen "kapı ❌: <neden>" → sinyal yok |
| Likidite ❌ | İlk kararda uyarı ("likidite ❌ ilk karar"); iki ardışık haftalık kararda ❌ ise sinyal yok (histerezis, §5.1) |
| ❔ / ⚠️ | Sinyali etkilemez; kartta görünür |
| Kontrol yok ya da 3 günden eski | "kapı kontrolü bilinmiyor" → sinyal yok |

Açık pozisyonun kapatılması (§5.1 TEZ_GEÇERSİZ) pozisyon defteriyle (§8.2 ön-kaydı) gelecek; şimdilik kapı
yalnız yeni dilimi keser.

## Zamanlama, kayıt ve sağlık

- Kontrol günde bir (`ALTCOIN_WATCH_REFRESH_SECONDS`, varsayılan 86400, en az 3600). Bot 30 dakikada bir bakar;
  başarısız kontrol 30 dk, 1, 2, 4 ve 6 saat arayla yeniden denenir; anahtar eklenince bekleme atlanır.
- Bir ana kaynak (CoinGecko piyasa, DefiLlama eşleme/ücret/hack, CoinGecko platform listesi) başarısızsa kontrol
  başarısız sayılır; son anlık görüntü yaşıyla gösterilir. Tek coinin çağrısı başarısızsa yalnız o coin ❔.
- **Defter** `/data/altcoin_watch_ledger.jsonl` (yalnız ekleme, fsync): bir coinin durumu ya da ❌ kümesi
  önceki anlık görüntüye göre değişince `GATE` olayı (ilk kontrolde her coin bir kez). Olay kayıt dosyasının
  sha256'sını ve `can_authorize_trade: false` taşır. v1'in `WATCH`/`MONTH`/`OUTCOME` kayıtları aynı dosyada
  durur, okunmaz.
- `/status`: "Kapı bekçisi: N coin · ✅ a ❔ b ❌ c · süre önce". Sağlık ucu: `altcoin_watch_fresh`
  (3 güne kadar taze) ve son hata.
- Kayıt yüklenemezse modül `/long` ile birlikte hata durumunda kalır; hiçbir şey "biliniyor" gösterilmez.

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| `ALTCOIN_WATCH_ENABLED` | `1` | Modülü çalıştırır |
| `ALTCOIN_WATCH_REFRESH_SECONDS` | `86400` | Kontrol aralığı (en az 1 saat) |
| `ALTCOIN_WATCH_LEDGER_FILE` | `/data/altcoin_watch_ledger.jsonl` | Kapı değişikliği defteri |
| `COINGECKO_DEMO_API_KEY` | boş | Ücretsiz CoinGecko anahtarı (önerilir) |

## Bilinen sınırlar

- **Arz kapısı yalnız üst sınırla çalışır;** kilit açılış takvimi ücretsiz değil. Çekirdek coinlerin çoğu (UNI,
  AAVE, RENDER…) burada ❔ kalır; rapor bu alanı elle değerlendirdi, bot yükseltmez.
- **DefiLlama eşlemesi her coini kapsamaz;** eşleşmeyen altcoin faaliyette ❔ olur. Meme coinlerde faaliyet
  kapısı yoktur.
- **CoinGecko'nun toplam hacmi bütün borsaları kapsar;** sahte hacmi ayıklayamaz. Raporun G1/M3'ü de aynı
  tanımı kullanır.
- **Hack eşlemesi DefiLlama'nın protokol bağlantısıyla sınırlı;** köprü hack'leri ilgili coine her zaman
  bağlanmaz.
- **Proxy kontratlar** (AAVE, RENDER) GoPlus'ta doğrulanamaz: ❔, ❌ değil.
- Geçmişe dönük test yapılmadı ve dürüstçe yapılamaz: sağlayıcıların geçmiş verisi o gün bilinen veri değildir.
