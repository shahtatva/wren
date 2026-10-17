"""Is the user really cutting in, or is that echo or a backchannel?

The browser pauses Wren the moment it hears a voice. This decides, from the words
Nova-3 heard, whether to cancel the reply or let it resume:
- Words Wren just said are echo from the speakers, so they don't count.
- Backchannels ("mm-hmm", "yeah", "right") are listening, not interrupting.
- Two other words interrupt, and so does one stop word like "wait".
"""

import re

BACKCHANNELS = frozenset(
    {"mm", "mhm", "mmhmm", "hmm", "uh", "um", "huh", "uhhuh", "yeah", "yep", "yes", "okay", "ok",
     "right", "sure", "oh", "ah", "cool", "nice", "totally", "gotcha"}
)
STOP_WORDS = frozenset({"wait", "stop", "hold", "hang", "sorry", "actually", "no", "but"})

_WORD = re.compile(r"[a-z0-9']+")
_HUM = re.compile(r"[mh]+|(?:uh|um)+")  # mmm, hmmm, mhm, uhuh


def words(text: str) -> list[str]:
    text = text.lower().replace("’", "'").replace("-", "")
    return _WORD.findall(text)


def _backchannel(word: str) -> bool:
    return word in BACKCHANNELS or _HUM.fullmatch(word) is not None


def real_words(text: str, wren_recent: str = "") -> list[str]:
    """The words in text that are neither echo of wren_recent nor backchannels."""
    echo = set(words(wren_recent))
    return [w for w in words(text) if w not in echo and not _backchannel(w)]


def is_echo(text: str, wren_recent: str) -> bool:
    """Every word was something Wren just said: the speakers' tail, not the user."""
    heard = words(text)
    return bool(heard) and set(heard) <= set(words(wren_recent))


def is_interruption(text: str, wren_recent: str) -> bool:
    real = real_words(text, wren_recent)
    return len(real) >= 2 or (len(real) == 1 and real[0] in STOP_WORDS)
