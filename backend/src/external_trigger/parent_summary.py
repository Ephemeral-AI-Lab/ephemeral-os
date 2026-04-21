"""Parent-summary external trigger.

Spawns an ephemeral ``parent_summarizer`` agent to generate a Task Center
summary for a just-completed expandable (planner/replanner) task based on
its children's terminal statuses and notes. Mirrors the shape of
``tc_note.py`` and posts the result via
``task_center.notes.post`` with tags ``["implementation", "parent_summary"]``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.registry import get_definition
from external_trigger.runner import run
from team.models import Note
from tools.core.base import ToolExecutionContext
from tools.submission.toolkit import SubmitTaskSummaryInput, SubmitTaskSummaryTool
from tools.task_center.toolkit import ReadTaskDetailsTool


_DEFAULT_PARENT_SUMMARIZER_SYSTEM_PROMPT = (
    "You summarize the outcome of an expandable (planner/replanner) task "
    "after every direct child has reached a terminal state. The trigger gives "
    "you the parent task id and completed direct child task ids; read those "
    "task details first, including plan/replan JSON and final summaries, then "
    "submit one evidence-rich roll-up: what was planned, each child's status, "
    "what landed, what was replanned or dropped, and open risk. Do not invent "
    "next steps."
)


@dataclass
class ParentSummary:
    """Result of a parent-summary generation."""

    task_id: str
    content: str
    turns_used: int = 0


def _resolve_parent_summarizer_definition(
    team_run_id: str | None = None,
) -> tuple[str, str | None, str]:
    """Return (system_prompt, model, agent_name) for the parent summarizer.

    Team-roster override via roles ``parent_summarizer``, falling back to the
    registered ``parent_summarizer`` builtin.
    """
    agent_name = "parent_summarizer"
    if team_run_id:
        try:
            from team.runtime.registry import get as get_team_run

            team_run = get_team_run(team_run_id)
        except Exception:
            team_run = None
        if team_run is not None:
            roster = getattr(team_run, "roster", None)
            if isinstance(roster, dict):
                candidates = roster.get("parent_summarizer")
                if isinstance(candidates, list):
                    for candidate in candidates:
                        name = str(candidate).strip()
                        if name and get_definition(name) is not None:
                            agent_name = name
                            break

    defn = get_definition(agent_name)
    if defn is None:
        return _DEFAULT_PARENT_SUMMARIZER_SYSTEM_PROMPT, None, agent_name

    prompt = (defn.system_prompt or "").strip() or _DEFAULT_PARENT_SUMMARIZER_SYSTEM_PROMPT
    model = str(defn.model).strip() if defn.model else ""
    return prompt, (model if model and model != "inherit" else None), agent_name


def _build_parent_summary_prompt(
    parent: Any,
    children: list[Any],
) -> str:
    """Build the user prompt text fed to the summarizer agent."""
    lines: list[str] = []
    completed_child_ids = [str(getattr(child, "id", "")) for child in children]
    completed_child_ids = [child_id for child_id in completed_child_ids if child_id]
    lines.append("# Parent summarizer trigger")
    lines.append(
        "All direct children of the parent task are terminal. Read the parent "
        "task detail and each completed direct child task detail before you "
        "submit the parent roll-up."
    )
    lines.append("")
    lines.append("## Parent task id")
    lines.append(str(parent.id))
    lines.append("")
    lines.append("## Completed direct child task ids to read")
    if completed_child_ids:
        for child_id in completed_child_ids:
            lines.append(f"- {child_id}")
    else:
        lines.append("(none)")
    lines.append("")
    lines.append(
        "Workflow: first call `read_task_details(task_id=\""
        f"{parent.id}"
        "\")` for the parent. Then call `read_task_details(task_id=...)` once "
        "for every completed direct child id listed above. Only after every "
        "listed child has been read, produce exactly one `submit_task_summary` "
        "call with type=\"success\". The `content` must report what the parent "
        "planned, one direct child line per child with status plus delivered/"
        "replanned/dropped/open-risk classification, and an overall roll-up. "
        "Cite child final summaries, commands, failing ids, exit codes, "
        "blockers, missing summaries, and trivial summaries when present. Do "
        "not collapse the result into \"all children done\" and do not invent "
        "next steps. This terminal submission is the completion signal for the "
        "parent task."
    )
    return "\n".join(lines)

async def run_parent_summary(
    *,
    parent_task_id: str,
    team_run_id: str,
    api_client: Any,
    max_tokens: int = 2048,
) -> ParentSummary:
    """Generate and post a parent_summary note for ``parent_task_id``.

    Returns the posted ``ParentSummary``. Raises RuntimeError on validation
    or run failures; the dispatcher handles retries and fallback.
    """
    from team.runtime.registry import get as get_team_run

    team_run = get_team_run(team_run_id)
    if team_run is None:
        raise RuntimeError(f"run_parent_summary: team_run {team_run_id!r} not registered")

    task_center = team_run.task_center
    graph = task_center.graph
    parent = graph.get(parent_task_id)
    if parent is None:
        raise RuntimeError(
            f"run_parent_summary: parent {parent_task_id!r} not in graph"
        )

    children = [
        task for task in graph.values()
        if getattr(task, "parent_id", None) == parent_task_id
    ]
    system_prompt, model, agent_name = _resolve_parent_summarizer_definition(team_run_id)
    user_prompt = _build_parent_summary_prompt(parent, children)
    repo_root = getattr(getattr(team_run, "project_context", None), "repo_root", None)
    cwd = Path(str(repo_root)) if repo_root else Path(".")
    execution_context = ToolExecutionContext(
        cwd=cwd,
        metadata={
            "task_center": task_center,
            "agent_name": agent_name,
            "work_item_id": parent_task_id,
        },
    )

    result = await run(
        agent_name=f"{agent_name}:{parent_task_id}",
        messages=[],
        system_prompt=system_prompt,
        prompt=user_prompt,
        tools=[ReadTaskDetailsTool(), SubmitTaskSummaryTool()],
        api_client=api_client,
        max_tokens_per_turn=max_tokens,
        model=model,
        execution_context=execution_context,
        execute_tools=True,
        terminal_tool_names={"submit_task_summary"},
        execute_terminal_tools=False,
        team_run_id=team_run_id,
        work_item_id=parent_task_id,
        agent_run_id=str(uuid.uuid4()),
    )

    validated = result.validated
    if not isinstance(validated, SubmitTaskSummaryInput):
        raise RuntimeError(
            f"run_parent_summary ({parent_task_id}): expected "
            f"SubmitTaskSummaryInput, got {type(validated).__name__}"
        )

    await task_center.notes.post(
        Note(
            id=str(uuid.uuid4()),
            task_id=parent_task_id,
            agent_name=agent_name,
            content=validated.content,
            timestamp=time.time(),
            paths=list(getattr(parent, "scope_paths", []) or []),
            tags=["implementation", "parent_summary"],
        )
    )

    return ParentSummary(
        task_id=parent_task_id,
        content=validated.content,
        turns_used=result.turns_used,
    )
