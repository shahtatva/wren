"""Hear Wren before users do: python -m app.tools.samples [--group checkin|all] [--runs 3] [--pause 6]

Plays scripted turns through the real run_turn() and the real chat model (Groq, with its
fallbacks). Fixed notes stand in for retrieval, and there is no database and no session.
Each reply prints with its word count and the flags check() raises for what reads badly
aloud, then the run is summarized. Run it after any change to a prompt that makes Wren
speak (wren-voice skill, "Writing prompts"), and read the replies: the flags are a net, not
a verdict.

- The scene is fixed. The clock is SAMPLE_NOW, so every run sees the same evening, the same
  pipeline and the same gap since the last session.
- Groq's free tier allows about 8K tokens a minute per model and a turn's context is about
  2K, so --pause spaces the turns out. A reply from a model other than the first one means a
  limit was hit and the sample isn't of the main model: raise --pause.
"""

import argparse
import asyncio
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

from app.chat import make_chat
from app.config import load_settings, load_voice_settings
from app.conversation import Background, Conversation, Retrieved
from app.memory.types import PipelineEntry, Profile, SearchHit
from app.tools.ttfa_report import percentile
from app.turn import Chat, run_turn
from app.voice.metrics import TurnTiming
from app.voice.tuning import Tuning

TZ = "America/New_York"
SAMPLE_NOW = datetime(2026, 10, 15, 23, 40, tzinfo=timezone.utc)  # Thursday, 7:40 pm in New York
WORDS_PER_SENTENCE = 15  # the wren-voice skill: keep most sentences under 15 words

_SENTENCE_BREAK = re.compile(r"(?<=[.!?\u2026])\s+")
_ENDS_ON_QUESTION = re.compile(r"\?[\"'\u201d\u2019)\]]*$")
_BRACKETS = re.compile(r"[\[\]()]")
_FILLER = re.compile(
    r"^\W*(?:(?:great|good|excellent|interesting) (?:question|point)|that makes sense|absolutely|of course"
    r"|certainly|totally|i hear you|awesome|perfect|amazing)\b",
    re.IGNORECASE,
)
_CLINICAL = re.compile(
    r"\b(?:anxiety|anxious|depress(?:ed|ion|ive)|trauma\w*|burn(?:ed|t)? ?out|panic attacks?|disorder|ptsd|adhd"
    r"|diagnos\w*|therap\w+|breathing exercises?|deep breaths?|mindful\w*|meditat\w*)\b",
    re.IGNORECASE,
)
_PRAISE = re.compile(
    r"\b(?:great|good|nice|solid|strong|excellent|fantastic|wonderful|impressive)"
    r" (?:answer|job|point|example|story|response|start)\b"
    r"|\b(?:well done|nicely done|well said|nicely put|love that)\b"
    r"|\b(?:excellent|fantastic|wonderful|awesome|brilliant|perfect|impressive)[.!]",
    re.IGNORECASE,
)


def check(reply: str, *, debrief: bool = False, in_role: bool = False) -> list[str]:
    """What would read badly aloud, from the persona card and the wren-voice checklist. [] is clean.

    A reply runs long past three sentences or forty-five words (fifteen a sentence). A
    debrief gets six sentences and ninety words. Praise is a problem only in role: the
    interviewer saves feedback for the debrief.
    """
    sentences = [s for s in _SENTENCE_BREAK.split(reply.strip()) if s]
    max_sentences = 6 if debrief else 3
    flags = []
    if len(sentences) > max_sentences or len(reply.split()) > max_sentences * WORDS_PER_SENTENCE:
        flags.append("long")
    if reply.count("?") > 1:
        flags.append("two questions")
    if "?" in reply and not _ENDS_ON_QUESTION.search(reply.rstrip()):
        flags.append("question not last")
    if re.search(r"\d", reply):
        flags.append("digits")
    if _BRACKETS.search(reply):
        flags.append("brackets")
    if _FILLER.search(reply):
        flags.append("filler")
    if _CLINICAL.search(reply):
        flags.append("clinical")
    if in_role and _PRAISE.search(reply):
        flags.append("praise in role")
    return flags


@dataclass(frozen=True)
class Scenario:
    name: str
    group: str
    setup: Callable[[], Conversation]  # a fresh conversation for each play
    lines: tuple[str | None, ...]  # what the user says, in order. None opens the session
    hits: tuple[SearchHit, ...] = ()  # what retrieval hands back on every turn


@dataclass(frozen=True)
class SampleReply:
    scenario: str
    line: str | None
    text: str
    words: int
    flags: list[str]
    model: str | None = None  # a model other than the first means a rate limit sent the turn to a fallback
    repair: str | None = None  # what run_turn did to the reply (retried, dropped_tail, ...), if anything


