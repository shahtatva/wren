"""Aura-2 text-to-speech over Deepgram's REST API: one streamed request per segment.

The WebSocket API accepts only 20 Flush messages a minute, and Wren flushes once per
sentence, so a lively session would hit that and fall silent. Per-segment requests have
no such limit, map one-to-one onto segments (which the barge-in accounting needs), and
cancel cleanly. Measured 2026-10-16: a warm request reaches its first byte in about
95 ms, the same as the WebSocket. A cold connection adds about 180 ms of TLS handshake,
so the session calls warm() when a turn commits, while retrieval and the LLM run.
"""

import logging
from collections.abc import AsyncIterator
from typing import Protocol
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

API = "https://api.deepgram.com"
SAMPLE_RATE = 24000


class TtsError(RuntimeError):
    pass


class Tts(Protocol):
    async def warm(self) -> None: ...

    def stream(self, text: str) -> AsyncIterator[bytes]: ...

    async def close(self) -> None: ...


def aura_url(model: str) -> str:
    params = {"model": model, "encoding": "linear16", "sample_rate": SAMPLE_RATE, "container": "none"}
    return f"{API}/v1/speak?" + urlencode(params)


class AuraTts:
    def __init__(self, api_key: str, model: str, *, client: httpx.AsyncClient | None = None):
        self._url = aura_url(model)
        self._headers = {"Authorization": f"Token {api_key}"}
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0), limits=httpx.Limits(keepalive_expiry=60)
        )

    async def warm(self) -> None:
        """Open or refresh the connection to Deepgram without synthesizing anything."""
        try:
            await self._client.get(f"{API}/v1/projects", headers=self._headers)
        except httpx.HTTPError as e:
            logger.info("tts warm-up failed: %s: %s", type(e).__name__, e)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """The segment's raw PCM as it's generated, in chunks of whole 16-bit samples."""
        async with self._client.stream("POST", self._url, headers=self._headers, json={"text": text}) as response:
            if response.status_code != 200:
                body = (await response.aread())[:200]
                raise TtsError(f"Aura-2 returned {response.status_code}: {body!r}")
            carry = b""
            async for chunk in response.aiter_bytes():
                data = carry + chunk
                cut = len(data) - len(data) % 2
                carry = data[cut:]
                if cut:
                    yield data[:cut]

    async def close(self) -> None:
        await self._client.aclose()
