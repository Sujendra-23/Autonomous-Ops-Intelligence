"""Google Calendar meeting access through a workspace-scoped offline OAuth grant."""

import hashlib
import json
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.project import Project
from app.models.transcript import Transcript
from app.schemas.meeting import ScheduleMeeting
from app.services.project_resolver import get_or_create_project
from app.tenancy import LEGACY_WORKSPACE, principal_context


class CalendarEvent(BaseModel):
    id: str
    title: str
    starts_at: str
    all_day: bool
    participants: list[str]
    recurring_event_id: str | None = None
    meeting_url: str | None = None
    ends_at: str | None = None
    time_zone: str | None = None
    calendar_url: str | None = None
    conference_status: str | None = None
    attendee_emails: list[str] = []
    description: str = ""


def safe_google_url(value: str | None, *, meet=False) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        allowed = {"meet.google.com"} if meet else {"calendar.google.com", "www.google.com"}
        if (
            parsed.scheme == "https"
            and parsed.hostname in allowed
            and not parsed.username
            and not parsed.password
        ):
            return value
    except ValueError:
        pass
    return None


def normalize_event(event: dict) -> CalendarEvent:
    start = event.get("start", {})
    conference = event.get("conferenceData") or {}
    video = next(
        (
            p.get("uri")
            for p in conference.get("entryPoints", [])
            if p.get("entryPointType") == "video"
        ),
        None,
    )
    meeting_url = safe_google_url(event.get("hangoutLink") or video, meet=True)
    return CalendarEvent(
        id=event["id"],
        title=event.get("summary") or "Untitled meeting",
        starts_at=start.get("dateTime") or start.get("date", ""),
        all_day="date" in start,
        participants=[
            a.get("displayName") or a["email"]
            for a in event.get("attendees", [])
            if (a.get("displayName") or a.get("email")) and a.get("responseStatus") != "declined"
        ],
        recurring_event_id=event.get("recurringEventId"),
        meeting_url=meeting_url,
        ends_at=event.get("end", {}).get("dateTime") or event.get("end", {}).get("date"),
        time_zone=start.get("timeZone"),
        calendar_url=safe_google_url(event.get("htmlLink")),
        conference_status="success"
        if meeting_url
        else conference.get("createRequest", {}).get("status", {}).get("statusCode"),
        attendee_emails=[a["email"] for a in event.get("attendees", []) if a.get("email")],
        description=event.get("description") or "",
    )


