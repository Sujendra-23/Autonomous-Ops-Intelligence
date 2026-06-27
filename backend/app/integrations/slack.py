"""Slack integration via the bot token API.

Posts an execution summary to the configured channel after each transcript is
processed, and is also used by the drift monitor for reminders and escalations.
"""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.integrations.base import DispatchResult, Notifier
from app.logging import get_logger
from app.models.project import Project
from app.models.transcript import Transcript
from app.schemas.extraction import ExtractionResult

logger = get_logger("app.integrations.slack")

_SLACK_POST = "https://slack.com/api/chat.postMessage"


class SlackAdapter(Notifier):
    name = "slack"

    def __init__(self) -> None:
        s = get_settings()
        self._token = s.slack_bot_token.get_secret_value()
        self._channel = s.slack_default_channel

    def is_enabled(self) -> bool:
        return get_settings().slack_enabled

    async def post_summary(
        self,
        transcript: Transcript,
        project: Project | None,
        result: ExtractionResult,
    ) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")

        blocks = _build_summary_blocks(transcript, project, result)
        return await self._post_blocks(blocks, fallback=result.summary)

    async def send_reminder(self, text: str) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        return await self._post_text(text)

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _post_blocks(self, blocks: list[dict], *, fallback: str) -> DispatchResult:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                _SLACK_POST,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={
                    "channel": self._channel,
                    "text": fallback,
                    "blocks": blocks,
                },
            )
            response.raise_for_status()
            body = response.json()
        ok = bool(body.get("ok"))
        return DispatchResult(
            self.name,
            ok,
            detail=None if ok else body.get("error"),
            external_id=body.get("ts"),
        )

    async def _post_text(self, text: str) -> DispatchResult:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                _SLACK_POST,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={"channel": self._channel, "text": text},
            )
            response.raise_for_status()
            body = response.json()
        ok = bool(body.get("ok"))
        if not ok:
            logger.warning("slack.post_failed", error=body.get("error"))
        return DispatchResult(
            self.name, ok, detail=None if ok else body.get("error"), external_id=body.get("ts")
        )


def _build_summary_blocks(
    transcript: Transcript,
    project: Project | None,
    result: ExtractionResult,
) -> list[dict]:
    header_text = f"📝 *{transcript.title}*"
    if project:
        header_text += f"  ·  _{project.name}_"

    sections: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header_text}},
        {"type": "section", "text": {"type": "mrkdwn", "text": result.summary}},
    ]

    if result.tasks:
        lines = []
        for t in result.tasks[:10]:
            owner = f" — _{t.owner}_" if t.owner else ""
            due = f" — due {t.due_date.date().isoformat()}" if t.due_date else ""
            lines.append(f"• {t.title}{owner}{due}")
        sections.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Tasks*\n" + "\n".join(lines)},
            }
        )
    if result.decisions:
        lines = [f"• {d.summary}" for d in result.decisions[:6]]
        sections.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Decisions*\n" + "\n".join(lines)},
            }
        )
    if result.blockers:
        lines = [f"• {b.summary}" for b in result.blockers[:6]]
        sections.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Blockers*\n" + "\n".join(lines)},
            }
        )
    return sections
