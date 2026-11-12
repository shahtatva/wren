"""The "What Wren knows" routes: the page reads and corrects what Wren remembers. Design:
docs/superpowers/specs/2026-11-11-perch-ui-design.md ("The voice server's side").

- GET /knows: the profile, the current interviews with the dates they replaced, and the current memories.
- PATCH /knows/memories/{id} {text}: the owner's text, embedded again. DELETE removes it and its older versions.
- PATCH /knows/pipeline/{id}: corrects an interview in place. DELETE removes it and its history.
- PATCH /knows/profile: name, background and target roles. Only the fields sent change.

Each route admits the caller as POST /turn does (a signed-in adult, or the loopback dev identity) and
runs as that user, so RLS scopes every row. Refusals are JSON {code, message}. What the owner wrote is
never logged.
"""

import logging
from datetime import date, time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.auth_routes import NOT_SAVED
from app.memory.live import Memory, MemoryOff
from app.memory.types import InterviewFields, KnownInterview, KnownMemory, Knows, Profile, Source
from app.routes import HTTP_STATUS, Refusal, admit
from app.runtime import get_runtime
from app.voice.identity import bearer_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/knows")

NOT_FOUND = "That isn't there anymore."
MEMORY_OFF = "Wren's memory is off right now. Try again later."
UNAVAILABLE = "I can't reach Wren's memory right now. Try again in a minute."

Role = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class MemoryEdit(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class InterviewEdit(BaseModel):
    """Every field the page can correct, each one required (null where it allows), because the row is
    rewritten whole: a field left out would otherwise wipe it. A blank role or note means there isn't one."""

    company: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    role: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None
    stage: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    next_date: date | None
    next_time: time | None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None

    @field_validator("role", "notes")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None

    def fields(self) -> InterviewFields:
        return InterviewFields(self.company, self.role, self.stage, self.next_date, self.next_time, self.notes)


class ProfileEdit(BaseModel):
    """Only the fields the page sends change. A blank name clears it."""

    display_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    summary: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None
    target_roles: Annotated[list[Role], Field(max_length=10)] | None = None

    @model_validator(mode="after")
    def _names_a_field(self) -> "ProfileEdit":
        if not self.model_fields_set:
            raise ValueError("name at least one field to change")
        return self

    def changes(self) -> dict[str, object]:
        empty = {"display_name": None, "summary": "", "target_roles": []}  # what each field holds when cleared
        return {name: getattr(self, name) or empty[name] for name in self.model_fields_set}


def _source(source: Source) -> dict:
    return {"mode": source.mode, "at": source.at.isoformat()}


def _when(day: date | None, at: time | None) -> dict:
    return {"next_date": day.isoformat() if day else None, "next_time": at.strftime("%H:%M") if at else None}


def _memory(memory: KnownMemory) -> dict:
    return {"id": memory.id, "kind": memory.kind, "text": memory.text, "source": _source(memory.source)}


def _interview(interview: KnownInterview) -> dict:
    fields = interview.fields
    return {
        "id": interview.id, "company": fields.company, "role": fields.role, "stage": fields.stage,
        **_when(fields.next_date, fields.next_time), "notes": fields.notes, "source": _source(interview.source),
        "moved_from": [_when(day, at) for day, at in interview.moved_from],
    }


def _profile(profile: Profile | None) -> dict | None:
    if profile is None:
        return None
    return {"display_name": profile.display_name, "summary": profile.summary, "target_roles": list(profile.target_roles)}


def _knows(knows: Knows) -> dict:
    return {
        "timezone": knows.timezone,
        "profile": _profile(knows.profile),
        "pipeline": [_interview(interview) for interview in knows.pipeline],
        "memories": [_memory(memory) for memory in knows.memories],
    }


def _refuse(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, "message": message})


async def _admit(request: Request) -> tuple[UUID, Memory] | JSONResponse:
    """The caller and their memory, or the refusal to send. The same admission as POST /turn."""
    runtime = get_runtime(request.app)
    origin = request.headers.get("origin")
    if origin is not None and origin not in runtime.voice.allowed_origins:
        raise HTTPException(status_code=403, detail="origin not allowed")
    admitted = await admit(
        runtime, bearer_token(request.headers.get("authorization")), request.client.host if request.client else None,
    )
    if isinstance(admitted, Refusal):
        logger.info("refusing /knows: %s", admitted.code)
        return _refuse(HTTP_STATUS[admitted.code], admitted.code, admitted.message("Try again in a minute."))
    return admitted.user_id, await runtime.memory.for_user(admitted.user_id)


async def _answer(request: Request, name: str, work, *, write: bool = True) -> Response:
    """Admit the caller, then run work(user_id, memory). It returns the item to send (200), True for done
    with nothing to send (204), or None or False when the user has no such current row (404)."""
    caller = await _admit(request)
    if isinstance(caller, JSONResponse):
        return caller
    user_id, memory = caller
    try:
        result = await work(user_id, memory)
    except MemoryOff:
        return _refuse(503, "memory_off", MEMORY_OFF)
    except Exception as e:  # the route and the error's type only: never what the owner wrote
        logger.warning("knows: %s failed: %s", name, type(e).__name__)
        return _refuse(503, "not_saved", NOT_SAVED) if write else _refuse(503, "unavailable", UNAVAILABLE)
    if result is None or result is False:
        return _refuse(404, "not_found", NOT_FOUND)
    if result is True:
        return Response(status_code=204)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})  # what Wren knows stays off the browser's disk


@router.get("")
async def read_knows(request: Request) -> Response:
    async def work(user_id, memory):
        return _knows(await memory.knows(user_id))

    return await _answer(request, "read", work, write=False)


@router.patch("/memories/{memory_id}")
async def edit_memory(memory_id: UUID, body: MemoryEdit, request: Request) -> Response:
    async def work(user_id, memory):
        edited = await memory.edit_memory(user_id, memory_id, body.text)
        return _memory(edited) if edited else None

    return await _answer(request, "edit memory", work)


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: UUID, request: Request) -> Response:
    return await _answer(request, "delete memory", lambda user_id, memory: memory.delete_memory(user_id, memory_id))


@router.patch("/pipeline/{interview_id}")
async def edit_interview(interview_id: UUID, body: InterviewEdit, request: Request) -> Response:
    async def work(user_id, memory):
        edited = await memory.edit_interview(user_id, interview_id, body.fields())
        return _interview(edited) if edited else None

    return await _answer(request, "edit interview", work)


@router.delete("/pipeline/{interview_id}")
async def delete_interview(interview_id: UUID, request: Request) -> Response:
    return await _answer(request, "delete interview", lambda user_id, memory: memory.delete_interview(user_id, interview_id))


@router.patch("/profile")
async def edit_profile(body: ProfileEdit, request: Request) -> Response:
    async def work(user_id, memory):
        return _profile(await memory.edit_profile(user_id, body.changes()))

    return await _answer(request, "edit profile", work)
