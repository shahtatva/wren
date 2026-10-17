"""Time-to-first-audio report: python -m app.tools.ttfa_report [path]

Reads the per-turn records the voice server appends to voice/logs/turns.jsonl and prints
p50/p95 by mode, with the median of each stage, so a slow number says where the time
went. Only voice turns count: typed turns and the opening line have no end of speech.
"""

import argparse
import json
import math
import sys
from pathlib import Path

from app.runtime import LOG_DIR

STAGES = ("endpoint_ms", "retrieval_ms", "llm_ttft_ms", "tts_ttfb_ms")


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile, so every reported number is a turn that really happened."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def load_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def summarize(records: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for record in records:
        if record.get("ttfa_ms") is not None:
            groups.setdefault(f"{record.get('mode')} {record.get('input')}", []).append(record)
    rows = []
    for group, turns in sorted(groups.items()):
        ttfa = [t["ttfa_ms"] for t in turns]
        row = {"group": group, "turns": len(turns), "ttfa_p50": percentile(ttfa, 50), "ttfa_p95": percentile(ttfa, 95)}
        for stage in STAGES:
            row[stage.removesuffix("_ms") + "_p50"] = percentile([t[stage] for t in turns if t.get(stage) is not None], 50)
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report time to first audio from the voice server's turn log.")
    parser.add_argument("path", nargs="?", type=Path, default=LOG_DIR / "turns.jsonl")
    args = parser.parse_args(argv)
    rows = summarize(load_records(args.path))
    if not rows:
        print(f"No voice turns in {args.path} yet.")
        return 0
    print(f"{'group':<16}{'turns':>6}{'ttfa p50':>10}{'ttfa p95':>10}{'endpoint':>10}{'retrieval':>11}{'llm ttft':>10}{'tts ttfb':>10}")
    for r in rows:
        cells = [r["ttfa_p50"], r["ttfa_p95"], r["endpoint_p50"], r["retrieval_p50"], r["llm_ttft_p50"], r["tts_ttfb_p50"]]
        print(f"{r['group']:<16}{r['turns']:>6}" + "".join(f"{'-' if c is None else c:>10}" for c in cells))
    print("Targets (docs/spec.md): ttfa p50 <= 1000 ms, p95 <= 1500 ms. Stage columns are medians.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
