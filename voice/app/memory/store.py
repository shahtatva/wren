"""Postgres access for memories.

Week 1 queries run as the database owner and filter by user_id explicitly.
Per-request RLS (running as `authenticated`) arrives with the /turn endpoint.

Every function takes the schema it works in. The app uses "public"; the eval harness
keeps its own copy of these tables in a separate schema (evals/schema.py).
"""

from datetime import datetime
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.memory.types import ExtractedMemory, SearchHit, Session

# hybrid_search() ranking parameters a caller may set, and their SQL types.
RANKING_PARAMS = {
    "rrf_k": "integer",
    "semantic_weight": "double precision",
    "full_text_weight": "double precision",
    "recency_weight": "double precision",
    "importance_weight": "double precision",
    "recency_half_life_days": "double precision",
}


def connect(database_url: str) -> psycopg.Connection:
    # prepare_threshold=None: Supabase's pooler doesn't support prepared statements in all modes.
    return psycopg.connect(database_url, autocommit=True, prepare_threshold=None)


def to_pgvector(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in vector) + "]"


def insert_session(conn: psycopg.Connection, user_id: UUID, session: Session, *, schema: str = "public") -> UUID:
    transcript = [{"id": t.id, "speaker": t.speaker, "text": t.text} for t in session.turns]
    query = sql.SQL(
        "insert into {} (user_id, external_id, mode, started_at, transcript) values (%s, %s, %s, %s, %s) returning id"
    ).format(sql.Identifier(schema, "sessions"))
    return conn.execute(query, (user_id, session.id, session.mode, session.started_at, Jsonb(transcript))).fetchone()[0]


def insert_memories(
    conn: psycopg.Connection,
    user_id: UUID,
    session_db_id: UUID,
    memories: list[ExtractedMemory],
    embeddings: list[list[float]],
    *,
    schema: str = "public",
) -> list[UUID]:
    if len(memories) != len(embeddings):
        raise ValueError(f"{len(memories)} memories but {len(embeddings)} embeddings")
    query = sql.SQL(
        "insert into {} (user_id, kind, text, entities, importance, source_session, source_turns, embedding) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s::extensions.vector) returning id"
    ).format(sql.Identifier(schema, "memories"))
    ids = []
    with conn.transaction():
        for memory, embedding in zip(memories, embeddings):
            row = conn.execute(
                query,
                (user_id, memory.kind, memory.text, list(memory.entities), memory.importance,
                 session_db_id, list(memory.source_turns), to_pgvector(embedding)),
            ).fetchone()
            ids.append(row[0])
    return ids


def mark_superseded(conn: psycopg.Connection, old_id: UUID, new_id: UUID, *, schema: str = "public") -> None:
    query = sql.SQL("update {} set superseded_by = %s where id = %s").format(sql.Identifier(schema, "memories"))
    conn.execute(query, (new_id, old_id))


def hybrid_search_many(
    conn: psycopg.Connection,
    user_id: UUID,
    questions: list[str],
    embeddings: list[list[float]],
    k: int,
    as_of: datetime | None = None,
    *,
    schema: str = "public",
    ranking: dict[str, float] | None = None,
) -> list[list[SearchHit]]:
    """Run hybrid_search() for each question in one round trip.

    Returns one ranked list per question, in question order. Recency is measured from
    as_of, or from now() when it's None. Evals pin it so reruns match. ranking sets
    hybrid_search() parameters by name (see RANKING_PARAMS); the rest keep their defaults.
    """
    if len(questions) != len(embeddings):
        raise ValueError(f"{len(questions)} questions but {len(embeddings)} embeddings")
    ranking = ranking or {}
    unknown = set(ranking) - set(RANKING_PARAMS)
    if unknown:
        raise ValueError(f"unknown ranking parameters: {sorted(unknown)}")
    named = sql.SQL("").join(
        sql.SQL(", {} => {}::{}").format(sql.Identifier(name), sql.Placeholder(name), sql.SQL(RANKING_PARAMS[name]))
        for name in ranking
    )
    query = sql.SQL(
        """
        select q.n, h.id, h.kind, h.text, h.source_turns, h.score
        from unnest(%(questions)s::text[], %(embeddings)s::text[]) with ordinality as q(question, embedding, n)
        cross join lateral {function}(
          q.embedding::extensions.vector, q.question, %(k)s::integer,
          for_user => %(user_id)s::uuid, as_of => coalesce(%(as_of)s::timestamptz, now()){named}
        ) h
        order by q.n, h.score desc, h.id
        """
    ).format(function=sql.Identifier(schema, "hybrid_search"), named=named)
    params = {
        "questions": questions,
        "embeddings": [to_pgvector(e) for e in embeddings],
        "k": k,
        "user_id": user_id,
        "as_of": as_of,
        **ranking,
    }
    per_question: list[list[SearchHit]] = [[] for _ in questions]
    for n, id, kind, text, source_turns, score in conn.execute(query, params).fetchall():
        per_question[n - 1].append(SearchHit(str(id), kind, text, tuple(source_turns), float(score)))
    return per_question