class GoogleCalendar:
    async def _get(self, suffix: str = "", params: dict | None = None) -> dict:
        return await self._request("GET", suffix, params=params)

    async def _request(
        self, method: str, suffix: str = "", *, params: dict | None = None, body: dict | None = None
    ) -> dict:
        settings = get_settings()
        if not settings.google_calendar_enabled:
            raise HTTPException(503, "Google Calendar is not configured on the backend")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                token = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": settings.google_calendar_client_id,
                        "client_secret": settings.google_calendar_client_secret.get_secret_value(),
                        "refresh_token": settings.google_calendar_refresh_token.get_secret_value(),
                        "grant_type": "refresh_token",
                    },
                )
                token.raise_for_status()
                calendar_id = quote(settings.google_calendar_id, safe="")
                response = await client.request(
                    method,
                    f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events{suffix}",
                    headers={"Authorization": f"Bearer {token.json()['access_token']}"},
                    params=params,
                    **({"json": body} if body is not None else {}),
                )
                if response.status_code == 409:
                    raise HTTPException(409, "Calendar event already exists")
                if response.status_code == 403:
                    raise HTTPException(
                        403,
                        "Reconnect Google Calendar with scheduling permission. "
                        "Check that the calendar is writable.",
                    )
                if response.status_code == 401:
                    raise HTTPException(503, "Reconnect Google Calendar to renew access")
                if response.status_code in (404, 410):
                    raise HTTPException(404, "Calendar event or calendar no longer exists")
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # Provider bodies and URLs can contain credentials or private event data.
            raise HTTPException(
                502,
                "Calendar request could not be confirmed. "
                "Refresh upcoming meetings or retry with the same details.",
            ) from exc

    async def schedule(self, payload: ScheduleMeeting, *, aoi_url: str) -> CalendarEvent:
        principal = principal_context.get()
        workspace = str(principal.workspace_id if principal else LEGACY_WORKSPACE)
        event_id = "aoi" + hashlib.sha256(f"{workspace}:{payload.request_id}".encode()).hexdigest()
        body = {
            "id": event_id,
            "summary": payload.title,
            "description": payload.description,
            "start": {"dateTime": payload.start().isoformat(), "timeZone": payload.time_zone},
            "end": {"dateTime": payload.end().isoformat(), "timeZone": payload.time_zone},
            "attendees": [{"email": email} for email in payload.attendees],
            "guestsCanModify": False,
            "reminders": {
                "useDefault": False,
                "overrides": []
                if payload.reminder_minutes is None
                else [{"method": "popup", "minutes": payload.reminder_minutes}],
            },
        }
        body["location"] = aoi_url
        body["description"] = payload.description + "\n\nJoin in AOI: " + aoi_url
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        body["extendedProperties"] = {"private": {"aoiScheduleHash": fingerprint}}
        try:
            event = await self._request("POST", params={"sendUpdates": "all"}, body=body)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            event = await self._get("/" + event_id)
            if (
                event.get("extendedProperties", {}).get("private", {}).get("aoiScheduleHash")
                != fingerprint
            ):
                raise HTTPException(
                    409,
                    "This form already created a different meeting. "
                    "Check your meetings before starting a new one.",
                ) from exc
            if event.get("status") == "cancelled":
                raise HTTPException(
                    409,
                    "This meeting was cancelled. Start a new meeting instead.",
                ) from exc
        return normalize_event(event)

    async def list_events(
        self, start: datetime, end: datetime, page_token: str | None = None
    ) -> dict:
        params = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 100,
        }
        if page_token:
            params["pageToken"] = page_token
        data = await self._get(params=params)
        return {
            "items": [
                normalize_event(e) for e in data.get("items", []) if e.get("status") != "cancelled"
            ],
            "next_page_token": data.get("nextPageToken"),
        }

    async def get_event(self, event_id: str) -> CalendarEvent:
        event = await self._get("/" + quote(event_id, safe=""))
        if event.get("status") == "cancelled":
            raise HTTPException(409, "This calendar event was cancelled; select another meeting")
        return normalize_event(event)


async def meeting_context(event_id: str | None) -> dict:
    if not event_id:
        return {}
    event = await GoogleCalendar().get_event(event_id)
    # An all-day event supplies a calendar date, not an invented UTC meeting time.
    meeting_date = (
        None
        if event.all_day
        else datetime.fromisoformat(event.starts_at.replace("Z", "+00:00")).astimezone(UTC)
    )
    return {
        "title": event.title[:512],
        "participants": event.participants,
        "meeting_date": meeting_date,
        "calendar_context": {
            "provider": "google",
            "calendar_id": get_settings().google_calendar_id,
            **event.model_dump(),
        },
    }


async def resolve_meeting_project(
    session: AsyncSession, context: dict, hint: str | None
) -> Project | None:
    if hint and hint.strip():
        return await get_or_create_project(session, hint)
    calendar = context.get("calendar_context", {})
    series = calendar.get("recurring_event_id")
    if series:
        project_id = await session.scalar(
            select(Transcript.project_id)
            .where(
                Transcript.project_id.is_not(None),
                Transcript.calendar_context["calendar_id"].astext == calendar["calendar_id"],
                Transcript.calendar_context["recurring_event_id"].astext == series,
            )
            .order_by(Transcript.created_at.desc())
            .limit(1)
        )
        if project_id:
            return await session.get(Project, project_id)
    return None
