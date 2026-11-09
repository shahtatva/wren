import array
import random

import pytest

from app.voice.vad import ClearVoice, EnergyVad, RoomTalk

FRAME_MS = 20


def frame(level: int) -> bytes:
    """20 ms of 16 kHz audio: a square-ish wave at roughly this RMS, or noise when small."""
    rng = random.Random(level)
    if level <= 60:
        return array.array("h", [rng.randint(-level, level) for _ in range(320)]).tobytes()
    return array.array("h", [level if i % 16 < 8 else -level for i in range(320)]).tobytes()


def feed(vad: EnergyVad, levels: list[int], start: float = 0.0) -> list[tuple[float, str]]:
    events = []
    for i, level in enumerate(levels):
        now = start + i * FRAME_MS / 1000
        if (event := vad.push(frame(level))) is not None:
            events.append((round(now, 2), event))
    return events


def test_speech_then_quiet_gives_start_then_end_after_the_silence_window():
    vad = EnergyVad(silence_ms=300)
    events = feed(vad, [30] * 25 + [3000] * 50 + [30] * 30)
    # Speech starts at frame 25 (0.50 s) and is confirmed after 3 frames.
    assert events[0] == (0.54, "start")
    # Quiet begins at frame 75 (1.50 s); 15 quiet frames make 300 ms.
    assert events[1] == (1.78, "end")
    assert len(events) == 2


def test_speech_in_the_very_first_frames_is_still_speech():
    vad = EnergyVad(silence_ms=300)
    events = feed(vad, [3000] * 10 + [30] * 30)  # talking already when the mic opens
    assert [e for _, e in events] == ["start", "end"]


def test_short_blips_are_not_speech():
    vad = EnergyVad(silence_ms=300)
    assert feed(vad, [30] * 25 + [3000] * 2 + [30] * 30) == []  # a 40 ms click


def test_a_pause_shorter_than_the_window_does_not_end_speech():
    vad = EnergyVad(silence_ms=300)
    events = feed(vad, [30] * 25 + [3000] * 20 + [30] * 10 + [3000] * 20 + [30] * 20)
    assert [e for _, e in events] == ["start", "end"]


def test_the_noise_floor_adapts_to_a_louder_room():
    vad = EnergyVad(silence_ms=300)
    # A steady hum at ~600 RMS is the room, not speech, once the floor has learned it.
    assert feed(vad, [600] * 150) == []
    events = feed(vad, [5000] * 30 + [600] * 30, start=3.0)
    assert [e for _, e in events] == ["start", "end"]


def test_speech_can_start_again_after_an_end():
    vad = EnergyVad(silence_ms=300)
    events = feed(vad, [30] * 25 + [3000] * 20 + [30] * 20 + [3000] * 20 + [30] * 20)
    assert [e for _, e in events] == ["start", "end", "start", "end"]
    assert vad.speaking is False


def test_the_room_level_is_how_loud_its_talkers_get():
    room = RoomTalk()
    for _ in range(49):
        room.observe(600.0)
    assert room.level() is None  # under a second of frames: not known yet
    room = RoomTalk()
    for i in range(400):
        room.observe(3000.0 if i % 5 < 2 else 600.0)  # people talking 40% of the time
    assert room.level() == 3000.0


def test_clearly_you_needs_twice_the_room_and_the_floor():
    room = RoomTalk()
    assert room.clearly_you(201.0) and not room.clearly_you(199.0)  # before the room is known: today's floor
    for _ in range(400):
        room.observe(3000.0)
    assert not room.clearly_you(5999.0)
    assert room.clearly_you(6001.0)


def test_clear_voice_needs_200ms_within_260ms():
    you = ClearVoice()
    for i in range(9):
        you.push(True, i * 0.02)
    assert you.last_at is None
    you.push(True, 9 * 0.02)
    assert you.last_at == pytest.approx(0.18)
    gappy = ClearVoice()  # a 60 ms dip between syllables still counts
    for i, clear in enumerate([True] * 5 + [False] * 3 + [True] * 5):
        gappy.push(clear, i * 0.02)
    assert gappy.last_at == pytest.approx(0.24)


def test_heard_within():
    you = ClearVoice()
    for i in range(10):
        you.push(True, 0.82 + i * 0.02)
    assert you.last_at == pytest.approx(1.0)
    assert you.heard_within(2.9, 2.0)
    assert not you.heard_within(3.1, 2.0)


def test_the_room_level_rises_within_a_second_of_new_talk():
    room = RoomTalk()
    for _ in range(400):
        room.observe(100.0)  # 8 s of a quiet room
    for _ in range(30):
        room.observe(3000.0)  # then people start talking nearby
    assert room.level() == 3000.0


def test_clear_within_counts_any_clear_frame():
    you = ClearVoice()
    you.push(True, 1.0)
    assert you.last_at is None  # one frame isn't voice that counts...
    assert you.clear_within(1.25, 0.3) and not you.clear_within(1.35, 0.3)  # ...but it's still the owner talking
