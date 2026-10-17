"""One clock for the voice loop: monotonic for durations, mapped to UTC for log timestamps."""

import time
from datetime import datetime, timezone

# Fixed once, so a wall-clock adjustment mid-session can't bend measured durations.
_WALL_OFFSET = time.time() - time.monotonic()


def now() -> float:
    return time.monotonic()


def wall(mono: float) -> datetime:
    return datetime.fromtimestamp(mono + _WALL_OFFSET, tz=timezone.utc)


def iso(mono: float | None) -> str | None:
    if mono is None:
        return None
    return wall(mono).isoformat(timespec="milliseconds").replace("+00:00", "Z")
