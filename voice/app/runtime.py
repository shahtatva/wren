"""Everything the routes need, built once per process: settings, model clients, adapters.

Built lazily on the first request, so importing the app (as the health check and tests
do) needs no keys. Tests put their own Runtime on app.state.runtime.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.auth import SupabaseAuth
from app.chat import make_chat
from app.config import VoiceSettings, load_settings, load_voice_settings
from app.conversation import ConversationRegistry
from app.memory.live import Memory, MemoryProvider
from app.turn import Chat
from app.voice.metrics import TurnLog
from app.voice.stt import Stt, connect_nova
from app.voice.tts import AuraTts, Tts
from app.voice.tuning import Tuning

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


class Memories(Protocol):
    async def for_user(self, user_id: UUID) -> Memory: ...


@dataclass
class Runtime:
    voice: VoiceSettings
    tuning: Tuning
    chat: Chat
    registry: ConversationRegistry
    memory: Memories
    connect_stt: Callable[[], Awaitable[Stt]] | None  # None: no voice input, text only
    tts: Tts | None  # None: replies arrive as text only
    turn_log: TurnLog | None = None
    auth: SupabaseAuth | None = None  # None: no sign-in, only the loopback dev identity


def build_runtime() -> Runtime:
    settings, voice, tuning = load_settings(), load_voice_settings(), Tuning()

    async def connect_stt() -> Stt:
        return await connect_nova(voice.deepgram_api_key, voice.deepgram_stt_model, tuning)

    return Runtime(
        voice=voice,
        tuning=tuning,
        chat=make_chat(settings, voice, max_tokens=tuning.max_reply_tokens),
        registry=ConversationRegistry(),
        memory=MemoryProvider(settings, tuning),
        connect_stt=connect_stt,
        tts=AuraTts(voice.deepgram_api_key, voice.deepgram_tts_model),
        turn_log=TurnLog(LOG_DIR / "turns.jsonl"),
        auth=SupabaseAuth(settings.supabase_url, settings.supabase_secret_key),
    )


def get_runtime(app) -> Runtime:
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        runtime = app.state.runtime = build_runtime()
    return runtime
