import asyncio
import json
import sys
import types

import pytest

from app.conversation import Conversation
from app.mock import interviewer_name, step_out_reason
from app.tools.bench import frames_of, room_noise
from app.tools.samples import RAMBLE, SCENARIOS, SampleReply, Scenario, check, maya, run_scenario
from app.tools.samples import summarize as summarize_replies
from app.tools.ttfa_report import load_records, percentile, summarize
from app.turn import INTERVIEW_START
from app.voice.tuning import Tuning
from app.voice.vad import rms
from tests.voice_fakes import USER, FakeChat


def test_percentile_is_nearest_rank():
    values = [900, 700, 1200, 800, 1000]
    assert percentile(values, 50) == 900
    assert percentile(values, 95) == 1200
    assert percentile([], 50) is None


def test_summary_groups_voice_turns_by_mode_and_skips_turns_without_audio():
    records = [
        {"mode": "checkin", "input": "voice", "ttfa_ms": 900, "endpoint_ms": 350, "retrieval_ms": 200, "llm_ttft_ms": 190, "tts_ttfb_ms": 110},
        {"mode": "checkin", "input": "voice", "ttfa_ms": 1300, "endpoint_ms": 380, "retrieval_ms": 260, "llm_ttft_ms": 400, "tts_ttfb_ms": 120},
        {"mode": "mock", "input": "voice", "ttfa_ms": 1700, "endpoint_ms": 1050, "retrieval_ms": 210, "llm_ttft_ms": 200, "tts_ttfb_ms": 100},
        {"mode": "checkin", "input": "text", "ttfa_ms": None, "turn_to_audio_ms": 600},
        {"mode": "checkin", "input": "opening", "ttfa_ms": None},
    ]
    rows = {row["group"]: row for row in summarize(records)}
    assert set(rows) == {"checkin voice", "mock voice"}
    assert rows["checkin voice"]["turns"] == 2
    assert rows["checkin voice"]["ttfa_p50"] == 900 and rows["checkin voice"]["ttfa_p95"] == 1300
    assert rows["mock voice"]["endpoint_p50"] == 1050


def test_load_records_reads_json_lines_and_skips_bad_ones(tmp_path):
    path = tmp_path / "turns.jsonl"
    path.write_text(json.dumps({"ttfa_ms": 800}) + "\nnot json\n\n" + json.dumps({"ttfa_ms": 900}) + "\n", encoding="utf-8")
    assert [r["ttfa_ms"] for r in load_records(path)] == [800, 900]
    assert load_records(tmp_path / "missing.jsonl") == []


def test_bench_frames_are_20ms_and_pad_the_tail():
    frames = frames_of(b"\x01\x00" * 500)  # 1000 bytes: one full frame and a padded tail
    assert [len(f) for f in frames] == [640, 640]
    assert frames[1][360:] == b"\x00" * 280


def test_room_noise_is_quiet_but_not_digital_silence():
    level = rms(room_noise())
    assert 5 < level < 60


def test_wav_bytes_wraps_pcm_with_a_header():
    import io
    import wave

    from app.tools.voices import wav_bytes

    data = wav_bytes(b"\x01\x00\x02\x00", 24000)
    with wave.open(io.BytesIO(data)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (1, 2, 24000, 2)


def test_leaked_words_counts_what_nova_heard():
    from app.tools.echo_spike import leaked_words

    assert leaked_words("") == 0
    assert leaked_words("tonight run your opener") == 4


def test_leak_check_finds_a_secret_in_a_bundle_without_printing_it(tmp_path, capsys):
    from app.tools.leak_check import scan

    (tmp_path / "chunks").mkdir()
    (tmp_path / "chunks" / "a.js").write_text("const k='sk-live-123456';", encoding="utf-8")
    (tmp_path / "b.css").write_text("body{}", encoding="utf-8")
    leaks = scan(tmp_path, {"GROQ_API_KEY": "sk-live-123456", "GEMINI_API_KEY": "AIza-abcdef"})
    assert leaks == {"GROQ_API_KEY": ["chunks/a.js"]}


def test_leak_check_ignores_short_or_empty_values(tmp_path):
    from app.tools.leak_check import scan

    (tmp_path / "a.js").write_text("x = 1", encoding="utf-8")
    assert scan(tmp_path, {"EMPTY": "", "SHORT": "1"}) == {}


def test_leak_check_scans_server_pages_but_not_the_build_cache(tmp_path):
    from app.tools.leak_check import scan

    (tmp_path / "server" / "app").mkdir(parents=True)
    (tmp_path / "server" / "app" / "talk.html").write_text("key=sk-live-123456")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "x.bin").write_text("sk-live-123456")
    leaks = scan(tmp_path, {"GROQ_API_KEY": "sk-live-123456"}, skip=("cache",))
    assert leaks == {"GROQ_API_KEY": ["server/app/talk.html"]}


