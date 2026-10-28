import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest

from app.config import Settings
from app.conversation import Background, HistoryTurn
from app.memory import store
from app.memory.live import MemoryProvider, NoMemory, PostgresMemory, _history

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


def test_saved_turns_come_back_with_their_mock_and_role():
    turns = _history([
        {"id": "s:t01", "speaker": "wren", "text": "Why Sprout?", "mock": 1, "as": "interviewer"},
        {"id": "s:t02", "speaker": "user", "text": "Because.", "mock": "x"},
    ])
    assert [(t.mock, t.in_role) for t in turns] == [(1, True), (None, False)]


def test_saved_turns_from_before_mocks_come_back_as_they_were():
    turns = _history([
        {"id": "s:t01", "speaker": "user", "text": "hi"},
        {"id": "s:t02", "speaker": "wren", "text": "Hey th", "interrupted": True},
    ])
    assert turns == [HistoryTurn("s:t01", "user", "hi"), HistoryTurn("s:t02", "wren", "Hey th", interrupted=True)]


@pytest.mark.parametrize("saved", ["x", True, 1.5, None])
def test_a_saved_mock_that_is_not_a_whole_number_is_ignored(saved):
    (turn,) = _history([{"id": "s:t01", "speaker": "user", "text": "hi", "mock": saved}])
    assert turn.mock is None


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


class AgePostgres(FakePostgres):
    """Answers the age check from `confirmed`, or raises `error` like a database that's down."""

    def __init__(self, confirmed=False, error=None):
        super().__init__()
        self.confirmed, self.age_error, self.confirmations = confirmed, error, []

    async def age_confirmed(self, user_id):
        if self.age_error:
            raise self.age_error
        return self.confirmed

    async def confirm_age(self, user_id, at, tz):
        if self.age_error:
            raise self.age_error
        self.confirmations.append((user_id, at, tz))


def test_a_confirmed_adult_is_remembered_when_the_database_goes_down():
    postgres = AgePostgres(confirmed=True)
    provider = MemoryProvider(postgres=postgres)
    assert asyncio.run(provider.age_confirmed(USER)) is True
    postgres.age_error = psycopg.OperationalError("down")
    assert asyncio.run(provider.age_confirmed(USER)) is True


def test_an_unconfirmed_user_is_asked_about_again():
    postgres = AgePostgres(confirmed=False)
    provider = MemoryProvider(postgres=postgres)
    assert asyncio.run(provider.age_confirmed(USER)) is False
    postgres.confirmed = True
    assert asyncio.run(provider.age_confirmed(USER)) is True


def test_an_age_check_the_database_cant_answer_raises():
    provider = MemoryProvider(postgres=AgePostgres(error=psycopg.OperationalError("down")))
    with pytest.raises(psycopg.OperationalError):
        asyncio.run(provider.age_confirmed(USER))


def test_a_confirmation_is_remembered():
    postgres = AgePostgres(confirmed=False)
    provider = MemoryProvider(postgres=postgres)
    at = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)
    asyncio.run(provider.confirm_age(USER, at, "America/Chicago"))
    assert postgres.confirmations == [(USER, at, "America/Chicago")]
    postgres.age_error = psycopg.OperationalError("down")
    assert asyncio.run(provider.age_confirmed(USER)) is True
