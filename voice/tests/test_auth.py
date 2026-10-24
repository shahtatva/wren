"""SupabaseAuth against a fake Supabase Auth, served through httpx.MockTransport."""

import asyncio
import base64
import json
from uuid import UUID

import httpx
import pytest

from app.auth import SignInRefused, SupabaseAuth, Tokens
from app.throttle import Throttled

URL, KEY = "https://wren-test.supabase.co", "sb_secret_test"
USER = UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")
START = 1_800_000_000.0  # wall-clock seconds; the tests move the clock by hand

USER_BODY = {  # GET /auth/v1/user answers with UserSchema
    "id": str(USER), "aud": "authenticated", "role": "authenticated", "email": "maya@example.com",
    "email_confirmed_at": "2026-10-16T17:00:00Z", "phone": "", "confirmed_at": "2026-10-16T17:00:00Z",
    "last_sign_in_at": "2026-10-24T09:00:00Z", "app_metadata": {"provider": "email", "providers": ["email"]},
    "user_metadata": {}, "identities": [], "created_at": "2026-10-16T17:00:00Z",
    "updated_at": "2026-10-24T09:00:00Z", "is_anonymous": False,
}
BAD_JWT = {"code": 403, "error_code": "bad_jwt", "msg": "invalid JWT: unable to parse or verify signature, token is expired"}
INVALID_CREDENTIALS = {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"}


def jwt(exp: float, n: int = 0) -> str:
    """An access token's shape. SupabaseAuth reads only exp from it, unverified."""

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    payload = {"sub": str(USER), "role": "authenticated", "session_id": f"session-{n}", "exp": int(exp)}
    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(payload)}.signature-{n}"


def token_body(access: str, refresh: str) -> dict:  # POST /auth/v1/token answers with AccessTokenResponseSchema
    return {
        "access_token": access, "token_type": "bearer", "expires_in": 3600, "expires_at": int(START) + 3600,
        "refresh_token": refresh, "user": USER_BODY,
    }


class FakeSupabase:
    """Answers each request with the next scripted (status, body); the last one repeats."""

    def __init__(self, *answers: tuple[int, dict | None]):
        self.answers, self.requests = list(answers), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return httpx.Response(status) if body is None else httpx.Response(status, json=body)


class Clock:
    def __init__(self):
        self.now = START

    def __call__(self) -> float:
        return self.now


def make_auth(*answers):
    supabase, clock = FakeSupabase(*answers), Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(supabase))
    return SupabaseAuth(URL, KEY, client, clock=clock), supabase, clock


def test_a_valid_token_resolves_to_its_user_id():
    auth, supabase, _ = make_auth((200, USER_BODY))
    token = jwt(START + 3600)
    assert asyncio.run(auth.user_for_token(token)) == USER
    (request,) = supabase.requests
    assert (request.method, str(request.url)) == ("GET", f"{URL}/auth/v1/user")
    assert request.headers["apikey"] == KEY and request.headers["authorization"] == f"Bearer {token}"


def test_an_invalid_token_resolves_to_nobody():
    auth, _, _ = make_auth((401, {"code": 401, "error_code": "no_authorization", "msg": "This endpoint requires a valid Bearer token"}))
    assert asyncio.run(auth.user_for_token(jwt(START + 3600))) is None


def test_a_verified_token_is_cached_for_five_minutes_at_most():
    auth, supabase, clock = make_auth((200, USER_BODY))
    token = jwt(START + 3600)

    async def main():
        assert await auth.user_for_token(token) == USER
        clock.now += 299
        assert await auth.user_for_token(token) == USER
        assert len(supabase.requests) == 1
        clock.now += 2  # 301 s after the first lookup
        assert await auth.user_for_token(token) == USER
        assert len(supabase.requests) == 2

    asyncio.run(main())


def test_the_cache_never_outlives_the_token():
    auth, supabase, clock = make_auth((200, USER_BODY), (403, BAD_JWT))
    token = jwt(START + 60)

    async def main():
        assert await auth.user_for_token(token) == USER
        clock.now += 61
        assert await auth.user_for_token(token) is None  # asked again, and Supabase says it expired

    asyncio.run(main())
    assert len(supabase.requests) == 2


