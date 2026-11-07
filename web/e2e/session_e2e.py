"""Browser test of two full Wren sessions on the real talk page: one typed, one spoken.

Text session: no microphone permission, so the page falls back to text on the same socket,
as the voice-loop design says. Two typed turns, the second leaning on the first, then the
user leaves the page, which ends the session.

Voice session: Chrome's fake mic (--use-fake-device-for-media-stream with
--use-file-for-fake-audio-capture) plays evals/audio/sample.wav once (%noloop), after enough
quiet room noise for Wren's opening line to finish. One spoken turn, then the user leaves.

Each session checks the socket protocol, the transcript on the page, the AI disclosure, Wren's
audio and its playback, the timing record, and the server's turn log.

It needs the voice server on :8000 (with WREN_DEV_USER_ID set) and the web app on :3000.
From the repo root, with voice/.venv active and `pip install -r web/e2e/requirements.txt` done:

    python web/e2e/session_e2e.py [--mode text|voice|both] [--headed] [--lead-in 14] [--tail 40]

--no-apm runs the voice session with Chrome's echo cancellation, noise suppression and auto
gain off. It's a diagnostic: with them on, Nova-3 missed the name in the sample (session 4).
Output (socket dumps, screenshots, the fake-mic WAV) goes to voice/logs/e2e-session/.
"""

import argparse
import array
import json
import math
import random
import re
import sys
import time
import traceback
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "voice" / "logs" / "e2e-session"  # git-ignored
WEB = "http://localhost:3000"
SAMPLE = REPO / "evals" / "audio" / "sample.wav"
TURN_LOG = REPO / "voice" / "logs" / "turns.jsonl"
# Deepgram pre-recorded Nova-3 on sample.wav: what the live path should commit.
REFERENCE = "I have a recruiter screening with Dolan scheduled for Thursday"
TYPED = (
    "Hi Wren. I have a phone screen with Sprout on Monday at 10 and I'm a bit nervous about it.",
    "What should I prepare first for it?",
)
CONTEXT_WORDS = ("sprout", "monday", "screen", "phone")  # any of these: the 2nd reply used the 1st message
MIC_DENIED = "I can't hear you. Allow the microphone in your browser, or type instead."
AI_NOTE = "Wren is an AI, not a person."
AUDIO_BYTES_PER_S = 48000  # 24 kHz mono linear16
SPEECH = 0.01  # mic level that counts as the sample's voice; the fake room sits near 0.0005

# The header's status line, every time it changes, on the page's clock.
STATUS_TRACE = """
window.__statusTrace = [];
new MutationObserver(() => {
  const el = document.querySelector('header p[aria-live]');
  if (!el) return;
  const trace = window.__statusTrace;
  const text = el.textContent;
  if (!trace.length || trace[trace.length - 1][1] !== text) trace.push([performance.now(), text]);
}).observe(document, { subtree: true, childList: true, characterData: true });
"""


# Diagnostic only (--no-apm): ask for the mic with Chrome's echo cancellation, noise suppression and
# auto gain off, to tell their effect apart from the capture worklet's.
NO_APM = """
const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia = () =>
  getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
"""


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def squash(text: str) -> str:
    return " ".join(text.split())


def voice_onset(levels: list, since_ms: float) -> float | None:
    """Seconds from since_ms to the first sustained voice in the page's mic trace (7 of 10 frames loud)."""
    for i, (at, rms, _, _) in enumerate(levels):
        if rms > SPEECH and sum(1 for frame in levels[i : i + 10] if frame[1] > SPEECH) >= 7:
            return (at - since_ms) / 1000
    return None


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        diagonal, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            best = min(row[j] + 1, row[j - 1] + 1, diagonal + (r != h))
            diagonal, row[j] = row[j], best
    return row[len(hyp)] / max(1, len(ref))


