# Proactive Opener Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a session starts, Wren's first line leads with what matters: an interview that just happened, the next one coming up with the feedback that fits it, or where they left off. It's one or two sentences, under twenty words.

**Architecture:** The session loads the opener's facts once, in `open_session()`'s transaction (Task 1). A pure function in a new `voice/app/opener.py` picks the lead and its fixed fallback line (Task 2). `turn.py` puts that lead's facts in the opening prompt (Task 3). Then it holds the opener until it's whole and checks its shape, asking once more before it falls back (Task 4). The samples tool gets an `opener` group, to hear and measure it with the real model (Task 5).

**Tech Stack:** Python 3.14, FastAPI, psycopg 3, pytest. No new dependencies or services.

**Spec:** `docs/superpowers/specs/2026-11-06-proactive-opener-design.md`, approved 2026-11-06. Also `docs/spec.md` feature 6, `CLAUDE.md`, and the wren-voice skill (`.claude/skills/wren-voice/SKILL.md`).

**Branch:** `proactive-opener`, from `master`. Before Task 1:

```bash
git switch -c proactive-opener
git add docs/superpowers/specs/2026-11-06-proactive-opener-design.md docs/superpowers/plans/2026-11-06-proactive-opener.md
git commit -m "Design and plan the proactive opener" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Commands run from `voice/` with `voice/.venv` active unless a step says otherwise. Database tests need `WREN_DB_TESTS=1` and the dev project in `voice/.env`. If the database can't be reached, stop and say so. Don't skip them.

## Global Constraints

- The opener is one or two sentences, under twenty words in total. At most one question, as the last sentence.
- Lead order, first match wins: `first`, `how_did_it_go`, `same_day`, `coming_up`, `pick_up`, `plain`. A same-day visit with no summary is `plain`.
- An opener makes at most two model calls, as every reply does today. After a shape miss, the second ask keeps its reasoning (`retry=False`), because low reasoning invented stories in the whole-replies replays.
- The facts load in `open_session()`'s one transaction, inside `store.as_user()`, so RLS applies. No search, no model call, no schema change, and at most one more database read than today.
- `Background.pipeline` keeps its meaning (today through seven days out), and later turns never see the new fields. Replies after the opening still stream sentence by sentence.
- Voice and typed sessions share the opener through `run_turn(convo, None)`. POST /turn has no opener.
- wren-voice holds for every line Wren says. Numbers as words. No lists, symbols or parentheses. No hello sentence before the point. Never invent an event, date or feedback. Nothing about how long they've been away. No vibe memories. The first session says it's an AI.
- A shape miss is logged with the model and the counts, never the words.
- No eval run: nothing E1–E3 measure changes (design, "Evals").
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **An event earlier today, before the last session.** It's neither asked about nor "coming up". Task 2: `test_an_event_earlier_today_before_the_last_session_is_neither_asked_about_nor_coming_up`.
2. **The clock near midnight.** A take-home due today in New York, while UTC is already tomorrow, is still coming up today. Task 3: `test_the_opening_reads_the_clock_in_their_zone`.
3. **Feedback whose session was deleted** (`source_session` is null). It still loads, dated by `created_at`. Task 1: `test_feedback_whose_session_is_gone_is_dated_by_when_it_was_saved`.
4. **A stage that isn't an event**, like "applied", or a long company name. The fixed lines never use the stage, and they still fit the shape. Task 2: `test_fixed_lines_never_use_the_stage`, `test_every_fixed_line_fits_the_opener_shape`.
5. **A model that keeps opening with a hello sentence.** One second ask, then the fixed line, never a third call. Task 4: `test_an_opening_that_misses_twice_says_its_fixed_line_after_two_calls`.

## Files

| File | Change |
| --- | --- |
| `voice/app/memory/types.py` | `FeedbackNote` |
| `voice/app/conversation.py` | Three opener fields on `Background` |
| `voice/app/memory/store.py` | `recent_feedback()`, `previous_session()` in place of `last_session_started()`, and `upcoming_pipeline()` renamed `pipeline_between()` |
| `voice/app/memory/live.py` | `load_background()`, which `open_session()` calls |
| `voice/app/voice/tuning.py` | `opener_feedback` |
| `voice/app/opener.py` (new) | The lead choice and the fixed lines |
| `voice/app/turn.py` | The opening prompt, then the hold and the shape check |
| `voice/app/voice/metrics.py` | `held_ms`, and `reshaped` in the repair list |
| `voice/app/tools/samples.py` | The `opener` group, the opening check, held times |
| `voice/tests/` | `test_store_db.py`, `test_opener.py` (new), `test_turn.py`, `test_tools.py` |
| Docs | The samples line in `CLAUDE.md`, build notes in the design |

---

### Task 1: The session loads the opener's facts

**Files:**
- Modify: `voice/app/memory/types.py`, `voice/app/conversation.py:23-27`, `voice/app/voice/tuning.py`, `voice/app/memory/store.py:219-268`, `voice/app/memory/live.py:129-147`
- Test: `voice/tests/test_store_db.py`

**Interfaces:**
- Produces:
  - `FeedbackNote(text: str, at: datetime)` in `app.memory.types`, frozen. `at` is tz-aware: when its session started, or when the memory was saved if that session is gone.
  - `Background` gains `recent: tuple[PipelineEntry, ...] = ()`, `feedback: tuple[FeedbackNote, ...] = ()` and `last_summary: str | None = None`, after its three fields.
  - `store.recent_feedback(conn, user_id: UUID, limit: int, *, schema: str = "public") -> list[FeedbackNote]`
  - `store.previous_session(conn, user_id: UUID, before: datetime, *, schema: str = "public") -> tuple[datetime, str | None] | None`, in place of `last_session_started()`.
  - `store.pipeline_between()`: `upcoming_pipeline()` renamed, with the same signature and query.
  - `live.load_background(conn, user_id: UUID, tz: str, now: datetime, started: datetime, tuning: Tuning) -> Background`
  - `Tuning.opener_feedback: int = 3`

- [ ] **Step 1: Write the failing database tests**

In `voice/tests/test_store_db.py`, import `pipeline_between`, `previous_session` and `recent_feedback` in place of `last_session_started` and `upcoming_pipeline`. Also import `FeedbackNote`, `load_background` (from `app.memory.live`) and `Tuning`. In `test_profile_pipeline_and_last_session`, call `pipeline_between()`, and change the last read and its assertion to:

```python
        last = previous_session(conn, a, before=NOW)
    ...
    assert last == (datetime(2026, 10, 14, 18, tzinfo=UTC), None)
