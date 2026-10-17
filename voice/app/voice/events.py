"""Events the speech adapters emit. Times on speech-to-text events are seconds of audio
since the stream opened, as Deepgram reports them; AudioTimeline maps them to server time.

The adapters normalize Deepgram's messages into these, so the session and its tests
never see provider JSON, and a different speech-to-text model (Flux, say) is an adapter swap.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeechStarted:
    at: float


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool  # this stretch of audio won't be transcribed again
    speech_final: bool  # Deepgram heard the endpointing pause after it
    start: float
    end: float  # end of the last word
    from_finalize: bool = False  # returned because the session sent Finalize at the end of speech


@dataclass(frozen=True)
class UtteranceEnd:
    last_word_end: float  # -1 when Deepgram finalized before the gap was reached


SttEvent = SpeechStarted | Transcript | UtteranceEnd


@dataclass(frozen=True)
class TtsAudio:
    turn: int
    seg: int
    pcm: bytes


@dataclass(frozen=True)
class TtsDone:
    """All audio for (turn, seg) has been sent."""

    turn: int
    seg: int


TtsEvent = TtsAudio | TtsDone
