"""Eval harness entry point: python -m evals.run --experiment E1 --change "what changed and why"

Each run rebuilds the eval schema, loads the frozen eval set with the storage the
experiment needs, runs every probe through every variant, and appends one row per variant
to evals/results.csv. Rows are appended only after every variant finishes, so a run that
fails partway (a free-tier rate limit, say) logs nothing. See the memory-evals skill, and
evals/experiments.py for what each variant holds fixed.
"""

import argparse
import sys
from functools import partial

from app.config import load_settings
from evals import experiments
from evals.evalset import load_eval_set, validate
from evals.results import append_rows

EXPERIMENTS = {
    "E1": "What unit should be stored?",
    "E2": "What should be searched with?",
    "E3": "How should results be ranked?",
    "E4": "Does compaction pay off?",
    "E5": "Which embedding model?",
}
RUNNERS = {name: partial(experiments.run, name) for name in experiments.EXPERIMENTS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Wren memory experiment.")
    parser.add_argument("--experiment", required=True, choices=sorted(EXPERIMENTS))
    parser.add_argument("--change", required=True, help="One line: what changed and why. Logged in results.csv.")
    parser.add_argument("--variant", action="append", help="Run only this variant. Repeat for several.")
    args = parser.parse_args(argv)

    runner = RUNNERS.get(args.experiment)
    if runner is None:
        print(f"{args.experiment} ({EXPERIMENTS[args.experiment]}): not implemented yet. Nothing was logged.")
        return 2

    evalset = load_eval_set()
    errors = validate(evalset)
    if errors:
        print("The eval set is invalid, so nothing was run:\n" + "\n".join(errors))
        return 1

    if args.variant:
        runner = partial(runner, only=tuple(args.variant))
    rows = runner(evalset, load_settings(), args.change)
    append_rows(rows)
    for row in rows:
        print(", ".join(f"{key}={value}" for key, value in row.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