def test_leak_check_main_fails_on_a_key_in_a_prerendered_page(tmp_path, monkeypatch, capsys):
    from app.tools import leak_check

    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "a.js").write_text("x = 1", encoding="utf-8")
    (tmp_path / "server" / "app").mkdir(parents=True)
    (tmp_path / "server" / "app" / "talk.rsc").write_text("sk-live-123456", encoding="utf-8")
    monkeypatch.setattr(leak_check, "BUILD", tmp_path)
    monkeypatch.setattr(leak_check, "dotenv_values", lambda path: {"GROQ_API_KEY": "sk-live-123456"})
    assert leak_check.main() == 1
    out = capsys.readouterr().out
    assert "FAIL  GROQ_API_KEY found in server/app/talk.rsc" in out and "sk-live-123456" not in out


def test_leak_check_main_says_how_much_it_scanned(tmp_path, monkeypatch, capsys):
    from app.tools import leak_check

    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "a.js").write_text("x = 1", encoding="utf-8")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "b.bin").write_text("y", encoding="utf-8")
    monkeypatch.setattr(leak_check, "BUILD", tmp_path)
    monkeypatch.setattr(leak_check, "dotenv_values", lambda path: {"GROQ_API_KEY": "sk-live-123456"})
    assert leak_check.main() == 0
    out = capsys.readouterr().out
    assert "scanned 1 file in" in out and str(tmp_path) in out  # a scan of nothing would pass every key


def test_check_flags_what_reads_badly_aloud():
    assert set(check("Great question! You did 3 things. Want to try? Or not?")) >= {"filler", "digits", "two questions"}
    assert check("Lead with the result. Want to try it again?") == []
    assert "long" in check("One. Two. Three. Four.")
    assert "long" not in check("One. Two. Three. Four. Five. Six.", debrief=True)
    assert "question not last" in check("Ready? Let's go.")
    assert "clinical" in check("That sounds like anxiety.")
    assert "praise in role" in check("Great answer. Why Sprout?", in_role=True)
    assert "praise in role" not in check("Great answer. Why Sprout?")


def test_check_flags_brackets_and_three_sentences_that_run_long():
    assert "brackets" in check("Sure. [mock: Sprout]")
    assert "brackets" in check("Try the STAR shape (situation first). Ready?")
    three_long = " ".join([" ".join(["word"] * 16) + "."] * 3)  # three sentences, forty-eight words
    assert "long" in check(three_long)
    assert "long" not in check(three_long, debrief=True)
    assert "long" not in check(" ".join([" ".join(["word"] * 14) + "."] * 3))  # forty-two words


def test_check_flags_praise_words_in_role_but_not_a_greeting():
    for reply in ("That's a solid result. Why Sprout?", "Great, Maya. Walk me through your background?",
                  "Nice. What drew you to sales?", "That's strong. Why now?", "Impressive. What came next?"):
        assert "praise in role" in check(reply, in_role=True), reply
        assert "praise in role" not in check(reply), reply
    assert "praise in role" not in check("Nice to meet you, Maya. Walk me through your background?", in_role=True)


def test_a_reply_that_hit_the_token_cap_says_so_even_when_it_ends_on_a_sentence():
    async def chat(messages, timing, *, retry=False, max_tokens=None):
        timing.model, timing.finish_reason = "fake-chat", "length"
        yield "Your opener landed."

    [reply] = asyncio.run(run_scenario(Scenario("cap", "checkin", maya, ("Hi.",)), chat, Tuning()))
    assert (reply.text, reply.repair, reply.finish) == ("Your opener landed.", None, "length")
    assert summarize_replies([reply])["hit_cap"] == 1


def test_a_scenario_runs_through_run_turn():
    scenario = Scenario("hi", "checkin", lambda: Conversation("s", USER, "checkin", "UTC"), ("Hi there.",))
    replies = asyncio.run(run_scenario(scenario, FakeChat([["Hey. ", "How's the search going?"]]), Tuning()))
    assert [(r.text, r.words, r.flags) for r in replies] == [("Hey. How's the search going?", 5, [])]
    assert replies[0].model == "fake-chat" and replies[0].switch is None
    assert replies[0].parts == (("Wren", "Hey. How's the search going?"),)
    assert summarize_replies(replies)["median_words"] == 5


def test_summarize_reports_length_and_what_was_flagged():
    def reply(text):
        return SampleReply("s", None, text, len(text.split()), check(text))

    replies = [reply("Hey. How's the search going?"), reply("Want to try? Or not?"), reply("You did 3 things.")]
    summary = summarize_replies(replies)
    assert (summary["replies"], summary["median_words"], summary["max_words"]) == (3, 5, 5)
    assert (summary["two_questions"], summary["flagged"]) == (1, 2)


