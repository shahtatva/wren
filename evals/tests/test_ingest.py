from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from app.memory.types import PipelineItem, Session, Turn
from evals.evalset import load_eval_set
from evals.ingest import advance_pipeline, chunk_items, pipeline_text, to_app_session, turn_items, user_id_for

FIXTURE = Path(__file__).parent / "fixtures" / "mini"
SESSION = Session(
    "s1",
    datetime(2026, 10, 3, 18, tzinfo=timezone.utc),
    "checkin",
    (
        Turn("s1:t01", "wren", "one two three"),
        Turn("s1:t02", "user", "four five"),
        Turn("s1:t03", "wren", "six seven eight nine ten eleven twelve"),
        Turn("s1:t04", "user", "thirteen"),
    ),
)


def words(text: str) -> int:
    return len(text.split())


def _item(company, stage, replaces=None, turns=("s1:t02",)):
    return PipelineItem(company, "", stage, "", "", "", turns, replaces)


def test_to_app_session_keeps_ids_speakers_and_date():
    session = to_app_session(load_eval_set(FIXTURE).transcripts["ana-s01"])
    assert session.id == "ana-s01"
    assert session.started_at == datetime(2026, 10, 1, 18, tzinfo=timezone.utc)
    assert session.mode == "checkin"
    assert [(t.id, t.speaker) for t in session.turns][:2] == [("ana-s01:t01", "wren"), ("ana-s01:t02", "user")]


def test_turn_items_are_one_per_turn_with_a_speaker_label():
    items = turn_items(SESSION)
    assert [(i.kind, i.text, i.source_turns) for i in items[:2]] == [
        ("turn", "Wren: one two three", ("s1:t01",)),
        ("turn", "User: four five", ("s1:t02",)),
    ]
    assert len(items) == 4


def test_chunks_pack_whole_turns_up_to_the_limit():
    # Lines are 4, 3, 8 and 2 words. With a limit of 8: [t01 t02] is 7, then t03 alone is 8, then t04.
    chunks = chunk_items(SESSION, max_tokens=8, count=words)
    assert [c.source_turns for c in chunks] == [("s1:t01", "s1:t02"), ("s1:t03",), ("s1:t04",)]
    assert chunks[0].text == "Wren: one two three\nUser: four five"
    assert {c.kind for c in chunks} == {"chunk"}


def test_a_turn_longer_than_the_limit_gets_a_chunk_of_its_own():
    chunks = chunk_items(SESSION, max_tokens=5, count=words)
    assert [c.source_turns for c in chunks] == [("s1:t01",), ("s1:t02",), ("s1:t03",), ("s1:t04",)]


def test_pipeline_text_reads_the_date_and_time():
    item = PipelineItem("Velloway Analytics", "BDR", "recruiter screen", "2026-10-09", "11:00", "Call with the recruiter.", ("s1:t02",), None)
    assert pipeline_text(item) == "Velloway Analytics, BDR: recruiter screen, next on Friday October 9, 2026 at 11:00. Call with the recruiter."
    assert pipeline_text(_item("Corvid Labs", "rejected")) == "Corvid Labs: rejected."


def test_advance_pipeline_supersedes_replaced_items():
    a, b, c = UUID(int=1), UUID(int=2), UUID(int=3)
    booked, take_home = _item("Velloway Analytics", "recruiter screen"), _item("Quillbase", "take-home")
    moved = _item("Velloway Analytics", "recruiter screen", replaces="P1")
    current, superseded = advance_pipeline([(booked, a), (take_home, b)], [(moved, c)])
    assert superseded == [(a, c)]
    assert current == [(take_home, b), (_item("Velloway Analytics", "recruiter screen"), c)]


def test_user_ids_are_stable_and_differ_per_storage_and_persona():
    assert user_id_for("atomic", "maya") == user_id_for("atomic", "maya")
    assert len({user_id_for(s, p) for s in ("chunk512", "turn", "atomic") for p in ("maya", "dev")}) == 6
