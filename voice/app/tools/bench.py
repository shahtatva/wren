"""Live time-to-first-audio bench. Start the voice server first (see CLAUDE.md), then:

    python -m app.tools.bench [--url ws://127.0.0.1:8000/ws] [--barge-in] [--mode checkin]

It behaves like a browser. One 20 ms mic frame goes up every 20 ms, with faint room
noise between prompts. Wren's audio is "played" on a simulated clock, with playback
events sent back.
- Prompts are synthesized once with Aura-2 at 16 kHz and cached in voice/logs/bench-audio/.
- Each turn's metrics, as the server measured them, are printed with p50/p95 and saved
  to voice/logs/bench-<time>.json.
- With --barge-in it also interrupts a long reply and reports how long the server took
  to confirm the interruption.
- Uses a little Deepgram credit (about a cent a run) and Groq's free tier.
"""

import argparse
import array
import asyncio
import hashlib
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect

from app.config import allowed_origins, load_voice_settings
from app.runtime import LOG_DIR
from app.tools.ttfa_report import percentile

FRAME_BYTES = 640  # 20 ms of 16 kHz mono linear16
PROMPTS = [
    "Sprout moved my screen to Monday morning.",
    "Can you help me tighten my tell me about yourself?",
    "I think my biggest worry is the panel round.",
    "What should I ask them at the end of the interview?",
    "Okay. Let's practice the opener once.",
]
BARGE_PROMPT = "Give me three quick tips for a recruiter screen, one short sentence each."
INTERRUPTION = "Wait, stop. Let's talk about Monday instead."


