"""A live conversation: who it's with, what was said, and what loads once per session.

The voice socket, POST /turn and evals all hold one of these. Wren's turns carry only
the words the user heard, so after an interruption the next prompt shows where Wren was
cut off. The registry keeps conversations alive briefly between reconnects, which also
lets a session resume when memory (the database) is off.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.memory.types import PipelineEntry, Profile, SearchHit


@dataclass(frozen=True)
class Background:
    profile: Profile | None = None
    pipeline: tuple[PipelineEntry, ...] = ()
    last_session_at: datetime | None = None


@dataclass
class HistoryTurn:
    id: str
    speaker: Literal["user", "wren"]
    text: str
    interrupted: bool = False

    def record(self) -> dict:
        """The transcript entry: {id, speaker, text}, the shape extraction reads."""
        record = {"id": self.id, "speaker": self.speaker, "text": self.text}
        if self.interrupted:
            record["interrupted"] = True
        return record


@dataclass(frozen=True)
class Retrieved:
    hits: list[SearchHit]
    ms: float
    fallback: str | None = None  # why the raw message was searched, when the rewrite failed


@dataclass
class Conversation:
    session_id: str
    user_id: UUID
    mode: str  # "checkin" or "mock"
    tz: str  # IANA zone for time of day and pipeline dates
    background: Background = field(default_factory=Background)
    turns: list[HistoryTurn] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one turn at a time, socket or HTTP
    last_active: float = field(default_factory=time.monotonic)

    def next_turn_id(self) -> str:
        return f"{self.session_id}:t{len(self.turns) + 1:02d}"

    def add(self, speaker: Literal["user", "wren"], text: str, interrupted: bool = False) -> HistoryTurn:
        turn = HistoryTurn(self.next_turn_id(), speaker, text, interrupted)
        self.turns.append(turn)
        self.last_active = time.monotonic()
        return turn


class ConversationRegistry:
    def __init__(self, max_idle_s: float = 1800.0):
        self._max_idle_s = max_idle_s
        self._conversations: dict[str, Conversation] = {}

    def get(self, session_id: str) -> Conversation | None:
        return self._conversations.get(session_id)

    def put(self, convo: Conversation) -> None:
        self._conversations[convo.session_id] = convo

    def evict_idle(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        for session_id, convo in list(self._conversations.items()):
            if now - convo.last_active > self._max_idle_s:
                del self._conversations[session_id]
