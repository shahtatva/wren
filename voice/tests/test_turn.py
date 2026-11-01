import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from time import monotonic
from uuid import UUID

import pytest

from app.conversation import Background, Conversation, Retrieved, reply_runs
from app.memory.types import PipelineEntry, Profile, SearchHit
from app.mock import CLOSER, Mock, Target
from app.turn import (
    CRISIS_FALLBACK,
    INTERVIEW_RESUME,
    INTERVIEW_START,
    RETRY_LINE,
    build_interviewer_messages,
    claims_to_be_human,
    build_messages,
    ends_a_sentence,
    run_turn,
)
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
        self.deltas, self.messages, self.max_tokens = deltas, None, None

    async def __call__(self, messages, timing, *, max_tokens=None):
        self.messages, self.max_tokens = messages, max_tokens
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


def test_the_persona_card_keeps_replies_short_and_honest_about_memory():
    content = build_messages(conversation(), "hi", [], NOW, Tuning())[0]["content"]
    assert "under thirty words" in content
    assert content.count("One question at most, as your last sentence.") == 1
    assert "Ask at most one question" not in content  # conversation.md no longer repeats the card
    assert "Don't explain how" in content and "say you don't have it" in content


def test_the_persona_card_keeps_what_the_samples_needed():
    # Each line came from `python -m app.tools.samples` (logs/session-7/mock-interviews-ledger.md).
    content = build_messages(conversation(), "hi", [], NOW, Tuning())[0]["content"]
    assert "each under fifteen words" in content  # without it the median reply ran 26 words
    assert "Write numbers as words" in content  # without it the crisis reply said 9-1-1 and 9-8-8
    assert "no question as a greeting" in content  # without it an opening asked two questions


async def _no_memories(text):
    return Retrieved([], 0.0)


class ScriptedChat:
    def __init__(self, *replies):
        self.replies, self.calls, self.retries, self.max_tokens = list(replies), 0, [], []
        self.messages, self.timings = [], []  # each call's messages and timing object

    async def __call__(self, messages, timing, *, retry=False, max_tokens=None):
        reply = self.replies[self.calls]
        self.calls += 1
        self.retries.append(retry)
        self.max_tokens.append(max_tokens)
        self.messages.append(messages)
        self.timings.append(timing)
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


def test_the_retry_asks_the_model_differently():
    chat, timing = ScriptedChat([], ["Take your time."]), TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Take your time."]
    assert chat.retries == [False, True]


@pytest.mark.parametrize("last_session", [False, True])
def test_an_opening_that_fails_twice_greets_instead_of_asking_to_repeat(last_session):
    chat, timing = ScriptedChat(["I"], ["I"]), TurnTiming(1, "checkin", "opening")
    convo = conversation(turns=0, last_session=last_session)
    (line,) = asyncio.run(_collect(run_turn(convo, None, retrieve=_no_memories, chat=chat, timing=timing)))
    assert "again?" not in line and line.endswith("?") and timing.reply_repair == "gave_up"
    assert ("an AI" in line) == (not last_session)  # a first session hears that Wren is an AI


def test_a_retried_prompt_counts_its_tokens_once():
    chat, timing = ScriptedChat(["I"], ["Take your time."]), TurnTiming(3, "checkin", "voice")
    turn_out(chat, timing)
    assert chat.calls == 2 and timing.context_tokens == sum(len(m["content"]) for m in chat.messages[0]) // 4


SPROUT_MOCK = Mock(1, "Dana", "recruiter", Target("Sprout Social", "BDR", "recruiter screen", None))


def in_mock(asked: int = 1) -> Conversation:
    """A check-in that handed off to a Sprout mock. The interviewer has asked `asked` questions."""
    convo = conversation(turns=2)
    convo.add("user", "Can we practice for Sprout?")
    convo.add("wren", "Okay, stepping into role.")
    convo.add("wren", "I'm Dana, the recruiter at Sprout Social. Walk me through your background.", mock=1, in_role=True)
    for n in range(2, asked + 1):
        convo.add("user", f"answer {n - 1}", mock=1)
        convo.add("wren", f"Question {n}?", mock=1, in_role=True)
    convo.mock = SPROUT_MOCK
    return convo


def test_a_mock_cue_hands_off_to_the_interviewer():
    chat = ScriptedChat(["Sure. [mock: Sprout]"], ["Thanks for making the time. ", "Walk me through your background."])
    convo, timing = conversation(), TurnTiming(3, "checkin", "text")
    out = asyncio.run(_collect(run_turn(convo, "Can we practice for Sprout?", retrieve=_no_memories, chat=chat, timing=timing)))
    name = convo.mock.name
    assert out == ["Sure.", "Okay, stepping into role.", f"I'm {name}, the recruiter at Sprout Social.",
                   "Thanks for making the time.", "Walk me through your background."]
    assert [s.mock for s in out] == [None, None, 1, 1, 1] and [s.switch for s in out] == [False, True, False, False, False]
    assert convo.mock.on and convo.mock.target.company == "Sprout Social" and timing.switch == "mock_start"
    system = chat.messages[1][0]["content"]
    assert f"You are {name}, the recruiter at Sprout Social" in system and "You are Wren" not in system
    assert "Ask your first question" in system and chat.messages[1][1:] == [{"role": "user", "content": INTERVIEW_START}]


