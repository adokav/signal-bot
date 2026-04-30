# Signal Bot ACCE

Render üzerinde çalışacak sade dosya yapısı.

## Dosyalar

```text
bot.py
requirements.txt
render.yaml
.python-version
.gitignore
README.md
```

## Render Build Command

```bash
pip install -r requirements.txt
```

Bu komutu kendi bilgisayarında çalıştırmak zorunda değilsin. Render bu komutu deploy sırasında kendi sunucusunda çalıştırır.

## Render Start Command

```bash
python bot.py
```

## Render Environment Variables

Render > Environment bölümüne şunları gir:

```text
TOKEN=telegram_bot_token
CHAT_ID=telegram_chat_id

EXECUTION_MODE=PAPER
ENABLE_LIVE_TRADING=0
TRADE_TRACKING_ENABLED=1
PAPER_EXECUTION_ENABLED=1

ACCE_TRADE_BRAIN_ENABLED=1
ACCE_LONG_ONLY=1
ACCE_COLLATERAL_ASSET=USDT
ACCE_NO_SECOND_POSITION_WITHOUT_PROFIT_CUSHION=1
PM_SCALE_IN_ENABLED=0
SCAN_INTERVAL=300
```

NEWS_API_KEY opsiyoneldir.

## Önemli

Bu bot FastAPI/uvicorn projesi değildir. Start command:

```bash
python bot.py
```

olmalıdır.