```

Add:

```python
def _session(conn, user, day, summary=None):
    query = "insert into public.sessions (user_id, mode, started_at, summary) values (%s, 'checkin', %s, %s) returning id"
    return conn.execute(query, (user, datetime(2026, 10, day, 18, tzinfo=UTC), summary)).fetchone()[0]


def _memory(conn, user, session_id, text, kind="feedback"):
    return insert_memories(conn, user, session_id, [ExtractedMemory(kind, text, (), 3, ())], [vector(len(text))])[0]


def test_recent_feedback_is_current_newest_first_and_capped(db):
    conn, a, b = db
    for day in (8, 10, 12):
        _memory(conn, a, _session(conn, a, day), f"feedback from the {day}th")
    latest = _session(conn, a, 14)
    kept, old = _memory(conn, a, latest, "feedback from the 14th"), _memory(conn, a, latest, "an older version")
    conn.execute("update public.memories set superseded_by = %s where id = %s", (kept, old))
    _memory(conn, a, latest, "a story", kind="story")
    _memory(conn, b, _session(conn, b, 15), "b's feedback")
    with as_user(conn, a):
        notes = recent_feedback(conn, a, 3)
    assert [n.text for n in notes] == ["feedback from the 14th", "feedback from the 12th", "feedback from the 10th"]
    assert notes[0].at == datetime(2026, 10, 14, 18, tzinfo=UTC)
    with as_user(conn, b):
        assert recent_feedback(conn, a, 3) == []


def test_feedback_whose_session_is_gone_is_dated_by_when_it_was_saved(db):
    conn, a, _ = db
    memory = _memory(conn, a, _session(conn, a, 9), "Leads with a number")
    saved = datetime(2026, 10, 9, 20, tzinfo=UTC)
    conn.execute("update public.memories set source_session = null, created_at = %s where id = %s", (saved, memory))
    with as_user(conn, a):
        assert recent_feedback(conn, a, 3) == [FeedbackNote("Leads with a number", saved)]


def test_previous_session_brings_its_summary_and_never_an_older_one(db):
    conn, a, _ = db
    _session(conn, a, 10, "The user practiced the churn story.")
    _session(conn, a, 14)
    with as_user(conn, a):
        assert previous_session(conn, a, before=NOW) == (datetime(2026, 10, 14, 18, tzinfo=UTC), None)
        assert previous_session(conn, a, before=datetime(2026, 10, 12, tzinfo=UTC)) == (
            datetime(2026, 10, 10, 18, tzinfo=UTC), "The user practiced the churn story.")
        assert previous_session(conn, a, before=datetime(2026, 10, 1, tzinfo=UTC)) is None


def test_the_session_loads_the_opener_facts_with_the_pipeline_split_at_today(db):
    conn, a, _ = db
    conn.execute(
        "insert into public.profiles (user_id, display_name, summary, target_roles, timezone) "
        "values (%s, 'Maya', '', '{BDR}', 'America/New_York')", (a,),
    )
    insert = "insert into public.pipeline (user_id, company, stage, next_date) values (%s, %s, 'recruiter screen', %s)"
    for company, day in (("Too Old", 8), ("Velloway Analytics", 14), ("Quillbase", 16), ("Sprout Social", 19), ("Too Far", 24)):
        conn.execute(insert, (a, company, date(2026, 10, day)))
    _memory(conn, a, _session(conn, a, 14, "The user practiced the churn story."), "Leads with a number")
    with as_user(conn, a):
        background = load_background(conn, a, "UTC", NOW, NOW, Tuning())  # NOW is October 16, 7 pm in New York
    assert [e.company for e in background.pipeline] == ["Quillbase", "Sprout Social"]
    assert [e.company for e in background.recent] == ["Velloway Analytics", "Quillbase"]
    assert background.last_session_at == datetime(2026, 10, 14, 18, tzinfo=UTC)
    assert background.last_summary == "The user practiced the churn story."
    assert [n.text for n in background.feedback] == ["Leads with a number"]
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `WREN_DB_TESTS=1 pytest tests/test_store_db.py -v`
Expected: collection fails with `ImportError: cannot import name 'pipeline_between'`.

