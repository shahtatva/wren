import asyncio
import logging
import threading
import time
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
from uuid import UUID

import psycopg
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.memory.live import MemoryProvider
from app.voice.events import SpeechStarted, Transcript
from tests.voice_fakes import (
    FAST,
    ORIGIN,
    FakeAuth,
    FakeChat,
    FakeStt,
    FakeTts,
    RecordingMemory,
    Wire,
    make_runtime,
    play,
    send_silence,
    voice_session,
)

OPENING = ["Hey Maya. ", "How's the week going?"]


def final(text, start, end, speech_final=True):
    return Transcript(text, True, speech_final, start, end)


def interim(text, start=0.0, end=0.5):
    return Transcript(text, False, False, start, end)


def test_bad_origin_is_refused_before_accept():
    app.state.runtime = make_runtime(FakeChat([OPENING]))
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            with pytest.raises(WebSocketDisconnect) as refused:
                with client.websocket_connect("/ws", headers={"origin": "https://elsewhere.example"}):
                    pass
            assert refused.value.code == 1008
    finally:
        app.state.runtime = None


def test_a_missing_hello_closes_the_socket():
    app.state.runtime = make_runtime(FakeChat([OPENING]))
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
                assert Wire(ws).next(timeout=3) == {"type": "_closed", "code": 4400}
    finally:
        app.state.runtime = None


def test_without_an_identity_the_hello_is_refused():
    app.state.runtime = make_runtime(FakeChat([OPENING]))
    try:
        with TestClient(app, client=("192.168.1.20", 50000)) as client:  # not loopback
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
                ws.send_json({"type": "hello"})
                wire = Wire(ws)
                assert wire.next()["code"] == "unauthorized"
                assert wire.next() == {"type": "_closed", "code": 4401}
    finally:
        app.state.runtime = None


SIGNED_IN = UUID("0b8d1f4e-9c2a-4f6e-8a1d-3e5f7a9b1c2d")
AWAY = "203.0.113.9"  # not loopback: only a token gets in


def signed_in_runtime(**options):
    return make_runtime(FakeChat([OPENING]), auth=FakeAuth({"good-token": SIGNED_IN}), dev_user=None, **options)


def refused_hello(runtime, client_host=AWAY, **hello):
    """Send a hello and return the error and the close that follow it."""
    app.state.runtime = runtime
    try:
        with TestClient(app, client=(client_host, 50000)) as client:
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
                ws.send_json({"type": "hello", **hello})
                wire = Wire(ws)
                return wire.next(), wire.next()
    finally:
        app.state.runtime = None


def test_hello_with_a_valid_token_starts_a_session():
    memory = RecordingMemory()
    with voice_session(signed_in_runtime(memory=memory), client_host=AWAY, token="good-token") as (_, _, ready):
        assert ready["type"] == "ready"
    assert memory.opened == [SIGNED_IN]


def test_an_unknown_token_is_refused_for_good():
    error, closed = refused_hello(signed_in_runtime(), token="junk")
    assert error["code"] == "unauthorized" and error["message"] and closed == {"type": "_closed", "code": 4401}


def test_hello_without_age_confirmation_is_refused():
    error, closed = refused_hello(signed_in_runtime(adult=False), token="good-token")
    assert error["code"] == "age_unconfirmed" and error["message"] and closed == {"type": "_closed", "code": 4401}


def test_an_unanswered_age_check_closes_with_the_retry_code():
    error, closed = refused_hello(signed_in_runtime(adult=psycopg.OperationalError("down")), token="good-token")
    assert error["code"] == "age_check_failed" and error["message"] and closed == {"type": "_closed", "code": 4503}


def test_too_many_token_lookups_close_with_the_retry_code():
    runtime = make_runtime(FakeChat([OPENING]), auth=FakeAuth({}, throttled=True), dev_user=None)
    error, closed = refused_hello(runtime, token="good-token")
    assert error["code"] == "unavailable" and error["message"] and closed == {"type": "_closed", "code": 4503}


class FlakyPostgres:
    """Up, until `error` is set: then every call raises it, like an unreachable database."""

    def __init__(self):
        self.error = None

    def unavailable_reason(self, user_id):
        if self.error:
            raise self.error
        return None

    async def age_confirmed(self, user_id):
        if self.error:
            raise self.error
        return True


def test_a_remembered_adult_starts_while_the_database_is_down():
    postgres = FlakyPostgres()
    runtime = signed_in_runtime()
    runtime.memory = MemoryProvider(postgres=postgres)
    assert asyncio.run(runtime.memory.age_confirmed(SIGNED_IN))  # an earlier session's check
    postgres.error = psycopg.OperationalError("down")
    with voice_session(runtime, client_host=AWAY, token="good-token") as (_, _, ready):
        assert ready["memory"].startswith("off: database unreachable")


def test_the_dev_identity_skips_the_age_check():
    with voice_session(make_runtime(FakeChat([OPENING]), adult=False)) as (_, _, ready):
        assert ready["type"] == "ready"


def test_a_refused_hello_never_logs_its_token(caplog):
    caplog.set_level(logging.DEBUG)
    refused_hello(signed_in_runtime(), token="secret-token-value")
    refused_hello(signed_in_runtime(adult=False), token="good-token")
    refused_hello(signed_in_runtime(adult=psycopg.OperationalError("down")), token="good-token")
    assert caplog.records
    assert "secret-token-value" not in caplog.text and "good-token" not in caplog.text


