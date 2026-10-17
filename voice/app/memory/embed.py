"""Gemini embeddings at a fixed 768 dimensions, the size of memories.embedding.

Written for gemini-embedding-2, which differs from older Gemini embedding models:
- It rejects task_type. The task goes in as a text prefix instead.
- It merges a list of plain strings into one embedding, so each text is sent as its own Content.
"""

from typing import Literal

from google.genai import types

from app.config import EMBED_DIM, Settings
from app.memory.llm import client

BATCH_SIZE = 100
PREFIXES = {"document": "title: none | text: ", "query": "task: search result | query: "}


def embed_texts(texts: list[str], task: Literal["document", "query"], settings: Settings) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        response = client(settings).models.embed_content(
            model=settings.gemini_embed_model,
            contents=[types.Content(parts=[types.Part(text=PREFIXES[task] + text)]) for text in batch],
            config=types.EmbedContentConfig(output_dimensionality=EMBED_DIM),
        )
        if len(response.embeddings) != len(batch):
            raise ValueError(f"sent {len(batch)} texts but got {len(response.embeddings)} embeddings back")
        vectors.extend(list(e.values) for e in response.embeddings)
    if any(len(v) != EMBED_DIM for v in vectors):
        raise ValueError(f"{settings.gemini_embed_model} returned vectors that aren't {EMBED_DIM}-dimensional")
    return vectors
