from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from app import config
from app.voice import clock
from app.voice.tuning import Tuning

NAMES = {
    "DEEPGRAM_API_KEY": "dg-key",
    "DEEPGRAM_STT_MODEL": "nova-3",
    "DEEPGRAM_TTS_MODEL": "aura-2-vesta-en",
    "GROQ_CHAT_MODEL": "qwen/qwen3.8-27b",
    "ALLOWED_ORIGINS": " http://localhost:3000, https://wren.example ,",
}


@pytest.fixture
def voice_env(monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE", Path("does-not-exist.env"))
    for name, value in NAMES.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("WREN_DEV_USER_ID", raising=False)
    monkeypatch.delenv("GROQ_CHAT_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_CHAT_FALLBACK_MODELS", raising=False)
    return monkeypatch


def test_gemini_chat_backups_parse_as_an_ordered_list(voice_env):
    assert config.load_voice_settings().gemini_chat_models == ()
    voice_env.setenv("GEMINI_CHAT_FALLBACK_MODELS", " gemini-3.5-flash-lite, gemini-3.1-flash-lite ,")
    assert config.load_voice_settings().gemini_chat_models == ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite")


def test_voice_settings_parse_origins_and_optional_dev_user(voice_env):
    settings = config.load_voice_settings()
    assert settings.allowed_origins == ("http://localhost:3000", "https://wren.example")
    assert settings.groq_chat_model == "qwen/qwen3.8-27b"
    assert settings.groq_chat_fallback_model is None
    assert settings.dev_user_id is None

    voice_env.setenv("GROQ_CHAT_FALLBACK_MODEL", "openai/gpt-oss-120b")
    assert config.load_voice_settings().groq_chat_fallback_model == "openai/gpt-oss-120b"

    voice_env.setenv("WREN_DEV_USER_ID", "6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")
    assert config.load_voice_settings().dev_user_id == UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")


def test_missing_deepgram_key_names_the_variable(voice_env):
    voice_env.setenv("DEEPGRAM_API_KEY", " ")
    with pytest.raises(config.MissingSetting, match="DEEPGRAM_API_KEY"):
        config.load_voice_settings()


def test_bad_dev_user_id_names_the_variable(voice_env):
    voice_env.setenv("WREN_DEV_USER_ID", "user-zero")
    with pytest.raises(config.MissingSetting, match="WREN_DEV_USER_ID"):
        config.load_voice_settings()


def test_an_empty_dev_user_id_beats_the_env_file(voice_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("WREN_DEV_USER_ID=6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10\n")
    voice_env.setattr(config, "ENV_FILE", env_file)
    voice_env.setenv("WREN_DEV_USER_ID", "")  # what the image's start command does
    assert config.load_voice_settings().dev_user_id is None
    voice_env.delenv("WREN_DEV_USER_ID")  # unset instead: the file brings the dev identity back
    assert config.load_voice_settings().dev_user_id == UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")


def test_hold_is_longer_in_mocks():
    tuning = Tuning()
    assert tuning.hold_s("checkin") == 0.0
    assert tuning.hold_s("mock") == 0.7
    assert tuning.endpointing_ms == 300


def test_iso_formats_a_monotonic_time_as_utc_with_milliseconds():
    stamp = clock.iso(clock.now())
    assert stamp.endswith("Z") and len(stamp) == len("2026-10-16T21:03:04.512Z")
    parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert abs((parsed - datetime.now(timezone.utc)).total_seconds()) < 2
    assert clock.iso(None) is None