- [ ] **Step 3: Add the type and the fields**

Add `FeedbackNote` to `types.py`. Add the three `Background` fields to `conversation.py`, with a comment that only the opener reads them. Add `opener_feedback: int = 3` to `Tuning`, commented as how many feedback memories the opener sees.

- [ ] **Step 4: Add the reads to `store.py`**

- `recent_feedback()` reads the user's current feedback memories (`kind = 'feedback'`, `superseded_by is null`). Each is dated `coalesce(sessions.started_at, memories.created_at)`, through a left join on `source_session`. They come newest first, then by `importance desc`, then by `id`, at most `limit` of them. Filter by `user_id` explicitly, as the other live reads do.
- `previous_session()` returns `started_at` and `summary` of the user's latest session that started before `before`, or None. Delete `last_session_started()`.
- Rename `upcoming_pipeline()` to `pipeline_between()`.

- [ ] **Step 5: Add `load_background()` to `live.py`, and call it from `open_session()`**

It does what `open_session()` does today, and adds the opener's facts:

- It loads the profile, then finds today's date in the profile's zone, or in `tz` when there's no profile, as today.
- It makes one `pipeline_between()` read, from `today - pipeline_days` through `today + pipeline_days`. `pipeline` gets the rows dated today or later, and `recent` gets the rows dated today or earlier, so today's rows are in both.
- `previous_session(before=started)` gives `last_session_at` and `last_summary`.
- `recent_feedback(limit=tuning.opener_feedback)` gives `feedback`.

`open_session()` keeps its transaction and its resume logic, and builds its `Background` with `load_background(conn, user_id, tz, now, started, self._tuning)`. `NoMemory` doesn't change.

- [ ] **Step 6: Run the database tests, then everything**

Run: `WREN_DB_TESTS=1 pytest tests/test_store_db.py -v`, then `pytest`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add app/memory/types.py app/conversation.py app/voice/tuning.py app/memory/store.py app/memory/live.py tests/test_store_db.py
git commit -m "Load the opener's facts when a session opens" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Choose the lead, and its fixed line

**Files:**
- Create: `voice/app/opener.py`
- Modify: `voice/app/turn.py:76-78`, where the two opening lines move out
- Test: `voice/tests/test_opener.py` (new)

**Interfaces:**
- Consumes: `Background` and `FeedbackNote` (Task 1), and `PipelineEntry`.
- Produces, in `app.opener`:
  - `LeadKind = Literal["first", "how_did_it_go", "same_day", "coming_up", "pick_up", "plain"]`
  - `Lead(kind: LeadKind, event: PipelineEntry | None = None, summary: str | None = None, feedback: tuple[FeedbackNote, ...] = ())`, frozen.
  - `choose_lead(background: Background, now_local: datetime, window_days: int) -> Lead`
  - `fixed_line(lead: Lead, today: date) -> str`
  - `OPENING_LINE` and `FIRST_OPENING_LINE`, moved from `turn.py` unchanged. `turn.py` imports them from here.

- [ ] **Step 1: Write the failing tests**

Create `voice/tests/test_opener.py`:

