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

    def has_room(self, key: str) -> bool:
        hits = self._hits.get(key)
        if hits is None:
            return True
        cutoff = self._clock() - self._window_s
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return len(hits) < self._limit

    def record(self, key: str) -> None:
        self._hits.setdefault(key, deque()).append(self._clock())

    def allow(self, key: str) -> bool:
        if not self.has_room(key):
            return False
        self.record(key)
        return True


def allow_all(*checks: tuple[SlidingWindow, str]) -> bool:
    """Count the request against every window, but only if all of them have room: a refused
    request uses up no one's budget."""
    if not all(window.has_room(key) for window, key in checks):
        return False
    for window, key in checks:
        window.record(key)
    return True
