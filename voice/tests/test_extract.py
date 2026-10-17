from datetime import datetime, timezone

from app.memory.extract import (
    ExtractionResult,
    build_prompt,
    format_pipeline,
    format_transcript,
    next_pipeline,
    parse_extraction,
)
from app.memory.types import PipelineItem, Session, Turn

SESSION = Session(
    id="s1",
    started_at=datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
    mode="checkin",
    turns=(
        Turn("s1:t01", "wren", "How was the week?"),
        Turn("s1:t02", "user", "Velloway moved my screen to Friday."),
    ),
)
BOOKED = PipelineItem("Velloway Analytics", "BDR", "recruiter screen", "2026-10-08", "14:00", "", ("s0:t04",), None)
OTHER = PipelineItem("Quillbase", "", "take-home", "2026-10-02", "", "30-60-90 day plan", ("s0:t06",), None)


def _memory(**overrides):
    item = {"kind": "story", "text": "The user cut donor churn by 15 percent.",
            "entities": ["consulting club"], "importance": 5, "source_turns": ["s1:t02"]}
    item.update(overrides)
    return item


def _pipeline(**overrides):
    item = {"replaces": "", "company": "Velloway Analytics", "role": "BDR", "stage": "recruiter screen",
            "next_date": "2026-10-09", "next_time": "11:00", "notes": "", "source_turns": ["s1:t02"]}
    item.update(overrides)
    return item


def _parse(memories=(), pipeline=(), current=()):
    result = ExtractionResult.model_validate({"memories": list(memories), "pipeline": list(pipeline)})
    return parse_extraction(result, SESSION, list(current))


def test_format_transcript_labels_turns_and_speakers():
    assert format_transcript(SESSION) == "[s1:t01] Wren: How was the week?\n[s1:t02] User: Velloway moved my screen to Friday."


def test_build_prompt_gives_the_local_date_with_its_weekday_and_the_pipeline():
    prompt = build_prompt(SESSION, [BOOKED], "America/New_York")
    assert "Session date: Saturday, 2026-10-03 (America/New_York)" in prompt
    assert "Session mode: checkin" in prompt
    assert "P1: Velloway Analytics | BDR | recruiter screen | next: 2026-10-08 14:00" in prompt
    assert "[s1:t02] User: Velloway moved my screen to Friday." in prompt


def test_the_local_date_can_differ_from_the_utc_date():
    late = Session("s1", datetime(2026, 10, 4, 2, tzinfo=timezone.utc), "checkin", SESSION.turns)
    assert "Session date: Saturday, 2026-10-03 (America/New_York)" in build_prompt(late, [], "America/New_York")


def test_an_empty_pipeline_says_so():
    assert format_pipeline([]) == "(none yet)"


def test_unknown_turn_ids_are_dropped_and_a_memory_without_valid_turns_is_dropped():
    extraction = _parse(memories=[_memory(source_turns=["s1:t02", "s1:t99"]), _memory(source_turns=["s9:t01"])])
    assert [m.source_turns for m in extraction.memories] == [("s1:t02",)]


def test_importance_is_clamped_and_unknown_kinds_are_dropped():
    extraction = _parse(memories=[_memory(importance=0), _memory(importance=9), _memory(kind="diagnosis")])
    assert [m.importance for m in extraction.memories] == [1, 5]


def test_text_whitespace_and_duplicate_entities_are_cleaned():
    (memory,) = _parse(memories=[_memory(text="  The user   cut\nchurn. ", entities=["Velloway", " Velloway", ""])]).memories
    assert memory.text == "The user cut churn."
    assert memory.entities == ("Velloway",)


def test_replaces_is_kept_only_when_it_names_a_listed_item():
    extraction = _parse(pipeline=[_pipeline(replaces="P1"), _pipeline(replaces="P7"), _pipeline(replaces=" ")], current=[BOOKED])
    assert [item.replaces for item in extraction.pipeline] == ["P1", None, None]


def test_malformed_dates_and_times_are_blanked():
    (item,) = _parse(pipeline=[_pipeline(next_date="Friday", next_time="11am")]).pipeline
    assert (item.next_date, item.next_time) == ("", "")


def test_pipeline_items_without_a_company_or_valid_turns_are_dropped():
    extraction = _parse(pipeline=[_pipeline(company="  "), _pipeline(source_turns=["s9:t01"]), _pipeline()])
    assert len(extraction.pipeline) == 1


def test_next_pipeline_swaps_replaced_items_for_their_new_versions():
    moved = PipelineItem("Velloway Analytics", "BDR", "recruiter screen", "2026-10-09", "11:00", "", ("s1:t02",), "P1")
    assert next_pipeline([BOOKED, OTHER], [moved]) == [
        OTHER,
        PipelineItem("Velloway Analytics", "BDR", "recruiter screen", "2026-10-09", "11:00", "", ("s1:t02",), None),
    ]