def test_opening_then_voice_turn_streams_segments_audio_and_metrics():
    stt, tts = FakeStt(), FakeTts()
    chat = FakeChat([OPENING, ["Friday works. ", "Want to prep tonight?"]])
    with voice_session(make_runtime(chat, stt=stt, tts=tts)) as (ws, wire, ready):
        assert ready["voice"] is True and ready["memory"] == "off: test" and ready["resumed"] is False
        opening = play(ws, wire, turn=1)
        assert [m["text"] for m in opening] == ["Hey Maya.", "How's the week going?"]
        assert {(t, s) for t, s, _ in wire.audio} == {(1, 0), (1, 1)}

        send_silence(ws, stt, 1.0)
        stt.emit(final("Sprout moved to Friday.", 0.2, 0.8))
        user, _ = wire.until("user_turn")
        assert user == {"type": "user_turn", "turn": 2, "text": "Sprout moved to Friday."}
        reply = play(ws, wire, turn=2)
        assert [m["text"] for m in reply] == ["Friday works.", "Want to prep tonight?"]
        assert tts.spoken == ["Hey Maya.", "How's the week going?", "Friday works.", "Want to prep tonight?"]
        assert chat.calls[1][-1] == {"role": "user", "content": "Sprout moved to Friday."}


def test_metrics_carry_speech_end_first_token_first_audio():
    stt = FakeStt()
    with voice_session(make_runtime(FakeChat([OPENING, ["Got it."]]), stt=stt)) as (ws, wire, _):
        play(ws, wire, turn=1)
        send_silence(ws, stt, 1.0)
        stt.emit(final("Sprout moved.", 0.2, 0.8))
        metrics, _ = wire.until("metrics", turn=2)
        stamps = [datetime.fromisoformat(metrics[k].replace("Z", "+00:00")) for k in ("speech_end_at", "first_token_at", "first_audio_at")]
        assert stamps == sorted(stamps)
        assert metrics["ttfa_ms"] >= 0 and metrics["input"] == "voice" and metrics["model"] == "fake-chat"


def test_silence_after_speech_asks_deepgram_to_finalize():
    stt = FakeStt()
    with voice_session(make_runtime(FakeChat([OPENING]), stt=stt)) as (ws, wire, _):
        loud = (b"\x00\x30" * 320)  # 20 ms at RMS ~12k
        for _ in range(10):
            ws.send_bytes(loud)
        send_silence(ws, stt, 0.5)
        deadline = time.monotonic() + 3
        while stt.finalized == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert stt.finalized == 1


def speaking_session(chat, **runtime):
    """A session whose opening has played, so the next reply is the one under test."""
    stt = FakeStt()
    rt = make_runtime(chat, stt=stt, **runtime)
    session = voice_session(rt)
    ws, wire, ready = session.__enter__()
    play(ws, wire, turn=1)
    return session, ws, wire, ready, stt, rt


def test_barge_in_confirmed_cancels_and_trims_history():
    chat = FakeChat([OPENING, ["First sentence here is long enough to cut. ", "Second one. "]], hang_after={1: 2})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Give me feedback"})
        wire.until("segment_end", turn=2, seg=1)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 100, "onset_ms": 120})
        wire.quiet(0.1)
        stt.emit(interim("wait I meant Monday"))
        wire.until("interrupt", turn=2)
        metrics, _ = wire.until("metrics", turn=2, outcome="interrupted")
        assert metrics["barge_in_ms"] == 120
        convo = rt.registry.get(ready["session_id"])
        wren = convo.turns[-1]
        assert (wren.speaker, wren.text, wren.interrupted) == ("wren", "First sentence here is", True)
        assert 1 in chat.cancelled
    finally:
        session.__exit__(None, None, None)


def test_backchannel_during_pause_resumes():
    chat = FakeChat([OPENING, ["Lead with the number. ", "Then the result. "]], hang_after={1: 2})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Feedback please"})
        wire.until("segment_end", turn=2, seg=1)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 80, "onset_ms": 110})
        wire.quiet(0.1)
        stt.emit(interim("mm-hmm"))
        resumed, seen = wire.until("resume", turn=2, timeout=2)
        assert not [m for m in seen if m["type"] == "interrupt"]
        assert chat.cancelled == []
    finally:
        session.__exit__(None, None, None)


def test_a_pause_holds_while_the_user_is_still_talking():
    chat = FakeChat([OPENING, ["Lead with the number. ", "Then the result. "]], hang_after={1: 2})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Feedback please"})
        wire.until("segment_end", turn=2, seg=1)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 80, "onset_ms": 110})
        for _ in range(30):  # 600 ms of talking, twice the confirm window, before any words arrive
            ws.send_bytes(b"\x00\x30" * 320)
            time.sleep(0.02)
        assert not [m for m in wire.quiet(0.05) if m["type"] == "resume"]
        send_silence(ws, stt, 0.4)
        wire.until("resume", turn=2, timeout=2)
    finally:
        session.__exit__(None, None, None)


def test_echo_during_playback_is_never_committed():
    chat = FakeChat([OPENING, ["You lost me around the second project. ", "Lead with the result."]])
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "How was that?"})
        wire.until("reply_end", turn=2)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        send_silence(ws, stt, 1.0)
        stt.emit(final("lost me around the second project", 0.1, 0.9))
        assert not [m for m in wire.quiet(0.4) if m["type"] in ("user_turn", "interrupt")]
        for seg in (0, 1):
            ws.send_json({"type": "playback", "turn": 2, "seg": seg, "event": "end"})
        wire.until("metrics", turn=2, outcome="complete")
        send_silence(ws, stt, 0.5)
        stt.emit(final("Monday works.", 1.1, 1.4))
        user, _ = wire.until("user_turn")
        assert user["text"] == "Monday works."
    finally:
        session.__exit__(None, None, None)


