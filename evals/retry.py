"""Retries for the harness's free-tier API calls (Gemini and Groq). Never used on the live path.

Free tiers shed load with 503 and 429, and spikes usually pass within minutes. The SDKs'
own retries give up within seconds, and the live-path Groq client never retries at all.
"""

import time
from collections.abc import Callable
from typing import TypeVar

import groq
import httpx
from google.genai import errors as genai_errors

WAITS_S = (30, 60, 120)

T = TypeVar("T")


def _transient(error: Exception) -> bool:
    if isinstance(error, genai_errors.ServerError):
        return True
    if isinstance(error, genai_errors.ClientError):
        return error.code == 429
    # The Gemini SDK lets dropped connections through as raw httpx errors.
    return isinstance(error, (groq.RateLimitError, groq.InternalServerError, groq.APIConnectionError, httpx.TransportError))


def with_retries(call: Callable[[], T], waits=WAITS_S, sleep=time.sleep) -> T:
    """Retry through busy (503), rate-limit (429), timeout and connection errors. Others raise at once."""
    for wait in waits:
        try:
            return call()
        except Exception as e:
            if not _transient(e):
                raise
            print(f"  {type(e).__name__}; retrying in {wait}s", flush=True)
            sleep(wait)
    return call()
