"""/turn: one conversational turn, with the context rebuilt from scratch. Design: CLAUDE.md "Memory design".

Voice, typed text, POST /turn and evals all run this same function, so text mode and
voice mode share one pipeline. It retrieves memories for the user's words, builds the
context (persona card, live signals, profile, the next few days of pipeline, retrieved
memories, the last few turns), streams the reply, and yields it one speakable segment
at a time.

A reply comes out whole. Each sentence is yielded as soon as it's complete, but the tail
the model stops on is dropped unless it ends a sentence, so a reply cut mid-word is never
spoken or saved. gpt-oss sometimes finishes an answer and starts a second one with no
space between them; the reply ends with the first. A reply with no whole sentence at all
(empty, or one fragment) is asked for once more, before any audio, and if that fails too
Wren says RETRY_LINE, or at the opening, a plain greeting. A sentence that claims to be human
or a real person ("I'm the real recruiter") is never spoken: the reply ends before it, and
if nothing was said yet, it's asked for once more the same way.

Mock interviews. Wren starts one by writing a cue, [mock: company], instead of talking. The
cue is caught before it's spoken: Wren says its switch line, an interviewer introduces itself,
and a separate interviewer prompt asks the first question, all in the same turn. While the
mock is on, the user's words go to the interviewer, with no retrieval: it sees only its card
and the mock's own lines. Its last question, "What questions do you have for me?", is scripted
(CLOSER), with no model call. Every segment says who speaks it (Spoken).

Stepping out. Before the interviewer answers, the code checks the user's words (step_out_reason),
and ends the mock for the interviewer once it runs two turns past its questions. The interviewer
can also hand the floor back by writing [step out]. Either way Wren says "Stepping out. It's me
again." and answers as itself, with the mock word for word in its prompt and an instruction that
fits why it stepped out: debrief it, hold the debrief, say it's an AI, or answer a crisis with
no debrief. That reply gets more room than usual (Tuning.max_step_out_tokens). Unless the interview
was done or they asked to stop it, Wren's next [mock] cue picks it back up. The last mock stays in
Wren's prompt while any of its lines are among the recent turns, unless Wren stepped out for a crisis.

The stages come in as callables, like retrieve()'s, so the server, evals and tests can
each wire their own. Retrieval failing never fails the turn: Wren answers without memories.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import aclosing
from dataclasses import replace
from datetime import date, datetime, time, timezone
from functools import cache
from pathlib import Path
from typing import Literal, Protocol, Self
from zoneinfo import ZoneInfo

from app.conversation import Conversation, HistoryTurn, Retrieved
from app.memory.types import PipelineEntry, Profile, SearchHit
from app.mock import (
    CLOSER, CLOSER_LEAD, STEP_BACK_IN, STEP_IN, STEP_OUT, Cue, CueFilter, Mock, interviewer_name, interviewer_title,
    plain_company, resolve_target, same_company, step_out_reason,
)
from app.voice import clock
from app.voice.metrics import TurnTiming
from app.voice.segment import Segmenter
from app.voice.tuning import Tuning

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent / "prompts"
CUT_OFF = " [cut off here. They didn't hear the rest]"
OPENING = "[The session just started.]"
INTERVIEW_START = "[The interview starts now.]"
INTERVIEW_RESUME = "[The interview picks back up after a break.]"
ROLE_MARK = "[As the interviewer] "  # starts a line Wren spoke in role, in Wren's own history
MODES = {"checkin": "a check-in", "mock": "mock interview practice"}
RETRY_LINE = "I lost my train of thought. Could you say that again?"
# When the opening fails twice there's nothing to say again, so Wren just greets.
OPENING_LINE = "Hey, good to have you back. How's the search going?"
FIRST_OPENING_LINE = "Hi, I'm Wren, an AI here to help with your job search. How's it going so far?"
# When the interviewer's first question fails twice, there's no answer to ask for again, so it starts simple.
FIRST_QUESTION_LINE = "Let's start simple. Walk me through your background."

# What Wren does with the mock in its prompt (debrief.md), by why it took the floor back this turn.
DEBRIEF = (  # done, or stop after an answer
    "You've just stepped out. Debrief it now in short spoken sentences, six at most, even if their last words were"
    " a question for the interviewer. Say what landed and what drifted in this mock, using their actual words or"
    " stories from it."
    " Give one fix. If it fits, say a friend could run the next one with them. End by asking how it felt. Your"
    " boundaries come first: if what they just said needs one of them, like a crisis, answer that as yourself"
    " instead, and skip the debrief."
)
NOTHING_YET = (  # stop before any answer
    "They stopped before answering anything, so there's nothing to debrief yet. Ask what they'd like to do."
)
HOLD = (  # pause, or the interviewer's cue mid-interview
    "You stepped out because of what they just said. Answer that as yourself, and your boundaries come first."
    " Then hold the debrief, and offer to pick the interview back up unless a boundary rules it out. Don't ask the"
    " interviewer's questions yourself. Debrief now only if they asked to stop or end the interview: what landed,"
    " what drifted, and one fix, then ask how it felt."
)
AI_ASKED = (
    "You stepped out because they asked whether you're an AI or a person. Say plainly that you're an AI, then"
    " offer to pick the interview back up."
)
CRISIS = (
    "You stepped out because of what they just said. Answer it as yourself, and your boundaries come first."
    " Don't debrief, and don't bring up the interview or practice, not even for later."
)
AFTER = (  # a later turn, while the mock's lines are recent
    "If they ask about it, use this. If they want feedback you haven't given yet, debrief it: what landed, what"
    " drifted, and one fix. If they want to go on with it, write only [mock: company]."
)
_INSTRUCTIONS = {"done": DEBRIEF, "stop": DEBRIEF, "pause": HOLD, "cue": HOLD, "ai": AI_ASKED, "crisis": CRISIS}
_RESUMABLE = ("pause", "cue", "ai", "crisis")  # done and stop end the mock

_END_MARK = r"[.!?\u2026][\"'\u201d\u2019)\]]*"
_SENTENCE_END = re.compile(_END_MARK + "$")
# "...ready.Whenever you're ready, ...": a sentence end with no space, then a word that starts a
# sentence. Not "Ph.D.", "Socket.IO" or "www.LinkedIn.com": the next word must look like a word.
_GLUED_ANSWER = re.compile(r"(?<=[a-z0-9])" + _END_MARK + r"(?=(?:[A-Z][a-z]+|[AI])[\s,;:!?.'\u2019])")

# A spoken line that claims to be human or a real person. Normalized to straight apostrophes first.
_APOSTROPHES = str.maketrans("‘’", "''")
_I_AM = r"\bi(?:'m|\s+am)\s+"
_SO = r"(?:(?:just|really|actually|truly)\s+)?"  # "I'm actually human", "I am really a person"
_REAL_ONE = r"(?:real|actual)\s+(?:live\s+)?(?:person|human(?:\s+being)?|recruiter|interviewer|hiring\s+manager|one)\b"
_CLAUSE_ENDS = r"(?=\s*(?:[.!?,;:]|$))"  # so "I'm a person who..." and "a human resources partner" aren't claims
_CLAIM = re.compile(
    _I_AM + _SO + r"(?:(?:a|an|the)\s+)?" + _REAL_ONE  # "I'm the real recruiter", "I'm a real live person"
    + r"|" + _I_AM + r"(?-i:[A-Z][a-z]+),\s+(?:a|an|the)\s+" + _REAL_ONE  # "I'm Jordan, a real person at Sprout"
    + r"|" + _I_AM + _SO + r"(?:a\s+)?(?:human(?:\s+being)?|person)" + _CLAUSE_ENDS  # "I'm just a person."
    + r"|" + _I_AM + _SO + r"real" + _CLAUSE_ENDS  # "Yes, I'm real."
    # "No, I'm not an AI.", "I'm not a bot."
    + r"|" + _I_AM + r"not\s+(?:an?\s+)?(?:ai|a\.i\.|bot|chat\s?bot|robot|machine|computer|program)\b",
    re.IGNORECASE,
)

Retrieve = Callable[[str], Awaitable[Retrieved]]


def claims_to_be_human(text: str, interviewer: str | None = None) -> bool:
    """Whether a line matches the patterns of a claim to be human or a real person: "I'm the real recruiter",
    "I'm just a person.", "No, I'm not an AI." interviewer is the mock's name, for Wren's own lines: "I'm
    Jordan" from Wren speaks as the interviewer with no switch ("I'm Jordan's coach" doesn't). "I'm not a
    real person", "I'm an AI, not a human" and "I'm a person who turns complaints into fixes" don't count."""
    text = text.translate(_APOSTROPHES)
    if _CLAIM.search(text):
        return True
    name = rf"{_I_AM}{re.escape(interviewer)}\b(?!'s)" if interviewer else None
    return name is not None and re.search(name, text, re.IGNORECASE) is not None