def test_expired_cache_entries_are_evicted():
    auth, _, clock = make_auth((200, USER_BODY), (200, USER_BODY), (403, BAD_JWT), (403, BAD_JWT))

    async def main():
        await auth.user_for_token(jwt(START + 60, 1))
        await auth.user_for_token(jwt(START + 3600, 2))
        await auth.user_for_token("junk-1")
        clock.now += 301  # both verified answers and the refusal are stale now
        await auth.user_for_token("junk-2")

    asyncio.run(main())
    # The cache stays small: an insert drops what has expired. Only junk-2's refusal is left.
    assert (len(auth._verified), len(auth._refused)) == (0, 1)


def test_a_refused_token_is_remembered_for_a_minute():
    auth, supabase, clock = make_auth((403, BAD_JWT))

    async def main():
        assert await auth.user_for_token("junk") is None
        assert await auth.user_for_token("junk") is None
        assert len(supabase.requests) == 1
        clock.now += 61
        assert await auth.user_for_token("junk") is None
        assert len(supabase.requests) == 2

    asyncio.run(main())


def test_lookups_over_the_cap_are_refused_without_calling_supabase():
    auth, supabase, clock = make_auth((200, USER_BODY))

    async def main():
        for n in range(25):
            assert await auth.user_for_token(jwt(START + 3600, n)) == USER
        with pytest.raises(Throttled):
            await auth.user_for_token(jwt(START + 3600, 25))
        assert len(supabase.requests) == 25
        assert await auth.user_for_token(jwt(START + 3600, 0)) == USER  # a cached answer doesn't count
        clock.now += 301  # the 5-minute window has moved on
        assert await auth.user_for_token(jwt(START + 3600, 25)) == USER
        assert len(supabase.requests) == 26

    asyncio.run(main())


def test_sign_in_sends_the_password_grant_and_returns_tokens():
    auth, supabase, _ = make_auth((200, token_body("access-1", "refresh-1")))
    assert asyncio.run(auth.sign_in("maya@example.com", "correct horse")) == Tokens("access-1", "refresh-1", 3600)
    (request,) = supabase.requests
    assert (request.method, str(request.url)) == ("POST", f"{URL}/auth/v1/token?grant_type=password")
    assert json.loads(request.content) == {"email": "maya@example.com", "password": "correct horse"}
    assert request.headers["apikey"] == KEY and "authorization" not in request.headers


def test_a_wrong_password_raises_sign_in_refused():
    auth, _, _ = make_auth((400, INVALID_CREDENTIALS))
    with pytest.raises(SignInRefused):
        asyncio.run(auth.sign_in("maya@example.com", "wrong"))


def test_refresh_sends_the_refresh_grant():
    auth, supabase, _ = make_auth((200, token_body("access-2", "refresh-2")))
    assert asyncio.run(auth.refresh("refresh-1")) == Tokens("access-2", "refresh-2", 3600)
    (request,) = supabase.requests
    assert (request.method, str(request.url)) == ("POST", f"{URL}/auth/v1/token?grant_type=refresh_token")
    assert json.loads(request.content) == {"refresh_token": "refresh-1"}


def test_a_spent_refresh_token_raises_sign_in_refused():
    auth, _, _ = make_auth((400, {"code": 400, "error_code": "refresh_token_already_used", "msg": "Invalid Refresh Token: Already Used"}))
    with pytest.raises(SignInRefused):
        asyncio.run(auth.refresh("refresh-1"))


def test_sign_out_ends_only_this_session_and_forgets_the_token():
    auth, supabase, _ = make_auth((200, USER_BODY), (204, None), (403, BAD_JWT))
    token = jwt(START + 3600)

    async def main():
        assert await auth.user_for_token(token) == USER
        await auth.sign_out(token)
        assert await auth.user_for_token(token) is None  # asked again, not answered from the cache

    asyncio.run(main())
    logout = supabase.requests[1]
    assert (logout.method, str(logout.url)) == ("POST", f"{URL}/auth/v1/logout?scope=local")
    assert logout.headers["authorization"] == f"Bearer {token}" and logout.headers["apikey"] == KEY
    assert len(supabase.requests) == 3


def test_supabase_is_reached_only_over_https():
    with pytest.raises(ValueError):
        SupabaseAuth("http://wren-test.supabase.co", KEY)


def test_supabase_rate_limiting_a_lookup_counts_as_throttled():
    auth, _, _ = make_auth((429, {"code": 429, "error_code": "over_request_rate_limit", "msg": "Request rate limit reached"}))
    with pytest.raises(Throttled):
        asyncio.run(auth.user_for_token(jwt(START + 3600)))
