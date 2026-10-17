from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.memory import rewrite

SETTINGS = Settings("gemini-key", "flash-model", "embed-model", "groq-key", "rewrite-model", "postgres://x", "https://x", "secret")


@pytest.fixture(autouse=True)
def fresh_client():
    rewrite._client.cache_clear()
    yield
    rewrite._client.cache_clear()


def _completion(content: str | None, finish_reason: str = "stop"):
    """The parts of a Groq ChatCompletion that rewrite reads, shaped like the real object."""
    message = SimpleNamespace(role="assistant", content=content, reasoning=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(index=0, message=message, finish_reason=finish_reason)])


def test_parse_cleans_dedupes_and_keeps_at_most_three():
    content = (
        '{"questions": ["  When is the   Sprout screen? ", "when is the sprout screen?", '
        '"How did they feel about Sprout?", "What story fits Sprout?", "Who do they know at Sprout?"]}'
    )
    assert rewrite.parse_questions(content) == [
        "When is the Sprout screen?",
        "How did they feel about Sprout?",
        "What story fits Sprout?",
    ]


def test_parse_rejects_output_without_questions():
    with pytest.raises(rewrite.RewriteFailed):
        rewrite.parse_questions('{"questions": ["", "   "]}')


def test_parse_rejects_cut_off_output():
    with pytest.raises(rewrite.RewriteFailed):
        rewrite.parse_questions('{"questions": ["When is')


def test_rewrite_sends_the_prompt_and_message_and_returns_questions(monkeypatch, tmp_path):
    prompt = tmp_path / "rewrite.md"
    prompt.write_text("PROMPT TEXT", encoding="utf-8")
    monkeypatch.setattr(rewrite, "PROMPT_FILE", prompt)
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        return _completion('{"questions": ["When is their Sprout phone screen?", "How do they feel about Sprout?"]}')

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(rewrite, "_client", lambda api_key: fake)

    questions = rewrite.rewrite_questions("Sprout moved my screen to Friday.", SETTINGS)

    assert questions == ["When is their Sprout phone screen?", "How do they feel about Sprout?"]
    assert sent["model"] == "rewrite-model"
    assert sent["messages"] == [
        {"role": "system", "content": "PROMPT TEXT"},
        {"role": "user", "content": "Sprout moved my screen to Friday."},
    ]


def test_a_failed_call_is_not_retried(monkeypatch):
    requests = []

    def overloaded(request):
        requests.append(request)
        return httpx.Response(503, json={"error": {"message": "over capacity", "type": "server_error"}})

    real_groq = rewrite.Groq
    monkeypatch.setattr(
        rewrite, "Groq", lambda **kwargs: real_groq(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(overloaded)))
    )
    with pytest.raises(Exception):
        rewrite.rewrite_questions("hi", SETTINGS)
    assert len(requests) == 1
