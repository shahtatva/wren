"""M0 echo spike (throwaway): does Wren's own voice leak back into the mic on your setup?

Browsers cancel echo reliably only for audio they can use as a reference, and whether
Chrome counts Web Audio output is unsettled. That decides whether barge-in works on
laptop speakers, so measure it on the real machine:

1. python -m app.tools.echo_spike clip
   Writes web/public/dev/echo-clip.wav: about 40 seconds of Wren talking.
2. With the web dev server running, open http://localhost:3000/dev/echo on laptop speakers,
   with no headphones. Record each of the three playback routes and stay quiet. The page
   saves three recordings (WebM).
3. python -m app.tools.echo_spike count path/to/echo-*.webm
   Transcribes each recording with Nova-3 and prints how many words leaked through. The
   route with about zero is the one the player should use. If none gets there, plan on
   headphones for now.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from app.config import load_voice_settings
from app.tools.voices import speak, wav_bytes

REPO = Path(__file__).resolve().parents[3]
CONTENT_TYPES = {".wav": "audio/wav", ".webm": "audio/webm", ".ogg": "audio/ogg"}
CLIP = REPO / "web" / "public" / "dev" / "echo-clip.wav"
SCRIPT = (
    "Okay, here's the plan for Monday. Your Sprout screen is at eleven, so we have the weekend. "
    "Tonight, run your opener twice and time it. Keep it under a minute. Lead with the fifteen percent, "
    "then say why sales. Tomorrow, pick two stories: the consulting club project and the alumni calls. "
    "Practice ending each one on the result. On Sunday, write three questions for the recruiter. "
    "Ask about the team, how they ramp new reps, and what success looks like at ninety days. "
    "That's it. Short and specific beats long and careful. You've got this."
)


def leaked_words(transcript: str) -> int:
    return len(transcript.split())


async def make_clip() -> None:
    key = load_voice_settings().deepgram_api_key
    async with httpx.AsyncClient(timeout=60) as client:
        pcm = await speak(client, key, load_voice_settings().deepgram_tts_model, SCRIPT)
    CLIP.parent.mkdir(parents=True, exist_ok=True)
    CLIP.write_bytes(wav_bytes(pcm, 24000))
    print(f"wrote {CLIP} ({len(pcm) / 48000:.1f} s)")


async def count(paths: list[Path]) -> None:
    key = load_voice_settings().deepgram_api_key
    async with httpx.AsyncClient(timeout=60) as client:
        for path in paths:
            response = await client.post(
                "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true",
                headers={"Authorization": f"Token {key}", "Content-Type": CONTENT_TYPES.get(path.suffix.lower(), "audio/webm")},
                content=path.read_bytes(),
            )
            response.raise_for_status()
            transcript = response.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
            print(f"{path.name}: {leaked_words(transcript)} words leaked. {transcript[:120]!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("clip", help="write the clip the spike page plays")
    counting = sub.add_parser("count", help="count Wren's words in each recording")
    counting.add_argument("recordings", nargs="+", type=Path)
    args = parser.parse_args(argv)
    asyncio.run(make_clip() if args.command == "clip" else count(args.recordings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