def test_speech_while_thinking_merges_into_one_user_turn():
    chat = FakeChat([OPENING, ["never said"], ["Monday it is."]], hang_before={1})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        send_silence(ws, stt, 1.5)
        stt.emit(final("Sprout moved my screen", 0.1, 0.9))
        wire.until("user_turn", turn=2, text="Sprout moved my screen")
        stt.emit(SpeechStarted(1.0))
        stt.emit(interim("to Monday", 1.0, 1.2))
        end, _ = wire.until("reply_end", turn=2)
        assert end["outcome"] == "continued"
        stt.emit(final("to Monday.", 1.0, 1.4))
        merged, _ = wire.until("user_turn", turn=3)
        assert merged["text"] == "Sprout moved my screen to Monday."
        wire.until("reply_end", turn=3)
        assert chat.calls[2][-1]["content"] == "Sprout moved my screen to Monday."
        assert 1 in chat.cancelled
    finally:
        session.__exit__(None, None, None)


LOUD = b"\x00\x30" * 320  # 20 ms of voice-level audio


def test_resumed_speech_while_thinking_merges_before_any_words_arrive():
    chat = FakeChat([OPENING, ["never said"], ["Monday it is."]], hang_before={1})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        send_silence(ws, stt, 1.0)
        stt.emit(final("Can you help me tighten my", 0.1, 0.9))
        wire.until("user_turn", turn=2)
        for _ in range(5):  # they keep talking; Deepgram's words for it are still a second away
            ws.send_bytes(LOUD)
        end, _ = wire.until("reply_end", turn=2)
        assert end["outcome"] == "continued"
        stt.emit(final("tell me about yourself?", 1.0, 1.6))
        merged, _ = wire.until("user_turn", turn=3)
        assert merged["text"] == "Can you help me tighten my tell me about yourself?"
    finally:
        session.__exit__(None, None, None)


def test_a_cough_while_thinking_recommits_the_first_part():
    chat = FakeChat([OPENING, ["never said"], ["Got it."]], hang_before={1})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        send_silence(ws, stt, 1.0)
        stt.emit(final("Sprout moved my screen", 0.1, 0.9))
        wire.until("user_turn", turn=2)
        for _ in range(5):
            ws.send_bytes(LOUD)
        wire.until("reply_end", turn=2, outcome="continued")
        send_silence(ws, stt, 0.4)  # quiet again, and no words ever come
        again, _ = wire.until("user_turn", turn=3, timeout=3)
        assert again["text"] == "Sprout moved my screen"
    finally:
        session.__exit__(None, None, None)


def test_a_reply_finishes_even_if_the_browser_never_reports_playback():
    with voice_session(make_runtime(FakeChat([["Hey there."]]), stt=FakeStt())) as (ws, wire, _):
        wire.until("reply_end", turn=1)  # all audio sent; the page never says it played
        metrics, _ = wire.until("metrics", turn=1, outcome="complete", timeout=3)
        assert metrics["ttfa_ms"] is None


def test_echo_of_the_last_reply_right_after_it_ends_is_dropped():
    chat = FakeChat([OPENING, ["You lost me around the second project."]])
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "How was that?"})
        play(ws, wire, turn=2)
        send_silence(ws, stt, 1.0)
        stt.emit(final("around the second project", 0.1, 0.9))  # Nova-3 catching up on the speakers' tail
        assert not [m for m in wire.quiet(0.4) if m["type"] == "user_turn"]
        stt.emit(final("Monday works.", 0.95, 0.99))
        user, _ = wire.until("user_turn")
        assert user["text"] == "Monday works."
    finally:
        session.__exit__(None, None, None)


def test_the_ticker_survives_an_unexpected_error(monkeypatch):
    from app.voice import endpoint

    calls = {"n": 0}
    real_poll = endpoint.EndOfTurn.poll

    def flaky_poll(self, now):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_poll(self, now)

    monkeypatch.setattr(endpoint.EndOfTurn, "poll", flaky_poll)
    chat = FakeChat([OPENING, ["Lead with the number. ", "Then the result. "]], hang_after={1: 2})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Feedback please"})
        wire.until("segment_end", turn=2, seg=1)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 80, "onset_ms": 110})
        wire.until("resume", turn=2, timeout=2)  # the ticker is what resumes a pause
        assert calls["n"] > 1
    finally:
        session.__exit__(None, None, None)


def test_typed_text_interrupts_and_commits():
    chat = FakeChat([OPENING, ["Okay so here is the thing. ", "And more."]], hang_after={1: 1})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Tell me"})
        wire.until("segment_end", turn=2, seg=0)
        ws.send_json({"type": "user_text", "text": "Actually, Tuesday."})
        wire.until("interrupt", turn=2)
        user, _ = wire.until("user_turn", turn=3)
        assert user["text"] == "Actually, Tuesday."
    finally:
        session.__exit__(None, None, None)


def test_stt_failure_switches_to_text_and_text_still_works():
    stt = FakeStt(fail=RuntimeError("deepgram hung up"))
    chat = FakeChat([OPENING, ["Text still works."]])
    with voice_session(make_runtime(chat, stt=stt)) as (ws, wire, ready):
        lost, _ = wire.until("voice")
        assert lost == {"type": "voice", "available": False, "reason": "stt_closed"}
        ws.send_json({"type": "user_text", "text": "hello"})
        user, _ = wire.until("user_turn", text="hello")
        segment, _ = wire.until("segment", turn=user["turn"])
        assert segment["text"] == "Text still works."


