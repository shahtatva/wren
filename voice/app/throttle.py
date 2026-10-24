"""In-memory rate limits for sign-in and token lookups.

Every Supabase Auth call comes from this server's one IP, so Supabase's per-IP limits are
shared by everyone who signs in. These keep any one email, and everyone together, under
them. They live in memory, which works because the voice server runs as one instance.
"""

import time
from collections import deque
from collections.abc import Callable


class Throttled(Exception):
    """Over a limit. The caller should try again later."""


class SlidingWindow:
    """At most `limit` requests per key in any `window_s` seconds."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self._limit, self._window_s, self._clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self._window_s:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
