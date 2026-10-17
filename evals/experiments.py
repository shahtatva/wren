"""E1–E3: what to store, what to search with, and how to rank. Metrics: the memory-evals skill.

Each experiment varies one factor from a shared base: atomic storage, the raw probe
question as the query, and vector-only ranking. The base appears in all three experiments
(E1 atomic, E2 raw, E3 vector), so those rows must agree on recall, tokens and stale-fact
rate. If they don't, something in the harness isn't deterministic.

Every probe goes through app.memory.retrieve(), the function /turn calls. Extraction,
query rewriting, HyDE and embeddings are API calls. They're cached on disk by model,
prompt and input, so reruns measure code changes and not sampling noise, and so free-tier
quotas last. Each cache entry keeps the latency of the call that filled it. A probe's
latency is the live database time plus those recorded API latencies.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg

from app.config import EMBED_DIM, Settings
from app.memory import extract as extraction
from app.memory import hyde as hyde_module
from app.memory import rewrite as rewrite_module
from app.memory.embed import embed_texts
from app.memory.retrieve import retrieve
from app.memory.store import connect, hybrid_search_many
from app.memory.types import ExtractedMemory, Extraction, PipelineItem, SearchHit, Session
from evals.cache import cache_key, cached_batch, cached_json
from evals.evalset import EvalSet, Probe, gold_turns, stale_turns
from evals.ingest import STORAGE, TIMEZONE, ingest, user_id_for
from evals.results import git_commit, utc_now
from evals.retry import with_retries
from evals.schema import SCHEMA, reset
from evals.score import ProbeOutcome, count_tokens, is_hit, is_stale, summarize

K = 5
# The day after the last eval session. Pinned, so recency scores are the same on every run.
AS_OF = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
RANKINGS = {
    "vector": {"full_text_weight": 0.0, "recency_weight": 0.0, "importance_weight": 0.0},
    "rrf": {"recency_weight": 0.0, "importance_weight": 0.0},
    "rrf_boost": {},  # hybrid_search()'s defaults, as the app runs it
}
# Groq's free tier allows 30 requests and 8,000 tokens a minute. A rewrite is about 450 tokens.
GROQ_INTERVAL_S = 4.0


@dataclass(frozen=True)
class Config:
    storage: str  # chunk512 | turn | atomic
    query: str  # raw | rewrite | hyde
    ranking: str  # vector | rrf | rrf_boost


BASE = Config(storage="atomic", query="raw", ranking="vector")
EXPERIMENTS = {
    "E1": ("storage", STORAGE),
    "E2": ("query", ("raw", "rewrite", "hyde")),
    "E3": ("ranking", tuple(RANKINGS)),
}


def variants(experiment: str, only: tuple[str, ...] | None = None) -> list[tuple[str, Config]]:
    factor, names = EXPERIMENTS[experiment]
    if only:
        unknown = set(only) - set(names)
        if unknown:
            raise ValueError(f"{experiment} has no variants {sorted(unknown)}; it has {list(names)}")
        names = tuple(name for name in names if name in only)
    return [(name, replace(BASE, **{factor: name})) for name in names]


@dataclass(frozen=True)
class Timed:
    value: Any
    ms: float  # how long the API call that produced value took


@dataclass(frozen=True)
class Services:
    """Every call that leaves the machine except the database. Tests pass fakes."""

    extract: Callable[[Session, list[PipelineItem]], Extraction]
    embed_documents: Callable[[list[str]], list[list[float]]]
    embed_query: Callable[[list[str], str], Timed]  # texts, task ("query" or "document") -> vectors
    rewrite: Callable[[str], Timed]  # question -> 2-3 questions
    hyde: Callable[[str], Timed]  # question -> hypothetical memory note


Search = Callable[[UUID, list[str], list[list[float]], int, dict], list[list[SearchHit]]]


def probe_outcome(evalset: EvalSet, probe: Probe, config: Config, services: Services, search: Search) -> ProbeOutcome:
    api_ms = 0.0
    if config.query == "rewrite":
        timed = services.rewrite(probe.question)
        questions, task, api_ms = list(timed.value), "query", timed.ms
    elif config.query == "hyde":
        # The note reads like the memories it should match, so it's embedded as a document.
        timed = services.hyde(probe.question)
        questions, task, api_ms = [timed.value], "document", timed.ms
    else:
        questions, task = [probe.question], "query"

    def embed(texts: list[str]) -> list[list[float]]:
        nonlocal api_ms
        timed = services.embed_query(texts, task)
        api_ms += timed.ms
        return timed.value

    user_id = user_id_for(config.storage, probe.persona)
    result = retrieve(
        probe.question,
        rewrite=lambda message: questions,
        embed=embed,
        search=lambda qs, vectors, n: search(user_id, qs, vectors, n, RANKINGS[config.ranking]),
        k=K,
    )
    ranked = [hit.source_turns for hit in result.hits]
    gold, stale = gold_turns(evalset, probe), stale_turns(evalset, probe)
    return ProbeOutcome(
        probe.id,
        is_hit(ranked, gold, K),
        is_stale(ranked, gold, stale, K) if stale else None,
        sum(count_tokens(hit.text) for hit in result.hits),
        result.total_ms + api_ms,
    )


def run_experiment(
    experiment: str,
    evalset: EvalSet,
    conn: psycopg.Connection,
    services: Services,
    only: tuple[str, ...] | None = None,
    schema: str = SCHEMA,
) -> dict[str, list[ProbeOutcome]]:
    configs = variants(experiment, only)
    reset(conn, schema)
    for storage in sorted({config.storage for _, config in configs}, key=STORAGE.index):
        items = ingest(conn, evalset, storage, services.extract, services.embed_documents, schema)
        print(f"{storage}: {items} items loaded", flush=True)

    def search(user_id, questions, vectors, k, ranking):
        return hybrid_search_many(conn, user_id, questions, vectors, k, AS_OF, schema=schema, ranking=ranking)

    retrieve_log = logging.getLogger("app.memory.retrieve")
    level = retrieve_log.level
    retrieve_log.setLevel(logging.ERROR)  # one line per probe would bury the results
    try:
        results = {}
        for name, config in configs:
            results[name] = [probe_outcome(evalset, probe, config, services, search) for probe in evalset.probes]
            metrics = summarize(results[name])
            print(f"{name}: " + ", ".join(f"{key}={value}" for key, value in metrics.items()), flush=True)
    finally:
        retrieve_log.setLevel(level)
    return results


def _timed_once(call: Callable[[], Any]) -> dict:
    start = time.perf_counter()
    value = call()
    return {"value": value, "ms": round((time.perf_counter() - start) * 1000, 1)}


class _Pacer:
    """Spaces out uncached calls to one API so a free-tier per-minute limit isn't hit."""

    def __init__(self, interval_s: float):
        self.interval_s, self.last = interval_s, 0.0

    def wait(self) -> None:
        delay = self.last + self.interval_s - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()


