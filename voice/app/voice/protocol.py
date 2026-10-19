"""Messages between the browser and voice/. Design: specs/2026-10-16-voice-loop-design.md, "Wire protocol".

Upstream binary frames are raw mic PCM (linear16, 16 kHz, mono). Downstream binary
frames are text-to-speech audio (linear16, 24 kHz, mono) behind a 4-byte header: the
turn and segment numbers as big-endian uint16, so the browser can drop audio from a
cancelled turn. Everything else is JSON with a "type".
"""

import json
import struct
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StringConstraints, TypeAdapter, ValidationError, field_validator

_HEADER = struct.Struct(">HH")


class Hello(BaseModel):
    type: Literal["hello"]
    mode: Literal["checkin", "mock"] = "checkin"
    tz: str = "UTC"  # the browser's IANA time zone, for "time of day" and pipeline dates
    session_id: Annotated[str, StringConstraints(max_length=64)] | None = None  # resume after a reconnect
    voice_out: bool = True  # speak replies, or text only
    token: str | None = None  # the user's sign-in token, once real auth exists

    @field_validator("tz")
    @classmethod
    def _known_zone(cls, tz: str) -> str:
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            return "UTC"
        return tz


class UserText(BaseModel):
    type: Literal["user_text"]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Mic(BaseModel):
    type: Literal["mic"]
    on: bool


class Playback(BaseModel):
    """A segment started or finished playing, as actually heard."""

    type: Literal["playback"]
    turn: Annotated[int, Field(ge=0, le=65535)]
    seg: Annotated[int, Field(ge=0, le=65535)]
    event: Literal["start", "end"]


class BargeIn(BaseModel):
    """The browser heard the user over Wren and paused playback."""

    type: Literal["barge_in"]
    turn: Annotated[int, Field(ge=0, le=65535)]
    seg: Annotated[int, Field(ge=0, le=65535)]
    played_ms: Annotated[float, Field(ge=0)]  # how much of that segment played before the pause
    onset_ms: Annotated[float, Field(ge=0)]  # from the start of the user's voice to the pause


class End(BaseModel):
    type: Literal["end"]


ClientMessage = Annotated[Hello | UserText | Mic | Playback | BargeIn | End, Field(discriminator="type")]
_CLIENT = TypeAdapter(ClientMessage)


def parse_client(raw: str) -> ClientMessage:
    try:
        return _CLIENT.validate_python(json.loads(raw))
    except json.JSONDecodeError as e:  # its message gives a position, never the text
        raise ValueError(f"bad client message: {e}") from None
    except ValidationError as e:
        # Name the field and the problem, never the input: that can be a user's private words.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'message'}: {error['type']}"
            for error in e.errors(include_input=False, include_url=False)
        )
        raise ValueError(f"bad client message: {problems}") from None


def pack_audio(turn: int, seg: int, pcm: bytes) -> bytes:
    return _HEADER.pack(turn % 65536, seg % 65536) + pcm


def unpack_audio(frame: bytes) -> tuple[int, int, bytes]:
    if len(frame) < _HEADER.size:
        raise ValueError("audio frame shorter than its header")
    turn, seg = _HEADER.unpack_from(frame)
    return turn, seg, frame[_HEADER.size :]
