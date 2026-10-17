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
from typing import Literal

BYTES_PER_MS = 32  # 16 kHz mono linear16
# The floor starts from the first frame, but no higher than a quiet room: if the user is
# already talking when the mic opens, their voice mustn't become the floor.
INITIAL_FLOOR_CAP = 250.0


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
