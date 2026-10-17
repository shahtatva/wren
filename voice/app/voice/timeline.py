"""Map Deepgram's audio timestamps to server time.

Nova-3 reports word times in seconds of audio since the stream opened. Each mic frame
is forwarded the moment it arrives, so the arrival time of the frame that carried a word
is when the user said it, give or take the hop from the browser. That puts the end of the
user's speech on the server clock, which is where time to first audio is measured from.
"""

from bisect import bisect_left


class AudioTimeline:
    def __init__(self, bytes_per_second: int = 32000, keep_s: float = 120.0):
        self._bytes_per_second = bytes_per_second
        self._keep_s = keep_s
        self.reset()

    def reset(self) -> None:
        """Start a new stream: Deepgram's clock restarts at zero on every connection."""
        self._ends: list[float] = []  # audio seconds at the end of each frame
        self._arrivals: list[float] = []
        self._first_start = 0.0  # audio seconds where the oldest kept frame begins
        self._total_bytes = 0

    @property
    def seconds(self) -> float:
        return self._total_bytes / self._bytes_per_second

    def add(self, nbytes: int, arrived: float) -> None:
        self._total_bytes += nbytes
        self._ends.append(self.seconds)
        self._arrivals.append(arrived)
        if self._ends[0] < self.seconds - 2 * self._keep_s:  # trim in batches, not per frame
            cut = bisect_left(self._ends, self.seconds - self._keep_s)
            self._first_start = self._ends[cut - 1]
            del self._ends[:cut]
            del self._arrivals[:cut]

    def arrival_of(self, audio_s: float) -> float | None:
        """When the audio at audio_s reached the server, or None if it's unknown or dropped."""
        if not self._ends or audio_s < self._first_start:
            return None
        i = bisect_left(self._ends, audio_s)
        if i == len(self._ends):  # rounding past the last frame
            return self._arrivals[-1]
        # The frame arrived with its last sample; earlier samples were captured earlier.
        return self._arrivals[i] - (self._ends[i] - audio_s)
