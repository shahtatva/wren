"""Split a streaming reply into speakable segments, and clean what shouldn't be spoken.

Each segment goes to text-to-speech on its own, so audio can start before the reply is
finished. Segments end at sentence ends. The first may also end at a clause once it
has a few words, which starts audio sooner. A run-on with no punctuation is cut at a
word cap. The persona card already asks for plain spoken text; clean() is the safety
net for the markdown, emoji and links models slip in anyway.
"""

import re
from collections.abc import AsyncIterator

ABBREVIATIONS = frozenset({"mr", "mrs", "ms", "dr", "st", "vs", "e.g", "i.e", "jr", "sr", "prof", "mt"})

_SENTENCE_END = re.compile(r"[.!?]+[\"'\u201d\u2019)\]]*(?=\s)")
_CLAUSE_END = re.compile(r"[,;:](?=\s)")
_COMPLETE_WORD = re.compile(r"\S+(?=\s)")
_LAST_WORD = re.compile(r"(\S+)$")

_URL = re.compile(r"(?:https?://|www\.)\S+")
_KEYCAP = re.compile("\\d\ufe0f?\u20e3")
_EMOJI = re.compile(
    "[\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f\u200d\U0001f1e6-\U0001f1ff]"
)
_LINE_MARKER = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*\u2022]\s+|\d+[.)]\s+)")
_EMPHASIS = re.compile(r"\*{1,3}|_{2,3}|`+")
_HYPHENS = re.compile("[\u2010\u2011\u2012\u2212]")
_DIGIT_DASH = re.compile("(?<=\\d)[\u2013\u2014](?=\\d)")
_DASH = re.compile("\\s*[\u2013\u2014]\\s*|\\s+-\\s+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,;:.!?])")
_REPEATED_COMMA = re.compile(r",(?:\s*,)+")


def clean(text: str) -> str:
    lines = [_LINE_MARKER.sub("", line) for line in text.splitlines()]
    text = " ".join(lines)
    text = _URL.sub("", text)
    text = _KEYCAP.sub("", text)
    text = _EMOJI.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = _HYPHENS.sub("-", text)
    text = _DIGIT_DASH.sub("-", text)
    text = _DASH.sub(", ", text)
    text = " ".join(text.split())
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _REPEATED_COMMA.sub(",", text)
    return text.lstrip(",;: ").strip()


class Segmenter:
    def __init__(self, first_min_words: int = 5, max_words: int = 30):
        self._first_min_words = first_min_words
        self._max_words = max_words
        self._buf = ""
        self._first = True

    def _sentence_end(self) -> int | None:
        for match in _SENTENCE_END.finditer(self._buf):
            before = _LAST_WORD.search(self._buf[: match.start()])
            word = before.group(1).lower().strip("\"'(\u201c\u2018") if before else ""
            if match.group().startswith(".") and word in ABBREVIATIONS:
                continue
            return match.end()
        return None

    def _clause_end(self) -> int | None:
        if not self._first:
            return None
        for match in _CLAUSE_END.finditer(self._buf):
            if len(self._buf[: match.start()].split()) >= self._first_min_words:
                return match.end()
        return None

    def _word_cap(self) -> int | None:
        words = list(_COMPLETE_WORD.finditer(self._buf))
        if len(words) > self._max_words:
            return words[self._max_words - 1].end()
        return None

    def _next_split(self) -> int | None:
        found = [i for i in (self._sentence_end(), self._clause_end(), self._word_cap()) if i is not None]
        return min(found) if found else None

    def _emit(self, raw: str, out: list[str]) -> None:
        text = clean(raw)
        if text:
            out.append(text)
            self._first = False

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while (split := self._next_split()) is not None:
            raw, self._buf = self._buf[:split], self._buf[split:]
            self._emit(raw, out)
        return out

    def flush(self) -> list[str]:
        out: list[str] = []
        raw, self._buf = self._buf, ""
        self._emit(raw, out)
        return out


async def segments(deltas: AsyncIterator[str], **options) -> AsyncIterator[str]:
    segmenter = Segmenter(**options)
    async for delta in deltas:
        for segment in segmenter.feed(delta):
            yield segment
    for segment in segmenter.flush():
        yield segment
