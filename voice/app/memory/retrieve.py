"""Memory retrieval for one turn. Design: CLAUDE.md "Memory design", Retrieval.

1. A fast model rewrites the latest user message into 2-3 search questions.
2. All questions are embedded in one call, and hybrid_search() runs for each of them in
   one database round trip.
3. The per-question results are merged by adding up each memory's scores.

The recency and importance boost inside hybrid_search() doesn't depend on the question,
so the summed score is one rank fusion over every question's vector and full-text lists,
boosted once.

The stages come in as callables so /turn, the eval harness and tests can each wire their
own (live calls, cached calls, fakes). Every call logs its latency against the budget.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from app.memory.types import SearchHit

logger = logging.getLogger(__name__)

BUDGET_MS = 300
K = 8  # memories that go into the context

Rewrite = Callable[[str], list[str]]
Embed = Callable[[list[str]], list[list[float]]]
Search = Callable[[list[str], list[list[float]], int], list[list[SearchHit]]]


@dataclass(frozen=True)
class Retrieval:
    hits: list[SearchHit]
    questions: list[str]
    fallback: str | None  # why the raw message was searched instead of rewritten questions
    rewrite_ms: float
    embed_ms: float
    search_ms: float
    total_ms: float


def merge_hits(per_question: list[list[SearchHit]], k: int) -> list[SearchHit]:
    first_seen: dict[str, SearchHit] = {}
    totals: dict[str, float] = {}
    for hits in per_question:
        for hit in hits:
            first_seen.setdefault(hit.id, hit)
            totals[hit.id] = totals.get(hit.id, 0.0) + hit.score
    ranked = sorted(totals, key=lambda id: (-totals[id], id))
    return [
        SearchHit(id, first_seen[id].kind, first_seen[id].text, first_seen[id].source_turns, totals[id])
        for id in ranked[:k]
    ]


def retrieve(message: str, rewrite: Rewrite, embed: Embed, search: Search, *, k: int = K) -> Retrieval:
    start = time.perf_counter()
    fallback = None
    try:
        questions = rewrite(message)
        if not questions:
            fallback = "no questions"
    except Exception as e:  # the live path never fails on a rewrite; it searches the raw message
        fallback = type(e).__name__
    if fallback:
        questions = [message]
    rewritten = time.perf_counter()

    vectors = embed(questions)
    embedded = time.perf_counter()

    # Each question fetches more than k, so a memory that several questions rank just
    # below their own top k still gets credit from all of them in the merge.
    hits = merge_hits(search(questions, vectors, 2 * k), k)
    done = time.perf_counter()

    result = Retrieval(
        hits=hits,
        questions=questions,
        fallback=fallback,
        rewrite_ms=(rewritten - start) * 1000,
        embed_ms=(embedded - rewritten) * 1000,
        search_ms=(done - embedded) * 1000,
        total_ms=(done - start) * 1000,
    )
    level = logging.WARNING if result.total_ms > BUDGET_MS or fallback else logging.INFO
    logger.log(
        level,
        "retrieval total_ms=%.0f rewrite_ms=%.0f embed_ms=%.0f search_ms=%.0f budget_ms=%d questions=%d hits=%d fallback=%s",
        result.total_ms, result.rewrite_ms, result.embed_ms, result.search_ms,
        BUDGET_MS, len(questions), len(hits), fallback or "-",
    )
    return result
