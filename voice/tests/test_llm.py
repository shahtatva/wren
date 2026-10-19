from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.memory import llm

SETTINGS = Settings("gemini-key", "flash-model", "embed-model", "groq-key", "rewrite-model", "postgres://x", "https://x", "secret")


class Reply(BaseModel):
    questions: list[str]


def _fake_client(monkeypatch, text):
    sent = {}

    def generate_content(*, model, contents, config):
        sent.update(model=model, contents=contents, config=config)
        return SimpleNamespace(text=text)

    monkeypatch.setattr(llm, "client", lambda settings: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    return sent


def test_generate_json_asks_the_flash_model_for_the_schema_and_parses_the_reply(monkeypatch):
    sent = _fake_client(monkeypatch, '{"questions": ["When is the screen?"]}')
    result = llm.generate_json("PROMPT", Reply, SETTINGS)
    assert result == Reply(questions=["When is the screen?"])
    assert sent["model"] == "flash-model"
    assert sent["contents"] == "PROMPT"
    assert sent["config"].response_mime_type == "application/json"
    assert sent["config"].response_schema is Reply


def test_generate_json_rejects_a_reply_that_breaks_the_schema(monkeypatch):
    _fake_client(monkeypatch, '{"questions": "not a list"}')
    with pytest.raises(ValidationError):
        llm.generate_json("PROMPT", Reply, SETTINGS)


def test_the_gemini_client_gives_up_instead_of_waiting_forever(monkeypatch):
    made = {}
    monkeypatch.setattr(llm.genai, "Client", lambda **kwargs: made.update(kwargs) or "client")
    llm._client.cache_clear()
    try:
        assert llm._client("key") == "client"
    finally:
        llm._client.cache_clear()
    assert made["api_key"] == "key"
    assert made["http_options"].timeout >= 30_000  # ms, above extraction's 10-20 s calls; the SDK default is none
