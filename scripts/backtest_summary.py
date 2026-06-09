"""Render historical replay JSON report as GitHub-flavored markdown summary.

Read from REPORT_PATH env (defaults to historical_replay_report.json) and
print markdown to stdout. Intended for $GITHUB_STEP_SUMMARY in CI.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPORT_PATH = Path(os.getenv("REPORT_PATH", "historical_replay_report.json"))


def _fmt_pct(v):
    if v is None:
        return "N/A"
    try:
        return f"{float(v) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_winrate(v):
    if v is None:
        return "N/A"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_num(v, digits=2):
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "N/A"


def _section_metrics(name: str, m: dict, capital: float) -> list[str]:
    n = m.get("trades", 0) or 0
    if n == 0:
        return [f"### {name}", "_Trade yok._", ""]
    final_eq = m.get("final_equity")
    if final_eq is not None and capital > 0:
        pnl_pct = (float(final_eq) - capital) / capital
        pnl_usd = float(final_eq) - capital
        pnl_line = f"P&L: {pnl_usd:+.2f} USDT ({pnl_pct * 100:+.2f}%)"
    else:
        pnl_line = "P&L: N/A"
    return [
        f"### {name}",
        f"- Trades: **{n}** ({m.get('wins', 0)}W / {m.get('losses', 0)}L)",
        f"- Win rate: **{_fmt_winrate(m.get('win_rate'))}**",
        f"- Profit factor: {_fmt_num(m.get('profit_factor'))}",
        f"- Expectancy: {_fmt_num(m.get('expectancy_r'), 3)} R",
        f"- Avg win / loss: {_fmt_num(m.get('avg_win_r'), 3)} R / {_fmt_num(m.get('avg_loss_r'), 3)} R",
        f"- Max drawdown: {_fmt_pct(m.get('max_drawdown_pct'))}",
        f"- Final equity: {_fmt_num(final_eq)} USDT",
        f"- {pnl_line}",
        "",
    ]


def _table_breakdown(title: str, breakdown: dict, capital: float) -> list[str]:
    if not breakdown:
        return []
    lines = [f"### {title}", "", "| Bucket | Trades | WR | PF | ExpR | P&L | DD |", "|---|---|---|---|---|---|---|"]
    for bucket, m in breakdown.items():
        n = m.get("trades", 0) or 0
        if n == 0:
            continue
        final_eq = m.get("final_equity")
        pnl_pct_str = "N/A"
        if final_eq is not None and capital > 0:
            pnl_pct = (float(final_eq) - capital) / capital
            pnl_pct_str = f"{pnl_pct * 100:+.2f}%"
        lines.append(
            f"| {bucket} | {n} ({m.get('wins', 0)}W/{m.get('losses', 0)}L) | "
            f"{_fmt_winrate(m.get('win_rate'))} | "
            f"{_fmt_num(m.get('profit_factor'))} | "
            f"{_fmt_num(m.get('expectancy_r'), 3)} | "
            f"{pnl_pct_str} | "
            f"{_fmt_pct(m.get('max_drawdown_pct'))} |"
        )
    lines.append("")
    return lines


def _sample_trades(samples: list[dict]) -> list[str]:
    if not samples:
        return []
    lines = ["### Son 25 trade (ornek)", "", "| Sembol | Yon | Rejim | R | Sebep |", "|---|---|---|---|---|"]
    for t in samples[-25:]:
        pnl_r = t.get("pnl_r")
        try:
            r_str = f"{float(pnl_r):+.2f}"
        except (TypeError, ValueError):
            r_str = "N/A"
        lines.append(
            f"| {t.get('symbol', '?')} | "
            f"{t.get('direction', '?')} | "
            f"{t.get('regime', '?')} | "
            f"{r_str} | "
            f"{str(t.get('reason') or t.get('exit_reason') or '?')[:30]} |"
        )
    lines.append("")
    return lines


def main() -> int:
    if not REPORT_PATH.exists():
        print(f"# Backtest sonucu\n\n**Hata:** Rapor dosyasi bulunamadi (`{REPORT_PATH}`).")
        print("Replay calismadi veya cikti yazilamadi.")
        return 1

    report = json.loads(REPORT_PATH.read_text())
    summary = report.get("summary") or {}
    coverage = report.get("coverage") or {}
    capital = float(os.getenv("ACCOUNT_SIZE_USD", "100") or 100.0)

    out: list[str] = []
    out.append(f"# Backtest Raporu")
    out.append("")
    out.append(f"- Bot version: `{report.get('version')}`")
    out.append(f"- Olusturma: {report.get('created_at')}")
    out.append(f"- Mode: {report.get('mode')}")
    out.append(f"- Semboller: {', '.join(report.get('symbols') or [])}")
    out.append(f"- Gun: {report.get('days')}")
    out.append(f"- Baslangic kapital: **{capital:.2f} USDT**")
    out.append(f"- Veri araligi: {coverage.get('first_ts')} -> {coverage.get('last_ts')}")
    out.append(f"- 5m bar: {coverage.get('bars_evaluated')}, raw sinyal: {coverage.get('raw_signals_seen')}, bloklu: {coverage.get('blocked_signals_seen')}, kapanmis trade: {coverage.get('closed_trades')}")
    out.append("")

    out.extend(_section_metrics("Genel Performans", summary, capital))
    out.extend(_table_breakdown("Yon Bazli", report.get("by_direction") or {}, capital))
    out.extend(_table_breakdown("Rejim Bazli", report.get("by_regime") or {}, capital))
    out.extend(_table_breakdown("Sembol Bazli", report.get("by_symbol") or {}, capital))
    out.extend(_table_breakdown("Grup Bazli", report.get("by_group") or {}, capital))
    out.extend(_sample_trades(report.get("sample_trades_tail") or []))

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
