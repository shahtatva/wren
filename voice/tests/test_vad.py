import array
import random

from app.voice.vad import EnergyVad

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
