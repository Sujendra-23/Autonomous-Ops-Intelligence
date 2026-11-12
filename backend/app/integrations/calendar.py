"""Google Calendar access using a server-side offline OAuth grant.

Reads are always available; event creation (`create_event`) is opt-in via
GOOGLE_CALENDAR_WRITE_ENABLED and needs the calendar.events scope.
"""

from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.project import Project
from app.models.transcript import Transcript
from app.services.project_resolver import get_or_create_project


class CalendarEvent(BaseModel):
    id: str
    title: str
    starts_at: str
    all_day: bool
    participants: list[str]
    recurring_event_id: str | None = None
    meeting_url: str | None = None


def normalize_event(event: dict) -> CalendarEvent:
    start = event.get("start", {})
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
        meeting_url=event.get("hangoutLink"),
    )


class GoogleCalendar:
    async def _call(
        self,
        method: str = "GET",
        suffix: str = "",
        params: dict | None = None,
        json_body: dict | None = None,
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
                    json=json_body,
                )
                if response.status_code in (404, 410):
                    raise HTTPException(404, "Calendar event or calendar no longer exists")
                if response.status_code == 409 and method == "POST":
                    raise HTTPException(409, "Calendar event already exists")
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            # Provider bodies and URLs can contain credentials or private event data.
            raise HTTPException(
                502, "Calendar request failed; check backend OAuth configuration"
            ) from exc

    async def _get(self, suffix: str = "", params: dict | None = None) -> dict:
        return await self._call("GET", suffix, params)

    async def create_event(
        self,
        *,
        event_id: str,
        title: str,
        start: datetime,
        end: datetime,
        description: str = "",
        timezone: str = "UTC",
    ) -> CalendarEvent:
        """Insert an event. `event_id` makes retries idempotent (409 = already created).

        Google requires ids of 5-1024 chars from [a-v0-9]; a hex digest qualifies.
        Requires a refresh token granted the calendar.events scope and
        GOOGLE_CALENDAR_WRITE_ENABLED=true.
        """
        if not get_settings().google_calendar_write_enabled:
            raise HTTPException(503, "Calendar writes are disabled (GOOGLE_CALENDAR_WRITE_ENABLED)")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("event times must be timezone-aware")
        body = {
            "id": event_id,
            "summary": title[:512],
            "description": description[:8000],
            "start": {"dateTime": start.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone},
        }
        try:
            data = await self._call("POST", params={"sendUpdates": "none"}, json_body=body)
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            data = await self._call("GET", "/" + quote(event_id, safe=""))
        return normalize_event(data)

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