```python
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.conversation import Background
from app.memory.types import FeedbackNote, PipelineEntry
from app.opener import FIRST_OPENING_LINE, OPENING_LINE, Lead, choose_lead, fixed_line

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 15, 19, 40, tzinfo=NY)  # Thursday evening, as in the samples
TUESDAY = datetime(2026, 10, 13, 18, 40, tzinfo=NY)  # the last session
SPROUT = PipelineEntry("Sprout Social", "BDR", "recruiter screen", date(2026, 10, 16), time(11, 0), None)
NOTES = (FeedbackNote("Rambles on 'tell me about yourself', running past two minutes", TUESDAY),)
SUMMARY = "The user practiced the consulting club story."


def row(company, day, at=None, stage="recruiter screen"):
    return PipelineEntry(company, None, stage, day, at, None)


def lead(last=TUESDAY, recent=(), pipeline=(), feedback=(), summary=None, now=NOW):
    background = Background(None, pipeline, last, recent=recent, feedback=feedback, last_summary=summary)
    return choose_lead(background, now, 7)


def test_a_first_session_is_first_even_with_an_event_coming_up():
    assert lead(last=None, pipeline=(SPROUT,), feedback=NOTES) == Lead("first")


def test_an_event_since_the_last_session_comes_before_anything_coming_up():
    velloway = row("Velloway Analytics", date(2026, 10, 15), time(14, 0))
    assert lead(recent=(velloway,), pipeline=(velloway, SPROUT), feedback=NOTES) == Lead("how_did_it_go", event=velloway)


def test_with_several_events_since_the_last_session_the_latest_is_asked_about():
    later = row("Velloway Analytics", date(2026, 10, 14), time(15, 0))
    earlier = row("Corvid Labs", date(2026, 10, 14), time(9, 0))
    assert lead(recent=(later, earlier)).event == later


def test_an_event_before_the_last_session_is_not_asked_about_again():
    velloway = row("Velloway Analytics", date(2026, 10, 13), time(10, 0))
    assert lead(recent=(velloway,), pipeline=(SPROUT,)) == Lead("coming_up", event=SPROUT)


def test_an_event_earlier_today_before_the_last_session_is_neither_asked_about_nor_coming_up():
    velloway = row("Velloway Analytics", date(2026, 10, 15), time(10, 0))
    seen = dict(last=datetime(2026, 10, 15, 11, 0, tzinfo=NY), recent=(velloway,), pipeline=(velloway,))
    assert lead(**seen) == Lead("plain")
    assert lead(**seen, summary=SUMMARY) == Lead("same_day", summary=SUMMARY)


def test_an_event_later_today_is_coming_up():
    tonight = row("Sprout Social", date(2026, 10, 15), time(21, 0))
    assert lead(recent=(tonight,), pipeline=(tonight,)) == Lead("coming_up", event=tonight)


def test_a_row_with_no_time_counts_as_the_end_of_its_day():
    due_today = row("Quillbase", date(2026, 10, 15), stage="take-home")
    due_yesterday = row("Quillbase", date(2026, 10, 14), stage="take-home")
    assert lead(recent=(due_today,), pipeline=(due_today,)) == Lead("coming_up", event=due_today)
    assert lead(recent=(due_yesterday,)) == Lead("how_did_it_go", event=due_yesterday)


def test_an_event_more_than_seven_days_back_is_not_asked_about():
    week_ago = row("Velloway Analytics", date(2026, 10, 7), time(14, 0))
    assert lead(last=datetime(2026, 10, 1, 12, 0, tzinfo=NY), recent=(week_ago,)) == Lead("plain")


def test_a_second_visit_the_same_day_picks_up_and_skips_what_is_coming_up():
    earlier = datetime(2026, 10, 15, 17, 10, tzinfo=NY)
    assert lead(last=earlier, pipeline=(SPROUT,), feedback=NOTES, summary=SUMMARY) == Lead("same_day", summary=SUMMARY)
    assert lead(last=earlier, pipeline=(SPROUT,), feedback=NOTES) == Lead("plain")


def test_a_second_visit_after_an_event_today_asks_about_the_event():
    velloway = row("Velloway Analytics", date(2026, 10, 15), time(14, 0))
    assert lead(last=datetime(2026, 10, 15, 9, 0, tzinfo=NY), recent=(velloway,)).kind == "how_did_it_go"


def test_coming_up_names_the_soonest_event_still_ahead_and_carries_the_feedback():
    corvid = row("Corvid Labs", date(2026, 10, 18), stage="panel")
    assert lead(pipeline=(corvid, SPROUT), feedback=NOTES) == Lead("coming_up", event=SPROUT, feedback=NOTES)


def test_without_anything_coming_up_the_summary_is_picked_up_and_otherwise_it_is_plain():
    assert lead(feedback=NOTES, summary=f" {SUMMARY} ") == Lead("pick_up", summary=SUMMARY)
    assert lead(feedback=NOTES, summary="  ") == Lead("plain")
    assert lead(pipeline=(row("Corvid Labs", None),)) == Lead("plain")


@pytest.mark.parametrize("chosen, line", [
    (Lead("first"), FIRST_OPENING_LINE),
    (Lead("how_did_it_go", event=SPROUT), "How did things go with Sprout Social?"),
    (Lead("coming_up", event=row("Sprout Social", date(2026, 10, 15), time(21, 0))),
     "Your next step with Sprout Social is today. Want to get ready for it?"),
    (Lead("coming_up", event=SPROUT), "Your next step with Sprout Social is tomorrow. Want to get ready for it?"),
    (Lead("coming_up", event=row("Corvid Labs", date(2026, 10, 19))),
     "Your next step with Corvid Labs is Monday. Want to get ready for it?"),
    (Lead("coming_up", event=row("Corvid Labs", date(2026, 10, 22))),
     "Your next step with Corvid Labs is next Thursday. Want to get ready for it?"),
    (Lead("same_day", summary=SUMMARY), OPENING_LINE),
    (Lead("pick_up", summary=SUMMARY), OPENING_LINE),
    (Lead("plain"), OPENING_LINE),
])
def test_fixed_lines_say_the_day_in_spoken_words(chosen, line):
    assert fixed_line(chosen, NOW.date()) == line


def test_fixed_lines_never_use_the_stage():
    applied = row("Velloway Analytics", date(2026, 10, 14), stage="applied")
    assert fixed_line(Lead("how_did_it_go", event=applied), NOW.date()) == "How did things go with Velloway Analytics?"


def test_every_fixed_line_fits_the_opener_shape():
    long_name = row("Brightline Analytics Group International", date(2026, 10, 22))
    for chosen in (Lead("first"), Lead("how_did_it_go", event=long_name), Lead("coming_up", event=long_name), Lead("plain")):
        line = fixed_line(chosen, NOW.date())
        assert len(line.split()) < 20 and len(re.split(r"(?<=[.!?])\s+", line)) <= 2, line
        assert line.count("?") == 1 and line.endswith("?") and not re.search(r"\d", line), line
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_opener.py -v`
Expected: collection fails with `ModuleNotFoundError: No module named 'app.opener'`.

- [ ] **Step 3: Write `choose_lead()` in `voice/app/opener.py`**

It follows the design's table and definitions. The zone is `now_local.tzinfo`, and `today` is `now_local.date()`.

