import asyncio
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from uuid import UUID

import pytest

from app.conversation import Background, Conversation, Retrieved
from app.memory.types import PipelineEntry, Profile, SearchHit
from app.turn import build_messages, ends_a_sentence, run_turn
from app.voice.metrics import TurnTiming
from app.voice.tuning import Tuning

USER = UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")
# Thursday 2026-10-15, 7:40 pm in New York
NOW = datetime(2026, 10, 15, 23, 40, tzinfo=timezone.utc)
HITS = [
    SearchHit("m1", "story", "Led a five-person consulting-club project that cut donor churn by 15 percent", ("s1:t04",), 0.04),
    SearchHit("m2", "feedback", "Rambles on tell me about yourself, past two minutes", ("s2:t09",), 0.03),
]


def conversation(turns: int = 8, last_session: bool = True) -> Conversation:
    background = Background(
        profile=Profile("Maya", "MS in Information Management, May 2026", ("BDR", "analyst"), "America/New_York"),
        pipeline=(PipelineEntry("Sprout Social", "BDR", "recruiter screen", date(2026, 10, 16), time(11, 0), None),),
        last_session_at=NOW - timedelta(days=2, hours=1) if last_session else None,
    )
    convo = Conversation(session_id="s9", user_id=USER, mode="checkin", tz="America/New_York", background=background)
    for i in range(turns):
        convo.add("user" if i % 2 == 0 else "wren", f"line {i + 1}")
    return convo


def test_turn_ids_count_up_within_the_session():
    convo = conversation(turns=2)
    assert [t.id for t in convo.turns] == ["s9:t01", "s9:t02"]
    assert convo.add("user", "next").id == "s9:t03"


def test_messages_hold_persona_signals_memories_and_last_six_turns():
    messages = build_messages(conversation(), "Sprout moved my screen", HITS, NOW, Tuning())
    system = messages[0]
    assert system["role"] == "system"
    content = system["content"]
    assert "You are Wren" in content
    assert "Thursday evening" in content and "7:40 PM" in content
    assert "2 days since your last session" in content
    assert "Sprout Social" in content and "tomorrow" in content and "11:00 AM" in content
    assert "MS in Information Management" in content
    assert "[story] Led a five-person consulting-club project" in content
    assert "[feedback] Rambles on tell me about yourself" in content
    history = messages[1:-1]
    assert [m["content"] for m in history] == [f"line {i}" for i in range(3, 9)]
    assert [m["role"] for m in history] == ["user", "assistant"] * 3
    assert messages[-1] == {"role": "user", "content": "Sprout moved my screen"}


def test_empty_background_says_so():
    convo = Conversation(session_id="s1", user_id=USER, mode="mock", tz="UTC")
    content = build_messages(convo, "hi", [], NOW, Tuning())[0]["content"]
    assert "first session together" in content
    assert "Nothing scheduled." in content and "Nothing relevant yet." in content
    assert "mock interview" in content


def test_interrupted_wren_line_is_marked():
    convo = conversation(turns=0)
    convo.add("user", "How do I fix my opener?")
    convo.add("wren", "Lead with the number. Then", interrupted=True)
    assistant = build_messages(convo, "Which number?", [], NOW, Tuning())[2]
    assert assistant["content"] == "Lead with the number. Then [cut off here. They didn't hear the rest]"


async def _collect(gen):
    return [s async for s in gen]


class FakeChat:
    def __init__(self, deltas):
        self.deltas, self.messages = deltas, None

    async def __call__(self, messages, timing):
        self.messages = messages
        timing.model = "fake-model"
        for d in self.deltas:
            if timing.llm_first_token is None:
                timing.llm_first_token = 1.0
            yield d


def test_opening_turn_skips_retrieval_and_asks_for_a_greeting():
    calls = []

    async def retrieve(text):
        calls.append(text)
        return Retrieved([], 0.0)

    chat = FakeChat(["Hey Maya. How's the week going?"])
    timing = TurnTiming(turn=1, mode="checkin", input="opening")
    convo = conversation(turns=0, last_session=False)
    out = asyncio.run(_collect(run_turn(convo, None, retrieve=retrieve, chat=chat, timing=timing)))
    assert out == ["Hey Maya.", "How's the week going?"]
    assert calls == [] and timing.retrieval_start is None
    system = chat.messages[0]["content"]
    assert "session is just starting" in system and "you're an AI" in system
    assert chat.messages[-1]["role"] == "user"