def test_a_cue_alone_is_a_whole_reply_but_not_at_the_opening():
    chat = ScriptedChat(["[mock]"], ["Tell me about yourself."])
    out = asyncio.run(_collect(run_turn(conversation(), "Let's practice.", retrieve=_no_memories, chat=chat, timing=TurnTiming(3, "checkin", "text"))))
    assert chat.retries == [False, False] and out[0] == "Okay, stepping into role."
    opening = ScriptedChat(["[mock]"], ["Hey Maya. How's the search going?"])
    convo = conversation(turns=0)
    out = asyncio.run(_collect(run_turn(convo, None, retrieve=_no_memories, chat=opening, timing=TurnTiming(1, "checkin", "opening"))))
    assert convo.mock is None and opening.retries == [False, True] and out[-1] == "How's the search going?"


def test_a_cue_wren_doesnt_act_on_is_dropped_and_asked_again():
    chat, convo = ScriptedChat(["[step out]"], ["Sure. What's on your mind?"]), conversation()
    out = asyncio.run(_collect(run_turn(convo, "Hmm.", retrieve=_no_memories, chat=chat, timing=TurnTiming(3, "checkin", "text"))))
    assert out == ["Sure.", "What's on your mind?"] and chat.retries == [False, True] and convo.mock is None


def test_a_cue_closes_the_stream_so_generation_stops():
    asked = []

    async def chat(messages, timing, *, retry=False):
        call, deltas = len(asked), [["Sure. [mock", "] I'm Dana.", " Let's begin."], ["Tell me about yourself."]][len(asked)]
        asked.append([])
        try:
            for delta in deltas:
                asked[call].append(delta)
                yield delta
        finally:
            asked[call].append("closed")

    out = asyncio.run(_collect(run_turn(conversation(), "Let's practice.", retrieve=_no_memories, chat=chat, timing=TurnTiming(3, "checkin", "text"))))
    assert asked[0] == ["Sure. [mock", "] I'm Dana.", "closed"]  # nothing was asked for after the cue
    assert out[:2] == ["Sure.", "Okay, stepping into role."] and "I'm Dana." not in out


@pytest.mark.parametrize("display_name", ["omar Haddad", "Omar.", " OMAR"])
def test_the_interviewer_never_has_their_first_name(display_name):
    convo = conversation()  # session s9, whose first interviewer would be Omar
    convo.background = replace(convo.background, profile=replace(convo.background.profile, display_name=display_name))
    chat = ScriptedChat(["[mock: Sprout]"], ["Walk me through your background."])
    asyncio.run(_collect(run_turn(convo, "Can we practice for Sprout?", retrieve=_no_memories, chat=chat, timing=TurnTiming(3, "checkin", "text"))))
    assert convo.mock.name == "Lena"
    nameless = Conversation(session_id="s9", user_id=USER, mode="checkin", tz="UTC")  # no profile: the hashed name
    asyncio.run(_collect(run_turn(nameless, "Let's practice.", retrieve=_no_memories, chat=ScriptedChat(["[mock]"], ["Hi."]), timing=TurnTiming(3, "checkin", "text"))))
    assert nameless.mock.name == "Omar"


def test_the_cue_names_the_company_plainly():
    # A company that isn't in the pipeline is spoken as the cue named it, and spoken lines skip clean().
    chat, convo = ScriptedChat(["[mock: Acme (Series B)]"], ["Tell me about yourself."]), conversation()
    out = asyncio.run(_collect(run_turn(convo, "Let's practice for Acme.", retrieve=_no_memories, chat=chat, timing=TurnTiming(3, "checkin", "text"))))
    assert convo.mock.target.company == "Acme" and out[1] == f"I'm {convo.mock.name}, one of the interviewers at Acme."


def test_interviewer_turns_skip_retrieval_and_see_only_the_mock():
    calls = []

    async def retrieve(text):
        calls.append(text)
        return Retrieved(HITS, 1.0)

    chat, timing = ScriptedChat(["Okay. ", "Why Sprout?"]), TurnTiming(5, "mock", "voice")
    out = asyncio.run(_collect(run_turn(in_mock(1), "I studied information management.", retrieve=retrieve, chat=chat, timing=timing)))
    assert out == ["Okay.", "Why Sprout?"] and all(s.mock == 1 for s in out)
    assert calls == [] and timing.retrieval_start is None
    messages = chat.messages[0]
    assert "line 1" not in str(messages) and "Can we practice" not in str(messages)
    assert [m["role"] for m in messages[1:]] == ["user", "assistant", "user"]
    assert messages[-1] == {"role": "user", "content": "I studied information management."}


@pytest.mark.parametrize("asked, step", [
    (2, "Ask your next question. You've asked 2 of 4."),
    (3, "React to their answer in one short sentence, with no question. The closing question is asked for you after it."),
    (4, "You've asked all your questions"),
])
def test_the_interviewer_knows_where_it_is(asked, step):
    chat = ScriptedChat(["Okay. Next one?"])
    asyncio.run(_collect(run_turn(in_mock(asked), "My answer.", retrieve=_no_memories, chat=chat, timing=TurnTiming(5, "mock", "voice"))))
    assert step in chat.messages[0][0]["content"]


