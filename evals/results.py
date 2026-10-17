"""evals/results.csv: one row per variant per run, append-only. See the memory-evals skill."""

import csv
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = REPO_ROOT / "evals" / "results.csv"
COLUMNS = (
    "date", "commit", "experiment", "variant", "change",
    "recall_at_5", "tokens_per_turn", "stale_fact_rate", "latency_p50_ms", "latency_p95_ms",
)
# Writing results must not make the next run look like it ran on uncommitted code.
IGNORED_FOR_DIRTY = {"evals/results.csv"}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def git_commit(repo: Path = REPO_ROOT) -> str:
    sha = _git(repo, "rev-parse", "--short", "HEAD").strip()
    changed = [line[3:].strip() for line in _git(repo, "status", "--porcelain").splitlines()]
    dirty = [path for path in changed if path not in IGNORED_FOR_DIRTY]
    return f"{sha}-dirty" if dirty else sha


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_rows(rows: list[dict], path: Path = RESULTS_PATH) -> None:
    for row in rows:
        if set(row) != set(COLUMNS):
            raise ValueError(f"row keys {sorted(row)} don't match the results.csv columns")
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