- An entry's time is its date and time in that zone. An entry with no time counts as the end of its day, the midnight after its date. Entries with no date never count.
- `first` when `last_session_at` is None.
- `how_did_it_go` takes the latest `recent` entry whose time is after `last_session_at` and no later than `now_local`, dated no more than `window_days` before today.
- When `last_session_at` falls on today in that zone, the lead is `same_day` with the summary if `last_summary` isn't blank, and `plain` otherwise.
- `coming_up` takes the `pipeline` entry with the soonest time after `now_local`, dated no more than `window_days` after today, with `feedback=background.feedback`.
- `pick_up`, with the summary stripped, when `last_summary` isn't blank. Otherwise `plain`.

Give the module a docstring that points to the design doc.

- [ ] **Step 4: Write `fixed_line()`, and move the two lines**

- `first`: `FIRST_OPENING_LINE`.
- `how_did_it_go`: `f"How did things go with {company}?"`
- `coming_up`: `f"Your next step with {company} is {day}. Want to get ready for it?"`. The day is "today", then "tomorrow", then the weekday's name two to six days out, then "next " and the weekday's name seven or more days out.
- Every other lead: `OPENING_LINE`.

Move `OPENING_LINE` and `FIRST_OPENING_LINE` from `turn.py` to `opener.py`, and import them in `turn.py`, which keeps using them until Task 4.

- [ ] **Step 5: Run the tests, then everything**

Run: `pytest tests/test_opener.py -v`, then `pytest`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add app/opener.py app/turn.py tests/test_opener.py
git commit -m "Choose what the opener leads with, and its fixed line" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Put the lead's facts in the opening prompt

**Files:**
- Modify: `voice/app/turn.py`: `_day`, `_signals`, `_pipeline` and `build_messages`
- Test: `voice/tests/test_turn.py`

**Interfaces:**
- Consumes: `choose_lead()` and `Lead` (Task 2). `FeedbackNote` and the new `Background` fields (Task 1).
- Produces: `_opening_lead(convo: Conversation, now_utc: datetime, tuning: Tuning) -> Lead` in `turn.py`. It's `choose_lead()` on the conversation's local clock (`ZoneInfo(convo.tz)`) with `tuning.pipeline_days`. Task 4 uses it for the fallback. `build_messages()` keeps its signature.

- [ ] **Step 1: Write the failing tests**

In `voice/tests/test_turn.py`, import `FeedbackNote` from `app.memory.types`, and add:

```python
VELLOWAY = PipelineEntry("Velloway Analytics", "BDR", "recruiter screen", date(2026, 10, 14), time(14, 0), None)
NOTE = FeedbackNote("Rambles on 'tell me about yourself', running past two minutes", NOW - timedelta(days=2, hours=1))
SUMMARY = "The user practiced the consulting club story and worked on ending it on the result."


def opening_prompt(now=NOW, **background) -> str:
    convo = conversation(turns=0)
    convo.background = replace(convo.background, **background)
    return build_messages(convo, None, [], now, Tuning())[0]["content"]


def test_the_opening_prompt_holds_the_shape_and_says_nothing_about_time_away():
    content = opening_prompt()
    assert "session is just starting" in content and "under twenty words in total" in content
    assert "Don't mention how long" in content and "Open in under 25 words" not in content


@pytest.mark.parametrize("background, present, absent", [
    ({"recent": (VELLOWAY,)},
     ["they had this: Velloway Analytics, BDR, recruiter screen: Wednesday, October 14 (yesterday) at 2:00 PM", "how it went"],
     ["Coming up soonest"]),
    ({"last_session_at": datetime(2026, 10, 15, 21, 10, tzinfo=timezone.utc), "last_summary": SUMMARY},
     [SUMMARY, "keep going on it"], ["Coming up soonest"]),
    ({"feedback": (NOTE,)},
     ["Coming up soonest: Sprout Social, BDR, recruiter screen: Friday, October 16 (tomorrow) at 11:00 AM",
      "- [feedback, Tuesday, October 13] Rambles on 'tell me about yourself', running past two minutes"],
     ["Nothing relevant yet."]),
    ({"pipeline": (), "last_summary": SUMMARY, "feedback": (NOTE,)},
     [SUMMARY, "pick it back up", "Nothing relevant yet."], ["Rambles on"]),
    ({"pipeline": ()}, ["Don't bring up an interview or a date"], ["Coming up soonest"]),
])
def test_each_lead_puts_its_own_facts_in_the_opening_prompt(background, present, absent):
    content = opening_prompt(**background)
    for text in present:
        assert text in content, text
    for text in absent:
        assert text not in content, text


def test_later_turns_never_see_the_opener_facts():
    convo = conversation()
    convo.background = replace(convo.background, recent=(VELLOWAY,), feedback=(NOTE,), last_summary=SUMMARY)
    content = build_messages(convo, "hi", [], NOW, Tuning())[0]["content"]
    for text in ("Velloway", "Rambles on", SUMMARY, "under twenty words", "session is just starting"):
        assert text not in content, text


def test_the_opening_reads_the_clock_in_their_zone():
    take_home = PipelineEntry("Quillbase", None, "take-home", date(2026, 10, 15), None, None)
    late = datetime(2026, 10, 16, 2, 0, tzinfo=timezone.utc)  # 10 pm Thursday in New York, already Friday in UTC
    content = opening_prompt(now=late, pipeline=(take_home,), recent=(take_home,))
    assert "Coming up soonest: Quillbase, take-home: Thursday, October 15 (today)" in content
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_turn.py -v -k "opening_prompt or each_lead or opener_facts or their_zone"`
Expected: three FAIL, because the prompt still has the old "Open in under 25 words" line. `test_later_turns_never_see_the_opener_facts` already passes. It guards the change.