STALLED = {"type": "voice", "available": False, "reason": "stt_stalled"}


def test_a_stalled_deepgram_send_drops_hearing_and_keeps_the_session_going():
    stalled, fresh = FakeStt(hang={"send"}), FakeStt()
    connections = [stalled, fresh]

    async def connect():
        return connections.pop(0)

    chat = FakeChat([OPENING, ["Text still works."]])
    rt = make_runtime(chat, stt_factory=connect, tuning=replace(FAST, stt_send_timeout_s=0.2))
    with voice_session(rt) as (ws, wire, _):
        play(ws, wire, turn=1)
        ws.send_bytes(b"\x00" * 640)
        lost, _ = wire.until("voice", timeout=2)
        assert lost == STALLED and stalled.closed
        ws.send_json({"type": "user_text", "text": "hello"})
        _, seen = wire.until("reply_end", turn=2, outcome="complete")
        back = {"type": "voice", "available": True}
        if back not in seen:
            wire.until("voice", available=True, timeout=3)  # the usual retry, after a second


def test_a_stalled_keepalive_doesnt_freeze_the_ticker():
    stt = FakeStt(hang={"keep_alive"})
    chat = FakeChat([OPENING, ["Still here."]])
    rt = make_runtime(chat, stt=stt, tuning=replace(FAST, keepalive_s=0.1, stt_send_timeout_s=0.2))
    with voice_session(rt) as (ws, wire, _):
        lost, _ = wire.until("voice", timeout=2)  # no audio arrives, so the ticker sends a KeepAlive
        assert lost == STALLED
        ws.send_json({"type": "user_text", "text": "hello"})
        wire.until("reply_end", turn=2, outcome="complete")
        # Nobody reports playback, so only the ticker can finish the turn.
        wire.until("metrics", turn=2, outcome="complete", timeout=3)


def test_tts_failure_still_delivers_the_text():
    chat = FakeChat([["Hey there."]])
    with voice_session(make_runtime(chat, stt=FakeStt(), tts=FakeTts(error=RuntimeError("aura down")))) as (ws, wire, _):
        segment, _ = wire.until("segment", turn=1)
        end, _ = wire.until("segment_end", turn=1, seg=0)
        assert segment["text"] == "Hey there." and end["samples"] == 0
        wire.until("metrics", turn=1, outcome="complete")  # nothing to play, so the turn completes


def test_disconnect_mid_reply_cancels_and_saves_heard_text():
    memory = RecordingMemory()
    chat = FakeChat([OPENING, ["Okay so here is the thing. ", "And more."]], hang_after={1: 1})
    session, ws, wire, ready, stt, rt = speaking_session(chat, memory=memory)
    try:
        ws.send_json({"type": "user_text", "text": "hello"})
        wire.until("segment_end", turn=2, seg=0)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        wire.quiet(0.1)
        ws.close()
        deadline = time.monotonic() + 3
        while not memory.closed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert memory.closed == [ready["session_id"]]
        wren = [t for t in memory.appended if t["speaker"] == "wren"][-1]
        assert wren.get("interrupted") is True
        assert wren["text"] and "Okay so here is the thing.".startswith(wren["text"])
        assert {"id": f"{ready['session_id']}:t02", "speaker": "user", "text": "hello"} in memory.appended
        assert 1 in chat.cancelled and stt.closed
    finally:
        try:
            session.__exit__(None, None, None)
        except Exception:
            pass


def test_reconnect_with_session_id_resumes_history():
    chat = FakeChat([OPENING, ["Noted."], ["You said hello there."]])
    runtime = make_runtime(chat, stt=None)
    with voice_session(runtime) as (ws, wire, ready):
        play(ws, wire, turn=1)
        ws.send_json({"type": "user_text", "text": "hello there"})
        play(ws, wire, turn=2)
        session_id = ready["session_id"]
    with voice_session(runtime, session_id=session_id) as (ws, wire, ready):
        assert ready["session_id"] == session_id and ready["resumed"] is True
        ws.send_json({"type": "user_text", "text": "what did I say?"})
        wire.until("reply_end")
    contents = [m["content"] for m in chat.calls[2]]
    assert "hello there" in contents and "Noted." in contents and len(chat.calls) == 3  # no second opening


def test_pauses_extend_the_playback_deadline():
    chat = FakeChat([OPENING, ["Lead with the number and then the result."]])
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Feedback please"})
        wire.until("reply_end", turn=2)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 50, "onset_ms": 100})
        for _ in range(50):  # about 1 s of murmuring: longer than the audio plus the grace
            ws.send_bytes(LOUD)
            time.sleep(0.02)
        send_silence(ws, stt, 0.4)
        wire.until("resume", turn=2, timeout=3)
        assert not [m for m in wire.quiet(0.3) if m.get("outcome") == "complete"]  # the browser is still playing
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "end"})
        wire.until("metrics", turn=2, outcome="complete")
    finally:
        session.__exit__(None, None, None)


def test_a_twice_paused_reply_can_still_be_interrupted():
    chat = FakeChat([OPENING, ["Lead with the number and then the result."]])
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Feedback please"})
        wire.until("reply_end", turn=2)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        for played in (40, 80):  # two pauses with no words, as echo on speakers causes
            ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": played, "onset_ms": 100})
            wire.until("resume", turn=2, timeout=3)
        wire.quiet(0.2)  # past the old deadline: 200 ms of audio plus 0.5 s of grace
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 120, "onset_ms": 100})
        stt.emit(interim("wait, I meant Monday"))
        wire.until("interrupt", turn=2)
    finally:
        session.__exit__(None, None, None)


