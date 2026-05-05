# Signal Bot ACCE — Reporting v3

Bu sürüm Telegram mesajlarını okunabilir karar raporu formatına çevirir.

Build Command:
```bash
pip install -r requirements.txt
```

Start Command:
```bash
python bot.py
```

Yeni env:
```text
TELEGRAM_REPORT_STYLE=HUMAN
TELEGRAM_FULL_HEARTBEAT=0
```

Debug rapora dönmek için:
```text
TELEGRAM_REPORT_STYLE=DEBUG
```


## Decision-change Telegram mode

Bu sürümde Telegram durum raporu varsayılan olarak yalnızca karar değiştiğinde gönderilir.

```text
TELEGRAM_DECISION_CHANGE_ONLY=1
TELEGRAM_FORCE_HEARTBEAT_SECONDS=0
```

Bu modda tekrar eden WAIT / NEUTRAL / aynı risk durumu mesajları gönderilmez.

Eski periyodik heartbeat davranışına dönmek için:

```text
TELEGRAM_DECISION_CHANGE_ONLY=0
```

Nadir zorunlu heartbeat istersen örneğin 6 saatte bir:

```text
TELEGRAM_FORCE_HEARTBEAT_SECONDS=21600
```
