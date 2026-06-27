"""Base classes and protocols for integration adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models.project import Project
from app.models.task import Task
from app.models.transcript import Transcript
from app.schemas.extraction import ExtractionResult


@dataclass
class DispatchResult:
    adapter: str
    success: bool
    detail: str | None = None
    external_id: str | None = None
    external_url: str | None = None


class Adapter(ABC):
    name: str = "base"

    @abstractmethod
    def is_enabled(self) -> bool:
        ...


class ProjectMirror(Adapter):
    """Adapters that mirror a `Project` into an external system."""

    @abstractmethod
    async def ensure_project(self, project: Project) -> DispatchResult:
        ...


class TaskMirror(Adapter):
    """Adapters that create issues/tickets out of `Task` rows."""

    @abstractmethod
    async def create_task(self, task: Task, project: Project | None) -> DispatchResult:
        ...

    async def remind_task(self, task: Task) -> DispatchResult:
        return DispatchResult(self.name, False, detail="not implemented")


class Notifier(Adapter):
    """Adapters that post human-facing summaries of a transcript."""

    @abstractmethod
    async def post_summary(
        self,
        transcript: Transcript,
        project: Project | None,
        result: ExtractionResult,
    ) -> DispatchResult:
        ...
