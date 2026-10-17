"""Turn a finished session into atomic memories and pipeline updates. Prompt: prompts/extract.md.

Runs after the session, never on the live path. Pipeline items record where each
application stands. An item that replaces an earlier one supersedes it: the old row
stays, with superseded_by set, so "it moved to Friday" keeps its history.
"""

import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.config import Settings
from app.memory.llm import generate_json
from app.memory.types import MEMORY_KINDS, ExtractedMemory, Extraction, PipelineItem, Session

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "extract.md"
SPEAKER_LABELS = {"user": "User", "wren": "Wren"}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME = re.compile(r"\d{2}:\d{2}")


class _Memory(BaseModel):
    kind: str
    text: str
    entities: list[str]
    importance: int
    source_turns: list[str]


class _PipelineItem(BaseModel):
    replaces: str
    company: str
    role: str
    stage: str
    next_date: str
    next_time: str
    notes: str
    source_turns: list[str]


class ExtractionResult(BaseModel):
    pipeline: list[_PipelineItem]
    memories: list[_Memory]


def prompt_text() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def _clean(text: str) -> str:
    return " ".join(text.split())


def format_transcript(session: Session) -> str:
    return "\n".join(f"[{t.id}] {SPEAKER_LABELS[t.speaker]}: {t.text}" for t in session.turns)


def pipeline_ref(index: int) -> str:
    return f"P{index + 1}"


def format_pipeline(current: list[PipelineItem]) -> str:
    if not current:
        return "(none yet)"
    lines = []
    for i, item in enumerate(current):
        when = " ".join(part for part in (item.next_date, item.next_time) if part)
        parts = [item.company, item.role or "role not given", item.stage, f"next: {when}" if when else "nothing scheduled"]
        if item.notes:
            parts.append(item.notes)
        lines.append(f"{pipeline_ref(i)}: " + " | ".join(parts))
    return "\n".join(lines)


def local_date(session: Session, timezone: str) -> date:
    return session.started_at.astimezone(ZoneInfo(timezone)).date()


def build_prompt(session: Session, pipeline: list[PipelineItem], timezone: str) -> str:
    day = local_date(session, timezone)
    return prompt_text().format(
        # The weekday lets the model resolve "this Friday" without working out the calendar.
        session_date=f"{day:%A}, {day.isoformat()}",
        timezone=timezone,
        mode=session.mode,
        pipeline=format_pipeline(pipeline),
        transcript=format_transcript(session),
    )


def parse_extraction(result: ExtractionResult, session: Session, pipeline: list[PipelineItem]) -> Extraction:
    """Keep only well-formed items that cite real turns of this session."""
    known = {t.id for t in session.turns}
    refs = {pipeline_ref(i) for i in range(len(pipeline))}

    def turns_of(item) -> tuple[str, ...]:
        return tuple(dict.fromkeys(t for t in item.source_turns if t in known))

    memories = []
    for item in result.memories:
        text, turns = _clean(item.text), turns_of(item)
        if item.kind not in MEMORY_KINDS or not text or not turns:
            continue
        entities = tuple(dict.fromkeys(e.strip() for e in item.entities if e.strip()))
        memories.append(ExtractedMemory(item.kind, text, entities, min(5, max(1, item.importance)), turns))

    updates = []
    for item in result.pipeline:
        company, stage, turns = _clean(item.company), _clean(item.stage), turns_of(item)
        if not company or not stage or not turns:
            continue
        updates.append(
            PipelineItem(
                company=company,
                role=_clean(item.role),
                stage=stage,
                next_date=item.next_date.strip() if DATE.fullmatch(item.next_date.strip()) else "",
                next_time=item.next_time.strip() if TIME.fullmatch(item.next_time.strip()) else "",
                notes=_clean(item.notes),
                source_turns=turns,
                replaces=item.replaces.strip() if item.replaces.strip() in refs else None,
            )
        )
    return Extraction(memories, updates)


def next_pipeline(current: list[PipelineItem], updates: list[PipelineItem]) -> list[PipelineItem]:
    """The pipeline after this session: replaced items drop out and new versions join at the end."""
    replaced = {u.replaces for u in updates if u.replaces}
    kept = [item for i, item in enumerate(current) if pipeline_ref(i) not in replaced]
    return kept + [replace(u, replaces=None) for u in updates]


def extract(session: Session, pipeline: list[PipelineItem], timezone: str, settings: Settings) -> Extraction:
    result = generate_json(build_prompt(session, pipeline, timezone), ExtractionResult, settings)
    return parse_extraction(result, session, pipeline)
