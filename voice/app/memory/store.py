"""Postgres access for memories, sessions, profile and pipeline.

Background jobs and evals run as the database owner and filter by user_id explicitly.
The live path (the voice socket and POST /turn) wraps its queries in as_user(), which
runs them as `authenticated` with the user's id, so RLS applies too.

Every function takes the schema it works in. The app uses "public"; the eval harness
keeps its own copy of these tables in a separate schema (evals/schema.py).
"""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date, datetime, time
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.memory.types import (
    ExtractedMemory, FeedbackNote, InterviewFields, KnownInterview, KnownMemory, Knows, PipelineEntry, Profile, SearchHit,
    Session, Source,
)

# hybrid_search() ranking parameters a caller may set, and their SQL types.
RANKING_PARAMS = {
    "rrf_k": "integer",
    "semantic_weight": "double precision",
    "full_text_weight": "double precision",
    "recency_weight": "double precision",
    "importance_weight": "double precision",
    "recency_half_life_days": "double precision",
}


def connect(database_url: str, *, statement_timeout_ms: int | None = None) -> psycopg.Connection:
    """A connection that fails fast when the network is down and stays alive while idle.

    statement_timeout_ms cancels any query that runs longer, for the whole connection.
    DATABASE_URL is the session pooler, so a session setting lasts as long as the connection.
    """
    # prepare_threshold=None: Supabase's pooler doesn't support prepared statements in all modes.
    # Keepalives stop a NAT or the pooler from silently dropping a connection between sessions.
    conn = psycopg.connect(
        database_url, autocommit=True, prepare_threshold=None,
        connect_timeout=5, keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )
    if statement_timeout_ms is not None:
        try:
            # SET takes no bind parameters; set_config does.
            conn.execute("select set_config('statement_timeout', %s, false)", (str(statement_timeout_ms),))
        except Exception:
            conn.close()
            raise
    return conn


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


@contextmanager
def as_user(conn: psycopg.Connection, user_id: UUID) -> Iterator[psycopg.Connection]:
    """Run the block in a transaction as `authenticated` with this user's id, so RLS applies.

    The role and claims are local to the transaction. Inside an outer transaction (as in
    the database tests) they're reset on the way out, so they never outlive the block.
    """
    claims = json.dumps({"sub": str(user_id), "role": "authenticated"})
    with conn.transaction():
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
        yield conn
        conn.execute("reset role")
        conn.execute("select set_config('request.jwt.claims', '', true)")


def schema_ready(conn: psycopg.Connection, *, schema: str = "public") -> bool:
    tables = [f"{schema}.{name}" for name in ("profiles", "pipeline", "sessions", "memories")]
    query = """
        select bool_and(to_regclass(t) is not null)
          and exists (
            select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = %s and p.proname = 'hybrid_search'
          )
        from unnest(%s::text[]) as t
    """
    return bool(conn.execute(query, (schema, tables)).fetchone()[0])


def user_exists(conn: psycopg.Connection, user_id: UUID) -> bool:
    return conn.execute("select exists (select 1 from auth.users where id = %s)", (user_id,)).fetchone()[0]


def start_session(conn: psycopg.Connection, user_id: UUID, mode: str, started_at: datetime, *, schema: str = "public") -> UUID:
    query = sql.SQL("insert into {} (user_id, mode, started_at) values (%s, %s, %s) returning id").format(
        sql.Identifier(schema, "sessions")
    )
    return conn.execute(query, (user_id, mode, started_at)).fetchone()[0]


def append_turns(conn: psycopg.Connection, session_id: UUID, turns: list[dict], *, schema: str = "public") -> None:
    """Add turns to the end of the transcript, in order. A turn whose id is already there is
    skipped, so a write retried after it had landed can't save a turn twice."""
    query = sql.SQL(
        "update {} set transcript = transcript || coalesce(("
        " select jsonb_agg(t.turn order by t.n)"
        " from jsonb_array_elements(%s::jsonb) with ordinality as t(turn, n)"
        " where not transcript @> jsonb_build_array(jsonb_build_object('id', t.turn -> 'id'))"
        "), '[]'::jsonb) where id = %s"
    ).format(sql.Identifier(schema, "sessions"))
    conn.execute(query, (Jsonb(turns), session_id))


