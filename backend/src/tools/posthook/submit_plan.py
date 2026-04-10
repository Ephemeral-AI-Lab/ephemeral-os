"""``submit_plan`` tool — stashes a validated Plan in ``ctx.tool_metadata``."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from team.models import Plan, WorkItemKind
from team.planning.validation import validate_plan_phase_a
from tools.core.base import ToolExecutionContext
from tools.posthook.base import SubmitPosthookTool, _decode_json_array_string


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _SubmitBriefing(BaseModel):
    name: str
    source: str  # "artifact" | "inline"
    ref: str | None = None
    inline: str | None = None
    description: str | None = None


class _SubmitPlanItem(BaseModel):
    agent_name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    local_id: str | None = None
    deps: list[str] = Field(default_factory=list)
    notes: str | None = None
    timeout_seconds: float | None = None
    kind: WorkItemKind = WorkItemKind.ATOMIC
    briefings: list[_SubmitBriefing] = Field(default_factory=list)


class SubmitPlanInput(BaseModel):
    items: list[_SubmitPlanItem]
    rationale: str | None = None

    @field_validator("items", mode="before")
    @classmethod
    def _deserialize_items(cls, value: Any) -> Any:
        return _decode_json_array_string(value)


class SubmitPlanTool(SubmitPosthookTool):
    name: str = "submit_plan"
    description: str = (
        "Submit a Plan to extend the team's DAG. Each item names an existing "
        "agent and an optional list of dependency local_ids or external "
        "work_item_ids. Validation runs synchronously: if any structural "
        "issue is found the tool returns a structured error and you MUST "
        "fix it and call submit_plan again."
    )
    input_model = SubmitPlanInput
    default_metadata_key: str = "submitted_plan"

    def _build_payload(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> tuple[Any, str | None]:
        assert isinstance(arguments, SubmitPlanInput)
        try:
            plan = Plan.from_dict(arguments.model_dump())
        except Exception as exc:
            return None, f"Invalid Plan shape: {exc}"

        max_plan_size = int(context.metadata.get("max_plan_size", 50) or 50)
        max_validators_per_plan = _optional_int(context.metadata.get("max_validators_per_plan"))
        require_validator_for_plan_size = _optional_int(
            context.metadata.get("require_validator_for_plan_size")
        )
        benchmark_test_ids, benchmark_test_files = self._known_benchmark_targets(context)
        issues = validate_plan_phase_a(
            plan,
            max_plan_size=max_plan_size,
            allow_empty=self._allow_empty_plan(context),
            known_external_deps=self._known_external_dep_ids(context),
            benchmark_test_ids=benchmark_test_ids,
            benchmark_test_files=benchmark_test_files,
            max_validators_per_plan=max_validators_per_plan,
            require_validator_for_plan_size=require_validator_for_plan_size,
        )
        if issues:
            lines = [f"- {i['field']}: {i['msg']}" for i in issues]
            return None, (
                "invalid_plan:\n"
                + "\n".join(lines)
                + "\n\nFix the issues above and call submit_plan again."
            )
        return plan, None

    def _accepted_message(self, payload: Any) -> str:
        assert isinstance(payload, Plan)
        return f"Plan accepted: {len(payload.items)} item(s) queued for dispatch."

    def _allow_empty_plan(self, context: ToolExecutionContext) -> bool:
        team_run_id = str(context.metadata.get("team_run_id") or "").strip()
        work_item_id = str(context.metadata.get("work_item_id") or "").strip()
        if not team_run_id or not work_item_id:
            return False
        try:
            from team.runtime.registry import get as get_team_run

            team_run = get_team_run(team_run_id)
        except Exception:
            return False
        if team_run is None:
            return False
        root_id = str(getattr(team_run, "root_work_item_id", "") or "")
        if not root_id or work_item_id == root_id:
            return False
        graph = getattr(getattr(team_run, "dispatcher", None), "graph", None)
        if not isinstance(graph, dict):
            return False
        work_item = graph.get(work_item_id)
        if work_item is None:
            return False
        return (
            str(getattr(work_item, "agent_name", "") or "") == "team_planner"
            and getattr(work_item, "kind", None) == WorkItemKind.EXPANDABLE
        )

    def _known_external_dep_ids(self, context: ToolExecutionContext) -> set[str] | None:
        team_run_id = str(context.metadata.get("team_run_id") or "").strip()
        if not team_run_id:
            return None
        try:
            from team.runtime.registry import get as get_team_run

            team_run = get_team_run(team_run_id)
        except Exception:
            return None
        if team_run is None:
            return None
        graph = getattr(getattr(team_run, "dispatcher", None), "graph", None)
        if not isinstance(graph, dict):
            return None
        return {str(wi_id) for wi_id in graph}

    def _known_benchmark_targets(
        self, context: ToolExecutionContext
    ) -> tuple[set[str] | None, set[str] | None]:
        team_run_id = str(context.metadata.get("team_run_id") or "").strip()
        if not team_run_id:
            return None, None
        try:
            from team.runtime.registry import get as get_team_run

            team_run = get_team_run(team_run_id)
        except Exception:
            return None, None
        if team_run is None:
            return None, None
        graph = getattr(getattr(team_run, "dispatcher", None), "graph", None)
        root_id = getattr(team_run, "root_work_item_id", None)
        if not isinstance(graph, dict) or not isinstance(root_id, str):
            return None, None
        root = graph.get(root_id)
        payload = getattr(root, "payload", None) if root is not None else None
        if not isinstance(payload, dict):
            return None, None
        fail_to_pass = payload.get("fail_to_pass")
        pass_to_pass = payload.get("pass_to_pass")
        test_ids = {
            str(item).strip()
            for item in (fail_to_pass or [])
            if isinstance(item, str) and str(item).strip()
        }
        test_ids.update(
            str(item).strip()
            for item in (pass_to_pass or [])
            if isinstance(item, str) and str(item).strip()
        )
        if not test_ids:
            return None, None
        test_files = {
            item.split("::", 1)[0]
            for item in test_ids
            if "::" in item and item.split("::", 1)[0]
        }
        return test_ids, test_files
