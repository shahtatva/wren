import groq
import httpx
import pytest
from google.genai import errors

from evals.retry import with_retries

REQUEST = httpx.Request("POST", "https://api.example.test/v1")


def _gemini_error(cls, code, status):
    return cls(code, {"error": {"code": code, "message": "test", "status": status}})


def _groq_error(cls, code):
    return cls("test", response=httpx.Response(code, request=REQUEST), body=None)


def _failing_then(value, failures):
    failures = list(failures)

    def call():
        if failures:
            raise failures.pop(0)
        return value

    return call


def test_busy_and_rate_limited_gemini_calls_are_retried_after_a_wait():
    call = _failing_then("ok", [_gemini_error(errors.ServerError, 503, "UNAVAILABLE"),
                                _gemini_error(errors.ClientError, 429, "RESOURCE_EXHAUSTED")])
    waited = []
    assert with_retries(call, waits=(30, 60, 120), sleep=waited.append) == "ok"
    assert waited == [30, 60]


def test_rate_limited_and_timed_out_groq_calls_are_retried_after_a_wait():
    call = _failing_then("ok", [_groq_error(groq.RateLimitError, 429), groq.APITimeoutError(request=REQUEST)])
    waited = []
    assert with_retries(call, waits=(30, 60, 120), sleep=waited.append) == "ok"
    assert waited == [30, 60]


def test_dropped_connections_are_retried_after_a_wait():
    call = _failing_then("ok", [httpx.ReadError("connection aborted", request=REQUEST)])
    waited = []
    assert with_retries(call, waits=(30,), sleep=waited.append) == "ok"
    assert waited == [30]


def test_other_errors_are_not_retried():
    for error in (_gemini_error(errors.ClientError, 400, "INVALID_ARGUMENT"), _groq_error(groq.BadRequestError, 400)):
        waited = []
        with pytest.raises(type(error)):
            with_retries(_failing_then("ok", [error]), waits=(30,), sleep=waited.append)
        assert waited == []


def test_gives_up_after_the_last_wait():
    busy = [_gemini_error(errors.ServerError, 503, "UNAVAILABLE") for _ in range(3)]
    waited = []
    with pytest.raises(errors.ServerError):
        with_retries(_failing_then("ok", busy), waits=(30, 60), sleep=waited.append)
    assert waited == [30, 60]
