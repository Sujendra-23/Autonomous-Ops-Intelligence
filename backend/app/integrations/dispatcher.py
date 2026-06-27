"""Fan-out dispatcher.

The extraction pipeline calls `IntegrationDispatcher.publish` after a
transcript has been processed. The dispatcher:

1. Asks the project mirrors (Notion) to ensure the project page exists.
2. Asks task mirrors (Linear or Jira) to create issues for new tasks.
3. Asks notifiers (Slack, Notion) to publish a human-facing summary.

Each adapter is responsible for short-circuiting when disabled, so the
dispatcher's only job is to orchestrate, swallow per-adapter failures, and
keep going. Failures are logged but don't roll back the database — the
extraction is still valuable even if Slack is briefly down.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.base import DispatchResult, Notifier, ProjectMirror, TaskMirror
from app.integrations.jira import JiraAdapter
from app.integrations.linear import LinearAdapter
from app.integrations.notion import NotionAdapter
from app.integrations.slack import SlackAdapter
from app.logging import get_logger
from app.models.project import Project
from app.models.task import Task, TaskActivity
from app.models.transcript import Transcript
from app.schemas.extraction import ExtractionResult

logger = get_logger("app.integrations.dispatcher")


class IntegrationDispatcher:
    def __init__(
        self,
        *,
        project_mirrors: list[ProjectMirror] | None = None,
        task_mirrors: list[TaskMirror] | None = None,
        notifiers: list[Notifier] | None = None,
    ) -> None:
        self._project_mirrors: list[ProjectMirror] = project_mirrors or [NotionAdapter()]
        # Linear takes precedence over Jira if both are configured.
        self._task_mirrors: list[TaskMirror] = task_mirrors or [
            LinearAdapter(),
            JiraAdapter(),
        ]
        self._notifiers: list[Notifier] = notifiers or [SlackAdapter(), NotionAdapter()]

    async def publish(
        self,
        session: AsyncSession,
        transcript: Transcript,
        project: Project | None,
        result: ExtractionResult,
    ) -> list[DispatchResult]:
        outcomes: list[DispatchResult] = []

        if project is not None:
            for mirror in self._project_mirrors:
                if not mirror.is_enabled():
                    continue
                outcomes.append(await self._safe(mirror.ensure_project, project))
            await session.commit()

        # Re-load the tasks emitted for this transcript so we can attach
        # external identifiers as the mirrors return them.
        task_rows = (
            await session.execute(
                select(Task).where(Task.transcript_id == transcript.id)
            )
        ).scalars().all()

        for task in task_rows:
            for mirror in self._task_mirrors:
                if not mirror.is_enabled():
                    continue
                outcome = await self._safe(mirror.create_task, task, project)
                outcomes.append(outcome)
                if outcome.success and outcome.external_id:
                    session.add(
                        TaskActivity(
                            task_id=task.id,
                            kind="external_mirror_created",
                            payload={
                                "adapter": outcome.adapter,
                                "external_id": outcome.external_id,
                                "external_url": outcome.external_url,
                            },
                        )
                    )
                    # Only mirror to the first enabled task tool — avoid
                    # creating both a Linear and a Jira ticket for one task.
                    break
        await session.commit()

        for notifier in self._notifiers:
            if not notifier.is_enabled():
                continue
            outcomes.append(
                await self._safe(notifier.post_summary, transcript, project, result)
            )

        return outcomes

    @staticmethod
    async def _safe(fn, *args, **kwargs) -> DispatchResult:
        try:
            result = await fn(*args, **kwargs)
            if not isinstance(result, DispatchResult):
                return DispatchResult(getattr(fn, "__qualname__", "?"), True)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("dispatcher.adapter_failed", fn=fn.__qualname__, error=str(exc))
            return DispatchResult(getattr(fn, "__qualname__", "?"), False, detail=str(exc))
