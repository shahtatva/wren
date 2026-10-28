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
Wren says RETRY_LINE, or at the opening, a plain greeting.

Mock interviews. Wren starts one by writing a cue, [mock: company], instead of talking. The
cue is caught before it's spoken: Wren says its switch line, an interviewer introduces itself,
and a separate interviewer prompt asks the first question, all in the same turn. While the
mock is on, the user's words go to the interviewer, with no retrieval: it sees only its card
and the mock's own lines. Every segment says who speaks it (Spoken).

The stages come in as callables, like retrieve()'s, so the server, evals and tests can
each wire their own. Retrieval failing never fails the turn: Wren answers without memories.
"""

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from datetime import date, datetime, time, timezone
from functools import cache
from pathlib import Path
from typing import Protocol, Self
from zoneinfo import ZoneInfo

from app.conversation import Conversation, HistoryTurn, Retrieved
from app.memory.types import PipelineEntry, Profile, SearchHit
from app.mock import STEP_IN, Cue, CueFilter, Mock, interviewer_name, interviewer_title, plain_company, resolve_target
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
_END_MARK = r"[.!?\u2026][\"'\u201d\u2019)\]]*"
_SENTENCE_END = re.compile(_END_MARK + "$")
# "...ready.Whenever you're ready, ...": a sentence end with no space, then a word that starts a
# sentence. Not "Ph.D.", "Socket.IO" or "www.LinkedIn.com": the next word must look like a word.
_GLUED_ANSWER = re.compile(r"(?<=[a-z0-9])" + _END_MARK + r"(?=(?:[A-Z][a-z]+|[AI])[\s,;:!?.'\u2019])")

Retrieve = Callable[[str], Awaitable[Retrieved]]


class Chat(Protocol):
    """Streams the reply. retry=True is the second ask, after a reply with no whole sentence."""

    def __call__(self, messages: list[dict], timing: TurnTiming, *, retry: bool = False) -> AsyncIterator[str]: ...


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


def build_messages(
    convo: Conversation, user_text: str | None, hits: list[SearchHit], now_utc: datetime, tuning: Tuning
) -> list[dict]:
    now_local = now_utc.astimezone(ZoneInfo(convo.tz))
    system = _prompt("conversation.md").format(
        persona=_prompt("persona.md"),
        signals=_signals(convo, now_local, opening=user_text is None),
        profile=_profile(convo.background.profile),
        pipeline_days=tuning.pipeline_days,
        pipeline=_pipeline(convo.background.pipeline, now_local.date()),
        memories=_memories(hits),
    )
    messages = [{"role": "system", "content": system}]
    history = convo.turns[-tuning.history_turns :] if tuning.history_turns else []
    messages += [_message(turn, mark_role=True) for turn in history]
    messages.append({"role": "user", "content": OPENING if user_text is None else user_text})
    return _alternating(messages)


def _candidate(profile: Profile | None) -> str:
    """Their name and background, as an interviewer would read them on a resume, or "not given"."""
    if profile is None:
        return "not given"
    parts = [part.strip() for part in (profile.display_name, profile.summary) if part and part.strip()]
    return " ".join(part if ends_a_sentence(part) else f"{part}." for part in parts) or "not given"


def _next_step(asked: int, total: int) -> str:
    """What the interviewer does next, from how many questions it has asked: its turns in the mock."""
    if asked == 0:
        return "You've just introduced yourself. Ask your first question."
    if asked < total - 1:
        return f"Ask your next question. You've asked {asked} of {total}."
    if asked == total - 1:
        return "Ask your last question: what questions do they have for you?"
    return (
        "You've asked all your questions. If they asked you anything, answer briefly and in general terms."
        " Then thank them in one short sentence and write [step out]."
    )


def build_interviewer_messages(
    convo: Conversation, user_text: str | None, tuning: Tuning, *, resumed: bool = False
) -> list[dict]:
    """The interviewer's context: its card, a fixed start line, then this mock's lines and nothing else,
    with no memories. user_text is their answer, or None when the interviewer speaks first: at the
    start of the mock, or when it picks back up (resumed)."""
    mock = convo.mock
    assert mock is not None, "only a conversation with a mock has an interviewer"
    turns = [turn for turn in convo.turns if turn.mock == mock.id]
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
        next_step=_next_step(sum(turn.in_role for turn in turns), tuning.mock_questions),
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


async def _turn(
    convo: Conversation, user_text: str | None, retrieve: Retrieve, chat: Chat, timing: TurnTiming, tuning: Tuning,
    utcnow: Callable[[], datetime],
) -> AsyncIterator[Spoken]:
    """Who answers: the interviewer while a mock is on, else Wren, whose [mock] cue hands off to one."""
    mock = convo.mock
    if user_text is not None and mock is not None and mock.on:  # the interviewer has the floor
        messages = build_interviewer_messages(convo, user_text, tuning)
        async with aclosing(_reply(messages, chat, timing, None, mock=mock.id, fallback=RETRY_LINE)) as reply:
            async for spoken in reply:
                yield spoken
        return
    hits = [] if user_text is None else await _retrieve_memories(user_text, retrieve, timing, tuning)
    messages = build_messages(convo, user_text, hits, utcnow(), tuning)
    if user_text is None:  # the opening acts on no cue
        cues, fallback = None, (FIRST_OPENING_LINE if convo.background.last_session_at is None else OPENING_LINE)
    else:
        cues, fallback = CueFilter(), RETRY_LINE
    async with aclosing(_reply(messages, chat, timing, cues, mock=None, fallback=fallback)) as reply:
        async for spoken in reply:
            yield spoken
    if cues is not None and cues.cue is not None:
        async with aclosing(_hand_off(convo, cues.cue, chat, timing, tuning)) as handoff:
            async for spoken in handoff:
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
    mock = Mock(mock_id, interviewer_name(convo.session_id, mock_id), interviewer_title(target.stage), target)
    convo.mock, timing.switch = mock, "mock_start"
    for i, line in enumerate(STEP_IN):
        yield Spoken(line, switch=i == 0)
    yield Spoken(mock.introduction(), mock=mock.id)
    messages = build_interviewer_messages(convo, None, tuning)
    async with aclosing(_reply(messages, chat, timing, None, mock=mock.id, fallback=RETRY_LINE)) as reply:
        async for spoken in reply:
            yield spoken


# What a later model call reports goes on the turn's timing. Its clock times don't: the turn's path
# to first audio started before it, with a scripted line or an earlier call's reply.
_COPIED_BACK = ("model", "chat_fallback", "finish_reason", "completion_tokens", "reply_repair")


async def _reply(
    messages: list[dict], chat: Chat, timing: TurnTiming, cues: CueFilter | None, *, mock: int | None, fallback: str
) -> AsyncIterator[Spoken]:
    """One model call's reply, as segments spoken by Wren (mock=None) or by that mock's interviewer.

    A reply with no whole sentence is asked for once more, before any of it is spoken, and if that
    fails too, fallback is said instead. Each attempt runs through a cue filter, so a cue is never
    spoken, and it ends the reply. Pass cues when the caller acts on its speaker's cue, [mock] for
    Wren or [step out] for the interviewer: that cue counts as a whole reply, and cues.cue holds it
    afterwards. Any other cue is dropped, so a reply that was only a dropped cue is asked for again.

    The call that runs before anything is spoken (run_turn sets timing.first_segment at the first
    segment) writes the turn's timing. A later one writes a scratch timing and copies back what it
    says about the model (_COPIED_BACK). context_tokens adds each prompt once, however many attempts.
    """
    own_cue = "mock" if mock is None else "step_out"  # Wren hands the floor over, the interviewer hands it back
    found = cues if cues is not None else CueFilter()
    call = timing if timing.first_segment is None else TurnTiming(timing.turn, timing.mode, timing.input)
    # rough, no tokenizer for every model
    timing.context_tokens = (timing.context_tokens or 0) + sum(len(m["content"]) for m in messages) // 4
    call.llm_start = clock.now()
    try:
        for attempt in range(2):
            found.reset()
            spoke = False
            stream = chat(messages, call, retry=True) if attempt else chat(messages, call)
            async with aclosing(_whole_sentences(_until_cue(stream, found), call)) as segments:
                async for text in segments:
                    spoke = True
                    yield Spoken(text, mock=mock)
            if found.cue is not None and (cues is None or found.cue.kind != own_cue):
                logger.warning("chat model %s wrote %s where it isn't acted on, dropped it", call.model, found.cue)
                found.reset()
            if spoke or found.cue is not None:
                if attempt:
                    call.reply_repair = "retried"
                return
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

