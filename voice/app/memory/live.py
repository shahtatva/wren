"""Memory for the live path: the voice socket and POST /turn.

PostgresMemory runs the memory code (sync psycopg, Groq and Gemini clients) in worker
threads, so the event loop keeps audio flowing, and it runs every query for a user as
`authenticated` with that user's id, so RLS applies. NoMemory stands in when the
database isn't ready (no schema yet, no such user, unreachable). Wren still talks but
remembers nothing, and the ready message and the logs say why.
"""

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.config import Settings
from app.conversation import Background, HistoryTurn, Retrieved
from app.memory import store
from app.memory.embed import embed_texts
from app.memory.retrieve import retrieve as run_retrieval
from app.memory.rewrite import rewrite_questions
from app.voice.tuning import Tuning

logger = logging.getLogger(__name__)

# No live query comes near this. A stuck one must not hold the shared connection for long.
LIVE_STATEMENT_TIMEOUT_MS = 3000


class Memory(Protocol):
    status: str  # "on", or "off: <why>"

    async def open_session(
        self, user_id: UUID, mode: str, tz: str, resume_id: str | None, now: datetime
    ) -> tuple[str, Background, list[HistoryTurn]]: ...

    async def retrieve(self, user_id: UUID, text: str) -> Retrieved: ...

    async def append(self, session_id: str, user_id: UUID, turns: list[dict]) -> None: ...

    async def close_session(self, session_id: str, user_id: UUID, now: datetime) -> None: ...


class NoMemory:
    def __init__(self, reason: str):
        self.status = f"off: {reason}"

    async def open_session(self, user_id, mode, tz, resume_id, now):
        return resume_id or str(uuid.uuid4()), Background(), []

    async def retrieve(self, user_id, text):
        return Retrieved([], 0.0, fallback="memory off")

    async def append(self, session_id, user_id, turns):
        return None

    async def close_session(self, session_id, user_id, now):
        return None


def _history(transcript: list[dict]) -> list[HistoryTurn]:
    return [
        HistoryTurn(t["id"], t["speaker"], t["text"], bool(t.get("interrupted")))
        for t in transcript
        if t.get("speaker") in ("user", "wren")
    ]


def _session_uuid(session_id: str | None) -> UUID | None:
    try:
        return UUID(session_id) if session_id else None
    except ValueError:
        return None


class PostgresMemory:
    status = "on"

    def __init__(self, settings: Settings, tuning: Tuning = Tuning()):
        self._settings = settings
        self._tuning = tuning
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()  # one operation at a time on the shared connection

    def _run(self, work):
        """Run work(conn) on the shared connection.

        A pooler or NAT can drop an idle connection, and psycopg only finds out on use. So
        when the work fails that way, reconnect and run it once more. That's safe: every work
        is one transaction (as_user), which a dead connection rolls back, or is idempotent.
        """
        with self._lock:
            for attempt in (1, 2):
                if self._conn is None or self._conn.closed or self._conn.broken:
                    self._conn = store.connect(self._settings.database_url, statement_timeout_ms=LIVE_STATEMENT_TIMEOUT_MS)
                try:
                    return work(self._conn)
                except psycopg.errors.QueryCanceled:
                    raise  # the statement timeout: the connection is fine, and running it again would wait twice
                except psycopg.OperationalError:
                    self._conn.close()
                    if attempt == 2:
                        raise

    def unavailable_reason(self, user_id: UUID) -> str | None:
        def check(conn):
            if not store.schema_ready(conn):
                return "no schema (push supabase/migrations)"
            if not store.user_exists(conn, user_id):
                return "user not in auth.users"
            return None

        return self._run(check)

    async def open_session(self, user_id, mode, tz, resume_id, now):
        def work(conn):
            with store.as_user(conn, user_id):
                resumed = _session_uuid(resume_id)
                prior = store.load_transcript(conn, resumed) if resumed else None
                if prior:
                    session_id, (_, started, transcript) = resumed, prior
                    turns = _history(transcript)
                else:
                    session_id, started, turns = store.start_session(conn, user_id, mode, now), now, []
                profile = store.load_profile(conn, user_id)
                today = now.astimezone(ZoneInfo(profile.timezone if profile else tz)).date()
                pipeline = store.upcoming_pipeline(
                    conn, user_id, today, today + timedelta(days=self._tuning.pipeline_days)
                )
                last = store.last_session_started(conn, user_id, started)
            return str(session_id), Background(profile, tuple(pipeline), last), turns

        return await asyncio.to_thread(self._run, work)

    async def retrieve(self, user_id, text):
        settings = self._settings

        def search(questions, vectors, k):
            def work(conn):
                with store.as_user(conn, user_id):
                    return store.hybrid_search_many(conn, user_id, questions, vectors, k)

            return self._run(work)

        def work():
            result = run_retrieval(
                text,
                lambda message: rewrite_questions(message, settings),
                lambda questions: embed_texts(questions, "query", settings),
                search,
                k=self._tuning.memories_k,
            )
            return Retrieved(result.hits, result.total_ms, result.fallback)

        return await asyncio.to_thread(work)

    async def append(self, session_id, user_id, turns):
        def work(conn):
            with store.as_user(conn, user_id):
                store.append_turns(conn, UUID(session_id), turns)

        await asyncio.to_thread(self._run, work)

    async def close_session(self, session_id, user_id, now):
        def work(conn):
            with store.as_user(conn, user_id):
                store.end_session(conn, UUID(session_id), now)

        await asyncio.to_thread(self._run, work)

    async def age_confirmed(self, user_id: UUID) -> bool:
        def work(conn):
            with store.as_user(conn, user_id):
                return store.age_confirmed(conn, user_id)

        return await asyncio.to_thread(self._run, work)

    async def confirm_age(self, user_id: UUID, at: datetime, tz: str) -> None:
        def work(conn):
            with store.as_user(conn, user_id):
                store.confirm_age(conn, user_id, at, tz)

        await asyncio.to_thread(self._run, work)


class MemoryProvider:
    """Decides, per session, whether memory is on: the schema and the user must exist.

    It also answers the 18+ check, which fails closed: it raises when the database can't
    answer. A confirmation never reverts, so confirmed users are remembered and skip the
    database after that.
    """

    def __init__(self, settings: Settings | None = None, tuning: Tuning = Tuning(), *, postgres=None):
        self._postgres = postgres or PostgresMemory(settings, tuning)
        self._adults: set[UUID] = set()

    async def for_user(self, user_id: UUID) -> Memory:
        try:
            reason = await asyncio.to_thread(self._postgres.unavailable_reason, user_id)
        except Exception as e:
            reason = f"database unreachable ({type(e).__name__})"
        if reason:
            logger.warning("memory off for this session: %s", reason)
            return NoMemory(reason)
        return self._postgres

    async def age_confirmed(self, user_id: UUID) -> bool:
        if user_id in self._adults:
            return True
        if confirmed := await self._postgres.age_confirmed(user_id):
            self._adults.add(user_id)
        return confirmed

    async def confirm_age(self, user_id: UUID, at: datetime, tz: str) -> None:
        await self._postgres.confirm_age(user_id, at, tz)
        self._adults.add(user_id)
