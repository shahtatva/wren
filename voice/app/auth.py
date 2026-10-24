"""Supabase Auth, called only from the voice server: the browser never talks to Supabase.

A token is checked by asking Supabase (GET /auth/v1/user). The project has no asymmetric
signing keys, so tokens can't be verified locally.
- An answer is cached for at most 5 minutes, and never past the token's exp, so a revoked
  session stops working within 5 minutes.
- A token Supabase refuses is remembered for a minute.
- Both caches are keyed by the token's SHA-256, never the token, and an insert drops what
  has expired, so they stay small.
- Lookups are capped overall: they all come from this one IP, and Supabase lists
  /auth/v1/user under 30 requests per 5 minutes per IP.
- A token Supabase has just issued, at sign-in or refresh, is cached straight away.
- A string that can't be an access token (not a JWT, no numeric exp ahead, too long) is
  refused here, without spending a lookup.

Sign-in attempts are capped per email and overall, which also slows password guessing, and
refreshes are capped overall: sign-in and refresh share one Supabase budget.

The secret key goes only in the apikey header (it isn't a JWT), and only to SUPABASE_URL
over HTTPS. Tokens and passwords are never logged.
"""

import base64
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.throttle import SlidingWindow, Throttled, allow_all

CACHE_S = 300.0
REFUSED_S = 60.0
LOOKUP_LIMIT, LOOKUP_WINDOW_S = 25, 300.0
SIGN_IN_PER_EMAIL = ((5, 15 * 60.0), (20, 24 * 3600.0))
SIGN_IN_OVERALL = (30, 300.0)  # sign-in shares Supabase's 150 per 5 minutes with token refresh
REFRESH_OVERALL = (100, 300.0)  # with sign-in's 30, under that shared 150
MAX_TOKEN_CHARS = 8192
TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_in: int


class SignInRefused(Exception):
    """Supabase refused the email and password, or the refresh token: sign in again."""


def project_url(url: str) -> str:
    """The project's base URL. SUPABASE_URL may be the REST endpoint (.../rest/v1/); Auth is at /auth/v1."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("SUPABASE_URL must be an https URL: the secret key travels with every Auth call")
    return f"https://{parts.netloc}"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _exp(token: str) -> float | None:
    """The token's exp claim, read without verifying it: only to bound the cache."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


def _could_be_valid(token: str, now: float) -> bool:
    """A Supabase access token is a JWT whose numeric exp is still ahead. Anything else can't be valid."""
    if len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
        return False
    exp = _exp(token)
    return exp is not None and exp > now


def _tokens(body: dict) -> Tokens:
    return Tokens(body["access_token"], body["refresh_token"], int(body["expires_in"]))


class SupabaseAuth:
    def __init__(
        self, url: str, key: str, client: httpx.AsyncClient | None = None, *, clock: Callable[[], float] = time.time,
    ):
        self._url, self._key, self._clock = project_url(url), key, clock
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._verified: dict[str, tuple[UUID, float]] = {}  # token digest -> (user, good until)
        self._refused: dict[str, float] = {}  # token digest -> remembered until
        self._lookups = SlidingWindow(LOOKUP_LIMIT, LOOKUP_WINDOW_S, clock=clock)
        self._sign_in_email = [SlidingWindow(limit, window, clock=clock) for limit, window in SIGN_IN_PER_EMAIL]
        self._sign_in_all = SlidingWindow(*SIGN_IN_OVERALL, clock=clock)
        self._refreshes = SlidingWindow(*REFRESH_OVERALL, clock=clock)

    def _evict(self, now: float) -> None:
        self._verified = {k: v for k, v in self._verified.items() if v[1] > now}
        self._refused = {k: until for k, until in self._refused.items() if until > now}

    async def user_for_token(self, token: str) -> UUID | None:
        now = self._clock()
        if not _could_be_valid(token, now):
            return None
        key = _digest(token)
        if (hit := self._verified.get(key)) is not None and hit[1] > now:
            return hit[0]
        if self._refused.get(key, 0.0) > now:
            return None
        if not self._lookups.allow("all"):
            raise Throttled("token lookups")
        response = await self._client.get(
            f"{self._url}/auth/v1/user", headers={"apikey": self._key, "Authorization": f"Bearer {token}"},
        )
        self._evict(now)
        if response.status_code in (401, 403):
            self._refused[key] = now + REFUSED_S
            return None
        if response.status_code == 429:  # Supabase's own per-IP limit, shared by everyone here
            raise Throttled("supabase")
        response.raise_for_status()
        user = UUID(response.json()["id"])
        self._remember(key, user, token, now)
        return user

    def _remember(self, key: str, user: UUID, token: str, now: float) -> None:
        until, exp = now + CACHE_S, _exp(token)
        self._verified[key] = (user, until if exp is None else min(until, exp))

    def forget(self, token: str) -> None:
        self._verified.pop(_digest(token), None)

    async def _grant(self, grant_type: str, body: dict) -> Tokens:
        response = await self._client.post(
            f"{self._url}/auth/v1/token", params={"grant_type": grant_type}, headers={"apikey": self._key}, json=body,
        )
        if response.status_code in (400, 401):
            raise SignInRefused(grant_type)
        if response.status_code == 429:
            raise Throttled("supabase")
        response.raise_for_status()
        body = response.json()
        tokens, now = _tokens(body), self._clock()
        self._evict(now)
        self._remember(_digest(tokens.access_token), UUID(body["user"]["id"]), tokens.access_token, now)
        return tokens

    async def sign_in(self, email: str, password: str) -> Tokens:
        key = email.strip().lower()
        if not allow_all(*((window, key) for window in self._sign_in_email), (self._sign_in_all, "all")):
            raise Throttled("sign-in")
        return await self._grant("password", {"email": email.strip(), "password": password})

    async def refresh(self, refresh_token: str) -> Tokens:
        if not self._refreshes.allow("all"):
            raise Throttled("refresh")
        return await self._grant("refresh_token", {"refresh_token": refresh_token})

    async def sign_out(self, access_token: str) -> None:
        """End this session only (scope=local): the user's other devices stay signed in."""
        self.forget(access_token)
        response = await self._client.post(
            f"{self._url}/auth/v1/logout", params={"scope": "local"},
            headers={"apikey": self._key, "Authorization": f"Bearer {access_token}"},
        )
        if response.status_code not in (401, 403, 404):  # already signed out, or the token is spent
            response.raise_for_status()
