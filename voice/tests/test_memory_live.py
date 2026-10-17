import asyncio
from datetime import datetime, timezone
from uuid import UUID

import psycopg

from app.conversation import Background
from app.memory.live import MemoryProvider, NoMemory

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
