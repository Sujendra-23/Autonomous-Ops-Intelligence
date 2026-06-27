"""SQLAlchemy ORM models.

All models inherit from `app.database.Base`. Importing this package registers
every model with SQLAlchemy's metadata, which Alembic relies on for autogenerate.
"""

from app.models.blocker import Blocker
from app.models.decision import Decision
from app.models.project import Project
from app.models.risk import Risk
from app.models.task import Task, TaskActivity
from app.models.transcript import Transcript, TranscriptChunk

__all__ = [
    "Blocker",
    "Decision",
    "Project",
    "Risk",
    "Task",
    "TaskActivity",
    "Transcript",
    "TranscriptChunk",
]
