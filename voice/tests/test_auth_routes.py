"""The sign-in routes, against a fake Supabase Auth with one account."""

import json
import logging
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from starlette.testclient import TestClient

from app.auth import SupabaseAuth
from app.main import app
from tests.supabase_fakes import (
    BAD_JWT, EMAIL, INVALID_CREDENTIALS, KEY, PASSWORD, RATE_LIMITED, SPENT_REFRESH, START, URL, USER, Clock,
    FakeSupabase, client_for, jwt, token_body, user_body,
)
from tests.voice_fakes import ORIGIN, FakeChat, make_runtime

ACCESS, NEXT_ACCESS = jwt(START + 3600, 1), jwt(START + 3600, 2)
REFRESH, NEXT_REFRESH = "refresh-token-1", "refresh-token-2"


class OneAccount:
    """Supabase Auth with one account, EMAIL with PASSWORD. A signed-out token stops working."""

    def __init__(self):
        self.signed_out: set[str] = set()

    def __call__(self, request: httpx.Request):
        path, grant = request.url.path, request.url.params.get("grant_type")
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if path == "/auth/v1/token" and grant == "password":
            body = json.loads(request.content)
            return (200, token_body(ACCESS, REFRESH)) if (body["email"], body["password"]) == (EMAIL, PASSWORD) else (400, INVALID_CREDENTIALS)
        if path == "/auth/v1/token" and grant == "refresh_token":
            return (200, token_body(NEXT_ACCESS, NEXT_REFRESH)) if json.loads(request.content) == {"refresh_token": REFRESH} else (400, SPENT_REFRESH)
        if path == "/auth/v1/user":
            valid = token in (ACCESS, NEXT_ACCESS) and token not in self.signed_out
            return (200, user_body()) if valid else (403, BAD_JWT)
        if path == "/auth/v1/logout":
            self.signed_out.add(token)
            return 204, None
        return 404, {"code": 404, "error_code": "not_found", "msg": "Not found"}


@contextmanager
def serve(*answers, adult=True, transport=None):
    """The voice server with real SupabaseAuth over a fake Supabase. The client isn't on
    loopback, so the dev identity never applies."""
    supabase = FakeSupabase(*answers, route=None if answers else OneAccount())
    clock = Clock()
    client = httpx.AsyncClient(transport=transport) if transport else client_for(supabase)
    runtime = make_runtime(FakeChat([]), auth=SupabaseAuth(URL, KEY, client, clock=clock), adult=adult)
    app.state.runtime = runtime
    try:
        with TestClient(app, client=("203.0.113.9", 50000)) as http:
            def post(path, body=None, token=None, origin=ORIGIN):
                headers = {"origin": origin} if origin else {}
                if token:
                    headers["authorization"] = f"Bearer {token}"
                return http.post(path, json=body or {}, headers=headers)

            yield SimpleNamespace(post=post, supabase=supabase, runtime=runtime, clock=clock)
    finally:
        app.state.runtime = None


def sign_in(server, email=EMAIL, password=PASSWORD):
    return server.post("/auth/signin", {"email": email, "password": password})


@pytest.mark.parametrize("adult", [True, False])
def test_sign_in_returns_tokens_and_whether_the_age_is_confirmed(adult):
    with serve(adult=adult) as server:
        response = sign_in(server)
    assert response.status_code == 200
    assert response.json() == {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 3600, "age_confirmed": adult}


def test_sign_in_leaves_the_age_to_the_hello_when_the_database_cant_answer():
    with serve(adult=psycopg.OperationalError("down")) as server:
        response = sign_in(server)
    assert response.status_code == 200 and response.json()["age_confirmed"] is None


def test_sign_in_with_a_wrong_password_returns_a_plain_error():
    with serve() as server:
        response = sign_in(server, password="not it")
    assert response.status_code == 400
    body = response.json()
    assert set(body) == {"code", "message"} and body["code"] == "wrong_password" and body["message"]


def test_an_unknown_email_gets_the_same_error():
    with serve() as server:
        wrong_password = sign_in(server, password="not it")
        unknown_email = sign_in(server, email="nobody@example.com")
    assert (unknown_email.status_code, unknown_email.json()) == (wrong_password.status_code, wrong_password.json())


def test_refresh_returns_new_tokens():
    with serve() as server:
        response = server.post("/auth/refresh", {"refresh_token": REFRESH})
    assert response.status_code == 200
    assert response.json() == {"access_token": NEXT_ACCESS, "refresh_token": NEXT_REFRESH, "expires_in": 3600}


