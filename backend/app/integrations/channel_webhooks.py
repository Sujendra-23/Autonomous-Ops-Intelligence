"""Meeting summaries via Discord channel webhooks and Teams Workflows.

Webhook URLs are credentials. Never include them or response bodies in errors
returned to the dispatcher. Delivery follows the existing best-effort notifier
contract; these adapters do not use the generic signed-webhook outbox.
"""

from __future__ import annotations

import httpx

from app.config import get_settings
from app.integrations.base import DispatchResult, Notifier
from app.models.project import Project
from app.models.transcript import Transcript
from app.schemas.extraction import ExtractionResult


def summary_text(
    transcript: Transcript, project: Project | None, result: ExtractionResult, limit: int
) -> str:
    """Bound provider payloads while including all extracted item categories."""
    heading = transcript.title[:300]
    if project:
        heading += f" · {project.name[:150]}"
    parts = [heading, result.summary[:1200]]
    tasks = []
    for task in result.tasks[:10]:
        owner = f" — {task.owner[:100]}" if task.owner else ""
        due = f" — due {task.due_date.date().isoformat()}" if task.due_date else ""
        tasks.append(f"- {task.title[:250]}{owner}{due}")
    for label, lines in (
        ("Tasks", tasks),
        ("Decisions", [f"- {item.summary[:250]}" for item in result.decisions[:6]]),
        ("Risks", [f"- {item.title[:250]}" for item in result.risks[:6]]),
        ("Blockers", [f"- {item.summary[:250]}" for item in result.blockers[:6]]),
    ):
        if lines:
            parts.append(label + "\n" + "\n".join(lines))
    text = "\n\n".join(parts)
    if len(text) > limit:
        suffix = "\n… More items in AOI."
        text = text[: limit - len(suffix)] + suffix
    return text


class ChannelWebhookAdapter(Notifier):
    setting_name: str
    limit: int

    def __init__(self) -> None:
        self._url = getattr(get_settings(), self.setting_name).get_secret_value()

    def is_enabled(self) -> bool:
        return bool(self._url)

    def payload(self, text: str) -> dict:
        raise NotImplementedError

    async def post_summary(
        self,
        transcript: Transcript,
        project: Project | None,
        result: ExtractionResult,
    ) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        text = summary_text(transcript, project, result, self.limit)
        url = httpx.URL(self._url)
        if self.name == "discord":
            # wait=true confirms message creation, preserving any thread_id.
            url = url.copy_merge_params({"wait": "true"})
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
                response = await client.post(url, json=self.payload(text))
                response.raise_for_status()
            external_id = None
            if self.name == "discord":
                try:
                    body = response.json()
                    external_id = body.get("id") if isinstance(body, dict) else None
                except ValueError:
                    pass
                if not external_id:
                    return DispatchResult(self.name, False, detail="Missing message confirmation")
            return DispatchResult(self.name, True, external_id=external_id)
        except httpx.HTTPStatusError as exc:
            return DispatchResult(self.name, False, detail=f"HTTP {exc.response.status_code}")
        except httpx.HTTPError:
            return DispatchResult(self.name, False, detail="Network error")


class DiscordAdapter(ChannelWebhookAdapter):
    name = "discord"
    setting_name = "discord_webhook_url"
    limit = 2000

    def payload(self, text: str) -> dict:
        return {"content": text, "allowed_mentions": {"parse": []}}


class TeamsAdapter(ChannelWebhookAdapter):
    name = "teams"
    setting_name = "teams_webhook_url"
    limit = 5000

    def payload(self, text: str) -> dict:
        return {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": {
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard",
                        "version": "1.2",
                        "body": [{"type": "TextBlock", "text": text, "wrap": True}],
                    },
                }
            ],
        }
