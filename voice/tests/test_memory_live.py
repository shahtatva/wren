import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest

from app.config import Settings
from app.conversation import Background
from app.memory import store
from app.memory.live import MemoryProvider, NoMemory, PostgresMemory

USER = UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")
NOW = datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc)


def test_no_memory_retrieves_nothing_and_keeps_session_ids_unique():
    memory = NoMemory("no schema")
    assert memory.status == "off: no schema"

    async def run():
        a = await memory.open_session(USER, "checkin", "UTC", None, NOW)
        b = await memory.open_session(USER, "checkin", "UTC", None, NOW)
        resumed = await memory.open_session(USER, "checkin", "UTC", "keep-me", NOW)
        found = await memory.retrieve(USER, "hi")
        await memory.append(a[0], USER, [{"id": "x:t01", "speaker": "user", "text": "hi"}])
        await memory.close_session(a[0], USER, NOW)
        return a, b, resumed, found

    a, b, resumed, found = asyncio.run(run())
    assert a[0] != b[0]
    assert a[1] == Background() and a[2] == []
    assert resumed[0] == "keep-me"
    assert found.hits == [] and found.fallback == "memory off"


class FakePostgres:
    status = "on"

    def __init__(self, reason=None, error=None):
        self.reason, self.error = reason, error

    def unavailable_reason(self, user_id):
        if self.error:
            raise self.error
        return self.reason


def test_connect_memory_reports_missing_schema():
    memory = asyncio.run(MemoryProvider(postgres=FakePostgres(reason="no schema")).for_user(USER))
    assert isinstance(memory, NoMemory) and memory.status == "off: no schema"


def test_unreachable_database_turns_memory_off():
    provider = MemoryProvider(postgres=FakePostgres(error=psycopg.OperationalError("timeout")))
    assert asyncio.run(provider.for_user(USER)).status == "off: database unreachable (OperationalError)"


def test_ready_database_gives_postgres_memory():
    postgres = FakePostgres()
    assert asyncio.run(MemoryProvider(postgres=postgres).for_user(USER)) is postgres


class DeadConn:
    """A connection a pooler or NAT dropped while idle: psycopg finds out only on use."""
    closed = broken = False

    def execute(self, *args, **kwargs):
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    def close(self):
        self.closed = True


class LiveConn:
    closed = broken = False

    def execute(self, *args, **kwargs):
        return SimpleNamespace(fetchone=lambda: [True])

    def close(self):
        self.closed = True


SETTINGS = Settings(
    gemini_api_key="g", gemini_flash_model="f", gemini_embed_model="e", groq_api_key="q",
    groq_rewrite_model="r", database_url="postgresql://x", supabase_url="u", supabase_secret_key="s",
)


def connections(monkeypatch, *conns):
    made = []

    def connect(url, **kwargs):
        made.append(kwargs)
        return conns[len(made) - 1]

    monkeypatch.setattr(store, "connect", connect)
    return made


def test_the_session_after_a_dropped_connection_still_has_memory(monkeypatch):
    connections(monkeypatch, DeadConn(), LiveConn())
    memory = asyncio.run(MemoryProvider(postgres=PostgresMemory(SETTINGS)).for_user(USER))
    assert memory.status == "on"


def test_a_database_that_stays_down_turns_memory_off_after_one_retry(monkeypatch):
    made = connections(monkeypatch, DeadConn(), DeadConn(), LiveConn())
    memory = asyncio.run(MemoryProvider(postgres=PostgresMemory(SETTINGS)).for_user(USER))
    assert memory.status == "off: database unreachable (OperationalError)"
    assert len(made) == 2


def test_live_connections_ask_for_a_statement_timeout(monkeypatch):
    made = connections(monkeypatch, LiveConn())
    PostgresMemory(SETTINGS).unavailable_reason(USER)
    (kwargs,) = made
    assert kwargs.get("statement_timeout_ms", 0) >= 1000  # in ms; test_store_db checks it cancels a slow query


class SlowConn(LiveConn):
    """A healthy connection whose query ran past the statement timeout."""

    def execute(self, *args, **kwargs):
        raise psycopg.errors.QueryCanceled("canceling statement due to statement timeout")


def test_a_query_stopped_by_the_statement_timeout_is_not_rerun(monkeypatch):
    slow = SlowConn()
    made = connections(monkeypatch, slow, LiveConn())
    with pytest.raises(psycopg.errors.QueryCanceled):
        PostgresMemory(SETTINGS).unavailable_reason(USER)
    assert len(made) == 1 and not slow.closed  # the connection is fine; only the query was slow