def test_a_barge_in_for_a_finished_reply_gets_a_resume():
    with voice_session(make_runtime(FakeChat([OPENING]), stt=FakeStt())) as (ws, wire, _):
        play(ws, wire, turn=1)
        ws.send_json({"type": "barge_in", "turn": 1, "seg": 0, "played_ms": 300, "onset_ms": 100})
        wire.until("resume", turn=1, timeout=2)


def test_the_session_cap_closes_with_its_own_code():
    rt = make_runtime(FakeChat([OPENING]), stt=None, tuning=replace(FAST, session_cap_s=1.0))
    with voice_session(rt) as (ws, wire, _):
        over, _ = wire.until("error", code="session_over", timeout=3)
        assert over["message"].strip(), "the cap tells the user why the session ended"
        assert wire.next(timeout=3) == {"type": "_closed", "code": 4410}  # the page doesn't reconnect after it


def test_the_cap_mid_reply_saves_what_was_heard():
    memory = RecordingMemory()
    chat = FakeChat([OPENING, ["First sentence here is long enough to cut. ", "Second one. "]], hang_after={1: 2})
    rt = make_runtime(chat, stt=FakeStt(), memory=memory, tuning=replace(FAST, session_cap_s=1.5))
    with voice_session(rt) as (ws, wire, ready):
        play(ws, wire, turn=1)
        ws.send_json({"type": "user_text", "text": "Give me feedback"})
        wire.until("segment_end", turn=2, seg=1)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        wire.until("error", code="session_over", timeout=3)
        deadline = time.monotonic() + 3
        while not memory.closed and time.monotonic() < deadline:  # the shutdown saves, then closes the session
            time.sleep(0.02)
    assert memory.closed == [ready["session_id"]]
    wren = [t for t in memory.appended if t["speaker"] == "wren"][-1]
    assert wren["text"] == "First sentence here is long enough to cut." and wren.get("interrupted") is True


def test_a_cough_before_the_opening_plays_keeps_it():
    stt = FakeStt()
    rt = make_runtime(FakeChat([OPENING], delay_before={0: 0.3}), stt=stt)
    with voice_session(rt) as (ws, wire, _):
        for _ in range(5):  # 100 ms of noise while the opening is still being prepared
            ws.send_bytes(LOUD)
        send_silence(ws, stt, 0.4)
        segments = play(ws, wire, turn=1)
        assert [s["text"] for s in segments] == ["Hey Maya.", "How's the week going?"]


def test_words_before_the_opening_still_replace_it():
    stt = FakeStt()
    rt = make_runtime(FakeChat([OPENING, ["Sure. Start with your opener."]], hang_before={0}), stt=stt)
    with voice_session(rt) as (ws, wire, _):
        send_silence(ws, stt, 0.2)
        stt.emit(final("Can we practice my Sprout answer?", 0.1, 0.9))
        end, _ = wire.until("reply_end", turn=1)
        assert end["outcome"] == "continued"
        user, _ = wire.until("user_turn", turn=2)
        assert user["text"] == "Can we practice my Sprout answer?"


def test_a_rejected_message_is_logged_without_its_text(caplog):
    with voice_session(make_runtime(FakeChat([OPENING]), stt=None)) as (ws, wire, _):
        play(ws, wire, turn=1)
        with caplog.at_level(logging.INFO, logger="app.voice.session"):
            ws.send_json({"type": "user_text", "text": "my private story " * 200})
            wire.quiet(0.2)
    assert "ignoring a bad client message" in caplog.text and "private story" not in caplog.text


def test_a_second_barge_in_during_the_same_pause_changes_nothing():
    chat = FakeChat([OPENING, ["Lead with the number and then the result."]])
    tuning = replace(FAST, confirm_window_s=0.6, playback_grace_s=5.0)
    session, ws, wire, ready, stt, rt = speaking_session(chat, tuning=tuning)
    try:
        ws.send_json({"type": "user_text", "text": "Feedback please"})
        wire.until("reply_end", turn=2)
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 50, "onset_ms": 100})
        paused_at = time.monotonic()
        wire.quiet(0.4)
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 50, "onset_ms": 100})  # the same pause again
        assert not [m for m in wire.quiet(0.1) if m["type"] == "resume"]  # not taken for a stale one
        wire.until("resume", turn=2, timeout=2)
        assert time.monotonic() - paused_at < 0.9  # the window still runs from the first barge_in
    finally:
        session.__exit__(None, None, None)


def test_a_mock_hands_off_and_the_hold_follows_the_interviewer():
    memory = RecordingMemory()
    chat = FakeChat([OPENING, ["[mock: Sprout]"], ["Walk me through your background."], ["Okay. ", "Why Sprout?"]])
    session, ws, wire, ready, stt, rt = speaking_session(chat, memory=memory, tuning=replace(FAST, hold_mock_s=0.4))
    try:
        ws.send_json({"type": "user_text", "text": "Can we practice for Sprout?"})
        mock, _ = wire.until("mock")
        assert mock["on"] is True and mock["company"] == "Sprout" and mock["title"] == "interviewer"
        segments = play(ws, wire, turn=2)
        assert segments[0]["text"] == "Okay, stepping into role." and "role" not in segments[0]
        assert [s.get("role") for s in segments[1:]] == ["interviewer", "interviewer"]
        send_silence(ws, stt, 1.0)
        stt.emit(final("I studied information management.", 0.2, 0.8))
        metrics, _ = wire.until("metrics", turn=3)
        assert metrics["mode"] == "mock" and metrics["endpoint_ms"] >= 400
        saved = [(r["speaker"], r.get("mock"), r.get("as")) for r in memory.appended]
        assert saved[1:4] == [("user", None, None), ("wren", None, None), ("wren", 1, "interviewer")]  # [0] is the opening
    finally:
        session.__exit__(None, None, None)


