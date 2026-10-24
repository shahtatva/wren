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
Wren says RETRY_LINE.

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
from zoneinfo import ZoneInfo

from app.conversation import Conversation, Retrieved
from app.memory.types import PipelineEntry, Profile, SearchHit
from app.voice import clock
from app.voice.metrics import TurnTiming
from app.voice.segment import Segmenter
from app.voice.tuning import Tuning

logger = logging.getLogger(__name__)

PROMPTS = Path(__file__).resolve().parent / "prompts"
CUT_OFF = " [cut off here. They didn't hear the rest]"
OPENING = "[The session just started.]"
MODES = {"checkin": "a check-in", "mock": "mock interview practice"}
RETRY_LINE = "I lost my train of thought. Could you say that again?"
_END_MARK = r"[.!?\u2026][\"'\u201d\u2019)\]]*"
_SENTENCE_END = re.compile(_END_MARK + "$")
_GLUED_ANSWER = re.compile(r"(?<=[a-z0-9])" + _END_MARK + "(?=[A-Z])")  # "...ready.Whenever you're ready, ..."

Retrieve = Callable[[str], Awaitable[Retrieved]]
Chat = Callable[[list[dict], TurnTiming], AsyncIterator[str]]


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
    for turn in convo.turns[-tuning.history_turns :] if tuning.history_turns else []:
        if turn.speaker == "user":
            messages.append({"role": "user", "content": turn.text})
        else:
            messages.append({"role": "assistant", "content": turn.text + (CUT_OFF if turn.interrupted else "")})
    messages.append({"role": "user", "content": OPENING if user_text is None else user_text})
    return messages


async def run_turn(
    convo: Conversation,
    user_text: str | None,
    *,
    retrieve: Retrieve,
    chat: Chat,
    timing: TurnTiming,
    tuning: Tuning = Tuning(),
    utcnow: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> AsyncIterator[str]:
    """Yield Wren's reply as speakable segments. user_text=None opens the session."""
    hits: list[SearchHit] = []
    if user_text is not None:
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

    messages = build_messages(convo, user_text, hits, utcnow(), tuning)
    timing.context_tokens = sum(len(m["content"]) for m in messages) // 4  # rough, no tokenizer for every model
    timing.llm_start = clock.now()
    for attempt in range(2):
        spoke = False
        async for segment in _whole_sentences(chat(messages, timing), timing):
            if timing.first_segment is None:
                timing.first_segment = clock.now()
            spoke = True
            yield segment
        if spoke:
            if attempt:
                timing.reply_repair = "retried"
            return
        logger.warning("chat model %s sent no whole sentence%s", timing.model, " again" if attempt else ", asking again")
    timing.reply_repair = "gave_up"
    timing.first_segment = clock.now()
    yield RETRY_LINE


async def _whole_sentences(deltas: AsyncIterator[str], timing: TurnTiming) -> AsyncIterator[str]:
    segmenter, reply = Segmenter(), ""
    async with aclosing(deltas) as stream:
        async for delta in stream:
            seen, reply = len(reply), reply + delta
            glued = _GLUED_ANSWER.search(reply)
            for segment in segmenter.feed(delta if glued is None else delta[: glued.end() - seen]):
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
            logger.warning("chat model %s stopped mid-sentence, dropped the unfinished tail", timing.model)

