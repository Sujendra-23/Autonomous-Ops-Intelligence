"""Validated wall-clock meeting input; the server resolves the selected IANA zone."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScheduleMeeting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: uuid.UUID
    title: str = Field(min_length=1, max_length=512, pattern=r"\S")
    meeting_date: date
    start_time: time
    time_zone: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(default=30, ge=5, le=1440)
    attendees: list[str] = Field(default_factory=list, max_length=100)
    description: str = Field(default="", max_length=10000)
    reminder_minutes: Literal[5, 10, 15, 30, 60] | None = 10

    @field_validator("title", "description")
    @classmethod
    def trim(cls, value):
        return value.strip()

    @field_validator("attendees")
    @classmethod
    def emails(cls, values):
        import re

        cleaned = []
        for email in values:
            email = email.strip().lower()
            if len(email) > 254 or not re.fullmatch(
                r"[^\s@<>,;\"]+@[^\s@<>,;\"]+\.[^\s@<>,;\"]+", email
            ):
                raise ValueError("Enter complete attendee email addresses")
            if email not in cleaned:
                cleaned.append(email)
        return cleaned

    def start(self) -> datetime:
        try:
            zone = ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Choose a valid IANA time zone") from exc
        if self.start_time.tzinfo is not None:
            raise ValueError("Use a local start time and the separate time zone")
        local = datetime.combine(self.meeting_date, self.start_time)
        options = [local.replace(tzinfo=zone, fold=fold) for fold in (0, 1)]
        valid = [
            value
            for value in options
            if value.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == local
        ]
        if not valid:
            raise ValueError(
                "This local time does not exist due to daylight saving. Choose another time"
            )
        if len(valid) == 2 and valid[0].utcoffset() != valid[1].utcoffset():
            raise ValueError(
                "This local time occurs twice due to daylight saving. Choose another time"
            )
        return valid[0]

    @model_validator(mode="after")
    def valid_start(self):
        if self.start().astimezone(UTC) <= datetime.now(UTC):
            raise ValueError("Choose a meeting time in the future")
        return self

    def end(self) -> datetime:
        # Duration is elapsed time, including when a meeting crosses a DST change.
        return (self.start().astimezone(UTC) + timedelta(minutes=self.duration_minutes)).astimezone(
            ZoneInfo(self.time_zone)
        )