def test_summarize_counts_the_switches_seen_and_replies_that_gave_up():
    def reply(switch=None, repair=None):
        return SampleReply("s", "Hi.", "Okay.", 1, [], switch=switch, repair=repair)

    summary = summarize_replies([reply("mock_start"), reply(), reply("mock_start"), reply("step_out:done", "gave_up")])
    assert summary["switches"] == {"mock_start": 2, "step_out:done": 1}
    assert summary["gave_up"] == 1
    assert summarize_replies([reply()])["switches"] == {}


def test_a_mock_keeps_its_turns_as_post_turn_does_and_each_speaker_is_checked_on_their_own_words():
    debrief = (
        "Your opener was clear. The donor project landed. You never said what you did in it. "
        "Lead with your part. A friend could run the next one. How did that feel?"
    )
    chat = FakeChat([
        ["[mock: Sprout Social]"],
        ["Thanks for making time. ", "Walk me through your background?"],
        ["Great answer. ", "Why Sprout?"],
        [debrief],
    ])
    lines = ("Can we practice for Sprout?", "I led the donor project.", "Okay, let's stop there.")
    scenario = Scenario("mock", "mock", maya, lines)
    first, answer, stop = asyncio.run(run_scenario(scenario, chat, Tuning()))
    name = interviewer_name("samples", 1)
    question = f"I'm {name}, the recruiter at Sprout Social. Thanks for making time. Walk me through your background?"
    assert first.parts == (("Wren", "Okay, stepping into role."), (name, question))
    assert (first.switch, first.cues, first.flags) == ("mock_start", ("[mock: Sprout Social]",), [])
    # The interviewer's words are checked in role, so its praise is flagged.
    assert answer.parts == ((name, "Great answer. Why Sprout?"),) and answer.flags == ["praise in role"]
    # A stop debriefs: six sentences are a debrief's to use, and the scripted "Stepping out. It's me again." don't count.
    assert stop.parts == (("Wren", f"Stepping out. It's me again. {debrief}"),)
    assert (stop.switch, stop.flags) == ("step_out:stop", [])
    # The interviewer saw its own question, and the debrief saw the whole mock, with the step-out budget.
    assert [m["content"] for m in chat.calls[2][1:]] == [INTERVIEW_START, question, "I led the donor project."]
    assert chat.max_tokens == [None, None, None, Tuning().max_step_out_tokens]
    mock = chat.calls[3][0]["content"]
    assert f"Candidate: I led the donor project.\n{name}: Great answer. Why Sprout?\nCandidate: Okay, let's stop there." in mock


def test_a_turn_names_every_model_its_calls_used():
    """The turn's timing keeps only the last call's model, which would hide a fallback that wrote the cue."""

    async def chat(messages, timing, *, retry=False):
        timing.model = "first" if len(called) == 0 else "fallback"
        called.append(timing.model)
        yield ["[mock]", "Tell me about yourself."][len(called) - 1]

    called = []
    scenario = Scenario("start", "mock", maya, ("Let's practice.",))
    [reply] = asyncio.run(run_scenario(scenario, chat, Tuning()))
    assert reply.model == "first + fallback" and reply.cues == ("[mock]",)


def test_the_checkin_scenarios_set_one_scene_and_the_ramble_comes_with_its_question():
    by_name = {s.name: s for s in SCENARIOS if s.group == "checkin"}
    assert list(by_name) == ["opening", "nervous", "ramble", "memory", "person", "crisis"]
    assert 100 <= len(RAMBLE.split()) <= 140  # about 120 words
    chat = FakeChat([["Okay."]])
    asyncio.run(run_scenario(by_name["ramble"], chat, Tuning()))
    system = chat.calls[0][0]["content"]
    for scene in ("Maya", "MS in Information Management", "Sprout Social", "(tomorrow) at 11:00 AM", "2 days since your last session", "[story]"):
        assert scene in system, scene
    # One message, so Wren can't credit a retry that never happened.
    assert len(chat.calls) == 1 and chat.calls[0][-1]["content"] == f"{RAMBLE} How was that?"