def voiced_rms(samples, rate: int) -> float:
    """RMS over the 20 ms frames that carry voice (louder than a tenth of the loudest frame)."""
    n = max(1, rate // 50)
    levels = [math.sqrt(sum(x * x for x in samples[i : i + n]) / n) for i in range(0, len(samples) - n + 1, n)]
    loud = [level for level in levels if level > 0.1 * max(levels)]
    return math.sqrt(sum(level * level for level in loud) / len(loud))


def chatter(voice: array.array, rate: int, length: int, style: str) -> list[float]:
    """People talking nearby, with no words to make out: the sample time-reversed. One talker
    repeats a single copy with 1.5 s gaps, like someone taking turns; a crowd overlaps four copies."""
    rev = voice[::-1]
    if style == "talker":
        unit = list(rev) + [0] * int(1.5 * rate)
        return [float(unit[i % len(unit)]) for i in range(length)]
    out = [0.0] * length
    for offset_s in (0.0, 1.7, 3.1, 4.6):
        off = int(offset_s * rate)
        for i in range(length):
            out[i] += rev[(i + off) % len(rev)]
    return out


def build_mic(path: Path, lead_in_s: float, tail_s: float, chatter_db: float | None = None, chatter_style: str = "talker") -> tuple[Path, float]:
    """sample.wav between stretches of quiet room noise, mono, at the sample's own rate. With
    chatter_db, people talk under the whole track, that many dB below the sample's voice."""
    with wave.open(str(SAMPLE), "rb") as src:
        rate, channels = src.getframerate(), src.getnchannels()
        assert src.getsampwidth() == 2, "expected 16-bit PCM"
        pcm = array.array("h")
        pcm.frombytes(src.readframes(src.getnframes()))
    voice = pcm[0::channels]  # the sample's two channels are identical
    rng = random.Random(7)

    def room(seconds: float) -> array.array:
        return array.array("h", (rng.randint(-30, 30) for _ in range(int(seconds * rate))))

    track = room(lead_in_s) + voice + room(tail_s)
    if chatter_db is not None:
        talk = chatter(voice, rate, len(track), chatter_style)
        gain = voiced_rms(voice, rate) * 10 ** (-chatter_db / 20) / voiced_rms(talk, rate)
        track = array.array("h", (max(-32768, min(32767, round(a + b * gain))) for a, b in zip(track, talk)))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(track.tobytes())
    return path, len(voice) / rate


def summarize(m: dict) -> str:
    kind = m.get("type")
    if kind == "segment":
        return f"segment turn={m['turn']} seg={m['seg']} {m['text'][:72]!r}"
    if kind == "metrics":
        keys = ("turn", "input", "outcome", "ttfa_ms", "turn_to_audio_ms", "endpoint_ms", "llm_ttft_ms", "tts_ttfb_ms", "barge_in_ms")
        return "metrics " + " ".join(f"{k}={m[k]}" for k in keys if m.get(k) is not None)
    return f"{kind} " + json.dumps({k: v for k, v in m.items() if k not in ("type", "session_id", "model")}, ensure_ascii=False)


class Wire:
    """Everything the page's voice socket carried, stamped with time.monotonic()."""

    def __init__(self):
        self.received: list[tuple[float, object]] = []
        self.sent: list[tuple[float, object]] = []
        self.sockets = 0
        self.closed_at: float | None = None

    def attach(self, ws) -> None:
        if not ws.url.rstrip("/").endswith("/ws"):
            return  # Next's hot-reload socket
        self.sockets += 1
        ws.on("framereceived", lambda payload: self.received.append((time.monotonic(), payload)))
        ws.on("framesent", lambda payload: self.sent.append((time.monotonic(), payload)))
        ws.on("close", lambda _ws: setattr(self, "closed_at", time.monotonic()))

    @staticmethod
    def _parse(frames, kind):
        out = []
        for _, payload in frames:
            if isinstance(payload, str):
                m = json.loads(payload)
                if kind is None or m.get("type") == kind:
                    out.append(m)
        return out

    def messages(self, kind: str | None = None) -> list[dict]:
        return self._parse(self.received, kind)

    def sent_messages(self, kind: str | None = None) -> list[dict]:
        return self._parse(self.sent, kind)

    def outcome(self, turn: int) -> str | None:
        done = [m["outcome"] for m in self.messages("metrics") if m["turn"] == turn and m.get("outcome")]
        return done[-1] if done else None

    def final_metrics(self, turn: int) -> dict | None:
        done = [m for m in self.messages("metrics") if m["turn"] == turn and m.get("outcome")]
        return done[-1] if done else None

    def audio(self, turn: int) -> list[tuple[float, bytes]]:
        return [(at, p) for at, p in self.received if isinstance(p, bytes) and int.from_bytes(p[:2], "big") == turn]

    def mic_frames(self) -> int:
        return sum(1 for _, p in self.sent if isinstance(p, bytes))

    def timeline(self, t0: float) -> list[str]:
        events = []
        first_audio: set[int] = set()
        for at, payload in self.received:
            if isinstance(payload, bytes):
                turn = int.from_bytes(payload[:2], "big")
                if turn not in first_audio:
                    first_audio.add(turn)
                    events.append((at, "<-", {"type": "audio", "turn": turn, "first_frame": True}))
                continue
            m = json.loads(payload)
            if m.get("type") == "stt" and not m.get("final"):
                continue  # interim captions: counted, not listed
            events.append((at, "<-", m))
        for at, payload in self.sent:
            if isinstance(payload, str):
                events.append((at, "->", json.loads(payload)))
        return [f"{at - t0:6.2f}s {way} {summarize(m)}" for at, way, m in sorted(events, key=lambda e: e[0])]

    def dump(self, path: Path, t0: float) -> None:
        frames = [
            {"at": round(at - t0, 3), "dir": way, "msg": json.loads(p)}
            for way, stream in (("in", self.received), ("out", self.sent))
            for at, p in stream
            if isinstance(p, str)
        ]
        path.write_text(json.dumps(sorted(frames, key=lambda f: f["at"]), indent=1, ensure_ascii=False), encoding="utf-8")


class Report:
    def __init__(self, title: str):
        self.title = title
        self.checks: list[tuple[bool, str, str]] = []
        self.notes: list[str] = []
        self.sections: dict[str, list[str]] = {}

    def check(self, name: str, ok, detail: str = "") -> bool:
        self.checks.append((bool(ok), name, detail))
        return bool(ok)

    def note(self, text: str) -> None:
        self.notes.append(text)

    @property
    def ok(self) -> bool:
        return all(ok for ok, _, _ in self.checks)

    def print(self) -> None:
        print(f"\n=== {self.title} ===")
        for name, lines in self.sections.items():
            print(f"-- {name}")
            for line in lines:
                print(f"   {line}")
        print("-- checks")
        for ok, name, detail in self.checks:
            print(f"   {'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
        for note in self.notes:
            print(f"   NOTE  {note}")


class Session:
    """One browser and one talk page, and everything they saw."""

    def __init__(self, p, args, name: str, title: str, launch_args: list[str]):
        self.name, self.args = name, args
        self.report = Report(title)
        self.browser = p.chromium.launch(channel="chrome", headless=not args.headed, args=launch_args)
        self.page = self.browser.new_context().new_page()
        self.wire = Wire()
        self.console: list[str] = []
        self.errors: list[str] = []
        self.page.on("websocket", self.wire.attach)
        self.page.on("console", lambda m: self.console.append(f"{m.type}: {m.text}"))
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.add_init_script(STATUS_TRACE)
        self.t0 = time.monotonic()
        self.offset_ms = 0.0
        self.session_id: str | None = None

    def wait_for(self, condition, timeout_s: float, step_ms: int = 100):
        deadline = time.monotonic() + timeout_s
        while True:
            result = condition()
            if result or time.monotonic() >= deadline:
                return result
            self.page.wait_for_timeout(step_ms)

    def page_ms(self, at: float) -> float:
        return at * 1000 + self.offset_ms

    def open_talk(self) -> None:
        self.page.goto(f"{WEB}/talk?debug=1", timeout=180_000)
        self.page.wait_for_load_state("networkidle")
        self.offset_ms = self.page.evaluate("performance.now()") - time.monotonic() * 1000
        empty = self.page.get_by_text("Tap the mic and say hi")
        self.report.check("talk page loads with its empty state", empty.is_visible(), f"title {self.page.title()!r}")

    def await_ready(self) -> dict | None:
        ready = self.wait_for(lambda: self.wire.messages("ready"), 30)
        detail = summarize(ready[0]) if ready else "no ready message"
        if self.report.check("socket connects and the server is ready", ready, detail):
            self.session_id = ready[0]["session_id"]
            return ready[0]
        return None

    def lines(self) -> list[tuple[str, str]]:
        rows = self.page.locator('section[aria-label="Conversation"] ol > li').all()
        return [(row.locator("span").first.inner_text().strip(), squash(row.locator("p").first.inner_text())) for row in rows]

    def wren_lines(self) -> list[str]:
        return [text for who, text in self.lines() if who == "Wren"]

    def reply(self, turn: int, label: str, since: float, since_what: str) -> str:
        """Wait for Wren's reply to `turn` to finish, then check it was said, sent as audio, and played."""
        r, wire = self.report, self.wire
        outcome = self.wait_for(lambda: wire.outcome(turn), 120)
        segments = [m for m in wire.messages("segment") if m["turn"] == turn]
        text = squash(" ".join(m["text"] for m in segments))
        audio = wire.audio(turn)
        seconds = sum(len(p) - 4 for _, p in audio) / AUDIO_BYTES_PER_S
        played = {m["seg"] for m in wire.sent_messages("playback") if m["turn"] == turn and m["event"] == "end"}
        r.check(f"{label}: Wren answers and the turn completes", outcome == "complete" and text,
                f"outcome {outcome}, {len(segments)} segment(s), {len(text.split())} words")
        first = f", first audio frame {1000 * (audio[0][0] - since):,.0f} ms after {since_what}" if audio else ""
        r.check(f"{label}: Wren's voice arrives and the page plays all of it",
                audio and played == set(range(len(segments))),
                f"{len(audio)} audio frames ({seconds:.1f} s), playback ended for {len(played)}/{len(segments)} segments{first}")
        shown = ("Wren", text) in self.lines()
        r.check(f"{label}: the page shows Wren's words", shown, "" if shown else f"expected {text[:60]!r}")
        return text

    def discloses_ai(self, spoken: bool) -> None:
        """The page always says Wren is an AI. A spoken first session should also hear it."""
        note = self.page.get_by_text(AI_NOTE, exact=True).is_visible()
        self.report.check("the page says Wren is an AI", note, AI_NOTE if note else "the disclosure note is missing")
        said = [t for t in self.wren_lines() if re.search(r"\bAI\b", t)]
        detail = f"in: {said[0][:90]!r}" if said else "no Wren line in this session mentions being an AI"
        if spoken:
            self.report.check("Wren says it's an AI (memory is off, so every session is a first session)", said, detail)
        elif not said:
            self.report.note("Wren didn't say it's an AI aloud: typing first cancels the opening line. The page note covers it.")

    def leave(self) -> None:
        """End the session the way a user does: the header link back home unmounts the talk page."""
        self.page.get_by_role("link", name="Wren", exact=True).click()
        ended = self.wait_for(lambda: self.wire.sent_messages("end"), 45)
        closed = self.wait_for(lambda: self.wire.closed_at is not None, 15)
        self.report.check("leaving the page ends the session (end sent, socket closed)", ended and closed,
                          f"end sent: {bool(ended)}, socket closed: {closed}, now at {self.page.url}")
        self.report.check("one socket for the whole session", self.wire.sockets == 1, f"{self.wire.sockets} voice socket(s)")

    def server_log(self) -> list[dict]:
        self.page.wait_for_timeout(2000)  # the server appends after each turn, off the live path
        if not self.session_id or not TURN_LOG.exists():
            return []
        rows = []
        for line in TURN_LOG.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("session") == self.session_id:
                rows.append(row)
        self.report.sections["server turn log (voice/logs/turns.jsonl)"] = [
            " ".join(f"{k}={row.get(k)}" for k in ("turn", "input", "outcome", "ttfa_ms", "turn_to_audio_ms", "llm_ttft_ms", "tts_ttfb_ms", "model", "retrieval_fallback"))
            for row in rows
        ] or ["(no rows for this session)"]
        return rows

    def finish(self) -> None:
        r = self.report
        trace = self.page.evaluate("window.__statusTrace || []") if not self.page.is_closed() else []
        if trace:
            r.sections["status line"] = [" -> ".join(f"{text} @{(at - self.page_ms(self.t0)) / 1000:.1f}s" for at, text in trace)]
        r.sections["protocol timeline (s from start)"] = self.wire.timeline(self.t0)
        self.wire.dump(OUT / f"{self.name}-wire.json", self.t0)
        errors = [line for line in self.console if line.startswith("error")] + [f"pageerror: {e}" for e in self.errors]
        r.check("no page errors or console errors", not errors, "; ".join(errors[:5]) or "none")
        self.browser.close()


def text_session(p, args) -> Report:
    s = Session(p, args, "text", "Text session: mic declined, two typed turns",
                ["--autoplay-policy=no-user-gesture-required"])
    r, wire, page = s.report, s.wire, s.page
    try:
        s.open_talk()
        replies: list[str] = []
        for i, typed in enumerate(TYPED, 1):
            page.get_by_label("Message").fill(typed)
            sent_at = time.monotonic()
            if i == 1:
                s.t0 = sent_at
            page.get_by_role("button", name="Send").click()
            if i == 1:
                ready = s.await_ready()
                if ready is None:
                    return r
                hello = wire.sent_messages("hello")
                r.sections["session"] = [f"hello: {summarize(hello[0]) if hello else '-'}", f"ready: {summarize(ready)}"]
                notices = s.wait_for(lambda: page.locator('p[role="status"]').all_inner_texts(), 10) or []
                r.check("declined mic: the page says it can't hear and offers typing", MIC_DENIED in notices,
                        " | ".join(notices) or "no notice shown")
            turn = s.wait_for(lambda: next((m for m in wire.messages("user_turn") if m["text"] == typed), None), 30)
            if not r.check(f"typed turn {i} reaches the server as the user's turn", turn,
                           f"turn {turn['turn']}" if turn else "no matching user_turn"):
                return r
            if i == 1:
                opening = wire.outcome(1) if turn["turn"] > 1 else None
                if opening == "continued":
                    r.note("Wren's opening line (turn 1) was cancelled: the typed message reached the server while the "
                           "opening was still generating, so the session went straight to answering it.")
            replies.append(s.reply(turn["turn"], f"typed turn {i}", sent_at, "Send"))
            metrics = wire.final_metrics(turn["turn"]) or {}
            r.check(f"typed turn {i}: timing record says text input", metrics.get("input") == "text",
                    f"input {metrics.get('input')}, commit to first audio {metrics.get('turn_to_audio_ms')} ms, "
                    f"LLM first token {metrics.get('llm_ttft_ms')} ms, TTS first byte {metrics.get('tts_ttfb_ms')} ms")
            s.wait_for(lambda: page.locator("header p[aria-live]").inner_text() == "Listening", 15)

        hit = [w for w in CONTEXT_WORDS if w in replies[1].lower()]
        r.check("2nd reply builds on the 1st message (same-session history)", hit, f"mentions {hit}" if hit else replies[1][:120])
        s.discloses_ai(spoken=False)
        you = [text for who, text in s.lines() if who == "You"]
        r.check("the page shows both typed messages as the user's lines", you == [squash(t) for t in TYPED], f"{len(you)} user line(s)")
        r.check("no mic audio went upstream", wire.mic_frames() == 0, f"{wire.mic_frames()} binary frames sent")
        panel = page.locator('aside[aria-label="Voice timing"]').inner_text()
        r.check("debug panel shows the model and the memory status", f"Memory {ready['memory']}" in panel,
                panel.split("\n")[-1])
        r.sections["conversation on the page"] = [f"{who}: {text}" for who, text in s.lines()]
        page.screenshot(path=str(OUT / "text-session.png"), full_page=True)
        s.leave()
        rows = s.server_log()
        typed_rows = [row for row in rows if row["input"] == "text" and row["outcome"] == "complete"]
        r.check("server logged both typed turns as complete", len(typed_rows) == len(TYPED), f"{len(rows)} row(s) for session {s.session_id}")
    except Exception:
        r.check("ran without an exception", False, traceback.format_exc().strip().splitlines()[-1])
        r.sections["traceback"] = traceback.format_exc().splitlines()
        try:
            page.screenshot(path=str(OUT / "text-session-error.png"), full_page=True)
        except Exception:
            pass
    finally:
        s.finish()
    return r


def voice_session(p, args) -> Report:
    mic, sample_s = build_mic(OUT / f"voice-mic{args.tag}.wav", args.lead_in, args.tail, args.chatter, args.chatter_style)
    title = "Voice session: fake mic playing evals/audio/sample.wav" + (" (Chrome audio processing OFF)" if args.no_apm else "")
    s = Session(p, args, "voice" + args.tag, title, [
        "--use-fake-ui-for-media-stream",  # accept the mic prompt
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-audio-capture={mic}%noloop",
        "--autoplay-policy=no-user-gesture-required",
    ])
    r, wire, page = s.report, s.wire, s.page
    if args.no_apm:
        page.add_init_script(NO_APM)
    r.sections["fake mic"] = [f"{mic}: {args.lead_in:.0f} s quiet room, sample.wav ({sample_s:.1f} s), {args.tail:.0f} s quiet; played once"]
    if args.chatter is not None:
        r.sections["fake mic"].append(f"people talking under all of it ({args.chatter_style}), {args.chatter:g} dB below the sample's voice")
    try:
        s.open_talk()
        s.t0 = time.monotonic()
        page.get_by_role("button", name="Start talking").click()
        ready = s.await_ready()
        if ready is None:
            return r
        hello = wire.sent_messages("hello")
        r.sections["session"] = [f"hello: {summarize(hello[0]) if hello else '-'}", f"ready: {summarize(ready)}"]
        live = s.wait_for(lambda: page.get_by_role("button", name="Mute").count() == 1, 5)
        r.check("the page hears: speech-to-text is up and the mic is live", ready["voice"] and live,
                f"ready.voice={ready['voice']}, mic button offers Mute: {bool(live)}")

        s.reply(1, "opening line", s.t0, "Start talking")

        sample_end = s.t0 + args.lead_in + sample_s
        last_word = s.t0 + args.lead_in + 7.12  # "Thursday" ends 7.12 s into the sample

        def settled():
            if time.monotonic() < sample_end + 2.0:  # a cut-short piece commits within a second; let it
                return None
            turns = wire.messages("user_turn")
            last = turns[-1] if turns else None
            return last if last and wire.outcome(last["turn"]) in ("complete", "interrupted", "failed") else None

        last = s.wait_for(settled, sample_end - time.monotonic() + 60)
        turns = wire.messages("user_turn")
        r.sections["user turns the server committed"] = [f"turn {m['turn']}: {m['text']!r} -> {wire.outcome(m['turn'])}" for m in turns] or ["(none)"]
        if not r.check("the spoken sample is committed as a user turn", last, f"{len(turns)} user_turn message(s)"):
            return r

        captions = [m for m in wire.messages("stt")]
        r.check("live captions show while the user speaks", captions, f"{len(captions)} caption update(s), {sum(1 for m in captions if m['final'])} final")
        answered = [m for m in turns if wire.outcome(m["turn"]) != "continued"]
        merged = [m for m in turns if wire.outcome(m["turn"]) == "continued"]
        r.check("the spoken sentence ends up as one answered turn", len(answered) == 1,
                f"{len(answered)} answered" + (f", {len(merged)} cut short and merged into the next" if merged else ""))
        error = wer(REFERENCE, last["text"])
        r.check("committed words match the reference transcript (WER <= 25%)", error <= 0.25,
                f"WER {error:.0%}: heard {last['text']!r}, reference {REFERENCE!r}")

        answer = s.reply(last["turn"], "spoken turn", last_word, "the sample's last word (test clock, about ±0.3 s)")
        hit = [w for w in ("thursday", "recruiter", "screen", "dolan") if w in answer.lower()]
        r.check("Wren's answer uses what the user said", hit, f"mentions {hit}" if hit else answer[:120])
        metrics = wire.final_metrics(last["turn"]) or {}
        ttfa = metrics.get("ttfa_ms")
        r.check("spoken turn: time to first audio is logged with its breakdown", metrics.get("input") == "voice" and ttfa is not None,
                f"TTFA {ttfa} ms = end of speech to turn {metrics.get('endpoint_ms')} + retrieval {metrics.get('retrieval_ms')} "
                f"+ LLM first token {metrics.get('llm_ttft_ms')} + first segment {metrics.get('segment_ms')} + TTS first byte {metrics.get('tts_ttfb_ms')} ms")
        if ttfa is not None and ttfa > 1500:
            r.note(f"TTFA {ttfa} ms is over the spec's 1,500 ms p95 target (one turn, so not a percentile).")
        panel = page.locator('aside[aria-label="Voice timing"]').inner_text()
        r.check("debug panel shows that TTFA", ttfa is not None and f"{ttfa:,} ms" in panel, " | ".join(panel.split("\n")[:2]))

        page_you = [text for who, text in s.lines() if who == "You"]
        r.check("the page shows the user's words once, without the cut-short piece", page_you == [squash(last["text"])], f"user lines: {page_you}")
        s.discloses_ai(spoken=True)

        barges = wire.sent_messages("barge_in")
        if barges:
            r.note(f"{len(barges)} barge-in(s) during the session: {[summarize(b) for b in barges]}")
        levels = page.evaluate("window.__wrenLevels || []")
        start = voice_onset(levels, s.page_ms(s.t0))
        room = sorted(rms for at, rms, _, _ in levels if at < s.page_ms(s.t0) + (args.lead_in - 2) * 1000)
        floor = f"room level median {room[len(room) // 2]:.4f}" if room else "no lead-in frames left in the trace"
        expected = args.lead_in + 0.72  # the sample's first word, per the reference
        if args.chatter is None:  # with people talking, "sustained voice" is theirs from the start
            r.check("the page heard the sample when expected", start is not None and abs(start - expected) < 2.0,
                    (f"sustained voice from {start:.1f} s after the click (first word expected at {expected:.1f} s), " if start is not None
                     else f"no sustained voice above {SPEECH} in {len(levels)} frames, ")
                    + f"peak level {max((f[1] for f in levels), default=0):.3f}, {floor}")
        else:
            pauses = [at - s.t0 for at, payload in wire.sent if isinstance(payload, str) and '"barge_in"' in payload]
            r.check("the page never paused Wren for the room", not pauses,
                    f"{len(pauses)} barge_in sent" + (f", at {', '.join(f'{t:.1f}' for t in pauses)} s" if pauses else "")
                    + f"; {floor}")

        r.sections["conversation on the page"] = [f"{who}: {text}" for who, text in s.lines()]
        page.screenshot(path=str(OUT / f"{s.name}-session.png"), full_page=True)
        s.leave()
        rows = s.server_log()
        voiced = [row for row in rows if row["input"] == "voice" and row["outcome"] == "complete" and row["ttfa_ms"] is not None]
        r.check("server logged the spoken turn with its TTFA", voiced, f"{len(rows)} row(s) for session {s.session_id}")
    except Exception:
        r.check("ran without an exception", False, traceback.format_exc().strip().splitlines()[-1])
        r.sections["traceback"] = traceback.format_exc().splitlines()
        try:
            page.screenshot(path=str(OUT / f"{s.name}-session-error.png"), full_page=True)
        except Exception:
            pass
    finally:
        s.finish()
    return r


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("text", "voice", "both"), default="both")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--lead-in", type=float, default=14.0, help="seconds of quiet room before the sample")
    parser.add_argument("--tail", type=float, default=40.0, help="seconds of quiet room after it")
    parser.add_argument("--no-apm", action="store_true", help="diagnostic: voice session with Chrome's audio processing off")
    parser.add_argument("--tag", default="", help="suffix for this run's output files")
    parser.add_argument("--chatter", type=float, default=None, metavar="DB",
                        help="voice session: people talking under the whole fake-mic track, DB below the sample's voice")
    parser.add_argument("--chatter-style", choices=("talker", "crowd"), default="talker",
                        help="one person nearby taking turns (default), or a crowd")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    reports = []
    with sync_playwright() as p:
        if args.mode in ("text", "both"):
            reports.append(text_session(p, args))
        if args.mode in ("voice", "both"):
            reports.append(voice_session(p, args))
    for report in reports:
        report.print()
    print(f"\nScreenshots and wire dumps: {OUT}")
    return 0 if all(report.ok for report in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