def test_run_turn_yields_clean_segments_and_records_timing():
    async def retrieve(text):
        assert text == "Sprout moved my screen"
        return Retrieved(HITS, 210.0, fallback=None)

    chat = FakeChat(["**Sprout** moved to Friday. ", "That gives you a night. ", "Want to run it once?"])
    timing = TurnTiming(turn=3, mode="checkin", input="voice")
    out = asyncio.run(_collect(run_turn(conversation(), "Sprout moved my screen", retrieve=retrieve, chat=chat, timing=timing)))
    assert out == ["Sprout moved to Friday.", "That gives you a night.", "Want to run it once?"]
    assert timing.retrieval_start is not None and timing.retrieval_end >= timing.retrieval_start
    assert timing.memories == 2 and timing.retrieval_fallback is None
    assert timing.llm_start is not None and timing.first_segment >= timing.llm_start
    assert timing.context_tokens > 100 and timing.model == "fake-model"


def test_retrieval_failure_keeps_the_turn_going():
    async def retrieve(text):
        raise RuntimeError("database is down")

    chat = FakeChat(["Got it. Tell me more?"])
    timing = TurnTiming(turn=3, mode="checkin", input="text")
    out = asyncio.run(_collect(run_turn(conversation(), "hello", retrieve=retrieve, chat=chat, timing=timing)))
    assert out == ["Got it.", "Tell me more?"]
    assert timing.retrieval_fallback == "error:RuntimeError" and timing.memories == 0
    assert "Nothing relevant yet." in chat.messages[0]["content"]


def test_retrieval_fallback_is_recorded():
    async def retrieve(text):
        return Retrieved(HITS[:1], 400.0, fallback="TimeoutError")

    timing = TurnTiming(turn=3, mode="checkin", input="text")
    asyncio.run(_collect(run_turn(conversation(), "hi", retrieve=retrieve, chat=FakeChat(["Hi."]), timing=timing)))
    assert timing.retrieval_fallback == "TimeoutError" and timing.memories == 1


@pytest.mark.parametrize("hour, part", [(13, "morning"), (19, "afternoon"), (2, "night")])
def test_part_of_day_follows_the_local_clock(hour, part):
    # New York is UTC-4 in October.
    now = datetime(2026, 10, 15, hour, 0, tzinfo=timezone.utc)
    content = build_messages(conversation(), "hi", [], now, Tuning())[0]["content"]
    assert part in content


def test_a_slow_retrieval_is_dropped_at_the_deadline():
    async def retrieve(text):
        await asyncio.sleep(2)
        return Retrieved(HITS, 2_000.0)

    chat = FakeChat(["Got it."])
    timing = TurnTiming(turn=3, mode="checkin", input="voice")
    started = monotonic()
    out = asyncio.run(_collect(run_turn(
        conversation(), "hello", retrieve=retrieve, chat=chat, timing=timing, tuning=Tuning(retrieval_timeout_s=0.05),
    )))
    assert out == ["Got it."] and monotonic() - started < 1.0
    assert timing.retrieval_fallback == "timeout" and timing.memories == 0
    assert "Nothing relevant yet." in chat.messages[0]["content"]


def test_the_persona_card_holds_every_boundary():
    content = build_messages(conversation(), "hi", [], NOW, Tuning())[0]["content"]
    for rule in (
        "you are an AI", "No romance", "Never diagnose", "nine one one", "nine eight eight",
        "someone they trust", "only if they say they are", "under eighteen", "school counselor or career office",
    ):
        assert rule in content, rule


async def _no_memories(text):
    return Retrieved([], 0.0)


class ScriptedChat:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    async def __call__(self, messages, timing):
        reply = self.replies[self.calls]
        self.calls += 1
        timing.model = "fake-model"
        for delta in reply:
            yield delta


