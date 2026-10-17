"""Load the eval set into the eval schema, with one storage strategy (experiment E1).

- chunk512: each session's transcript in chunks of up to 512 tokens, cut between turns.
- turn: one item per turn.
- atomic: what extraction keeps. That's the memories plus the pipeline items, written out
  as text. The app keeps pipeline items in their own table and always puts them in the
  context, and schedule probes have their answers there. An item that replaces an earlier
  one supersedes it, as in the app.

Each (storage, persona) pair gets its own user_id, so all arms share one table and
hybrid_search()'s user filter keeps them apart.
"""

import uuid
from collections.abc import Callable
from datetime import date, datetime
from uuid import UUID

import psycopg

from app.memory.extract import SPEAKER_LABELS, pipeline_ref
from app.memory.store import insert_memories, insert_session, mark_superseded
from app.memory.types import ExtractedMemory, Extraction, PipelineItem, Session, Turn
from evals.evalset import EvalSet, Transcript
from evals.schema import SCHEMA
from evals.score import count_tokens

STORAGE = ("chunk512", "turn", "atomic")
CHUNK_TOKENS = 512
TIMEZONE = "America/New_York"  # the personas are US-based; pipeline dates are local to this zone
NEUTRAL_IMPORTANCE = 3  # chunks and turns carry no importance of their own
PIPELINE_IMPORTANCE = 5  # interview dates, rejections and offers rank with signature stories
_NAMESPACE = uuid.UUID("6f1c2a52-6a3c-4a0e-9a8e-0e5a1d7c3b21")

Extract = Callable[[Session, list[PipelineItem]], Extraction]
Embed = Callable[[list[str]], list[list[float]]]


def to_app_session(transcript: Transcript) -> Session:
    return Session(
        id=transcript.id,
        started_at=datetime.fromisoformat(f"{transcript.date}T18:00:00+00:00"),
        mode=transcript.mode,
        turns=tuple(Turn(t.id, t.speaker, t.text) for t in transcript.turns),
    )


def user_id_for(storage: str, persona_id: str) -> UUID:
    return uuid.uuid5(_NAMESPACE, f"{storage}:{persona_id}")


def _line(turn: Turn) -> str:
    return f"{SPEAKER_LABELS[turn.speaker]}: {turn.text}"


def turn_items(session: Session) -> list[ExtractedMemory]:
    return [ExtractedMemory("turn", _line(t), (), NEUTRAL_IMPORTANCE, (t.id,)) for t in session.turns]


def chunk_items(
    session: Session, max_tokens: int = CHUNK_TOKENS, count: Callable[[str], int] = count_tokens
) -> list[ExtractedMemory]:
    """Pack whole turns into chunks of up to max_tokens. A longer turn becomes a chunk of its own."""
    chunks: list[list[Turn]] = []
    for turn in session.turns:
        candidate = (chunks[-1] if chunks else []) + [turn]
        if chunks and count("\n".join(_line(t) for t in candidate)) <= max_tokens:
            chunks[-1] = candidate
        else:
            chunks.append([turn])
    return [
        ExtractedMemory("chunk", "\n".join(_line(t) for t in turns), (), NEUTRAL_IMPORTANCE, tuple(t.id for t in turns))
        for turns in chunks
    ]


def pipeline_text(item: PipelineItem) -> str:
    text = item.company + (f", {item.role}" if item.role else "") + f": {item.stage}"
    if item.next_date:
        day = date.fromisoformat(item.next_date)
        text += f", next on {day:%A} {day:%B} {day.day}, {day.year}"
        if item.next_time:
            text += f" at {item.next_time}"
    if item.notes:
        text += f". {item.notes.rstrip('.')}"
    return text + "."


def pipeline_memory(item: PipelineItem) -> ExtractedMemory:
    return ExtractedMemory("pipeline", pipeline_text(item), (item.company,), PIPELINE_IMPORTANCE, item.source_turns)


def advance_pipeline(
    current: list[tuple[PipelineItem, UUID]], updates: list[tuple[PipelineItem, UUID]]
) -> tuple[list[tuple[PipelineItem, UUID]], list[tuple[UUID, UUID]]]:
    """The pipeline after one session, and the (old row, new row) pairs to mark superseded.

    References like "P2" index the current list as it was shown to extraction.
    """
    by_ref = {pipeline_ref(i): row_id for i, (_, row_id) in enumerate(current)}
    superseded = [(by_ref[item.replaces], row_id) for item, row_id in updates if item.replaces in by_ref]
    replaced = {old for old, _ in superseded}
    kept = [(item, row_id) for item, row_id in current if row_id not in replaced]
    new = [(PipelineItem(**{**item.__dict__, "replaces": None}), row_id) for item, row_id in updates]
    return kept + new, superseded


def ingest(
    conn: psycopg.Connection, evalset: EvalSet, storage: str, extract: Extract, embed: Embed, schema: str = SCHEMA
) -> int:
    """Load every persona's sessions in date order with one storage strategy. Returns the item count."""
    if storage not in STORAGE:
        raise ValueError(f"unknown storage {storage}")
    count = 0
    for persona_id in sorted(evalset.personas):
        user_id = user_id_for(storage, persona_id)
        pipeline: list[tuple[PipelineItem, UUID]] = []
        transcripts = sorted((t for t in evalset.transcripts.values() if t.persona == persona_id), key=lambda t: (t.date, t.id))
        for transcript in transcripts:
            session = to_app_session(transcript)
            session_db_id = insert_session(conn, user_id, session, schema=schema)
            updates: list[PipelineItem] = []
            if storage == "turn":
                items = turn_items(session)
            elif storage == "chunk512":
                items = chunk_items(session)
            else:
                extraction = extract(session, [item for item, _ in pipeline])
                updates = extraction.pipeline
                items = extraction.memories + [pipeline_memory(item) for item in updates]
            if not items:
                continue
            ids = insert_memories(conn, user_id, session_db_id, items, embed([i.text for i in items]), schema=schema)
            count += len(ids)
            if updates:
                pipeline, superseded = advance_pipeline(pipeline, list(zip(updates, ids[-len(updates):])))
                for old_id, new_id in superseded:
                    mark_superseded(conn, old_id, new_id, schema=schema)
    return count
