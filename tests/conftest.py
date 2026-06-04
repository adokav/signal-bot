"""Pytest setup for characterization tests against bot.py.

Refactor disiplini:
  - Bu testler 'bot.py mevcut davranisi' golden snapshot'lari ile karsilastirir
  - Amac: refactor sonrasi cikti degismedi mi dogrulamak
  - Edge/strateji dogrulugunu test etmiyor; SADECE 'ayni icin ayni cikar'

Non-determinism: bot.py icindeki bircok fonksiyon now_ts() / tr_now_text()
sonucunu plan/snapshot dict'lerine gomuyor. Karakterizasyon icin time'i
sabitleyip ayni input ile ayni output ureten bir ortam kuruyoruz. Bu
testlerin sirri donus dict'in bytewise ayniligi DEGIL; refactor sonra
ayni mantiksal cikti uretildigi.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# bot.py modul seviyesinde StateManager(STATE_FILE) yaratiyor.
# Test sirasinda repo kokunde state.json yaratmasin diye tmp'ye yonlendir.
os.environ.setdefault("STATE_FILE", "/tmp/bot_test_state.json")
# Telegram I/O kapali kalsin
os.environ.setdefault("TOKEN", "")
os.environ.setdefault("CHAT_ID", "")

sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

FROZEN_TS = 1_700_000_000  # 2023-11-14 22:13:20 UTC, deterministik
FROZEN_TR_TEXT = "2023-11-15 01:13:20 TR"


@pytest.fixture(autouse=True)
def freeze_time(monkeypatch):
    """now_ts ve tr_now_text'i sabitler, plan dict'lerine sizan timestamp'leri
    deterministik yapar. autouse: her testte aktif."""
    import bot

    monkeypatch.setattr(bot, "now_ts", lambda: FROZEN_TS)
    monkeypatch.setattr(bot, "tr_now_text", lambda: FROZEN_TR_TEXT)
    yield
