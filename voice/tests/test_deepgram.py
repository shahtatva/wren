import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.voice.events import SpeechStarted, Transcript, UtteranceEnd
from app.voice.stt import NovaStt, nova_url, parse_stt
from app.voice.tts import AuraTts, TtsError, aura_url
from app.voice.tuning import Tuning


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


def test_nova_url_has_endpointing_and_vad_events():
    url = nova_url("nova-3", Tuning(endpointing_ms=300, utterance_end_ms=1000))
    assert url.startswith("wss://api.deepgram.com/v1/listen?")
    assert query(url) == {
        "model": "nova-3", "encoding": "linear16", "sample_rate": "16000", "channels": "1",
        "interim_results": "true", "endpointing": "300", "utterance_end_ms": "1000",
        "vad_events": "true", "smart_format": "true", "language": "en",
    }


def results(transcript: str, words: list[tuple[float, float]], is_final=True, speech_final=False) -> str:
    return json.dumps({
        "type": "Results", "start": 1.0, "duration": 2.5, "is_final": is_final, "speech_final": speech_final,
        "channel": {"alternatives": [{
            "transcript": transcript,
            "words": [{"word": "w", "start": s, "end": e} for s, e in words],
        }]},
    })


def test_parse_results_uses_last_word_end():
    event = parse_stt(results("Sprout moved my screen.", [(1.1, 1.4), (1.5, 2.9)], speech_final=True))
    assert event == Transcript("Sprout moved my screen.", True, True, 1.1, 2.9)


def test_parse_marks_results_that_came_from_finalize():
    raw = json.loads(results("to Monday.", [(1.3, 2.1)]))
    raw["from_finalize"] = True
    assert parse_stt(json.dumps(raw)) == Transcript("to Monday.", True, False, 1.3, 2.1, from_finalize=True)


def test_finalize_and_keep_alive_send_control_messages():
    class FakeSocket:
        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

    socket = FakeSocket()
    stt = NovaStt(socket)
    asyncio.run(stt.finalize())
    asyncio.run(stt.keep_alive())
    assert [json.loads(m) for m in socket.sent] == [{"type": "Finalize"}, {"type": "KeepAlive"}]


def test_parse_results_without_words_falls_back_to_the_window():
    assert parse_stt(results("", [], is_final=True)) == Transcript("", True, False, 1.0, 3.5)


def test_parse_speech_started_and_utterance_end():
    assert parse_stt('{"type": "SpeechStarted", "channel": [0, 1], "timestamp": 9.54}') == SpeechStarted(9.54)
    assert parse_stt('{"type": "UtteranceEnd", "channel": [0, 1], "last_word_end": 2.395}') == UtteranceEnd(2.395)


@pytest.mark.parametrize("raw", ['{"type": "Metadata", "request_id": "x"}', '{"type": "Results", "channel": {"alternatives": []}}', "nonsense"])
def test_parse_ignores_what_it_does_not_need(raw):
    assert parse_stt(raw) is None


def test_aura_url_asks_for_raw_24k_pcm():
    url = aura_url("aura-2-vesta-en")
    assert url.startswith("https://api.deepgram.com/v1/speak?")
    assert query(url) == {"model": "aura-2-vesta-en", "encoding": "linear16", "sample_rate": "24000", "container": "none"}


def tts_with(handler) -> AuraTts:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AuraTts("dg-key", "aura-2-vesta-en", client=client)


def test_stream_yields_whole_samples_in_order():
    body = bytes(range(256)) * 9  # 2304 bytes

    async def split_body():  # odd sizes split samples across chunks
        offset = 0
        for size in (3, 1, 500, 0, 7, 1793):
            yield body[offset : offset + size]
            offset += size

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["text"] = json.loads(request.content)["text"]
        return httpx.Response(200, content=split_body())

    async def run():
        tts = tts_with(handler)
        out = [chunk async for chunk in tts.stream("Lead with the number.")]
        await tts.close()
        return out

    out = asyncio.run(run())
    assert all(len(chunk) % 2 == 0 and chunk for chunk in out)
    assert b"".join(out) == body
    assert seen == {"auth": "Token dg-key", "text": "Lead with the number."}


def test_stream_raises_on_http_error():
    async def run():
        tts = tts_with(lambda request: httpx.Response(401, json={"err_msg": "bad key"}))
        try:
            return [chunk async for chunk in tts.stream("Hi.")]
        finally:
            await tts.close()

    with pytest.raises(TtsError, match="401"):
        asyncio.run(run())


@pytest.mark.parametrize("interviewer, in_role, model", [
    ("aura-2-orion-en", True, "aura-2-orion-en"), ("aura-2-orion-en", False, "aura-2-vesta-en"), (None, True, "aura-2-vesta-en"),
])
def test_role_segments_use_the_interviewer_voice(interviewer, in_role, model):
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(query(str(request.url))["model"])
        return httpx.Response(200, content=b"\x00\x00")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tts = AuraTts("dg-key", "aura-2-vesta-en", interviewer_model=interviewer, client=client)

    async def speak():
        return [pcm async for pcm in tts.stream("Why Sprout?", in_role=in_role)]

    asyncio.run(speak())
    assert asked == [model]


def test_warm_opens_a_connection_without_speaking():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"projects": []})

    async def run():
        tts = tts_with(handler)
        await tts.warm()
        await tts.close()

    asyncio.run(run())
    assert paths == ["/v1/projects"]
