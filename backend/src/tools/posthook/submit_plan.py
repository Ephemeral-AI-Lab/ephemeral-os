"""``submit_plan`` tool — stashes a validated Plan in ``ctx.tool_metadata``."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from team.models import Plan, WorkItemKind
from team.planning.validation import normalize_plan_kinds, validate_plan_phase_a
from tools.core.base import ToolExecutionContext
from tools.posthook.base import SubmitPosthookTool


def _looks_like_validator_payload(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("verify", "verification", "retries", "reproduction"))


def _normalize_submit_plan_item_shape(item: Any) -> Any:
    """Structural aliasing only — field renaming, no agent-name inference.

    Agent-name resolution requires the roster and is deferred to
    ``_resolve_plan_item_agent_names`` which runs inside ``_build_payload``
    where the execution context is available.
    """
    if not isinstance(item, dict):
        return item

    normalized = dict(item)
    payload = normalized.get("payload")
    payload_dict = dict(payload) if isinstance(payload, dict) else {}

    if "local_id" not in normalized and isinstance(normalized.get("id"), str):
        normalized["local_id"] = normalized["id"]

    if "briefings" not in normalized and isinstance(payload_dict.get("briefings"), list):
        normalized["briefings"] = payload_dict.pop("briefings")

    if "agent_name" not in normalized and isinstance(normalized.get("agent"), str):
        normalized["agent_name"] = normalized["agent"]

    if payload_dict:
        normalized["payload"] = payload_dict

    return normalized


def _resolve_plan_item_agent_names(
    items: list[dict[str, Any]],
    roster_agent_names: set[str] | None,
) -> list[dict[str, Any]]:
    """Infer canonical agent names using the roster.

    When ``roster_agent_names`` is ``None`` (single-agent mode or no
    roster available), agent names are accepted as-is with no inference.
    """
    if roster_agent_names is None:
        return items

    resolved: list[dict[str, Any]] = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            resolved.append(raw_item)
            continue
        item = dict(raw_item)
        raw_agent_name = item.get("agent_name")
        if not isinstance(raw_agent_name, str):
            resolved.append(item)
            continue

        agent_name = raw_agent_name.strip()
        if agent_name in roster_agent_names:
            resolved.append(item)
            continue

        # Agent name not in roster — attempt inference.
        local_id = item.get("local_id")
        explicit_agent_name = "agent_name" in raw_item
        local_id_like_name = any(sep in agent_name for sep in ("_", "-"))
        payload = item.get("payload")
        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        inferred_agent = None

        # Check if the "agent" field (before aliasing) is a known name.
        explicit_agent = item.get("agent")
        if isinstance(explicit_agent, str) and explicit_agent.strip() in roster_agent_names:
            inferred_agent = explicit_agent.strip()
        elif (not explicit_agent_name or local_id_like_name) and item.get("kind") == WorkItemKind.EXPANDABLE.value:
            # Expandable items map to planner-role agents.
            inferred_agent = _find_roster_agent_by_role(roster_agent_names, "planner")
        elif (not explicit_agent_name or local_id_like_name) and (
            agent_name.startswith("validate") or agent_name.startswith("validator")
        ):
            inferred_agent = _find_roster_agent_by_role(roster_agent_names, "validator")
        elif (
            (not explicit_agent_name or local_id_like_name)
            and _looks_like_validator_payload(payload_dict)
            and item.get("deps")
        ):
            inferred_agent = _find_roster_agent_by_role(roster_agent_names, "validator")
        elif not explicit_agent_name or local_id_like_name:
            inferred_agent = _find_roster_agent_by_role(roster_agent_names, "developer")
        else:
            inferred_agent = agent_name

        if inferred_agent is not None:
            item["agent_name"] = inferred_agent
            if not isinstance(local_id, str) or not local_id.strip():
                item["local_id"] = agent_name

        resolved.append(item)
    return resolved


def _find_roster_agent_by_role(
    roster_agent_names: set[str],
    role_hint: str,
) -> str | None:
    """Find a roster agent name matching a role hint.

    Uses simple substring matching: "planner" matches "team_planner",
    "validator" matches "validator", "developer" matches "dev_python", etc.
    Returns the first match or ``None``.
    """
    for name in sorted(roster_agent_names):
        if role_hint in name or name in role_hint:
            return name
    return None


def _is_submit_plan_item_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _decode_submit_plan_items(value: Any) -> Any:
    """Decode only top-level arrays of plan-item objects.

    The generic array extractor is intentionally permissive so serializer
    agents can recover arrays embedded in prose. For submit_plan, that
    permissiveness can misfire on nested benchmark/id arrays inside an
    otherwise malformed item payload and turn ``items`` into ``list[str]``.
    Restrict recovery here to arrays whose elements are object-shaped plan
    items.
    """
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return value

    if text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if _is_submit_plan_item_list(payload):
            return payload

    decoder = json.JSONDecoder()
    best_payload: list[dict[str, Any]] | None = None
    best_start: int | None = None
    best_end = -1
    for start, char in enumerate(text):
        if char != "[":
            continue
        try:
            payload, end = decoder.raw_decode(text, idx=start)
        except ValueError:
            continue
        if not _is_submit_plan_item_list(payload):
            continue
        if end > best_end or (end == best_end and (best_start is None or start < best_start)):
            best_payload = payload
            best_start = start
            best_end = end
    return best_payload if best_payload is not None else value


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
        raw_items = _decode_submit_plan_items(value)
        if isinstance(value, str) and raw_items is value:
            raise ValueError(
                "`items` must be a real list of plan item objects or a JSON array string "
                'that decodes to objects like {"agent_name":"developer","local_id":"w1","payload":{}}.'
            )
        if not isinstance(raw_items, list):
            return raw_items
        bad_entries: list[str] = []
        normalized_items: list[Any] = []
        for index, item in enumerate(raw_items):
            if isinstance(item, _SubmitPlanItem):
                normalized_items.append(item)
                continue
            if not isinstance(item, dict):
                preview = repr(item)
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                bad_entries.append(f"{index}={preview}")
                continue
            normalized_items.append(_normalize_submit_plan_item_shape(item))
        if bad_entries:
            joined = ", ".join(bad_entries[:5])
            raise ValueError(
                "`items` must contain plan item objects, not bare strings or other scalars. "
                'Each item should look like {"agent_name":"developer","local_id":"w1","payload":{}}. '
                f"Invalid entries: {joined}"
            )
        return normalized_items


class SubmitPlanTool(SubmitPosthookTool):
    name: str = "submit_plan"
    description: str = (
        "Submit a Plan to extend the team's DAG. Each item names an existing "
        "agent and an optional list of dependency local_ids or external "
        "work_item_ids. `items` must be a list of object-shaped plan items "
        "with fields such as `agent_name`, optional `local_id`, `payload`, "
        "and `deps` — never a list of test ids or other bare strings. "
        "`kind` is auto-inferred from the target agent's role (planner → "
        "expandable, all others → atomic). Validation runs synchronously: "
        "if any structural issue is found the tool returns a structured "
        "error and you MUST fix it and call submit_plan again."
    )
    input_model = SubmitPlanInput
    default_metadata_key: str = "submitted_plan"

    def _build_payload(
        self, arguments: BaseModel, context: ToolExecutionContext
    ) -> tuple[Any, str | None]:
        assert isinstance(arguments, SubmitPlanInput)
        benchmark_test_ids = context.metadata.get("benchmark_test_ids")
        benchmark_test_files = context.metadata.get("benchmark_test_files")
        raw_plan = self._normalize_benchmark_command_payloads(
            arguments.model_dump(),
            context=context,
            benchmark_test_ids=benchmark_test_ids,
            benchmark_test_files=benchmark_test_files,
        )
        # Resolve agent names against the roster before parsing into Plan.
        roster_agent_names = context.metadata.get("roster_agent_names")
        raw_items = raw_plan.get("items")
        if isinstance(raw_items, list):
            raw_plan["items"] = _resolve_plan_item_agent_names(raw_items, roster_agent_names)
        try:
            plan = Plan.from_dict(raw_plan)
        except Exception as exc:
            return None, f"Invalid Plan shape: {exc}"

        normalize_plan_kinds(plan)

        max_plan_size = int(context.metadata.get("max_plan_size", 50) or 50)
        max_reviewers_per_plan = _optional_int(context.metadata.get("max_reviewers_per_plan"))
        require_reviewer_for_plan_size = _optional_int(
            context.metadata.get("require_reviewer_for_plan_size")
        )
        extra_validators = self._build_extra_validators(
            benchmark_test_ids=benchmark_test_ids,
            benchmark_test_files=benchmark_test_files,
        )
        # Read pre-populated context from metadata (set by team runtime's
        # build_work_item_metadata or by single-agent callers directly).
        allow_empty = bool(context.metadata.get("allow_empty_plan", False))
        known_external_deps = context.metadata.get("known_external_dep_ids")
        issues = validate_plan_phase_a(
            plan,
            max_plan_size=max_plan_size,
            allow_empty=allow_empty,
            known_external_deps=known_external_deps,
            max_reviewers_per_plan=max_reviewers_per_plan,
            require_reviewer_for_plan_size=require_reviewer_for_plan_size,
            extra_validators=extra_validators,
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

    @staticmethod
    def _build_extra_validators(
        *,
        benchmark_test_ids: set[str] | None,
        benchmark_test_files: set[str] | None,
    ) -> list[Any] | None:
        if not benchmark_test_ids and not benchmark_test_files:
            return None
        try:
            from benchmarks.sweevo.plan_validation import (
                build_benchmark_payload_ref_validator,
            )

            return [
                build_benchmark_payload_ref_validator(
                    benchmark_test_ids=benchmark_test_ids or set(),
                    benchmark_test_files=benchmark_test_files or set(),
                )
            ]
        except ImportError:
            return None

    def _normalize_benchmark_command_payloads(
        self,
        plan_data: dict[str, Any],
        *,
        context: ToolExecutionContext,
        benchmark_test_ids: set[str] | None,
        benchmark_test_files: set[str] | None,
    ) -> dict[str, Any]:
        if not benchmark_test_ids and not benchmark_test_files:
            return plan_data
        repo_dir = str(
            context.metadata.get("daytona_cwd")
            or context.metadata.get("ci_workspace_root")
            or ""
        ).strip()
        try:
            from benchmarks.sweevo.plan_normalization import (
                normalize_benchmark_command_payloads,
            )

            return normalize_benchmark_command_payloads(
                plan_data,
                repo_dir=repo_dir,
                benchmark_test_ids=benchmark_test_ids,
                benchmark_test_files=benchmark_test_files,
            )
        except ImportError:
            return plan_data
