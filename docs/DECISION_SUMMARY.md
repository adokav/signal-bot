# Karar mekanizması özeti (2026-10-23)

Botun ne yaptığını tek sayfada anlatır. Ayrıntı ve gerekçe: `docs/POSITION_LONG.md` (tasarım),
`docs/ALTCOIN_WATCH.md` (kapı bekçisi), `docs/BREAKOUT_COHORT.md` (kanıt çalışması). Hiçbir kural kanıtlı değildir;
bot emir vermez (`can_authorize_trade = false`), karar kullanıcınındır.

## 1. Girdi: liste rapordan gelir

`research/register/v3_2_2026-10-23.json` — kullanıcının v3.2 raporunun kendi çıktısı: 13 altcoin, 9 meme coin,
sepetler, puanlar, senaryo ve risk çıktıları, tetikleyiciler, rejim ve pozisyon kuralları. Bot listeyi **seçmez,
sıralamaz, yükseltmez**; yalnız okur ve doğrular (tek alan bozuksa hiçbir şey yüklenmez). Değişiklik PR ile gelir.
Kayıt 180 günden eskiyse "bayat": uyarı ve hiçbir coine yeni dilim.

| Sepet | Coinler | Dilim sinyali alır mı |
|---|---|---|
| Çekirdek | UNI, PENDLE, AAVE, RENDER · PEPE, BONK, DOGE, WIF, MOODENG | Evet (bütün koşullar sağlanırsa) |
| Çekirdek, koşullu | BNB (kazanç verisi), INJ (emisyon) | Koşul bayrağı PR ile kalkana kadar hayır |
| İzleme / havuzlar | SKY · HYPE, LINK, AVAX · MUBARAK · FLOKI, FARTCOIN · QNT, OKB, SHIB · XMR | Hayır; kartları izlenir |

## 2. Günlük: kapı bekçisi (`/altcoin`)

Her gün, 22 coin, dört kapı. Kapılar **yalnız düşürür**: ❌ `/long` sinyalini keser; ❔ bilgidir (rapor o alanı elle
değerlendirdi); eksik veri ❔'dir, asla ✅ değil.

| Kapı | ❌ nedeni | Kaynak |
|---|---|---|
| Arz | — (yalnız ❔: dolaşım dışı arz > %5, takvim ücretsiz veride yok) | CoinGecko |
| Faaliyet (altcoin) | Son 30 günde ücret geliri yok | DefiLlama |
| Güvenlik | Son 180 günde net ≥ 1 M$ hack; EVM: honeypot, bakiye değiştirme, gizli sahip, kendini yok etme, sahipliği geri alma; Solana: dondurma yetkisi | DefiLlama, GoPlus, RugCheck |
| Likidite | 30 günlük medyan spot hacim < 10 M$ | CoinGecko |

Durum değişince deftere `GATE` olayı yazılır.

## 3. Haftalık: long kararı (`/long`)

Pazartesi 00:00 UTC sonrası ilk tick'te (Pazar mumu kapanmış), her coin için:

**Seriler.** MEXC vadeli günlük mumlar (730 gün), funding geçmişi (90 gün), açık pozisyon, sürdürme marjı, endeks
fiyatı; CoinGecko piyasa değeri, spot hacim, toplu perp OI ve hacim (sabit 9 borsa), BTC.D; ilk 50 altcoinin 90
günlük getirisinden ALT90; FRED'den VIX (bilgi).

**Koşullar — hepsi birden sağlanmalı; sağlanmayan adıyla yazılır.**

