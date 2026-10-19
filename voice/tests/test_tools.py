import json

import pytest

from app.tools.bench import frames_of, room_noise
from app.tools.ttfa_report import load_records, percentile, summarize
from app.voice.vad import rms


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
