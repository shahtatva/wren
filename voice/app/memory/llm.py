"""Gemini calls shared by embeddings, extraction, eval generation and (later) compaction."""

from functools import lru_cache
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import Settings

T = TypeVar("T", bound=BaseModel)

# The SDK's default is to wait forever. Extraction and eval generation take about 10-20 s a call.
GEMINI_TIMEOUT_MS = 60_000


@lru_cache(maxsize=4)
def _client(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))


def client(settings: Settings) -> genai.Client:
    """One client per key, so connections stay warm between turns."""
    return _client(settings.gemini_api_key)


def generate_json(prompt: str, schema: type[T], settings: Settings) -> T:
    """One structured-output call. No temperature: newer Flash models deprecate it,
    and the eval harness caches outputs instead."""
    response = client(settings).models.generate_content(
        model=settings.gemini_flash_model,
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema),
    )
    return schema.model_validate_json(response.text)
