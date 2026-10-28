"""A mock interview's pure logic: its cues, who is interviewing whom, and the words that bring Wren back.

No I/O and no model calls, so voice, POST /turn and tests all lean on the same rules.

Cues. The model starts and ends a mock by writing a bracketed cue, which is never spoken. In a
check-in, Wren's whole reply is [mock: Sprout Social], or [mock] when no company was named. The
interviewer ends its part with [step out]. CueFilter finds a cue in the streaming reply, holds back
any text that might still turn into one, and drops everything after it.

Stepping out. step_out_reason() reads what the user just said and says why Wren should take over
from the interviewer, or None to keep the interview going. The first match wins, in this order:
crisis, ai, stop, pause. Safety comes first, so nobody in danger is taken for someone asking for
a pause. The code only acts on plain requests, because a wrong guess ends someone's practice:
loose phrases, and "step out" on its own, count only in a short message, so a long answer about
stepping out of a comfort zone, or "we had to stop the launch", goes on. The interviewer's own
[step out] is the backstop for anything looser.
"""

import re
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time
from typing import Literal

from app.memory.types import PipelineEntry

# Each sentence is its own segment, and the first is the switch line.
STEP_IN = ("Okay, stepping into role.",)
STEP_BACK_IN = ("Okay, stepping back into role.",)
STEP_OUT = ("Stepping out.", "It's me again.")
# First names only, none over six letters, so they fit the page's speaker column.
INTERVIEWER_NAMES = ("Dana", "Priya", "Marcus", "Elena", "Jordan", "Nadia", "Theo", "Grace", "Omar", "Lena")

StepOutReason = Literal["crisis", "ai", "stop", "pause"]


@dataclass(frozen=True)
class Target:
    """What the mock practices for. Whatever isn't known stays None."""

    company: str | None = None
    role: str | None = None
    stage: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class Mock:
    """One mock interview. Frozen, so the session can restore it by reference."""

    id: int
    name: str
    title: str
    target: Target
    on: bool = True  # the interviewer has the floor
    resumable: bool = False  # Wren stepped out for a reason that lets the interview pick back up

    def who(self) -> str:
        company = self.target.company
        if self.title == "interviewer":
            return f"one of the interviewers at {company}" if company else "an interviewer"
        return f"the {self.title} at {company}" if company else f"a {self.title}"

    def introduction(self) -> str:
        if self.target.company:
            return f"I'm {self.name}, {self.who()}."
        return f"I'm {self.name}, and I'll be your {self.title} today."


@dataclass(frozen=True)
class Cue:
    kind: Literal["mock", "step_out"]
    company: str | None = None  # only a [mock: company] cue has one


_CUE = re.compile(r"\[\s*(mock|step\s+out)\s*(?::\s*([^\]]*?))?\s*\]", re.IGNORECASE)
_MAX_CUE = 60  # characters, brackets included. A longer bracket is something else.


def _cue_of(match: re.Match[str]) -> Cue:
    if match.group(1).lower() == "mock":
        return Cue("mock", match.group(2) or None)
    return Cue("step_out")


class CueFilter:
    """Takes a streaming reply one delta at a time and returns the text that is safe to speak.

    A "[" might open a cue, so it and what follows are held back until the "]" shows what they are,
    or until _MAX_CUE characters have passed without one. The same text gives the same result
    however it is split into deltas.
    """

    def __init__(self) -> None:
        self.cue: Cue | None = None
        self._held = ""

    def reset(self) -> None:
        self.cue, self._held = None, ""

    def feed(self, delta: str) -> str:
        if self.cue is not None:  # nothing after a cue is spoken
            return ""
        text, self._held = self._held + delta, ""
        out: list[str] = []
        while text:
            start = text.find("[")
            if start < 0:
                out.append(text)
                break
            out.append(text[:start])
            text = text[start:]
            end = text.find("]", 0, _MAX_CUE)
            match = _CUE.fullmatch(text[: end + 1]) if end >= 0 else None
            if match:
                self.cue = _cue_of(match)
                break
            if end < 0 and len(text) < _MAX_CUE:  # no "]" yet, and it could still turn into a cue
                self._held = text
                break
            out.append("[")  # not a cue: let the bracket through and look again after it
            text = text[1:]
        return "".join(out)

    def flush(self) -> str:
        """Release held-back text that turned out not to be a cue."""
        held, self._held = self._held, ""
        return held


def _name_words(name: str | None) -> str:
    return " ".join(re.findall(r"[^\W_]+", (name or "").casefold()))


def same_company(a: str | None, b: str | None) -> bool:
    """Case- and punctuation-blind. One name may sit inside the other, as whole words."""
    a, b = _name_words(a), _name_words(b)
    return bool(a and b) and (f" {a} " in f" {b} " or f" {b} " in f" {a} ")


