from types import SimpleNamespace

import pytest
from google.genai import types

from app.config import Settings
from app.memory import embed

SETTINGS = Settings("gemini-key", "flash-model", "embed-model", "groq-key", "rewrite-model", "postgres://x", "https://x", "secret")


def _vector(marker: float, dim: int = 768) -> list[float]:
    return [marker] + [0.0] * (dim - 1)


class FakeGemini:
    """Stands in for genai.Client. Returns one embedding per Content, marked with its position."""

    def __init__(self, respond=None):
        self.calls = []
        self.models = SimpleNamespace(embed_content=self._embed_content)
        self._respond = respond

    def _embed_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self._respond:
            return self._respond(contents)
        offset = sum(len(c["contents"]) for c in self.calls[:-1])
        return types.EmbedContentResponse(
            embeddings=[types.ContentEmbedding(values=_vector(offset + i)) for i in range(len(contents))]
        )


@pytest.fixture
def gemini(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(embed, "client", lambda settings: fake)
    return fake


def _sent_texts(call) -> list[str]:
    return [content.parts[0].text for content in call["contents"]]


def test_queries_go_in_as_one_content_each_with_the_search_prefix(gemini):
    embed.embed_texts(["When is the Sprout screen?", "How did it go?"], "query", SETTINGS)
    (call,) = gemini.calls
    assert all(isinstance(content, types.Content) for content in call["contents"])
    assert _sent_texts(call) == [
        "task: search result | query: When is the Sprout screen?",
        "task: search result | query: How did it go?",
    ]
    assert call["model"] == "embed-model"
    assert call["config"].output_dimensionality == 768
    assert call["config"].task_type is None  # gemini-embedding-2 rejects task_type


def test_documents_get_the_untitled_document_prefix(gemini):
    embed.embed_texts(["The user moved the Sprout screen to Friday."], "document", SETTINGS)
    assert _sent_texts(gemini.calls[0]) == ["title: none | text: The user moved the Sprout screen to Friday."]


def test_returns_one_vector_per_text_in_order_across_batches(gemini):
    vectors = embed.embed_texts([f"text {i}" for i in range(250)], "document", SETTINGS)
    assert [len(c["contents"]) for c in gemini.calls] == [100, 100, 50]
    assert [v[0] for v in vectors] == [float(i) for i in range(250)]


def test_fewer_vectors_than_texts_is_an_error(monkeypatch):
    merged = FakeGemini(respond=lambda contents: types.EmbedContentResponse(embeddings=[types.ContentEmbedding(values=_vector(0))]))
    monkeypatch.setattr(embed, "client", lambda settings: merged)
    with pytest.raises(ValueError, match="3 texts"):
        embed.embed_texts(["a", "b", "c"], "query", SETTINGS)


def test_wrong_dimension_is_an_error(monkeypatch):
    short = FakeGemini(respond=lambda contents: types.EmbedContentResponse(
        embeddings=[types.ContentEmbedding(values=_vector(0, dim=767)) for _ in contents]))
    monkeypatch.setattr(embed, "client", lambda settings: short)
    with pytest.raises(ValueError, match="768"):
        embed.embed_texts(["a"], "query", SETTINGS)
