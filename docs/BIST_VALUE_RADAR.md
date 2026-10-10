# BIST Değer Radarı v0 — Tasarım

## Amaç

Borsa İstanbul'da uzun vadeli değer yatırımı için aday üretmek: piyasa
değeri içsel değerinin altında, bilançosu ve kurumsal yönetimi güçlü,
sermayesini yüksek getiriyle yeniden yatırabilen şirketler.

Radar bir **izleme listesi ve gerekçe kartı** üretir. Alım/satım emri,
pozisyon büyüklüğü veya işlem yetkisi üretmez (`can_authorize_trade = false`).
Son karar insanındır.

## Sınırlar

Radarın sahip oldukları:

- KAP bildirimlerinin yayınlandığı haliyle (as-published) arşivi;
- point-in-time finansal tablo görünümü;
- sektör içi puanlar, eleme sonuçları, gerekçe kartları;
- ileriye dönük performans kaydı (her öneri o günkü verisiyle saklanır).

Sahip olmadıkları:

- aracı kurum kimlik bilgileri, emir gönderimi;
- portföy muhasebesi;
- nitel notun kendisi (insan girer, ayrı alanda tutulur).

## Veri katmanı (başlangıç seviyesi)

| Veri | Kaynak | Maliyet |
|---|---|---|
| Mali tablolar, yayın zamanı, sermaye artırımları, içeriden işlemler, denetçi | KAP (kap.org.tr) | Ücretsiz |
| Günlük fiyat, bölünme/bedelsiz düzeltmesi | EODHD EOD planı veya ücretsiz kaynak — kapsam doğrulanacak | 0–20 $/ay |
| USD/TRY, TÜFE | TCMB EVDS | Ücretsiz |

### KAP keşif bulguları (2026-10-10)

Site içi uç noktalar (resmi/dokümante API değil; değişebilir):

- **Şirket listesi:** `GET /tr/bist-sirketler` sayfası; her kayıtta
  `mkkMemberOid`, `stockCode`, unvan ve **bağımsız denetçi**
  (`relatedMemberTitle`) var.
- **Bildirim sorgusu:** `POST /tr/api/disclosure/members/byCriteria`,
  gövdede `fromDate`/`toDate` (`YYYY-MM-DD`), `mkkMemberOidList`,
  `disclosureClass: "FR"`. Uzun tarih aralıkları HTTP 500 dönüyor; sorgular
  ≤ ~6 aylık pencerelere bölünmeli.
  Dönen alanlar: `publishDate` (saniye hassasiyetinde), `disclosureIndex`,
  `year`, `ruleType` (3/6/9 Aylık, Yıllık), `subject`, `isLate`,
  `modifyStatus`.
- **Bildirim içeriği:** `GET /tr/Bildirim/{disclosureIndex}`. Finansal
  rapor tabloları HTML içinde XBRL etiketli satırlar olarak geliyor:
  - satır kimliği: `ifrs-full_Revenue|`, `ifrs-full_Assets|`,
    `kap-fr_...` gibi taksonomi adları;
  - ham değer: `td.taxonomy-context-value [title]` (binlik ayraçsız);
  - dönem başlıkları: `td.context-header` (Cari/Önceki Dönem tarihleri);
  - başlıkta `Sunum Para Birimi` (ör. `1.000 TL`) ve
    `Finansal Tablo Niteliği` (Konsolide/Solo).
- **Geçmiş derinliği:** Bildirim kaydı 2009'a kadar uzanıyor, ancak XBRL
  etiketli tablolar **2016 6 aylık** dönemden itibaren var (ASELSAN
  örneğinde 2016/3 aylık yalnızca PDF eki). Yapılandırılmış geçmiş ≈ 10 yıl.
- **Güvenilirlik:** 4–5 MB'lık sayfalar zaman zaman eksik iniyor
  (`curl: (18) transfer closed`). Her indirme bütünlük kontrolünden geçmeli;
  eksik sayfa ayrıştırılmamalı.
- **Resmi API:** `apiportal.kap.org.tr` bu ortamdan erişilemedi; erişim
  koşulları ve ücreti doğrulanmadı. Site uç noktaları düşük hızda, önbellekli
  ve nazik kullanılmalı (istekler arası bekleme, tekrar indirme yok).

### TMS 29 yeniden ifadesi: doğrulanmış örnek

ASELSAN, 31.12.2025 tarihli toplam varlıklar (bin TL):

| Rapor | Yayın zamanı | 31.12.2025 Toplam Varlıklar |
|---|---|---|
| 2025 Yıllık (`1561039`) | 24.02.2026 18:27:36 | 431.587.329 |
| 2026 6 Aylık (`1643141`) | 04.08.2026 18:39:41 | 508.228.606 |

Aynı tarih için rakam ≈ %17,8 değişmiş (Haziran 2026 satın alma gücüne
yeniden ifade). Sonuçlar:

1. Her rapor **yayınlandığı haliyle, değiştirilmeden** saklanır; üzerine yazma
   yok.
2. Bir `as_of` anında yalnızca `publishDate <= as_of` olan raporlar
   görünürdür; o anda en son yayınlanmış raporun kendi karşılaştırmalı
   kolonları kullanılır.
3. Uzun dönem eğilimler TL nominal serilerden değil, USD ve reel (TÜFE)
   serilerden okunur. Oranlar (marj, ROIC, borç/FAVÖK) aynı rapor içindeki
   rakamlardan hesaplanır ki satın alma gücü tutarlı olsun.

### Saklama modeli (öneri)

```text
raw_disclosure       disclosure_index, member_oid, stock_code, publish_time,
                     year, period, rule_type, subject, is_late, modify_status,
                     sha256, fetched_at, html_path
fact                 disclosure_index, concept (ifrs-full_Revenue), context
                     (dönem başı/sonu, cari/önceki), value, unit_scale,
                     consolidation, publish_time
```