def turn_out(chat, timing):
    return asyncio.run(_collect(run_turn(conversation(), "Elaborate, please.", retrieve=_no_memories, chat=chat, timing=timing)))


def test_a_reply_that_stops_mid_sentence_is_asked_again():
    chat, timing = ScriptedChat(["I have a"], ["I keep notes between sessions."]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["I keep notes between sessions."]
    assert chat.calls == 2 and timing.reply_repair == "retried"


def test_an_empty_reply_is_asked_again():
    chat, timing = ScriptedChat([], ["Take your time."]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Take your time."]


def test_a_trailing_fragment_after_whole_sentences_is_dropped():
    chat, timing = ScriptedChat(["Let's get back to your search. How"]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Let's get back to your search."]
    assert chat.calls == 1 and timing.reply_repair == "dropped_tail"


def test_two_broken_replies_end_with_the_retry_line():
    chat, timing = ScriptedChat(["I"], ["I"]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["I lost my train of thought. Could you say that again?"]
    assert timing.reply_repair == "gave_up"


def test_ends_a_sentence_accepts_ellipses_and_closing_quotes():
    assert all(map(ends_a_sentence, ["Done.", "Really?", "Wait…", 'He said "go."', "(Sure.)"]))
    assert not any(map(ends_a_sentence, ["I have a", "Nice to meet you,", ""]))


def test_the_first_sentence_is_yielded_before_the_reply_ends():
    release = asyncio.Event()

    async def slow_chat(messages, timing):
        yield "First sentence here. "
        await release.wait()  # the rest of the reply hasn't come yet
        yield "Second."

    async def first_piece():
        gen = run_turn(conversation(), "hi", retrieve=_no_memories, chat=slow_chat, timing=TurnTiming(3, "checkin", "voice"))
        piece = await asyncio.wait_for(anext(gen), 1.0)
        await gen.aclose()
        return piece

    assert asyncio.run(first_piece()) == "First sentence here."


def test_a_second_answer_glued_to_the_first_is_dropped():
    # gpt-oss sometimes finishes its answer and starts another, with no space between them.
    chat, timing = ScriptedChat(["Take your time. What's the company?Whenever you're ready, just tell me the name."]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Take your time.", "What's the company?"]
    assert timing.reply_repair == "dropped_stacked"


def test_a_glued_answer_across_deltas_is_dropped_and_the_stream_closed():
    closed = []

    async def chat(messages, timing):
        try:
            for delta in ("Sure, tell me the company when you're ready.", "Okay, let's focus", " on that company. "):
                yield delta
        finally:
            closed.append(True)

    timing = TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Sure, tell me the company when you're ready."]
    assert closed == [True] and timing.reply_repair == "dropped_stacked"


def test_glued_looking_text_inside_a_sentence_is_kept():
    chat, timing = ScriptedChat(["It's 2.5 times better on Node.js in the U.S. Want to try?"]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["It's 2.5 times better on Node.js in the U.S.", "Want to try?"]
    assert timing.reply_repair is None


@pytest.mark.parametrize("reply", [
    "Since you have a Ph.D. in chemistry, lead with the lab work. What was the project?",
    "Your Socket.IO project is a good story. Want to try it?",
    "Check their page on www.LinkedIn.com before Monday. Ready?",
])
def test_degrees_and_tech_names_are_not_taken_for_a_second_answer(reply):
    timing = TurnTiming(3, "checkin", "voice")
    out = turn_out(ScriptedChat([reply]), timing)
    assert timing.reply_repair is None and out[-1].endswith("?")


def test_a_glued_answer_split_mid_word_across_deltas_is_dropped():
    chat, timing = ScriptedChat(["Sure, tell me the company when you're ready.Whenev", "er you're ready, just tell me. "]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Sure, tell me the company when you're ready."]
    assert timing.reply_repair == "dropped_stacked"


def test_a_glued_answer_starting_with_i_is_dropped():
    chat, timing = ScriptedChat(["Take your time.I'm here when you're ready. "]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Take your time."]
    assert timing.reply_repair == "dropped_stacked"
