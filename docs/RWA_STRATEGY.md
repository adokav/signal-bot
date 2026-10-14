# RWA Risk & Veri Bütünlüğü — Strateji Notu

> Durum: **karar kaydı / yön belgesi** (2026-10-14). Kod değil, ürün yönü.
> Bu belge `signal-bot` deposunun SHADOW/research sınırını değiştirmez ve hiçbir
> modüle emir/sermaye yetkisi vermez. Hukuki tavsiye değildir.

## 1. Temel karar: token en sonda

Sıralama: **ürün → kullanıcı → gelir → (ağ etkisi gerekiyorsa) token.**

Token yalnızca şu durumlarda meşrudur:
- Merkezi olmayan bir ağ varsa (birden çok veri sağlayıcı/doğrulayıcı stake eder, yanlış veride slashing).
- Protokol ücreti token'la ödeniyor veya token'a gerçek değer akışı varsa.
- Yönetişim gerçekten bir şeyi yönetiyorsa.

"Token olmadan bu ürün neden çalışmaz?" sorusuna cevap yoksa token çıkarılmaz.
Token gerekmezse kârlı bir veri/risk şirketi olmak da başarılı sonuçtur.

## 2. Mevcut avantaj

`signal-bot`un asıl değeri sinyaller değil, **disiplin**: point-in-time veri,
açık mum reddi, fail-closed, eksik veriyi güvenli saymama, new-listing manipülasyon
riski, araştırmanın emir yetkisi kazanmaması.

Uyarı: radarların forward-return edge'i out-of-sample doğrulanmamıştır
(bkz. `README.md`, `docs/EVOLUTION_ROADMAP.md`). Bu nedenle **sinyal satışı /
sinyal token'ı yapılmaz** (etik + hukuki risk; Türkiye'de yatırım danışmanlığı
kapsamına girebilir).

## 3. Seçilen yön: RWA risk ve veri bütünlüğü katmanı

RWA **ihraççısı değil, izleyicisi/denetleyicisi** olunur. Tokenizasyon emtialaştı
(BlackRock BUIDL, Ondo, Securitize, Centrifuge, Franklin vb. lisanslı oyuncular).
Boşluk bağımsız izleme tarafında:

- **NAV / oracle tazeliği** — son güncelleme yaşı; bayat veri `UNKNOWN`, "sağlıklı" değil.
- **Rezerv / attestation doğrulaması** — tarih, kapsam, on-chain arzla tutarlılık.
- **Redemption davranışı** — itfa kuyruğu, gecikme, ikincil piyasada NAV iskontosu.
- **Özel kredi riski** — gecikme/temerrüt ve yoğunlaşma için erken bağımsız sinyal.
- **Yoğunlaşma / bağımlılık** — custodian, tek imza yetkilisi, admin anahtarları.

İlkeler doğrudan ürün özelliğidir: *unknown is not safe*; eksik veri = `UNKNOWN`,
nötr veya pozitif kanıt değil; skorlar `can_authorize_trade = false`.

**Hedef müşteri:** RWA teminatı kabul eden lending protokolleri, DAO hazineleri,
fonlar, borsa listeleme ekipleri. Ödeme stablecoin ile.

### Türkiye'ye özgü ikincil fırsatlar (daha zor)
- Fatura / faktoring / ihracat alacakları (lisans + finans kurumu ortaklığı gerekir).
- Altın (talep güçlü; fark ancak doğrulanabilir rezervle).
- Gayrimenkul — tapu nedeniyle hukuken en zor; başlangıç için önerilmez.

## 4. Yol haritası

| Faz | Süre | İş | Başarı ölçütü |
|---|---|---|---|
| 0. Doğrulama | 0–1 ay | 15–20 risk ekibi/fon/protokol görüşmesi; avukat ön görüşmesi (7518 sayılı Kanun, SPK, MiCA) | ≥3 ekip "kullanırım/öderim" |
| 1. Off-chain MVP | 1–3 ay | İlk 10–15 RWA token için halka açık veriden risk paneli; haftalık şeffaf rapor | Güven, ilk ücretsiz pilotlar |
| 2. Doğrulanabilirlik + gelir | 3–6 ay | Skor hash'lerini önceden on-chain yazma (commit-reveal); testnet oracle feed; ücretli API/B2B | İlk ücretli müşteri |
| 3. Ağ | 6–12+ ay | Çoklu sağlayıcı gerekiyorsa stake/slashing'li ağ | Token'ın teknik ihtiyaç olarak doğması |
| 4. Token (gerekirse) | Sonra | Kullanım tabanlı tokenomics, vesting, getiri vaadi yok, hukuki yapı | Token'sız çalışmayan mekanizma |

## 5. Kırmızı çizgiler

- Getiri vaadi yok (Howey / SPK / MiCA). Yapıyı avukat belirler.
- Kanıtlanmamış performans pazarlanmaz.
- Ekip/yatırımcı token'larında uzun vesting, kilitli likidite, şeffaf hazine.
- Hayır: memecoin, kendi borsanda listeleme, sinyal grubu token'ı.

## 6. Sıradaki somut adım

En büyük 10 RWA token'ı için tablo: NAV kaynağı, güncelleme sıklığı, attestation
linki, redemption şartları, custodian, admin/upgrade yetkileri. Tablodaki boşluklar
Faz 1 panelinin ilk özellikleridir. Pazar isimleri bilgi sınırına göredir;
güncel araştırmayla doğrulanmalıdır.

## Açık işler
- [ ] Güncel RWA pazar ve rakip (risk/oracle sağlayıcıları) araştırması
- [ ] Türkiye/AB mevzuat özeti (avukatla)
- [ ] Faz 1 panel teknik tasarımı (mevcut `macro_*` fail-closed altyapısı üzerine)
