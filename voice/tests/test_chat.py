import asyncio
from types import SimpleNamespace

import pytest

from app.chat import make_chat
from app.config import Settings, VoiceSettings
from app.voice.metrics import TurnTiming

SETTINGS = Settings(
    gemini_api_key="g", gemini_flash_model="gemini-test-flash", gemini_embed_model="e",
    groq_api_key="q", groq_rewrite_model="r", database_url="d", supabase_url="u", supabase_secret_key="s",
)
VOICE = VoiceSettings(
    deepgram_api_key="dg", deepgram_stt_model="nova-3", deepgram_tts_model="aura-2-vesta-en",
    groq_chat_model="qwen/qwen3.8-27b", groq_chat_fallback_model=None,
    allowed_origins=("http://localhost:3000",), dev_user_id=None,
)
VOICE_WITH_BACKUP = VoiceSettings(**{**VOICE.__dict__, "groq_chat_fallback_model": "openai/gpt-oss-120b"})
MESSAGES = [
    {"role": "system", "content": "You are Wren."},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "Hey."},
    {"role": "user", "content": "Sprout moved."},
]


class RateLimited(Exception):
    pass


class FakeGroqStream:
    def __init__(self, deltas, fail_after=None, hang_after=None):
        self.deltas, self.fail_after, self.hang_after, self.closed = deltas, fail_after, hang_after, False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for i, d in enumerate(self.deltas):
            if self.fail_after is not None and i == self.fail_after:
                raise RuntimeError("connection reset")
            if self.hang_after is not None and i == self.hang_after:
                await asyncio.Event().wait()
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=d))])

    async def close(self):
        self.closed = True


class FakeGroq:
    """Streams `stream` for every model, except models listed in `errors`, which raise."""

    def __init__(self, stream=None, error=None, errors=None):
        self.stream, self.error, self.errors, self.calls = stream, error, errors or {}, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        error = self.errors.get(kwargs["model"], self.error)
        if error:
            raise error
        return self.stream


