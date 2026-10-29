import logging
from dataclasses import replace
from datetime import datetime

import pytest

from app.voice import clock
from app.voice.metrics import TurnTiming, log_turn


def voice_turn(ttfa_s: float = 0.9) -> TurnTiming:
    t0 = clock.now()
    return TurnTiming(
        turn=4,
        mode="checkin",
        input="voice",
        speech_end=t0,
        committed=t0 + 0.30,
        retrieval_start=t0 + 0.30,
        retrieval_end=t0 + 0.52,
        llm_start=t0 + 0.53,
        llm_first_token=t0 + 0.71,
        first_segment=t0 + 0.75,
        tts_first_byte=t0 + ttfa_s - 0.01,
        first_audio_sent=t0 + ttfa_s,
        memories=8,
        model="qwen/qwen3.8-27b",
        context_tokens=1650,
        outcome="complete",
    )


def test_ttfa_is_first_audio_minus_speech_end():
    assert voice_turn().ttfa_ms() == pytest.approx(900, abs=1)
    assert TurnTiming(turn=1, mode="checkin", input="text").ttfa_ms() is None


def test_record_has_iso_stamps_for_speech_end_first_token_first_audio():
    record = voice_turn().record()
    assert record["ttfa_ms"] == 900
    assert record["endpoint_ms"] == 300
    assert record["retrieval_ms"] == 220
    assert record["llm_ttft_ms"] == 180
    assert record["tts_ttfb_ms"] == 140
    assert record["turn_to_audio_ms"] == 600
    stamps = [datetime.fromisoformat(record[k].replace("Z", "+00:00")) for k in ("speech_end_at", "first_token_at", "first_audio_at")]
    assert stamps == sorted(stamps)
    assert (stamps[2] - stamps[0]).total_seconds() == pytest.approx(0.9, abs=0.002)
    assert record["turn"] == 4 and record["input"] == "voice" and record["model"] == "qwen/qwen3.8-27b"


def test_record_of_a_turn_without_audio_has_no_ttfa():
    record = TurnTiming(turn=2, mode="checkin", input="text", committed=clock.now()).record()
    assert record["ttfa_ms"] is None and record["first_audio_at"] is None and record["committed_at"]


def test_log_line_warns_over_1500ms(caplog):
    caplog.set_level(logging.INFO, logger="app.voice.metrics")
    log_turn("s-1", voice_turn(ttfa_s=0.9))
    log_turn("s-1", voice_turn(ttfa_s=1.6))
    fast, slow = [r for r in caplog.records if r.name == "app.voice.metrics"]
    assert fast.levelno == logging.INFO and slow.levelno == logging.WARNING
    message = slow.getMessage()
    for field in ("session=s-1", "turn=4", "ttfa_ms=1600", "speech_end_at=", "first_token_at=", "first_audio_at=", "outcome=complete"):
        assert field in message


def test_record_and_log_line_say_when_a_turn_switched_roles(caplog):
    caplog.set_level(logging.INFO, logger="app.voice.metrics")
    timing = replace(voice_turn(), switch="mock_start")
    assert timing.record()["switch"] == "mock_start"
    log_turn("s-1", timing)
    assert "switch=mock_start" in caplog.text