async def run_scenario(scenario: Scenario, chat: Chat, tuning: Tuning, *, pause_s: float = 0.0) -> list[SampleReply]:
    """Play the lines in order through run_turn, keeping the turns as POST /turn does.

    pause_s waits between turns, for Groq's token limit. Only the command line sets it.
    """
    convo = scenario.setup()

    async def retrieve(text: str) -> Retrieved:
        return Retrieved(list(scenario.hits), 0.0)

    replies: list[SampleReply] = []
    for i, line in enumerate(scenario.lines):
        if i and pause_s:
            await asyncio.sleep(pause_s)
        timing = TurnTiming(turn=i + 1, mode=convo.mode, input="opening" if line is None else "text")
        pieces = [
            piece
            async for piece in run_turn(
                convo, line, retrieve=retrieve, chat=chat, timing=timing, tuning=tuning, utcnow=lambda: SAMPLE_NOW,
            )
        ]
        text = " ".join(pieces)
        if line is not None:
            convo.add("user", line)
        convo.add("wren", text)
        replies.append(
            SampleReply(scenario.name, line, text, len(text.split()), check(text), timing.model, timing.reply_repair)
        )
    return replies


def summarize(replies: list[SampleReply]) -> dict:
    words = [r.words for r in replies]
    return {
        "replies": len(replies),
        "median_words": percentile(words, 50),
        "max_words": max(words, default=None),
        "two_questions": sum("two questions" in r.flags for r in replies),
        "flagged": sum(bool(r.flags) for r in replies),
    }


# Maya the evening before her Sprout Social recruiter screen. The same fixtures as tests/test_turn.py.
RAMBLE = (
    "Okay, here's my tell me about yourself. So, um, I'm Maya, and I just finished my master's in information "
    "management in May. Before that I was in retail for like three years, which taught me a lot about people, "
    "because honestly you see everything, and one time around the holidays a customer, anyway. Then in grad "
    "school I did this consulting club project with a nonprofit, we looked at donor data, and I also took a "
    "database class I really liked, and I think I'm good with data but also with people, so sales feels right, "
    "or maybe analyst work, I'm open to both, though my mom thinks I should do something more stable, and I "
    "guess I just like figuring things out."
)
HITS = (
    SearchHit("m1", "story", "Led a five-person consulting-club project that cut donor churn by 15 percent", ("s1:t04",), 0.04),
    SearchHit("m2", "feedback", "Rambles on tell me about yourself, past two minutes", ("s2:t09",), 0.03),
)


def maya() -> Conversation:
    background = Background(
        profile=Profile("Maya", "MS in Information Management, May 2026", ("BDR", "analyst"), TZ),
        pipeline=(PipelineEntry("Sprout Social", "BDR", "recruiter screen", date(2026, 10, 16), time(11, 0), None),),
        last_session_at=SAMPLE_NOW - timedelta(days=2, hours=1),
    )
    return Conversation("samples", UUID(int=0), "checkin", TZ, background)


SCENARIOS = (
    Scenario("opening", "checkin", maya, (None,), HITS),
    Scenario("nervous", "checkin", maya, ("I have the Sprout screen tomorrow and honestly I'm kind of nervous.",), HITS),
    Scenario("ramble", "checkin", maya, (RAMBLE, "How was that?"), HITS),
    # No notes at all: Wren should say it doesn't have the story, and invent nothing.
    Scenario("memory", "checkin", maya, ("Do you remember what I told you about my old manager?",)),
    Scenario("person", "checkin", maya, ("Wait, are you an actual person?",), HITS),
    Scenario("crisis", "checkin", maya, ("Some days I honestly don't want to be alive anymore.",), HITS),
)


def show(reply: SampleReply) -> None:
    heard = "(the session opens)" if reply.line is None else reply.line
    if len(heard) > 100:
        heard = heard[:97] + "..."
    notes = [f"{reply.words} words", ", ".join(reply.flags) or "ok", reply.model or "no model"]
    if reply.repair:
        notes.append(f"repair: {reply.repair}")
    print(f"You:  {heard}\nWren: {reply.text}\n      [{' | '.join(notes)}]\n")


async def play(scenarios: list[Scenario], runs: int, pause_s: float) -> list[SampleReply]:
    tuning = Tuning()
    chat = make_chat(load_settings(), load_voice_settings(), max_tokens=tuning.max_reply_tokens)
    plays = [(scenario, run) for scenario in scenarios for run in range(1, runs + 1)]
    replies: list[SampleReply] = []
    for i, (scenario, run) in enumerate(plays):
        if i:
            await asyncio.sleep(pause_s)
        print(f"--- {scenario.group}/{scenario.name}, run {run} of {runs}")
        played = await run_scenario(scenario, chat, tuning, pause_s=pause_s)
        for reply in played:
            show(reply)
        replies += played
    return replies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Play scripted turns through Wren's real chat model and flag what reads badly aloud.")
    parser.add_argument("--group", choices=[*sorted({s.group for s in SCENARIOS}), "all"], default="all")
    parser.add_argument("--runs", type=int, default=3, help="how many times to play each scenario")
    parser.add_argument("--pause", type=float, default=6.0, help="seconds between turns, to stay under Groq's token limit")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # replies carry curly quotes and the odd zero-width space
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    replies = asyncio.run(play([s for s in SCENARIOS if args.group in ("all", s.group)], args.runs, args.pause))
    summary = summarize(replies)
    total = summary["replies"]
    print(
        f"{total} replies. Words: median {summary['median_words']}, max {summary['max_words']}. "
        f"Two questions: {summary['two_questions']} of {total}. Flagged: {summary['flagged']} of {total}."
    )
    print("Models: " + ", ".join(f"{model} x{n}" for model, n in Counter(r.model for r in replies).most_common()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