def test_a_handoff_cut_before_its_switch_line_is_undone():
    chat = FakeChat([OPENING, ["Sure, let's run the Sprout screen together. [mock]"], ["Walk me through your background."]], hang_before={2})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Can we practice?"})
        wire.until("segment_end", turn=2, seg=2)  # the introduction, in role, is synthesized too
        ws.send_json({"type": "playback", "turn": 2, "seg": 0, "event": "start"})
        ws.send_json({"type": "barge_in", "turn": 2, "seg": 0, "played_ms": 100, "onset_ms": 120})
        wire.quiet(0.1)
        stt.emit(interim("wait, not tonight"))
        wire.until("interrupt", turn=2)
        wire.until("mock", on=False)
        assert rt.registry.get(ready["session_id"]).mock is None
    finally:
        session.__exit__(None, None, None)


def test_a_reconnect_mid_mock_keeps_the_interviewer():
    chat = FakeChat([OPENING, ["[mock]"], ["Walk me through your background."], ["Okay. ", "Why this role?"]])
    runtime = make_runtime(chat, stt=None)
    with voice_session(runtime) as (ws, wire, ready):
        play(ws, wire, turn=1)
        ws.send_json({"type": "user_text", "text": "Let's practice."})
        play(ws, wire, turn=2)
        session_id = ready["session_id"]
    with voice_session(runtime, session_id=session_id) as (ws, wire, ready):
        mock, _ = wire.until("mock")
        assert ready["resumed"] is True and mock["on"] is True
        ws.send_json({"type": "user_text", "text": "I studied information management."})
        metrics, _ = wire.until("metrics")
        assert metrics["mode"] == "mock"
        wire.until("reply_end")
    assert "running a mock interview" in chat.calls[3][0]["content"]


def test_a_step_out_turn_saves_one_turn_per_speaker():
    memory = RecordingMemory()
    chat = FakeChat([
        OPENING, ["[mock: Sprout]"], ["Walk me through your background."],
        ["Thanks, that's all I had. [step out]"], ["You kept it short. ", "How did that feel?"],
    ])
    session, ws, wire, ready, stt, rt = speaking_session(chat, memory=memory)
    try:
        ws.send_json({"type": "user_text", "text": "Can we practice for Sprout?"})
        play(ws, wire, turn=2)
        ws.send_json({"type": "user_text", "text": "I studied information management."})
        segments = play(ws, wire, turn=3)
        assert [s.get("role") for s in segments] == ["interviewer", None, None, None, None]
        saved = [(r["speaker"], r["text"], r.get("mock"), r.get("as")) for r in memory.appended[-3:]]
        assert saved == [
            ("user", "I studied information management.", 1, None),  # the interviewer had the floor
            ("wren", "Thanks, that's all I had.", 1, "interviewer"),
            ("wren", "Stepping out. It's me again. You kept it short. How did that feel?", None, None),
        ]
        assert "metrics" in memory.appended[-2] and "metrics" not in memory.appended[-1]
    finally:
        session.__exit__(None, None, None)


def test_a_pickup_cut_before_its_switch_line_leaves_the_mock_paused():
    chat = FakeChat([
        OPENING, ["[mock: Sprout]"], ["Walk me through your background."], ["Sure, what's up?"], ["[mock]"],
        ["So, your background?"],
    ], hang_before={5})
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Can we practice for Sprout?"})
        play(ws, wire, turn=2)
        ws.send_json({"type": "user_text", "text": "Wren, can I ask you something?"})
        play(ws, wire, turn=3)
        convo = rt.registry.get(ready["session_id"])
        paused = convo.mock
        assert paused.on is False and paused.resumable is True
        ws.send_json({"type": "user_text", "text": "Never mind, let's keep going."})
        picked_up, _ = wire.until("mock", on=True)
        wire.until("segment_end", turn=4, seg=0)  # "Okay, stepping back into role." went out, unheard
        ws.send_json({"type": "user_text", "text": "Wait, one more thing first."})
        wire.until("interrupt", turn=4)
        undone, _ = wire.until("mock")
        assert undone == {**picked_up, "on": False}
        assert convo.mock is paused  # restored as it was, so it can still be picked back up
    finally:
        session.__exit__(None, None, None)


def test_a_handoff_merged_into_their_next_words_is_undone():
    chat = FakeChat([OPENING, ["[mock]"], ["Walk me through your background."], ["Another time, then."]], hang_before={2})
    runtime = make_runtime(chat, stt=None)
    with voice_session(runtime, voice_out=False) as (ws, wire, ready):  # text only: segments count as played at once
        play(ws, wire, turn=1)
        ws.send_json({"type": "user_text", "text": "Can we practice?"})
        wire.until("segment", turn=2, seg=1)  # the introduction: the first question is still coming
        ws.send_json({"type": "user_text", "text": "Actually, not tonight."})
        end, seen = wire.until("reply_end", turn=2)
        assert end["outcome"] == "continued"
        assert [m["on"] for m in seen if m["type"] == "mock"] == [False]
        assert [m["switch"] for m in seen if m["type"] == "metrics"] == ["mock_start:undone"]  # tried, never heard
        merged, _ = wire.until("user_turn", turn=3)
        assert merged["text"] == "Can we practice? Actually, not tonight."
        wire.until("reply_end", turn=3)
        assert runtime.registry.get(ready["session_id"]).mock is None


