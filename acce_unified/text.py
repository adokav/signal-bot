"""Telegram text helpers shared by the research menus: escaping, a monospace table, number and clock formats.

Moved unchanged from the removed ``long_alerts`` module (docs/DEAD_CODE_AUDIT.md, third wave).
"""

from __future__ import annotations

import html
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

TSI = timezone(timedelta(hours=3))   # Türkiye saati (UTC+3, yaz saati yok)

# Telegram shows a <pre> block in a monospace font; rows stay within ~32
# characters so a phone screen does not wrap them.
LABEL_WIDTH = 12
RULE = "─" * 24

Row = tuple[str, str] | None


def _clock(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=TSI).strftime("%d.%m %H:%M")


def _num(value: float) -> str:
    value = float(value)
    if value >= 10_000:
        return f"{value:,.0f}"
    if value >= 100:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value <= 0:
        return f"{value:g}"
    return f"{value:.{min(12, 3 - math.floor(math.log10(value)))}f}"   # 4 significant digits


def esc(value: Any) -> str:
    """Escape untrusted text for Telegram HTML (symbols, provider fields, evidence lines)."""

    return html.escape(str(value), quote=False)


def table(rows: Iterable[Row]) -> str:
    """Two-column monospace block; ``None`` draws a rule. Everything inside is escaped."""

    lines = [RULE if row is None else f"{row[0]:<{LABEL_WIDTH}}{row[1]}".rstrip() for row in rows]
    return "<pre>" + esc("\n".join(lines)) + "</pre>"
