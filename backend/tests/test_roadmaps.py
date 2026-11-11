"""Roadmap source isolation, schedule validation, task persistence and command boundaries."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.api import assistant, roadmaps
from app.llm.client import LLMError
from app.models.project import Project
from app.models.task import Task, TaskActivity
from app.models.transcript import Transcript
from app.schemas.roadmap import RoadmapDocument, RoadmapGenerate, RoadmapSave
from app.services import roadmap


def content():
    return {
        "summary": "Ship the onboarding flow.",
        "assumptions": ["Timing is proposed."],
        "milestones": [
            {
                "title": "Prototype",
                "objective": "Validate the first-run experience.",
                "start_week": 1,
                "end_week": 2,
                "owner": None,
                "deliverables": ["Build the prototype", "Review the prototype"],
                "depends_on": [],
                "status": "planned",
            },
            {
                "title": "Launch",
                "objective": "Release the approved experience.",
                "start_week": 3,
                "end_week": 4,
                "owner": "Sam",
                "deliverables": ["Publish the release"],
                "depends_on": [1],
                "status": "planned",
            },
        ],
    }


def document(**changes):
    return RoadmapDocument.model_validate(
        {
            **content(),
            "horizon_weeks": 4,
            "start_date": "2026-11-16",
            "source_type": "manual",
            "source_plan": "Ship onboarding.",
            "source_meeting_ids": [],
            "generated_at": datetime(2026, 11, 11, tzinfo=UTC),
            **changes,
        }
    )


def db(**changes):
    return MagicMock(
        get=AsyncMock(return_value=None),
        scalar=AsyncMock(return_value=None),
        scalars=AsyncMock(),
        commit=AsyncMock(),
        flush=AsyncMock(),
        rollback=AsyncMock(),
        **changes,
    )


@pytest.fixture
def planner(monkeypatch):
    plan = AsyncMock(return_value=json.dumps(content()))
    monkeypatch.setattr(roadmap, "get_llm_client", lambda: SimpleNamespace(plan=plan))
    return plan


async def test_manual_draft_is_redacted_and_does_not_write(planner):
    session = db()
    result = await roadmap.generate_roadmap(
        session,
        RoadmapGenerate(
            name="Onboarding", plan="Ship onboarding; contact alice@example.com", horizon_weeks=4
        ),
    )
    assert [m.title for m in result.milestones] == ["Prototype", "Launch"]
    assert "alice@example.com" not in result.source_plan
    assert "alice@example.com" not in planner.call_args.args[1]
    session.add.assert_not_called()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "change",
    [
        {"horizon_weeks": 2},
        {"milestones": [{**content()["milestones"][0], "start_week": 3, "end_week": 2}]},
        {"milestones": [{**content()["milestones"][0], "depends_on": [1]}]},
        {
            "milestones": [
                content()["milestones"][0],
                {**content()["milestones"][1], "start_week": 1},
            ]
        },
    ],
)
def test_invalid_or_cyclic_schedule_is_rejected(change):
    with pytest.raises(ValidationError):
        document(**change)


@pytest.mark.parametrize("source", ["project", "transcript"])
def test_sources_require_identifiers(source):
    with pytest.raises(ValidationError):
        RoadmapGenerate(name="Launch", source_type=source)


async def test_transcript_scope_content_and_completion(planner):
    project = Project(id=uuid.uuid4(), name="Launch", slug="launch")
    meeting = Transcript(
        id=uuid.uuid4(),
        title="Planning",
        content="Prototype then launch.",
        status="completed",
        project_id=project.id,
    )
    session = db()
    session.get.side_effect = [project, meeting]
    request = RoadmapGenerate(
        name=project.name,
        project_id=project.id,
        source_type="transcript",
        transcript_id=meeting.id,
        horizon_weeks=4,
    )
    result = await roadmap.generate_roadmap(session, request)
    assert result.source_meeting_ids == [meeting.id]
    assert meeting.content in planner.call_args.args[1]
    for status, project_id in [("live", project.id), ("completed", uuid.uuid4())]:
        meeting.status = status
        meeting.project_id = project_id
        session.get.side_effect = [project, meeting]
        planner.reset_mock()
        with pytest.raises(HTTPException) as error:
            await roadmap.generate_roadmap(session, request)
        assert error.value.status_code == 422
        planner.assert_not_awaited()


async def test_missing_project_is_not_disclosed(planner):
    with pytest.raises(HTTPException) as error:
        await roadmap.generate_roadmap(
            db(), RoadmapGenerate(name="Private", project_id=uuid.uuid4(), source_type="project")
        )
    assert error.value.status_code == 404
    planner.assert_not_awaited()


async def test_project_context_is_bounded_and_completed_only(planner):
    project = Project(id=uuid.uuid4(), name="Launch", slug="launch")
    meeting = Transcript(
        id=uuid.uuid4(),
        title="Planning",
        content="word " * 2000,
        status="completed",
        project_id=project.id,
    )
    session = db()
    session.get.return_value = project
    session.scalars.return_value = SimpleNamespace(all=lambda: [meeting])
    await roadmap.generate_roadmap(
        session,
        RoadmapGenerate(
            name="Launch", project_id=project.id, source_type="project", horizon_weeks=4
        ),
    )
    sql = str(session.scalars.call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 5" in sql and "completed" in sql
    prompt = json.loads(planner.call_args.args[1])
    assert prompt["meetings"][0]["truncated"] is True
    assert len(prompt["meetings"][0]["content"]) == 6000


async def test_invalid_output_is_rejected_and_proposals_cannot_claim_completion(planner):
    planner.return_value = '{"milestones": []}'
    session = db()
    request = RoadmapGenerate(
        name="Launch", plan="Build a prototype and release it.", horizon_weeks=4
    )
    with pytest.raises(LLMError):
        await roadmap.generate_roadmap(session, request)
    session.commit.assert_not_awaited()
    output = content()
    output["milestones"][0].update(status="done", task_ids=[str(uuid.uuid4())])
    planner.return_value = json.dumps(output)
    result = await roadmap.generate_roadmap(session, request)
    assert result.milestones[0].status == "planned" and result.milestones[0].task_ids == []


async def test_save_creates_real_project_tasks_once():
    session = db()
    first = await roadmaps.save(
        RoadmapSave(name="Launch", roadmap=document(), create_tasks=True), session
    )
    tasks = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], Task)]
    projects = [
        call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], Project)
    ]
    assert first.created_task_count == 3 and first.version == 1
    assert [task.title for task in tasks] == [
        "Build the prototype",
        "Review the prototype",
        "Publish the release",
    ]
    assert all(task.project_id == first.project_id and task.source == "local" for task in tasks)
    assert tasks[2].owner == "Sam" and tasks[2].due_date.date().isoformat() == "2026-12-13"
    assert len([c for c in session.add.call_args_list if isinstance(c.args[0], TaskActivity)]) == 3
    lookup = {p.id: p for p in projects} | {task.id: task for task in tasks}
    session.get.side_effect = lambda model, identifier, **kw: lookup.get(identifier)
    session.add.reset_mock()
    second = await roadmaps.save(
        RoadmapSave(
            project_id=first.project_id,
            name="Launch",
            expected_version=1,
            roadmap=first.roadmap,
            create_tasks=True,
        ),
        session,
    )
    assert second.created_task_count == 0 and second.version == 2
    session.add.assert_not_called()


async def test_stale_save_and_cross_project_task_links_are_rejected():
    session = db()
    project = Project(id=uuid.uuid4(), name="Launch", slug="launch", roadmap_version=2)
    session.get.return_value = project
    with pytest.raises(HTTPException) as error:
        await roadmaps.save(
            RoadmapSave(
                project_id=project.id,
                name="Launch",
                expected_version=1,
                roadmap=document(),
                create_tasks=True,
            ),
            session,
        )
    assert error.value.status_code == 409
    session.commit.assert_not_awaited()
    task = Task(id=uuid.uuid4(), project_id=uuid.uuid4(), source="local")
    session.get.side_effect = [project, task]
    draft = document()
    draft.milestones[0].task_ids = [task.id]
    with pytest.raises(HTTPException) as error:
        await roadmaps.save(
            RoadmapSave(
                project_id=project.id,
                name="Launch",
                expected_version=2,
                roadmap=draft,
                create_tasks=True,
            ),
            session,
        )
    assert error.value.status_code == 422
    session.commit.assert_not_awaited()


async def test_initial_flush_unique_conflict_is_rolled_back():
    session = db()
    session.flush.side_effect = IntegrityError("insert", {}, Exception())
    with pytest.raises(HTTPException) as error:
        await roadmaps.save(
            RoadmapSave(name="Launch", roadmap=document(), create_tasks=True), session
        )
    assert error.value.status_code == 409
    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "click", "target": "unknown"},
        {"kind": "fill", "target": "c0", "value": "wrong"},
        {"kind": "select", "target": "c0", "value": "invented"},
        {"kind": "navigate", "target": "external-site"},
    ],
)
async def test_voice_rejects_invalid_controls(action, monkeypatch):
    plan = AsyncMock(
        return_value=json.dumps({"message": "Working", "actions": [action], "done": False})
    )
    monkeypatch.setattr(assistant, "get_llm_client", lambda: SimpleNamespace(plan=plan))
    payload = assistant.Command(
        utterance="Create a roadmap",
        view="roadmaps",
        page_text="",
        controls=[assistant.Control(id="c0", role="button", label="Generate roadmap")],
    )
    with pytest.raises(HTTPException) as error:
        await assistant.command(payload)
    assert error.value.status_code == 422


async def test_voice_can_fill_goal_and_submit_current_form(monkeypatch):
    plan = AsyncMock(
        return_value=json.dumps(
            {
                "message": "Creating your draft",
                "actions": [
                    {
                        "kind": "fill",
                        "target": "c0",
                        "value": "Ship onboarding. Prototype then launch.",
                    },
                    {"kind": "click", "target": "c1"},
                ],
                "done": False,
            }
        )
    )
    monkeypatch.setattr(assistant, "get_llm_client", lambda: SimpleNamespace(plan=plan))
    result = await assistant.command(
        assistant.Command(
            utterance="Create this goal",
            view="roadmaps",
            page_text="",
            controls=[
                assistant.Control(id="c0", role="textarea", label="Your goal"),
                assistant.Control(id="c1", role="button", label="Generate roadmap"),
            ],
        )
    )
    assert len(result.actions) == 2 and plan.call_args.kwargs["operation"] == "assistant"


async def test_roadmap_writes_and_speech_planning_require_api_key(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from pydantic import SecretStr

    from app.api import deps
    from app.config import Settings

    monkeypatch.setattr(
        deps, "get_settings", lambda: Settings(_env_file=None, ingest_api_key=SecretStr("required"))
    )
    app = FastAPI()
    app.include_router(roadmaps.router, prefix="/api/projects")
    app.include_router(assistant.router, prefix="/api/assistant")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in (
            "/api/projects/roadmap",
            "/api/projects/roadmap/generate",
            "/api/assistant/command",
        ):
            assert (await client.post(path, json={})).status_code == 401


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
async def test_provider_planning_uses_bounded_json_and_rejects_truncated_output(provider):
    from app.llm.client import AnthropicClient, OpenAIClient

    if provider == "openai":
        response = SimpleNamespace(
            usage=None,
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content='{"ok":true}')
                )
            ],
        )
        create = AsyncMock(return_value=response)
        client = object.__new__(OpenAIClient)
        options = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    else:
        response = SimpleNamespace(
            usage=None, stop_reason="end_turn", content=[SimpleNamespace(text='{"ok":true}')]
        )
        create = AsyncMock(return_value=response)
        client = object.__new__(AnthropicClient)
        options = SimpleNamespace(messages=SimpleNamespace(create=create))
    client._client = MagicMock(with_options=MagicMock(return_value=options))
    client._model = "test-model"
    assert await client.plan("Return JSON", "alice@example.com") == '{"ok":true}'
    assert "alice@example.com" not in create.call_args.kwargs["messages"][-1]["content"]
    assert create.call_args.kwargs["max_tokens"] == 6000
    client._client.with_options.assert_called_with(timeout=60, max_retries=1)
    if provider == "openai":
        response.choices[0].finish_reason = "length"
    else:
        response.stop_reason = "max_tokens"
    with pytest.raises(LLMError):
        await client.plan("Return JSON", "test")
