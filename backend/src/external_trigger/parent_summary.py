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
from typing import Any

from agents.registry import get_definition
from external_trigger.runner import run
from team.models import Note
from tools.submission.toolkit import SubmitTaskSummaryInput, SubmitTaskSummaryTool


_DEFAULT_PARENT_SUMMARIZER_SYSTEM_PROMPT = (
    "You summarize the outcome of an expandable (planner/replanner) task "
    "based on its children's Task Center notes and final statuses. Report "
    "facts only: what was planned, what landed, what diverged, what is "
    "blocked. Do not invent next steps."
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
    child_notes: list[Any],
) -> str:
    """Build the user prompt text fed to the summarizer agent."""
    lines: list[str] = []
    lines.append(f"# Parent task: {parent.id}")
    agent_name = getattr(parent, "agent_name", "") or "<unknown>"
    objective = getattr(parent, "objective", "") or ""
    lines.append(f"Agent: {agent_name}")
    parent_status = getattr(parent, "status", None)
    lines.append(f"Status: {getattr(parent_status, 'value', str(parent_status))}")
    parent_deps = list(getattr(parent, "deps", []) or [])
    parent_scope = list(getattr(parent, "scope_paths", []) or [])
    if parent_deps:
        lines.append(f"Deps: {parent_deps}")
    if parent_scope:
        lines.append(f"Scope paths: {parent_scope}")
    lines.append("Objective:")
    lines.append(objective)
    lines.append("")
    lines.append("## Children")
    if not children:
        lines.append("(no children were spawned)")
    else:
        for child in children:
            child_id = getattr(child, "id", "<unknown>")
            child_agent = getattr(child, "agent_name", "") or "<unknown>"
            status = getattr(child, "status", None)
            status_text = getattr(status, "value", str(status))
            failure = getattr(child, "failure_reason", "") or ""
            child_deps = list(getattr(child, "deps", []) or [])
            child_scope = list(getattr(child, "scope_paths", []) or [])
            child_objective = getattr(child, "objective", "") or ""
            lines.append(f"- id={child_id} agent={child_agent} status={status_text}")
            if child_deps:
                lines.append(f"  deps: {child_deps}")
            if child_scope:
                lines.append(f"  scope_paths: {child_scope}")
            if failure:
                lines.append(f"  failure_reason: {failure}")
            if child_objective:
                lines.append("  objective:")
                for line in child_objective.splitlines():
                    lines.append(f"    {line}")
    lines.append("")
    lines.append("## Child notes")
    for note in child_notes:
        if note is None:
            continue
        note_task = getattr(note, "task_id", "<unknown>")
        note_content = getattr(note, "content", "") or ""
        lines.append(f"### {note_task}")
        lines.append(note_content)
        lines.append("")
    lines.append("")
    lines.append(
        "Produce exactly one `submit_task_summary` call with type=\"success\". "
        "The `content` must report what the parent planned, one direct child "
        "line per child with status plus delivered/replanned/dropped/open-risk "
        "classification, and an overall roll-up. Cite child final summaries, "
        "commands, failing ids, exit codes, blockers, missing summaries, and "
        "trivial summaries when present. Do not collapse the result into "
        "\"all children done\" and do not invent next steps."
    )
    return "\n".join(lines)


def _collect_child_notes(task_center: Any, children: list[Any]) -> list[Any]:
    """Best-effort collection of the latest note per child.

    Uses :meth:`NoteManager.notes_for_task` when available, falling back to
    the full ``snapshot`` filtered by task id. Returns a flat list of
    :class:`Note` objects (may be empty).
    """
    notes_mgr = getattr(task_center, "notes", None)
    if notes_mgr is None:
        return []
    collected: list[Any] = []
    for child in children:
        cid = getattr(child, "id", None)
        if not cid:
            continue
        fetch = getattr(notes_mgr, "notes_for_task", None)
        if callable(fetch):
            try:
                rows = fetch(cid)
            except Exception:
                rows = []
            if rows:
                collected.append(rows[-1])
                continue
        snapshot = getattr(notes_mgr, "snapshot", None)
        if callable(snapshot):
            try:
                all_notes = snapshot()
            except Exception:
                all_notes = []
            matching = [n for n in all_notes if getattr(n, "task_id", None) == cid]
            if matching:
                collected.append(matching[-1])
    return collected


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
    child_notes = _collect_child_notes(task_center, children)

    system_prompt, model, agent_name = _resolve_parent_summarizer_definition(team_run_id)
    user_prompt = _build_parent_summary_prompt(parent, children, child_notes)

    result = await run(
        agent_name=f"{agent_name}:{parent_task_id}",
        messages=[],
        system_prompt=system_prompt,
        prompt=user_prompt,
        tools=[SubmitTaskSummaryTool()],
        api_client=api_client,
        max_tokens_per_turn=max_tokens,
        model=model,
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