def test_a_spent_refresh_token_asks_for_a_new_sign_in():
    with serve() as server:
        response = server.post("/auth/refresh", {"refresh_token": "refresh-token-0"})
    assert response.status_code == 401 and response.json()["code"] == "unauthorized"


def test_age_route_records_the_confirmation_with_the_browser_zone():
    with serve(adult=False) as server:
        assert server.post("/auth/age", {"tz": "America/Chicago"}, token=ACCESS).status_code == 204
        assert server.post("/auth/age", {"tz": "Not/AZone"}, token=ACCESS).status_code == 204
        confirmed = server.runtime.memory.confirmed
    assert [(user, tz) for user, _, tz in confirmed] == [(USER, "America/Chicago"), (USER, "UTC")]
    assert all(isinstance(at, datetime) and at.tzinfo is not None for _, at, _ in confirmed)


def test_age_route_without_a_token_is_401():
    with serve() as server:
        assert server.post("/auth/age", {"tz": "UTC"}).status_code == 401
        assert server.post("/auth/age", {"tz": "UTC"}, token="junk").status_code == 401
        assert server.runtime.memory.confirmed == []


def test_signout_ends_the_supabase_session_and_forgets_the_token():
    with serve() as server:
        assert sign_in(server).status_code == 200
        assert server.post("/auth/signout", token=ACCESS).status_code == 204
        after = server.post("/auth/age", {"tz": "UTC"}, token=ACCESS)  # checked with Supabase again, not the cache
        (logout,) = server.supabase.calls("/auth/v1/logout")
    assert logout.url.params["scope"] == "local" and logout.headers["authorization"] == f"Bearer {ACCESS}"
    assert after.status_code == 401


def test_sign_ins_are_throttled_per_email_and_overall():
    with serve() as server:
        assert [sign_in(server, password="not it").status_code for _ in range(5)] == [400] * 5
        sixth = sign_in(server, password="not it")
        assert sixth.status_code == 429 and sixth.json()["code"] == "throttled"
        assert len(server.supabase.calls("/auth/v1/token")) == 5
        for n in range(5):  # five more emails, five tries each: 30 in 5 minutes
            assert [sign_in(server, f"user{n}@example.com").status_code for _ in range(5)] == [400] * 5
        assert sign_in(server, "late@example.com").status_code == 429
        assert len(server.supabase.calls("/auth/v1/token")) == 30
        server.clock.now += 15 * 60  # past every window
        assert sign_in(server).status_code == 200


def test_supabase_rate_limiting_a_sign_in_returns_429():
    with serve((429, RATE_LIMITED)) as server:
        response = sign_in(server)
    assert response.status_code == 429 and response.json()["code"] == "throttled"


def test_sign_in_while_supabase_is_unreachable_is_503():
    def unreachable(request):
        raise httpx.ConnectError("connection refused", request=request)

    with serve(transport=httpx.MockTransport(unreachable)) as server:
        response = sign_in(server)
    assert response.status_code == 503 and response.json()["code"] == "unavailable"


@pytest.mark.parametrize("path", ["/auth/signin", "/auth/refresh", "/auth/age", "/auth/signout"])
def test_a_foreign_origin_is_refused(path):
    body = {"email": EMAIL, "password": PASSWORD, "refresh_token": REFRESH, "tz": "UTC"}
    with serve() as server:
        response = server.post(path, body, token=ACCESS, origin="https://elsewhere.example")
        assert response.status_code == 403 and server.supabase.requests == []


def test_passwords_and_tokens_never_reach_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    with serve() as server:
        sign_in(server)
        sign_in(server, password="a wrong password")
        server.post("/auth/refresh", {"refresh_token": REFRESH})
        server.post("/auth/refresh", {"refresh_token": "refresh-token-0"})
        server.post("/auth/age", {"tz": "UTC"}, token=ACCESS)
        server.post("/auth/age", {"tz": "UTC"}, token="junk-token-value")
        server.post("/auth/signout", token=NEXT_ACCESS)

    def unreachable(request):
        raise httpx.ConnectError("connection refused", request=request)

    with serve(transport=httpx.MockTransport(unreachable)) as server:
        sign_in(server)
    assert caplog.records  # the routes and httpx did log
    for secret in (PASSWORD, "a wrong password", ACCESS, REFRESH, NEXT_ACCESS, NEXT_REFRESH, "junk-token-value"):
        assert secret not in caplog.text