def test_a_crisis_step_out_stands_even_if_its_line_was_never_heard():
    chat = FakeChat(
        [OPENING, ["[mock: Sprout]"], ["Walk me through your background."], ["I'm here with you."]],
        hang_after={3: 0},  # Wren's answer never starts: only "Stepping out." and "It's me again." go out
    )
    session, ws, wire, ready, stt, rt = speaking_session(chat)
    try:
        ws.send_json({"type": "user_text", "text": "Can we practice for Sprout?"})
        play(ws, wire, turn=2)
        ws.send_json({"type": "user_text", "text": "Honestly I want to end my life."})
        wire.until("segment_end", turn=3, seg=1)  # neither line has started playing
        stt.emit(interim("sorry, ignore that"))
        _, cut = wire.until("interrupt", turn=3)
        stt.emit(final("Sorry, ignore that.", 0.1, 0.9))
        _, after = wire.until("reply_end", turn=4)
        assert not [m for m in cut + after if m["type"] == "mock"]  # the page never hears the interviewer is back
        assert [m["switch"] for m in after if m["type"] == "metrics" and m["turn"] == 3] == ["step_out:crisis"]
        convo = rt.registry.get(ready["session_id"])
        assert convo.mock.on is False and convo.mock.last_step_out == "crisis"
        system = chat.calls[4][0]["content"]  # Wren's prompt, not the interviewer card
        assert system.startswith("You are Wren") and "running a mock interview" not in system
        assert chat.calls[4][-1]["content"] == "Honestly I want to end my life.\nSorry, ignore that."  # Wren sees both
    finally:
        session.__exit__(None, None, None)


def shared_client(runtime):
    """One test client for the socket and POST /turn, so both run on the server's loop with one conversation."""
    app.state.runtime = runtime
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.mark.parametrize("finish_by", ["ticker", "shutdown"])
def test_a_reply_finishing_after_a_post_turn_hand_off_leaves_it_on(finish_by, caplog):
    caplog.set_level(logging.INFO, logger="app.voice.metrics")
    memory = RecordingMemory()
    chat = FakeChat([OPENING, ["Hi there."], ["[mock: Sprout]"], ["Walk me through your background."]])
    rt = make_runtime(chat, memory=memory, tuning=replace(FAST, playback_grace_s=2.0))
    try:
        with shared_client(rt) as client:
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
                ws.send_json({"type": "hello"})
                wire = Wire(ws)
                ready, _ = wire.until("ready")
                play(ws, wire, turn=1)
                ws.send_json({"type": "user_text", "text": "Hi"})
                wire.until("reply_end", turn=2)  # sent, but the page never says it played: it lost the socket
                body = {"text": "Can we practice for Sprout?", "session_id": ready["session_id"]}
                assert client.post("/turn", json=body, headers={"origin": ORIGIN}).status_code == 200
                convo = rt.registry.get(ready["session_id"])
                handed_off = convo.mock
                assert handed_off is not None and handed_off.on
                if finish_by == "ticker":  # turn 2 ends when its audio should have
                    metrics, seen = wire.until("metrics", turn=2, outcome="complete", timeout=8)
                    assert not [m for m in seen if m["type"] == "mock"] and metrics["switch"] is None
            # leaving the socket ends the session, which finishes turn 2 if the ticker hasn't
        deadline = time.monotonic() + 3
        while not memory.closed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert memory.closed == [ready["session_id"]]
        assert convo.mock is handed_off  # still POST /turn's interviewer: turn 2 made no switch to undo
        assert ":undone" not in caplog.text
    finally:
        app.state.runtime = None


def test_a_turn_queued_behind_a_post_turn_hand_off_starts_in_the_mock():
    memory = RecordingMemory()
    chat = FakeChat(
        [OPENING, ["[mock: Sprout]"], ["Walk me through your background."], ["Okay. ", "Why Sprout?"]],
        delay_before={1: 0.5},  # POST /turn holds the conversation while the socket's turn comes in
    )
    rt = make_runtime(chat, memory=memory)
    try:
        with shared_client(rt) as client:
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
                ws.send_json({"type": "hello"})
                wire = Wire(ws)
                ready, _ = wire.until("ready")
                play(ws, wire, turn=1)
                body = {"text": "Can we practice for Sprout?", "session_id": ready["session_id"]}
                post = threading.Thread(target=lambda: client.post("/turn", json=body, headers={"origin": ORIGIN}))
                post.start()
                deadline = time.monotonic() + 3
                while len(chat.calls) < 2 and time.monotonic() < deadline:  # POST /turn is asking Wren
                    time.sleep(0.01)
                ws.send_json({"type": "user_text", "text": "I studied information management."})
                post.join(5)
                segments = play(ws, wire, turn=2)
        assert [s.get("role") for s in segments] == ["interviewer", "interviewer"]
        assert "running a mock interview" in chat.calls[3][0]["content"]
        user, answer = memory.appended[-2:]
        assert (user["text"], user.get("mock")) == ("I studied information management.", 1)  # the interviewer's floor
        assert answer["metrics"]["mode"] == "mock" and answer["metrics"]["switch"] is None
        assert rt.registry.get(ready["session_id"]).mock.on is True
    finally:
        app.state.runtime = None


