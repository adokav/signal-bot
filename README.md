# Signal Bot v5 Core

[![tests](https://github.com/adokav/signal-bot/actions/workflows/tests.yml/badge.svg)](https://github.com/adokav/signal-bot/actions/workflows/tests.yml)

Signal Bot bir araştırma botudur. Dört menüsü vardır; hiçbiri emir vermez:

0. **Long takibi** (`/long`, `/long <sembol>`) — kullanıcının v3.2 raporunda adıyla verilen coin ve
   meme coinler (`research/register/`) için haftalık değerlendirme: rejim dilimi (BTC.D, altcoin
   sezonu payı), MEXC vadeli fitil dipleri ve likidasyonu onların %20 altında tutan kaldıraç sınırı
   (≤ 2×), 90 günlük funding, OI/piyasa değeri ve perp/spot, 10 haftalık kanal; eksik koşullar
   adıyla yazılır. Bot listeyi seçmez. `docs/POSITION_LONG.md` §13–§14.
1. **Altcoin izleme** (`/altcoin`) — ilk 150 altcoinin haftalık temel taraması: arz, DefiLlama
   ücret ve geliri, hack ve kontrat yetkileri, likidite. Ön-kayıtlı ileriye dönük kayıt.
   `docs/ALTCOIN_WATCH.md`. Daraltılacak: tarama kalkar, kontroller yalnız kayıt coinlerinde
   çalışır (aşağıdaki yön notu).
2. **Yeni coinler ve kontrat kontrolü** (`/new`, `/check <adres>`) — CoinMarketCap'in yeni
   coinleri ve istenen herhangi bir kontrat için dolandırıcılık taraması (honeypot, yetkiler,
   cüzdan yoğunlaşması, havuz kilidi). `docs/NEW_COINS.md`.
3. **D1 trend döngüsü** (`/d1`) — majörlerde günlük kanal kırılımı döngüsünün gölge kaydı.
   Kanıt etiketi RİSK_AZALTIR (iki bağımsız testte düşüşü yarıya indirdi; getiri kanıtı yok).
   `docs/TREND_LOOP_LIVE.md`.

> **Yön (2026-10-23):** Bot, kısa vadeli taktik radarlardan (BTC/ETH taktik long, Likit-100)
> uzun vadeli bir **long pozisyon sistemine** dönüşüyor. Liste kullanıcının v3.2 raporundan
> gelir ve depoda kayıtlıdır (`research/register/`; altcoin çekirdek UNI, PENDLE, AAVE,
> RENDER + koşullu BNB, INJ; meme çekirdek PEPE, BONK, DOGE, WIF, MOODENG; izleme ve
> havuzlar). Bot seçmez ve yükseltmez; bu coinler için rejim dilimi, vadeli fitil diplerine
> göre stop/kaldıraç, 2×/4× kısmi satış ve tez geçersizliği çıkışları üretir. Tasarım `docs/POSITION_LONG.md`; kanıt durumu ve elenen
> stratejiler `docs/EVOLUTION_ROADMAP.md`; silinen modüller `docs/DEAD_CODE_AUDIT.md`.

## Güvenlik sınırı

`SHADOW / RADAR ONLY`. Emir oluşturmaz, API anahtarıyla işlem yapmaz, sermaye riske etmez
(`can_authorize_trade = false`). İcra katmanı ancak ayrı bir istatistiksel doğrulama ve risk
PR'ından sonra eklenebilir. Anahtarlar yalnız ortam değişkenlerinde durur; hata metinleri adres
ve anahtar içermez; dış metin (web, Telegram, sağlayıcı alanları) komut olarak yorumlanmaz.

## Telegram

```text
/panel     Sade kontrol paneli
/altcoin   Altcoin izleme listesi; /altcoin <sembol> tek coin kartı
/d1        D1 döngü: gölge pozisyonlar, çıkış seviyesi ve acil stop
/new       CoinMarketCap yeni coinler: dolandırıcılık taraması
/check     Kontrat adresiyle anında tarama: /check [zincir] <adres>
/status    Tarama sağlığı ve veri durumu
/scan      Yeni coin taramasını şimdi yap
```

Kaldırılan `/tactical`, `/longs`, `/radar` komutları paneli açar.

## Çalıştırma

```bash
pip install -r requirements.txt
python bot.py
```

Zorunlu: `TOKEN`, `CHAT_ID`. İsteğe bağlı anahtarlar (Render panelinde, kodda ve sohbette
değil): `CMC_API_KEY` (yeni coinler), `COINGECKO_DEMO_API_KEY` (altcoin izleme; anahtarsız
CoinGecko sık 429 verir). Ayarlar `render.yaml` içinde.

## Üretim ilkeleri

- Tek Telegram `getUpdates` tüketicisi vardır; paket importları thread başlatmaz.
- Durum `/data/core_state.json` altında atomik yazılır; kayıt defterleri yalnız ekleme ile,
  fsync ile yazılır.
- Sağlayıcı hataları son geçerli görünümü silmez; eksik veri hiçbir zaman "güvenli" sayılmaz.
- Sağlık ucu (`/`) yalnız taze veri varsa `ok` döner; hata metni değil, hata sınıfı gösterir.
- Yeni özellik eklemek yerine önce out-of-sample kanıt aranır; her test önceden kayda girer
  (`research/trials/registry.jsonl`).

## Araştırma şasisi

`trading/` paketi botta çalışmaz: Binance verisi, walk-forward ve replay harness'ları, risk
ve araştırma değerlendiricileri. Kapanmış çalışmaların raporları `docs/` altında
(`*_REPORT.md`, `TRADE_LOOP_STUDY.md`, `MAJORS_STUDY.md`, `ATLAS.md`, `EDGE_RESEARCH.md`).
Workflow'lar GitHub Actions'ta elle tetiklenir (`trade_loop_confirm`, `carry_*`,
`listing_replay`, `backtest`, `atlas_macro`).