# A "(...)" or "[...]" part of a name, or one left open to the end: a cue's company can't hold a "]".
_ASIDE = re.compile(r"\([^)]*\)?|\[[^\]]*\]?")
_NAME_ENDS = ".,;:!?\"'- "


def plain_company(text: str | None) -> str | None:
    """The company a [mock: ...] cue names, said plainly: "Acme (Series B)" is "Acme". The introduction
    speaks a company that isn't in the pipeline as the cue names it, and scripted lines aren't cleaned."""
    plain = " ".join(_ASIDE.sub(" ", text or "").split()).strip(_NAME_ENDS)
    return plain or None


def _soonest(entry: PipelineEntry) -> tuple[date, time]:
    return entry.next_date or date.max, entry.next_time or time.max


def resolve_target(company: str | None, pipeline: Sequence[PipelineEntry], target_roles: Sequence[str]) -> Target:
    """The pipeline entry the company names, else the soonest entry if none was named. A company
    that isn't in the pipeline is kept as said. With nothing at all, a generic interview for the
    profile's first target role."""
    company = (company or "").strip() or None
    role = target_roles[0] if target_roles else None
    entries = [entry for entry in pipeline if company is None or same_company(company, entry.company)]
    if entries:
        entry = min(entries, key=_soonest)
        return Target(entry.company, entry.role or role, entry.stage, entry.notes)
    return Target(company, role)


def interviewer_title(stage: str | None) -> str:
    stage = (stage or "").lower()
    if "manager" in stage:
        return "hiring manager"
    if any(word in stage for word in ("recruit", "screen", "phone")):
        return "recruiter"
    return "interviewer"


def interviewer_name(session_id: str, mock_id: int) -> str:
    """Invented, and the same for the same mock in any process, which hash() wouldn't be."""
    return INTERVIEWER_NAMES[zlib.crc32(f"{session_id}:{mock_id}".encode()) % len(INTERVIEWER_NAMES)]


_SHORT = 8  # words. Loose phrases count only in a message this short.
_APOSTROPHES = str.maketrans("\u2018\u2019", "''")

# Suicide, self-harm or danger. Idioms and job stories are not it: "the deadline was killing me", "a hill
# I want to die on", "in danger of missing it", "I felt like dying when the demo crashed", "better off
# without me micromanaging", "I don't feel safe speaking up", "I don't want to live in New York". "I
# don't want to be here anymore" and "I can't go on" are left to the interviewer: they are also real
# answers to "why are you leaving?"
_CRISIS = re.compile(
    r"\bsuicid(?:e|al)|\bself[\s-]?harm"
    r"|\b(?:kill|killing|hurt|hurting|harm|harming|cut|cutting)\s+my\s?self\b"
    r"|\b(?:end|ending|take|taking)\s+my\s+(?:own\s+)?life\b|\bend\s+it\s+all\b"
    r"|\b(?:want(?:ed)?|wanna)\s+to\s+die\b(?!\s+on\b)|\bwish\s+i\s+(?:was|were)\s+dead\b"
    r"|\bbetter\s+off\s+dead\b|\bno\s+reason\s+to\s+live\b"
    r"|\b(?:don't|do\s+not|no\s+longer)\s+(?:want|wanna)\s+to\s+"
    r"(?:be\s+alive|exist|live(?=\s*(?:[.!?,]|$|any\s*(?:more|longer))))"
    r"|\b(?:i'm|i\s+am|i\s+feel)\s+(?:not\s+safe|unsafe|in\s+(?:real\s+|serious\s+)?danger\b(?!\s+of\b))"
    r"|\bi\s+(?:don't|do\s+not)\s+feel\s+safe(?=\s*(?:[.!?,]|$|(?:right\s+)?now\b|at\s+home\b|here\b))"
    r"|\bfeel\s+like\s+dying\b|\bbetter\s+off\s+without\s+me(?=\s*(?:[.!?,]|$))",
    re.IGNORECASE,
)