- [ ] **Step 3: Change the opening prompt**

- `_day()` labels the day before today " (yesterday)", alongside today and tomorrow.
- Split the line for one entry out of `_pipeline()` into `_entry(entry, today) -> str`. It reads like "Sprout Social, BDR, recruiter screen: Friday, October 16 (tomorrow) at 11:00 AM", with ". " and the notes when there are any. `_pipeline()` joins the entries as before.
- `_signals(convo, now_local, lead: Lead | None)` replaces its `opening` flag. With a lead, the two old opening lines go, and these lines follow the usual signals, in this order. This is the starting copy. Task 5 may tune the wording, but must keep the phrases the tests check.
  1. `- The session is just starting, and you speak first.`
  2. The lead's line:
     - first: `- Say plainly, in a few words, that you're an AI. Then ask one question about their search.`
     - how_did_it_go: `- Since your last session, they had this: {entry}. Open by asking how it went.`
     - same_day: `- What you worked on earlier today: {summary} Open by offering to keep going on it.`
     - coming_up: `- Coming up soonest: {entry}. Name it, then offer one thing to practice for it, as your question. If a feedback note below fits it, work it in kindly, in your own words.`
     - pick_up: `- Last session, you worked on this: {summary} Open by offering to pick it back up.`
     - plain: `- Open with a plain check-in: one question about their search. Don't bring up an interview or a date.`
  3. `- One or two sentences, under twenty words in total. Start with the point, with no hello sentence first. Their name is fine.`
  4. `- Don't mention how long it's been since you last talked.`

  A summary with no closing punctuation gets a period.
- In `build_messages()`, with no user text, the lead comes from `_opening_lead()`. For `coming_up` with feedback, the memories block lists the notes, one a line, as `- [feedback, {_day(the note's local date, today)}] {text}`. Every other opening keeps today's empty block.

- [ ] **Step 4: Run the tests, then everything**

Run: `pytest tests/test_turn.py -v`, then `pytest`
Expected: all pass, including `test_opening_turn_skips_retrieval_and_asks_for_a_greeting`, which still finds "session is just starting" and "you're an AI".

- [ ] **Step 5: Commit**

```bash
git add app/turn.py tests/test_turn.py
git commit -m "Give the opener its lead's facts" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Hold the opener until it's whole, and check its shape

**Files:**
- Modify: `voice/app/turn.py` (the module docstring, `_wren`, `_reply` and `_COPIED_BACK`), `voice/app/voice/metrics.py`
- Test: `voice/tests/test_turn.py`

**Interfaces:**
- Consumes: `fixed_line()` (Task 2) and `_opening_lead()` (Task 3).
- Produces:
  - `TurnTiming.held_ms: int | None = None`: how long an opening was held, from its first whole sentence to when it was spoken. It's in `record()` as `"held_ms"`, and in `_COPIED_BACK`.
  - `reply_repair == "reshaped"` when the second ask after a shape miss is spoken. Add it to the comment's list.
  - `_reply(..., fits: Callable[[list[str]], bool] | None = None)`
  - `_fits_opening(segments: list[str]) -> bool`, with `OPENING_SENTENCES = 2` and `OPENING_WORDS = 20`.

- [ ] **Step 1: Write the failing tests**

In `voice/tests/test_turn.py`, add:

```python
THREE = "Hey Maya. Your Sprout screen is tomorrow. Want to run it once?"
FITS = "Maya, your Sprout screen is tomorrow. Want to run it once?"


def opening_out(chat, timing):
    return asyncio.run(_collect(run_turn(conversation(turns=0), None, retrieve=_no_memories, chat=chat, timing=timing)))


def test_an_opening_is_held_until_it_is_whole():
    release = asyncio.Event()

    async def chat(messages, timing, **_):
        yield "Maya, your Sprout screen is tomorrow. "
        await release.wait()
        yield "Want to run it once?"

    async def run():
        gen = run_turn(conversation(turns=0), None, retrieve=_no_memories, chat=chat, timing=TurnTiming(1, "checkin", "opening"))
        first = asyncio.ensure_future(anext(gen))
        await asyncio.sleep(0.05)
        held = not first.done()
        release.set()
        return held, [await first] + [s async for s in gen]

    held, out = asyncio.run(run())
    assert held and out == ["Maya, your Sprout screen is tomorrow.", "Want to run it once?"]


def test_an_opening_past_two_sentences_is_asked_again_keeping_its_reasoning():
    chat, timing = ScriptedChat([THREE], [FITS]), TurnTiming(1, "checkin", "opening")
    assert opening_out(chat, timing) == ["Maya, your Sprout screen is tomorrow.", "Want to run it once?"]
    assert chat.calls == 2 and chat.retries == [False, False] and timing.reply_repair == "reshaped"


@pytest.mark.parametrize("first", [
    "Maya, your Sprout screen is tomorrow, and last week you ran long on your intro. Want to tighten it tonight?",  # twenty words
    "How did the Velloway screen go? Tell me everything.",  # it asks before its last sentence
])
def test_an_opening_of_twenty_words_or_an_early_question_is_asked_again(first):
    chat, timing = ScriptedChat([first], [FITS]), TurnTiming(1, "checkin", "opening")
    assert opening_out(chat, timing)[-1] == "Want to run it once?" and timing.reply_repair == "reshaped"


