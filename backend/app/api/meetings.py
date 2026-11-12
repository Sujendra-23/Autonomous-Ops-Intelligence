"""Native AOI meeting and huddle records; every lookup uses the workspace scope."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_ingest_key
from app.config import get_settings
from app.database import get_session
from app.integrations.calendar import GoogleCalendar
from app.models.meeting import MeetingRoom
from app.schemas.meeting import ScheduleMeeting
from app.services import calls
from app.tenancy import LEGACY_WORKSPACE, principal_context, workspace_context

router = APIRouter(dependencies=[Depends(require_ingest_key)])


class RoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    title: str = Field(min_length=1, max_length=512, pattern=r"\S")
    kind: Literal["meeting", "huddle"] = "meeting"


class RoomSchedule(ScheduleMeeting):
    invite_on_calendar: bool = False


class JoinRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80, pattern=r"\S")


def workspace():
    return workspace_context.get() or LEGACY_WORKSPACE


def output(room, participants=None):
    return {
        "id": str(room.id),
        "title": room.title,
        "kind": room.kind,
        "starts_at": room.starts_at,
        "ends_at": room.ends_at,
        "time_zone": room.time_zone,
        "description": room.description,
        "attendees": room.attendees,
        "participants": participants,
        "calendar_url": room.calendar_url,
        "invitation_status": room.invitation_status,
    }


async def find(db, room_id):
    room = await db.scalar(
        select(MeetingRoom).where(
            MeetingRoom.id == room_id, MeetingRoom.workspace_id == workspace()
        )
    )
    if room is None:
        raise HTTPException(404, "Meeting not found in this workspace")
    return room


def check_retry(room, values, *, scheduled=False):
    keys = (
        (
            "title",
            "kind",
            "starts_at",
            "ends_at",
            "time_zone",
            "attendees",
            "description",
            "reminder_minutes",
        )
        if scheduled
        else ("title", "kind")
    )
    if any(getattr(room, key) != values[key] for key in keys):
        raise HTTPException(
            409,
            "This form already created a different meeting. "
            "Check your meetings before starting a new one.",
        )


async def save(db, request_id, *, scheduled=False, **values):
    room = await db.scalar(
        select(MeetingRoom).where(
            MeetingRoom.workspace_id == workspace(), MeetingRoom.request_id == request_id
        )
    )
    if room:
        check_retry(room, values, scheduled=scheduled)
        return room, False
    principal = principal_context.get()
    room = MeetingRoom(
        id=uuid.uuid4(),
        request_id=request_id,
        workspace_id=workspace(),
        created_by=principal.account_id if principal else None,
        **{
            "invitation_status": "not_requested",
            "attendees": [],
            "description": "",
            "reminder_minutes": 10,
            **values,
        },
    )
    db.add(room)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        room = await db.scalar(
            select(MeetingRoom).where(
                MeetingRoom.workspace_id == workspace(), MeetingRoom.request_id == request_id
            )
        )
        if room is None:
            raise
        check_retry(room, values, scheduled=scheduled)
        return room, False
    return room, True


@router.get("/status")
async def call_status():
    return {
        "calls_enabled": calls.enabled(),
        "calendar_enabled": get_settings().google_calendar_enabled,
    }


@router.get("")
async def list_meetings(
    kind: Literal["meeting", "huddle"] = "meeting",
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_session),
):
    query = select(MeetingRoom).where(
        MeetingRoom.workspace_id == workspace(), MeetingRoom.kind == kind
    )
    if kind == "meeting":
        query = query.where(MeetingRoom.ends_at >= datetime.now(UTC) - timedelta(hours=6))
    rows = (await db.scalars(query.order_by(MeetingRoom.starts_at).offset(offset).limit(51))).all()
    more = len(rows) > 50
    rows = rows[:50]
    counts = await calls.presence(rows)
    return {
        "items": [
            output(room, counts.get(room.media_room, 0) if counts is not None else None)
            for room in rows
        ],
        "has_more": more,
        "calls_enabled": calls.enabled(),
        "calendar_enabled": get_settings().google_calendar_enabled,
    }


@router.post("")
async def start_meeting(payload: RoomCreate, db: AsyncSession = Depends(get_session)):
    now = datetime.now(UTC)
    room, _ = await save(
        db,
        payload.request_id,
        title=payload.title.strip(),
        kind=payload.kind,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        time_zone="UTC",
    )
    return output(room)


@router.post("/schedule")
async def schedule_meeting(payload: RoomSchedule, db: AsyncSession = Depends(get_session)):
    room, fresh = await save(
        db,
        payload.request_id,
        scheduled=True,
        title=payload.title,
        kind="meeting",
        starts_at=payload.start(),
        ends_at=payload.end(),
        time_zone=payload.time_zone,
        description=payload.description,
        attendees=payload.attendees,
        reminder_minutes=payload.reminder_minutes,
        invitation_status="pending" if payload.invite_on_calendar else "not_requested",
    )
    if fresh:
        if payload.invite_on_calendar:
            await invite(db, room, payload)
    return output(room)


async def invite(db, room, payload):
    url = get_settings().app_public_url.rstrip("/") + "/meetings/rooms/" + str(room.id)
    try:
        event = await GoogleCalendar().schedule(payload, aoi_url=url)
        room.calendar_event_id = event.id
        room.calendar_url = event.calendar_url
        room.invitation_status = "requested"
    except HTTPException:
        # The AOI meeting is durable even if the optional calendar call fails.
        room.invitation_status = "failed"
    await db.commit()


@router.post("/{room_id}/invitations")
async def retry_invitations(room_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    # Serialize explicit retries against one stored invitation intent.
    room = await db.scalar(
        select(MeetingRoom)
        .where(MeetingRoom.id == room_id, MeetingRoom.workspace_id == workspace())
        .with_for_update()
    )
    if room is None:
        raise HTTPException(404, "Meeting not found in this workspace")
    if room.invitation_status == "requested":
        return output(room)
    if room.invitation_status not in {"failed", "pending"}:
        raise HTTPException(409, "Calendar invitations were not requested for this meeting")
    local = room.starts_at.astimezone(ZoneInfo(room.time_zone))
    try:
        payload = ScheduleMeeting(
            request_id=room.request_id,
            title=room.title,
            meeting_date=local.date(),
            start_time=local.time().replace(tzinfo=None),
            time_zone=room.time_zone,
            duration_minutes=int((room.ends_at - room.starts_at).total_seconds() / 60),
            attendees=room.attendees,
            description=room.description,
            reminder_minutes=room.reminder_minutes,
        )
    except ValueError as exc:
        raise HTTPException(
            409, "This meeting has already started. Manage its calendar invitation directly."
        ) from exc
    await invite(db, room, payload)
    return output(room)


@router.get("/{room_id}")
async def get_meeting(room_id: uuid.UUID, db: AsyncSession = Depends(get_session)):
    room = await find(db, room_id)
    return {**output(room), "calls_enabled": calls.enabled()}


@router.post("/{room_id}/join")
async def join_meeting(
    room_id: uuid.UUID,
    payload: JoinRequest,
    response: Response,
    db: AsyncSession = Depends(get_session),
):
    room = await find(db, room_id)
    response.headers["Cache-Control"] = "no-store"
    return await calls.join(room, payload.display_name.strip())
