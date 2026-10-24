"""Database tests for the live path, run as `authenticated` so RLS applies.

Each test applies the migrations (if the project doesn't have them yet) and creates its
users inside one transaction that is always rolled back, so nothing is left behind.
They talk to the dev project, so they only run with WREN_DB_TESTS=1.
"""

import math
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.memory.store import (
    age_confirmed,
    append_turns,
    confirm_age,
    as_user,
    end_session,
    hybrid_search_many,
    insert_memories,
    last_session_started,
    load_profile,
    load_transcript,
    schema_ready,
    start_session,
    upcoming_pipeline,
    user_exists,
)
from app.memory.types import ExtractedMemory, Profile

pytestmark = pytest.mark.skipif(os.environ.get("WREN_DB_TESTS") != "1", reason="set WREN_DB_TESTS=1 to run database tests")

REPO = Path(__file__).resolve().parents[2]
UTC = timezone.utc
NOW = datetime(2026, 10, 16, 23, 0, tzinfo=UTC)


@pytest.fixture
def db():
    sys.path.insert(0, str(REPO))
    from evals.schema import split_statements

    from app.config import load_settings
    from app.memory.store import connect

    with connect(load_settings().database_url) as conn:
        with conn.transaction(force_rollback=True):
            if not schema_ready(conn):
                for path in sorted((REPO / "supabase" / "migrations").glob("*.sql")):
                    for statement in split_statements(path.read_text(encoding="utf-8")):
                        conn.execute(statement)
            a, b = uuid4(), uuid4()
            for user in (a, b):
                conn.execute(
                    "insert into auth.users (id, aud, role, email) values (%s, 'authenticated', 'authenticated', %s)",
                    (user, f"{user}@example.test"),
                )
            yield conn, a, b


