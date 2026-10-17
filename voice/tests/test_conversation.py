from uuid import UUID

from app.conversation import Conversation, ConversationRegistry

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
