"""Browser end-to-end test of the voice loop: the real talk page in Chrome, with a fake mic.

Chrome plays a WAV file as the microphone: room noise, then a question once Wren's opening
line is done, then an interruption while Wren is answering. The test watches the page's
WebSocket frames and its mic level trace (recorded with ?debug=1), all on the page's clock.
It checks that the page:
- connects
- commits the spoken turn
- receives Wren's audio
- shows time to first audio in the debug panel
- pauses Wren within 300 ms of the interruption's first sound (the spec's barge-in target),
  with the server confirming the interruption

It needs the voice server on :8000 (with WREN_DEV_USER_ID set) and the web app on :3000.
From the repo root, with voice/.venv active and `pip install -r web/e2e/requirements.txt` done:

    python web/e2e/voice_e2e.py [--headed] [--timeline] [--levels]

It uses the installed Chrome (channel="chrome"), so Playwright downloads no browser.
"""

import argparse
import array
import asyncio
import json
import random
import sys
import time
import urllib.request
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "voice"))

from playwright.sync_api import sync_playwright  # noqa: E402

from app.config import load_voice_settings  # noqa: E402
from app.runtime import LOG_DIR  # noqa: E402
from app.tools.bench import INTERRUPTION, synthesize  # noqa: E402

WEB = "http://localhost:3000"
QUESTION = "Sprout moved my screen to Monday morning."
TIMELINE = ((8.0, QUESTION), (13.8, INTERRUPTION))  # seconds into the fake mic
LENGTH_S = 26.0
BARGE_IN_TARGET_MS = 300
SOUND = 0.01  # mic level that counts as the user's voice; the fake room sits near 0.0003


def build_mic(path: Path) -> None:
    voice = load_voice_settings()
    rate = 16000
    samples = array.array("h", (random.randint(-30, 30) for _ in range(int(LENGTH_S * rate))))
    for at, text in TIMELINE:
        clip = array.array("h")
        clip.frombytes(asyncio.run(synthesize(text, voice.deepgram_api_key, voice.deepgram_tts_model)))
        start = int(at * rate)
        samples[start : start + len(clip)] = clip[: len(samples) - start]
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.tobytes())


def worklet_type() -> str:
    with urllib.request.urlopen(f"{WEB}/voice/capture-worklet.mjs", timeout=10) as response:
        return response.headers.get("content-type", "")


