"""The "What Wren knows" routes, against a fake memory: who may call them, what they return, and what they refuse."""

import logging
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.middleware.cors import CORSMiddleware
from starlette.testclient import TestClient

from app.main import app
from app.memory.live import NoMemory
from app.memory.types import InterviewFields, KnownInterview, KnownMemory, Knows, Profile, Source
from tests.voice_fakes import ORIGIN, USER, FakeAuth, FakeChat, make_runtime

TOKEN = "good-token"
AT = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
MEMORY = KnownMemory(str(uuid4()), "feedback", "The user ends the consulting story on the result.", Source("mock", AT))
INTERVIEW = KnownInterview(
    str(uuid4()), InterviewFields("Sprout Social", "BDR", "recruiter screen", date(2026, 11, 14), time(14, 0), None),
    Source("checkin", AT), ((date(2026, 11, 13), time(14, 0)),),
)
PROFILE = Profile("Maya", "MS in Information Management", ("BDR",), "America/Chicago")
KNOWS = Knows("America/Chicago", PROFILE, (INTERVIEW,), (MEMORY,))
INTERVIEW_BODY = {"company": "Sprout Social", "role": "BDR", "stage": "panel", "next_date": "2026-11-21", "next_time": "09:30", "notes": None}


class KnowsMemory(NoMemory):
    """Memory that's on and holds KNOWS. It records each call, and every call raises `fail` when it's set."""

    def __init__(self, fail: Exception | None = None):
        super().__init__("test")
        self.status, self.fail, self.calls = "on", fail, []

    def _call(self, *call):
        self.calls.append(call)
        if self.fail:
            raise self.fail

    async def knows(self, user_id):
        self._call("knows", user_id)
        return KNOWS

    async def edit_memory(self, user_id, memory_id, text):
        self._call("edit_memory", user_id, memory_id, text)
        return replace(MEMORY, text=text) if str(memory_id) == MEMORY.id else None

    async def delete_memory(self, user_id, memory_id):
        self._call("delete_memory", user_id, memory_id)
        return str(memory_id) == MEMORY.id

    async def edit_interview(self, user_id, interview_id, fields):
        self._call("edit_interview", user_id, interview_id, fields)
        return replace(INTERVIEW, fields=fields) if str(interview_id) == INTERVIEW.id else None

    async def delete_interview(self, user_id, interview_id):
        self._call("delete_interview", user_id, interview_id)
        return str(interview_id) == INTERVIEW.id

    async def edit_profile(self, user_id, changes):
        self._call("edit_profile", user_id, changes)
        return replace(PROFILE, **{k: tuple(v) if k == "target_roles" else v for k, v in changes.items()})


@contextmanager
def serve(memory=None, *, adult=True, host="203.0.113.9"):
    """The voice server, called from `host`. Off loopback only TOKEN signs in. On loopback the dev identity applies."""
    memory = memory if memory is not None else KnowsMemory()
    app.state.runtime = make_runtime(FakeChat([]), memory=memory, auth=FakeAuth({TOKEN: USER}), adult=adult)
    try:
        with TestClient(app, client=(host, 50000)) as http:
            def call(method, path, body=None, token=TOKEN, origin=ORIGIN):
                headers = {"origin": origin} if origin else {}
                if token:
                    headers["authorization"] = f"Bearer {token}"
                return http.request(method, path, json=body, headers=headers)

            yield SimpleNamespace(call=call, memory=memory)
    finally:
        app.state.runtime = None


ROUTES = [
    ("GET", "/knows", None),
    ("PATCH", f"/knows/memories/{MEMORY.id}", {"text": "New."}),
    ("DELETE", f"/knows/memories/{MEMORY.id}", None),
    ("PATCH", f"/knows/pipeline/{INTERVIEW.id}", INTERVIEW_BODY),
    ("DELETE", f"/knows/pipeline/{INTERVIEW.id}", None),
    ("PATCH", "/knows/profile", {"summary": "New."}),
]


def test_get_knows_returns_the_profile_the_interviews_and_the_memories():
    with serve() as server:
        response = server.call("GET", "/knows")
    assert response.status_code == 200
    assert response.json() == {
        "timezone": "America/Chicago",
        "profile": {"display_name": "Maya", "summary": "MS in Information Management", "target_roles": ["BDR"]},
        "pipeline": [{
            "id": INTERVIEW.id, "company": "Sprout Social", "role": "BDR", "stage": "recruiter screen",
            "next_date": "2026-11-14", "next_time": "14:00", "notes": None,
            "source": {"mode": "checkin", "at": "2026-11-10T18:00:00+00:00"},
            "moved_from": [{"next_date": "2026-11-13", "next_time": "14:00"}],
        }],
        "memories": [{
            "id": MEMORY.id, "kind": "feedback", "text": "The user ends the consulting story on the result.",
            "source": {"mode": "mock", "at": "2026-11-10T18:00:00+00:00"},
        }],
    }


@pytest.mark.parametrize("method, path, body", ROUTES)
def test_every_route_refuses_a_caller_who_isnt_signed_in(method, path, body):
    with serve() as server:
        response = server.call(method, path, body, token=None)
    assert response.status_code == 401 and response.json()["code"] == "unauthorized"
    assert server.memory.calls == []


def test_an_account_that_hasnt_confirmed_18_is_refused():
    with serve(adult=False) as server:
        response = server.call("GET", "/knows")
    assert response.status_code == 403 and response.json()["code"] == "age_unconfirmed"


def test_the_dev_identity_works_from_this_machine():
    with serve(host="127.0.0.1") as server:
        response = server.call("GET", "/knows", token=None)
    assert response.status_code == 200 and server.memory.calls == [("knows", USER)]


