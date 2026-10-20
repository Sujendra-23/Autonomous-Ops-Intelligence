"""Notion integration.

Creates/updates a Notion page per project, and appends a meeting summary +
extracted artefacts each time a transcript is processed.

We use Notion REST directly via httpx rather than the official SDK to keep
dependencies light and async.
"""

from __future__ import annotations

import uuid

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.integrations.base import DispatchResult, Notifier, ProjectMirror, TaskMirror
from app.integrations.notion_permissions import NotionAccessError, require_notion_write
from app.logging import get_logger
from app.models.project import Project
from app.models.task import Task
from app.models.transcript import Transcript
from app.schemas.extraction import ExtractionResult

logger = get_logger("app.integrations.notion")

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


class NotionAdapter(ProjectMirror, TaskMirror, Notifier):
    name = "notion"

    @property
    def _parent_page_id(self) -> str:
        return get_settings().notion_parent_page_id

    def is_enabled(self) -> bool:
        s = get_settings()
        return s.notion_enabled

    # ------------------------------------------------------------------ #
    # Project mirror                                                     #
    # ------------------------------------------------------------------ #

    async def ensure_project(self, project: Project) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        require_notion_write(project)
        if project.notion_page_id:
            return DispatchResult(
                self.name, True, detail="exists", external_id=project.notion_page_id
            )

        try:
            page_id = await self._create_project_page(project)
        except Exception as exc:
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

        require_notion_write(transcript, project)
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                await self.check_project_scope(client, project)
            await self._append_meeting_block(project.notion_page_id, transcript, result)
            return DispatchResult(self.name, True, external_id=project.notion_page_id)
        except Exception as exc:
            logger.warning("notion.append_failed", error=str(exc))
            return DispatchResult(self.name, False, detail=str(exc))

    async def check_project_scope(self, client: httpx.AsyncClient, project: Project) -> str:
        require_notion_write(project)
        page_id = str(uuid.UUID(project.notion_page_id or ""))
        response = await client.get(f"{_NOTION_API}/pages/{page_id}", headers=self._headers())
        response.raise_for_status()
        page = response.json()
        parent = page.get("parent", {}).get("page_id", "")
        if (
            page.get("archived")
            or page.get("in_trash")
            or (not parent or uuid.UUID(parent) != uuid.UUID(self._parent_page_id))
        ):
            raise NotionAccessError("Notion project is outside the configured parent page")
        return page_id

    async def check_block_scope(
        self, client: httpx.AsyncClient, task: Task, project: Project
    ) -> dict:
        require_notion_write(task, project)
        page_id = await self.check_project_scope(client, project)
        block_id = str(uuid.UUID(task.notion_block_id or ""))
        response = await client.get(f"{_NOTION_API}/blocks/{block_id}", headers=self._headers())
        response.raise_for_status()
        block = response.json()
        parent = block.get("parent", {}).get("page_id", "")
        if (
            block.get("archived")
            or block.get("in_trash")
            or (not parent or uuid.UUID(parent) != uuid.UUID(page_id))
        ):
            raise NotionAccessError("Notion task is outside its project page")
        if block.get("type") != "to_do" or not isinstance(
            block.get("to_do", {}).get("checked"), bool
        ):
            raise NotionAccessError("Notion task is no longer a checkbox")
        return block

    async def create_task(self, task: Task, project: Project | None) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        require_notion_write(task, project)
        if project is None or not project.notion_page_id:
            return DispatchResult(self.name, False, detail="no project page to update")
        if task.notion_block_id:
            return DispatchResult(
                self.name, True, detail="exists", external_id=task.notion_block_id
            )
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                page_id = await self.check_project_scope(client, project)
                marker = f"[AOI:{task.id}]"
                cursor = None
                # Recover a prior append whose response or database commit was lost.
                while True:
                    response = await client.get(
                        f"{_NOTION_API}/blocks/{page_id}/children",
                        headers=self._headers(),
                        params={"start_cursor": cursor} if cursor else {},
                    )
                    response.raise_for_status()
                    body = response.json()
                    found = next(
                        (
                            b
                            for b in body["results"]
                            if b.get("type") == "to_do"
                            and marker
                            in "".join(
                                t.get("plain_text", t.get("text", {}).get("content", ""))
                                for t in b["to_do"].get("rich_text", [])
                            )
                        ),
                        None,
                    )
                    if found:
                        task.notion_block_id = found["id"]
                        break
                    if not body.get("has_more"):
                        label = task.title
                        if task.owner:
                            label += f" — owner: {task.owner}"
                        if task.due_date:
                            label += f" — due {task.due_date.date().isoformat()}"
                        block = {
                            "object": "block",
                            "type": "to_do",
                            "to_do": {
                                "checked": task.status == "done",
                                "rich_text": [
                                    {
                                        "type": "text",
                                        "text": {"content": f"{label[:1900]} {marker}"},
                                    }
                                ],
                            },
                        }
                        response = await client.patch(
                            f"{_NOTION_API}/blocks/{page_id}/children",
                            headers=self._headers(),
                            json={"children": [block]},
                        )
                        response.raise_for_status()
                        task.notion_block_id = response.json()["results"][0]["id"]
                        break
                    cursor = body["next_cursor"]
                task.notion_sync_pending = True
                task.notion_sync_checked_at = None
            return DispatchResult(self.name, True, external_id=task.notion_block_id)
        except (httpx.HTTPError, NotionAccessError, KeyError, ValueError, TypeError):
            return DispatchResult(
                self.name, False, detail="Notion task mirror failed; check page access"
            )

    # ------------------------------------------------------------------ #
    # HTTP plumbing                                                      #
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {get_settings().notion_api_key.get_secret_value()}",
            "Notion-Version": _NOTION_VERSION,
            "Content-Type": "application/json",
        }

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _create_project_page(self, project: Project) -> str:
        require_notion_write(project)
        payload = {
            "parent": {"page_id": str(uuid.UUID(self._parent_page_id))},
            "properties": {"title": [{"type": "text", "text": {"content": project.name}}]},
            "children": [
                _heading("Overview", level=1),
                _paragraph(project.description or "No description yet."),
                _heading("Meeting Log", level=2),
            ],
        }
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
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
        if result.decisions:
            blocks.append(_heading("Decisions", level=3))
            blocks.extend(_bullet(d.summary) for d in result.decisions)
        if result.risks:
            blocks.append(_heading("Risks", level=3))
            blocks.extend(_bullet(f"[{r.severity}] {r.title}") for r in result.risks)
        if result.blockers:
            blocks.append(_heading("Blockers", level=3))
            blocks.extend(_bullet(b.summary) for b in result.blockers)

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
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
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]},
    }
