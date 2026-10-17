from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.memory import hyde

SETTINGS = Settings("gemini-key", "flash-model", "embed-model", "groq-key", "rewrite-model", "postgres://x", "https://x", "secret")


@pytest.fixture(autouse=True)
def fresh_client():
    hyde._client.cache_clear()
    yield
    hyde._client.cache_clear()


def _completion(content):
    message = SimpleNamespace(role="assistant", content=content, reasoning=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(index=0, message=message, finish_reason="stop")])


def test_parse_note_collapses_whitespace():
    assert hyde.parse_note('{"note": "  The user moved   the Sprout\\nscreen to Friday. "}') == "The user moved the Sprout screen to Friday."


def test_parse_note_rejects_empty_or_cut_off_output():
    for content in ('{"note": "   "}', '{"note": "The user', None):
        with pytest.raises(hyde.HydeFailed):
            hyde.parse_note(content)


def test_hypothetical_note_sends_the_prompt_and_message(monkeypatch, tmp_path):
    prompt = tmp_path / "hyde.md"
    prompt.write_text("PROMPT TEXT", encoding="utf-8")
    monkeypatch.setattr(hyde, "PROMPT_FILE", prompt)
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        return _completion('{"note": "The user\'s Sprout phone screen moved to Friday at 11am."}')

    monkeypatch.setattr(hyde, "_client", lambda api_key: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    assert hyde.hypothetical_note("When's my Sprout call?", SETTINGS) == "The user's Sprout phone screen moved to Friday at 11am."
    assert sent["model"] == "rewrite-model"
    assert sent["messages"] == [{"role": "system", "content": "PROMPT TEXT"}, {"role": "user", "content": "When's my Sprout call?"}]


def test_a_failed_call_is_not_retried(monkeypatch):
    requests = []

    def overloaded(request):
        requests.append(request)
        return httpx.Response(503, json={"error": {"message": "over capacity", "type": "server_error"}})

    real_groq = hyde.Groq
    monkeypatch.setattr(hyde, "Groq", lambda **kwargs: real_groq(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(overloaded))))
    with pytest.raises(Exception):
        hyde.hypothetical_note("hi", SETTINGS)
    assert len(requests) == 1
