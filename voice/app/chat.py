"""Wren's conversation model: Groq, streamed, with fallbacks.

Groq's free tier allows about 8K tokens a minute per model and a turn's context is
about 2K, so a lively session can hit a 429. Groq's limits are per model, so the first
fallback is a second Groq model. Gemini Flash is the last resort: its free tier allows
only 20 requests a day per model (as of 2026-10-16). A model is skipped only if it
fails before its first token. After that, switching would repeat words already spoken,
so a failure propagates and the turn ends there.

Cancelling the consumer (barge-in) closes the Groq stream, which stops generation.
"""

import logging
from collections.abc import AsyncIterator

from google.genai import types
from groq import AsyncGroq

from app.config import Settings, VoiceSettings
from app.memory.llm import client as gemini_client
from app.turn import Chat
from app.voice import clock
from app.voice.metrics import TurnTiming

logger = logging.getLogger(__name__)

GROQ_TIMEOUT_S = 10.0  # a stall guard; warm first tokens arrive in a few hundred ms


def _groq_options(model: str) -> dict:
    """Reasoning settings per model family: reasoning tokens delay the first spoken word."""
    if "gpt-oss" in model:
        return {"reasoning_effort": "low", "include_reasoning": False}
    if "qwen" in model:
        return {"reasoning_effort": "none"}
    return {}


def _gemini_request(messages: list[dict]) -> tuple[str, list[types.Content]]:
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        types.Content(role="user" if m["role"] == "user" else "model", parts=[types.Part(text=m["content"])])
        for m in messages
        if m["role"] != "system"
    ]
    return system, contents


def make_chat(
    settings: Settings, voice: VoiceSettings, *, max_tokens: int = 400, groq=None, gemini=None
) -> Chat:
    groq = groq or AsyncGroq(api_key=settings.groq_api_key, max_retries=0, timeout=GROQ_TIMEOUT_S)
    gemini = gemini or gemini_client(settings)
    groq_models = [voice.groq_chat_model]
    if voice.groq_chat_fallback_model and voice.groq_chat_fallback_model != voice.groq_chat_model:
        groq_models.append(voice.groq_chat_fallback_model)

    async def groq_deltas(model: str, messages: list[dict]) -> AsyncIterator[str]:
        stream = await groq.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            max_completion_tokens=max_tokens,
            **_groq_options(model),
        )
        try:
            async for chunk in stream:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        finally:
            await stream.close()

    async def gemini_deltas(messages: list[dict]) -> AsyncIterator[str]:
        system, contents = _gemini_request(messages)
        stream = await gemini.aio.models.generate_content_stream(
            model=settings.gemini_flash_model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens),
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text

    async def chat(messages: list[dict], timing: TurnTiming) -> AsyncIterator[str]:
        sources = [(m, groq_deltas(m, messages)) for m in groq_models]
        sources.append((settings.gemini_flash_model, gemini_deltas(messages)))
        last_error: Exception | None = None
        for model, source in sources:
            try:
                first = await anext(source)
            except StopAsyncIteration:
                return
            except Exception as e:
                logger.warning("chat model %s failed before its first token: %s: %s", model, type(e).__name__, e)
                timing.chat_fallback = timing.chat_fallback or type(e).__name__
                last_error = e
                continue
            timing.model = model
            timing.llm_first_token = clock.now()
            try:
                yield first
                async for delta in source:
                    yield delta
            finally:
                await source.aclose()
            return
        raise last_error

    return chat
