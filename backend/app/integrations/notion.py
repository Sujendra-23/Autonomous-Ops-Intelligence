"""Notion integration.

Creates/updates a Notion page per project, and appends a meeting summary +
extracted artefacts each time a transcript is processed.

We use Notion REST directly via httpx rather than the official SDK to keep
dependencies light and async.
"""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.integrations.base import DispatchResult, Notifier, ProjectMirror
from app.logging import get_logger
from app.models.project import Project
from app.models.transcript import Transcript
from app.schemas.extraction import ExtractionResult

logger = get_logger("app.integrations.notion")

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


class NotionAdapter(ProjectMirror, Notifier):
    name = "notion"

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.notion_api_key.get_secret_value()
        self._parent_page_id = s.notion_parent_page_id

    def is_enabled(self) -> bool:
        s = get_settings()
        return s.notion_enabled

    # ------------------------------------------------------------------ #
    # Project mirror                                                     #
    # ------------------------------------------------------------------ #

    async def ensure_project(self, project: Project) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        if project.notion_page_id:
            return DispatchResult(
                self.name, True, detail="exists", external_id=project.notion_page_id
            )

        try:
            page_id = await self._create_project_page(project)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notion.create_project_failed", error=str(exc))
            return DispatchResult(self.name, False, detail=str(exc))

        project.notion_page_id = page_id
        return DispatchResult(
            self.name,
            True,
            external_id=page_id,
            external_url=f"https://www.notion.so/{page_id.replace('-', '')}",
        )

    # ------------------------------------------------------------------ #
    # Notifier                                                           #
    # ------------------------------------------------------------------ #

    async def post_summary(
        self,
        transcript: Transcript,
        project: Project | None,
        result: ExtractionResult,
    ) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        if project is None or project.notion_page_id is None:
            return DispatchResult(
                self.name,
                False,
                detail="no project page to update",
            )

        try:
            await self._append_meeting_block(project.notion_page_id, transcript, result)
            return DispatchResult(self.name, True, external_id=project.notion_page_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("notion.append_failed", error=str(exc))
            return DispatchResult(self.name, False, detail=str(exc))

    # ------------------------------------------------------------------ #
    # HTTP plumbing                                                      #
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _create_project_page(self, project: Project) -> str:
        payload = {
            "parent": {"page_id": self._parent_page_id},
            "properties": {
                "title": [
                    {"type": "text", "text": {"content": project.name}}
                ]
            },
            "children": [
                _heading("Overview", level=1),
                _paragraph(project.description or "No description yet."),
                _heading("Meeting Log", level=2),
            ],
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{_NOTION_API}/pages",
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            return response.json()["id"]

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _append_meeting_block(
        self,
        page_id: str,
        transcript: Transcript,
        result: ExtractionResult,
    ) -> None:
        blocks: list[dict] = [
            _heading(f"📅 {transcript.title}", level=3),
            _paragraph(result.summary),
        ]
        if result.tasks:
            blocks.append(_heading("Tasks", level=3))
            blocks.extend(
                _bullet(
                    f"{t.title}"
                    + (f" — owner: {t.owner}" if t.owner else "")
                    + (
                        f" — due {t.due_date.date().isoformat()}"
                        if t.due_date
                        else ""
                    )
                )
                for t in result.tasks
            )
        if result.decisions:
            blocks.append(_heading("Decisions", level=3))
            blocks.extend(_bullet(d.summary) for d in result.decisions)
        if result.risks:
            blocks.append(_heading("Risks", level=3))
            blocks.extend(
                _bullet(f"[{r.severity}] {r.title}") for r in result.risks
            )
        if result.blockers:
            blocks.append(_heading("Blockers", level=3))
            blocks.extend(_bullet(b.summary) for b in result.blockers)

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.patch(
                f"{_NOTION_API}/blocks/{page_id}/children",
                headers=self._headers(),
                json={"children": blocks},
            )
            response.raise_for_status()


# --------------------------------------------------------------------------- #
# Notion block helpers — keep them out of the adapter body for readability.   #
# --------------------------------------------------------------------------- #


def _heading(text: str, level: int = 1) -> dict:
    block_type = {1: "heading_1", 2: "heading_2", 3: "heading_3"}.get(level, "heading_2")
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
    }


def _paragraph(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
    }


def _bullet(text: str) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": text[:2000]}}]
        },
    }
