import json

import pytest

from app.voice.protocol import BargeIn, Hello, Playback, UserText, pack_audio, parse_client, unpack_audio


def test_hello_defaults():
    hello = parse_client(json.dumps({"type": "hello"}))
    assert isinstance(hello, Hello)
    assert (hello.mode, hello.tz, hello.session_id, hello.voice_out, hello.token) == ("checkin", "UTC", None, True, None)


def test_hello_with_an_unknown_time_zone_falls_back_to_utc():
    assert parse_client(json.dumps({"type": "hello", "tz": "Mars/Olympus"})).tz == "UTC"
    assert parse_client(json.dumps({"type": "hello", "tz": "America/Chicago"})).tz == "America/Chicago"


def test_client_messages_parse_by_type():
    assert parse_client('{"type": "user_text", "text": "  Sprout moved  "}') == UserText(type="user_text", text="Sprout moved")
    assert isinstance(parse_client('{"type": "playback", "turn": 3, "seg": 0, "event": "end"}'), Playback)
    barge = parse_client('{"type": "barge_in", "turn": 3, "seg": 1, "played_ms": 840.5, "onset_ms": 130}')
    assert isinstance(barge, BargeIn) and barge.played_ms == 840.5


@pytest.mark.parametrize(
    "raw",
    [
        '{"type": "shout"}',
        '{"type": "user_text", "text": "   "}',
        '{"type": "hello", "mode": "therapy"}',
        "not json",
        "[]",
    ],
)
def test_unknown_type_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_client(raw)


def test_audio_header_round_trips_and_wraps_turn_numbers():
    frame = pack_audio(65537, 3, b"\x01\x02\x03\x04")
    assert frame[:4] == b"\x00\x01\x00\x03"  # big-endian uint16 turn, then seg
    assert unpack_audio(frame) == (1, 3, b"\x01\x02\x03\x04")
    with pytest.raises(ValueError):
        unpack_audio(b"\x00\x01")
