"""Write a hypothetical memory note for the latest user message (HyDE), to search with instead
of the message itself.

It's a candidate for the live path (experiment E2), so it runs the way rewrite.py does: a
fast Groq model, no retries and a short timeout. The note is embedded as a document, since
it reads like the memories it should match.
"""

from functools import lru_cache
from pathlib import Path

from groq import Groq
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.memory.rewrite import TIMEOUT_S

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "hyde.md"
SCHEMA = {
    "name": "memory_note",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"note": {"type": "string"}},
        "required": ["note"],
        "additionalProperties": False,
    },
}


class HydeFailed(RuntimeError):
    pass


class _Output(BaseModel):
    note: str


@lru_cache(maxsize=4)
def _client(api_key: str) -> Groq:
    return Groq(api_key=api_key, max_retries=0, timeout=TIMEOUT_S)


def prompt_text() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def parse_note(content: str | None) -> str:
    try:
        note = " ".join(_Output.model_validate_json(content or "").note.split())
    except ValidationError as e:
        raise HydeFailed(f"unreadable HyDE output: {e.errors()[0]['msg']}") from e
    if not note:
        raise HydeFailed("the HyDE call returned an empty note")
    return note


def hypothetical_note(message: str, settings: Settings) -> str:
    response = _client(settings.groq_api_key).chat.completions.create(
        model=settings.groq_rewrite_model,
        messages=[{"role": "system", "content": prompt_text()}, {"role": "user", "content": message}],
        response_format={"type": "json_schema", "json_schema": SCHEMA},
        temperature=0,
        max_completion_tokens=300,  # reasoning counts against this, as in rewrite.py
        reasoning_effort="low",
        include_reasoning=False,
    )
    return parse_note(response.choices[0].message.content)
