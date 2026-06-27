"""Cross-meeting context — give the extractor memory of the project so far.

The extraction LLM call is otherwise stateless: it sees one transcript and
nothing else, so every meeting re-creates tasks, re-reports the same blocker,
and never notices that "John finished the migration" should *close* an existing
item rather than open a new one.

``build_prior_context`` gathers the still-relevant state for a project — open
tasks, recent decisions, open blockers — and renders it into a compact prompt
block. Each item is tagged with its database id so the model can reference it
back in ``task_updates`` / ``decision.supersedes`` (see ``schemas.extraction``).

The block is intentionally bounded (a handful of each kind, newest first) so it
never crowds out the transcript itself or blows the context window on a
long-running project.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.blocker import Blocker
from app.models.decision import Decision
from app.models.task import Task

_ACTIVE_TASK_STATUSES = ("open", "in_progress", "blocked")


@dataclass
class PriorContext:
    open_tasks: list[Task] = field(default_factory=list)
    recent_decisions: list[Decision] = field(default_factory=list)
    open_blockers: list[Blocker] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.open_tasks or self.recent_decisions or self.open_blockers)

    def to_prompt_block(self) -> str:
        """Render the context as the markdown block injected into the user prompt."""
        lines: list[str] = [
            "## Existing project state (continuity — do NOT re-create these)",
            "",
            "This is a NEW meeting for a project that already has tracked work below.",
            "Use it to:",
            "  - emit a `task_updates` entry (referencing the exact `id`) when this",
            "    meeting reports progress/completion of an existing task, instead of",
            "    creating a duplicate task;",
            "  - avoid re-emitting tasks, decisions, or blockers that already exist;",
            "  - set `supersedes` on a decision that reverses one listed here.",
            "",
        ]

        if self.open_tasks:
            lines.append("### Open tasks")
            for t in self.open_tasks:
                due = t.due_date.date().isoformat() if t.due_date else "no due date"
                lines.append(
                    f'- [id={t.id}] "{t.title}" '
                    f"(owner: {t.owner or 'unassigned'}, status: {t.status}, due: {due})"
                )
            lines.append("")

        if self.recent_decisions:
            lines.append("### Recent decisions")
            for d in self.recent_decisions:
                lines.append(f'- [id={d.id}] "{d.summary}"')
            lines.append("")

        if self.open_blockers:
            lines.append("### Open blockers")
            for b in self.open_blockers:
                lines.append(
                    f'- "{b.summary}" '
                    f"(blocked: {b.blocked_party or 'unknown'}, "
                    f"needs: {b.needs_from or 'unknown'})"
                )
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"


async def build_prior_context(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    max_tasks: int = 25,
    max_decisions: int = 10,
    max_blockers: int = 10,
) -> PriorContext:
    """Load the still-relevant state for ``project_id`` to feed the extractor."""
    open_tasks = (
        await session.execute(
            select(Task)
            .where(
                Task.project_id == project_id,
                Task.status.in_(_ACTIVE_TASK_STATUSES),
            )
            .order_by(Task.created_at.desc())
            .limit(max_tasks)
        )
    ).scalars().all()

    recent_decisions = (
        await session.execute(
            select(Decision)
            .where(Decision.project_id == project_id)
            .order_by(Decision.created_at.desc())
            .limit(max_decisions)
        )
    ).scalars().all()

    open_blockers = (
        await session.execute(
            select(Blocker)
            .where(
                Blocker.project_id == project_id,
                Blocker.status == "open",
            )
            .order_by(Blocker.created_at.desc())
            .limit(max_blockers)
        )
    ).scalars().all()

    return PriorContext(
        open_tasks=list(open_tasks),
        recent_decisions=list(recent_decisions),
        open_blockers=list(open_blockers),
    )