def barge_in_latency(levels: list, audible_from: float | None) -> tuple[float, float] | None:
    """From the level trace: the user's first sound while Wren was talking, and how long until the page paused."""
    for i, (at, rms, audible, status) in enumerate(levels):
        if status == "speaking" and audible and rms > SOUND and (audible_from is None or at >= audible_from):
            paused = next((later for later, _, _, s in levels[i:] if s == "paused"), None)
            return (at, paused - at) if paused is not None else None
    return None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--timeline", action="store_true", help="print the protocol messages in order")
    parser.add_argument("--levels", action="store_true", help="print the mic levels the page saw")
    args = parser.parse_args()

    mic = LOG_DIR / "e2e-mic.wav"
    build_mic(mic)
    received: list[tuple[float, object]] = []
    sent: list[tuple[float, dict]] = []
    console: list[str] = []

    def on_socket(ws):
        ws.on("framereceived", lambda payload: received.append((time.monotonic(), payload)))
        ws.on("framesent", lambda payload: sent.append((time.monotonic(), json.loads(payload))) if isinstance(payload, str) else None)

    def messages(kind: str) -> list[dict]:
        return [m for _, p in received if isinstance(p, str) and (m := json.loads(p)).get("type") == kind]

    checks: dict[str, tuple[bool, str]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=not args.headed,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                f"--use-file-for-fake-audio-capture={mic}",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        page = browser.new_page()
        page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        page.on("websocket", on_socket)
        page.goto(f"{WEB}/talk?debug=1")
        page.wait_for_load_state("networkidle")
        page.get_by_role("button", name="Start talking").click()
        # One clock for everything: the page's performance.now(), in ms.
        page_offset_ms = page.evaluate("performance.now()") - time.monotonic() * 1000

        def on_page_clock(at: float) -> float:
            return at * 1000 + page_offset_ms

        def wait_for(condition, timeout_s: float) -> bool:
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if condition():
                    return True
                page.wait_for_timeout(100)
            return False

        ready = wait_for(lambda: messages("ready"), 15)
        checks["connects and gets ready"] = (ready, (messages("ready") or [{}])[0].get("memory", "no ready message"))

        def question_turn():
            return next((m for m in messages("user_turn") if "monday" in m["text"].lower()), None)

        turn = wait_for(question_turn, 30) and question_turn()
        checks["commits the spoken question"] = (bool(turn), turn["text"] if turn else "no matching user_turn")
        if turn:
            def ttfa():
                return next((m for m in messages("metrics") if m["turn"] == turn["turn"] and m.get("ttfa_ms") is not None), None)

            timed = wait_for(ttfa, 20) and ttfa()
            detail = f"{timed['ttfa_ms']} ms (endpoint {timed['endpoint_ms']}, llm {timed['llm_ttft_ms']}, tts {timed['tts_ttfb_ms']})" if timed else "no metrics"
            checks["logs time to first audio"] = (bool(timed), detail)
            page.wait_for_timeout(1500)  # let the answer stream
            frames = [p for _, p in received if isinstance(p, bytes) and int.from_bytes(p[:2], "big") == turn["turn"]]
            checks["receives Wren's audio"] = (bool(frames), f"{len(frames)} audio frames for turn {turn['turn']}")
            panel = page.locator('aside[aria-label="Voice timing"]').inner_text()
            checks["debug panel shows it"] = (bool(timed) and f"{timed['ttfa_ms']:,} ms" in panel, " | ".join(panel.split("\n")[:2]))

        barged = wait_for(lambda: any(m.get("type") == "barge_in" for _, m in sent), 25)
        levels = page.evaluate("window.__wrenLevels || []")
        (LOG_DIR / "e2e-levels.json").write_text(json.dumps(levels), encoding="utf-8")
        if barged:
            barge_in = next(m for _, m in sent if m.get("type") == "barge_in")
            audible_from = next(
                (on_page_clock(a) for a, m in sent if m.get("type") == "playback" and m.get("turn") == barge_in["turn"] and m.get("event") == "start"),
                None,
            )
            measured = barge_in_latency(levels, audible_from)
            if measured:
                _, pause_ms = measured
                checks["pauses Wren fast"] = (
                    pause_ms <= BARGE_IN_TARGET_MS,
                    f"{pause_ms:.0f} ms from the first sound to paused, turn {barge_in['turn']} (target {BARGE_IN_TARGET_MS}); the page's own onset to pause: {barge_in['onset_ms']:.0f} ms",
                )
            else:
                checks["pauses Wren fast"] = (False, "no pause after the user's sound in the level trace")
            confirmed = wait_for(lambda: any(m["turn"] == barge_in["turn"] for m in messages("interrupt")), 10)
            checks["server confirms the interruption"] = (confirmed, f"turn {barge_in['turn']}")
        else:
            checks["pauses Wren fast"] = (False, "the page never sent barge_in")

        shot = LOG_DIR / "e2e-talk.png"
        page.screenshot(path=str(shot), full_page=True)
        browser.close()

    checks["worklets served as JavaScript"] = ("javascript" in (kind := worklet_type()), kind)
    first_frame = levels[0][0] if levels else on_page_clock(0)
    if args.timeline:
        shown = ("ready", "user_turn", "segment_end", "reply_end", "interrupt", "resume", "voice")
        events = [(at, "<-", json.loads(p)) for at, p in received if isinstance(p, str)]
        events += [(at, "->", m) for at, m in sent if m.get("type") in ("playback", "barge_in", "user_text")]
        for at, way, m in sorted(events, key=lambda e: e[0]):
            if m.get("type") in shown or way == "->":
                print(f"  {(on_page_clock(at) - first_frame) / 1000:6.2f}s {way} " + json.dumps({k: v for k, v in m.items() if k not in ('session_id', 'model')}))
    if args.levels:
        for at, rms, audible, status in levels[::5]:  # every 100 ms
            print(f"  {(at - first_frame) / 1000:6.2f}s {rms:.4f} {'W' if audible else ' '} {status:<10} " + "#" * min(40, int(rms * 400)))
    for name, (ok, detail) in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    errors = [line for line in console if line.startswith("error")]
    if errors:
        print("Console errors:\n  " + "\n  ".join(errors[:10]))
    print(f"Screenshot: {shot}  (times above are on the page's clock, from its first mic frame)")
    return 0 if all(ok for ok, _ in checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