class Chat(Protocol):
    """Streams the reply. retry=True is the second ask, after a reply with no whole sentence. max_tokens, when
    given, replaces the usual cap on the reply."""

    def __call__(
        self, messages: list[dict], timing: TurnTiming, *, retry: bool = False, max_tokens: int | None = None
    ) -> AsyncIterator[str]: ...


class Spoken(str):
    """A segment to say, and who says it: Wren, or with mock set, that mock's interviewer. switch
    marks the line that hands over the floor. It's a str, so code that only wants the words keeps working."""

    mock: int | None
    switch: bool

    def __new__(cls, text: str, mock: int | None = None, switch: bool = False) -> Self:
        spoken = super().__new__(cls, text)
        spoken.mock, spoken.switch = mock, switch
        return spoken


def ends_a_sentence(text: str) -> bool:
    return _SENTENCE_END.search(text.rstrip()) is not None


@cache
def _prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def _part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _clock_time(t: time | datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def _day(day: date, today: date) -> str:
    label = f"{day:%A}, {day:%B} {day.day}"
    return label + {0: " (today)", 1: " (tomorrow)"}.get((day - today).days, "")


def _signals(convo: Conversation, now_local: datetime, opening: bool) -> str:
    lines = [
        f"- Mode: {MODES.get(convo.mode, convo.mode)}.",
        f"- Their local time: {now_local:%A} {_part_of_day(now_local.hour)}, {_clock_time(now_local)}.",
    ]
    last = convo.background.last_session_at
    if last is None:
        lines.append("- This is your first session together.")
    else:
        days = (now_local.date() - last.astimezone(now_local.tzinfo).date()).days
        if days <= 0:
            lines.append("- Your last session was earlier today.")
        else:
            lines.append(f"- It's been {days} day{'s' if days > 1 else ''} since your last session.")
    if opening:
        lines.append(
            "- The session is just starting. Open in under 25 words: a short greeting, then one question about"
            " their search. If something in their pipeline is coming up soon, name it."
        )
        if last is None:
            lines.append("- Say plainly, in a few words, that you're an AI.")
    return "\n".join(lines)


def _profile(profile: Profile | None) -> str:
    if profile is None:
        return "- Nothing yet."
    lines = []
    if profile.display_name:
        lines.append(f"- Name: {profile.display_name}")
    if profile.summary:
        lines.append(f"- {profile.summary}")
    if profile.target_roles:
        lines.append(f"- Targeting: {', '.join(profile.target_roles)}")
    return "\n".join(lines) or "- Nothing yet."


def _pipeline(entries: tuple[PipelineEntry, ...], today: date) -> str:
    if not entries:
        return "- Nothing scheduled."
    lines = []
    for entry in entries:
        what = ", ".join(part for part in (entry.company, entry.role, entry.stage) if part)
        when = _day(entry.next_date, today) if entry.next_date else "no date yet"
        if entry.next_time:
            when += f" at {_clock_time(entry.next_time)}"
        lines.append(f"- {what}: {when}" + (f". {entry.notes}" if entry.notes else ""))
    return "\n".join(lines)


def _memories(hits: list[SearchHit]) -> str:
    if not hits:
        return "- Nothing relevant yet."
    return "\n".join(f"- [{hit.kind}] {hit.text}" for hit in hits)


def _message(turn: HistoryTurn, *, mark_role: bool) -> dict:
    """A saved turn as a chat message. In Wren's own history, the lines it spoke in role are marked."""
    if turn.speaker == "user":
        return {"role": "user", "content": turn.text}
    mark = ROLE_MARK if mark_role and turn.in_role else ""
    return {"role": "assistant", "content": mark + turn.text + (CUT_OFF if turn.interrupted else "")}


def _alternating(messages: list[dict]) -> list[dict]:
    """Neighbors with the same role joined with a newline: Gemini's fallback wants the roles to alternate."""
    joined: list[dict] = []
    for message in messages:
        if joined and joined[-1]["role"] == message["role"]:
            joined[-1] = {"role": message["role"], "content": f"{joined[-1]['content']}\n{message['content']}"}
        else:
            joined.append(message)
    return joined


def _mock_turns(convo: Conversation, mock: Mock) -> list[HistoryTurn]:
    """The mock's saved lines, the interviewer's and the user's, in order."""
    return [turn for turn in convo.turns if turn.mock == mock.id]


def _shown_mock(convo: Conversation, history: list[HistoryTurn], step_out: str | None) -> Mock | None:
    """The mock Wren sees word for word: the one it just stepped out of, or the last one while any of its
    lines are among the recent turns. After a crisis, only the turn that stepped out sees it: practice waits
    until they say they're safe, so later turns don't offer feedback."""
    mock = convo.mock
    if mock is None or (step_out is None and mock.last_step_out == "crisis"):
        return None
    if step_out is not None or any(turn.mock == mock.id for turn in history):
        return mock
    return None


def _mock_block(convo: Conversation, mock: Mock, step_out: str | None, unsaved: Sequence[HistoryTurn]) -> str:
    """debrief.md: the whole mock word for word, then what Wren does with it."""
    saved = _mock_turns(convo, mock)
    transcript = "\n".join(
        f"{'Candidate' if turn.speaker == 'user' else mock.name}: {turn.text}" for turn in (*saved, *unsaved)
    )
    if step_out == "stop" and not any(turn.speaker == "user" for turn in saved):
        instruction = NOTHING_YET  # the stop request is all they said in the mock
    else:
        instruction = AFTER if step_out is None else _INSTRUCTIONS[step_out]
    return _prompt("debrief.md").format(
        name=mock.name, who=mock.who(), transcript=transcript, instruction=instruction
    )


def _recent(convo: Conversation, tuning: Tuning) -> list[HistoryTurn]:
    """The last few turns: the history Wren's prompt shows."""
    return convo.turns[-tuning.history_turns :] if tuning.history_turns else []


def build_messages(
    convo: Conversation, user_text: str | None, hits: list[SearchHit], now_utc: datetime, tuning: Tuning, *,
    step_out: str | None = None, unsaved: Sequence[HistoryTurn] = (),
) -> list[dict]:
    """Wren's context. step_out is why Wren took the floor back from the interviewer this turn, and unsaved
    holds this turn's mock lines, which aren't in convo yet. The mock Wren just stepped out of, or the last
    one while any of its lines are among the recent turns, is shown word for word in its own block, and its
    lines leave the history, so they appear once."""
    now_local = now_utc.astimezone(ZoneInfo(convo.tz))
    history = _recent(convo, tuning)
    shown = _shown_mock(convo, history, step_out)
    if shown is not None:
        history = [turn for turn in history if turn.mock != shown.id]
    system = _prompt("conversation.md").format(
        persona=_prompt("persona.md"),
        signals=_signals(convo, now_local, opening=user_text is None),
        profile=_profile(convo.background.profile),
        pipeline_days=tuning.pipeline_days,
        pipeline=_pipeline(convo.background.pipeline, now_local.date()),
        memories=_memories(hits),
        mock="" if shown is None else _mock_block(convo, shown, step_out, unsaved),
    )
    messages = [{"role": "system", "content": system}]
    messages += [_message(turn, mark_role=True) for turn in history]
    messages.append({"role": "user", "content": OPENING if user_text is None else user_text})
    return _alternating(messages)


def _first_name(profile: Profile | None) -> str | None:
    """The first word of their name, which the interviewer's name mustn't be."""
    found = re.search(r"[^\W\d_]+", (profile.display_name or "") if profile is not None else "")
    return found.group() if found else None


def _candidate(profile: Profile | None) -> str:
    """Their name and background, as an interviewer would read them on a resume, or "not given"."""
    if profile is None:
        return "not given"
    parts = [part.strip() for part in (profile.display_name, profile.summary) if part and part.strip()]
    return " ".join(part if ends_a_sentence(part) else f"{part}." for part in parts) or "not given"


def _next_step(asked: int, total: int, *, resumed: bool = False) -> str:
    """What the interviewer does next, from how many questions it has asked: its turns in the mock. On a
    pickup, wherever it was, it asks the question it was on again. Before the last question it only
    reacts: _interview speaks "What questions do you have for me?" after it."""
    if resumed:
        return "You're picking the interview back up. Ask the question you were on again, in your own words."
    if asked == 0:
        return "You've just introduced yourself. Ask your first question."
    if asked < total - 1:
        return f"Ask your next question. You've asked {asked} of {total}."
    if asked == total - 1:
        return (
            "React to their answer in one short sentence, with no question. The closing question is asked for you"
            " after it."
        )
    return (
        "You've asked all your questions. If they asked you anything, answer in a sentence or two, in general terms."
        " Then thank them in one short sentence and write [step out]. Don't ask anything more."
    )


def build_interviewer_messages(
    convo: Conversation, user_text: str | None, tuning: Tuning, *, resumed: bool = False
) -> list[dict]:
    """The interviewer's context: its card, a fixed start line, then this mock's lines and nothing else,
    with no memories. user_text is their answer, or None when the interviewer speaks first: at the
    start of the mock, or when it picks back up (resumed). A pickup ends on the interviewer's last line,
    leaving out what they said after it: that made Wren step out, and Wren already answered it."""
    mock = convo.mock
    assert mock is not None, "only a conversation with a mock has an interviewer"
    turns = _mock_turns(convo, mock)
    while resumed and turns and not turns[-1].in_role:
        turns.pop()
    target = mock.target
    system = _prompt("interviewer.md").format(
        name=mock.name,
        who=mock.who(),
        title=mock.title,
        company=target.company or "not named. Don't invent one.",
        role=target.role or "not named",
        stage=target.stage or "a first-round interview",
        notes=target.notes or "none",
        candidate=_candidate(convo.background.profile),
        next_step=_next_step(sum(turn.in_role for turn in turns), tuning.mock_questions, resumed=resumed),
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": INTERVIEW_START}]
    messages += [_message(turn, mark_role=False) for turn in turns]
    if user_text is not None:
        messages.append({"role": "user", "content": user_text})
    if resumed:
        messages.append({"role": "user", "content": INTERVIEW_RESUME})
    return _alternating(messages)


async def run_turn(
    convo: Conversation,
    user_text: str | None,
    *,
    retrieve: Retrieve,
    chat: Chat,
    timing: TurnTiming,
    tuning: Tuning = Tuning(),
    utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> AsyncIterator[Spoken]:
    """Yield the reply as speakable segments, each saying who speaks it. user_text=None opens the session."""
    async with aclosing(_turn(convo, user_text, retrieve, chat, timing, tuning, utcnow)) as segments:
        async for spoken in segments:
            if timing.first_segment is None:  # what's said after this isn't on the path to first audio
                timing.first_segment = clock.now()
            yield spoken


def _turn(
    convo: Conversation, user_text: str | None, retrieve: Retrieve, chat: Chat, timing: TurnTiming, tuning: Tuning,
    utcnow: Callable[[], datetime],
) -> AsyncIterator[Spoken]:
    """Who answers: the interviewer while a mock is on, else Wren."""
    mock = convo.mock
    if user_text is not None and mock is not None and mock.on:  # the interviewer has the floor
        return _interview(convo, mock, user_text, retrieve, chat, timing, tuning, utcnow)
    return _wren(convo, user_text, retrieve, chat, timing, tuning, utcnow)


async def _interview(
    convo: Conversation, mock: Mock, user_text: str, retrieve: Retrieve, chat: Chat, timing: TurnTiming,
    tuning: Tuning, utcnow: Callable[[], datetime],
) -> AsyncIterator[Spoken]:
    """The user's words while the interviewer has the floor. The code checks them first, and ends a mock
    that runs two turns past its questions. If neither stops it, the interviewer answers in role, and can
    hand the floor back with [step out]. When Wren takes the floor back, it says so, then answers with the
    mock in view."""
    asked = sum(turn.in_role for turn in _mock_turns(convo, mock))  # the interviewer's questions before this turn
    unsaved = [_unsaved(mock, "user", user_text)]  # spoken while the interviewer had the floor
    reason = step_out_reason(user_text) or ("done" if asked >= tuning.mock_questions + 2 else None)
    if reason is None:
        # Before the last question, the model only reacts, and can still step out. The question itself is scripted.
        closing = asked == tuning.mock_questions - 1
        cues, said = CueFilter(), []
        messages = build_interviewer_messages(convo, user_text, tuning)
        fallback = CLOSER_LEAD if closing else RETRY_LINE
        async with aclosing(_reply(messages, chat, timing, cues, mock=mock.id, fallback=fallback)) as reply:
            async for spoken in reply:
                said.append(spoken)
                yield spoken
        if cues.cue is None:  # the interview goes on
            if closing:
                yield Spoken(CLOSER, mock=mock.id)
            return
        reason = "done" if asked >= tuning.mock_questions else "cue"
        if said:
            unsaved.append(_unsaved(mock, "wren", " ".join(said)))
    convo.mock = replace(mock, on=False, resumable=reason in _RESUMABLE, last_step_out=reason)
    timing.switch = f"step_out:{reason}"
    for spoken in _switch_lines(STEP_OUT):
        yield spoken
    wren = _wren(convo, user_text, retrieve, chat, timing, tuning, utcnow, step_out=reason, unsaved=unsaved)
    async with aclosing(wren) as reply:
        async for spoken in reply:
            yield spoken


async def _wren(
    convo: Conversation, user_text: str | None, retrieve: Retrieve, chat: Chat, timing: TurnTiming, tuning: Tuning,
    utcnow: Callable[[], datetime], *, step_out: str | None = None, unsaved: Sequence[HistoryTurn] = (),
) -> AsyncIterator[Spoken]:
    """Wren's reply, with memories for the user's words. step_out says why Wren just took the floor back
    from the interviewer, and unsaved holds this turn's mock lines. Otherwise, a [mock] cue in a reply to
    the user's words starts a mock, or picks a paused one back up."""
    hits = [] if user_text is None else await _retrieve_memories(user_text, retrieve, timing, tuning)
    messages = build_messages(convo, user_text, hits, utcnow(), tuning, step_out=step_out, unsaved=unsaved)
    if user_text is None:  # the opening acts on no cue
        cues, fallback = None, (FIRST_OPENING_LINE if convo.background.last_session_at is None else OPENING_LINE)
    elif step_out is not None:  # nor does the reply that just stepped out: a [mock] there is dropped
        cues, fallback = None, RETRY_LINE
    else:
        cues, fallback = CueFilter(), RETRY_LINE
    # A reply with the mock in view, after a step-out or on a later turn that can debrief, gets more room than the
    # usual cap: debriefs ran into it and lost their fix.
    in_view = _shown_mock(convo, _recent(convo, tuning), step_out) is not None
    max_tokens = tuning.max_step_out_tokens if in_view else None
    interviewer = convo.mock.name if convo.mock is not None else None  # Wren never speaks as the interviewer
    reply = _reply(
        messages, chat, timing, cues, mock=None, fallback=fallback, max_tokens=max_tokens, interviewer=interviewer,
    )
    async with aclosing(reply) as reply:
        async for spoken in reply:
            yield spoken
    if cues is not None and cues.cue is not None:
        mock = convo.mock
        if mock is not None and _resumes(mock, cues.cue):
            switch = _pick_back_up(convo, mock, chat, timing, tuning)
        else:
            switch = _hand_off(convo, cues.cue, chat, timing, tuning)
        async with aclosing(switch) as lines:
            async for spoken in lines:
                yield spoken


async def _retrieve_memories(
    user_text: str, retrieve: Retrieve, timing: TurnTiming, tuning: Tuning
) -> list[SearchHit]:
    """Memories for the user's words, within the deadline. Failing never fails the turn."""
    hits: list[SearchHit] = []
    timing.retrieval_start = clock.now()
    try:
        found = await asyncio.wait_for(retrieve(user_text), tuning.retrieval_timeout_s)
        hits = list(found.hits[: tuning.memories_k])
        timing.retrieval_fallback = found.fallback
    except TimeoutError:  # before Exception, which would record it as "error:TimeoutError"
        logger.warning("retrieval took over %.1f s, answering without memories", tuning.retrieval_timeout_s)
        timing.retrieval_fallback = "timeout"
    except Exception as e:  # the live path never fails on retrieval
        logger.warning("retrieval failed, answering without memories: %s: %s", type(e).__name__, e)
        timing.retrieval_fallback = f"error:{type(e).__name__}"
    timing.retrieval_end = clock.now()
    timing.memories = len(hits)
    return hits


async def _hand_off(
    convo: Conversation, cue: Cue, chat: Chat, timing: TurnTiming, tuning: Tuning
) -> AsyncIterator[Spoken]:
    """Start the mock Wren cued: Wren's switch line, the interviewer's introduction, then its first question."""
    background = convo.background
    roles = background.profile.target_roles if background.profile else ()
    target = resolve_target(plain_company(cue.company), background.pipeline, roles)
    mock_id = convo.next_mock_id()
    name = interviewer_name(convo.session_id, mock_id, avoid=_first_name(background.profile))
    mock = Mock(mock_id, name, interviewer_title(target.stage), target)
    convo.mock, timing.switch = mock, "mock_start"
    for spoken in _switch_lines(STEP_IN):
        yield spoken
    yield Spoken(mock.introduction(), mock=mock.id)
    messages = build_interviewer_messages(convo, None, tuning)
    first = _reply(messages, chat, timing, None, mock=mock.id, fallback=FIRST_QUESTION_LINE)
    async with aclosing(first) as reply:
        async for spoken in reply:
            yield spoken


def _resumes(mock: Mock, cue: Cue) -> bool:
    """Whether a [mock] cue picks this mock back up: it was paused, and the cue names no company, or the same one."""
    company = plain_company(cue.company)
    return mock.resumable and (company is None or same_company(company, mock.target.company))


async def _pick_back_up(
    convo: Conversation, mock: Mock, chat: Chat, timing: TurnTiming, tuning: Tuning
) -> AsyncIterator[Spoken]:
    """Resume the paused mock, with the same interviewer and question count: Wren's switch line, then the
    interviewer asks the question it was on again. If that call fails twice, it says its last line as saved."""
    mock = replace(mock, on=True, resumable=False, last_step_out=None)
    convo.mock, timing.switch = mock, "mock_resume"
    for spoken in _switch_lines(STEP_BACK_IN):
        yield spoken
    messages = build_interviewer_messages(convo, None, tuning, resumed=True)
    said = [turn.text for turn in _mock_turns(convo, mock) if turn.in_role]
    fallback = said[-1] if said else FIRST_QUESTION_LINE
    async with aclosing(_reply(messages, chat, timing, None, mock=mock.id, fallback=fallback)) as reply:
        async for spoken in reply:
            yield spoken


def _switch_lines(lines: tuple[str, ...]) -> list[Spoken]:
    """Wren's scripted lines as the floor changes hands. The first is the switch line."""
    return [Spoken(line, switch=i == 0) for i, line in enumerate(lines)]


def _unsaved(mock: Mock, speaker: Literal["user", "wren"], text: str) -> HistoryTurn:
    """One of this turn's mock lines. The session saves it after the reply, so it has no id yet."""
    return HistoryTurn("", speaker, text, mock=mock.id, in_role=speaker == "wren")


# What a later model call reports goes on the turn's timing. Its clock times don't: the turn's path
# to first audio started before it, with a scripted line or an earlier call's reply.
_COPIED_BACK = ("model", "chat_fallback", "finish_reason", "completion_tokens", "reply_repair")


async def _reply(
    messages: list[dict], chat: Chat, timing: TurnTiming, cues: CueFilter | None, *, mock: int | None, fallback: str,
    max_tokens: int | None = None, interviewer: str | None = None,
) -> AsyncIterator[Spoken]:
    """One model call's reply, as segments spoken by Wren (mock=None) or by that mock's interviewer.

    A reply with no whole sentence is asked for once more, before any of it is spoken, and if that
    fails too, fallback is said instead. Each attempt runs through a cue filter, so a cue is never
    spoken, and it ends the reply. Pass cues when the caller acts on its speaker's cue, [mock] for
    Wren or [step out] for the interviewer: that cue counts as a whole reply, and cues.cue holds it
    afterwards. Any other cue is dropped, so a reply that was only a dropped cue is asked for again.
    max_tokens, when given, replaces the chat's usual cap for every attempt.

    A sentence that claims to be human or a real person is never spoken (claims_to_be_human). It ends
    the reply, and what came before it stands: the stream closes and the rest is dropped, a cue
    included. With nothing spoken, it's a reply to ask for again. For Wren's own replies, interviewer
    is the mock's name, so Wren can't speak as the interviewer either.

    The call that runs before anything is spoken (run_turn sets timing.first_segment at the first
    segment) writes the turn's timing. A later one writes a scratch timing and copies back what it
    says about the model (_COPIED_BACK). context_tokens adds each prompt once, however many attempts.
    """
    own_cue = "mock" if mock is None else "step_out"  # Wren hands the floor over, the interviewer hands it back
    found = cues if cues is not None else CueFilter()
    cap = {} if max_tokens is None else {"max_tokens": max_tokens}  # like retry, passed only when it isn't the usual
    name_to_guard = interviewer if mock is None else None  # the interviewer itself may say its own name
    call = timing if timing.first_segment is None else TurnTiming(timing.turn, timing.mode, timing.input)
    # rough, no tokenizer for every model
    timing.context_tokens = (timing.context_tokens or 0) + sum(len(m["content"]) for m in messages) // 4
    call.llm_start = clock.now()
    try:
        claimed = False  # the attempt before was cut at a claim to be human
        for attempt in range(2):
            found.reset()
            spoke = False
            # The second ask runs at low reasoning (retry=True) after a reply with no whole sentence, which reasoning
            # that filled the budget can cause. After a dropped claim it keeps its reasoning.
            low = attempt > 0 and not claimed
            stream = chat(messages, call, retry=True, **cap) if low else chat(messages, call, **cap)
            claimed = False
            async with aclosing(_whole_sentences(_until_cue(stream, found), call)) as segments:
                async for text in segments:
                    if claims_to_be_human(text, name_to_guard):  # leaving the loop closes the stream
                        found.reset()  # a cue after it goes too
                        claimed, call.reply_repair = True, "dropped_claim"
                        break
                    spoke = True
                    yield Spoken(text, mock=mock)
            if found.cue is not None and (cues is None or found.cue.kind != own_cue):
                logger.warning("chat model %s wrote %s where it isn't acted on, dropped it", call.model, found.cue)
                found.reset()
            if claimed:
                logger.warning(
                    "chat model %s claimed to be human: dropped the claim and what followed%s",
                    call.model, "" if spoke else (", again" if attempt else ", asking again"),
                )
            if spoke or found.cue is not None:
                if attempt and call.reply_repair != "dropped_claim":  # a dropped claim is the repair to see
                    call.reply_repair = "retried"
                return
            if not claimed:
                logger.warning(
                    "chat model %s sent no whole sentence (finish %s)%s",
                    call.model, call.finish_reason, " again" if attempt else ", asking again",
                )
        call.reply_repair = "gave_up"
        yield Spoken(fallback, mock=mock)
    finally:
        if call is not timing:
            for name in _COPIED_BACK:
                if (value := getattr(call, name)) is not None:
                    setattr(timing, name, value)


async def _until_cue(deltas: AsyncIterator[str], cues: CueFilter) -> AsyncIterator[str]:
    """The reply's text up to its first cue. The cue closes the stream, which stops generation. Text
    held back in case it was a cue comes out at the end if it wasn't one."""
    async with aclosing(deltas) as stream:
        async for delta in stream:
            if text := cues.feed(delta):
                yield text
            if cues.cue is not None:
                return
    if held := cues.flush():
        yield held


async def _whole_sentences(deltas: AsyncIterator[str], timing: TurnTiming) -> AsyncIterator[str]:
    segmenter, reply = Segmenter(), ""
    async with aclosing(deltas) as stream:
        async for delta in stream:
            seen, reply = len(reply), reply + delta
            glued = _GLUED_ANSWER.search(reply)
            if glued is not None:  # the second answer starts at glued.end(), maybe in an earlier delta
                segmenter.unfeed(max(seen - glued.end(), 0))  # fed already, but only part of one word: not spoken
                delta = delta[: max(glued.end() - seen, 0)]
            for segment in segmenter.feed(delta):
                yield segment
            if glued is not None:  # closing the stream stops generation
                timing.reply_repair = "dropped_stacked"
                logger.warning("chat model %s started a second answer, dropped it", timing.model)
                break
    for tail in segmenter.flush():
        if ends_a_sentence(tail):
            yield tail
        else:
            timing.reply_repair = "dropped_tail"
            logger.warning(
                "chat model %s stopped mid-sentence (finish %s), dropped the unfinished tail",
                timing.model, timing.finish_reason,
            )

