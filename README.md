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


## Sprint 9 — Long Signal Sensitivity & Quality Upgrade

Bu sürüm long yönlü sinyal hassasiyetini artırır, fakat trade kalitesi için ek kalite kapısı koyar.

Eklenenler:

```text
LONG_SETUP_ENGINE_ENABLED=1
LONG_CONFLUENCE_MIN_SETUP=45
LONG_CONFLUENCE_MIN_TRADE=68
LONG_LATE_ENTRY_FILTER_ENABLED=1
LONG_MEME_MIN_VOLUME_RATIO=1.8
```

Yeni long durumları:

```text
NO_LONG_SETUP
LONG_SETUP_FORMING
LONG_TRIGGER_READY
LONG_TRADE_ALLOWED
LONG_TRADE_BLOCKED
```

Amaç: Daha erken radar, daha seçici tetik, daha sağlam long trade.


## Sprint 10 — Long Radar Explanation Upgrade

Bu sürümde Long Radar açıklaması sadeleştirildi ve detaylandırıldı.

Yeni format:
- En güçlü aday coin
- Teknik statü + insan diliyle açıklama
- Long Confluence / Radar eşiği / Trade eşiği
- Olumlu taraflar
- Eksik taraflar
- Net aksiyon

Artık `LDOUSDT long radarında | confluence 58.35 | PASS` gibi belirsiz ifade yerine:
`LDOUSDT radar’a girdi; setup eşiği geçti, trade eşiği henüz geçilmedi` mantığı kullanılır.


## Sprint 11 — Volume Surge & Relative Strength Radar

Bu sürüm, piyasa sakin olsa bile tekil coinde hacim patlaması ve BTC’ye göre relatif güçlenmeyi erken yakalamak için Coin Atak Radarı ekler.

Yeni env:
```text
VOLUME_SURGE_RADAR_ENABLED=1
VOLUME_SURGE_MIN_SCORE=55
VOLUME_SURGE_STRONG_SCORE=70
VOLUME_SURGE_TRIGGER_SCORE=80
VOLUME_SURGE_REL_STRENGTH_MIN=0.70
```

Önemli: Bu radar trade açmaz; sadece coin atağını erken yakalar. Trade için retest/trigger ve ACCE Trade Brain onayı gerekir.