def _session_fingerprint(session: Session) -> str:
    return json.dumps([session.mode, session.started_at.isoformat(), [[t.id, t.speaker, t.text] for t in session.turns]])


def _extraction_from_json(raw: dict) -> Extraction:
    memories = [ExtractedMemory(m["kind"], m["text"], tuple(m["entities"]), m["importance"], tuple(m["source_turns"]))
                for m in raw["memories"]]
    pipeline = [PipelineItem(**{**p, "source_turns": tuple(p["source_turns"])}) for p in raw["pipeline"]]
    return Extraction(memories, pipeline)


def live_services(settings: Settings) -> Services:
    groq_pacer = _Pacer(GROQ_INTERVAL_S)

    def paced_groq(call):
        def attempt():
            groq_pacer.wait()
            return _timed_once(call)
        return attempt

    def extract(session: Session, pipeline: list[PipelineItem]) -> Extraction:
        key = cache_key(settings.gemini_flash_model, extraction.prompt_text(), TIMEZONE,
                        _session_fingerprint(session), json.dumps([asdict(p) for p in pipeline]))

        def compute():
            result = with_retries(lambda: extraction.extract(session, pipeline, TIMEZONE, settings))
            return {"memories": [asdict(m) for m in result.memories], "pipeline": [asdict(p) for p in result.pipeline]}

        return _extraction_from_json(cached_json("extract", key, compute))

    def embed_documents(texts: list[str]) -> list[list[float]]:
        keys = [cache_key(settings.gemini_embed_model, str(EMBED_DIM), "document", text) for text in texts]
        return cached_batch(
            "embed", keys, lambda missing: with_retries(lambda: embed_texts([texts[i] for i in missing], "document", settings))
        )

    def embed_query(texts: list[str], task: str) -> Timed:
        key = cache_key(settings.gemini_embed_model, str(EMBED_DIM), task, json.dumps(texts))
        return Timed(**cached_json("query-embed", key, lambda: with_retries(lambda: _timed_once(lambda: embed_texts(texts, task, settings)))))

    def rewrite(question: str) -> Timed:
        key = cache_key(settings.groq_rewrite_model, rewrite_module.prompt_text(), question)
        call = paced_groq(lambda: rewrite_module.rewrite_questions(question, settings))
        return Timed(**cached_json("rewrite", key, lambda: with_retries(call)))

    def hyde(question: str) -> Timed:
        key = cache_key(settings.groq_rewrite_model, hyde_module.prompt_text(), question)
        call = paced_groq(lambda: hyde_module.hypothetical_note(question, settings))
        return Timed(**cached_json("hyde", key, lambda: with_retries(call)))

    return Services(extract, embed_documents, embed_query, rewrite, hyde)


def run(experiment: str, evalset: EvalSet, settings: Settings, change: str, only: tuple[str, ...] | None = None) -> list[dict]:
    commit, started = git_commit(), utc_now()
    with connect(settings.database_url) as conn:
        results = run_experiment(experiment, evalset, conn, live_services(settings), only)
    return [
        {"date": started, "commit": commit, "experiment": experiment, "variant": name, "change": change, **summarize(outcomes)}
        for name, outcomes in results.items()
    ]