def test_the_mock_scenarios_reach_the_moments_they_test():
    by_name = {s.name: s for s in SCENARIOS}
    assert len(by_name) == len(SCENARIOS)  # --scenario picks them by name
    assert [s.name for s in SCENARIOS if s.group == "mock"] == [
        "start", "yes-to-offer", "no-start", "ai-in-role", "crisis-in-role", "flirt-in-role", "stop-mid", "full-mock",
        "pickup-ai", "pickup-pause", "pickup-cue", "pickup-crisis", "after-crisis",
    ]
    assert by_name["yes-to-offer"].setup().turns[-1].text == "Sprout's tomorrow. Want to run the screen once tonight?"
    # In role, the code steps out for the AI question, the crisis and the stop. Flirting is the interviewer's to catch.
    in_role = ("ai-in-role", "crisis-in-role", "flirt-in-role", "stop-mid")
    assert [step_out_reason(by_name[name].lines[0]) for name in in_role] == ["ai", "crisis", None, "stop"]
    for name in in_role:
        assert by_name[name].setup().mock.on, name
    stop = by_name["stop-mid"].setup()
    assert [t.speaker for t in stop.turns if t.mock == 1] == ["wren", "user", "wren", "user", "wren"]  # two answers
    # The full mock: four answers, none of which steps out, and one rambles past 150 words.
    full = by_name["full-mock"].lines
    assert len(full) == 5 and [step_out_reason(line) for line in full[1:]] == [None] * 4
    assert max(len(line.split()) for line in full) > 150
    # Each pickup starts from a paused mock that Wren stepped out of for its reason, and can pick back up.
    paused = {"pickup-ai": "ai", "pickup-pause": "pause", "pickup-cue": "cue"}
    for name, why in (*paused.items(), ("pickup-crisis", "crisis"), ("after-crisis", "crisis")):
        convo = by_name[name].setup()
        assert (convo.mock.on, convo.mock.resumable, convo.mock.last_step_out) == (False, True, why), name
        said = convo.turns[-2]
        assert said.speaker == "user" and said.mock == 1 and step_out_reason(said.text) == (None if why == "cue" else why)
        assert convo.turns[-1].text.startswith("Stepping out. It's me again. ") and convo.turns[-1].mock is None


def test_the_command_line_plays_the_chosen_scenarios_with_its_pause(monkeypatch, capsys):
    from app import config
    from app.tools import samples

    made, slept, chat = [], [], FakeChat([])  # every call says "Okay."

    def make_chat(settings, voice, *, max_tokens):
        made.append((settings, voice, max_tokens))
        return chat

    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        if seconds:
            slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setitem(sys.modules, "app.chat", types.SimpleNamespace(make_chat=make_chat))
    monkeypatch.setattr(config, "load_settings", lambda: "settings")
    monkeypatch.setattr(config, "load_voice_settings", lambda: "voice")
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    def convo():
        return Conversation("s", USER, "checkin", "UTC")

    monkeypatch.setattr(samples, "SCENARIOS", (
        Scenario("two", "x", convo, ("One.", "Two.")),
        Scenario("other", "x", convo, ("Hi.",)),
        Scenario("y", "y", convo, ("Hey.",)),
    ))
    assert samples.main(["--group", "x", "--scenario", "two", "--scenario", "y", "--runs", "2", "--pause", "12"]) == 0
    assert made == [("settings", "voice", Tuning().max_reply_tokens)]
    assert slept == [12.0, 12.0, 12.0]  # within the first play, between the plays, and within the second
    out = capsys.readouterr().out
    assert "x/two, run 2 of 2" in out and "other" not in out and "y/y" not in out
    assert "4 replies." in out and "Gave up: 0 of 4." in out and "Switches: none" in out and "Models: fake-chat x4" in out
    assert "Hit the token cap: 0 of 4." in out
    with pytest.raises(SystemExit):
        samples.main(["--group", "y", "--scenario", "two"])  # the filters leave nothing to play


def test_the_memory_scenario_has_no_notes_and_the_opening_has_no_user_line():
    by_name = {s.name: s for s in SCENARIOS if s.group == "checkin"}
    chat = FakeChat([["I don't have that."]])
    memory = asyncio.run(run_scenario(by_name["memory"], chat, Tuning()))
    assert memory[0].line == "Do you remember what I told you about my old manager?"
    assert "Nothing relevant yet." in chat.calls[0][0]["content"]
    chat = FakeChat([["Hey Maya. Sprout's tomorrow. How are you feeling?"]])
    opening = asyncio.run(run_scenario(by_name["opening"], chat, Tuning()))
    assert opening[0].line is None and "session is just starting" in chat.calls[0][0]["content"]


def test_a_pause_waits_between_turns_but_not_before_the_first(monkeypatch):
    slept = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        if seconds:
            slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    scenario = Scenario("two", "checkin", lambda: Conversation("s", USER, "checkin", "UTC"), ("One.", "Two."))
    asyncio.run(run_scenario(scenario, FakeChat([["Hi."], ["Hi."]]), Tuning(), pause_s=6.0))
    assert slept == [6.0]
