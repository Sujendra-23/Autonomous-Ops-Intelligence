"""Jira Cloud integration as an alternative to Linear."""

from __future__ import annotations

import base64

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.integrations.base import DispatchResult, TaskMirror
from app.logging import get_logger
from app.models.project import Project
from app.models.task import Task

logger = get_logger("app.integrations.jira")


class JiraAdapter(TaskMirror):
    name = "jira"

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.jira_base_url.rstrip("/")
        self._email = s.jira_email
        self._token = s.jira_api_token.get_secret_value()
        self._project = s.jira_project_key

    def is_enabled(self) -> bool:
        return get_settings().jira_enabled

    async def create_task(self, task: Task, project: Project | None) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        if task.jira_issue_key:
            return DispatchResult(self.name, True, detail="exists")

        priority = {"urgent": "Highest", "high": "High", "medium": "Medium", "low": "Low"}
        payload = {
            "fields": {
                "project": {"key": self._project},
                "summary": task.title[:255],
                "description": _adf(task, project),
                "issuetype": {"name": "Task"},
                "priority": {"name": priority.get(task.priority, "Medium")},
            }
        }
        if task.due_date:
            payload["fields"]["duedate"] = task.due_date.date().isoformat()

        try:
            data = await self._post("/rest/api/3/issue", payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("jira.create_failed", error=str(exc))
            return DispatchResult(self.name, False, detail=str(exc))

        key = data.get("key")
        url = f"{self._base}/browse/{key}" if key else None
        if key:
            task.jira_issue_key = key
            task.jira_issue_url = url
        return DispatchResult(self.name, bool(key), external_id=key, external_url=url)

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _post(self, path: str, payload: dict) -> dict:
        creds = base64.b64encode(f"{self._email}:{self._token}".encode()).decode()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self._base}{path}",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()


def _adf(task: Task, project: Project | None) -> dict:
    """Atlassian Document Format — minimal representation of a description."""
    paragraphs: list[dict] = []
    if task.description:
        paragraphs.append(_p(task.description))
    if task.source_quote:
        paragraphs.append(_p(f"Source quote: \"{task.source_quote.strip()}\""))
    if task.owner:
        paragraphs.append(_p(f"Inferred owner: {task.owner}"))
    if project:
        paragraphs.append(_p(f"Project: {project.name}"))
    paragraphs.append(_p("Auto-created by the Autonomous Operational Intelligence Layer."))
    return {"version": 1, "type": "doc", "content": paragraphs}


def _p(text: str) -> dict:
    return {
        "type": "paragraph",
        "content": [{"type": "text", "text": text[:2000]}],
    }