def end_session(conn: psycopg.Connection, session_id: UUID, ended_at: datetime, *, schema: str = "public") -> None:
    query = sql.SQL("update {} set ended_at = %s where id = %s").format(sql.Identifier(schema, "sessions"))
    conn.execute(query, (ended_at, session_id))


def load_transcript(
    conn: psycopg.Connection, session_id: UUID, *, schema: str = "public"
) -> tuple[str, datetime, list[dict]] | None:
    """The session's mode, start time and transcript, or None if it isn't there (or isn't yours)."""
    query = sql.SQL("select mode, started_at, transcript from {} where id = %s").format(sql.Identifier(schema, "sessions"))
    row = conn.execute(query, (session_id,)).fetchone()
    return (row[0], row[1], list(row[2])) if row else None


def previous_session(
    conn: psycopg.Connection, user_id: UUID, before: datetime, *, schema: str = "public"
) -> tuple[datetime, str | None] | None:
    """When the user's latest session before `before` started, and its summary (None until the summary
    job writes one), or None if there's no such session."""
    query = sql.SQL(
        "select started_at, summary from {} where user_id = %s and started_at < %s order by started_at desc limit 1"
    ).format(sql.Identifier(schema, "sessions"))
    row = conn.execute(query, (user_id, before)).fetchone()
    return (row[0], row[1]) if row else None


def recent_feedback(
    conn: psycopg.Connection, user_id: UUID, limit: int, *, schema: str = "public"
) -> list[FeedbackNote]:
    """The user's latest current feedback memories, newest first, for the opener. Each is dated by when its
    session started, or by when it was saved if that session is gone."""
    query = sql.SQL(
        "select m.text, coalesce(s.started_at, m.created_at) as dated_at from {memories} m "
        "left join {sessions} s on s.id = m.source_session "
        "where m.user_id = %s and m.kind = 'feedback' and m.superseded_by is null "
        "order by dated_at desc, m.importance desc, m.id limit %s"
    ).format(memories=sql.Identifier(schema, "memories"), sessions=sql.Identifier(schema, "sessions"))
    return [FeedbackNote(text, at) for text, at in conn.execute(query, (user_id, limit)).fetchall()]


def confirm_age(conn: psycopg.Connection, user_id: UUID, at: datetime, tz: str, *, schema: str = "public") -> None:
    """Record that the user confirmed they're 18 or older.

    A new profile row takes the browser's zone, because the column's default is UTC and the
    live path prefers the profile's zone. An existing row keeps its zone and its first
    confirmation.
    """
    query = sql.SQL(
        "insert into {} as p (user_id, age_confirmed_at, timezone) values (%s, %s, %s) "
        "on conflict (user_id) do update set age_confirmed_at = coalesce(p.age_confirmed_at, excluded.age_confirmed_at)"
    ).format(sql.Identifier(schema, "profiles"))
    conn.execute(query, (user_id, at, tz))


def age_confirmed(conn: psycopg.Connection, user_id: UUID, *, schema: str = "public") -> bool:
    """False when the user hasn't confirmed, or has no profile row yet."""
    query = sql.SQL("select age_confirmed_at is not null from {} where user_id = %s").format(
        sql.Identifier(schema, "profiles")
    )
    row = conn.execute(query, (user_id,)).fetchone()
    return bool(row and row[0])


def load_profile(conn: psycopg.Connection, user_id: UUID, *, schema: str = "public") -> Profile | None:
    query = sql.SQL("select display_name, summary, target_roles, timezone from {} where user_id = %s").format(
        sql.Identifier(schema, "profiles")
    )
    row = conn.execute(query, (user_id,)).fetchone()
    return Profile(row[0], row[1], tuple(row[2]), row[3]) if row else None


