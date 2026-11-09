"""A live conversation: who it's with, what was said, and what loads once per session.

The voice socket, POST /turn and evals all hold one of these. Wren's turns carry only
the words the user heard, so after an interruption the next prompt shows where Wren was
cut off. A turn can belong to a mock interview, and Wren's turns can be spoken in role as
the interviewer. Both are saved with the turn, so a resumed session still knows which
lines were role-play. The registry keeps conversations alive briefly between reconnects,
which also lets a session resume when memory (the database) is off.
"""

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.memory.types import PipelineEntry, Profile, SearchHit
from app.mock import Mock
from app.voice.vad import RoomTalk


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
    mock: int | None = None  # the id of the mock interview it belongs to
    in_role: bool = False  # Wren said it as the interviewer

    def record(self) -> dict:
        """The transcript entry: {id, speaker, text}, the shape extraction reads, plus
        interrupted, mock (the id) and as ("interviewer") when they're set."""
        record = {"id": self.id, "speaker": self.speaker, "text": self.text}
        if self.interrupted:
            record["interrupted"] = True
        if self.mock is not None:
            record["mock"] = self.mock
        if self.in_role:
            record["as"] = "interviewer"
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
    mock: Mock | None = None  # the mock in progress, or the session's last one
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)  # one turn at a time, socket or HTTP
    last_active: float = field(default_factory=time.monotonic)
    room: RoomTalk = field(default_factory=RoomTalk)  # how loud the room's talkers get: kept across reconnects

    @property
    def turn_mode(self) -> str:
        """The mode this turn runs in: "mock" while the interviewer has the floor, else the session's."""
        return "mock" if self.mock is not None and self.mock.on else self.mode

    def next_turn_id(self) -> str:
        return f"{self.session_id}:t{len(self.turns) + 1:02d}"

    def next_mock_id(self) -> int:
        """One past the highest mock id in the turns or in the mock in progress."""
        ids = [turn.mock for turn in self.turns if turn.mock is not None]
        if self.mock is not None:
            ids.append(self.mock.id)
        return max(ids, default=0) + 1

    def add(
        self, speaker: Literal["user", "wren"], text: str, interrupted: bool = False, *,
        mock: int | None = None, in_role: bool = False,
    ) -> HistoryTurn:
        turn = HistoryTurn(self.next_turn_id(), speaker, text, interrupted, mock=mock, in_role=in_role)
        self.turns.append(turn)
        self.last_active = time.monotonic()
        return turn


def reply_runs(parts: Iterable[tuple[str, int | None]]) -> list[tuple[str, int | None]]:
    """Join consecutive (text, mock id) parts that share a mock id, with spaces. A reply that
    changes speaker saves as one turn per run."""
    runs: list[tuple[str, int | None]] = []
    for text, mock in parts:
        if runs and runs[-1][1] == mock:
            runs[-1] = (f"{runs[-1][0]} {text}", mock)
        else:
            runs.append((text, mock))
    return runs


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
