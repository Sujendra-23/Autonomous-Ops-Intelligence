"""Regenerate the checked-in, deterministic 26-question evaluation dataset."""

import json
from pathlib import Path

TASKS = [
    ["t1", "p1", "open", "high", "Ada", True, "2000-01-01", True],
    ["t2", "p1", "in_progress", "urgent", "bo@example.test", True, None, False],
    ["t3", "p1", "done", "low", "Ada", True, None, False],
    ["t4", "p2", "blocked", "high", None, False, "2000-01-01", True],
    ["t5", "p2", "cancelled", "medium", None, False, None, False],
    ["t6", "p2", "open", "medium", "Cy", True, None, False],
]
RISKS = [
    ["r1", "p1", "open", "high", "high"],
    ["r2", "p1", "closed", "low", "low"],
    ["r3", "p2", "open", "critical", "medium"],
]
BLOCKERS = [
    ["b1", "p1", "t1", "open", "high", None],
    ["b2", "p2", "t4", "open", "critical", None],
    ["b3", "p1", "t2", "resolved", "low", "2026-01-01"],
]
# Precomputed view outputs (the eval fixture stands in for the semantic views, not the tables).
PULL_REQUESTS = [
    [
        "pr1",
        "a/x",
        "merged",
        "Ada",
        True,
        "2026-01-05T09:00",
        "2026-01-05T10:00",
        "2026-01-05T17:00",
        True,
        1.0,
        8.0,
        "2026-01-06T10:00",
        "2026-01-05",
        25.0,
    ],
    [
        "pr2",
        "a/x",
        "merged",
        "bo",
        True,
        "2026-01-06T09:00",
        "2026-01-06T15:00",
        "2026-01-07T09:00",
        True,
        6.0,
        24.0,
        "2026-01-08T09:00",
        "2026-01-05",
        48.0,
    ],
    [
        "pr3",
        "a/x",
        "open",
        "Cy",
        True,
        "2026-01-12T09:00",
        None,
        None,
        False,
        None,
        None,
        None,
        None,
        None,
    ],
    [
        "pr4",
        "a/x",
        "closed",
        None,
        False,
        "2026-01-12T10:00",
        None,
        None,
        False,
        None,
        None,
        None,
        None,
        None,
    ],
]
DEPLOYMENTS = [
    [
        "d1",
        "a/x",
        "production",
        "deploy.yml",
        "success",
        "2026-01-06T09:50",
        "2026-01-06T10:00",
        True,
        False,
        0,
        "2026-01-05",
        None,
        None,
    ],
    [
        "d2",
        "a/x",
        "production",
        "deploy.yml",
        "failure",
        "2026-01-07T09:50",
        "2026-01-07T10:00",
        True,
        True,
        1,
        "2026-01-05",
        "2026-01-08T09:00",
        23.0,
    ],
    [
        "d3",
        "a/x",
        "production",
        "deploy.yml",
        "success",
        "2026-01-08T08:50",
        "2026-01-08T09:00",
        True,
        False,
        0,
        "2026-01-05",
        None,
        None,
    ],
    [
        "d4",
        "a/x",
        "production",
        "deploy.yml",
        "success",
        "2026-01-13T09:50",
        "2026-01-13T10:00",
        True,
        False,
        0,
        "2026-01-12",
        None,
        None,
    ],
    [
        "d5",
        "a/x",
        "staging",
        "deploy.yml",
        "success",
        "2026-01-13T08:50",
        "2026-01-13T09:00",
        False,
        False,
        0,
        "2026-01-12",
        None,
        None,
    ],
]
DORA_WEEKLY = [
    ["a/x", "2026-01-05", 2, 1, 1 / 3, 36.5, 1, 23.0],
    ["a/x", "2026-01-12", 1, 0, 0.0, None, 0, None],
]
CASES = [
    ("How many tasks are there?", "SELECT COUNT(*) FROM analytics.tasks", [[6]]),
    (
        "How many open tasks (including in progress and blocked)?",
        "SELECT COUNT(*) FROM analytics.tasks WHERE status IN ('open', 'in_progress', 'blocked')",
        [[4]],
    ),
    (
        "How many tasks are done?",
        "SELECT COUNT(*) FROM analytics.tasks WHERE status = 'done'",
        [[1]],
    ),
    (
        "How many tasks are overdue?",
        "SELECT COUNT(*) FROM analytics.tasks WHERE is_overdue = TRUE",
        [[2]],
    ),
    (
        "How many tasks have no owner?",
        "SELECT COUNT(*) FROM analytics.tasks WHERE has_owner = FALSE",
        [[2]],
    ),
    (
        "How many tasks are urgent?",
        "SELECT COUNT(*) FROM analytics.tasks WHERE priority = 'urgent'",
        [[1]],
    ),
    (
        "Count tasks by status alphabetically.",
        "SELECT status, COUNT(*) FROM analytics.tasks GROUP BY status ORDER BY status",
        [["blocked", 1], ["cancelled", 1], ["done", 1], ["in_progress", 1], ["open", 2]],
    ),
    (
        "Count tasks by priority alphabetically.",
        "SELECT priority, COUNT(*) FROM analytics.tasks GROUP BY priority ORDER BY priority",
        [["high", 2], ["low", 1], ["medium", 2], ["urgent", 1]],
    ),
    (
        "List distinct task project IDs in order.",
        "SELECT DISTINCT project_id FROM analytics.tasks ORDER BY project_id",
        [["p1"], ["p2"]],
    ),
    (
        "List overdue task IDs in order.",
        "SELECT id FROM analytics.tasks WHERE is_overdue = TRUE ORDER BY id",
        [["t1"], ["t4"]],
    ),
    (
        "Show each assigned task's owner, ordered by task ID.",
        "SELECT id, owner FROM analytics.tasks WHERE has_owner = TRUE ORDER BY id",
        [["t1", "[MASKED]"], ["t2", "[MASKED]"], ["t3", "[MASKED]"], ["t6", "[MASKED]"]],
    ),
    (
        "How many tasks have an owner?",
        "SELECT COUNT(*) FROM analytics.tasks WHERE has_owner = TRUE",
        [[4]],
    ),
    (
        "How many risks are open?",
        "SELECT COUNT(*) FROM analytics.risks WHERE status = 'open'",
        [[2]],
    ),
    (
        "How many risks are critical?",
        "SELECT COUNT(*) FROM analytics.risks WHERE severity = 'critical'",
        [[1]],
    ),
    (
        "Count risks by likelihood alphabetically.",
        "SELECT likelihood, COUNT(*) FROM analytics.risks GROUP BY likelihood ORDER BY likelihood",
        [["high", 1], ["low", 1], ["medium", 1]],
    ),
    (
        "How many blockers are open?",
        "SELECT COUNT(*) FROM analytics.blockers WHERE status = 'open'",
        [[2]],
    ),
    (
        "How many blockers are resolved?",
        "SELECT COUNT(*) FROM analytics.blockers WHERE status = 'resolved'",
        [[1]],
    ),
    (
        "Count blockers by severity alphabetically.",
        "SELECT severity, COUNT(*) FROM analytics.blockers GROUP BY severity ORDER BY severity",
        [["critical", 1], ["high", 1], ["low", 1]],
    ),
    (
        "List task IDs with open blockers, ordered by task ID.",
        "SELECT task_id FROM analytics.blockers WHERE status = 'open' ORDER BY task_id",
        [["t1"], ["t4"]],
    ),
    (
        "How many blockers have no resolution timestamp?",
        "SELECT COUNT(*) FROM analytics.blockers WHERE resolved_at IS NULL",
        [[2]],
    ),
    (
        "How many production deployments succeeded?",
        "SELECT COUNT(*) FROM analytics.deployments "
        "WHERE is_production = TRUE AND status = 'success'",
        [[3]],
    ),
    (
        "Deployment frequency: successful production deployments per week.",
        "SELECT week_start, deployment_count FROM analytics.dora_weekly ORDER BY week_start",
        [["2026-01-05", 2], ["2026-01-12", 1]],
    ),
    (
        "What is the change failure rate of production deployments?",
        "SELECT AVG(failed) FROM analytics.deployments WHERE is_production = TRUE",
        [[0.25]],
    ),
    (
        "What is the average lead time in hours for merged pull requests?",
        "SELECT AVG(lead_time_hours) FROM analytics.pull_requests WHERE is_merged = TRUE",
        [[36.5]],
    ),
    (
        "What is the average time to restore in hours?",
        "SELECT AVG(hours_to_restore) FROM analytics.deployments WHERE is_production = TRUE",
        [[23.0]],
    ),
    (
        "How many pull requests are still open?",
        "SELECT COUNT(*) FROM analytics.pull_requests WHERE state = 'open'",
        [[1]],
    ),
]

if __name__ == "__main__":
    Path(__file__).with_name("analytics.json").write_text(
        json.dumps(
            {
                "fixtures": {
                    "tasks": TASKS,
                    "risks": RISKS,
                    "blockers": BLOCKERS,
                    "pull_requests": PULL_REQUESTS,
                    "deployments": DEPLOYMENTS,
                    "dora_weekly": DORA_WEEKLY,
                },
                "cases": [
                    {"question": q, "sql": sql, "expected": expected} for q, sql, expected in CASES
                ],
            },
            indent=2,
        )
        + "\n"
    )