def room_noise() -> bytes:
    """A frame of faint noise. A real mic is never digitally silent."""
    return array.array("h", [random.randint(-30, 30) for _ in range(FRAME_BYTES // 2)]).tobytes()


def frames_of(pcm: bytes) -> list[bytes]:
    frames = [pcm[i : i + FRAME_BYTES] for i in range(0, len(pcm), FRAME_BYTES)]
    if frames and len(frames[-1]) < FRAME_BYTES:
        frames[-1] += b"\x00" * (FRAME_BYTES - len(frames[-1]))
    return frames


async def synthesize(text: str, key: str, model: str) -> bytes:
    cache = LOG_DIR / "bench-audio" / (hashlib.sha1(f"{model}|{text}".encode()).hexdigest() + ".pcm")
    if cache.exists():
        return cache.read_bytes()
    params = {"model": model, "encoding": "linear16", "sample_rate": 16000, "container": "none"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.deepgram.com/v1/speak?" + urlencode(params),
            headers={"Authorization": f"Token {key}"},
            json={"text": text},
        )
        response.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(response.content)
    return response.content


class Browser:
    """Streams mic frames in real time and plays Wren's audio on a simulated speaker.
    Every server message is kept, with its arrival time, for the waits below to search."""

    def __init__(self, ws):
        self.ws = ws
        self.speech: list[bytes] = []
        self.log: list[dict] = []
        self.speaker_free_at = 0.0
        self.playing: dict[int, tuple[int, float]] = {}  # turn -> (segment, started at)
        self.plays: dict[int, list[asyncio.Task]] = {}

    def say(self, pcm: bytes) -> None:
        self.speech.extend(frames_of(pcm))

    async def mic(self) -> None:
        start, sent = time.perf_counter(), 0
        while True:
            await self.ws.send(self.speech.pop(0) if self.speech else room_noise())
            sent += 1
            delay = start + sent * 0.02 - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)

    async def receive(self) -> None:
        async for message in self.ws:
            if isinstance(message, bytes):
                continue
            data = json.loads(message)
            data["_at"] = time.perf_counter()
            self.log.append(data)
            if data["type"] == "segment_end" and data["samples"]:
                task = asyncio.create_task(self._play(data["turn"], data["seg"], data["samples"] / 24000))
                self.plays.setdefault(data["turn"], []).append(task)
            elif data["type"] == "interrupt":
                for task in self.plays.pop(data["turn"], []):
                    task.cancel()
                self.speaker_free_at = time.perf_counter()

    async def _play(self, turn: int, seg: int, seconds: float) -> None:
        now = time.perf_counter()
        start = max(now, self.speaker_free_at)
        self.speaker_free_at = start + seconds
        await asyncio.sleep(start - now)
        self.playing[turn] = (seg, time.perf_counter())
        await self.ws.send(json.dumps({"type": "playback", "turn": turn, "seg": seg, "event": "start"}))
        await asyncio.sleep(seconds)
        await self.ws.send(json.dumps({"type": "playback", "turn": turn, "seg": seg, "event": "end"}))

    def find(self, kind: str, since: float = 0.0, **fields) -> dict | None:
        for message in self.log:
            if message["_at"] >= since and message["type"] == kind and all(message.get(k) == v for k, v in fields.items()):
                return message
        return None

    def final(self, turn: int) -> dict | None:
        """The turn's final metrics: the ones with an outcome."""
        return next((m for m in reversed(self.log) if m["type"] == "metrics" and m["turn"] == turn and m.get("outcome")), None)

    async def wait(self, look, timeout: float = 25.0, what: str = "a message") -> dict:
        deadline = time.perf_counter() + timeout
        while (found := look()) is None:
            if time.perf_counter() > deadline:
                raise TimeoutError(f"waited {timeout:.0f} s for {what}")
            await asyncio.sleep(0.02)
        return found


def _clean(message: dict) -> dict:
    return {k: v for k, v in message.items() if not k.startswith("_") and k != "type"}


async def answered(browser: Browser, since: float) -> tuple[dict, dict, int]:
    """The first user turn after `since` that wasn't merged into a later one, its final
    metrics, and how many earlier parts merged into it."""
    merged = 0
    while True:
        user = await browser.wait(lambda: browser.find("user_turn", since=since), what="the user's turn")
        final = await browser.wait(lambda: browser.final(user["turn"]), timeout=40, what="the turn to finish")
        if final["outcome"] != "continued":
            return user, final, merged
        merged, since = merged + 1, user["_at"] + 1e-6


async def one_turn(browser: Browser, label: str, pcm: bytes) -> dict:
    """Say one prompt. Turns that start later from the same prompt mean it was split; they're reported."""
    since = time.perf_counter()
    browser.say(pcm)
    user, final, merged = await answered(browser, since)
    await asyncio.sleep(1.5)
    splits = [m for m in browser.log if m["type"] == "user_turn" and m["_at"] > user["_at"]]
    for split in splits:
        await browser.wait(lambda: browser.final(split["turn"]), timeout=40, what="a split turn to finish")
    first = browser.find("metrics", turn=user["turn"]) or {}
    heard = " | ".join([user["text"], *(m["text"] for m in splits)])
    return {"prompt": label, "heard": heard, **_clean(first), "outcome": final["outcome"], "merged": merged, "splits": len(splits)}


async def barge_in(browser: Browser, ask: bytes, interruption: bytes) -> list[dict]:
    since = time.perf_counter()
    browser.say(ask)

    def answering():  # the question's turn that Wren is actually speaking (earlier parts may have merged)
        return next((m for m in browser.log if m["type"] == "user_turn" and m["_at"] >= since and m["turn"] in browser.playing), None)

    user = await browser.wait(answering, what="Wren to start answering the long question")
    turn = user["turn"]
    await asyncio.sleep(0.8)  # let Wren get a sentence in
    seg, started = browser.playing[turn]
    sent = time.perf_counter()
    await browser.ws.send(json.dumps({"type": "barge_in", "turn": turn, "seg": seg, "played_ms": (sent - started) * 1000, "onset_ms": 120}))
    browser.say(interruption)
    interrupt = await browser.wait(lambda: browser.find("interrupt", since=sent, turn=turn), what="the interruption to be confirmed")
    cut = await browser.wait(lambda: browser.final(turn), what="the cut turn's metrics")
    follow, follow_final, merged = await answered(browser, sent)
    follow_first = browser.find("metrics", turn=follow["turn"]) or {}
    return [
        {"prompt": "(long reply, interrupted)", "heard": user["text"], "outcome": cut["outcome"], "confirm_ms": round((interrupt["_at"] - sent) * 1000)},
        {"prompt": "(the interruption)", "heard": follow["text"], **_clean(follow_first), "outcome": follow_final["outcome"], "merged": merged},
    ]


def report(results: list[dict], path: Path) -> None:
    print(f"\n{'prompt':<46}{'ttfa':>7}{'endpt':>7}{'retr':>6}{'llm':>6}{'tts':>6}  outcome / heard")
    for r in results:
        cells = [r.get(k) for k in ("ttfa_ms", "endpoint_ms", "retrieval_ms", "llm_ttft_ms", "tts_ttfb_ms")]
        extra = f"confirm {r['confirm_ms']} ms" if "confirm_ms" in r else r.get("heard", "")
        if r.get("merged"):
            extra += f"  (merged {r['merged']}x)"
        if r.get("splits"):
            extra += f"  (SPLIT into {r['splits'] + 1} turns)"
        print(f"{r['prompt'][:45]:<46}" + "".join(f"{'-' if c is None else c:>7}" for c in cells[:2]) + "".join(f"{'-' if c is None else c:>6}" for c in cells[2:]) + f"  {r['outcome']} / {extra}")
    ttfa = [r["ttfa_ms"] for r in results if r.get("ttfa_ms") is not None]
    print(f"\nTTFA over {len(ttfa)} voice turns: p50 {percentile(ttfa, 50)} ms, p95 {percentile(ttfa, 95)} ms (targets: 1000 / 1500)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Saved {path}")


async def run(args) -> None:
    voice = load_voice_settings()
    prompts = [(p, await synthesize(p, voice.deepgram_api_key, voice.deepgram_tts_model)) for p in PROMPTS[: args.turns]]
    ask = await synthesize(BARGE_PROMPT, voice.deepgram_api_key, voice.deepgram_tts_model)
    interruption = await synthesize(INTERRUPTION, voice.deepgram_api_key, voice.deepgram_tts_model)
    async with connect(args.url, origin=allowed_origins()[0], max_size=None) as ws:
        await ws.send(json.dumps({"type": "hello", "mode": args.mode, "tz": args.tz, "voice_out": True}))
        browser = Browser(ws)
        tasks = [asyncio.create_task(browser.mic()), asyncio.create_task(browser.receive())]
        try:
            ready = await browser.wait(lambda: browser.find("ready"), what="ready")
            print(f"session {ready['session_id']}  memory: {ready['memory']}  model: {ready['model']}  voice: {ready['voice']}")
            if not ready["voice"]:
                raise SystemExit("The server has no speech-to-text connection. Check DEEPGRAM_API_KEY and the server log.")
            await browser.wait(lambda: browser.final(1), timeout=40, what="the opening line")
            results = []
            for label, pcm in prompts:
                results.append(await one_turn(browser, label, pcm))
                print(f"  {results[-1]['ttfa_ms']} ms  {results[-1]['heard']}")
                await asyncio.sleep(1.0)
            if args.barge_in:
                results.extend(await barge_in(browser, ask, interruption))
            await ws.send(json.dumps({"type": "end"}))
        finally:
            for task in tasks:
                task.cancel()
    report(results, LOG_DIR / f"bench-{datetime.now():%Y%m%d-%H%M%S}.json")


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Measure time to first audio against a running voice server.")
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws")
    parser.add_argument("--mode", choices=("checkin", "mock"), default="checkin")
    parser.add_argument("--tz", default="America/New_York")
    parser.add_argument("--turns", type=int, default=len(PROMPTS))
    parser.add_argument("--barge-in", action="store_true", help="also interrupt a long reply")
    asyncio.run(run(parser.parse_args(argv)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
