"""The voice socket and POST /turn. Design: docs/superpowers/specs/2026-10-16-voice-loop-design.md.

Both run the same run_turn(), so text mode and voice mode share one pipeline. POST /turn
is the text fallback for when the socket can't connect, and a plain way to talk to Wren
from a script. Provider keys stay in this process; neither route sends one anywhere.

Both admit a signed-in adult, or the loopback dev identity, and check once: at the hello,
or on each POST /turn, never per spoken turn. A refusal says why. Permanent ones (no valid
token, no 18+ confirmation) close the socket with 4401; transient ones (the age check or
the token check couldn't answer) close with 4503, and the page reconnects with backoff.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.websockets import WebSocketDisconnect
from typing import Annotated, Literal

from app.conversation import Conversation, reply_runs
from app.runtime import Runtime, get_runtime
from app.throttle import Throttled
from app.turn import Spoken, run_turn
from app.voice import clock
from app.voice.identity import Identity, bearer_token, resolve_identity
from app.voice.metrics import TurnTiming, log_turn
from app.voice.protocol import Hello, mock_message, parse_client
from app.voice.session import VoiceSession

logger = logging.getLogger(__name__)
router = APIRouter()

REFUSED_CLOSE = 4401  # retrying won't help: the page goes to sign-in or the age step
SESSION_UNAVAILABLE_CLOSE = 4503  # transient: the page keeps the message and reconnects with backoff
REFUSALS = {
    "unauthorized": "You're signed out. Sign in to keep going.",
    "age_unconfirmed": "I'm built for adults. Confirm you're 18 or older to keep going.",
    "age_check_failed": "I can't check your account right now.",
    "unavailable": "I can't reach sign-in right now.",
}
HTTP_STATUS = {"unauthorized": 401, "age_unconfirmed": 403, "age_check_failed": 503, "unavailable": 503}


@dataclass(frozen=True)
class Refusal:
    code: str
    transient: bool

    def message(self, retry: str) -> str:
        return f"{REFUSALS[self.code]} {retry}" if self.transient else REFUSALS[self.code]


async def admit(runtime: Runtime, token: str | None, client_host: str | None) -> Identity | Refusal:
    """Who is asking, if they may talk to Wren: a signed-in adult, or the dev identity."""
    try:
        identity = await resolve_identity(token, client_host, runtime.voice.dev_user_id, runtime.auth)
    except (Throttled, httpx.HTTPError) as e:
        logger.warning("couldn't check a sign-in token: %s", type(e).__name__)
        return Refusal("unavailable", transient=True)
    if identity is None:
        return Refusal("unauthorized", transient=False)
    if identity.dev:  # only on the owner's machine
        return identity
    try:
        adult = await runtime.memory.age_confirmed(identity.user_id)
    except Exception as e:  # 18+ is a product rule, so the check fails closed
        logger.warning("the age check couldn't answer: %s", type(e).__name__)
        return Refusal("age_check_failed", transient=True)
    return identity if adult else Refusal("age_unconfirmed", transient=False)


@router.websocket("/ws")
async def voice_socket(ws: WebSocket) -> None:
    runtime = get_runtime(ws.app)
    if ws.headers.get("origin") not in runtime.voice.allowed_origins:
        await ws.close(code=1008)  # before accept: the browser sees the handshake refused
        return
    await ws.accept()
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=runtime.tuning.hello_timeout_s)
        hello = parse_client(raw)
        if not isinstance(hello, Hello):
            raise ValueError("the first message must be a hello")
    except WebSocketDisconnect:
        return
    except (TimeoutError, ValueError, KeyError) as e:
        logger.info("closing a socket without a valid hello: %s", type(e).__name__)
        await ws.close(code=4400)
        return
    admitted = await admit(runtime, hello.token, ws.client.host if ws.client else None)
    if isinstance(admitted, Refusal):
        logger.info("refusing a hello: %s", admitted.code)
        message = admitted.message("I'll keep trying.")
        await ws.send_text(json.dumps({"type": "error", "code": admitted.code, "message": message}))
        await ws.close(code=SESSION_UNAVAILABLE_CLOSE if admitted.transient else REFUSED_CLOSE)
        return
    await VoiceSession(ws, runtime, admitted.user_id, hello).run()


class TurnRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    session_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    mode: Literal["checkin", "mock"] = "checkin"
    tz: str = Field(default="UTC", max_length=64)


@router.post("/turn")
async def post_turn(body: TurnRequest, request: Request) -> Response:
    runtime = get_runtime(request.app)
    origin = request.headers.get("origin")
    if origin is not None and origin not in runtime.voice.allowed_origins:
        raise HTTPException(status_code=403, detail="origin not allowed")
    admitted = await admit(
        runtime, bearer_token(request.headers.get("authorization")), request.client.host if request.client else None,
    )
    if isinstance(admitted, Refusal):
        logger.info("refusing POST /turn: %s", admitted.code)
        content = {"code": admitted.code, "message": admitted.message("Try again in a minute.")}
        return JSONResponse(status_code=HTTP_STATUS[admitted.code], content=content)
    user_id = admitted.user_id
    tz = Hello(type="hello", tz=body.tz).tz  # unknown zones fall back to UTC
    memory = await runtime.memory.for_user(user_id)
    convo = runtime.registry.get(body.session_id) if body.session_id else None
    if convo is None or convo.user_id != user_id:
        session_id, background, turns = await memory.open_session(
            user_id, body.mode, tz, body.session_id, datetime.now(timezone.utc)
        )
        convo = Conversation(session_id, user_id, body.mode, tz, background, turns)
        runtime.registry.put(convo)

    async def stream():
        yield json.dumps({"type": "session", "session_id": convo.session_id, "memory": memory.status}) + "\n"
        timing = TurnTiming(turn=len(convo.turns) // 2 + 1, mode=convo.mode, input="text", committed=clock.now())
        said: list[Spoken] = []
        try:
            async with convo.lock:
                # Who has the floor is read under the lock: a turn on the voice socket can be handing it over.
                floor, timing.mode = convo.mock, convo.turn_mode
                async for spoken in run_turn(
                    convo, body.text, retrieve=lambda t: memory.retrieve(user_id, t), chat=runtime.chat,
                    timing=timing, tuning=runtime.tuning,
                ):
                    if spoken.switch:  # run_turn has just handed over the floor
                        yield json.dumps(mock_message(convo.mock)) + "\n"
                    said.append(spoken)
                    line = {"type": "segment", "text": spoken}
                    if spoken.mock is not None:
                        line["role"] = "interviewer"
                    yield json.dumps(line) + "\n"
                # Their words belong to the mock if the interviewer had the floor. Wren's save one turn per speaker.
                theirs = floor.id if floor is not None and floor.on else None
                records = [convo.add("user", body.text, mock=theirs).record()]
                for text, mock in reply_runs((spoken, spoken.mock) for spoken in said):
                    records.append(convo.add("wren", text, mock=mock, in_role=mock is not None).record())
            timing.outcome = "complete"
            await memory.append(convo.session_id, user_id, records)
        except Exception as e:
            logger.exception("POST /turn failed: %s", e)
            timing.outcome = "failed"
            yield json.dumps({"type": "error", "code": "reply_failed", "message": "I lost my train of thought. Could you say that again?"}) + "\n"
        log_turn(convo.session_id, timing)
        yield json.dumps({"type": "end", "metrics": timing.record()}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")
