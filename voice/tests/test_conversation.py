from dataclasses import replace
from uuid import UUID

from app.conversation import Conversation, ConversationRegistry, HistoryTurn, reply_runs
from app.mock import Mock, Target

USER = UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")


def test_registry_returns_conversations_by_session_id():
    registry = ConversationRegistry()
    convo = Conversation(session_id="s1", user_id=USER, mode="checkin", tz="UTC")
    registry.put(convo)
    assert registry.get("s1") is convo
    assert registry.get("nope") is None


def test_registry_evicts_idle_conversations():
    registry = ConversationRegistry(max_idle_s=60)
    old = Conversation(session_id="old", user_id=USER, mode="checkin", tz="UTC", last_active=100.0)
    fresh = Conversation(session_id="fresh", user_id=USER, mode="checkin", tz="UTC", last_active=150.0)
    registry.put(old)
    registry.put(fresh)
    registry.evict_idle(now=170.0)
    assert registry.get("old") is None and registry.get("fresh") is fresh


def test_history_turn_record_marks_interruptions_only_when_true():
    convo = Conversation(session_id="s1", user_id=USER, mode="checkin", tz="UTC")
    assert convo.add("user", "hi").record() == {"id": "s1:t01", "speaker": "user", "text": "hi"}
    assert convo.add("wren", "Hey th", interrupted=True).record() == {
        "id": "s1:t02", "speaker": "wren", "text": "Hey th", "interrupted": True,
    }


def test_records_carry_the_mock_and_the_role_only_when_set():
    convo = Conversation(session_id="s1", user_id=USER, mode="checkin", tz="UTC")
    assert convo.add("user", "Ready.", mock=1).record() == {"id": "s1:t01", "speaker": "user", "text": "Ready.", "mock": 1}
    assert convo.add("wren", "Why Sprout?", mock=1, in_role=True).record() == {
        "id": "s1:t02", "speaker": "wren", "text": "Why Sprout?", "mock": 1, "as": "interviewer",
    }
    assert convo.add("wren", "Stepping out.").record() == {"id": "s1:t03", "speaker": "wren", "text": "Stepping out."}


def test_the_turn_mode_is_mock_only_while_the_interviewer_has_the_floor():
    convo = Conversation(session_id="s1", user_id=USER, mode="checkin", tz="UTC")
    assert convo.turn_mode == "checkin"
    convo.mock = Mock(1, "Dana", "recruiter", Target("Sprout Social"))
    assert convo.turn_mode == "mock"
    convo.mock = replace(convo.mock, on=False)
    assert convo.turn_mode == "checkin"


def test_mock_ids_count_past_resumed_turns():
    turns = [HistoryTurn("s1:t01", "wren", "Why?", mock=2, in_role=True)]
    assert Conversation("s1", USER, "checkin", "UTC", turns=turns).next_mock_id() == 3
    assert Conversation("s2", USER, "checkin", "UTC").next_mock_id() == 1


def test_mock_ids_count_past_the_mock_in_progress_too():
    turns = [HistoryTurn("s1:t01", "wren", "Why?", mock=2, in_role=True)]
    convo = Conversation("s1", USER, "checkin", "UTC", turns=turns)
    convo.mock = Mock(4, "Dana", "recruiter", Target("Sprout Social"))  # started, no turns yet
    assert convo.next_mock_id() == 5
    convo.mock = Mock(1, "Dana", "recruiter", Target("Sprout Social"))
    assert convo.next_mock_id() == 3


def test_reply_runs_join_lines_by_speaker():
    parts = [("Sure.", None), ("Okay, stepping into role.", None), ("I'm Dana.", 1), ("Why Sprout?", 1)]
    assert reply_runs(parts) == [("Sure. Okay, stepping into role.", None), ("I'm Dana. Why Sprout?", 1)]


def test_reply_runs_join_only_neighbors():
    parts = [("Why Sprout?", 1), ("Stepping out.", None), ("Back.", 1)]
    assert reply_runs(parts) == [("Why Sprout?", 1), ("Stepping out.", None), ("Back.", 1)]
    assert reply_runs(iter([("One.", None), ("Two.", None)])) == [("One. Two.", None)]
    assert reply_runs([]) == []