| # | Koşul | Eşik / kaynak | Sağlanmazsa |
|---|---|---|---|
| 1 | Kayıt bayat değil | ≤ 180 gün | "kayıt bayat" |
| 2 | Kapı kontrolü biliniyor ve ❌ yok | Kapı bekçisi ≤ 3 gün; hack/kontrat/faaliyet ❌ hemen, likidite ❌ iki ardışık kararda | "kapı kontrolü bilinmiyor" / "kapı ❌: …" |
| 3 | Sepet çekirdek, koşul yok | Kayıt | "sepet …" / "koşul bekliyor" |
| 4 | Rejim DİLİM 1 ya da 2 | Rapor §9: BTC.D < 55 → dilim 2; BTC.D > 60 ve yükseliyor → azalt; ALT90 > 50 **ve** BTC.D düşüyor → dilim 1; yön 4 haftalık arşivden | "rejim BEKLE / AZALT / BİLİNMİYOR" |
| 5 | Haftalık kanal YUKARI | Haftalık kapanış son 10 haftanın en yükseğini aşmış ve en düşüğünün altına inmemiş (D1 ailesi, haftalık) | "kanal AŞAĞI / BİLİNMİYOR" |
| 6 | Fitil referansı biliniyor | min(WICK365, FLASH365, CYCLE_LOW) hesaplanabildi | "fitil referansı bilinmiyor" |
| 7 | Sürdürme marjı biliniyor; 1× kuralı sağlıyor | Borsanın oranı alındı; L_max ≥ 1 | "sürdürme marjı bilinmiyor" / "1× bile …" |
| 8 | Funding biliniyor ve ≤ %20/yıl | FUND90, ≥ 60 ödeme | "funding bilinmiyor" / "funding yıllık %N > %20" |
| 9 | Kaldıraç birikimi yok | OI/pd > %20 **ve** perp/spot > 6 değil (rapor tetiği); toplam eksikse bilinmiyor | "OI/hacim bilinmiyor" / "kaldıraç birikimi" |

Sonuç: **DİLİM 1** ya da **DİLİM 2** (rejime göre) ya da eksik koşulların listesi. Bilinmeyen hiçbir koşul sağlanmış
sayılmaz.

**Seviyeler (her kartta).**

| Seviye | Tanım |
|---|---|
| Fitil referansı | giriş × (1 + en kötü fitil): son 365 günün en derin fitili, tek günlük en derin düşüş, 730 günlük döngü dibi (hangisi daha derinse) |
| L_max | 1 / (1 + sürdürme marjı − 0,8 × (1 + en kötü fitil)); kod tavanı 2×; varsayılan pozisyon 1× |
| Likidasyon 1× ve L_max | giriş × (1 − 1/L + marj) |
| Marj koruma stopu | Fitil referansının %10 altı; yalnız 1× üstü kaldıraçta, tezin parçası değil |
| 2× / 4× | Kısmi satış seviyeleri; pozisyon açılınca birikmiş funding ile düzeltilir |
| Pay | Kayıp bütçesi ÷ 0,8 × min(1, 0,9/σ); likidite üst sınırı = günlük spot hacmin %10'u |

**Yalnız bilgi, karara girmez:** VIX; anlık ve 90 günlük perp primi; OI/pd ve perp/spot sayıları (tetik dışında);
raporun σ, medyan çarpan, dokunma olasılıkları; tetikleyici bayrakları (AAVE geri alım, SKY hacim, LINK hazine…).

## 4. Çıktılar

- `/long` liste: rejim satırı (dilim, BTC.D, yön, ALT90, VIX), çekirdek coinler (durum, fitil, L_max, funding),
  izleme ve havuzlar, eksik veri.
- `/long <sembol>` kart: koşullar, seviyeler, funding, prim, OI, kanal, kapılar, pay, tetikleyiciler.
- `/altcoin`, `/altcoin <sembol>`: kapı durumları ve nedenleri.
- Defterler (`/data/*.jsonl`, yalnız ekleme): haftalık `KARAR` olayı (her coin; kayıt sha256, kapı kararı,
  seviyeler), günlük `GATE` değişiklikleri. İleri kayıt ön-kaydı henüz yok (`trial: null`).
- `/status` ve `/` sağlık ucu: tazelik ve eksiksizlik; eksik seri varsa sağlıksız.

## 5. Botun karar vermediği şeyler

Sermaye ve pozisyon büyüklüğü (yalnız yüzde ve üst sınır yazar), kaldıraç seçimi (sınırı yazar), gerçek giriş
fiyatı ve emir, raporun listesi ve sepetleri, koşul bayraklarının kalkması, tetikleyicilerin "gerçekleşti" durumu.
Bunlar kullanıcıdan PR ile gelir.

## 6. Kanıt durumu ve açık kararlar

- Kanıt: hiçbiri. D1 ailesi günlükte majörlerde RİSK_AZALTIR; haftalık ölçekte test edilmedi (§8.1 bekliyor).
  Atılım kohortu keşif koşusu bekliyor; perp katmanına `PREM90` eklendi.
- Açık: rejim kuralında "ve" mi "veya" mı; BTC.D arşivinin elle tohumlanması; kayıp bütçesi, dilim ve kısmi satış
  oranları; pozisyon defteri ve ileri kayıt ön-kaydı; raporlama tasarımı.
