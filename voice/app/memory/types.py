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
class FeedbackNote:
    """A current feedback memory, for the opener. at is when its session started, or when the memory
    was saved if that session is gone."""

    text: str
    at: datetime


@dataclass(frozen=True)
class SearchHit:
    id: str
    kind: str
    text: str
    source_turns: tuple[str, ...]
    score: float


@dataclass(frozen=True)
class Source:
    """Where a remembered item came from: its session's mode and start. When that session is gone,
    mode is None and at is when the item was saved."""

    mode: str | None
    at: datetime


@dataclass(frozen=True)
class KnownMemory:
    """A current memory, as the "What Wren knows" page shows it."""

    id: str
    kind: str
    text: str
    source: Source


@dataclass(frozen=True)
class InterviewFields:
    """What the owner can correct on a pipeline row. Dates and times are local to the user."""

    company: str
    role: str | None
    stage: str
    next_date: date | None
    next_time: time | None
    notes: str | None


@dataclass(frozen=True)
class KnownInterview:
    """A current pipeline row, with the date and time of each version it replaced, newest first."""

    id: str
    fields: InterviewFields
    source: Source
    moved_from: tuple[tuple[date | None, time | None], ...]


@dataclass(frozen=True)
class Knows:
    """Everything the "What Wren knows" page shows. timezone is the profile's, or UTC with no profile."""

    timezone: str
    profile: Profile | None
    pipeline: tuple[KnownInterview, ...]
    memories: tuple[KnownMemory, ...]  # current only, newest first
