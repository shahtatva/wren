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

In a mock interview, run_turn says who speaks each segment. A segment spoken in role
reaches the page with "role": "interviewer", and a "mock" message goes out before each
line that hands over the floor. A reply that changes speaker saves as one turn per
speaker, and the user's words belong to the mock if the interviewer had the floor. A
switch counts only if the user heard at least part of its line. On a live socket, a
reply cut before that line, by a barge-in, the user still talking or a failure, gives
the floor back and tells the page. It undoes only its own switch, and not once the floor
has moved on or another turn has been saved since. A socket that closes undoes nothing:
its page already showed the switch's words, and POST /turn or a reconnect may be using
that floor without having saved a turn yet. Stepping out for a crisis always stands,
heard or not: boundaries break role at once.

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

from app.conversation import Conversation, reply_runs
from app.memory.live import NoMemory
from app.mock import Mock, step_out_reason
from app.runtime import Runtime
from app.turn import run_turn
from app.voice import clock
from app.voice.bargein import is_echo, is_interruption, real_words
from app.voice.endpoint import Commit, EndOfTurn
from app.voice.events import SpeechStarted, SttEvent, Transcript, UtteranceEnd
from app.voice.metrics import TurnTiming, log_turn
from app.voice.protocol import BargeIn, End, Hello, Mic, Playback, UserText, mock_message, pack_audio, parse_client
from app.voice.stt import Stt
from app.voice.timeline import AudioTimeline
from app.voice.vad import BYTES_PER_MS, ROOM_TRUST_FRAMES, ClearVoice, EnergyVad, RoomTalk, rms

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
    mock: int | None = None  # the mock whose interviewer speaks it, or None for Wren
    switch: bool = False  # the line that hands over the floor
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
    mock_before: Mock | None  # the conversation's mock as the turn starts, read again under the lock
    switched_to: Mock | None = None  # the mock this reply's own switch line put on the floor
    turns_at_switch: int | None = None  # the conversation's saved turns at that switch
    segments: list[Segment] = field(default_factory=list)
    task: asyncio.Task | None = None
    llm_done: bool = False
    end_sent: bool = False
    finished: bool = False
    pause: tuple[int, float] | None = None  # (segment, played ms) where the browser paused
    paused_s: float = 0.0  # total time the browser held this reply paused

    def full_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    def heard(self, now: float) -> list[tuple[Segment, str]]:
        """Each segment the user heard, with the words of it they heard: the fully played segments,
        then the share of the cut segment that played, if a word of it did. Aura-2 gives no word
        timings, so the cut is proportional to playback time, rounded down."""
        heard: list[tuple[Segment, str]] = []
        for seg in self.segments:
            if seg.played:
                heard.append((seg, seg.text))
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
            if cut := words[: int(len(words) * fraction)]:
                heard.append((seg, " ".join(cut)))
            break
        return heard

    def heard_text(self, now: float) -> str:
        return " ".join(words for _, words in self.heard(now))


