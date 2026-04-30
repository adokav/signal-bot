# Signal Bot ACCE Trade Brain

Bu repo `signal_bot_3_3_0_acce_trade_brain.py` dosyasını Render üzerinde 7/24 çalışan Flask tabanlı paper-trading/sinyal botu olarak çalıştırmak içindir.

## Ana dosya

```text
signal_bot_3_3_0_acce_trade_brain.py
```

Bu dosya:
- Flask health endpoint açar.
- Arka planda bot döngüsünü thread olarak çalıştırır.
- MEXC public market/futures verilerini kullanır.
- Telegram'a sinyal/heartbeat mesajı gönderebilir.
- NewsAPI varsa NewsAPI, yoksa GDELT haber katmanını kullanır.
- ACCE Trade Brain v1 ile paper trade / trade tracking yapar.
- Canlı emir varsayılan olarak kapalıdır.

## Repo kökü nasıl görünmeli?

```text
repo/
├── signal_bot_3_3_0_acce_trade_brain.py
├── requirements.txt
├── render.yaml
├── .python-version
└── README.md
```

Önemli: Python dosyası repo kökünde olmalı. Dosya adı Render start command ile bire bir aynı olmalı.

## requirements.txt

```text
Flask==3.0.3
requests==2.32.3
urllib3==2.2.3
```

## Render ayarları

Render'da **Web Service** oluştur.

Build Command:

```bash
pip install -r requirements.txt
```

Start Command:

```bash
python signal_bot_3_3_0_acce_trade_brain.py
```

Önemli: Bu proje FastAPI/uvicorn projesi değildir. Şu komutu kullanma:

```bash
uvicorn src.render_app:app --host 0.0.0.0 --port $PORT
```

Bu komut eski modüler ACCE paketleri içindi. Yeni referans dosyamız tek dosyalık Flask botudur.

## Environment Variables

Render > Service > Environment bölümüne şunları ekle:

```text
PYTHON_VERSION=3.11.9

TOKEN=telegram_bot_token
CHAT_ID=telegram_chat_id
NEWS_API_KEY=opsiyonel_newsapi_key

EXECUTION_MODE=PAPER
ENABLE_LIVE_TRADING=0
TRADE_TRACKING_ENABLED=1
PAPER_EXECUTION_ENABLED=1

ACCE_TRADE_BRAIN_ENABLED=1
ACCE_LONG_ONLY=1
ACCE_COLLATERAL_ASSET=USDT
ACCE_INITIAL_NOTIONAL_MAX_COLLATERAL_PCT=1.0
ACCE_MAX_PORTFOLIO_HEAT_PCT=0.12
ACCE_NO_SECOND_POSITION_WITHOUT_PROFIT_CUSHION=1
ACCE_PROFIT_CUSHION_TRIGGER_PCT=0.08
ACCE_PROFIT_LOCK_TRIGGER_PCT=0.18
ACCE_REQUIRE_RISK_ON_FOR_NEW_LONG=1
ACCE_MIN_QUALITY_GRADE=A
ACCE_MIN_CONFIDENCE=55

PM_SCALE_IN_ENABLED=0
SCAN_INTERVAL=300

SEND_STANDALONE_NEWS_ALERTS=0
SEND_MOVEMENT_ALERTS=0
SEND_SUMMARY_MESSAGES=0
```

## Health endpointleri

Deploy sonrası şu adresleri kontrol et:

```text
https://SENIN-SERVISIN.onrender.com/
https://SENIN-SERVISIN.onrender.com/healthz
https://SENIN-SERVISIN.onrender.com/readyz
```

Beklenen:
- `/` → `signal-bot is running`
- `/healthz` → bot state ve son başarılı scan bilgisi
- `/readyz` → Telegram ayarı varsa ready, eksikse degraded

## GitHub'a gönderme

```bash
git add .
git commit -m "Deploy signal bot 3.3.0 ACCE Trade Brain"
git push
```

Render auto-deploy açıksa push sonrası yeniden deploy eder.

## En sık hata nedenleri

### 1. Yanlış start command

Hatalı:

```bash
uvicorn src.render_app:app --host 0.0.0.0 --port $PORT
```

Doğru:

```bash
python signal_bot_3_3_0_acce_trade_brain.py
```

### 2. requirements.txt eksik

Eğer `ModuleNotFoundError: No module named 'flask'` veya `requests` hatası gelirse `requirements.txt` eksiktir veya repo kökünde değildir.

### 3. Dosya adı uyuşmuyor

Start command şu dosyayı arar:

```text
signal_bot_3_3_0_acce_trade_brain.py
```

GitHub'da dosya adı farklıysa Render çalışmaz.

### 4. TOKEN / CHAT_ID eksik

Bot yine açılır ama Telegram mesajı gönderemez. `STRICT_ENV=1` yapılırsa eksik TOKEN/CHAT_ID botu durdurabilir.

### 5. Python sürümü eski

`.python-version` dosyasını repo köküne koy:

```text
3.11.9
```

## Güvenlik

Canlı emir kapalıdır:

```text
EXECUTION_MODE=PAPER
ENABLE_LIVE_TRADING=0
```

Bu iki değişken bilinçli olarak değiştirilmedikçe canlı emir açılmaz.
