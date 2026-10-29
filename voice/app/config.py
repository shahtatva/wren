"""Settings for the voice server and the eval harness, read from voice/.env."""

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
EMBED_DIM = 768  # fixed by the memories.embedding column


class MissingSetting(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_flash_model: str
    gemini_embed_model: str
    groq_api_key: str
    groq_rewrite_model: str
    database_url: str
    supabase_url: str
    supabase_secret_key: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingSetting(f"{name} is not set. Add it to voice/.env (see voice/.env.example).")
    return value


def load_settings() -> Settings:
    load_dotenv(ENV_FILE, override=False)
    return Settings(
        gemini_api_key=_require("GEMINI_API_KEY"),
        gemini_flash_model=_require("GEMINI_FLASH_MODEL"),
        gemini_embed_model=_require("GEMINI_EMBED_MODEL"),
        groq_api_key=_require("GROQ_API_KEY"),
        groq_rewrite_model=_require("GROQ_REWRITE_MODEL"),
        database_url=_require("DATABASE_URL"),
        supabase_url=_require("SUPABASE_URL"),
        supabase_secret_key=_require("SUPABASE_SECRET_KEY"),
    )


@dataclass(frozen=True)
class VoiceSettings:
    """Settings only the voice server reads, so the eval harness runs without a Deepgram key."""

    deepgram_api_key: str
    deepgram_stt_model: str
    deepgram_tts_model: str
    groq_chat_model: str
    groq_chat_fallback_model: str | None  # Groq limits are per model, so a second one absorbs 429s
    allowed_origins: tuple[str, ...]
    dev_user_id: UUID | None  # the loopback-only dev identity; a deploy leaves it unset
    # The chat's last resort after the Groq models, tried in order. Empty: GEMINI_FLASH_MODEL.
    gemini_chat_models: tuple[str, ...] = ()
    # The Aura-2 voice for the interviewer in mock interviews. None: Wren's voice.
    deepgram_interviewer_tts_model: str | None = None


def _dev_user_id() -> UUID | None:
    value = os.environ.get("WREN_DEV_USER_ID", "").strip()
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        raise MissingSetting("WREN_DEV_USER_ID must be a user id from auth.users (a UUID).") from None


def allowed_origins() -> tuple[str, ...]:
    """Browser origins that may use the voice server. Read at startup for CORS."""
    load_dotenv(ENV_FILE, override=False)
    return tuple(o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip())


def load_voice_settings() -> VoiceSettings:
    load_dotenv(ENV_FILE, override=False)
    return VoiceSettings(
        deepgram_api_key=_require("DEEPGRAM_API_KEY"),
        deepgram_stt_model=_require("DEEPGRAM_STT_MODEL"),
        deepgram_tts_model=_require("DEEPGRAM_TTS_MODEL"),
        groq_chat_model=_require("GROQ_CHAT_MODEL"),
        groq_chat_fallback_model=os.environ.get("GROQ_CHAT_FALLBACK_MODEL", "").strip() or None,
        allowed_origins=tuple(o.strip() for o in _require("ALLOWED_ORIGINS").split(",") if o.strip()),
        dev_user_id=_dev_user_id(),
        gemini_chat_models=tuple(
            m.strip() for m in os.environ.get("GEMINI_CHAT_FALLBACK_MODELS", "").split(",") if m.strip()
        ),
        deepgram_interviewer_tts_model=os.environ.get("DEEPGRAM_INTERVIEWER_TTS_MODEL", "").strip() or None,
    )