def pipeline_between(
    conn: psycopg.Connection, user_id: UUID, start: date, end: date, *, schema: str = "public"
) -> list[PipelineEntry]:
    """Current pipeline rows with a date from start to end, inclusive. Superseded rows are history."""
    query = sql.SQL(
        "select company, role, stage, next_date, next_time, notes from {} "
        "where user_id = %s and superseded_by is null and next_date between %s and %s "
        "order by next_date, next_time nulls last, company"
    ).format(sql.Identifier(schema, "pipeline"))
    return [PipelineEntry(*row) for row in conn.execute(query, (user_id, start, end)).fetchall()]


# The profile fields the owner can change on the "What Wren knows" page. The time zone is set at the age step.
PROFILE_FIELDS = ("display_name", "summary", "target_roles")


def load_knows(conn: psycopg.Connection, user_id: UUID, *, schema: str = "public") -> Knows:
    """Everything the "What Wren knows" page shows: the profile, the current interviews with the dates
    they replaced, and the current memories, newest first. Each item carries its session's mode and start,
    or, when that session is gone, when the item was saved."""
    profile = load_profile(conn, user_id, schema=schema)
    query = sql.SQL(
        "select m.id, m.kind, m.text, s.mode, coalesce(s.started_at, m.created_at) as dated_at from {memories} m "
        "left join {sessions} s on s.id = m.source_session "
        "where m.user_id = %s and m.superseded_by is null "
        "order by dated_at desc, m.created_at desc, m.id"
    ).format(memories=sql.Identifier(schema, "memories"), sessions=sql.Identifier(schema, "sessions"))
    memories = tuple(
        KnownMemory(str(id), kind, text, Source(mode, at))
        for id, kind, text, mode, at in conn.execute(query, (user_id,)).fetchall()
    )
    timezone = profile.timezone if profile else "UTC"
    return Knows(timezone, profile, tuple(_interviews(conn, user_id, schema)), memories)


def _interviews(conn: psycopg.Connection, user_id: UUID, schema: str, only: UUID | None = None) -> list[KnownInterview]:
    """Current pipeline rows (only that one, when `only` is given), each with its source and the date and
    time of every version it replaced, newest first."""
    pipeline, sessions = sql.Identifier(schema, "pipeline"), sql.Identifier(schema, "sessions")
    params = {"user_id": user_id, "only": only}
    current = sql.SQL(
        "select p.id, p.company, p.role, p.stage, p.next_date, p.next_time, p.notes, s.mode, "
        "coalesce(s.started_at, p.created_at) from {pipeline} p left join {sessions} s on s.id = p.source_session "
        "where p.user_id = %(user_id)s and p.superseded_by is null and (%(only)s::uuid is null or p.id = %(only)s::uuid) "
        "order by p.next_date, p.next_time, p.company, p.id"
    ).format(pipeline=pipeline, sessions=sessions)
    older = sql.SQL(
        "with recursive chain as ("
        " select id as head, id, next_date, next_time, 0 as depth from {pipeline}"
        " where user_id = %(user_id)s and superseded_by is null and (%(only)s::uuid is null or id = %(only)s::uuid)"
        " union all"
        " select c.head, p.id, p.next_date, p.next_time, c.depth + 1 from {pipeline} p"
        " join chain c on p.superseded_by = c.id where p.user_id = %(user_id)s"
        ") select head, next_date, next_time from chain where depth > 0 order by head, depth, id"
    ).format(pipeline=pipeline)
    history: dict[UUID, list[tuple[date | None, time | None]]] = {}
    for head, next_date, next_time in conn.execute(older, params).fetchall():
        history.setdefault(head, []).append((next_date, next_time))
    return [
        KnownInterview(
            str(id), InterviewFields(company, role, stage, next_date, next_time, notes), Source(mode, at),
            tuple(history.get(id, ())),
        )
        for id, company, role, stage, next_date, next_time, notes, mode, at in conn.execute(current, params).fetchall()
    ]


