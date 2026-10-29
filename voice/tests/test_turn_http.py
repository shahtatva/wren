import json
from uuid import UUID

import psycopg
from starlette.testclient import TestClient

from app.main import app
from tests.voice_fakes import ORIGIN, FakeAuth, FakeChat, RecordingMemory, make_runtime

SIGNED_IN = UUID("0b8d1f4e-9c2a-4f6e-8a1d-3e5f7a9b1c2d")


def lines(response):
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_post_turn_streams_ndjson_through_run_turn():
    memory = RecordingMemory()
    chat = FakeChat([["Hi there. ", "What's up?"]])
    app.state.runtime = make_runtime(chat, memory=memory)
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            response = client.post("/turn", json={"text": "hello"}, headers={"origin": ORIGIN})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        out = lines(response)
        assert out[0]["type"] == "session" and out[0]["session_id"]
        assert [m["text"] for m in out if m["type"] == "segment"] == ["Hi there.", "What's up?"]
        assert out[-1]["type"] == "end" and out[-1]["metrics"]["outcome"] == "complete"
        assert chat.calls[0][-1] == {"role": "user", "content": "hello"}
        assert [t["speaker"] for t in memory.appended] == ["user", "wren"]
    finally:
        app.state.runtime = None


def test_post_turn_continues_a_known_session():
    chat = FakeChat([["One."], ["Two."]])
    app.state.runtime = make_runtime(chat)
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            first = lines(client.post("/turn", json={"text": "first"}, headers={"origin": ORIGIN}))
            session_id = first[0]["session_id"]
            second = lines(client.post("/turn", json={"text": "second", "session_id": session_id}, headers={"origin": ORIGIN}))
        assert second[0]["session_id"] == session_id
        assert [m["content"] for m in chat.calls[1][1:]] == ["first", "One.", "second"]
    finally:
        app.state.runtime = None


def test_post_turn_hands_off_and_keeps_the_mock_across_requests():
    memory = RecordingMemory()
    chat = FakeChat([["[mock: Sprout]"], ["Walk me through your background."], ["Okay. ", "Why Sprout?"]])
    app.state.runtime = make_runtime(chat, memory=memory)
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            first = lines(client.post("/turn", json={"text": "Can we practice for Sprout?"}, headers={"origin": ORIGIN}))
            session_id = first[0]["session_id"]
            client.post("/turn", json={"text": "I studied information management.", "session_id": session_id}, headers={"origin": ORIGIN})
        assert [m["on"] for m in first if m["type"] == "mock"] == [True]
        assert [m.get("role") for m in first if m["type"] == "segment"] == [None, "interviewer", "interviewer"]
        assert "running a mock interview" in chat.calls[2][0]["content"]
        assert [(r["speaker"], r.get("as")) for r in memory.appended] == [
            ("user", None), ("wren", None), ("wren", "interviewer"), ("user", None), ("wren", "interviewer")]
        assert memory.appended[3]["mock"] == 1
    finally:
        app.state.runtime = None


def test_post_turn_without_identity_is_401():
    app.state.runtime = make_runtime(FakeChat([["x"]]))
    try:
        with TestClient(app, client=("192.168.1.20", 50000)) as client:
            assert client.post("/turn", json={"text": "hi"}, headers={"origin": ORIGIN}).status_code == 401
    finally:
        app.state.runtime = None


def test_post_turn_ignores_a_non_bearer_authorization_header():
    app.state.runtime = make_runtime(FakeChat([["Hi."]]))
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            response = client.post("/turn", json={"text": "hi"}, headers={"origin": ORIGIN, "authorization": "Basic abc"})
        assert response.status_code == 200
    finally:
        app.state.runtime = None


def post_signed_in(text="hello", token="good-token", **options):
    memory = RecordingMemory()
    app.state.runtime = make_runtime(
        FakeChat([["Hi there."]]), memory=memory, auth=FakeAuth({"good-token": SIGNED_IN}), dev_user=None, **options,
    )
    try:
        with TestClient(app, client=("203.0.113.9", 50000)) as client:
            headers = {"origin": ORIGIN, "authorization": f"Bearer {token}"}
            return client.post("/turn", json={"text": text}, headers=headers), memory
    finally:
        app.state.runtime = None


def test_post_turn_accepts_a_bearer_token():
    response, memory = post_signed_in()
    assert response.status_code == 200 and lines(response)[-1]["metrics"]["outcome"] == "complete"
    assert memory.opened == [SIGNED_IN]


def test_post_turn_with_an_unknown_token_is_401_with_a_message():
    response, _ = post_signed_in(token="junk")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized" and response.json()["message"]


def test_post_turn_without_age_confirmation_is_403():
    response, memory = post_signed_in(adult=False)
    assert response.status_code == 403
    assert response.json()["code"] == "age_unconfirmed" and response.json()["message"]
    assert memory.opened == []


def test_post_turn_when_the_age_check_cant_answer_is_503():
    response, _ = post_signed_in(adult=psycopg.OperationalError("down"))
    assert response.status_code == 503 and response.json()["code"] == "age_check_failed"