def test_nineteen_words_in_two_sentences_is_spoken_as_it_came():
    reply = "Maya, your Sprout screen is tomorrow, and last week you ran long on your intro. Want to tighten it?"
    chat, timing = ScriptedChat([reply]), TurnTiming(1, "checkin", "opening")
    assert len(opening_out(chat, timing)) == 2 and chat.calls == 1 and timing.reply_repair is None


@pytest.mark.parametrize("second", [[THREE], ["I"]])
def test_an_opening_that_misses_twice_says_its_fixed_line_after_two_calls(second):
    chat, timing = ScriptedChat([THREE], second), TurnTiming(1, "checkin", "opening")
    assert opening_out(chat, timing) == ["Your next step with Sprout Social is tomorrow. Want to get ready for it?"]
    assert chat.calls == 2 and chat.retries == [False, False] and timing.reply_repair == "gave_up"


def test_an_opening_records_how_long_it_was_held():
    async def chat(messages, timing, **_):
        yield "Maya, your Sprout screen is tomorrow. "
        await asyncio.sleep(0.05)
        yield "Want to run it once?"

    timing = TurnTiming(1, "checkin", "opening")
    opening_out(chat, timing)
    assert timing.held_ms >= 40 and timing.record()["held_ms"] == timing.held_ms
    later = TurnTiming(3, "checkin", "voice")
    turn_out(ScriptedChat(["Got it."]), later)
    assert later.held_ms is None
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_turn.py -v`
Expected: the new tests FAIL, because the opening still streams and `held_ms` doesn't exist. `test_nineteen_words_in_two_sentences_is_spoken_as_it_came` already passes. It guards against holding back a reply that fits.

- [ ] **Step 3: Write `_fits_opening()`**

It's True when there are at most `OPENING_SENTENCES` segments, the words across them (split on whitespace) number fewer than `OPENING_WORDS`, and no segment but the last asks something (`_asks()`).

- [ ] **Step 4: Teach `_reply()` to hold**

Without `fits`, nothing changes. With it:

- Each attempt's segments are held until its stream ends, not yielded. The claim check runs on each one first, as today, so the segments held before a dropped claim are the ones checked.
- If the held segments fit: set `call.held_ms`, from when the first one was held to now. Yield them all, and the attempt counts as spoken.
- If they don't fit: yield nothing. Log a warning with the model, the segment count and the word count, never the words. The attempt counts as one with no whole sentence.
- The second ask uses low reasoning (`retry=True`) only when the first attempt had no whole sentence. After a shape miss it keeps its reasoning, as it does after a dropped claim.
- A second ask spoken after a shape miss records `"reshaped"`, unless a claim was dropped. `"retried"` and `"gave_up"` stay as they are.

- [ ] **Step 5: Use it for the opening in `_wren()`**

Take `utcnow()` once, and pass it to `build_messages()`. With no user text, the fallback is `fixed_line(_opening_lead(convo, now, tuning), today)`, where `today` is the date in the conversation's zone, and `fits=_fits_opening`. Other replies pass no `fits`. Update the module docstring: the opening is held until it's whole and checked against its shape. A miss is asked for once more, and then Wren says the lead's fixed line.

- [ ] **Step 6: Run the tests, then everything**

Run: `pytest tests/test_turn.py -v`, then `pytest`
Expected: all pass. The voice session tests' scripted opening, "Hey Maya. How's the week going?", fits, so their call counts don't move.

- [ ] **Step 7: Commit**

```bash
git add app/turn.py app/voice/metrics.py tests/test_turn.py
git commit -m "Hold the opener until it's whole, and keep it to two short sentences" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Hear it with the samples tool, and write it up

**Files:**
- Modify: `voice/app/tools/samples.py`, `CLAUDE.md` (the samples line), `docs/superpowers/specs/2026-11-06-proactive-opener-design.md` (build notes)
- Test: `voice/tests/test_tools.py`

**Interfaces:**
- Consumes: `choose_lead()` (Task 2), `TurnTiming.held_ms` (Task 4) and `FeedbackNote` (Task 1).
- Produces:
  - `check(reply, *, debrief=False, in_role=False, opening=False)`
  - `SampleReply.held_ms: int | None = None`
  - `summarize()` keys `held_p50` and `held_max`
  - An `opener` group of six scenes

- [ ] **Step 1: Write the failing tests**

In `voice/tests/test_tools.py`, import `replace`, `ZoneInfo`, `choose_lead`, and `SAMPLE_NOW` and `TZ` from `app.tools.samples`. Change the checkin list in `test_the_checkin_scenarios_set_one_scene_and_the_ramble_comes_with_its_question` to `["nervous", "ramble", "memory", "person", "crisis"]`. Rename `test_the_memory_scenario_has_no_notes_and_the_opening_has_no_user_line` to `test_the_memory_scenario_has_no_notes` and drop its opening half. Add:

