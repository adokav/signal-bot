# Signal Bot v5 Core

Signal Bot artık yalnız iki MEXC fırsat motorundan oluşur:

1. **MEXC Likit 100 Long İlk 3**
2. **MEXC doğrulanmış New Listing Patlama Radarı**

Eski çok amaçlı ajan, dinamik işlem evreni, Robinhood yan radarı, bağımsız sosyal/temel menüler, otomatik ağırlık öğrenme, ML sanal işlemler, parametre önerileri ve karmaşık Telegram komut merkezi üretim giriş noktasından kaldırılmıştır.

## Güvenlik sınırı

Bu sürüm `SHADOW / RADAR ONLY` çalışır. Emir oluşturmaz, API anahtarıyla işlem yapmaz ve sermaye riske etmez. İcra katmanı ancak ayrı bir istatistiksel doğrulama ve risk PR'ından sonra eklenebilir.

## Veri akışı

### Likit 100 Long İlk 3

- MEXC USDT Spot piyasaları 24 saatlik hacme göre sıralanır.
- En likit 100 parite seçilir.
- Spread, günlük aşırı hareket, 1s/4s momentum, EMA yapısı, RSI, ATR, hacim ivmesi ve arz kalitesi kapıları uygulanır.
- Teknik `%65`, arz kalitesi `%25`, piyasa bağlamı `%10` ağırlığıyla en iyi üç aday gösterilir.
- Her taramada mutlaka işlem adayı çıkması gerekmez.

### New Listing Patlama Radarı

- Yalnız resmî MEXC Spot ilk listeleme duyurusu veya ardışık `exchangeInfo` teyidi kabul edilir.
- Futures/perpetual duyuruları, eski katalog kayıtları ve pause→reopen olayları yeni listing sayılmaz.
- Hacim ivmesi, fiyat davranışı, arz/FDV ve topluluk kanıtı aday detayında birlikte gösterilir.
- Yeni listing adayları otomatik emir yetkisi kazanmaz.

## Telegram

Yalnız beş komut vardır:

```text
/panel   Sade kontrol paneli
/longs   MEXC Likit 100 Long İlk 3
/new     MEXC yeni listeleme adayları
/status  Tarama ve veri sağlığı
/scan    Şimdi yeniden tara
```

Eski `/social`, `/fundamentals`, `/filtered`, `/watch`, `/regime`, `/universe`, `/positions`, `/approvals` ve benzeri komutlar kaldırılmıştır.

## Çalıştırma

```bash
pip install -r requirements.txt
python bot.py
```

Zorunlu Telegram değişkenleri:

```text
TOKEN=
CHAT_ID=
```

CoinGecko ve topluluk sağlayıcıları yalnız New Listing zenginleştirmesi için isteğe bağlıdır:

```text
COINGECKO_DEMO_API_KEY=
COINGECKO_PRO_API_KEY=
X_BEARER_TOKEN=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=
```

## Üretim ilkeleri

- Tek Telegram `getUpdates` tüketicisi vardır.
- Paket importları thread başlatmaz.
- MEXC piyasa verisi ana otoritedir.
- State atomik olarak `/data/core_state.json` altında tutulur.
- Sağlayıcı hataları emir üretmez ve son geçerli görünümü silmez.
- Yeni özellik eklemek yerine önce out-of-sample kanıt aranır.
