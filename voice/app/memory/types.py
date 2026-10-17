"""Plain data types shared by extraction, storage and retrieval."""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal

Speaker = Literal["user", "wren"]
MEMORY_KINDS = ("story", "feedback", "episode", "vibe")


@dataclass(frozen=True)
class Turn:
    id: str
    speaker: Speaker
    text: str


@dataclass(frozen=True)
class Session:
    id: str  # external id, e.g. "maya-s03" in evals
    started_at: datetime
    mode: str  # "checkin" or "mock"
    turns: tuple[Turn, ...]


@dataclass(frozen=True)
class ExtractedMemory:
    kind: str
    text: str
    entities: tuple[str, ...]
    importance: int
    source_turns: tuple[str, ...]


@dataclass(frozen=True)
class PipelineItem:
    """Where one application stands. Dates and times are local to the user's time zone."""

    company: str
    role: str
    stage: str
    next_date: str  # YYYY-MM-DD, or "" when nothing is scheduled
    next_time: str  # HH:MM, or ""
    notes: str
    source_turns: tuple[str, ...]
    replaces: str | None  # reference of the item this supersedes, e.g. "P2", as listed in the prompt


@dataclass(frozen=True)
class Extraction:
    memories: list[ExtractedMemory]
    pipeline: list[PipelineItem]


@dataclass(frozen=True)
class Profile:
    display_name: str | None
    summary: str
    target_roles: tuple[str, ...]
    timezone: str


@dataclass(frozen=True)
class PipelineEntry:
    """A current pipeline row, as /turn shows it. Dates and times are local to the user."""

    company: str
    role: str | None
    stage: str
    next_date: date | None
    next_time: time | None
    notes: str | None


@dataclass(frozen=True)
class SearchHit:
    id: str
    kind: str
    text: str
    source_turns: tuple[str, ...]
    score: float
