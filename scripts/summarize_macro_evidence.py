"""Summarize purged walk-forward macro evidence from a replay JSONL file.

This command reports out-of-sample factor bucket behavior only. It does not
rank factors, create a macro regime, size a position or modify live signals.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from macro_evidence import build_evidence_report
from macro_replay import ForwardOutcome, ReplayRow


def _load_replay(path: Path) -> tuple[ReplayRow, ...]:
    rows: list[ReplayRow] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            payload = json.loads(line)
            outcomes = {
                key: ForwardOutcome(**value)
                for key, value in (payload.get("outcomes") or {}).items()
            }
            for outcome in outcomes.values():
                outcome.validate()
            rows.append(ReplayRow(
                as_of=payload["as_of"],
                factors=dict(payload.get("factors") or {}),
                factor_complete=bool(payload.get("factor_complete")),
                outcomes=outcomes,
            ))
    return tuple(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("replay", nargs="?", default="research/data/macro_btc_daily_replay.jsonl")
    parser.add_argument("--min-train-rows", type=int, default=365)
    parser.add_argument("--test-rows", type=int, default=90)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    replay = _load_replay(Path(args.replay))
    factor_names = sorted({name for row in replay for name, value in row.factors.items() if value is not None})
    report = build_evidence_report(
        replay,
        factors=factor_names,
        min_train_rows=args.min_train_rows,
        test_rows=args.test_rows,
    )

    if args.json:
        print(json.dumps([asdict(row) for row in report], indent=2, sort_keys=True))
        return

    print("MACRO FACTOR OOS EVIDENCE — NO SCORE / NO REGIME")
    for row in report:
        effect = "n/a" if row.median_high_minus_low_return_pct is None else f"{row.median_high_minus_low_return_pct:+.3f}%"
        consistency = "n/a" if row.return_effect_positive_split_pct is None else f"{row.return_effect_positive_split_pct:.1f}%"
        print(
            f"{row.factor:28s} {row.horizon_key:8s} "
            f"splits={row.comparable_split_count}/{row.split_count} "
            f"low_n={row.low_n:4d} high_n={row.high_n:4d} "
            f"median(high-low)={effect} positive_splits={consistency}"
        )


if __name__ == "__main__":
    main()