def vector(seed: int) -> list[float]:
    raw = [math.sin(seed * 7.13 + i * 0.37) for i in range(768)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


def test_schema_and_users_are_ready_inside_the_test_transaction(db):
    conn, a, _ = db
    assert schema_ready(conn) and user_exists(conn, a) and not user_exists(conn, uuid4())


def test_session_round_trip_as_user(db):
    conn, a, _ = db
    with as_user(conn, a):
        session_id = start_session(conn, a, "checkin", NOW)
        append_turns(conn, session_id, [{"id": "s:t01", "speaker": "user", "text": "hi"}])
        append_turns(conn, session_id, [{"id": "s:t02", "speaker": "wren", "text": "Hey th", "interrupted": True}])
        end_session(conn, session_id, NOW + timedelta(minutes=5))
        mode, started, transcript = load_transcript(conn, session_id)
    assert mode == "checkin" and started == NOW
    assert [t["id"] for t in transcript] == ["s:t01", "s:t02"] and transcript[1]["interrupted"] is True
    ended = conn.execute("select ended_at from public.sessions where id = %s", (session_id,)).fetchone()[0]
    assert ended == NOW + timedelta(minutes=5)
    assert conn.execute("select current_user").fetchone()[0] != "authenticated"  # as_user doesn't leak its role


def test_appending_the_same_turns_again_keeps_one_copy(db):
    conn, a, _ = db
    first = [{"id": "s:t01", "speaker": "user", "text": "hi"}, {"id": "s:t02", "speaker": "wren", "text": "Hey."}]
    with as_user(conn, a):
        session_id = start_session(conn, a, "checkin", NOW)
        append_turns(conn, session_id, first)
        append_turns(conn, session_id, first)  # a retry after the first write had landed
        append_turns(conn, session_id, [first[1], {"id": "s:t03", "speaker": "user", "text": "Friday."}])
        _, _, transcript = load_transcript(conn, session_id)
    assert [t["id"] for t in transcript] == ["s:t01", "s:t02", "s:t03"]


def test_a_statement_timeout_cancels_a_slow_query():
    from app.config import load_settings
    from app.memory.store import connect

    with connect(load_settings().database_url, statement_timeout_ms=200) as conn:
        with pytest.raises(psycopg.errors.QueryCanceled):
            conn.execute("select pg_sleep(1)")


def test_rls_hides_other_users_sessions(db):
    conn, a, b = db
    with as_user(conn, a):
        session_id = start_session(conn, a, "checkin", NOW)
    with as_user(conn, b):
        assert load_transcript(conn, session_id) is None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with as_user(conn, b):
            start_session(conn, a, "checkin", NOW)


def test_profile_pipeline_and_last_session(db):
    conn, a, _ = db
    conn.execute(
        "insert into public.profiles (user_id, display_name, summary, target_roles, timezone) "
        "values (%s, 'Maya', 'MS in Information Management', '{BDR}', 'America/New_York')",
        (a,),
    )
    insert = (
        "insert into public.pipeline (user_id, company, role, stage, next_date, next_time) "
        "values (%s, %s, %s, %s, %s, %s) returning id"
    )
    sprout = conn.execute(insert, (a, "Sprout Social", "BDR", "recruiter screen", date(2026, 10, 19), time(11, 0))).fetchone()[0]
    conn.execute(insert, (a, "Corvid Labs", None, "panel", date(2026, 10, 30), None))
    old = conn.execute(insert, (a, "Sprout Social", "BDR", "recruiter screen", date(2026, 10, 17), time(14, 0))).fetchone()[0]
    conn.execute("update public.pipeline set superseded_by = %s where id = %s", (sprout, old))
    for started in (datetime(2026, 10, 10, 18, tzinfo=UTC), datetime(2026, 10, 14, 18, tzinfo=UTC)):
        conn.execute("insert into public.sessions (user_id, mode, started_at) values (%s, 'checkin', %s)", (a, started))

    with as_user(conn, a):
        profile = load_profile(conn, a)
        upcoming = upcoming_pipeline(conn, a, date(2026, 10, 16), date(2026, 10, 23))
        last = last_session_started(conn, a, before=NOW)
    assert profile == Profile("Maya", "MS in Information Management", ("BDR",), "America/New_York")
    assert [(e.company, e.next_date, e.next_time) for e in upcoming] == [("Sprout Social", date(2026, 10, 19), time(11, 0))]
    assert last == datetime(2026, 10, 14, 18, tzinfo=UTC)


def test_live_search_matches_owner_search(db):
    conn, a, b = db
    session_a = conn.execute("insert into public.sessions (user_id, mode) values (%s, 'checkin') returning id", (a,)).fetchone()[0]
    session_b = conn.execute("insert into public.sessions (user_id, mode) values (%s, 'checkin') returning id", (b,)).fetchone()[0]
    texts = ["Led the consulting-club churn project", "Sprout screen moved to Monday", "Nervous before panels"]
    memories = [ExtractedMemory("story", t, (), 3, (f"s:t0{i}",)) for i, t in enumerate(texts)]
    insert_memories(conn, a, session_a, memories, [vector(i) for i in range(3)])
    insert_memories(conn, b, session_b, [ExtractedMemory("story", "B's churn story", (), 3, ("b:t01",))], [vector(1)])

    question = (["churn project"], [vector(0)], 5)
    owner = hybrid_search_many(conn, a, *question)
    with as_user(conn, a):
        live = hybrid_search_many(conn, a, *question)
    with as_user(conn, b):
        other = hybrid_search_many(conn, a, *question)  # b asking for a's memories
    assert live[0] and [h.id for h in live[0]] == [h.id for h in owner[0]]
    assert "B's churn story" not in [h.text for h in live[0]]
    assert other == [[]]


def test_age_confirmation_round_trip_as_user(db):
    conn, a, b = db
    first, later = NOW, NOW + timedelta(days=1)
    with as_user(conn, a):
        assert age_confirmed(conn, a) is False  # no profile row yet
        confirm_age(conn, a, first, "America/Chicago")
        assert age_confirmed(conn, a) is True
        assert load_profile(conn, a).timezone == "America/Chicago"  # a new row takes the browser's zone
        confirm_age(conn, a, later, "Asia/Tokyo")
        row = conn.execute("select age_confirmed_at, timezone from public.profiles where user_id = %s", (a,)).fetchone()
        assert row == (first, "America/Chicago")  # the first confirmation and the zone stay
    with as_user(conn, b):
        assert age_confirmed(conn, a) is False  # RLS: another user's row is invisible
        conn.execute("insert into public.profiles (user_id, timezone) values (%s, 'Europe/London')", (b,))
        confirm_age(conn, b, first, "America/Chicago")
        assert age_confirmed(conn, b) is True and load_profile(conn, b).timezone == "Europe/London"