class VoiceSession:
    def __init__(self, ws: WebSocket, runtime: Runtime, user_id: UUID, hello: Hello):
        self.ws, self.rt, self.user_id, self.hello = ws, runtime, user_id, hello
        tuning = runtime.tuning
        self.endpoint = EndOfTurn(tuning.hold_s(hello.mode))
        self.vad = EnergyVad(tuning.vad_silence_ms)
        # Other people talking nearby mustn't stop Wren (docs/superpowers/specs/2026-11-06-room-noise-design.md):
        # how loud the room's talkers get, and when the owner's own voice was last heard over them.
        self.room = RoomTalk()
        self.you = ClearVoice()
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
        self.endpoint.hold_s = self.rt.tuning.hold_s(self.convo.turn_mode)  # a reconnect can land mid-mock
        self.room = self.convo.room  # a reconnect mustn't forget what the room sounds like
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
        if self.convo.mock is not None and self.convo.mock.on:  # the interviewer still has the floor
            await self._send(mock_message(self.convo.mock))
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
            # The page already showed the switch's words, and POST /turn or a reconnect may be using that
            # floor without having saved a turn yet, so a closing socket undoes no switch.
            await self._finish(reply, "interrupted", undo=False)
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

    async def _to_stt(self, stt: Stt, send) -> bool:
        """Awaits one send to Deepgram, with a deadline. False if it didn't go through.

        A send that stalls would freeze whichever loop awaits it (the socket reader or the
        ticker), so after `stt_send_timeout_s` speech-to-text is dropped like a broken connection.
        """
        try:
            await asyncio.wait_for(send, self.rt.tuning.stt_send_timeout_s)
            return True
        except TimeoutError:
            logger.warning("a send to speech-to-text stalled (%s); dropping the connection", send.__qualname__)
            if self.stt is stt and not self._closing:
                await self._close_stt("stt_stalled")
                self._schedule_stt_retry()
            return False
        except Exception:
            return False  # the reader notices the closed stream

    def _room_only(self) -> bool:
        """The owner is normally quiet now: Wren is speaking, or preparing its opening."""
        reply = self.reply
        return self.state == "speaking" or (self.state == "thinking" and reply is not None and reply.user_text is None)

    def _heard_you(self, now: float) -> bool:
        """The owner's own voice was heard recently enough to be behind words arriving now."""
        return self.you.heard_within(now, self.rt.tuning.heard_you_s)

    async def _on_audio(self, pcm: bytes) -> None:
        if not self.mic_on or self.stt is None:
            return
        now = clock.now()
        self.timeline.add(len(pcm), now)
        self._last_audio = now
        if not await self._to_stt(self.stt, self.stt.send(pcm)):
            return
        level = rms(pcm)
        room_only = self._room_only()
        if room_only:
            self.room.observe(level)
        # Until the room has been heard for a while, a moment the owner is normally quiet in stays
        # theirs to be quiet in: otherwise talk that starts early would count as their voice.
        clear = self.room.clearly_you(level) and not (room_only and self.room.frames < ROOM_TRUST_FRAMES)
        self.you.push(clear, now, len(pcm) / BYTES_PER_MS)
        # Deepgram's words lag about a second behind the audio, so resumed voice is the first sign
        # the user hadn't finished. Wren hasn't said anything yet: start over. Only the owner's own
        # voice counts, held long enough: not the room's talkers, and not a cough. The opening
        # answers nobody, so it isn't cancelled this way; real words still do (_on_stt).
        if (
            self.you.last_at == now and self.state == "thinking"
            and self.reply is not None and self.reply.user_text is not None
        ):
            await self._continue(self.reply)
        heard = self.vad.push(pcm)
        if heard == "start":
            self._last_speech = now
            self.endpoint.speech_resumed()
        elif heard == "end":
            if (stt := self.stt) is not None:
                await self._to_stt(stt, stt.finalize())
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
                if self.state == "speaking" and step_out_reason(text) != "crisis":
                    # Only the page's pause lets words in while Wren speaks: it heard voice clearly
                    # louder than the room's talkers. Without one, these are the room's. A crisis
                    # always gets through: it must never be dropped (CLAUDE.md, Wren's boundaries).
                    logger.info("ignored words while Wren spoke: no pause from the page")
                    return
                await self._interrupt(reply)
            elif reply is not None and self.state == "thinking" and real_words(text):
                if not self._heard_you(now):
                    logger.info("ignored words while Wren prepared a reply: not the owner's voice")
                    return  # the room's talkers, not the owner going on
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
        # Provisional: _generate reads the mode and the mock again under the conversation lock, since a
        # POST /turn on this session can move the floor first. Only a reply that never gets the lock keeps them.
        timing = TurnTiming(turn=self._turn, mode=self.convo.turn_mode, input=kind, speech_end=speech_end, committed=clock.now())
        reply = Reply(self._turn, user_text, timing, mock_before=self.convo.mock)
        self.reply, self.state = reply, "thinking"
        if user_text is not None:
            await self._send({"type": "user_turn", "turn": reply.no, "text": user_text})
        if self._speaks:
            self._spawn(self.rt.tts.warm())  # keeps the Deepgram connection hot while retrieval and the LLM run
        reply.task = asyncio.create_task(self._generate(reply))

    async def _generate(self, reply: Reply) -> None:
        try:
            async with self.convo.lock:
                reply.mock_before, reply.timing.mode = self.convo.mock, self.convo.turn_mode
                async for spoken in run_turn(
                    self.convo, reply.user_text, retrieve=self._retrieve, chat=self.rt.chat,
                    timing=reply.timing, tuning=self.rt.tuning,
                ):
                    if spoken.switch:  # run_turn handed over the floor in this same step: this is its mock
                        reply.switched_to, reply.turns_at_switch = self.convo.mock, len(self.convo.turns)
                        await self._send(mock_message(reply.switched_to))
                    seg = Segment(len(reply.segments), spoken, mock=spoken.mock, switch=spoken.switch)
                    reply.segments.append(seg)
                    message = {"type": "segment", "turn": reply.no, "seg": seg.index, "text": spoken}
                    if seg.mock is not None:
                        message["role"] = "interviewer"
                    await self._send(message)
                    await self._speak(reply, seg)
            reply.llm_done = reply.end_sent = True
            await self._send({"type": "reply_end", "turn": reply.no, "outcome": "complete"})
            await self._maybe_finish(reply)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("reply failed: %s", e)
            await self._finish(reply, "failed", first={"type": "error", "code": "reply_failed", "message": REPLY_FAILED})

    async def _speak(self, reply: Reply, seg: Segment) -> None:
        if self._speaks:
            try:
                async for pcm in self.rt.tts.stream(seg.text, in_role=seg.mock is not None):
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
        await self._finish(reply, "interrupted", first={"type": "interrupt", "turn": reply.no})

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

    async def _finish(self, reply: Reply, outcome: str, *, first: dict | None = None, undo: bool = True) -> None:
        """End the turn. Everything up to the first await settles it: what the user heard, the undo, the
        saved turns and the hold. So any turn that starts after this one ends, on the socket or by POST
        /turn, starts from who really has the floor. Then the browser hears first (the interrupt or the
        error), mock if a switch was undone, the metrics and reply_end. undo=False keeps any switch: the
        closing socket passes it."""
        if reply.finished:
            return
        reply.finished = True
        timing = reply.timing
        timing.outcome = outcome
        if outcome == "continued":
            timing.barge_in_ms = None
        if outcome == "complete":
            heard = [(seg, seg.text) for seg in reply.segments]
        elif outcome == "continued":  # dropped: their words carry on into their next turn
            heard = []
        else:
            heard = reply.heard(clock.now())
        # A switch counts only if the user heard at least part of its line. On a live socket, a reply cut
        # before that line undoes its own switch, unless the floor has moved on or a turn has been saved
        # since. This reply's turns are saved below, so a turn saved since is another writer's, which may
        # build on the switch. A writer still in flight hasn't saved yet, so a closing socket (undo=False)
        # undoes nothing. Stepping out for a crisis stands even unheard: boundaries break role at once.
        switched = reply.switched_to
        undone = (
            undo and switched is not None and self.convo.mock is switched
            and len(self.convo.turns) == reply.turns_at_switch
            and switched.last_step_out != "crisis" and not any(seg.switch for seg, _ in heard)
        )
        if undone:
            self.convo.mock = reply.mock_before
            timing.switch = f"{timing.switch}:undone"  # the turn log shows it was tried, not that it happened
        if outcome != "continued":
            self._save_turn(reply, heard)
        self.endpoint.hold_s = self.rt.tuning.hold_s(self.convo.turn_mode)
        if outcome in ("complete", "interrupted") and reply.segments:
            self._recent_wren = reply.full_text()
            self._recent_wren_until = clock.now() + self.rt.tuning.echo_tail_s
        log_turn(self.convo.session_id, timing)
        record = timing.record()
        if self.rt.turn_log is not None:
            self._spawn(self.rt.turn_log.write(self.convo.session_id, record), save=True)
        if first is not None:
            await self._send(first)
        if undone:
            await self._send(mock_message(self.convo.mock))
        await self._send({"type": "metrics", **record})
        if not reply.end_sent:
            await self._send({"type": "reply_end", "turn": reply.no, "outcome": outcome})
        if self.reply is reply:
            self.reply, self.state = None, "listening"

    def _save_turn(self, reply: Reply, heard: list[tuple[Segment, str]]) -> None:
        """Add the turn to the conversation and save it: the user's words, then Wren's as heard, one
        turn per speaker. Only the last can be cut off, and the first carries the turn's metrics."""
        before = reply.mock_before
        records = []
        if reply.user_text is not None:  # their words belong to the mock if the interviewer had the floor
            theirs = before.id if before is not None and before.on else None
            records.append(self.convo.add("user", reply.user_text, mock=theirs).record())
        runs = reply_runs((words, seg.mock) for seg, words in heard)
        cut = " ".join(words for _, words in heard) != reply.full_text()
        for i, (text, mock) in enumerate(runs):
            last = i == len(runs) - 1
            wren = self.convo.add("wren", text, interrupted=cut and last, mock=mock, in_role=mock is not None).record()
            if i == 0:
                wren["metrics"] = reply.timing.record()
            records.append(wren)
        if records:
            self._persist(records)

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
        # so someone still talking is still interrupting even before their words arrive. Only the
        # owner's own voice holds a pause, not the room's talkers, and never past the cap without words.
        paused_for = now - self._paused_at
        if (
            self.state == "paused" and reply is not None and paused_for >= tuning.confirm_window_s
            and (not self.you.clear_within(now, tuning.vad_silence_ms / 1000) or paused_for >= tuning.pause_cap_s)
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
                await self._to_stt(self.stt, self.stt.keep_alive())
        if now - self._started >= tuning.session_cap_s:
            await self._send({"type": "error", "code": "session_over", "message": SESSION_OVER})
            await self.ws.close(code=SESSION_OVER_CLOSE)
            return True
        return False
