"""Test doubles for the voice session: speech-to-text, text-to-speech, chat and memory,
plus a reader for the socket that splits JSON from audio and never blocks forever.

The session runs on TestClient's event loop, in another thread. FakeStt.emit() hands
events over thread-safely, and Wire reads with a timeout so a missing message fails
the test instead of hanging it.
"""

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from uuid import UUID

import anyio
from starlette.testclient import TestClient

from app.config import VoiceSettings
from app.conversation import ConversationRegistry
from app.main import app
from app.memory.live import NoMemory
from app.runtime import Runtime
from app.throttle import Throttled
from app.voice import clock
from app.voice.protocol import unpack_audio
from app.voice.tuning import Tuning

USER = UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")
ORIGIN = "http://localhost:3000"
FAST = Tuning(confirm_window_s=0.3, hello_timeout_s=0.5, idle_s=60.0, hold_checkin_s=0.0, playback_grace_s=0.5)
SEGMENT_BYTES = 4800  # one fake TTS chunk: 2400 samples, 100 ms at 24 kHz


class FakeStt:
    def __init__(self, fail=None, hang: set[str] = frozenset()):
        """`hang` names methods ("send", "keep_alive", "finalize") that wait forever, like a stalled socket."""
        self.fail, self.hang, self.sent_bytes, self.finalized, self.keepalives, self.closed = fail, hang, 0, 0, 0, False
        self._loop = self._queue = None
        self._ready = threading.Event()

    async def _maybe_hang(self, method: str):
        if method in self.hang:
            await asyncio.Event().wait()

    async def send(self, pcm):
        await self._maybe_hang("send")
        self.sent_bytes += len(pcm)

    async def keep_alive(self):
        await self._maybe_hang("keep_alive")
        self.keepalives += 1

    async def finalize(self):
        await self._maybe_hang("finalize")
        self.finalized += 1

    async def events(self):
        self._loop, self._queue = asyncio.get_running_loop(), asyncio.Queue()
        self._ready.set()
        if self.fail:
            raise self.fail
        while (item := await self._queue.get()) is not None:
            yield item

    def emit(self, event):
        """Called from the test thread."""
        assert self._ready.wait(5), "the session never started reading speech-to-text"
        self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    async def close(self):
        self.closed = True
        if self._queue is not None:
            self._queue.put_nowait(None)


