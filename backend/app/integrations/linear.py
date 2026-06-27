"""Linear integration via its GraphQL API.

We create an issue per extracted task, link it back to the task row, and
include the source quote in the description so the assignee can audit how
the LLM arrived at the assignment.
"""

from __future__ import annotations

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.integrations.base import DispatchResult, TaskMirror
from app.logging import get_logger
from app.models.project import Project
from app.models.task import Task

logger = get_logger("app.integrations.linear")

_LINEAR_GQL = "https://api.linear.app/graphql"


class LinearAdapter(TaskMirror):
    name = "linear"

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.linear_api_key.get_secret_value()
        self._team_id = s.linear_team_id

    def is_enabled(self) -> bool:
        return get_settings().linear_enabled

    async def create_task(self, task: Task, project: Project | None) -> DispatchResult:
        if not self.is_enabled():
            return DispatchResult(self.name, False, detail="disabled")
        if task.linear_issue_id:
            return DispatchResult(self.name, True, detail="exists")

        description = _format_description(task, project)
        priority = {"urgent": 1, "high": 2, "medium": 3, "low": 4}.get(task.priority, 3)

        mutation = """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier url }
          }
        }
        """
        variables = {
            "input": {
                "teamId": self._team_id,
                "title": task.title[:255],
                "description": description,
                "priority": priority,
            }
        }
        if task.due_date is not None:
            variables["input"]["dueDate"] = task.due_date.date().isoformat()

        try:
            data = await self._gql(mutation, variables)
        except Exception as exc:  # noqa: BLE001
            logger.warning("linear.create_failed", error=str(exc))
            return DispatchResult(self.name, False, detail=str(exc))

        payload = data.get("issueCreate", {})
        if not payload.get("success"):
            return DispatchResult(self.name, False, detail="Linear reported failure")
        issue = payload["issue"]
        task.linear_issue_id = issue["id"]
        task.linear_issue_url = issue["url"]
        return DispatchResult(
            self.name,
            True,
            external_id=issue["id"],
            external_url=issue["url"],
            detail=issue["identifier"],
        )

    @retry(reraise=True, stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _gql(self, query: str, variables: dict) -> dict:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                _LINEAR_GQL,
                headers={"Authorization": self._api_key, "Content-Type": "application/json"},
                json={"query": query, "variables": variables},
            )
            response.raise_for_status()
            body = response.json()
        if body.get("errors"):
            raise RuntimeError(str(body["errors"]))
        return body.get("data") or {}


def _format_description(task: Task, project: Project | None) -> str:
    parts: list[str] = []
    if task.description:
        parts.append(task.description)
    parts.append("---")
    parts.append("**Source quote**")
    parts.append(f"> {task.source_quote.strip()}" if task.source_quote else "_(no quote)_")
    if task.owner:
        parts.append(f"**Inferred owner:** {task.owner}")
    if project:
        parts.append(f"**Project:** {project.name}")
    parts.append("_Auto-created by the Autonomous Operational Intelligence Layer._")
    return "\n\n".join(parts)
