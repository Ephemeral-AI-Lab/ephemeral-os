"""Unit tests for team.planning.validation.validate_plan."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from team.models import Plan, TaskSpec
from team.planning.validation import validate_plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    id_: str,
    agent: str = "developer",
    task: str = "do work",
    deps: list[str] | None = None,
    scope_paths: list[str] | None = None,
) -> TaskSpec:
    return TaskSpec(
        id=id_,
        task=task,
        agent=agent,
        deps=deps or [],
        scope_paths=scope_paths or [],
    )


def _plan(*specs: TaskSpec, rationale: str | None = None) -> Plan:
    return Plan(tasks=list(specs), rationale=rationale)


# We need to patch agent resolution so tests don't depend on real registry state.
# The conftest in test_team registers standard agents, but here we directly patch
# since we're in a different test directory.
_AGENT_EXISTS_PATH = "team.planning.validation._agent_exists"
_HAS_ROLE_PATH = "team.planning.validation._has_role"
_GET_DEFN_PATH = "team.planning.validation._get_definition"


def _mock_agent(agent_type: str = "agent"):
    """Return a simple namespace that looks like an AgentDefinition."""
    class _Defn:
        role = "developer"

    _Defn.agent_type = agent_type
    return _Defn()


# ---------------------------------------------------------------------------
# Empty plan
# ---------------------------------------------------------------------------


def test_empty_plan_fails_by_default():
    plan = _plan()
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    assert any("no tasks" in i["msg"] for i in issues)


def test_empty_plan_allowed_with_allow_empty_flag():
    plan = _plan()
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan, allow_empty=True)
    assert issues == []


# ---------------------------------------------------------------------------
# Max plan size
# ---------------------------------------------------------------------------


def test_plan_exceeding_max_plan_size_fails():
    specs = [_spec(f"t{i}") for i in range(10)]
    plan = _plan(*specs)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan, max_plan_size=5)
    assert any("exceeds max_plan_size" in i["msg"] for i in issues)


def test_plan_at_max_plan_size_passes():
    specs = [_spec(f"t{i}") for i in range(5)]
    plan = _plan(*specs)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan, max_plan_size=5)
    # Should not have a size-related issue
    assert not any("exceeds max_plan_size" in i["msg"] for i in issues)


# ---------------------------------------------------------------------------
# Duplicate IDs
# ---------------------------------------------------------------------------


def test_duplicate_task_ids_fail():
    specs = [_spec("t1"), _spec("t1")]
    plan = _plan(*specs)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    assert any("duplicate task id" in i["msg"] for i in issues)


# ---------------------------------------------------------------------------
# Agent name validation
# ---------------------------------------------------------------------------


def test_missing_agent_name_fails():
    spec = TaskSpec(id="t1", task="do work", agent="")
    plan = _plan(spec)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    assert any("agent is required" in i["msg"] for i in issues)


def test_unknown_agent_name_fails():
    spec = _spec("t1", agent="nonexistent_agent")
    plan = _plan(spec)
    with patch(_AGENT_EXISTS_PATH, return_value=False), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=None):
        issues = validate_plan(plan)
    assert any("unknown agent" in i["msg"] for i in issues)


def test_known_agent_passes_agent_check():
    spec = _spec("t1", agent="developer")
    plan = _plan(spec)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    assert not any("unknown agent" in i["msg"] or "agent is required" in i["msg"] for i in issues)


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


def test_cycle_a_depends_on_b_b_depends_on_a_detected():
    a = _spec("A", deps=["B"])
    b = _spec("B", deps=["A"])
    plan = _plan(a, b)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    assert any("cycle detected" in i["msg"] for i in issues)


def test_self_referencing_dep_creates_cycle():
    spec = _spec("t1", deps=["t1"])
    plan = _plan(spec)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    assert any("cycle detected" in i["msg"] for i in issues)


def test_linear_chain_no_cycle():
    a = _spec("A")
    b = _spec("B", deps=["A"])
    c = _spec("C", deps=["B"])
    plan = _plan(a, b, c)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    assert not any("cycle" in i["msg"] for i in issues)


def test_diamond_dependency_no_cycle():
    a = _spec("A")
    b = _spec("B", deps=["A"])
    c = _spec("C", deps=["A"])
    d = _spec("D", deps=["B", "C"])
    plan = _plan(a, b, c, d)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    assert not any("cycle" in i["msg"] for i in issues)


# ---------------------------------------------------------------------------
# Valid plan returns empty issues
# ---------------------------------------------------------------------------


def test_valid_simple_plan_returns_no_issues():
    a = _spec("A")
    b = _spec("B", deps=["A"])
    plan = _plan(a, b)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)
    # May have validator policy issues but not structural issues
    structural_issues = [
        i for i in issues
        if any(kw in i["msg"] for kw in ["duplicate", "unknown agent", "cycle", "no tasks", "agent is required"])
    ]
    assert structural_issues == []


# ---------------------------------------------------------------------------
# External dep refs
# ---------------------------------------------------------------------------


def test_unknown_dep_without_known_external_deps_still_reported():
    # Unknown deps are reported even when known_external_deps is None
    spec = _spec("t1", deps=["external-ghost"])
    plan = _plan(spec)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan, known_external_deps=None)
    assert any("unknown dep" in i["msg"] for i in issues)


def test_unknown_dep_not_in_known_external_deps_fails():
    spec = _spec("t1", deps=["ghost-id"])
    plan = _plan(spec)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan, known_external_deps={"other-id"})
    assert any("unknown dep" in i["msg"] for i in issues)


def test_dep_in_known_external_deps_passes():
    spec = _spec("t1", deps=["real-external"])
    plan = _plan(spec)
    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan, known_external_deps={"real-external"})
    assert not any("unknown dep" in i["msg"] for i in issues)


# ---------------------------------------------------------------------------
# Extra validators
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Validator cascade_policy enforcement
# ---------------------------------------------------------------------------


def _mock_validator_agent():
    """Return a namespace that looks like a validator AgentDefinition."""
    class _Defn:
        role = "reviewer"
        agent_type = "agent"
    return _Defn()


def _mock_planner_agent():
    """Return a namespace that looks like an expandable planner AgentDefinition."""
    class _Defn:
        role = "planner"
        agent_type = "agent"
    return _Defn()


def test_validator_with_cancel_cascade_policy_fails():
    """Validators must use cascade_policy='continue' to ensure recovery cycle works."""
    dev = _spec("dev-1")
    val = TaskSpec(
        id="val-root",
        task="validate",
        agent="validator",
        deps=["dev-1"],
        cascade_policy="cancel",
    )
    plan = _plan(dev, val)

    def side_effect_exists(name):
        return True

    def side_effect_role(name, role):
        return name == "validator" and role == "reviewer"

    def side_effect_defn(name):
        if name == "validator":
            return _mock_validator_agent()
        return _mock_agent()

    with patch(_AGENT_EXISTS_PATH, side_effect=side_effect_exists), \
         patch(_HAS_ROLE_PATH, side_effect=side_effect_role), \
         patch(_GET_DEFN_PATH, side_effect=side_effect_defn):
        issues = validate_plan(plan)
    assert any("cascade_policy" in i["msg"] and "continue" in i["msg"] for i in issues)


def test_validator_with_continue_cascade_policy_passes():
    """Validators with cascade_policy='continue' should not trigger the check."""
    dev = _spec("dev-1")
    val = TaskSpec(
        id="val-root",
        task="validate",
        agent="validator",
        deps=["dev-1"],
        cascade_policy="continue",
    )
    plan = _plan(dev, val)

    def side_effect_exists(name):
        return True

    def side_effect_role(name, role):
        return name == "validator" and role == "reviewer"

    def side_effect_defn(name):
        if name == "validator":
            return _mock_validator_agent()
        return _mock_agent()

    with patch(_AGENT_EXISTS_PATH, side_effect=side_effect_exists), \
         patch(_HAS_ROLE_PATH, side_effect=side_effect_role), \
         patch(_GET_DEFN_PATH, side_effect=side_effect_defn):
        issues = validate_plan(plan)
    assert not any("cascade_policy" in i["msg"] for i in issues)


# ---------------------------------------------------------------------------
# Extra validators
# ---------------------------------------------------------------------------


def test_extra_validators_are_called():
    a = _spec("A")
    plan = _plan(a)
    called = []

    def extra(items):
        called.append(True)
        return [{"field": "tasks", "msg": "custom error"}]

    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan, extra_validators=[extra])
    assert called
    assert any("custom error" in i["msg"] for i in issues)


def test_crowded_plan_without_expandable_lane_fails():
    specs = [_spec(f"dev-{idx}") for idx in range(7)]
    validator = TaskSpec(
        id="val-root",
        task="validate",
        agent="validator",
        deps=[spec.id for spec in specs],
        cascade_policy="continue",
    )
    plan = _plan(*specs, validator)

    def side_effect_role(name, role):
        return name == "validator" and role == "reviewer"

    def side_effect_defn(name):
        if name == "validator":
            return _mock_validator_agent()
        return _mock_agent()

    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, side_effect=side_effect_role), \
         patch(_GET_DEFN_PATH, side_effect=side_effect_defn):
        issues = validate_plan(plan)

    assert any("expandable planner lane" in i["msg"] for i in issues)


def test_crowded_plan_with_expandable_lane_passes_expandability_check():
    specs = [_spec(f"dev-{idx}") for idx in range(6)]
    planner = TaskSpec(
        id="plan-residual",
        task="split the residual branch",
        agent="team_planner",
        deps=[],
        scope_paths=["pkg/residual/"],
    )
    validator = TaskSpec(
        id="val-root",
        task="validate",
        agent="validator",
        deps=[spec.id for spec in specs] + [planner.id],
        cascade_policy="continue",
    )
    plan = _plan(*specs, planner, validator)

    def side_effect_role(name, role):
        return name == "validator" and role == "reviewer"

    def side_effect_defn(name):
        if name == "validator":
            return _mock_validator_agent()
        if name == "team_planner":
            return _mock_planner_agent()
        return _mock_agent()

    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, side_effect=side_effect_role), \
         patch(_GET_DEFN_PATH, side_effect=side_effect_defn):
        issues = validate_plan(plan)

    assert not any("expandable planner lane" in i["msg"] for i in issues)


def test_parallel_tasks_with_shared_scope_paths_require_sequencing():
    left = _spec("dev-plot", scope_paths=["dvc/command/plot.py", "dvc/repo/plot/data.py"])
    right = _spec("dev-cli", scope_paths=["dvc/command/plot.py", "dvc/command/update.py"])
    plan = _plan(left, right)

    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)

    assert any("share overlapping scope_paths" in i["msg"] for i in issues)


def test_sequenced_tasks_with_shared_scope_paths_pass():
    left = _spec("dev-plot", scope_paths=["dvc/command/plot.py"])
    right = _spec("dev-cli", deps=["dev-plot"], scope_paths=["dvc/command/plot.py"])
    plan = _plan(left, right)

    with patch(_AGENT_EXISTS_PATH, return_value=True), \
         patch(_HAS_ROLE_PATH, return_value=False), \
         patch(_GET_DEFN_PATH, return_value=_mock_agent()):
        issues = validate_plan(plan)

    assert not any("share overlapping scope_paths" in i["msg"] for i in issues)