def test_another_origin_is_refused():
    with serve() as server:
        response = server.call("GET", "/knows", origin="https://elsewhere.example")
    assert response.status_code == 403 and server.memory.calls == []


@pytest.mark.parametrize("method, path, body", ROUTES)
def test_memory_off_is_a_503_that_says_so(method, path, body):
    with serve(NoMemory("no schema")) as server:
        response = server.call(method, path, body)
    assert response.status_code == 503 and response.json()["code"] == "memory_off"


def test_a_database_that_cant_answer_is_a_503():
    with serve(KnowsMemory(fail=psycopg.OperationalError("down"))) as server:
        read = server.call("GET", "/knows")
        write = server.call("PATCH", f"/knows/memories/{MEMORY.id}", {"text": "New."})
    assert (read.status_code, read.json()["code"]) == (503, "unavailable")
    assert (write.status_code, write.json()["code"]) == (503, "not_saved")


def test_editing_a_memory_saves_the_trimmed_text():
    with serve() as server:
        response = server.call("PATCH", f"/knows/memories/{MEMORY.id}", {"text": "  The user opens with the product.  "})
    assert response.status_code == 200 and response.json()["text"] == "The user opens with the product."
    assert server.memory.calls == [("edit_memory", USER, UUID(MEMORY.id), "The user opens with the product.")]


@pytest.mark.parametrize("text", ["", "   ", "x" * 1001])
def test_a_memory_edit_needs_text_and_at_most_1000_characters(text):
    with serve() as server:
        response = server.call("PATCH", f"/knows/memories/{MEMORY.id}", {"text": text})
    assert response.status_code == 422 and server.memory.calls == []


@pytest.mark.parametrize("method, path, body", [
    ("PATCH", "/knows/memories/{id}", {"text": "New."}),
    ("DELETE", "/knows/memories/{id}", None),
    ("PATCH", "/knows/pipeline/{id}", INTERVIEW_BODY),
    ("DELETE", "/knows/pipeline/{id}", None),
])
def test_a_row_that_isnt_there_or_isnt_current_is_a_404(method, path, body):
    with serve() as server:
        response = server.call(method, path.format(id=uuid4()), body)
    assert response.status_code == 404 and response.json()["code"] == "not_found"


@pytest.mark.parametrize("path", [f"/knows/memories/{MEMORY.id}", f"/knows/pipeline/{INTERVIEW.id}"])
def test_a_delete_answers_204(path):
    with serve() as server:
        assert server.call("DELETE", path).status_code == 204


def test_an_interview_edit_trims_and_turns_blanks_into_nothing():
    body = {**INTERVIEW_BODY, "company": " Sprout Social ", "role": "", "notes": "   "}
    with serve() as server:
        response = server.call("PATCH", f"/knows/pipeline/{INTERVIEW.id}", body)
    fields = InterviewFields("Sprout Social", None, "panel", date(2026, 11, 21), time(9, 30), None)
    assert response.status_code == 200 and response.json()["next_time"] == "09:30"
    assert server.memory.calls == [("edit_interview", USER, UUID(INTERVIEW.id), fields)]


@pytest.mark.parametrize("change", [{"company": " "}, {"stage": ""}, {"next_time": "25:00"}])
def test_an_interview_needs_a_company_a_stage_and_a_real_time(change):
    with serve() as server:
        response = server.call("PATCH", f"/knows/pipeline/{INTERVIEW.id}", {**INTERVIEW_BODY, **change})
    assert response.status_code == 422 and server.memory.calls == []


@pytest.mark.parametrize("body, changes", [
    ({"summary": " MS in IM "}, {"summary": "MS in IM"}),
    ({"display_name": ""}, {"display_name": None}),
    ({"target_roles": ["BDR", " Analyst "]}, {"target_roles": ["BDR", "Analyst"]}),
    ({"target_roles": []}, {"target_roles": []}),
])
def test_a_profile_edit_changes_only_what_it_sends(body, changes):
    with serve() as server:
        response = server.call("PATCH", "/knows/profile", body)
    assert response.status_code == 200 and server.memory.calls == [("edit_profile", USER, changes)]


@pytest.mark.parametrize("body", [{}, {"target_roles": [""]}, {"target_roles": ["BDR"] * 11}])
def test_a_profile_edit_must_name_a_field_and_keep_to_the_limits(body):
    with serve() as server:
        response = server.call("PATCH", "/knows/profile", body)
    assert response.status_code == 422 and server.memory.calls == []


def test_cors_lets_the_page_read_change_and_delete():
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert {"GET", "POST", "PATCH", "DELETE"} <= set(cors.kwargs["allow_methods"])


def test_what_the_owner_wrote_never_reaches_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    with serve(KnowsMemory(fail=RuntimeError("boom"))) as server:
        server.call("PATCH", f"/knows/memories/{MEMORY.id}", {"text": "secret sprout story"})
        server.call("PATCH", "/knows/profile", {"summary": "secret summary"})
    assert "secret" not in caplog.text


@pytest.mark.parametrize("method, path, body", [("GET", "/knows", None), ("PATCH", f"/knows/memories/{MEMORY.id}", {"text": "New."})])
def test_what_wren_knows_is_never_kept_by_the_browser(method, path, body):
    with serve() as server:
        response = server.call(method, path, body)
    assert response.status_code == 200 and response.headers.get("cache-control") == "no-store"


@pytest.mark.parametrize("missing", ["role", "next_date", "next_time", "notes"])
def test_an_interview_edit_must_send_every_field_so_none_is_wiped_by_omission(missing):
    body = {key: value for key, value in INTERVIEW_BODY.items() if key != missing}
    with serve() as server:
        response = server.call("PATCH", f"/knows/pipeline/{INTERVIEW.id}", body)
    assert response.status_code == 422 and server.memory.calls == []