```python
def test_check_flags_an_opener_past_two_sentences_or_at_twenty_words():
    assert check("Maya, your Sprout screen is tomorrow. Want to run it once?", opening=True) == []
    assert "long" in check("Hey Maya. Your Sprout screen is tomorrow. Want to run it once?", opening=True)
    twenty = "Maya, your Sprout screen is tomorrow, and last week you ran long on your intro. Want to tighten it tonight?"
    assert "long" in check(twenty, opening=True) and "long" not in check(twenty)


def test_summarize_reports_how_long_openers_were_held():
    replies = [replace(SampleReply("s", None, "Hi.", 1, []), held_ms=ms) for ms in (10, 30, None)]
    summary = summarize_replies(replies)
    assert summary["held_p50"] == 10 and summary["held_max"] == 30


def test_the_opener_scenes_reach_each_lead():
    by_name = {s.name: s for s in SCENARIOS if s.group == "opener"}
    leads = {"first": "first", "how-did-it-go": "how_did_it_go", "same-day": "same_day",
             "coming-up": "coming_up", "pick-up": "pick_up", "plain": "plain"}
    assert list(by_name) == list(leads)
    now_local = SAMPLE_NOW.astimezone(ZoneInfo(TZ))
    for name, kind in leads.items():
        assert by_name[name].lines == (None,), name
        assert choose_lead(by_name[name].setup().background, now_local, Tuning().pipeline_days).kind == kind, name
    chat = FakeChat([["Maya, your Sprout screen is tomorrow. Want to run it once?"]])
    [reply] = asyncio.run(run_scenario(by_name["coming-up"], chat, Tuning()))
    assert reply.flags == [] and reply.held_ms is not None and "- [feedback, " in chat.calls[0][0]["content"]
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_tools.py -v`
Expected: FAIL. `check()` has no `opening` option, and there's no `opener` group.

- [ ] **Step 3: Change the samples tool**

- `check(opening=True)` flags "long" past two sentences or at twenty words, along with the usual flags. `_sample()` checks a turn with no user line as an opening.
- `SampleReply.held_ms` comes from the turn's timing. `show()` adds "held N ms" when it's set. `summarize()` gives the nearest-rank median and the max of the held times. `main()` prints `Openers held: median N ms, max N ms.` when there are any.
- Add the `opener` group: six scenes, each built from `maya()` with these changes to its background, opened with `(None,)` and given no notes.
  - `OPENER_FEEDBACK` holds the eval set's three feedback texts (maya-f06, f07 and f08 in `evals/data/personas/maya.json`), dated two, nine and sixteen days before `SAMPLE_NOW`.
  - `VELLOWAY` is `PipelineEntry("Velloway Analytics", "BDR", "recruiter screen", date(2026, 10, 15), time(14, 0), None)`.
  - `LAST_SUMMARY` is "The user practiced the consulting club story and worked on ending it on the result."

  | Scene | Changes to `maya()`'s background | Lead |
  | --- | --- | --- |
  | `first` | `last_session_at=None`, `pipeline=()` | first |
  | `how-did-it-go` | `recent=(VELLOWAY,)`, `feedback=OPENER_FEEDBACK` | how_did_it_go |
  | `same-day` | `last_session_at=SAMPLE_NOW - timedelta(hours=2, minutes=30)`, `last_summary=LAST_SUMMARY`, `feedback=OPENER_FEEDBACK` | same_day |
  | `coming-up` | `feedback=OPENER_FEEDBACK` | coming_up |
  | `pick-up` | `pipeline=()`, `last_summary=LAST_SUMMARY`, `feedback=OPENER_FEEDBACK` | pick_up |
  | `plain` | `pipeline=()` | plain |

- Remove the checkin group's `opening` scene. The docstring's `--group` line names `opener`.
- In `CLAUDE.md`, after the samples command, add: "After a change to the opener, run `--group opener`."

- [ ] **Step 4: Run the tests, then everything**

Run: `pytest tests/test_tools.py -v`, then `pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add app/tools/samples.py tests/test_tools.py ../CLAUDE.md
git commit -m "Hear the opener's six leads in the samples tool" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Hear the openers**

Run: `python -m app.tools.samples --group opener --runs 3`

Read all eighteen replies against the wren-voice checklist and the design. Each lead must do its job:

- `first` says it's an AI.
- `how-did-it-go` asks about Velloway.
- `same-day` and `pick-up` name the consulting club story.
- `coming-up` names Sprout and tomorrow, and offers one thing to practice.
- `plain` names no interview.
- None of them invents an event or mentions time away.

The summary line should show `Two questions: 0` and `Flagged: 0`. A reply from a model other than the first one means a rate limit: raise `--pause` and run it again. If a lead misses in two of its three runs, tune that lead's line in `_signals()`, keeping the phrases the tests check. Then rerun that scene with `--scenario <name> --runs 3`, and run `pytest tests/test_turn.py`.

- [ ] **Step 7: Write the build notes**

Add a "Build notes (date)" section at the top of the design doc, the way the voice-loop design has one. Record the model and the counts from the summary line: flagged, and gave up. From the per-reply notes, count how many were reshaped. Add the held median and max, one reply for each lead, and any wording you tuned and why. Say that spec feature 6's boxes stay open until live extraction gives a real account the facts.

- [ ] **Step 8: Check everything, then commit**

Run: `pytest`, then `WREN_DB_TESTS=1 pytest tests/test_store_db.py`, then `pytest evals/tests` from the repo root.
Expected: all pass.

```bash
git add app/turn.py ../docs/superpowers/specs/2026-11-06-proactive-opener-design.md
git commit -m "Note how the opener sounded and measured" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