# "Are you an AI?", "Am I talking to a bot?", "Is this a real recruiter?". A bare word like "real" or
# "AI" counts only where the question ends, so "Is this an AI company?" and "Are you real busy?"
# are not it.
_ASKED = r"(?=\s*(?:[?.!,;]|$)|\s+(?:or|right|really|though|then)\b)"
_REAL_ONE = r"(?:real|actual)\s+(?:person|human(?:\s+being)?|recruiter|interviewer|hiring\s+manager)\b"
_HUMAN = rf"(?:{_REAL_ONE}|(?:real|actual|human(?:\s+being)?|person){_ASKED})"
_MACHINE = rf"(?:ai|a\.i\.|bots?|chat\s?bots?|robots?|machine|computer|llm|chat\s?gpt){_ASKED}"
_AI = re.compile(
    rf"\bare\s+you\s+(?:even\s+|actually\s+|really\s+|just\s+)?(?:an?\s+)?(?:{_HUMAN}|{_MACHINE})"
    rf"|\bam\s+i\s+(?:talking|speaking|chatting)\s+(?:to|with)\s+(?:an?\s+)?(?:{_HUMAN}|{_MACHINE})"
    rf"|\bis\s+(?:this|that|it)\s+(?:an?\s+)?(?:{_HUMAN}|{_MACHINE})"
    rf"|\byou(?:'re|\s+are)\s+(?:actually\s+|really\s+|just\s+)?(?:an?\s+)?{_MACHINE}",
    re.IGNORECASE,
)

# Asking to end it, in any message, because the request names the mock, interview, practice or session.
# The verb has to open a sentence or follow a request, and the clause has to end with the object (or go
# on with "now", "here", "please", "for today"). So "I always end the interview by thanking them", "I
# need to end the interview process faster" and "End the session with a clear next step" stay answers.
# "finish" is left out: "let's finish the interview" means keep going.
_STOP = re.compile(
    r"(?:^|[.!?,;]\s+|\b(?:please|let'?s|(?:can|could)\s+(?:we|you)|(?:we|you)\s+can|we\s+should|"
    r"i\s+want\s+to|i'd\s+like\s+to|i\s+need\s+to)\s+)"
    r"(?:(?:okay|ok|so|just)\W+)*"
    r"(?:stop|end|quit|cancel)\s+(?:(?:the|this|our)\s+)?"
    r"(?:mock(?:\s+interview)?|interview|practice|session|role[\s-]?play)"
    r"(?=\s*(?:[.!?,;]|$)|\s+(?:now|here|there|please|for\s+(?:today|now|the\s+day))\b)",
    re.IGNORECASE,
)
# Looser ways to say it, which are also things people say in answers, so they count only in a short
# message. The verb has to end the clause or be followed by a stop-like word: "I want to stop." and
# "Let's stop there." are requests, "I want to stop feeling like this." isn't, and "I want to end it
# all." is a crisis. "That's enough." stops, "That's enough about me." doesn't. "I'm done" isn't here:
# a candidate says it to close an answer.
_STOP_ENDS = (
    r"(?=\s*(?:[.!?,]|$)"
    r"|\s+(?:(?:it\s+)?(?:right\s+)?(?:here|there|now)|this|for\s+(?:today|now|the\s+day))\b)"
)
_LOOSE_STOP = re.compile(
    rf"\b(?:let'?s|let\s+us|can\s+we|could\s+we)\s+(?:just\s+)?"
    rf"(?:stop|end|quit|wrap\s+(?:this\s+|it\s+)?up|call\s+it(?:\s+a\s+day)?){_STOP_ENDS}"
    rf"|\b(?:i\s+want\s+to|i'd\s+like\s+to|i\s+need\s+to|i\s+wanna)\s+(?:just\s+)?(?:stop|quit){_STOP_ENDS}"
    rf"|\bthat'?s\s+enough{_STOP_ENDS}",
    re.IGNORECASE,
)

# "Step out" is a request in a short message, or in any message when it names the role, mock or
# interview. Longer, it is an answer: "I had to step out of my comfort zone to lead that project", "you
# sometimes have to step out of your role and help".
_STEP_OUT = re.compile(r"\bstep\s+out\b", re.IGNORECASE)
_STEP_OUT_OF_ROLE = re.compile(r"\bstep\s+out\s+of\s+(?:the\s+)?(?:role|mock|interview)\b", re.IGNORECASE)
# A message that opens by speaking to Wren, after an opener or not: "Wren, can I ask you something?"
_ADDRESSES_WREN = re.compile(
    r"^\W*(?:(?:hey|hi|hello|okay|ok|um|uh|so|sorry|wait|hold\s+on|actually|oh)\W+)*wren\b", re.IGNORECASE
)


def step_out_reason(text: str) -> StepOutReason | None:
    """Why Wren should take over from the interviewer for this message, or None. Checked in order:
    crisis, ai, stop, pause."""
    text = text.translate(_APOSTROPHES).strip()
    short = len(text.split()) <= _SHORT
    if _CRISIS.search(text):
        return "crisis"
    if _AI.search(text):
        return "ai"
    if _STOP.search(text) or (short and _LOOSE_STOP.search(text)):
        return "stop"
    if _ADDRESSES_WREN.match(text) or _STEP_OUT_OF_ROLE.search(text) or (short and _STEP_OUT.search(text)):
        return "pause"
    return None
