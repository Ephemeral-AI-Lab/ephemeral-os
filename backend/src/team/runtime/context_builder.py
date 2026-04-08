"""Production ``build_query_context`` callable for the team Executor.

Assembles an agent query context for a ``WorkItem`` by rendering
briefings (shared + dep-snapshotted + explicit) into the preamble of
the initial user message. This is the single prod-side wiring point
for :func:`team.context.briefings.render_briefings`; the same helper
is called from the ``run_subagent`` spawn handler so subagents inherit
``shared_briefings`` symmetrically (§13).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from team.context.briefings import render_briefings
from team.models import WorkItem, WorkItemStatus
from team.runtime.registry import get as _get_team_run

if TYPE_CHECKING:
    from agents.types import AgentDefinition
    from team.runtime.team_run import TeamRun


def build_initial_user_message(
    team_run: "TeamRun",
    wi: "WorkItem",
    base_prompt: str,
) -> str:
    """Prepend rendered briefings (if any) to ``base_prompt``.

    Used by both the DAG executor (via ``build_query_context``) and the
    ``run_subagent`` spawn path so shared/dep/explicit briefings always
    flow into the child's initial user turn.
    """
    preamble = render_briefings(
        wi,
        team_run.dispatcher.artifact_store,
        project_context=getattr(team_run, "project_context", None),
        budgets=team_run.budgets,
    )
    if not preamble:
        return base_prompt
    return f"{preamble}\n\n{base_prompt}"


def prepend_shared_briefings_for_subagent(team_run_id: str | None, body: str) -> str:
    """Inject the team run's ``shared_briefings`` into a subagent prompt.

    Symmetric with the DAG executor path: the same ``render_briefings``
    helper renders the shared-context preamble so subagents inherit
    run-scoped context without re-exploring (§13). Parent ``wi.briefings``
    are deliberately NOT forwarded — only ``shared_briefings`` cross the
    subagent boundary.

    Returns ``body`` unchanged when no team run is registered or no
    shared briefings exist.
    """
    if not team_run_id:
        return body
    team_run = _get_team_run(team_run_id)
    if team_run is None:
        return body
    placeholder = WorkItem(
        id=f"subagent-{team_run_id}",
        team_run_id=team_run_id,
        agent_name="subagent",
        status=WorkItemStatus.RUNNING,
    )
    preamble = render_briefings(
        placeholder,
        team_run.artifacts,
        project_context=team_run.project_context,
        budgets=team_run.budgets,
    )
    if not preamble:
        return body
    return f"{preamble}\n\n{body}"


def default_base_prompt(wi: "WorkItem") -> str:
    """Minimal default rendering of a WorkItem payload into a user message."""
    payload = wi.payload or {}
    task = payload.get("task") or payload.get("prompt") or payload.get("description")
    if isinstance(task, str) and task:
        return task
    return f"Execute work item {wi.id} (agent={wi.agent_name}).\nPayload: {payload!r}"


def build_query_context(
    defn: "AgentDefinition",  # noqa: ARG001 — kept for QueryContextBuilder signature parity
    team_run: "TeamRun",
    wi: "WorkItem",
) -> dict[str, Any]:
    """Default production ``QueryContextBuilder``.

    Returns a minimal context dict carrying the rendered user message
    plus routing metadata that downstream hooks and tools rely on.
    Production executor factories may wrap this to add domain-specific
    fields — the briefings-preamble contract lives here.
    """
    user_message = build_initial_user_message(team_run, wi, default_base_prompt(wi))
    return {
        "user_message": user_message,
        "tool_metadata": {
            "team_run_id": team_run.id,
            "work_item_id": wi.id,
            "agent_run_id": wi.agent_run_id,
        },
    }
