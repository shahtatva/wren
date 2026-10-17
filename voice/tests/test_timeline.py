import pytest

from app.voice.timeline import AudioTimeline

FRAME = 640  # 20 ms of 16 kHz mono linear16


def test_arrival_of_maps_audio_time_to_the_frame_that_carried_it():
    timeline = AudioTimeline()
    for i, arrived in enumerate((10.0, 10.02, 10.04, 10.06)):
        timeline.add(FRAME, arrived)
    # 0.05 s of audio sits in the third frame (0.04-0.06), which arrived at 10.04.
    # That sample was captured 0.01 s before the frame's last sample.
    assert timeline.arrival_of(0.05) == pytest.approx(10.04 - 0.01)
    assert timeline.arrival_of(0.04) == pytest.approx(10.02)
    assert timeline.seconds == pytest.approx(0.08)


def test_arrival_of_before_first_frame_is_none():
    timeline = AudioTimeline()
    assert timeline.arrival_of(0.0) is None
    timeline.add(FRAME, 5.0)
    assert timeline.arrival_of(-0.01) is None


def test_arrival_past_the_last_frame_clamps_to_its_arrival():
    timeline = AudioTimeline()
    timeline.add(FRAME, 5.0)
    assert timeline.arrival_of(0.03) == pytest.approx(5.0)


def test_reset_starts_a_new_stream_at_zero():
    timeline = AudioTimeline()
    timeline.add(FRAME, 1.0)
    timeline.add(FRAME, 1.02)
    timeline.reset()
    timeline.add(FRAME, 7.0)
    assert timeline.seconds == pytest.approx(0.02)
    assert timeline.arrival_of(0.02) == pytest.approx(7.0)


def test_keeps_only_recent_audio():
    timeline = AudioTimeline(keep_s=1.0)
    for i in range(200):  # 4 s of frames
        timeline.add(FRAME, 100 + i * 0.02)
    assert timeline.arrival_of(0.5) is None  # dropped
    assert timeline.arrival_of(3.99) == pytest.approx(100 + 199 * 0.02 - 0.01)
