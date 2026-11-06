"""The opener: what Wren's first line leads with when a session starts. Design:
docs/superpowers/specs/2026-11-06-proactive-opener-design.md.

The code picks one lead (choose_lead) and the chat model words it, with that lead's facts in its prompt
(turn.py). If the model's opener misses its shape twice, Wren says the lead's fixed line (fixed_line).

The leads, first match wins:
- first: there's no earlier session. Wren says it's an AI, then asks about the search.
- how_did_it_go: a pipeline event happened since the last session, at most window_days back. The latest.
- same_day: the last session was earlier today. Wren picks up from its summary, or checks in plainly
  without one, so a second visit never repeats what's coming up.
- coming_up: the soonest event still ahead, at most window_days out, with the latest feedback.
- pick_up: the last session has a summary.
- plain: nothing else applies.

An entry's time is its date and time in the user's zone. One with no time counts as the end of its day,
so a take-home due today is still coming up tonight.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Literal

from app.conversation import Background
from app.memory.types import FeedbackNote, PipelineEntry

LeadKind = Literal["first", "how_did_it_go", "same_day", "coming_up", "pick_up", "plain"]

OPENING_LINE = "Hey, good to have you back. How's the search going?"
FIRST_OPENING_LINE = "Hi, I'm Wren, an AI here to help with your job search. How's it going so far?"


@dataclass(frozen=True)
class Lead:
    kind: LeadKind
    event: PipelineEntry | None = None  # how_did_it_go and coming_up
    summary: str | None = None  # same_day and pick_up
    feedback: tuple[FeedbackNote, ...] = ()  # coming_up only


def _event_at(entry: PipelineEntry, zone: tzinfo | None) -> datetime | None:
    """When the entry happens in the user's zone. One with no time counts as the end of its day."""
    if entry.next_date is None:
        return None
    if entry.next_time is None:
        return datetime.combine(entry.next_date + timedelta(days=1), time.min, tzinfo=zone)
    return datetime.combine(entry.next_date, entry.next_time, tzinfo=zone)


def choose_lead(background: Background, now_local: datetime, window_days: int) -> Lead:
    """What the opener leads with: the first lead that applies, in the order above."""
    last = background.last_session_at
    if last is None:
        return Lead("first")
    zone, today, window = now_local.tzinfo, now_local.date(), timedelta(days=window_days)
    summary = (background.last_summary or "").strip() or None
    happened = [
        (at, entry) for entry in background.recent
        if (at := _event_at(entry, zone)) is not None and last < at <= now_local and entry.next_date >= today - window
    ]
    if happened:
        return Lead("how_did_it_go", event=max(happened, key=lambda pair: pair[0])[1])
    if last.astimezone(zone).date() == today:
        return Lead("same_day", summary=summary) if summary else Lead("plain")
    ahead = [
        (at, entry) for entry in background.pipeline
        if (at := _event_at(entry, zone)) is not None and at > now_local and entry.next_date <= today + window
    ]
    if ahead:
        return Lead("coming_up", event=min(ahead, key=lambda pair: pair[0])[1], feedback=background.feedback)
    return Lead("pick_up", summary=summary) if summary else Lead("plain")


def _spoken_day(day: date, today: date) -> str:
    """The day as Wren would say it: today, tomorrow, Monday, next Thursday."""
    ahead = (day - today).days
    if ahead <= 0:
        return "today"
    if ahead == 1:
        return "tomorrow"
    return f"{day:%A}" if ahead < 7 else f"next {day:%A}"


def fixed_line(lead: Lead, today: date) -> str:
    """What Wren says when the model's opener misses its shape twice. It leaves out the stage, which is
    free text ("applied", "offer") and doesn't always read as an event."""
    event = lead.event
    if lead.kind == "first":
        return FIRST_OPENING_LINE
    if lead.kind == "how_did_it_go" and event is not None:
        return f"How did things go with {event.company.strip()}?"
    if lead.kind == "coming_up" and event is not None and event.next_date is not None:
        day = _spoken_day(event.next_date, today)
        return f"Your next step with {event.company.strip()} is {day}. Want to get ready for it?"
    return OPENING_LINE
