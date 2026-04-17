from __future__ import annotations

import sys
from pathlib import Path

from config.settings import Settings
from team.models import BudgetConfig, Task, TaskStatus, TeamDefinition
from team.persistence.events import make_note_posted, make_task_added, make_team_run_created, task_to_dict

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS_DIR = _ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from prompt_helpers import (  # noqa: E402
    build_team_run_user_prompt_report_text_sync,
    build_team_user_prompt_report_text_sync,
    default_team_run_prompt_report_path,
    default_team_user_prompt_report_path,
    register_builtins,
)


def test_build_team_user_prompt_report_uses_runtime_context_path(tmp_path: Path) -> None:
    register_builtins()
    team_def = TeamDefinition(
        id="team-12345678",
        name="demo team",
        description="demo",
        entry_planner="team_planner",
        roster={
            "planner": ["team_planner"],
            "developer": ["developer"],
            "task_center_note_taker": ["note_taker"],
        },
    )

    report, missing = build_team_user_prompt_report_text_sync(
        team_def,
        user_request="Fix the login retry behavior.",
        cwd=str(tmp_path),
        settings=Settings(),
    )

    assert missing == []
    assert "# Team User Prompts: demo team" in report
    assert "- Source: representative synthetic task graph rendered through `build_query_context`." in report
    assert "## Agent: team_planner" in report
    assert "## Available Agents" not in report
    assert "Fix the login retry behavior." in report
    assert "## Agent: developer" in report
    assert "Goal\nImplement the bounded code change" in report
    assert "Acceptance Criteria\n- Keep edits inside the assigned scope." in report
    assert "## Agent: note_taker" in report
    assert "### Edit Trigger" in report
    assert "Call submit_task_note" in report


def test_default_team_user_prompt_report_path_uses_team_prefix() -> None:
    team_def = TeamDefinition(
        id="abcdef123456",
        name="Demo Team",
        description="demo",
        entry_planner="team_planner",
    )

    path = default_team_user_prompt_report_path(team_def, output_dir="/tmp")

    assert str(path) == "/tmp/team-user-prompts-Demo-Team-abcdef12.md"


def test_build_team_run_user_prompt_report_replays_persisted_tasks(tmp_path: Path) -> None:
    register_builtins()
    root = Task(
        id="root",
        team_run_id="run-1",
        agent_name="team_planner",
        status=TaskStatus.DONE,
        objective="Fix retry behavior.",
        root_id="root",
        depth=0,
    )
    dev = Task(
        id="dev-1",
        team_run_id="run-1",
        agent_name="developer",
        status=TaskStatus.READY,
        objective="Implement the retry fix.",
        deps=["root"],
        scope_paths=["backend/src/retry.py"],
        parent_id="root",
        root_id="root",
        depth=1,
    )
    events = [
        make_team_run_created(
            "run-1",
            session_id="session-1",
            user_request="Fix retry behavior.",
            goal=None,
            repo_root=str(tmp_path),
            budgets=BudgetConfig().__dict__,
            roster={
                "planner": ["team_planner"],
                "developer": ["developer"],
            },
        ),
        make_task_added("run-1", task_to_dict(root)),
        make_task_added("run-1", task_to_dict(dev)),
        make_note_posted(
            "run-1",
            task_id="root",
            agent_name="team_planner",
            auto=False,
            scope_paths=["backend/src/retry.py"],
            content_preview="Planner assigned retry implementation.",
            content_bytes=39,
        ),
    ]

    report, missing = build_team_run_user_prompt_report_text_sync(
        team_run_id="run-1",
        events=events,
        cwd=str(tmp_path),
        settings=Settings(),
    )

    assert missing == []
    assert "# Team Run User Prompts: run-1" in report
    assert "- Task count: `2`" in report
    assert "### Task: dev-1" in report
    assert "Implement the retry fix." in report
    assert "## Context from dependencies" in report
    assert "Planner assigned retry implementation." in report


def test_default_team_run_prompt_report_path_uses_run_prefix() -> None:
    path = default_team_run_prompt_report_path("run/with spaces", output_dir="/tmp")

    assert str(path) == "/tmp/team-run-user-prompts-run-with-spaces.md"
