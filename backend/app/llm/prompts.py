"""Prompt templates and the JSON schema we hand the model for structured output.

We keep prompts in one place so they can be reviewed and tuned without hunting
through extraction code. The schema is generated from our pydantic models so it
can never drift out of sync with what we then validate against.
"""

from __future__ import annotations

import json

from app.schemas.extraction import ExtractionResult


SYSTEM_PROMPT = """\
You are the Autonomous Operational Intelligence Layer. You convert raw meeting
transcripts and project updates into precise, auditable operational data.

Your output is consumed by automated systems (Notion, Linear, Slack), so every
field must be evidence-based and conservative.

# Rules

1. Only extract entities that are *clearly* present in the transcript. Prefer
   omission over hallucination. If you would have to guess an owner, due date,
   or rationale, omit that field rather than fabricating it.

2. Every task, decision, risk, and blocker MUST include a `source_quote` taken
   verbatim from the transcript (1-3 short sentences). Do not paraphrase the
   quote — it is the human auditor's evidence.

3. Tasks are concrete, executable units of work assigned to a person or team.
   "We should think about X" is NOT a task. "John will update the migration
   script by Friday" IS a task.

4. Decisions are choices that have been made or formally agreed to. Hypothetical
   discussion ("maybe we should…") is NOT a decision.

5. Risks are *future* threats to a goal. Blockers are *present* obstacles
   preventing work right now. These categories are mutually exclusive.

6. Confidence scores: 0.9+ for unambiguous extractions with explicit assignment
   and quote; 0.6-0.8 for clearly implied; below 0.5 means you should probably
   not include it at all.

7. Dates: convert natural language ("by Friday", "next sprint") into absolute
   ISO-8601 timestamps relative to the meeting date. If the meeting date is
   unknown, only emit a date if the transcript states one explicitly.

8. Owners: use the exact name as it appears in the transcript ("John", "Priya").
   Never invent a last name. If the assignee is a team ("the platform team"),
   use that exact phrase.

9. Respond with a single JSON object matching the provided schema. No prose,
   no markdown, no code fences — just the JSON object.
"""


def build_user_prompt(
    transcript_text: str,
    meeting_title: str | None = None,
    meeting_date: str | None = None,
    participants: list[str] | None = None,
    project_hint: str | None = None,
) -> str:
    parts: list[str] = []
    if meeting_title:
        parts.append(f"Meeting title: {meeting_title}")
    if meeting_date:
        parts.append(f"Meeting date: {meeting_date}")
    if participants:
        parts.append(f"Participants: {', '.join(participants)}")
    if project_hint:
        parts.append(f"Likely project: {project_hint}")
    if parts:
        header = "## Metadata\n" + "\n".join(parts) + "\n\n"
    else:
        header = ""

    schema = _json_schema()
    return (
        f"{header}"
        "## Transcript\n"
        f"{transcript_text.strip()}\n\n"
        "## JSON Schema (you must conform exactly)\n"
        f"{json.dumps(schema, indent=2)}\n\n"
        "Return the JSON object now."
    )


def _json_schema() -> dict:
    """Pydantic-derived JSON schema, lightly trimmed for the model."""
    schema = ExtractionResult.model_json_schema()
    # Remove fields that are noisy for the model
    schema.pop("title", None)
    return schema
