"""Metrics for memory evals. Definitions live in the memory-evals skill."""

import math
import os
from dataclasses import dataclass
from functools import lru_cache
from statistics import mean

from evals.cache import CACHE_DIR

# Keep tiktoken's downloaded encoding next to the harness cache.
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(CACHE_DIR / "tiktoken"))

import tiktoken  # noqa: E402


def is_hit(ranked_turns: list[tuple[str, ...]], gold: set[str], k: int = 5) -> bool:
    return any(gold & set(turns) for turns in ranked_turns[:k])


def is_stale(ranked_turns: list[tuple[str, ...]], gold: set[str], stale: set[str], k: int = 5) -> bool:
    """True if a superseded version ranks above the current one, or shows up without it."""
    for turns in ranked_turns[:k]:
        cited = set(turns)
        if gold & cited:
            return False
        if stale & cited:
            return True
    return False


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile for q in (0, 100]."""
    if not values:
        raise ValueError("percentile of no values")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(q / 100 * len(ordered))) - 1]


@lru_cache(maxsize=1)
def _encoding():
    # cl100k_base is a fixed yardstick for comparing rows, not the conversation model's tokenizer.
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoding().encode(text))


@dataclass(frozen=True)
class ProbeOutcome:
    probe_id: str
    hit: bool
    stale: bool | None  # None when the probe has no superseded facts
    tokens: int
    latency_ms: float


def summarize(outcomes: list[ProbeOutcome]) -> dict:
    changed = [o.stale for o in outcomes if o.stale is not None]
    latencies = [o.latency_ms for o in outcomes]
    return {
        "recall_at_5": round(mean(o.hit for o in outcomes), 3),
        "tokens_per_turn": round(mean(o.tokens for o in outcomes), 1),
        "stale_fact_rate": round(mean(changed), 3) if changed else "",
        "latency_p50_ms": round(percentile(latencies, 50), 1),
        "latency_p95_ms": round(percentile(latencies, 95), 1),
    }
