"""Resolve a project for a transcript.

Given a `project_hint` (either a slug or a human name) we either find the
existing project or create one. This keeps the rest of the pipeline blissfully
unaware of how projects come into existence.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:64] or "project"


async def get_or_create_project(
    session: AsyncSession,
    name: str | None,
) -> Project | None:
    """Find or create a project by name; return None if `name` is empty."""
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    slug = slugify(name)

    result = await session.execute(
        select(Project).where((Project.slug == slug) | (Project.name == name))
    )
    project = result.scalars().first()
    if project:
        return project

    project = Project(name=name, slug=slug)
    session.add(project)
    await session.flush()
    return project
