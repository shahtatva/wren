"""Turn the latest user message into 2-3 search questions with a fast Groq model.

This runs on the live path, before retrieval, so the call never retries and has a short
timeout. When it raises, the caller searches with the raw message instead.
"""

from functools import lru_cache
from pathlib import Path

from groq import Groq
from pydantic import BaseModel, ValidationError

from app.config import Settings

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "rewrite.md"
MAX_QUESTIONS = 3
# A stall guard, not the budget. Warm calls took 0.16-0.64 s from a dev machine (2026-10-16),
# almost all of it Groq queue time and network.
TIMEOUT_S = 1.0
# Strict mode enforces the 2-3 count. With the prompt alone, gpt-oss-20b often wrote one question.
SCHEMA = {
    "name": "search_questions",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "questions": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": MAX_QUESTIONS},
        },
        "required": ["questions"],
        "additionalProperties": False,
    },
}


class RewriteFailed(RuntimeError):
    pass


class _Output(BaseModel):
    questions: list[str]


@lru_cache(maxsize=4)
def _client(api_key: str) -> Groq:
    return Groq(api_key=api_key, max_retries=0, timeout=TIMEOUT_S)


def prompt_text() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def parse_questions(content: str | None) -> list[str]:
    try:
        raw = _Output.model_validate_json(content or "").questions
    except ValidationError as e:
        raise RewriteFailed(f"unreadable rewrite output: {e.errors()[0]['msg']}") from e
    questions: list[str] = []
    seen: set[str] = set()
    for question in raw:
        question = " ".join(question.split())
        if question and question.casefold() not in seen:
            seen.add(question.casefold())
            questions.append(question)
    if not questions:
        raise RewriteFailed("the rewrite returned no questions")
    return questions[:MAX_QUESTIONS]


def rewrite_questions(message: str, settings: Settings) -> list[str]:
    response = _client(settings.groq_api_key).chat.completions.create(
        model=settings.groq_rewrite_model,
        messages=[{"role": "system", "content": prompt_text()}, {"role": "user", "content": message}],
        response_format={"type": "json_schema", "json_schema": SCHEMA},
        temperature=0,
        # Reasoning counts against this cap. Warm calls used 37-74 tokens in all.
        max_completion_tokens=300,
        # gpt-oss can't turn reasoning off. "low" kept it to 7-41 tokens.
        reasoning_effort="low",
        include_reasoning=False,
    )
    return parse_questions(response.choices[0].message.content)
