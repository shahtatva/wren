"""One browser connection: the turn state machine between speech-to-text, /turn and
text-to-speech. Design: docs/superpowers/specs/2026-10-16-voice-loop-design.md, "Turn lifecycle".

A session is in one of four states:
- listening
- thinking: a reply is being generated and no audio has gone out
- speaking: its audio has gone out
- paused: the browser heard the user over Wren and paused playback

Barge-in pauses fast in the browser and is confirmed here with real words. Echo of
Wren's own words and backchannels resume playback instead. Speech while thinking
cancels the reply and merges into the user's turn. Wren's turns keep only the words the
user heard, so the next context shows where Wren was cut off.

Every voice turn logs three timestamps: the end of the user's speech, the first LLM
token and the first audio byte sent, with the time to first audio between them. The
browser gets the same record for its debug panel.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from fastapi import WebSocket

from app.conversation import Conversation
from app.memory.live import NoMemory
from app.runtime import Runtime
from app.turn import run_turn
from app.voice import clock
from app.voice.bargein import is_echo, is_interruption, real_words
from app.voice.endpoint import Commit, EndOfTurn
from app.voice.events import SpeechStarted, SttEvent, Transcript, UtteranceEnd
from app.voice.metrics import TurnTiming, log_turn
from app.voice.protocol import BargeIn, End, Hello, Mic, Playback, UserText, pack_audio, parse_client
from app.voice.stt import Stt
from app.voice.timeline import AudioTimeline
from app.voice.vad import EnergyVad

logger = logging.getLogger(__name__)

TTS_SAMPLES_PER_MS = 24  # 24 kHz
STT_RETRY_S = (1, 2, 5, 10, 30)
# After speech resumed mid-reply and stopped again with no new words (a cough, a breath),
# how long to wait for Deepgram's finals before answering the first part on its own.
PREFIX_GRACE_S = 0.5
REPLY_FAILED = "I lost my train of thought. Could you say that again?"
SESSION_OVER = "That's our time for this session. Start a new one whenever you're ready."
SESSION_OVER_CLOSE = 4410  # the session cap's close code: the page doesn't reconnect after it


@dataclass
class Segment:
    index: int
    text: str
    samples: int = 0  # 24 kHz samples sent to the browser
    started_at: float | None = None  # when the browser started playing it
    played: bool = False  # the browser finished it, or it had no audio

    def duration_ms(self) -> float:
        return self.samples / TTS_SAMPLES_PER_MS


@dataclass
class Reply:
    no: int
    user_text: str | None  # None for the opening line
    timing: TurnTiming
    segments: list[Segment] = field(default_factory=list)
    task: asyncio.Task | None = None
    llm_done: bool = False
    end_sent: bool = False
    finished: bool = False
    pause: tuple[int, float] | None = None  # (segment, played ms) where the browser paused
    paused_s: float = 0.0  # total time the browser held this reply paused

    def full_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    def heard_text(self, now: float) -> str:
        """Fully played segments, then the share of the cut segment that played. Aura-2 gives
        no word timings, so the cut is proportional to playback time, rounded down."""
        heard: list[str] = []
        for seg in self.segments:
            if seg.played:
                heard.extend(seg.text.split())
                continue
            if self.pause and self.pause[0] == seg.index:
                played_ms = self.pause[1]
            elif seg.started_at is not None:
                played_ms = (now - seg.started_at) * 1000
            else:
                break
            words = seg.text.split()
            total = seg.duration_ms()
            fraction = min(1.0, played_ms / total) if total else 0.0
            heard.extend(words[: int(len(words) * fraction)])
            break
        return " ".join(heard)


class VoiceSession:
    def __init__(self, ws: WebSocket, runtime: Runtime, user_id: UUID, hello: Hello):
        self.ws, self.rt, self.user_id, self.hello = ws, runtime, user_id, hello
        tuning = runtime.tuning
        self.endpoint = EndOfTurn(tuning.hold_s(hello.mode))
        self.vad = EnergyVad(tuning.vad_silence_ms)
        self.timeline = AudioTimeline()
        self.state = "listening"
        self.reply: Reply | None = None
        self.stt: Stt | None = None
        self.mic_on = True
        self._turn = 0
        self._prefix = ""  # a committed turn whose reply was cancelled because the user kept talking
        self._prefix_speech_end: float | None = None
        self._prefix_deadline: float | None = None
        self._paused_at = 0.0
        self._recent_wren = ""  # Wren's last reply, for the echo tail after it ends
        self._recent_wren_until = 0.0
        now = clock.now()
        self._started = self._last_speech = self._last_audio = self._last_keepalive = now
        self._tasks: set[asyncio.Task] = set()
        self._saves: set[asyncio.Task] = set()  # persistence and logging: finished, not cancelled, at shutdown
        self.memory = None
        self.convo: Conversation | None = None
        self._send_lock = asyncio.Lock()
        self._socket_open = True
        self._closing = False
        self._stt_retry: asyncio.Task | None = None

    @property
    def _speaks(self) -> bool:
        return self.hello.voice_out and self.rt.tts is not None

    # --- plumbing ---

    def _spawn(self, coro, *, save: bool = False) -> asyncio.Task:
        task = asyncio.create_task(coro)
        tasks = self._saves if save else self._tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return task

    async def _send(self, message: dict | bytes) -> None:
        if not self._socket_open:
            return
        async with self._send_lock:
            try:
                if isinstance(message, bytes):
                    await self.ws.send_bytes(message)
                else:
                    await self.ws.send_text(json.dumps(message))
            except Exception:  # the browser went away; the read loop ends the session
                self._socket_open = False

    def _persist(self, records: list[dict]) -> None:
        async def save():
            for attempt in (1, 2):
                try:
                    await self.memory.append(self.convo.session_id, self.user_id, records)
                    return
                except Exception as e:
                    logger.warning("saving turns failed (attempt %d): %s: %s", attempt, type(e).__name__, e)
                    await asyncio.sleep(1.0)

        self._spawn(save(), save=True)

    async def _retrieve(self, text: str):
        return await self.memory.retrieve(self.user_id, text)

    # --- lifecycle ---

    async def run(self) -> None:
        try:
            await self._start()
            self._spawn(self._ticker())
            await self._read_client()
        finally:
            await self._shutdown()

    async def _start(self) -> None:
        self.memory = await self.rt.memory.for_user(self.user_id)
        known = self.rt.registry.get(self.hello.session_id) if self.hello.session_id else None
        if known is not None and known.user_id == self.user_id:
            self.convo = known
        else:
            now = datetime.now(timezone.utc)
            try:
                opened = await self.memory.open_session(self.user_id, self.hello.mode, self.hello.tz, self.hello.session_id, now)
            except Exception as e:  # talk anyway, without memory
                logger.warning("opening the session failed, memory off: %s: %s", type(e).__name__, e)
                self.memory = NoMemory(f"session open failed ({type(e).__name__})")
                opened = await self.memory.open_session(self.user_id, self.hello.mode, self.hello.tz, None, now)
            session_id, background, turns = opened
            self.convo = Conversation(session_id, self.user_id, self.hello.mode, self.hello.tz, background, turns)
            self.rt.registry.put(self.convo)
        resumed = bool(self.convo.turns)
        await self._connect_stt()
        if self._speaks:
            self._spawn(self.rt.tts.warm())
        await self._send({
            "type": "ready",
            "session_id": self.convo.session_id,
            "voice": self.stt is not None,
            "memory": self.memory.status,
            "model": self.rt.voice.groq_chat_model,
            "resumed": resumed,
        })
        if self.stt is None and self.rt.connect_stt is not None:
            await self._send({"type": "voice", "available": False, "reason": "stt_unavailable"})
        if not resumed:
            await self._start_reply(None, "opening", speech_end=None)

    async def _shutdown(self) -> None:
        self._closing = True
        self._socket_open = False
        reply = self.reply
        if reply is not None and not reply.finished:
            if reply.task is not None:
                reply.task.cancel()
            await self._finish(reply, "interrupted")
        for task in list(self._tasks):
            task.cancel()
        stt, self.stt = self.stt, None
        if stt is not None:
            try:
                await stt.close()
            except Exception:
                pass
        if self.convo is not None and self.memory is not None:
            try:
                await self.memory.close_session(self.convo.session_id, self.user_id, datetime.now(timezone.utc))
            except Exception as e:
                logger.warning("closing the session failed: %s: %s", type(e).__name__, e)
        if self._saves:
            await asyncio.wait(list(self._saves), timeout=5)

    # --- speech-to-text ---

    async def _connect_stt(self) -> None:
        if self.rt.connect_stt is None or self._closing:
            return
        try:
            stt = await self.rt.connect_stt()
        except Exception as e:
            logger.warning("speech-to-text connection failed: %s: %s", type(e).__name__, e)
            self._schedule_stt_retry()
            return
        self.stt = stt
        self.timeline.reset()
        self.vad = EnergyVad(self.rt.tuning.vad_silence_ms)
        self._last_speech = self._last_audio = clock.now()
        self._spawn(self._read_stt(stt))

    def _schedule_stt_retry(self) -> None:
        if self._stt_retry is not None and not self._stt_retry.done():
            return

        async def retry():
            for delay in STT_RETRY_S:
                await asyncio.sleep(delay)
                if self._closing or self.stt is not None:
                    return
                await self._connect_stt()
                if self.stt is not None:
                    await self._send({"type": "voice", "available": True})
                    return

        self._stt_retry = self._spawn(retry())

    async def _read_stt(self, stt: Stt) -> None:
        try:
            async for event in stt.events():
                await self._on_stt(event)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("speech-to-text stream failed: %s: %s", type(e).__name__, e)
        if self.stt is stt and not self._closing:
            self.stt = None
            await self._send({"type": "voice", "available": False, "reason": "stt_closed"})
            self._schedule_stt_retry()

    async def _close_stt(self, reason: str) -> None:
        stt, self.stt = self.stt, None
        if stt is not None:
            self._spawn(stt.close())
            await self._send({"type": "voice", "available": False, "reason": reason})

    async def _on_audio(self, pcm: bytes) -> None:
        if not self.mic_on or self.stt is None:
            return
        now = clock.now()
        self.timeline.add(len(pcm), now)
        self._last_audio = now
        try:
            await self.stt.send(pcm)
        except Exception:
            return  # the reader notices the closed stream
        heard = self.vad.push(pcm)
        if heard == "start":
            self._last_speech = now
            self.endpoint.speech_resumed()
            # Deepgram's words lag about a second behind the audio, so resumed speech is the
            # first sign the user hadn't finished. Wren hasn't said anything yet: start over.
            if self.state == "thinking" and self.reply is not None:
                await self._continue(self.reply)
        elif heard == "end":
            try:
                await self.stt.finalize()
            except Exception:
                pass
            if self._prefix and not self.endpoint.has_words():
                self._prefix_deadline = now + PREFIX_GRACE_S

    async def _on_stt(self, event: SttEvent) -> None:
        now = clock.now()
        if isinstance(event, SpeechStarted):
            self._last_speech = now
            return
        if isinstance(event, UtteranceEnd):
            if commit := self.endpoint.on_utterance_end(event, now):
                await self._commit(commit)
            return
        assert isinstance(event, Transcript)
        text = event.text.strip()
        reply = self.reply
        if text and self.state in ("listening", "thinking") and now < self._recent_wren_until:
            if is_echo(text, self._recent_wren):
                return  # Nova-3 catching up on the tail of Wren's last reply
        if text:
            self._last_speech = now
            if reply is not None and self.state in ("speaking", "paused"):
                if not is_interruption(text, reply.full_text()):
                    return  # echo of Wren, or a backchannel: never part of the user's turn
                await self._interrupt(reply)
            elif reply is not None and self.state == "thinking" and real_words(text):
                await self._continue(reply)
        commit = self.endpoint.on_transcript(event, now)
        if commit is not None:
            await self._commit(commit)
        elif text:
            await self._send({"type": "stt", "text": self.endpoint.caption(), "final": event.is_final})

    async def _commit(self, commit: Commit) -> None:
        text = f"{self._prefix} {commit.text}".strip()
        self._prefix, self._prefix_speech_end, self._prefix_deadline = "", None, None
        await self._start_reply(text, "voice", speech_end=self.timeline.arrival_of(commit.speech_end))

    # --- replies ---

    async def _start_reply(self, user_text: str | None, kind: str, speech_end: float | None) -> None:
        if self.reply is not None and not self.reply.finished:
            await self._interrupt(self.reply)
        self._turn = self._turn % 65535 + 1
        timing = TurnTiming(turn=self._turn, mode=self.convo.mode, input=kind, speech_end=speech_end, committed=clock.now())
        reply = Reply(self._turn, user_text, timing)
        self.reply, self.state = reply, "thinking"
        if user_text is not None:
            await self._send({"type": "user_turn", "turn": reply.no, "text": user_text})
        if self._speaks:
            self._spawn(self.rt.tts.warm())  # keeps the Deepgram connection hot while retrieval and the LLM run
        reply.task = asyncio.create_task(self._generate(reply))

    async def _generate(self, reply: Reply) -> None:
        try:
            async with self.convo.lock:
                async for text in run_turn(
                    self.convo, reply.user_text, retrieve=self._retrieve, chat=self.rt.chat,
                    timing=reply.timing, tuning=self.rt.tuning,
                ):
                    seg = Segment(len(reply.segments), text)
                    reply.segments.append(seg)
                    await self._send({"type": "segment", "turn": reply.no, "seg": seg.index, "text": text})
                    await self._speak(reply, seg)
            reply.llm_done = reply.end_sent = True
            await self._send({"type": "reply_end", "turn": reply.no, "outcome": "complete"})
            await self._maybe_finish(reply)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("reply failed: %s", e)
            await self._send({"type": "error", "code": "reply_failed", "message": REPLY_FAILED})
            await self._finish(reply, "failed")

    async def _speak(self, reply: Reply, seg: Segment) -> None:
        if self._speaks:
            try:
                async for pcm in self.rt.tts.stream(seg.text):
                    if reply.timing.tts_first_byte is None:
                        reply.timing.tts_first_byte = clock.now()
                    await self._send(pack_audio(reply.no, seg.index, pcm))
                    seg.samples += len(pcm) // 2
                    if reply.timing.first_audio_sent is None:
                        reply.timing.first_audio_sent = clock.now()
                        if self.state == "thinking":
                            self.state = "speaking"
                        await self._send({"type": "metrics", **reply.timing.record()})
            except asyncio.CancelledError:
                raise
            except Exception as e:  # the text still reaches the browser; Wren just doesn't say it
                logger.warning("text-to-speech failed for a segment: %s: %s", type(e).__name__, e)
        if seg.samples == 0:
            seg.played = True
        await self._send({"type": "segment_end", "turn": reply.no, "seg": seg.index, "samples": seg.samples})

    async def _maybe_finish(self, reply: Reply) -> None:
        if reply.llm_done and all(s.played for s in reply.segments):
            await self._finish(reply, "complete")

    async def _interrupt(self, reply: Reply) -> None:
        if reply.finished:
            return
        if reply.task is not None and not reply.task.done():
            reply.task.cancel()
        await self._send({"type": "interrupt", "turn": reply.no})
        await self._finish(reply, "interrupted")

    async def _continue(self, reply: Reply) -> None:
        """The user kept talking before Wren said anything: drop the reply, keep their words."""
        if reply.finished:
            return
        if reply.task is not None and not reply.task.done():
            reply.task.cancel()
        self._prefix = f"{self._prefix} {reply.user_text or ''}".strip()
        if self._prefix_speech_end is None:
            self._prefix_speech_end = reply.timing.speech_end
        await self._finish(reply, "continued")

    async def _finish(self, reply: Reply, outcome: str) -> None:
        if reply.finished:
            return
        reply.finished = True
        timing = reply.timing
        timing.outcome = outcome
        if outcome == "continued":
            timing.barge_in_ms = None
        records = []
        if outcome != "continued":
            if reply.user_text is not None:
                records.append(self.convo.add("user", reply.user_text).record())
            full = reply.full_text()
            heard = full if outcome == "complete" else reply.heard_text(clock.now())
            if heard:
                wren = self.convo.add("wren", heard, interrupted=heard != full).record()
                wren["metrics"] = timing.record()
                records.append(wren)
        if records:
            self._persist(records)
        if outcome in ("complete", "interrupted") and reply.segments:
            self._recent_wren = reply.full_text()
            self._recent_wren_until = clock.now() + self.rt.tuning.echo_tail_s
        log_turn(self.convo.session_id, timing)
        record = timing.record()
        if self.rt.turn_log is not None:
            self._spawn(self.rt.turn_log.write(self.convo.session_id, record), save=True)
        await self._send({"type": "metrics", **record})
        if not reply.end_sent:
            await self._send({"type": "reply_end", "turn": reply.no, "outcome": outcome})
        if self.reply is reply:
            self.reply, self.state = None, "listening"

    # --- the browser ---

    async def _read_client(self) -> None:
        while True:
            try:
                message = await self.ws.receive()
            except Exception:  # the socket is already gone
                return
            if message["type"] == "websocket.disconnect":
                return
            if message.get("bytes") is not None:
                await self._on_audio(message["bytes"])
                continue
            if message.get("text") is None:
                continue
            try:
                msg = parse_client(message["text"])
            except ValueError as e:
                logger.info("ignoring a bad client message: %s", e)
                continue
            if isinstance(msg, UserText):
                await self._on_user_text(msg.text)
            elif isinstance(msg, Playback):
                await self._on_playback(msg)
            elif isinstance(msg, BargeIn):
                await self._on_barge_in(msg)
            elif isinstance(msg, Mic):
                await self._on_mic(msg.on)
            elif isinstance(msg, End):
                return

    async def _on_user_text(self, text: str) -> None:
        reply = self.reply
        if reply is not None and not reply.finished:
            if self.state == "thinking":
                await self._continue(reply)
            else:
                await self._interrupt(reply)
        self.endpoint.clear()
        text = f"{self._prefix} {text}".strip()
        self._prefix, self._prefix_speech_end, self._prefix_deadline = "", None, None
        await self._start_reply(text, "text", speech_end=None)

    async def _on_playback(self, msg: Playback) -> None:
        reply = self.reply
        if reply is None or msg.turn != reply.no or msg.seg >= len(reply.segments):
            return
        seg = reply.segments[msg.seg]
        if msg.event == "start":
            seg.started_at = seg.started_at or clock.now()
        else:
            seg.played = True
            await self._maybe_finish(reply)

    async def _on_barge_in(self, msg: BargeIn) -> None:
        reply = self.reply
        if reply is not None and msg.turn == reply.no and self.state in ("speaking", "paused"):
            if self.state == "speaking":  # when already paused, it's the same pause reported twice
                self.state, self._paused_at = "paused", clock.now()
                reply.pause = (msg.seg, msg.played_ms)
                reply.timing.barge_in_ms = msg.onset_ms
            return
        # The browser paused a reply this server isn't playing: it finished, or it's another turn.
        # Nothing here would ever end that pause, so let the browser carry on.
        await self._send({"type": "resume", "turn": msg.turn})

    async def _on_mic(self, on: bool) -> None:
        self.mic_on = on
        if on and self.stt is None and not self._closing:
            await self._connect_stt()
            if self.stt is not None:
                await self._send({"type": "voice", "available": True})

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            try:
                if await self._tick():
                    return
            except Exception:  # one bad tick mustn't stop holds, resumes and idle checks for good
                logger.exception("voice session tick failed")

    async def _tick(self) -> bool:
        """One pass of the timers. Returns True when the session is over."""
        tuning = self.rt.tuning
        now = clock.now()
        if commit := self.endpoint.poll(now):
            await self._commit(commit)
        if self._prefix_deadline is not None and now >= self._prefix_deadline:
            self._prefix_deadline = None
            if self._prefix and not self.endpoint.has_words():  # it wasn't more words after all
                text, speech_end = self._prefix, self._prefix_speech_end
                self._prefix, self._prefix_speech_end = "", None
                await self._start_reply(text, "voice", speech_end=speech_end)
        reply = self.reply
        # Resume a pause only once the user has gone quiet: Deepgram's words lag the audio,
        # so someone still talking is still interrupting even before their words arrive.
        if (
            self.state == "paused" and reply is not None and not self.vad.speaking
            and now - self._paused_at >= tuning.confirm_window_s
        ):
            reply.paused_s += now - self._paused_at
            self.state, reply.pause, reply.timing.barge_in_ms = "speaking", None, None
            await self._send({"type": "resume", "turn": reply.no})
        # A browser that never reports the end of playback mustn't leave the turn speaking forever.
        # Time it spent paused doesn't count: the audio still had that much left to play.
        if (
            reply is not None and self.state == "speaking" and reply.llm_done
            and reply.timing.first_audio_sent is not None
            and now > reply.timing.first_audio_sent + sum(s.duration_ms() for s in reply.segments) / 1000
            + reply.paused_s + tuning.playback_grace_s
        ):
            logger.warning("turn %s: the browser never reported the end of playback; finishing it", reply.no)
            await self._finish(reply, "complete")
        if self.stt is not None:
            if self.state == "listening" and (not self.mic_on or now - self._last_speech >= tuning.idle_s):
                await self._close_stt("idle")
            elif now - self._last_audio >= tuning.keepalive_s and now - self._last_keepalive >= tuning.keepalive_s:
                self._last_keepalive = now
                try:
                    await self.stt.keep_alive()
                except Exception:
                    pass
        if now - self._started >= tuning.session_cap_s:
            await self._send({"type": "error", "code": "session_over", "message": SESSION_OVER})
            await self.ws.close(code=SESSION_OVER_CLOSE)
            return True
        return False
