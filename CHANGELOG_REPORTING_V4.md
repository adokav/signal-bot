# Reporting v4 — Decision Change Only

## Added
- `TELEGRAM_DECISION_CHANGE_ONLY=1`
- `TELEGRAM_FORCE_HEARTBEAT_SECONDS=0`
- Decision fingerprint:
  - bot decision
  - trade permission
  - regime bucket
  - macro bucket
  - risk mode
  - open position count
  - best candidate symbol
  - news category

## Behavior
Telegram status report is now sent only when the practical bot decision changes.

Trade opened / closed / critical messages remain independent.
