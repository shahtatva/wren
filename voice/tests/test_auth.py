"""SupabaseAuth against a fake Supabase Auth, served through httpx.MockTransport."""

import asyncio
import json

import pytest

from app.auth import SignInRefused, SupabaseAuth, Tokens
from app.throttle import Throttled
from tests.supabase_fakes import (
    BAD_JWT, INVALID_CREDENTIALS, KEY, RATE_LIMITED, SPENT_REFRESH, START, URL, USER, USER_BODY, Clock, FakeSupabase,
    client_for, jwt, token_body,
)


def make_auth(*answers, url=URL):
    supabase, clock = FakeSupabase(*answers), Clock()
    return SupabaseAuth(url, KEY, client_for(supabase), clock=clock), supabase, clock


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
    auth, _, _ = make_auth((400, SPENT_REFRESH))
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
    auth, _, _ = make_auth((429, RATE_LIMITED))
    with pytest.raises(Throttled):
        asyncio.run(auth.user_for_token(jwt(START + 3600)))


def test_a_project_url_with_a_rest_path_still_reaches_auth():
    auth, supabase, _ = make_auth((200, USER_BODY), url=f"{URL}/rest/v1/")
    asyncio.run(auth.user_for_token(jwt(START + 3600)))
    assert str(supabase.requests[0].url) == f"{URL}/auth/v1/user"


def test_a_fresh_sign_in_needs_no_lookup_for_its_token():
    token = jwt(START + 3600)
    auth, supabase, _ = make_auth((200, token_body(token, "refresh-1")))

    async def main():
        tokens = await auth.sign_in("maya@example.com", "correct horse")
        assert await auth.user_for_token(tokens.access_token) == USER

    asyncio.run(main())
    assert [r.url.path for r in supabase.requests] == ["/auth/v1/token"]  # Supabase just issued it


def test_supabase_rate_limiting_a_sign_in_counts_as_throttled():
    auth, _, _ = make_auth((429, RATE_LIMITED))
    with pytest.raises(Throttled):
        asyncio.run(auth.sign_in("maya@example.com", "correct horse"))