def test_the_closing_turn_reacts_then_asks_the_scripted_last_question():
    # In the samples the model skipped "What questions do you have for me?" every time, even when told it exactly.
    chat, timing = ScriptedChat(["Thanks for walking me through that."]), TurnTiming(5, "mock", "voice")
    out = asyncio.run(_collect(run_turn(in_mock(3), "My answer.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Thanks for walking me through that.", CLOSER] and CLOSER == "What questions do you have for me?"
    assert [s.mock for s in out] == [1, 1] and chat.calls == 1 and timing.switch is None  # the model still reads it
    failed = ScriptedChat(["I"], ["I"])  # no whole sentence, twice
    out = asyncio.run(_collect(run_turn(in_mock(3), "My answer.", retrieve=_no_memories, chat=failed, timing=TurnTiming(5, "mock", "voice"))))
    assert out == ["Thanks.", CLOSER]


def test_the_closing_turn_drops_a_question_in_the_reaction():
    # The scripted closer is the turn's one question.
    chat, timing = ScriptedChat(["Thanks for walking me through that. ", 'Did they ever ask you "why now?"']), TurnTiming(5, "mock", "voice")
    out = asyncio.run(_collect(run_turn(in_mock(3), "My answer.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Thanks for walking me through that.", CLOSER] and timing.reply_repair == "dropped_question"
    chat, timing = ScriptedChat(["Got it. ", "Tell me more about that."]), TurnTiming(5, "mock", "voice")
    out = asyncio.run(_collect(run_turn(in_mock(3), "My answer.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Got it.", CLOSER] and timing.reply_repair == "dropped_question"


@pytest.mark.parametrize("reply", [
    ["How did the team take it?"], ["Okay, tell me how that went."], ["Walk me through the numbers."],
    ["What happened next? ", "Describe the result."],
])
def test_a_reaction_that_only_asks_gives_thanks_with_no_second_call(reply):
    chat, timing = ScriptedChat(reply), TurnTiming(5, "mock", "voice")
    out = asyncio.run(_collect(run_turn(in_mock(3), "My answer.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Thanks.", CLOSER] and [s.mock for s in out] == [1, 1]
    assert chat.calls == 1 and timing.reply_repair == "dropped_question"


def test_the_closing_reaction_keeps_one_sentence_that_asks_nothing():
    chat, timing = ScriptedChat(["How did they take it? ", "Thanks for sharing that. ", "It's a clear example."]), TurnTiming(5, "mock", "voice")
    out = asyncio.run(_collect(run_turn(in_mock(3), "My answer.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Thanks for sharing that.", CLOSER]
    chat, timing = ScriptedChat(["Thanks for that. ", "It's a clear example."]), TurnTiming(5, "mock", "voice")
    out = asyncio.run(_collect(run_turn(in_mock(3), "My answer.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Thanks for that.", CLOSER] and timing.reply_repair is None  # nothing asked, only cut to one


def test_on_the_closing_turn_the_claim_guard_comes_before_the_question_check():
    chat, timing = ScriptedChat(["I'm a real person, aren't I?"], ["Thanks for that."]), TurnTiming(5, "mock", "voice")
    out = asyncio.run(_collect(run_turn(in_mock(3), "My answer.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Thanks for that.", CLOSER] and chat.calls == 2 and timing.reply_repair == "dropped_claim"


def test_a_question_before_the_interviewers_cue_still_steps_out():
    convo = in_mock(3)
    out, chat, timing, _ = step_out_turn(convo, "Honestly, I'm not doing great these days.",
                                         ["Are you okay? [step out]"], ["I'm here. What's going on?"])
    assert out == ["Stepping out.", "It's me again.", "I'm here.", "What's going on?"]
    assert timing.switch == "step_out:cue" and convo.mock.last_step_out == "cue" and chat.calls == 2
    assert f"{convo.mock.name}: Are you okay?" not in chat.messages[1][0]["content"]  # never said, so not in the mock


def say(convo: Conversation, text: str, *replies) -> tuple[list, ScriptedChat, TurnTiming]:
    """One turn, saved as POST /turn saves it: their words, then one Wren turn per speaker."""
    floor = convo.mock
    chat, timing = ScriptedChat(*replies), TurnTiming(9, convo.turn_mode, "text")
    out = asyncio.run(_collect(run_turn(convo, text, retrieve=_no_memories, chat=chat, timing=timing)))
    convo.add("user", text, mock=floor.id if floor is not None and floor.on else None)
    for words, mock in reply_runs((spoken, spoken.mock) for spoken in out):
        convo.add("wren", words, mock=mock, in_role=mock is not None)
    return out, chat, timing


def test_a_pickup_after_the_third_question_still_gets_the_closing_turn():
    # The pickup asks its question again, saved in role. It's not a new question, so the closing turn still comes.
    convo = in_mock(3)
    say(convo, "Wren, can I ask you something?", ["Of course. What's on your mind?"])
    _, _, timing = say(convo, "Never mind, let's keep going.", ["[mock]"], ["So, back to it. Question 3?"])
    assert timing.switch == "mock_resume" and convo.mock.reasks == 1
    out, chat, _ = say(convo, "My answer.", ["Thanks for walking me through that."])
    assert out == ["Thanks for walking me through that.", CLOSER]
    assert "React to their answer in one short sentence" in chat.messages[0][0]["content"]


def test_a_pickup_after_the_first_question_keeps_the_count():
    convo = in_mock(1)
    say(convo, "Wren, can I ask you something?", ["Of course. What's on your mind?"])
    say(convo, "Never mind, let's keep going.", ["[mock]"], ["Back to it. Walk me through your background."])
    _, chat, _ = say(convo, "My answer.", ["Okay. ", "Why Sprout?"])
    assert "- Next: Ask your next question. You've asked 1 of 4.\n" in chat.messages[0][0]["content"]


def test_a_question_asked_again_doesnt_count_toward_the_cap():
    convo = in_mock(6)
    convo.mock = replace(SPROUT_MOCK, reasks=1)  # one of its six lines in role asked a question again
    _, chat, timing, _ = step_out_turn(convo, "Thanks!", ["Thanks for your time. [step out]"], ["Good work. How did that feel?"])
    assert chat.calls == 2 and timing.switch == "step_out:done"  # the interviewer still had its turn


def test_a_crisis_reply_that_fails_twice_still_gives_real_help():
    assert CRISIS_FALLBACK == (
        "If you're in danger right now, call nine one one. You can call or text nine eight eight any time."
    )
    out, _, timing, _ = step_out_turn(in_mock(2), "I keep thinking about killing myself.", ["I"], ["I"])
    assert out == ["Stepping out.", "It's me again.", CRISIS_FALLBACK] and timing.reply_repair == "gave_up"
    out, _, _, _ = step_out_turn(in_mock(2), "Are you a bot?", ["I"], ["I"])  # any other step-out asks again
    assert out[-1] == RETRY_LINE


def test_a_step_out_on_the_closing_turn_still_steps_out():
    convo = in_mock(3)
    flirt = "Before I answer, I have to say, you have a really nice voice. Are you single?"
    out, chat, timing, _ = step_out_turn(convo, flirt, ["[step out]"], ["That's kind. I'm an AI. Want to pick it back up?"])
    assert out[:2] == ["Stepping out.", "It's me again."] and CLOSER not in out and timing.switch == "step_out:cue"
    assert convo.mock.resumable and "hold the debrief" in chat.messages[1][0]["content"]


def test_the_scripted_closer_is_saved_in_role_and_counts_as_a_question():
    convo = in_mock(3)
    chat = ScriptedChat(["Thanks for that."])
    out = asyncio.run(_collect(run_turn(convo, "My answer.", retrieve=_no_memories, chat=chat, timing=TurnTiming(5, "mock", "voice"))))
    convo.add("user", "My answer.", mock=1)  # saved as the session and POST /turn save them
    for text, mock in reply_runs((spoken, spoken.mock) for spoken in out):
        convo.add("wren", text, mock=mock, in_role=mock is not None)
    assert (convo.turns[-1].text, convo.turns[-1].in_role) == ("Thanks for that. What questions do you have for me?", True)
    chat = ScriptedChat(["Mostly outreach. Thanks for your time. [step out]"], ["Your opener landed. How did that feel?"])
    asyncio.run(_collect(run_turn(convo, "What's a typical day like?", retrieve=_no_memories, chat=chat, timing=TurnTiming(6, "mock", "voice"))))
    assert "You've asked all your questions" in chat.messages[0][0]["content"]


def test_the_interviewer_card_rules_out_praise_and_asks_one_question_a_turn():
    system = build_interviewer_messages(in_mock(2), "My answer.", Tuning())[0]["content"]
    assert '- No coaching, praise or feedback, not even "great" or "solid". That\'s for the debrief.' in system
    # One question that joins two asks counts as one question.
    assert "- Ask one question per turn, then stop and let them answer. A short follow-up counts as your question.\n" in system


def test_the_interviewer_card_fills_in_what_isnt_known():
    sprout = build_interviewer_messages(in_mock(1), "hi", Tuning())[0]["content"]
    assert "- Stage: recruiter screen" in sprout and "- Candidate: Maya. MS in Information Management, May 2026." in sprout
    convo = Conversation(session_id="s1", user_id=USER, mode="checkin", tz="UTC")
    convo.mock = Mock(1, "Theo", "interviewer", Target())
    system = build_interviewer_messages(convo, "hi", Tuning())[0]["content"]
    for line in ("You are Theo, an interviewer.", "- Company: not named. Don't invent one.", "- Role: not named",
                 "- Stage: a first-round interview", "- Notes: none", "- Candidate: not given",
                 "like a real interviewer at this stage"):
        assert line in system, line


def test_the_interviewers_messages_alternate_and_a_resume_comes_last():
    convo = in_mock(1)
    convo.add("user", "answer 1", mock=1)  # the interviewer's reply to it was never heard
    messages = build_interviewer_messages(convo, "Hello?", Tuning())
    assert [m["role"] for m in messages[1:]] == ["user", "assistant", "user"]
    assert messages[-1] == {"role": "user", "content": "answer 1\nHello?"}
    resumed = build_interviewer_messages(convo, None, Tuning(), resumed=True)  # leaves out what came after its last line
    assert resumed[-1] == {"role": "user", "content": INTERVIEW_RESUME}


def test_after_a_restart_role_lines_are_marked_and_merged():
    convo = conversation(turns=0)  # convo.mock is None, as after a server restart
    convo.add("user", "Let's practice.")
    convo.add("wren", "Okay, stepping into role.")
    convo.add("wren", "I'm Dana. Why Sprout?", mock=1, in_role=True)
    messages = build_messages(convo, "Sorry, where were we?", [], NOW, Tuning())
    assert messages[2] == {"role": "assistant", "content": "Okay, stepping into role.\n[As the interviewer] I'm Dana. Why Sprout?"}


def test_a_second_model_call_doesnt_rewrite_the_path_to_first_audio():
    chat, timing = ScriptedChat(["[mock]"], ["Tell me about yourself."]), TurnTiming(3, "checkin", "text")
    asyncio.run(_collect(run_turn(conversation(), "Let's practice.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert chat.timings[0] is timing and chat.timings[1] is not timing  # the interviewer's call writes a scratch timing
    assert timing.model == "fake-model"  # copied back from the scratch
    assert timing.context_tokens == sum(sum(len(m["content"]) for m in call) // 4 for call in chat.messages)


def test_the_interviewers_call_reports_its_model_but_keeps_the_first_token():
    calls = []

    async def chat(messages, timing, *, retry=False):
        calls.append(timing)
        timing.model, timing.llm_first_token = f"model-{len(calls)}", float(len(calls))
        if len(calls) == 1:
            timing.chat_fallback = "RateLimited"
            yield "[mock]"
        else:
            timing.finish_reason, timing.completion_tokens = "stop", 9
            yield "Tell me about yourself."

    timing = TurnTiming(3, "checkin", "text")
    asyncio.run(_collect(run_turn(conversation(), "Let's practice.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert timing.llm_first_token == 1.0  # the interviewer's first token came after the switch line was ready
    assert (timing.model, timing.finish_reason, timing.completion_tokens) == ("model-2", "stop", 9)
    assert timing.chat_fallback == "RateLimited"  # the scratch never set it, so Wren's stays


def test_the_interviewer_card_steps_out_for_every_boundary():
    system = build_interviewer_messages(in_mock(1), "hi", Tuning())[0]["content"]
    for rule in ("ask whether you're an AI", "suicide, self-harm or being in danger", "under eighteen", "flirt", "ask for Wren"):
        assert rule in system, rule


def step_out_turn(convo, text, *replies):
    chat, timing, calls = ScriptedChat(*replies), TurnTiming(7, "mock", "voice"), []

    async def retrieve(t):
        calls.append(t)
        return Retrieved([], 0.0)

    out = asyncio.run(_collect(run_turn(convo, text, retrieve=retrieve, chat=chat, timing=timing)))
    return out, chat, timing, calls


def test_stop_words_bring_wren_back_to_debrief_without_the_interviewer():
    convo = in_mock(3)
    out, chat, timing, calls = step_out_turn(convo, "Okay, let's stop there.",
                                             ["Your opener landed. ", "The Sprout answer drifted. ", "Lead with the result. ", "How did that feel?"])
    assert out[:3] == ["Stepping out.", "It's me again.", "Your opener landed."] and out[0].switch
    assert chat.calls == 1 and timing.switch == "step_out:stop" and calls == ["Okay, let's stop there."]
    assert timing.llm_first_token is None and chat.timings[0] is not timing  # a scripted line opened the turn
    system = chat.messages[0][0]["content"]
    assert "You are Wren" in system and "Debrief it now" in system
    assert "Dana: I'm Dana, the recruiter at Sprout Social. Walk me through your background." in system
    assert "Candidate: answer 1" in system and "Candidate: Okay, let's stop there." in system
    assert not convo.mock.on and not convo.mock.resumable


def test_stopping_before_any_answer_skips_the_debrief():
    _, chat, _, _ = step_out_turn(in_mock(1), "Actually, can we stop the interview?", ["Sure. What would you like to do instead?"])
    assert "nothing to debrief" in chat.messages[0][0]["content"]


def test_a_question_about_being_an_ai_breaks_role_at_once():
    convo = in_mock(2)
    out, chat, timing, _ = step_out_turn(convo, "Wait, are you a real person?", ["No, I'm an AI. ", "Want to pick it back up?"])
    assert out[:2] == ["Stepping out.", "It's me again."] and timing.switch == "step_out:ai"
    assert "whether you're an AI" in chat.messages[0][0]["content"] and convo.mock.resumable


def test_a_crisis_mention_in_role_gets_wren_and_no_debrief():
    _, chat, timing, _ = step_out_turn(in_mock(2), "I keep thinking about killing myself.", ["I'm really glad you told me."])
    system = chat.messages[0][0]["content"]
    assert timing.switch == "step_out:crisis" and "Don't debrief" in system and "nine eight eight" in system


def test_the_interviewers_closing_cue_leads_into_the_debrief():
    out, chat, timing, _ = step_out_turn(in_mock(4), "No questions from me.",
                                         ["Thanks, that's everything I had. [step out]"], ["Your close was strong. ", "How did that feel?"])
    assert out[:3] == ["Thanks, that's everything I had.", "Stepping out.", "It's me again."]
    assert out[0].mock == 1 and out[1].mock is None and timing.switch == "step_out:done"
    assert "Debrief it now" in chat.messages[1][0]["content"] and "Dana: Thanks, that's everything I had." in chat.messages[1][0]["content"]
    assert "six at most, even if their last words were a question for the interviewer." in chat.messages[1][0]["content"]


def test_a_step_out_cue_mid_interview_holds_the_debrief():
    convo = in_mock(2)
    _, chat, timing, _ = step_out_turn(convo, "You sound cute. Are you single?",
                                       ["[step out]"], ["That's kind. I'm an AI, so I'll stick to interview practice. Back to Sprout?"])
    assert timing.switch == "step_out:cue" and "hold the debrief" in chat.messages[1][0]["content"] and convo.mock.resumable


def test_a_mock_that_runs_past_its_questions_is_ended_by_the_code():
    _, chat, timing, _ = step_out_turn(in_mock(6), "Thanks!", ["Good work. How did that feel?"])
    assert chat.calls == 1 and timing.switch == "step_out:done"


def test_a_cue_after_a_pause_picks_the_same_mock_back_up():
    convo = in_mock(2)
    convo.add("user", "Wait, are you a real person?", mock=1)
    convo.add("wren", "Stepping out. It's me again. No, I'm an AI. Want to pick it back up?")
    convo.mock = replace(SPROUT_MOCK, on=False, resumable=True)
    chat, timing = ScriptedChat(["[mock]"], ["Where were we. ", "Why Sprout?"]), TurnTiming(9, "checkin", "voice")
    out = asyncio.run(_collect(run_turn(convo, "Yes, let's keep going.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out[0] == "Okay, stepping back into role." and out[0].switch and timing.switch == "mock_resume"
    assert (convo.mock.id, convo.mock.on, convo.mock.name) == (1, True, "Dana")
    assert "Question 2?" in str(chat.messages[1]) and chat.messages[1][-1]["content"].endswith(INTERVIEW_RESUME)


def test_a_cue_after_a_debrief_starts_a_new_mock():
    convo = in_mock(2)
    convo.mock = replace(SPROUT_MOCK, on=False, resumable=False)
    chat, timing = ScriptedChat(["[mock]"], ["Tell me about yourself."]), TurnTiming(9, "checkin", "voice")
    asyncio.run(_collect(run_turn(convo, "Let's do another one.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert convo.mock.id == 2 and timing.switch == "mock_start"


def test_wren_keeps_the_last_mock_in_view_for_a_few_turns():
    convo = in_mock(2)
    convo.add("user", "Okay, let's stop there.", mock=1)
    convo.add("wren", "Stepping out. It's me again. Your opener landed.")
    convo.mock = replace(SPROUT_MOCK, on=False)
    messages = build_messages(convo, "What about my second answer?", [], NOW, Tuning())
    assert "Candidate: answer 1" in messages[0]["content"] and "If they ask about it, use this" in messages[0]["content"]
    assert messages[0]["content"].rstrip().endswith(
        "If they want to go on with it, write only [mock: company]."
    )
    assert not any("Question 2?" in m["content"] for m in messages[1:])  # the mock's lines appear once, in the block
    for i in range(6):
        convo.add("user" if i % 2 == 0 else "wren", f"later {i}")
    assert "The mock interview" not in build_messages(convo, "hi", [], NOW, Tuning())[0]["content"]


def test_wren_after_a_step_out_never_starts_another_mock_in_the_same_turn():
    convo = in_mock(2)
    out, _, timing, _ = step_out_turn(convo, "Are you a bot?", ["Yes, I'm an AI. [mock]"])
    assert not convo.mock.on and timing.switch == "step_out:ai" and "Okay, stepping into role." not in out


def test_a_step_out_reply_that_is_only_a_mock_cue_is_asked_again():
    out, chat, _, _ = step_out_turn(in_mock(2), "Are you a bot?", ["[mock]"], ["Yes, I'm an AI."])
    assert chat.retries == [False, True] and out == ["Stepping out.", "It's me again.", "Yes, I'm an AI."]


def test_a_pause_holds_the_debrief_and_can_pick_back_up():
    convo = in_mock(2)
    _, chat, timing, _ = step_out_turn(convo, "Wren, can I ask you something?", ["Of course. What's on your mind?"])
    assert timing.switch == "step_out:pause" and "hold the debrief" in chat.messages[0][0]["content"] and convo.mock.resumable
    # In the samples Wren asked the interviewer's questions itself, in its own voice, with no switch.
    assert "back up unless a boundary rules it out. Don't ask the interviewer's questions yourself." in chat.messages[0][0]["content"]


@pytest.mark.parametrize("cue, mock_id, switch", [
    ("[mock: sprout]", 1, "mock_resume"),
    ("[mock: company]", 1, "mock_resume"),  # the prompt's placeholder names no company
    ("[mock: Acme]", 2, "mock_start"),
])
def test_a_paused_mock_picks_back_up_only_for_the_same_company(cue, mock_id, switch):
    convo = in_mock(2)
    convo.mock = replace(SPROUT_MOCK, on=False, resumable=True)
    chat, timing = ScriptedChat([cue], ["Tell me about yourself."]), TurnTiming(9, "checkin", "voice")
    asyncio.run(_collect(run_turn(convo, "Let's keep going.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert (convo.mock.id, convo.mock.on, timing.switch) == (mock_id, True, switch)


def test_stepping_out_shows_the_whole_mock_in_order_and_leaves_it_out_of_the_history():
    _, chat, _, _ = step_out_turn(in_mock(5), "Let's stop there.", ["Your opener landed."])
    messages = chat.messages[0]
    assert (  # the introduction is older than the last six turns
        "Word for word:\nDana: I'm Dana, the recruiter at Sprout Social. Walk me through your background.\n"
        "Candidate: answer 1\nDana: Question 2?\n"
    ) in messages[0]["content"]
    assert "Dana: Question 5?\nCandidate: Let's stop there.\n" in messages[0]["content"]
    assert not any("Question 5?" in m["content"] for m in messages[1:])
    assert messages[-1] == {"role": "user", "content": "Let's stop there."}


def test_a_boundary_only_the_interviewer_caught_comes_first_even_in_a_debrief():
    # The code leaves "I don't want to be here anymore" to the interviewer: it's also a real answer.
    _, chat, timing, _ = step_out_turn(in_mock(4), "Honestly, I don't want to be here anymore.",
                                       ["[step out]"], ["I'm really glad you told me."])
    system = chat.messages[1][0]["content"]
    assert timing.switch == "step_out:done" and "Debrief it now" in system and "Your boundaries come first" in system


def test_a_done_mock_ends_and_a_crisis_one_can_pick_back_up():
    done, crisis = in_mock(6), in_mock(2)
    step_out_turn(done, "Thanks!", ["Good work. How did that feel?"])
    step_out_turn(crisis, "I keep thinking about killing myself.", ["I'm really glad you told me."])
    assert (done.mock.resumable, done.mock.last_step_out) == (False, "done")
    assert (crisis.mock.resumable, crisis.mock.last_step_out) == (True, "crisis")


def paused_at_question_2() -> Conversation:
    """in_mock(2), after "Wait, are you a real person?" made Wren step out and answer."""
    convo = in_mock(2)
    convo.add("user", "Wait, are you a real person?", mock=1)
    convo.add("wren", "Stepping out. It's me again. No, I'm an AI. Want to pick it back up?")
    convo.mock = replace(SPROUT_MOCK, on=False, resumable=True, last_step_out="ai")
    return convo


def test_a_pickup_leaves_out_the_words_that_stepped_out():
    messages = build_interviewer_messages(paused_at_question_2(), None, Tuning(), resumed=True)
    assert not any("Wait, are you a real person?" in m["content"] for m in messages)  # Wren already answered it
    assert messages[-2:] == [{"role": "assistant", "content": "Question 2?"}, {"role": "user", "content": INTERVIEW_RESUME}]


@pytest.mark.parametrize("asked", [2, 4])
def test_a_pickup_asks_the_question_it_was_on_again(asked):
    system = build_interviewer_messages(in_mock(asked), None, Tuning(), resumed=True)[0]["content"]
    assert "- Next: You're picking the interview back up. Ask the question you were on again, in your own words.\n" in system
    assert "You've asked" not in system  # not even after the last question


@pytest.mark.parametrize("mock_lines, line", [
    (True, "Question 2?"),
    (False, "Let's start simple. Walk me through your background."),  # nothing to ask again
])
def test_a_pickup_that_fails_twice_asks_its_last_question_again(mock_lines, line):
    convo = paused_at_question_2() if mock_lines else conversation()
    convo.mock = replace(SPROUT_MOCK, on=False, resumable=True, last_step_out="ai")  # paused, with lines or none
    chat = ScriptedChat(["[mock]"], ["I"], ["I"])
    out = asyncio.run(_collect(run_turn(convo, "Yes, let's keep going.", retrieve=_no_memories, chat=chat, timing=TurnTiming(9, "checkin", "voice"))))
    assert out == ["Okay, stepping back into role.", line] and out[-1].mock == 1
    assert convo.mock.on and convo.mock.last_step_out is None


def test_after_a_crisis_the_mock_leaves_wrens_prompt():
    convo = in_mock(2)
    _, chat, _, _ = step_out_turn(convo, "I keep thinking about killing myself.", ["I'm really glad you told me."])
    assert "Don't debrief" in chat.messages[0][0]["content"]  # the turn that stepped out shows it, with CRISIS
    convo.add("user", "I keep thinking about killing myself.", mock=1)
    convo.add("wren", "Stepping out. It's me again. I'm really glad you told me.")
    messages = build_messages(convo, "Thanks. I'm safe now.", [], NOW, Tuning())
    assert "The mock interview" not in messages[0]["content"]
    history = "\n".join(m["content"] for m in messages[1:])
    assert "I keep thinking about killing myself." in history and "[As the interviewer] Question 2?" in history


def test_a_reply_after_any_step_out_gets_the_step_out_token_budget():
    budget = Tuning().max_step_out_tokens
    assert budget == 800
    for text in ("Okay, let's stop there.", "I keep thinking about killing myself.", "Are you a bot?"):
        _, chat, _, _ = step_out_turn(in_mock(3), text, ["Okay."])
        assert chat.max_tokens == [budget], text
    # The interviewer's own call keeps the usual cap. Wren's reply after its cue gets the budget, retries included.
    replies = (["Thanks for your time. [step out]"], ["[mock]"], ["Okay."])
    _, chat, _, _ = step_out_turn(in_mock(4), "No questions from me.", *replies)
    assert chat.max_tokens == [None, budget, budget] and chat.retries == [False, False, True]


def test_a_reply_with_the_mock_in_view_gets_the_step_out_budget():
    convo = in_mock(2)  # stopped, with the mock still among the recent turns: an AFTER turn can debrief
    convo.add("user", "Okay, let's stop there.", mock=1)
    convo.add("wren", "Stepping out. It's me again. Your opener landed.")
    convo.mock = replace(SPROUT_MOCK, on=False)
    chat = ScriptedChat(["Your second answer drifted. ", "Lead with the result."])
    asyncio.run(_collect(run_turn(convo, "What about my second answer?", retrieve=_no_memories, chat=chat, timing=TurnTiming(9, "checkin", "voice"))))
    assert chat.max_tokens == [Tuning().max_step_out_tokens]
    for i in range(6):  # the mock leaves the recent turns, and its block with it
        convo.add("user" if i % 2 == 0 else "wren", f"later {i}")
    chat = ScriptedChat(["Sure."])
    asyncio.run(_collect(run_turn(convo, "Thanks.", retrieve=_no_memories, chat=chat, timing=TurnTiming(13, "checkin", "voice"))))
    assert chat.max_tokens == [None]


def test_an_ordinary_reply_keeps_the_usual_cap():
    chat = ScriptedChat(["Sprout's tomorrow at eleven."])
    turn_out(chat, TurnTiming(3, "checkin", "voice"))
    assert chat.max_tokens == [None]


def test_the_interviewer_card_talks_for_the_ear():
    system = build_interviewer_messages(in_mock(1), "hi", Tuning())[0]["content"]
    assert "links or parentheses" in system and "Write numbers as words" in system


@pytest.mark.parametrize("text", [
    "Yes, I'm the real recruiter here.", "I am a real person, I promise.", "I’m actually the real one.",
    "I'm human.", "I am a person, like you.", "Honestly, I'm a real person, yes.", "I'm a real hiring manager.",
    # From any speaker, the interviewer included.
    "I'm Jordan, a real person at Sprout Social.", "Yes, I'm Jordan, the real recruiter here.", "No, I'm not an AI.",
    "I'm not a bot.", "Yes, I'm real.", "I'm actually human.", "I'm just a person.", "I am really a person.",
    "I'm a real live person.",
    # Worded other ways.
    "No, I'm not a language model.", "I'm not a large language model.", "No, I'm not an A.I.", "I'm not an LLM.",
    "I'm a human recruiter.", "I'm a human interviewer at Sprout.", "I'm definitely human.", "I'm totally real.",
    "I'm really a person.", "I'm Jordan, a human recruiter at Sprout Social.",
])
def test_a_claim_to_be_human_is_caught(text):
    assert claims_to_be_human(text) and claims_to_be_human(text, interviewer="Jordan")


@pytest.mark.parametrize("text", [
    "I'm not a real person.", "I'm an AI, not a human.", "I'm Wren.", "I'm the recruiter at Sprout Social.",
    "I'm actually an AI.", "I'm Jordanna's coach.", "I'm not human.", "I'm Wren, not Jordan.",
    "Try opening with: I'm a person who turns complaints into fixes.", "I'm a human resources partner at Acme.",
    "I'm Jordan's coach.", "It's me. I'm Wren, the real one.", "I'm not a language person, I'm a numbers person.",
])
def test_saying_its_an_ai_is_not_a_claim_to_be_human(text):
    assert not claims_to_be_human(text, interviewer="Jordan")


def test_a_dropped_claim_is_logged_cut_short_so_misses_can_be_checked(caplog):
    claim = "Yes, I'm the real recruiter here, and I've been hiring for the sales team at Sprout Social for years."
    assert len(claim) > 80
    out = asyncio.run(_collect(run_turn(
        in_mock(2), "My answer.", retrieve=_no_memories, chat=ScriptedChat([claim], ["Why Sprout?"]),
        timing=TurnTiming(5, "mock", "voice"),
    )))
    assert out == ["Why Sprout?"] and claim[:80] in caplog.text and claim[:81] not in caplog.text


def test_wren_speaking_as_the_interviewer_by_name_is_a_claim():
    assert claims_to_be_human("Yes, I’m Jordan.", interviewer="Jordan")
    assert claims_to_be_human("I am Jordan, the recruiter.", interviewer="Jordan")
    assert not claims_to_be_human("Yes, I'm Jordan.")  # with no name to guard, as for the interviewer's own lines


def paused_with_jordan() -> Conversation:
    convo = paused_at_question_2()
    convo.mock = replace(convo.mock, name="Jordan")
    return convo


def test_wren_never_speaks_a_claim_to_be_the_interviewer_and_asks_again(caplog):
    convo, timing = paused_with_jordan(), TurnTiming(9, "checkin", "voice")
    claim = ["Yes, I'm Jordan, the real recruiter here. ", "What excites you about the role?"]
    chat = ScriptedChat(claim, ["[mock: Sprout Social]"], ["Why Sprout?"])
    out = asyncio.run(_collect(run_turn(convo, "Yes, let's keep going.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Okay, stepping back into role.", "Why Sprout?"] and timing.switch == "mock_resume"
    # The ask again keeps its reasoning (retry=True is for replies short of tokens), and the log says why.
    assert chat.retries == [False, False, False] and timing.reply_repair == "dropped_claim"
    assert "claimed to be human" in caplog.text and "no whole sentence" not in caplog.text
    twice = ScriptedChat(claim, ["I'm a real person, honestly."])
    out = asyncio.run(_collect(run_turn(paused_with_jordan(), "Yes, let's keep going.", retrieve=_no_memories, chat=twice, timing=TurnTiming(9, "checkin", "voice"))))
    assert out == [RETRY_LINE]


def test_a_cue_after_a_claim_is_dropped_with_it():
    convo, timing = paused_with_jordan(), TurnTiming(9, "checkin", "voice")
    chat = ScriptedChat(["I'm Jordan. [mock]"], ["Want to pick it back up?"])
    out = asyncio.run(_collect(run_turn(convo, "Yes, let's keep going.", retrieve=_no_memories, chat=chat, timing=timing)))
    assert out == ["Want to pick it back up?"] and not convo.mock.on and timing.switch is None


def test_saying_its_an_ai_is_spoken_in_full():
    out, _, timing, _ = step_out_turn(in_mock(2), "Wait, are you a real person?",
                                      ["I'm an AI, not a real person. ", "Want to pick the interview back up?"])
    assert out[2:] == ["I'm an AI, not a real person.", "Want to pick the interview back up?"]
    assert timing.reply_repair is None


def test_an_interviewer_claiming_to_be_a_real_person_is_never_heard():
    chat = ScriptedChat(["Honestly, I'm a real person, yes."], ["Okay. ", "Why Sprout?"])
    out = asyncio.run(_collect(run_turn(in_mock(2), "My answer.", retrieve=_no_memories, chat=chat, timing=TurnTiming(5, "mock", "voice"))))
    assert out == ["Okay.", "Why Sprout?"] and [s.mock for s in out] == [1, 1] and chat.retries == [False, False]


def test_a_claim_mid_reply_keeps_what_came_before_it_and_stops_generation():
    pulled = []

    async def chat(messages, timing, *, retry=False):
        try:
            for delta in ["Sure. ", "I'm the real recruiter. ", "Ask me anything. ", "And more. ", "And more."]:
                pulled.append(delta)
                yield delta
        finally:
            pulled.append("closed")

    timing = TurnTiming(3, "checkin", "voice")
    assert turn_out(chat, timing) == ["Sure."] and timing.reply_repair == "dropped_claim"
    assert pulled[-1] == "closed" and len(pulled) < 6  # the stream closed before its last deltas


def test_an_interviewer_that_fails_its_first_question_starts_simple():
    convo, chat = conversation(), ScriptedChat(["[mock]"], ["I"], ["I"])
    out = asyncio.run(_collect(run_turn(convo, "Let's practice.", retrieve=_no_memories, chat=chat, timing=TurnTiming(3, "checkin", "text"))))
    assert out[-1] == "Let's start simple. Walk me through your background." and out[-1].mock == 1
    later = ScriptedChat(["I"], ["I"])  # after an answer, asking to hear it again fits
    out = asyncio.run(_collect(run_turn(in_mock(2), "My answer.", retrieve=_no_memories, chat=later, timing=TurnTiming(5, "mock", "voice"))))
    assert out == ["I lost my train of thought. Could you say that again?"] and out[0].mock == 1
