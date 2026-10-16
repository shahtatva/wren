"""Eval harness entry point: python -m evals.run --experiment E1

Placeholder until the eval set and experiments exist. Results will go in
evals/results.csv.
"""

import argparse

EXPERIMENTS = {
    "E1": "What unit should be stored?",
    "E2": "What should be searched with?",
    "E3": "How should results be ranked?",
    "E4": "Does compaction pay off?",
    "E5": "Which embedding model?",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Wren memory experiment.")
    parser.add_argument("--experiment", required=True, choices=sorted(EXPERIMENTS))
    args = parser.parse_args()
    print(f"{args.experiment} ({EXPERIMENTS[args.experiment]}): not implemented yet.")


if __name__ == "__main__":
    main()