class FakeGemini:
    """Streams `texts` for every model, except models listed in `errors`, which raise."""

    def __init__(self, texts, errors=None):
        self.texts, self.errors, self.calls = texts, errors or {}, []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content_stream=self._stream))

    async def _stream(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["model"] in self.errors:
            raise self.errors[kwargs["model"]]

        async def gen():
            for t in self.texts:
                yield SimpleNamespace(text=t)

        return gen()


async def collect(chat, timing):
    return [d async for d in chat(MESSAGES, timing)]


def test_streams_groq_deltas_and_stamps_first_token():
    stream = FakeGroqStream([None, "Sprout", " moved", " to Friday."])
    groq = FakeGroq(stream)
    timing = TurnTiming(turn=1, mode="checkin", input="voice")
    out = asyncio.run(collect(make_chat(SETTINGS, VOICE, groq=groq, gemini=FakeGemini([])), timing))
    assert out == ["Sprout", " moved", " to Friday."]
    assert timing.llm_first_token is not None and timing.model == "qwen/qwen3.8-27b" and timing.chat_fallback is None
    (call,) = groq.calls
    assert call["model"] == "qwen/qwen3.8-27b" and call["stream"] is True and call["messages"] == MESSAGES
    assert call["max_completion_tokens"] == 400 and call["reasoning_effort"] == "none"
    assert stream.closed


def test_falls_back_to_gemini_before_first_token():
    gemini = FakeGemini(["Okay. ", "Friday it is."])
    timing = TurnTiming(turn=1, mode="checkin", input="voice")
    chat = make_chat(SETTINGS, VOICE, groq=FakeGroq(error=RateLimited("429")), gemini=gemini)
    assert asyncio.run(collect(chat, timing)) == ["Okay. ", "Friday it is."]
    assert timing.chat_fallback == "RateLimited" and timing.model == "gemini-test-flash"
    assert timing.llm_first_token is not None
    (call,) = gemini.calls
    assert call["config"].system_instruction == "You are Wren."
    assert [c.role for c in call["contents"]] == ["user", "model", "user"]
    assert call["contents"][-1].parts[0].text == "Sprout moved."


def test_a_rate_limited_chat_model_falls_back_to_the_backup_groq_model_first():
    groq = FakeGroq(FakeGroqStream(["Backup here."]), errors={"qwen/qwen3.8-27b": RateLimited("429")})
    gemini = FakeGemini(["never"])
    timing = TurnTiming(turn=1, mode="checkin", input="voice")
    out = asyncio.run(collect(make_chat(SETTINGS, VOICE_WITH_BACKUP, groq=groq, gemini=gemini), timing))
    assert out == ["Backup here."]
    assert [c["model"] for c in groq.calls] == ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
    assert groq.calls[1]["reasoning_effort"] == "low"
    assert timing.model == "openai/gpt-oss-120b" and timing.chat_fallback == "RateLimited"
    assert gemini.calls == []


def test_gemini_is_the_last_resort_after_both_groq_models():
    groq = FakeGroq(error=RateLimited("429"))
    timing = TurnTiming(turn=1, mode="checkin", input="voice")
    out = asyncio.run(collect(make_chat(SETTINGS, VOICE_WITH_BACKUP, groq=groq, gemini=FakeGemini(["Okay."])), timing))
    assert out == ["Okay."] and len(groq.calls) == 2 and timing.model == "gemini-test-flash"


def test_gemini_backups_replace_flash_and_are_tried_in_order():
    voice = VoiceSettings(**{**VOICE.__dict__, "gemini_chat_models": ("gemini-lite-a", "gemini-lite-b")})
    gemini = FakeGemini(["Okay."], errors={"gemini-lite-a": RateLimited("429")})
    timing = TurnTiming(1, "checkin", "voice")
    out = asyncio.run(collect(make_chat(SETTINGS, voice, groq=FakeGroq(error=RateLimited("429")), gemini=gemini), timing))
    assert out == ["Okay."] and timing.model == "gemini-lite-b"
    assert [call["model"] for call in gemini.calls] == ["gemini-lite-a", "gemini-lite-b"]


def test_when_every_model_fails_the_last_error_propagates():
    class GeminiDown(Exception):
        pass

    class BrokenGemini(FakeGemini):
        async def _stream(self, **kwargs):
            raise GeminiDown("429 RESOURCE_EXHAUSTED")

    chat = make_chat(SETTINGS, VOICE, groq=FakeGroq(error=RateLimited("429")), gemini=BrokenGemini([]))
    with pytest.raises(GeminiDown):
        asyncio.run(collect(chat, TurnTiming(turn=1, mode="checkin", input="voice")))


def test_error_after_first_token_propagates():
    gemini = FakeGemini(["never"])
    chat = make_chat(SETTINGS, VOICE, groq=FakeGroq(FakeGroqStream(["Hi", " there"], fail_after=1)), gemini=gemini)
    timing = TurnTiming(turn=1, mode="checkin", input="voice")
    received = []

    async def run():
        async for d in chat(MESSAGES, timing):
            received.append(d)

    with pytest.raises(RuntimeError, match="connection reset"):
        asyncio.run(run())
    assert received == ["Hi"] and gemini.calls == []


def test_cancel_closes_the_groq_stream():
    stream = FakeGroqStream(["Hi", " there"], hang_after=1)
    chat = make_chat(SETTINGS, VOICE, groq=FakeGroq(stream), gemini=FakeGemini([]))
    timing = TurnTiming(turn=1, mode="checkin", input="voice")

    async def run():
        got_first = asyncio.Event()

        async def consume():
            async for _ in chat(MESSAGES, timing):
                got_first.set()

        task = asyncio.create_task(consume())
        await got_first.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert stream.closed


def test_gpt_oss_gets_low_reasoning_without_reasoning_text():
    voice = VoiceSettings(**{**VOICE.__dict__, "groq_chat_model": "openai/gpt-oss-120b"})
    groq = FakeGroq(FakeGroqStream(["Hi."]))
    asyncio.run(collect(make_chat(SETTINGS, voice, groq=groq, gemini=FakeGemini([])), TurnTiming(1, "checkin", "text")))
    assert groq.calls[0]["reasoning_effort"] == "low" and groq.calls[0]["include_reasoning"] is False


def test_a_model_without_a_first_token_in_time_falls_back():
    stream = FakeGroqStream(["never"], hang_after=0)
    timing = TurnTiming(turn=1, mode="checkin", input="voice")
    chat = make_chat(SETTINGS, VOICE, groq=FakeGroq(stream), gemini=FakeGemini(["Okay."]), first_token_s=0.05)
    assert asyncio.run(asyncio.wait_for(collect(chat, timing), 2)) == ["Okay."]  # never hang the suite
    assert timing.chat_fallback == "TimeoutError" and timing.model == "gemini-test-flash"
    assert stream.closed
