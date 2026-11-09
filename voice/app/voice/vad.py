"""Detect the end of the user's speech from mic energy, so Nova-3 can be asked to finalize.

Measured 2026-10-16: Nova-3's own endpointing fired about 1.5 s after the last word
at endpointing=10 or 100, and not at all within 3 s at 300 or 500. Asked with a
Finalize message instead, it returns finals in about 100 ms. So this detector watches
the frames as they're forwarded, and after a short quiet stretch the session sends
Finalize. Deepgram's own speech_final and UtteranceEnd still count if they come first,
and UtteranceEnd (based on word timing) covers rooms too noisy for an energy detector.

The noise floor tracks the room: it falls at once to a quieter frame and rises slowly,
and only while nobody is speaking.
"""

import array
import math
from collections import deque
from typing import Literal

BYTES_PER_MS = 32  # 16 kHz mono linear16
# The floor starts from the first frame, but no higher than a quiet room: if the user is
# already talking when the mic opens, their voice mustn't become the floor.
INITIAL_FLOOR_CAP = 250.0

# Telling the owner's voice from the room's talkers (docs/superpowers/specs/2026-11-06-room-noise-design.md).
# Mirrored in web/lib/voice/vad.ts, so the page and the server judge "it's you" the same way.
ROOM_FRAMES = 400  # the last 8 s of 20 ms frames heard while Wren speaks
ROOM_RECENT_FRAMES = 120  # and the last 2.4 s of them: new talk raises the bar within a second, two cut-ins don't
ROOM_MIN_FRAMES = 50  # a second of them before the room's level counts
ROOM_PERCENTILE = 0.8  # how loud the talkers get; the owner's short cut-ins stay under a fifth
ROOM_MARGIN = 2.0  # the owner's voice must be twice as loud as the room's talkers (6 dB)
MIN_YOU = 200.0  # and above EnergyVad's own start floor, which is all a quiet room asks
CLEAR_START_MS = 200.0  # as the page's onset: 200 ms of clear voice within 260 ms
CLEAR_WINDOW_MS = 260.0
# The server trusts its yardstick for moments the owner is normally quiet in only after 3 s of them:
# talk that starts just after a quiet first second would otherwise count as the owner's voice.
ROOM_TRUST_FRAMES = 150


def rms(frame: bytes) -> float:
    samples = array.array("h", frame[: len(frame) - len(frame) % 2])
    return math.sqrt(math.sumprod(samples, samples) / len(samples)) if samples else 0.0


class EnergyVad:
    def __init__(self, silence_ms: int = 300, start_ms: int = 60, min_start_rms: float = 200.0, min_quiet_rms: float = 150.0):
        self._silence_ms = silence_ms
        self._start_ms = start_ms
        self._min_start = min_start_rms
        self._min_quiet = min_quiet_rms
        self._floor: float | None = None
        self._loud_ms = 0.0
        self._quiet_ms = 0.0
        self.speaking = False

    def push(self, frame: bytes) -> Literal["start", "end"] | None:
        level = rms(frame)
        frame_ms = len(frame) / BYTES_PER_MS
        if self._floor is None:
            self._floor = min(max(level, 1.0), INITIAL_FLOOR_CAP)
        if self.speaking:
            if level < max(self._floor * 2.0, self._min_quiet):
                self._quiet_ms += frame_ms
                if self._quiet_ms >= self._silence_ms:
                    self.speaking, self._quiet_ms = False, 0.0
                    return "end"
            else:
                self._quiet_ms = 0.0
            return None
        if level > max(self._floor * 3.0, self._min_start):
            self._loud_ms += frame_ms
            if self._loud_ms >= self._start_ms:
                self.speaking, self._loud_ms = True, 0.0
                return "start"
            return None
        self._loud_ms = 0.0
        self._floor = level if level < self._floor else self._floor + (level - self._floor) * 0.02
        self._floor = max(self._floor, 1.0)
        return None


def _percentile(levels) -> float:
    ordered = sorted(levels)
    return ordered[min(len(ordered) - 1, int(len(ordered) * ROOM_PERCENTILE))]


class RoomTalk:
    """How loud the room's talkers get: a high percentile of the levels heard while the owner is
    normally quiet (Wren speaking, or preparing its opening). The owner talks a few inches from the
    phone, so their voice reaches the mic much louder than anyone a metre away. The room's quiet
    floor can't tell them apart: two people taking turns leave gaps, and the floor sits in them."""

    def __init__(self):
        self._levels: deque[float] = deque(maxlen=ROOM_FRAMES)

    def observe(self, level: float) -> None:
        self._levels.append(level)

    @property
    def frames(self) -> int:
        """How many of the last ROOM_FRAMES levels it has heard."""
        return len(self._levels)

    def level(self) -> float | None:
        if len(self._levels) < ROOM_MIN_FRAMES:
            return None
        recent = list(self._levels)[-ROOM_RECENT_FRAMES:]
        return max(_percentile(self._levels), _percentile(recent))

    def clearly_you(self, level: float) -> bool:
        talkers = self.level()
        return level > max(MIN_YOU, talkers * ROOM_MARGIN if talkers is not None else 0.0)


class ClearVoice:
    """When voice that's clearly the owner's was last heard: 200 ms of it within 260 ms, as the
    page needs before it pauses Wren."""

    def __init__(self):
        self._recent: deque[tuple[float, bool, float]] = deque()
        self.last_at: float | None = None  # the last time the owner's voice counted: 200 ms of it
        self.last_clear_at: float | None = None  # the last single frame of it, gaps between words and all

    def push(self, clear: bool, now: float, frame_ms: float = 20.0) -> None:
        self._recent.append((now, clear, frame_ms))
        if clear:
            self.last_clear_at = now
        while self._recent and self._recent[0][0] <= now - CLEAR_WINDOW_MS / 1000:
            self._recent.popleft()
        if clear and sum(ms for _, voiced, ms in self._recent if voiced) >= CLEAR_START_MS:
            self.last_at = now

    def heard_within(self, now: float, seconds: float) -> bool:
        return self.last_at is not None and now - self.last_at <= seconds

    def clear_within(self, now: float, seconds: float) -> bool:
        """Any frame of the owner's voice this recently: still talking, between words or not."""
        return self.last_clear_at is not None and now - self.last_clear_at <= seconds
