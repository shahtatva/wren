"""A fake Supabase Auth for tests, served through httpx.MockTransport.

Bodies mirror what the real API returns: UserSchema from GET /auth/v1/user and the admin
users list, AccessTokenResponseSchema from POST /auth/v1/token, and GoTrue's error shape
{code, error_code, msg}.
"""

import base64
import json
from collections.abc import Callable
from uuid import UUID

import httpx

URL, KEY = "https://wren-test.supabase.co", "sb_secret_test"
USER = UUID("6f1c2c1e-3d1a-4a52-9a43-0b7f2a1f9e10")
EMAIL, PASSWORD = "maya@example.com", "correct horse battery staple"
START = 1_800_000_000.0  # wall-clock seconds; tests move the clock by hand


def user_body(user: UUID = USER, email: str = EMAIL) -> dict:
    return {
        "id": str(user), "aud": "authenticated", "role": "authenticated", "email": email,
        "email_confirmed_at": "2026-10-16T17:00:00Z", "phone": "", "confirmed_at": "2026-10-16T17:00:00Z",
        "last_sign_in_at": "2026-10-24T09:00:00Z", "app_metadata": {"provider": "email", "providers": ["email"]},
        "user_metadata": {}, "identities": [], "created_at": "2026-10-16T17:00:00Z",
        "updated_at": "2026-10-24T09:00:00Z", "is_anonymous": False,
    }


USER_BODY = user_body()
BAD_JWT = {"code": 403, "error_code": "bad_jwt", "msg": "invalid JWT: unable to parse or verify signature, token is expired"}
INVALID_CREDENTIALS = {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"}
SPENT_REFRESH = {"code": 400, "error_code": "refresh_token_already_used", "msg": "Invalid Refresh Token: Already Used"}
RATE_LIMITED = {"code": 429, "error_code": "over_request_rate_limit", "msg": "Request rate limit reached"}


def jwt(exp: float, n: int = 0) -> str:
    """An access token's shape: header.payload.signature. The server reads only exp from it."""

    def part(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    payload = {"sub": str(USER), "role": "authenticated", "session_id": f"session-{n}", "exp": int(exp)}
    return f"{part({'alg': 'HS256', 'typ': 'JWT'})}.{part(payload)}.signature-{n}"


def token_body(access: str, refresh: str, user: UUID = USER) -> dict:
    return {
        "access_token": access, "token_type": "bearer", "expires_in": 3600, "expires_at": int(START) + 3600,
        "refresh_token": refresh, "user": user_body(user),
    }


Answer = tuple[int, dict | None]


class FakeSupabase:
    """Answers each request with the next scripted answer (the last one repeats), or with
    route(request) when given one. Keeps every request."""

    def __init__(self, *answers: Answer, route: Callable[[httpx.Request], Answer] | None = None):
        self.answers, self.route, self.requests = list(answers), route, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.route is not None:
            status, body = self.route(request)
        else:
            status, body = self.answers[min(len(self.requests), len(self.answers)) - 1]
        return httpx.Response(status) if body is None else httpx.Response(status, json=body)

    def calls(self, path: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == path]


class Clock:
    def __init__(self, now: float = START):
        self.now = now

    def __call__(self) -> float:
        return self.now


def client_for(supabase: FakeSupabase) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(supabase))
