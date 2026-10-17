from pathlib import Path

import pytest

from app import config

NAMES = [
    "GEMINI_API_KEY",
    "GEMINI_FLASH_MODEL",
    "GEMINI_EMBED_MODEL",
    "GROQ_API_KEY",
    "GROQ_REWRITE_MODEL",
    "DATABASE_URL",
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
]


@pytest.fixture
def no_env_file(monkeypatch):
    monkeypatch.setattr(config, "ENV_FILE", Path("does-not-exist.env"))


def test_loads_all_settings(monkeypatch, no_env_file):
    for name in NAMES:
        monkeypatch.setenv(name, f"value-of-{name}")
    settings = config.load_settings()
    assert settings.database_url == "value-of-DATABASE_URL"
    assert settings.gemini_embed_model == "value-of-GEMINI_EMBED_MODEL"
    assert settings.groq_rewrite_model == "value-of-GROQ_REWRITE_MODEL"


def test_missing_setting_names_the_variable(monkeypatch, no_env_file):
    for name in NAMES:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("DATABASE_URL", "  ")
    with pytest.raises(config.MissingSetting, match="DATABASE_URL"):
        config.load_settings()
