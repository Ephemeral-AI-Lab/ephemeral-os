"""Production ``build_query_context`` callable for the team Executor.

Assembles an agent query context for a ``WorkItem`` by rendering
briefings (shared + dep-snapshotted + explicit) into the preamble of
the initial user message. This is the single prod-side wiring point
for :func:`team.context.briefings.render_briefings`; the same helper
is called from the ``run_subagent`` spawn handler so subagents inherit
``shared_briefings`` symmetrically (§13).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from team.context.briefings import render_briefings
from team.models import WorkItem, WorkItemKind, WorkItemStatus
from team.runtime.registry import get as _get_team_run
from tools.core.runtime import ExecutionMetadata

if TYPE_CHECKING:
    from agents.types import AgentDefinition
    from team.runtime.team_run import TeamRun

logger = logging.getLogger(__name__)


@dataclass
class TeamAgentContext:
    """Canonical team-runtime context for work and posthook runners."""

    user_message: str = ""
    tool_metadata: ExecutionMetadata = field(default_factory=ExecutionMetadata)
    work_result: Any | None = None
    posthook_metadata_key: str = ""
    posthook_outputs: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.tool_metadata, dict):
            meta = ExecutionMetadata()
            meta.update(self.tool_metadata)
            self.tool_metadata = meta
        if self.work_result is not None and self.tool_metadata.get("work_result") is None:
            self.tool_metadata["work_result"] = self.work_result
        elif self.work_result is None:
            self.work_result = self.tool_metadata.get("work_result")
        if self.posthook_metadata_key:
            self.tool_metadata["posthook_metadata_key"] = self.posthook_metadata_key
        else:
            self.posthook_metadata_key = self.tool_metadata.get("posthook_metadata_key", "")

    def set_posthook_metadata_key(self, key: str) -> None:
        self.posthook_metadata_key = key
        self.tool_metadata["posthook_metadata_key"] = key

    def set_posthook_output(self, key: str, value: Any) -> None:
        self.posthook_outputs[key] = value
        self.tool_metadata[key] = value

    def get_posthook_output(self, key: str) -> Any:
        if key in self.posthook_outputs:
            return self.posthook_outputs[key]
        return self.tool_metadata.get(key)


def build_work_item_metadata(team_run: TeamRun, wi: WorkItem) -> ExecutionMetadata:
    """Build the canonical routing metadata for a team work item."""
    payload = wi.payload if isinstance(wi.payload, dict) else {}
    meta = ExecutionMetadata(
        team_run_id=team_run.id,
        work_item_id=wi.id,
        agent_run_id=wi.agent_run_id,
        agent_name=wi.agent_name,
        sandbox_id=getattr(team_run, "sandbox_id", "") or "",
    )
    # Captured before the agent starts its work phase. Scout artifacts
    # re-use this as their snapshot cutoff so atlas freshness can see
    # edits that land during the scout's read window.
    meta["work_item_started_at"] = time.time()
    meta["retry_count"] = wi.retry_count
    meta["max_retries"] = wi.max_retries
    repo_root = str(getattr(getattr(team_run, "project_context", None), "repo_root", "") or "")
    if repo_root:
        meta["daytona_cwd"] = repo_root
        meta["ci_workspace_root"] = repo_root
    # Apply run-level coordination overrides (set by benchmark runners or
    # other callers via team_run.coordination_metadata).
    for key, value in getattr(team_run, "coordination_metadata", {}).items():
        meta[key] = value
    for key in ("write_scope", "verification"):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            meta[key] = value

    # Pre-populate plan-submission context so SubmitPlanTool can read from
    # metadata instead of reaching into team.runtime.registry at call time.
    _populate_plan_submission_context(meta, team_run, wi)

    return meta


def _populate_plan_submission_context(
    meta: ExecutionMetadata,
    team_run: "TeamRun",
    wi: "WorkItem",
) -> None:
    """Inject plan-submission context into metadata.

    This decouples ``SubmitPlanTool`` from the team runtime: the tool
    reads these flat values from metadata rather than importing
    ``team.runtime.registry`` and walking the live object graph.
    """
    # allow_empty_plan: true for non-root expandable sub-planners
    root_id = str(getattr(team_run, "root_work_item_id", "") or "")
    is_sub_planner = (
        bool(root_id)
        and wi.id != root_id
        and wi.agent_name == "team_planner"
        and wi.kind == WorkItemKind.EXPANDABLE
    )
    meta["allow_empty_plan"] = is_sub_planner

    # known_external_dep_ids: all work item IDs in the dispatcher graph
    graph = getattr(getattr(team_run, "dispatcher", None), "graph", None)
    if isinstance(graph, dict):
        meta["known_external_dep_ids"] = {str(wi_id) for wi_id in graph}

    # roster_agent_names: flattened set of all agent names in the roster
    roster = getattr(team_run, "roster", None)
    if isinstance(roster, dict):
        agent_names: set[str] = set()
        for names in roster.values():
            if isinstance(names, list):
                agent_names.update(str(n) for n in names)
        if agent_names:
            meta["roster_agent_names"] = agent_names

    # benchmark targets (if benchmark runner populated them)
    try:
        from benchmarks.sweevo.plan_normalization import (
            extract_benchmark_targets_from_team_run,
        )

        test_ids, test_files = extract_benchmark_targets_from_team_run(team_run.id)
        if test_ids:
            meta["benchmark_test_ids"] = test_ids
        if test_files:
            meta["benchmark_test_files"] = test_files
    except ImportError:
        pass


def build_initial_user_message(
    team_run: TeamRun,
    wi: WorkItem,
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


def default_base_prompt(wi: WorkItem) -> str:
    """Minimal default rendering of a WorkItem payload into a user message."""
    if wi.replan_source_id is not None:
        return _render_replan_prompt(wi)
    payload = wi.payload or {}
    rendered = render_work_item_payload(payload)
    if rendered is not None:
        return rendered
    return f"Execute work item {wi.id} (agent={wi.agent_name}).\nPayload: {payload!r}"


def _render_replan_prompt(wi: WorkItem) -> str:
    """Render a replan work item with full failure context."""
    payload = wi.payload or {}
    original = json.dumps(payload.get("original_payload", {}), indent=2, default=str)
    return (
        f"## Replan Request\n\n"
        f"A sibling work item failed and requires corrective action at this depth level.\n\n"
        f"**Failed work item**: {payload.get('failed_work_item_id', 'unknown')}\n"
        f"**Failed agent**: {payload.get('failed_agent', 'unknown')}\n"
        f"**Failure reason**: {payload.get('failure_reason', 'unknown')}\n\n"
        f"### Failure Context\n{payload.get('failure_context', 'No context provided.')}\n\n"
        f"### Suggestion\n{payload.get('suggestion', 'None')}\n\n"
        f"### Original Payload\n{original}\n\n"
        f"Analyze the failure and return a JSON corrective replan payload with "
        f"``add_items`` and optional ``cancel_ids`` for the posthook agent to submit. "
        f"Completed sibling artifacts are available via dependency briefings above."
    )


def render_work_item_payload(payload: Any) -> str | None:
    """Render a structured payload without dropping critical fields."""
    if isinstance(payload, dict):
        if not payload:
            return None
        rendered_payload = json.dumps(payload, indent=2, default=str)
        primary: list[str] = []
        for key in ("task", "prompt", "description", "instructions", "final_text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                primary.append(value.strip())
        if primary:
            return (
                "\n\n".join(primary)
                + "\n\nWorkItem payload (authoritative):\n"
                + rendered_payload
            )
        return "WorkItem payload (authoritative):\n" + rendered_payload
    if isinstance(payload, str):
        return payload
    return None


def _render_roster(roster: dict[str, list[str]]) -> str:
    """Render the team roster into a compact reference block for planners."""
    from agents.registry import get_definition

    lines = ["## Available Agents\n"]
    for role, agent_names in roster.items():
        lines.append(f"### {role}")
        for name in agent_names:
            defn = get_definition(name)
            desc = defn.description if defn else ""
            lines.append(f"- **{name}**: {desc}")
        lines.append("")
    lines.append(
        "When submitting plan items, use these exact agent names. "
        "`kind` is auto-inferred from the agent's role "
        "(planner → expandable, all others → atomic)."
    )
    return "\n".join(lines)


def build_query_context(
    defn: AgentDefinition,
    team_run: TeamRun,
    wi: WorkItem,
) -> TeamAgentContext:
    """Default production ``QueryContextBuilder``.

    Returns the canonical typed context carrying the rendered user
    message plus routing metadata that downstream hooks and tools rely on.
    Production executor factories may wrap this to add domain-specific
    fields — the briefings-preamble contract lives here.
    """
    meta = build_work_item_metadata(team_run, wi)
    base_prompt = default_base_prompt(wi)
    # Inject roster reference for agents that submit plans (planners and
    # replanners) so they know which agents are available to target.
    roster = getattr(team_run, "roster", None)
    if roster and getattr(defn, "role", None) in ("planner", "replanner"):
        base_prompt = _render_roster(roster) + "\n\n" + base_prompt
    user_message = build_initial_user_message(team_run, wi, base_prompt)
    return TeamAgentContext(
        user_message=user_message,
        tool_metadata=meta,
    )