def update_memory_text(
    conn: psycopg.Connection, user_id: UUID, memory_id: UUID, text: str, embedding: list[float], *, schema: str = "public"
) -> KnownMemory | None:
    """Give a current memory the owner's text and its embedding. Kind, entities and importance stay, and
    tsv follows the text. None when the user has no such current memory."""
    query = sql.SQL(
        "with updated as ("
        " update {memories} set text = %s, embedding = %s::extensions.vector"
        " where id = %s and user_id = %s and superseded_by is null"
        " returning id, kind, text, source_session, created_at"
        ") select u.id, u.kind, u.text, s.mode, coalesce(s.started_at, u.created_at) from updated u"
        " left join {sessions} s on s.id = u.source_session"
    ).format(memories=sql.Identifier(schema, "memories"), sessions=sql.Identifier(schema, "sessions"))
    row = conn.execute(query, (text, to_pgvector(embedding), memory_id, user_id)).fetchone()
    return KnownMemory(str(row[0]), row[1], row[2], Source(row[3], row[4])) if row else None


def _delete_current(conn: psycopg.Connection, table: str, user_id: UUID, row_id: UUID, schema: str) -> bool:
    """Delete a current row and every older version that leads to it through superseded_by, in one
    statement. superseded_by is `on delete set null`, so deleting the newest row alone would make the
    one it replaced current again."""
    query = sql.SQL(
        "with recursive chain as ("
        " select id from {table} where id = %(id)s and user_id = %(user_id)s and superseded_by is null"
        " union all"
        " select t.id from {table} t join chain c on t.superseded_by = c.id where t.user_id = %(user_id)s"
        ") delete from {table} where id in (select id from chain)"
    ).format(table=sql.Identifier(schema, table))
    return conn.execute(query, {"id": row_id, "user_id": user_id}).rowcount > 0


def delete_memory(conn: psycopg.Connection, user_id: UUID, memory_id: UUID, *, schema: str = "public") -> bool:
    """False when the user has no such current memory."""
    return _delete_current(conn, "memories", user_id, memory_id, schema)


def delete_interview(conn: psycopg.Connection, user_id: UUID, interview_id: UUID, *, schema: str = "public") -> bool:
    """False when the user has no such current interview."""
    return _delete_current(conn, "pipeline", user_id, interview_id, schema)


def update_interview(
    conn: psycopg.Connection, user_id: UUID, interview_id: UUID, fields: InterviewFields, *, schema: str = "public"
) -> KnownInterview | None:
    """Correct a current pipeline row in place. A correction isn't a reschedule, so it makes no new
    version. None when the user has no such current interview."""
    query = sql.SQL(
        "update {} set company = %s, role = %s, stage = %s, next_date = %s, next_time = %s, notes = %s "
        "where id = %s and user_id = %s and superseded_by is null returning id"
    ).format(sql.Identifier(schema, "pipeline"))
    values = (fields.company, fields.role, fields.stage, fields.next_date, fields.next_time, fields.notes)
    row = conn.execute(query, (*values, interview_id, user_id)).fetchone()
    if row is None:
        return None
    (interview,) = _interviews(conn, user_id, schema, only=row[0])
    return interview


def update_profile(conn: psycopg.Connection, user_id: UUID, changes: Mapping[str, object], *, schema: str = "public") -> Profile:
    """Set the named profile fields (PROFILE_FIELDS; other keys are ignored). With no profile row yet, this
    makes one with the table's defaults, time zone UTC included."""
    table = sql.Identifier(schema, "profiles")
    columns = [name for name in PROFILE_FIELDS if name in changes]
    if columns:
        query = sql.SQL(
            "insert into {table} (user_id, {columns}) values (%s, {values}) on conflict (user_id) do update set {sets}"
        ).format(
            table=table,
            columns=sql.SQL(", ").join(map(sql.Identifier, columns)),
            values=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
            sets=sql.SQL(", ").join(sql.SQL("{c} = excluded.{c}").format(c=sql.Identifier(c)) for c in columns),
        )
        conn.execute(query, (user_id, *(changes[c] for c in columns)))
    else:
        conn.execute(sql.SQL("insert into {} (user_id) values (%s) on conflict (user_id) do nothing").format(table), (user_id,))
    return load_profile(conn, user_id, schema=schema)
