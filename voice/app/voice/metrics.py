"""Per-turn timings: one log line per turn, the record saved with the transcript, and the
numbers the browser's debug panel shows.

Time to first audio (TTFA) runs from the end of the user's speech to the first audio
byte sent to the browser. Every stage between them is timed too, so a slow turn says
where the time went. All times are clock.now() values; the record turns them into
milliseconds and UTC timestamps.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.voice import clock

logger = logging.getLogger(__name__)

TTFA_WARN_MS = 1500  # the spec's p95 target


def _ms(start: float | None, end: float | None) -> int | None:
    if start is None or end is None:
        return None
    return round((end - start) * 1000)


@dataclass
class TurnTiming:
    turn: int
    mode: str
    input: Literal["voice", "text", "opening"]
    speech_end: float | None = None  # the user's last word, mapped to server time
    committed: float | None = None  # the turn was handed to run_turn
    retrieval_start: float | None = None
    retrieval_end: float | None = None
    llm_start: float | None = None
    llm_first_token: float | None = None
    first_segment: float | None = None  # first speakable sentence ready
    tts_first_byte: float | None = None
    first_audio_sent: float | None = None
    memories: int = 0
    retrieval_fallback: str | None = None
    model: str | None = None
    chat_fallback: str | None = None
    context_tokens: int | None = None
    outcome: str | None = None  # complete, interrupted, continued, failed
    barge_in_ms: float | None = None  # the browser's onset-to-pause time, when interrupted
    reply_repair: str | None = None  # dropped_tail, dropped_stacked, dropped_claim, retried or gave_up: see run_turn
    finish_reason: str | None = None  # why the model's stream ended: "length" means the token cap
    completion_tokens: int | None = None  # reasoning plus reply, as the provider counted them
    switch: str | None = None  # mock_start, mock_resume or step_out:<why>; ":undone" follows if nobody heard it

    def ttfa_ms(self) -> int | None:
        return _ms(self.speech_end, self.first_audio_sent)

    def record(self) -> dict:
        return {
            "turn": self.turn,
            "mode": self.mode,
            "input": self.input,
            "outcome": self.outcome,
            "ttfa_ms": self.ttfa_ms(),
            "endpoint_ms": _ms(self.speech_end, self.committed),
            "retrieval_ms": _ms(self.retrieval_start, self.retrieval_end),
            "llm_ttft_ms": _ms(self.llm_start, self.llm_first_token),
            "segment_ms": _ms(self.llm_first_token, self.first_segment),
            "tts_ttfb_ms": _ms(self.first_segment, self.tts_first_byte),
            "turn_to_audio_ms": _ms(self.committed, self.first_audio_sent),
            "speech_end_at": clock.iso(self.speech_end),
            "committed_at": clock.iso(self.committed),
            "first_token_at": clock.iso(self.llm_first_token),
            "first_audio_at": clock.iso(self.first_audio_sent),
            "memories": self.memories,
            "retrieval_fallback": self.retrieval_fallback,
            "model": self.model,
            "chat_fallback": self.chat_fallback,
            "context_tokens": self.context_tokens,
            "barge_in_ms": None if self.barge_in_ms is None else round(self.barge_in_ms),
            "reply_repair": self.reply_repair,
            "finish_reason": self.finish_reason,
            "completion_tokens": self.completion_tokens,
            "switch": self.switch,
        }


def log_turn(session_id: str, timing: TurnTiming) -> None:
    r = timing.record()
    ttfa = r["ttfa_ms"]
    level = logging.WARNING if ttfa is not None and ttfa > TTFA_WARN_MS else logging.INFO

    def v(key: str) -> object:
        return "-" if r[key] is None else r[key]

    logger.log(
        level,
        "turn session=%s turn=%s mode=%s input=%s outcome=%s switch=%s ttfa_ms=%s endpoint_ms=%s retrieval_ms=%s "
        "llm_ttft_ms=%s segment_ms=%s tts_ttfb_ms=%s turn_to_audio_ms=%s memories=%s context_tokens=%s "
        "model=%s fallback=%s barge_in_ms=%s speech_end_at=%s first_token_at=%s first_audio_at=%s",
        session_id, r["turn"], r["mode"], r["input"], v("outcome"), v("switch"), v("ttfa_ms"), v("endpoint_ms"),
        v("retrieval_ms"), v("llm_ttft_ms"), v("segment_ms"), v("tts_ttfb_ms"), v("turn_to_audio_ms"),
        r["memories"], v("context_tokens"), v("model"),
        r["chat_fallback"] or r["retrieval_fallback"] or "-",
        v("barge_in_ms"), v("speech_end_at"), v("first_token_at"), v("first_audio_at"),
    )


class TurnLog:
    """Appends each turn's record to a JSON-lines file, so time-to-first-audio percentiles
    can be reported even when the database isn't set up. voice/logs/ is git-ignored."""

    def __init__(self, path: Path):
        self._path = path

    def _append(self, line: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def write(self, session_id: str, record: dict) -> None:
        await asyncio.to_thread(self._append, json.dumps({"session": session_id, **record}))
