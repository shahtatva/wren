import json

from starlette.testclient import TestClient

from app.main import app
from tests.voice_fakes import ORIGIN, FakeChat, RecordingMemory, make_runtime


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
