"""The voice socket and POST /turn. Design: docs/superpowers/specs/2026-10-16-voice-loop-design.md.

Both run the same run_turn(), so text mode and voice mode share one pipeline. POST /turn
is the text fallback for when the socket can't connect, and a plain way to talk to Wren
from a script. Provider keys stay in this process; neither route sends one anywhere.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from starlette.websockets import WebSocketDisconnect
from typing import Annotated, Literal

from app.conversation import Conversation
from app.runtime import get_runtime
from app.turn import run_turn
from app.voice import clock
from app.voice.identity import resolve_identity
from app.voice.metrics import TurnTiming, log_turn
from app.voice.protocol import Hello, parse_client
from app.voice.session import VoiceSession

logger = logging.getLogger(__name__)
router = APIRouter()


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
    user_id = resolve_identity(hello.token, ws.client.host if ws.client else None, runtime.voice.dev_user_id)
    if user_id is None:
        await ws.send_text(json.dumps({"type": "error", "code": "unauthorized", "message": "This voice server only accepts connections from its own machine for now."}))
        await ws.close(code=4401)
        return
    await VoiceSession(ws, runtime, user_id, hello).run()


class TurnRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    session_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    mode: Literal["checkin", "mock"] = "checkin"
    tz: str = Field(default="UTC", max_length=64)


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header.removeprefix("Bearer ").strip() or None


@router.post("/turn")
async def post_turn(body: TurnRequest, request: Request) -> StreamingResponse:
    runtime = get_runtime(request.app)
    origin = request.headers.get("origin")
    if origin is not None and origin not in runtime.voice.allowed_origins:
        raise HTTPException(status_code=403, detail="origin not allowed")
    user_id = resolve_identity(_bearer(request), request.client.host if request.client else None, runtime.voice.dev_user_id)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not signed in")
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
        texts: list[str] = []
        try:
            async with convo.lock:
                async for text in run_turn(
                    convo, body.text, retrieve=lambda t: memory.retrieve(user_id, t), chat=runtime.chat,
                    timing=timing, tuning=runtime.tuning,
                ):
                    texts.append(text)
                    yield json.dumps({"type": "segment", "text": text}) + "\n"
                records = [convo.add("user", body.text).record()]
                if texts:
                    records.append(convo.add("wren", " ".join(texts)).record())
            timing.outcome = "complete"
            await memory.append(convo.session_id, user_id, records)
        except Exception as e:
            logger.exception("POST /turn failed: %s", e)
            timing.outcome = "failed"
            yield json.dumps({"type": "error", "code": "reply_failed", "message": "I lost my train of thought. Could you say that again?"}) + "\n"
        log_turn(convo.session_id, timing)
        yield json.dumps({"type": "end", "metrics": timing.record()}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")
