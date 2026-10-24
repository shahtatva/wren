"""Sign-in routes. The browser never talks to Supabase: it signs in here, and the voice
server calls Supabase Auth with its secret key. Plan: docs/superpowers/plans/2026-10-19-sign-in.md.

- POST /auth/signin {email, password}: tokens, and whether the user has confirmed they're 18+.
- POST /auth/refresh {refresh_token}: new tokens.
- POST /auth/age {tz}, with a bearer token: records the 18+ confirmation.
- POST /auth/signout, with a bearer token: ends this session at Supabase.

Refusals are JSON {code, message}. A wrong email and a wrong password get the same one, so
the routes don't reveal who has an account. Passwords and tokens are never logged.
"""

import logging
from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from app.auth import SignInRefused
from app.runtime import Runtime, get_runtime
from app.throttle import Throttled
from app.voice.identity import bearer_token
from app.voice.protocol import Hello

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")

WRONG_PASSWORD = "That email and password don't match. Check them and try again."
TOO_MANY_TRIES = "Too many tries. Wait a few minutes, then try again."
SIGNED_OUT = "You've been signed out. Sign in again to keep going."
UNAVAILABLE = "I can't reach sign-in right now. Try again in a minute."
NOT_SAVED = "I couldn't save that just now. Try again in a minute."


class SignIn(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class Refresh(BaseModel):
    refresh_token: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class Age(BaseModel):
    tz: str = Field(default="UTC", max_length=64)


def _refuse(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


def _runtime(request: Request) -> Runtime:
    runtime = get_runtime(request.app)
    origin = request.headers.get("origin")
    if origin is not None and origin not in runtime.voice.allowed_origins:
        raise HTTPException(status_code=403, detail="origin not allowed")
    return runtime


async def _age_confirmed(runtime: Runtime, user) -> bool | None:
    """None when the database can't answer: the hello's own check decides then, and fails closed."""
    try:
        return await runtime.memory.age_confirmed(user)
    except Exception as e:
        logger.warning("sign-in: the age check couldn't answer: %s", type(e).__name__)
        return None


@router.post("/signin")
async def sign_in(body: SignIn, request: Request):
    runtime = _runtime(request)
    if runtime.auth is None:
        return _refuse(503, "unavailable", UNAVAILABLE)
    try:
        tokens = await runtime.auth.sign_in(body.email, body.password)
        user = await runtime.auth.user_for_token(tokens.access_token)  # cached by the sign-in: no second call
    except SignInRefused:
        logger.info("sign-in refused: wrong email or password")
        return _refuse(400, "wrong_password", WRONG_PASSWORD)
    except Throttled:
        logger.info("sign-in throttled")
        return _refuse(429, "throttled", TOO_MANY_TRIES)
    except httpx.HTTPError as e:
        logger.warning("sign-in: Supabase Auth failed: %s", type(e).__name__)
        return _refuse(503, "unavailable", UNAVAILABLE)
    if user is None:
        return _refuse(503, "unavailable", UNAVAILABLE)
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_in": tokens.expires_in,
        "age_confirmed": await _age_confirmed(runtime, user),
    }


@router.post("/refresh")
async def refresh(body: Refresh, request: Request):
    runtime = _runtime(request)
    if runtime.auth is None:
        return _refuse(503, "unavailable", UNAVAILABLE)
    try:
        tokens = await runtime.auth.refresh(body.refresh_token)
    except SignInRefused:
        return _refuse(401, "unauthorized", SIGNED_OUT)
    except Throttled:
        return _refuse(429, "throttled", TOO_MANY_TRIES)
    except httpx.HTTPError as e:
        logger.warning("refresh: Supabase Auth failed: %s", type(e).__name__)
        return _refuse(503, "unavailable", UNAVAILABLE)
    return {"access_token": tokens.access_token, "refresh_token": tokens.refresh_token, "expires_in": tokens.expires_in}


@router.post("/age")
async def confirm_age(body: Age, request: Request):
    runtime = _runtime(request)
    token = bearer_token(request.headers.get("authorization"))
    if token is None or runtime.auth is None:
        return _refuse(401, "unauthorized", SIGNED_OUT)
    try:
        user = await runtime.auth.user_for_token(token)
    except (Throttled, httpx.HTTPError) as e:
        logger.warning("age: couldn't check the token: %s", type(e).__name__)
        return _refuse(503, "unavailable", UNAVAILABLE)
    if user is None:
        return _refuse(401, "unauthorized", SIGNED_OUT)
    try:
        await runtime.memory.confirm_age(user, datetime.now(timezone.utc), Hello(type="hello", tz=body.tz).tz)
    except Exception as e:
        logger.warning("age: couldn't record the confirmation: %s", type(e).__name__)
        return _refuse(503, "unavailable", NOT_SAVED)
    return Response(status_code=204)


@router.post("/signout")
async def sign_out(request: Request):
    runtime = _runtime(request)
    token = bearer_token(request.headers.get("authorization"))
    if token is not None and runtime.auth is not None:
        try:
            await runtime.auth.sign_out(token)
        except httpx.HTTPError as e:  # the token is forgotten here either way, and the page drops it
            logger.warning("sign-out: Supabase Auth failed: %s", type(e).__name__)
    return Response(status_code=204)