Yazımlar atomik olmalı (geçici dosya + rename); ayrıştırması başarısız veya
eksik indirme `fact` tablosuna hiç girmez ve "bilinmiyor" sayılır.

## Hisse evreni

BIST Tüm, şu dışlamalarla:

- Yakın İzleme Pazarı, Piyasa Öncesi İşlem Platformu;
- son 6 ayda brüt takas / kredili işlem yasağı / SPK tedbiri;
- halka açıklık < %15;
- 60 günlük ortalama günlük işlem hacmi < 250 bin USD;
- < 3 yıllık XBRL finansal geçmişi.

Sektör modelleri: genel (sanayi/ticaret/hizmet), banka, holding, GYO,
sigorta. v0 yalnızca **genel model**; diğerleri ilk sürümde dışarıda.

## Kesin elemeler

| Eleme | Kriter |
|---|---|
| Kâr nakde dönmüyor | 3 yıllık Σ faaliyet nakit akışı / Σ esas faaliyet kârı < 0,6 |
| Kur riski | Döviz açık pozisyonu / öz kaynak < −%30 ve döviz geliri yok |
| Sulandırma | Bedelli artırımla hisse sayısı 3 yılda yıllık > %10 (bedelsiz hariç) |
| İlişkili taraf | İlişkili taraf satış/alımları > satışların %20'si |
| Denetim/yaptırım | Şartlı/olumsuz görüş, son 3 yılda yöneticilere SPK işlem yasağı |
| Eksik/bayat veri | Eleme: "bilinmiyor", puan verilmez |

## Puanlama

Her ölçü sektör içinde 0–100 yüzdelik sıraya çevrilir. Sütunlar ağırlıklı
**geometrik ortalama** ile birleşir. Ucuzluk, Kalite veya Kurumsal Yönetim
< 30 ise aday olamaz.

| Sütun | Ağırlık | Ölçüler |
|---|---|---|
| Ucuzluk | %30 | EV/EBIT, USD bazlı 3 yıllık ort. serbest nakit getirisi, çarpanın kendi USD geçmiş bandındaki yeri, ters DCF'nin ima ettiği büyüme |
| Kalite ve bilanço | %25 | Reel ROIC − USD bazlı sermaye maliyeti, faaliyet marjı istikrarı, net borç/FAVÖK, döviz pozisyonu, kısa vadeli borç payı, işletme sermayesi yükü |
| Kurumsal yönetim | %20 | Kurumsal Yönetim Endeksi ve derecelendirme notu, imtiyazlı paylar, hakim ortak pay rehni, içeriden alım/satım, temettü/geri alım tutarlılığı, bağımsız üyeler |
| Vizyon ve büyüme | %15 | USD satış büyümesinin sürekliliği, ihracat payı, Ar-Ge, yeniden yatırım oranı × ROIC, yatırım teslim geçmişi |
| Risk | %10 | Düşen bıçak (USD 12 ay performans + düşen kâr), müşteri/ürün/ülke yoğunlaşması, teşvik/regülasyon bağımlılığı |

Ağırlıklar **önseldir**; geçmiş veriye göre optimize edilmez. Değişiklikleri
sürümlenir ve önceki skorların önbelleğini geçersiz kılar.

Nitel not (0–5, yönetimin sözünü tutması) insan tarafından girilir, ayrı
alanda gösterilir ve model skoruna karıştırılmaz.

## Çıktı: gerekçe kartı

- skor ve sütun kırılımı, hangi elemelerden geçtiği;
- kötümser / baz / iyimser içsel değer ve kötümser senaryoya göre
  güvenlik payı;
- "Piyasa neden ucuz fiyatlıyor?" — en zayıf sütun ve ilgili veriler;
- kullanılan son raporun `disclosureIndex` ve yayın zamanı;
- eksik veya bilinmeyen alanların listesi.

## Doğrulama

- Ana ölçü USD getiri; ek olarak BIST 100'e göre fazla getiri ve
  mevduat/TÜFE üstü reel getiri.
- Borsadan çıkmış şirketler test evreninde kalır.
- 2023 TMS 29 geçişi yapısal kırılmadır; karşılaştırılabilir geçmiş kısa.
  Geriye dönük sonuçlar zayıf kanıt sayılır; ileriye dönük kayıt esastır.

## Yol haritası

1. **KAP toplayıcı:** şirket listesi + FR bildirim indeksi (2016 6A →
   bugün), ham HTML arşivi, bütünlük kontrolü, hız sınırı.
2. **Ayrıştırıcı:** XBRL satırlarından `fact` tablosu; dönem/kolon eşleme,
   birim ölçeği, konsolide/solo seçimi. Testler: eksik sayfa, bilinmeyen
   kolon, sayısal olmayan değer, yeniden ifade görünürlüğü.
3. **Fiyat katmanı:** günlük fiyat + bedelsiz/bölünme düzeltmesinin KAP
   sermaye artırımı bildirimleriyle çapraz kontrolü.
4. **Genel model v0:** elemeler + 5 sütun + kart; Telegram'da salt okunur
   komut.
5. Banka/holding/GYO modelleri, ileriye dönük kayıt raporu.

## Açık sorular

- Portföy yapısı: 12–20 hisse, çeyreklik değerlendirme (öneri) — onay
  bekliyor.
- Nitel notu kim, hangi arayüzden girecek?
- EODHD'nin BIST fiyat kapsamı ve bedelsiz düzeltme kalitesi deneme
  hesabıyla doğrulanacak.
- KAP resmi API portalının erişim koşulları.
