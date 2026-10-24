"""Wren's conversation model: Groq, streamed, with fallbacks.

Groq's free tier allows about 8K tokens a minute per model and a turn's context is
about 2K, so a lively session can hit a 429. Groq's limits are per model, so the first
fallback is a second Groq model. Gemini is the last resort: the models in
GEMINI_CHAT_FALLBACK_MODELS, in order, or GEMINI_FLASH_MODEL if that's empty. Flash Lite
allows 15 requests a minute and 500 a day, against Flash's 5 and 20, which extraction
needs (free tier, 2026-10-19). A model is skipped only if it fails before its first
token, or sends none within CHAT_FIRST_TOKEN_S. After that, switching would repeat
words already spoken, so a failure propagates and the turn ends there.

Cancelling the consumer (barge-in) closes the Groq stream, which stops generation.
"""

import asyncio
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
CHAT_FIRST_TOKEN_S = 4.0  # a model with no first token by then is skipped for the next one


def _groq_options(model: str) -> dict:
    """Reasoning settings per model family: reasoning tokens delay the first spoken word.

    gpt-oss gets medium, not low. In 2026-10-23 replays, gpt-oss-120b at low invented a
    story the user never told (4 of 4), garbled 988 in crisis replies (3 of 4) and glued a
    second answer onto the first (3 of 8). At medium: 0 of 3, 0 of 3 and 0 of 3.
    """
    if "gpt-oss" in model:
        return {"reasoning_effort": "medium", "include_reasoning": False}
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
    settings: Settings, voice: VoiceSettings, *, max_tokens: int = 400, groq=None, gemini=None,
    first_token_s: float = CHAT_FIRST_TOKEN_S,
) -> Chat:
    groq = groq or AsyncGroq(api_key=settings.groq_api_key, max_retries=0, timeout=GROQ_TIMEOUT_S)
    gemini = gemini or gemini_client(settings)
    groq_models = [voice.groq_chat_model]
    if voice.groq_chat_fallback_model and voice.groq_chat_fallback_model != voice.groq_chat_model:
        groq_models.append(voice.groq_chat_fallback_model)
    gemini_models = voice.gemini_chat_models or (settings.gemini_flash_model,)

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

    async def gemini_deltas(model: str, messages: list[dict]) -> AsyncIterator[str]:
        system, contents = _gemini_request(messages)
        stream = await gemini.aio.models.generate_content_stream(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens),
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text

    async def chat(messages: list[dict], timing: TurnTiming) -> AsyncIterator[str]:
        sources = [(m, groq_deltas(m, messages)) for m in groq_models]
        sources += [(m, gemini_deltas(m, messages)) for m in gemini_models]
        last_error: Exception | None = None
        for model, source in sources:
            try:
                first = await asyncio.wait_for(anext(source), first_token_s)
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
