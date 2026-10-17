"""Audition Aura-2 voices for Wren: python -m app.tools.voices [voice ...]

Writes one WAV per voice to voice/logs/voices/, all saying the same lines, so you can
pick DEEPGRAM_TTS_MODEL by ear. Each voice costs about a tenth of a cent of Deepgram credit.
"""

import asyncio
import io
import sys
import wave
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import load_voice_settings
from app.runtime import LOG_DIR

DEFAULT_VOICES = ("aura-2-vesta-en", "aura-2-luna-en", "aura-2-iris-en", "aura-2-cora-en", "aura-2-hyperion-en", "aura-2-thalia-en")
LINES = (
    "Hey Maya. Sprout's on Monday, and I'm a little fluttery for you. "
    "You lost me around the second project. Lead with the result. Want to try it again?"
)


def wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return out.getvalue()


async def speak(client: httpx.AsyncClient, key: str, model: str, text: str, sample_rate: int = 24000) -> bytes:
    params = {"model": model, "encoding": "linear16", "sample_rate": sample_rate, "container": "none"}
    response = await client.post(
        "https://api.deepgram.com/v1/speak?" + urlencode(params), headers={"Authorization": f"Token {key}"}, json={"text": text}
    )
    response.raise_for_status()
    return response.content


async def run(voices: list[str], out_dir: Path) -> None:
    key = load_voice_settings().deepgram_api_key
    out_dir.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=30) as client:
        for voice in voices:
            path = out_dir / f"{voice}.wav"
            path.write_bytes(wav_bytes(await speak(client, key, voice, LINES), 24000))
            print(f"wrote {path}")
    print("Listen, then set DEEPGRAM_TTS_MODEL in voice/.env to the one that sounds like Wren.")


def main(argv: list[str] | None = None) -> int:
    asyncio.run(run(list(argv if argv is not None else sys.argv[1:]) or list(DEFAULT_VOICES), LOG_DIR / "voices"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
