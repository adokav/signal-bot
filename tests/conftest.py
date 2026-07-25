"""Shared pytest setup for Signal Bot v5 Core.

The former global characterization fixture patched timestamps on the deleted
15k-line bot. Active MEXC strategy tests are deterministic on their own and must
not depend on legacy bot internals.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("TOKEN", "")
os.environ.setdefault("CHAT_ID", "")
os.environ.setdefault("CORE_STATE_FILE", "/tmp/signal_bot_v5_test_state.json")
sys.path.insert(0, str(REPO_ROOT))