def test_a_switch_another_turn_has_built_on_stays_after_a_barge_in(caplog):
    caplog.set_level(logging.INFO, logger="app.voice.metrics")
    stt = FakeStt()
    chat = FakeChat([OPENING, ["[mock: Sprout]"], ["Walk me through your background."], ["Okay. ", "Why Sprout?"]])
    rt = make_runtime(chat, stt=stt, tuning=replace(FAST, playback_grace_s=2.0))  # the ticker can't end turn 2 first
    try:
        with shared_client(rt) as client:
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
                ws.send_json({"type": "hello"})
                wire = Wire(ws)
                ready, _ = wire.until("ready")
                play(ws, wire, turn=1)
                ws.send_json({"type": "user_text", "text": "Can we practice for Sprout?"})
                wire.until("reply_end", turn=2)  # the handoff went out, and its switch line hasn't played
                body = {"text": "I studied information management.", "session_id": ready["session_id"]}
                answered = client.post("/turn", json=body, headers={"origin": ORIGIN}).text
                assert '"role": "interviewer"' in answered  # POST /turn answered in that mock and saved its turns
                stt.emit(interim("wait, one more thing"))  # a barge-in on the live socket
                metrics, seen = wire.until("metrics", turn=2, outcome="interrupted")
                assert metrics["switch"] == "mock_start" and not [m for m in seen if m["type"] == "mock"]
        assert rt.registry.get(ready["session_id"]).mock.on is True
        assert ":undone" not in caplog.text
    finally:
        app.state.runtime = None


def test_a_post_turn_in_flight_keeps_the_switch_when_the_socket_closes(caplog):
    caplog.set_level(logging.INFO, logger="app.voice.metrics")
    memory = RecordingMemory()
    # 0 opening, 1 the socket's cue, 2 its first question, 3 the in-flight POST /turn's interviewer, 4 the next one
    chat = FakeChat(
        [OPENING, ["[mock: Sprout]"], ["Walk me through your background."], ["Okay. ", "Why Sprout?"],
         ["Got it. ", "What drew you to analytics?"]],
        delay_before={3: 1.0},
    )
    rt = make_runtime(chat, memory=memory, tuning=replace(FAST, playback_grace_s=5.0))
    answered = {}
    try:
        with shared_client(rt) as client:
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as ws:
                ws.send_json({"type": "hello"})
                wire = Wire(ws)
                ready, _ = wire.until("ready")
                play(ws, wire, turn=1)
                ws.send_json({"type": "user_text", "text": "Can we practice for Sprout?"})
                wire.until("reply_end", turn=2)  # the handoff went out, and its switch line hasn't played
                body = {"text": "I studied information management.", "session_id": ready["session_id"]}
                post = threading.Thread(
                    target=lambda: answered.update(text=client.post("/turn", json=body, headers={"origin": ORIGIN}).text)
                )
                post.start()
                deadline = time.monotonic() + 3
                while len(chat.calls) < 4 and time.monotonic() < deadline:  # POST /turn is waiting on the interviewer
                    time.sleep(0.01)
            deadline = time.monotonic() + 3
            while not memory.closed and time.monotonic() < deadline:
                time.sleep(0.02)
            assert memory.closed and post.is_alive()  # the socket closed while POST /turn was still in flight
            post.join(5)
            assert '"role": "interviewer"' in answered["text"]  # it finished as the interviewer
            body = {"text": "Their analytics team.", "session_id": ready["session_id"]}
            assert client.post("/turn", json=body, headers={"origin": ORIGIN}).status_code == 200
        assert rt.registry.get(ready["session_id"]).mock.on is True
        assert ":undone" not in caplog.text
        assert "running a mock interview" in chat.calls[4][0]["content"]  # the next POST /turn still reaches the interviewer
    finally:
        app.state.runtime = None


def test_a_reconnect_shown_the_interviewer_keeps_it_when_the_old_socket_closes(caplog):
    caplog.set_level(logging.INFO, logger="app.voice.metrics")
    memory = RecordingMemory()
    # 0 opening, 1 the old socket's cue, 2 its first question, 3 the new socket's next reply
    chat = FakeChat([OPENING, ["[mock: Sprout]"], ["Walk me through your background."], ["Okay. ", "Why Sprout?"]])
    rt = make_runtime(chat, memory=memory, tuning=replace(FAST, playback_grace_s=5.0))
    try:
        with shared_client(rt) as client, ExitStack() as old_socket:
            ws = old_socket.enter_context(client.websocket_connect("/ws", headers={"origin": ORIGIN}))
            ws.send_json({"type": "hello"})
            wire = Wire(ws)
            ready, _ = wire.until("ready")
            play(ws, wire, turn=1)
            ws.send_json({"type": "user_text", "text": "Can we practice for Sprout?"})
            wire.until("reply_end", turn=2)  # the handoff went out, and its switch line hasn't played
            with client.websocket_connect("/ws", headers={"origin": ORIGIN}) as new:
                new.send_json({"type": "hello", "session_id": ready["session_id"]})
                wire = Wire(new)
                wire.until("ready")
                shown, _ = wire.until("mock")
                assert shown["on"] is True  # the reconnect is told the interviewer has the floor
                old_socket.close()  # only now does the old socket close
                deadline = time.monotonic() + 3
                while not memory.closed and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert memory.closed == [ready["session_id"]]
                new.send_json({"type": "user_text", "text": "I studied information management."})
                _, seen = wire.until("reply_end")
                assert not [m for m in seen if m["type"] == "mock"]
        assert rt.registry.get(ready["session_id"]).mock.on is True
        assert ":undone" not in caplog.text
        assert "running a mock interview" in chat.calls[3][0]["content"]  # the new socket's reply came from the interviewer
    finally:
        app.state.runtime = None