class FakeTts:
    """`spoken` records every segment as (text, in_role): in_role is True when the interviewer says it."""

    def __init__(self, chunks=2, error=None):
        self.chunks, self.error, self.spoken, self.warmed = chunks, error, [], 0

    async def warm(self):
        self.warmed += 1

    async def stream(self, text, *, in_role=False):
        self.spoken.append((text, in_role))
        if self.error:
            raise self.error
        for _ in range(self.chunks):
            await asyncio.sleep(0)
            yield b"\x01\x00" * (SEGMENT_BYTES // 2)

    async def close(self):
        pass


class FakeChat:
    """Scripted replies, one list of deltas per call. Calls can hang before their first
    token (hang_before) or after N deltas (hang_after), until cancelled, or wait a few
    seconds before their first token (delay_before)."""

    def __init__(self, replies, hang_before=(), hang_after=None, delay_before=None):
        self.replies, self.hang_before, self.hang_after = list(replies), set(hang_before), dict(hang_after or {})
        self.delay_before = dict(delay_before or {})
        self.calls, self.cancelled = [], []

    async def __call__(self, messages, timing, *, retry=False):
        index = len(self.calls)
        self.calls.append(messages)
        timing.model = "fake-chat"
        try:
            if index in self.hang_before:
                await asyncio.sleep(3600)
            await asyncio.sleep(self.delay_before.get(index, 0))
            deltas = self.replies[index] if index < len(self.replies) else ["Okay."]
            for i, delta in enumerate(deltas):
                if self.hang_after.get(index) == i:
                    await asyncio.sleep(3600)
                if timing.llm_first_token is None:
                    timing.llm_first_token = clock.now()
                yield delta
            if self.hang_after.get(index) == len(deltas):
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled.append(index)
            raise


class RecordingMemory(NoMemory):
    def __init__(self):
        super().__init__("test")
        self.appended, self.closed, self.opened = [], [], []

    async def open_session(self, user_id, mode, tz, resume_id, now):
        self.opened.append(user_id)
        return await super().open_session(user_id, mode, tz, resume_id, now)

    async def append(self, session_id, user_id, turns):
        self.appended.extend(turns)

    async def close_session(self, session_id, user_id, now):
        self.closed.append(session_id)


class FixedProvider:
    """Hands out one memory, and answers the age check with `adult`, raising it when it's an exception."""

    def __init__(self, memory, *, adult: bool | Exception = True):
        self.memory, self.adult, self.confirmed = memory, adult, []

    async def for_user(self, user_id):
        return self.memory

    async def age_confirmed(self, user_id):
        if isinstance(self.adult, Exception):
            raise self.adult
        return self.adult

    async def confirm_age(self, user_id, at, tz):
        self.confirmed.append((user_id, at, tz))


class FakeAuth:
    """Stands in for SupabaseAuth.user_for_token: `users` maps tokens to user ids. With
    throttled=True every lookup raises Throttled, as over the lookup cap."""

    def __init__(self, users: dict[str, UUID], *, throttled: bool = False):
        self.users, self.throttled, self.asked = dict(users), throttled, []

    async def user_for_token(self, token):
        self.asked.append(token)
        if self.throttled:
            raise Throttled("token lookups")
        return self.users.get(token)


def make_runtime(
    chat, *, stt=None, tts=None, memory=None, tuning=FAST, dev_user=USER, stt_factory=None, auth=None, adult=True,
):
    voice = VoiceSettings(
        deepgram_api_key="dg", deepgram_stt_model="nova-3", deepgram_tts_model="aura-2-vesta-en",
        groq_chat_model="fake-chat", groq_chat_fallback_model=None,
        allowed_origins=(ORIGIN,), dev_user_id=dev_user,
    )
    if stt_factory is None and stt is not None:
        connects = []

        async def stt_factory():
            connects.append(1)
            if len(connects) > 1:
                raise ConnectionError("no second speech-to-text connection in this test")
            return stt

    return Runtime(
        voice=voice,
        tuning=tuning,
        chat=chat,
        registry=ConversationRegistry(),
        memory=FixedProvider(memory or RecordingMemory(), adult=adult),
        connect_stt=stt_factory,
        tts=tts if tts is not None else FakeTts(),
        turn_log=None,
        auth=auth,
    )


def receive(ws, timeout: float):
    async def get():
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()

    return ws.portal.call(get)


class Wire:
    def __init__(self, ws):
        self.ws, self.audio = ws, []

    def next(self, timeout=5.0) -> dict:
        while True:
            message = receive(self.ws, timeout)
            if message["type"] == "websocket.close":
                return {"type": "_closed", "code": message.get("code")}
            if message.get("bytes") is not None:
                turn, seg, pcm = unpack_audio(message["bytes"])
                self.audio.append((turn, seg, len(pcm)))
                continue
            return json.loads(message["text"])

    def until(self, kind: str, timeout=5.0, **fields) -> tuple[dict, list[dict]]:
        seen = []
        while True:
            message = self.next(timeout)
            seen.append(message)
            if message["type"] == kind and all(message.get(k) == v for k, v in fields.items()):
                return message, seen
            if message["type"] == "_closed":
                raise AssertionError(f"socket closed while waiting for {kind} {fields}: {seen}")

    def quiet(self, seconds: float) -> list[dict]:
        """Everything that arrives within `seconds`."""
        seen, deadline = [], time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            try:
                seen.append(self.next(timeout=left))
            except TimeoutError:
                break
        return seen


def play(ws, wire: Wire, turn: int) -> list[dict]:
    """Wait for the whole reply, then report every segment as played."""
    end, seen = wire.until("reply_end", turn=turn)
    segments = [m for m in seen if m["type"] == "segment" and m["turn"] == turn]
    for m in segments:
        ws.send_json({"type": "playback", "turn": turn, "seg": m["seg"], "event": "start"})
        ws.send_json({"type": "playback", "turn": turn, "seg": m["seg"], "event": "end"})
    wire.until("metrics", turn=turn, outcome="complete")
    return segments


def send_silence(ws, stt: FakeStt, seconds: float):
    frames = int(seconds / 0.02)
    expected = stt.sent_bytes + frames * 640
    for _ in range(frames):
        ws.send_bytes(b"\x00" * 640)
    deadline = time.monotonic() + 5
    while stt.sent_bytes < expected:
        assert time.monotonic() < deadline, "the session never forwarded the audio"
        time.sleep(0.01)


@contextmanager
def voice_session(runtime: Runtime, client_host="127.0.0.1", **hello):
    app.state.runtime = runtime
    try:
        with TestClient(app, client=(client_host, 50000)) as client:
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
                ws.send_json({"type": "hello", **hello})
                wire = Wire(ws)
                ready, _ = wire.until("ready")
                yield ws, wire, ready
    finally:
        app.state.runtime = None
