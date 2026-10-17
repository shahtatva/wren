"""Decide when the user has finished their turn.

Finalized transcripts collect in a buffer. A candidate end is a final that Finalize
produced (the session sends Finalize when its silence detector hears the user stop) or
a final with Deepgram's own speech_final, whichever comes first. A mode-dependent hold
then waits a little longer: none in check-ins, most of a second in mocks, where people
pause to think mid-answer. New words or resumed speech cancel the hold. UtteranceEnd is
the backstop for noisy rooms. Pure logic: the caller passes the time in.
"""

from dataclasses import dataclass

from app.voice.events import Transcript, UtteranceEnd


@dataclass(frozen=True)
class Commit:
    text: str
    speech_end: float  # audio seconds where the user's last word ended


class EndOfTurn:
    def __init__(self, hold_s: float):
        self.hold_s = hold_s
        self.clear()

    def clear(self) -> None:
        self._finals: list[str] = []
        self._interim = ""
        self._last_word_end: float | None = None
        self._deadline: float | None = None

    def has_words(self) -> bool:
        return bool(self._finals or self._interim)

    def caption(self) -> str:
        return " ".join([*self._finals, self._interim]).strip()

    def _commit(self) -> Commit | None:
        text = " ".join(self._finals).strip()
        speech_end = self._last_word_end
        self.clear()
        if not text or speech_end is None:
            return None
        return Commit(text, speech_end)

    def _candidate_end(self, now: float) -> Commit | None:
        if self.hold_s <= 0:
            return self._commit()
        self._deadline = now + self.hold_s
        return None

    def on_transcript(self, t: Transcript, now: float) -> Commit | None:
        text = t.text.strip()
        if text:
            self._deadline = None  # they're still talking
        if not t.is_final:
            if text:
                self._interim = text
            return None
        if text:
            self._finals.append(text)
            self._last_word_end = t.end
            self._interim = ""
        if (t.speech_final or t.from_finalize) and self._finals:
            return self._candidate_end(now)
        return None

    def speech_resumed(self) -> None:
        """The silence detector heard the user again: don't commit on the old pause."""
        self._deadline = None

    def on_utterance_end(self, e: UtteranceEnd, now: float) -> Commit | None:
        if e.last_word_end < 0 or not self._finals:
            return None
        return self._commit()

    def poll(self, now: float) -> Commit | None:
        if self._deadline is not None and now >= self._deadline:
            return self._commit()
        return None
