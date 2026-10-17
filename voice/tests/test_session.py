import time
from datetime import datetime

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.voice.events import SpeechStarted, Transcript
from tests.voice_fakes import (
    ORIGIN,
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
