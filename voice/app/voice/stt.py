"""Nova-3 streaming speech-to-text over Deepgram's WebSocket.

Mic audio goes up as raw 16 kHz linear16 frames. Deepgram's JSON comes back and is
turned into events.py events here, so nothing past this file reads provider JSON. The
key goes in a server-side Authorization header and never reaches the browser.
"""

import json
from collections.abc import AsyncIterator
from typing import Protocol
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from app.voice.events import SpeechStarted, SttEvent, Transcript, UtteranceEnd
from app.voice.tuning import Tuning

SAMPLE_RATE = 16000


class Stt(Protocol):
    async def send(self, pcm: bytes) -> None: ...

    async def keep_alive(self) -> None: ...

    async def finalize(self) -> None: ...

    def events(self) -> AsyncIterator[SttEvent]: ...

    async def close(self) -> None: ...


def nova_url(model: str, tuning: Tuning) -> str:
    params = {
        "model": model,
        "encoding": "linear16",
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "interim_results": "true",
        "endpointing": tuning.endpointing_ms,
        "utterance_end_ms": tuning.utterance_end_ms,
        "vad_events": "true",
        "smart_format": "true",
        "language": "en",
    }
    return "wss://api.deepgram.com/v1/listen?" + urlencode(params)


def parse_stt(raw: str) -> SttEvent | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    kind = data.get("type") if isinstance(data, dict) else None
    if kind == "SpeechStarted":
        return SpeechStarted(float(data.get("timestamp", 0.0)))
    if kind == "UtteranceEnd":
        return UtteranceEnd(float(data.get("last_word_end", -1)))
    if kind != "Results":
        return None
    alternatives = (data.get("channel") or {}).get("alternatives") or []
    if not alternatives:
        return None
    best = alternatives[0]
    words = best.get("words") or []
    if words:
        start, end = float(words[0]["start"]), float(words[-1]["end"])
    else:
        start = float(data.get("start", 0.0))
        end = start + float(data.get("duration", 0.0))
    return Transcript(
        best.get("transcript", ""),
        bool(data.get("is_final")),
        bool(data.get("speech_final")),
        start,
        end,
        from_finalize=bool(data.get("from_finalize")),
    )


class NovaStt:
    def __init__(self, ws: ClientConnection):
        self._ws = ws

    async def send(self, pcm: bytes) -> None:
        await self._ws.send(pcm)

    async def keep_alive(self) -> None:
        """Deepgram closes the stream after 10 s with neither audio nor this."""
        await self._ws.send(json.dumps({"type": "KeepAlive"}))

    async def finalize(self) -> None:
        """Ask for finals for everything sent so far. They come back with from_finalize set."""
        await self._ws.send(json.dumps({"type": "Finalize"}))

    async def events(self) -> AsyncIterator[SttEvent]:
        """Ends when Deepgram closes the stream; raises if the connection drops."""
        async for message in self._ws:
            if isinstance(message, str) and (event := parse_stt(message)) is not None:
                yield event

    async def close(self) -> None:
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except ConnectionClosed:
            pass
        await self._ws.close()


async def connect_nova(api_key: str, model: str, tuning: Tuning) -> NovaStt:
    ws = await connect(
        nova_url(model, tuning),
        additional_headers={"Authorization": f"Token {api_key}"},
        max_size=None,
        open_timeout=10,
    )
    return NovaStt(ws)
