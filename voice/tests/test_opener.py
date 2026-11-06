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
