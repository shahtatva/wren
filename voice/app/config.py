"""Settings for the voice server and the eval harness, read from voice/.env."""

import os
from dataclasses import dataclass
from pathlib import Path

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
