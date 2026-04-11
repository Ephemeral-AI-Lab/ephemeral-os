"""Unit tests for team.models, team.planning.validation, team.artifacts.store."""

from __future__ import annotations

import pytest

from team.artifacts.store import InMemoryArtifactStore
from team.errors import ArtifactTooLarge, InvalidPlan
from team.models import (
    Briefing,
    BudgetConfig,
    BudgetState,
    Plan,
    WorkItem,
    WorkItemKind,
    WorkItemSpec,
    WorkItemStatus,
)
from team.planning.validation import validate_plan_phase_a, validate_plan_phase_b


# ---------- Plan construction ------------------------------------------------


def test_plan_from_dict_roundtrip():
    data = {
        "items": [
            {"agent_name": "a", "payload": {"x": 1}, "local_id": "w1"},
            {"agent_name": "b", "deps": ["w1"], "local_id": "w2"},
        ],
        "rationale": "why",
    }
    plan = Plan.from_dict(data)
    assert len(plan.items) == 2
    assert plan.items[0].agent_name == "a"
    assert plan.items[1].deps == ["w1"]
    assert plan.rationale == "why"


def test_plan_from_dict_normalizes_redundant_failure_payload_fields():
    repeated = ["tests/test_networks.py::test_any_url_success"] * 70
    data = {
        "items": [
            {
                "agent_name": "developer",
                "local_id": "dev1",
                "payload": {
                    "owned_files": ["pydantic/networks.py", "pydantic/networks.py"],
                    "owned_failures": repeated + ["tests/test_networks.py::test_address_valid"],
                    "verify": ["pytest tests/test_networks.py -q", "pytest tests/test_networks.py -q"],
                },
            }
        ]
    }

    plan = Plan.from_dict(data)
    payload = plan.items[0].payload

    assert payload["owned_files"] == ["pydantic/networks.py"]
    assert payload["verify"] == ["pytest tests/test_networks.py -q"]
    assert payload["owned_failures"] == [
        "tests/test_networks.py::test_any_url_success",
        "tests/test_networks.py::test_address_valid",
    ]
    assert payload["owned_failures_total"] == 71


def test_plan_from_dict_caps_owned_failures_to_representative_subset():
    failures = [f"tests/test_networks.py::case_{idx}" for idx in range(80)]
    data = {
        "items": [
            {
                "agent_name": "developer",
                "local_id": "dev1",
                "payload": {"owned_failures": failures},
            }
        ]
    }

    plan = Plan.from_dict(data)
    payload = plan.items[0].payload

    assert len(payload["owned_failures"]) == 64
    assert payload["owned_failures"][0] == "tests/test_networks.py::case_0"
    assert payload["owned_failures"][-1] == "tests/test_networks.py::case_63"
    assert payload["owned_failures_unique_total"] == 80


# ---------- Phase A ----------------------------------------------------------


def _patch_registry(monkeypatch, known_agents):
    from team.planning import validation

    monkeypatch.setattr(
        validation, "_agent_exists", lambda name: name in known_agents
    )


def test_phase_a_empty_plan(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    issues = validate_plan_phase_a(Plan(items=[]))
    assert any("no items" in i["msg"] for i in issues)


def test_phase_a_empty_plan_allowed_when_opted_in(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    assert validate_plan_phase_a(Plan(items=[]), allow_empty=True) == []


def test_phase_a_size_limit(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    plan = Plan(items=[WorkItemSpec(agent_name="a", local_id=f"w{i}") for i in range(51)])
    issues = validate_plan_phase_a(plan, max_plan_size=50)
    assert any("max_plan_size" in i["msg"] for i in issues)


def test_phase_a_duplicate_local_id(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="a", local_id="w1"),
            WorkItemSpec(agent_name="a", local_id="w1"),
        ]
    )
    issues = validate_plan_phase_a(plan)
    assert any("duplicate" in i["msg"] for i in issues)


def test_phase_a_unknown_agent(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    plan = Plan(items=[WorkItemSpec(agent_name="ghost", local_id="w1")])
    issues = validate_plan_phase_a(plan)
    assert any("unknown agent" in i["msg"] for i in issues)


def test_phase_a_internal_cycle(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="a", local_id="w1", deps=["w2"]),
            WorkItemSpec(agent_name="a", local_id="w2", deps=["w1"]),
        ]
    )
    issues = validate_plan_phase_a(plan)
    assert any("cycle" in i["msg"] for i in issues)


def test_phase_a_valid_plan(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="w1"),
            WorkItemSpec(agent_name="validator", local_id="w2", deps=["w1"]),
        ]
    )
    assert validate_plan_phase_a(plan) == []


def test_phase_a_validator_policy_enforces_hard_floor_and_stricter_overrides(monkeypatch):
    _patch_registry(monkeypatch, {"a", "validator"})
    no_validator_plan = Plan(
        items=[
            WorkItemSpec(agent_name="a", local_id="w1"),
            WorkItemSpec(agent_name="a", local_id="w2"),
            WorkItemSpec(agent_name="a", local_id="w3"),
        ]
    )

    issues = validate_plan_phase_a(no_validator_plan)
    assert any(
        "3 or more concrete non-planner items must include at least one terminal validator"
        in i["msg"]
        for i in issues
    )

    too_many_validators_plan = Plan(
        items=[
            WorkItemSpec(agent_name="validator", local_id="v1"),
            WorkItemSpec(agent_name="validator", local_id="v2"),
        ]
    )
    issues = validate_plan_phase_a(too_many_validators_plan, max_validators_per_plan=1)
    assert any("submitted plans may have at most 1" in i["msg"] for i in issues)


def test_phase_a_allows_one_or_two_developers_without_validators(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
        ]
    )

    assert validate_plan_phase_a(plan) == []


def test_phase_a_requires_terminal_validator_for_three_developers(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(agent_name="developer", local_id="dev3"),
        ]
    )

    issues = validate_plan_phase_a(plan)

    assert any(
        "plans with 3 or more concrete non-planner items must include at least one terminal validator"
        in issue["msg"]
        for issue in issues
    )


def test_phase_a_allows_two_developers_plus_child_planner_without_parent_validator(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "team_planner", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(
                agent_name="team_planner",
                local_id="child",
                kind=WorkItemKind.EXPANDABLE,
            ),
        ]
    )

    assert validate_plan_phase_a(plan) == []


def test_phase_a_accepts_terminal_validator_for_three_developers(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(agent_name="developer", local_id="dev3"),
            WorkItemSpec(
                agent_name="validator",
                local_id="val1",
                deps=["dev1", "dev2", "dev3"],
            ),
        ]
    )

    assert validate_plan_phase_a(plan) == []


def test_phase_a_rejects_three_validators(monkeypatch):
    _patch_registry(monkeypatch, {"validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="validator", local_id="val1"),
            WorkItemSpec(agent_name="validator", local_id="val2"),
            WorkItemSpec(agent_name="validator", local_id="val3"),
        ]
    )

    issues = validate_plan_phase_a(plan)

    assert any(
        "plan has 3 validator items; submitted plans may have at most 2" in issue["msg"]
        for issue in issues
    )


def test_phase_a_rejects_nonterminal_only_validator(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator", "a"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="validator", local_id="val1", deps=["dev1"]),
            WorkItemSpec(agent_name="a", local_id="followup", deps=["val1"]),
        ]
    )

    issues = validate_plan_phase_a(plan)

    assert any(
        "plans with validator items must leave at least one validator as a terminal end-of-chain guard"
        in issue["msg"]
        for issue in issues
    )


def test_phase_a_rejects_multiple_terminal_validators(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(agent_name="validator", local_id="val1", deps=["dev1"]),
            WorkItemSpec(agent_name="validator", local_id="val2", deps=["dev2"]),
        ]
    )

    issues = validate_plan_phase_a(plan)

    assert any(
        "plans with validator items must keep exactly one validator as the terminal end-of-chain guard"
        in issue["msg"]
        for issue in issues
    )


def test_phase_a_allows_midchain_validator_when_plan_still_ends_with_validator(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator", "a"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="validator", local_id="val_mid", deps=["dev1"]),
            WorkItemSpec(agent_name="a", local_id="followup", deps=["val_mid"]),
            WorkItemSpec(agent_name="validator", local_id="val_end", deps=["followup"]),
        ]
    )

    assert validate_plan_phase_a(plan) == []


def test_phase_a_accepts_one_grouped_validator_for_five_developers(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id=f"dev{i}")
            for i in range(1, 6)
        ]
        + [
            WorkItemSpec(
                agent_name="validator",
                local_id="val1",
                deps=["dev1", "dev2", "dev3", "dev4", "dev5"],
            )
        ]
    )

    assert validate_plan_phase_a(plan) == []


def test_phase_a_accepts_one_terminal_validator_for_six_developers(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id=f"dev{i}")
            for i in range(1, 7)
        ]
        + [
            WorkItemSpec(
                agent_name="validator",
                local_id="val1",
                deps=["dev1", "dev2", "dev3"],
            )
        ]
    )

    issues = validate_plan_phase_a(plan)

    assert issues == []


def test_phase_a_allows_developer_without_local_id_when_terminal_validator_exists(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(agent_name="validator", local_id="val1", deps=["dev2"]),
        ]
    )

    assert validate_plan_phase_a(plan) == []


def test_phase_a_rejects_unknown_dep_when_external_scope_is_known(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="w1"),
            WorkItemSpec(agent_name="validator", local_id="w2", deps=["missing_dep"]),
        ]
    )

    issues = validate_plan_phase_a(plan, known_external_deps={"EXISTING_WORK_ITEM"})

    assert any("unknown dep reference 'missing_dep'" in i["msg"] for i in issues)


def test_phase_a_rejects_benchmark_test_ref_aliases(monkeypatch):
    _patch_registry(monkeypatch, {"developer"})
    plan = Plan(
        items=[
            WorkItemSpec(
                agent_name="developer",
                payload={
                    "owned_failures": ["tests/test_hdf.py"],
                    "reproduction": ["pytest tests/test_hdf.py -q"],
                    "verify": ["pytest tests/test_hdf.py -q"],
                    "retries": ["pytest tests/test_hdf.py::test_read_hdf -q"],
                },
            )
        ]
    )

    issues = validate_plan_phase_a(
        plan,
        benchmark_test_files={"dask/dataframe/io/tests/test_hdf.py"},
    )

    assert any(
        "expected 'dask/dataframe/io/tests/test_hdf.py'" in issue["msg"]
        for issue in issues
    )
    assert any("payload.owned_failures[0]" in issue["field"] for issue in issues)
    assert any("payload.reproduction[0]" in issue["field"] for issue in issues)
    assert any("payload.verify[0]" in issue["field"] for issue in issues)
    assert any("payload.retries[0]" in issue["field"] for issue in issues)


def test_phase_a_accepts_exact_benchmark_test_refs(monkeypatch):
    _patch_registry(monkeypatch, {"developer"})
    exact_node = "dask/dataframe/io/tests/test_hdf.py::test_read_hdf"
    plan = Plan(
        items=[
            WorkItemSpec(
                agent_name="developer",
                payload={
                    "owned_failures": [exact_node],
                    "verification": ["pytest dask/dataframe/io/tests/test_hdf.py -q"],
                },
            )
        ]
    )

    issues = validate_plan_phase_a(
        plan,
        benchmark_test_ids={exact_node},
        benchmark_test_files={"dask/dataframe/io/tests/test_hdf.py"},
    )

    assert issues == []


# ---------- Phase B ----------------------------------------------------------


def _parent_wi(team_run_id="T1"):
    return WorkItem(
        id="PARENT",
        team_run_id=team_run_id,
        agent_name="planner",
        status=WorkItemStatus.RUNNING,
        kind=WorkItemKind.EXPANDABLE,
        root_id="PARENT",
        depth=0,
    )


def test_phase_b_resolves_local_ids(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    counter = {"n": 0}

    def fresh_id():
        counter["n"] += 1
        return f"NEW{counter['n']}"

    plan = Plan(
        items=[
            WorkItemSpec(agent_name="a", local_id="w1"),
            WorkItemSpec(agent_name="a", local_id="w2", deps=["w1"]),
        ]
    )
    parent = _parent_wi()
    existing = {parent.id: parent}
    new_items = validate_plan_phase_b(
        existing, plan, "T1", parent, new_id_factory=fresh_id, max_depth=5
    )
    assert len(new_items) == 2
    assert new_items[1].deps == [new_items[0].id]
    assert all(wi.depth == 1 for wi in new_items)
    assert all(wi.parent_id == "PARENT" for wi in new_items)


def test_phase_b_cross_run_dep_rejected(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    other = WorkItem(
        id="OTHER",
        team_run_id="OTHER_RUN",
        agent_name="a",
        status=WorkItemStatus.DONE,
    )
    parent = _parent_wi()
    existing = {parent.id: parent, other.id: other}
    plan = Plan(items=[WorkItemSpec(agent_name="a", deps=["OTHER"])])
    with pytest.raises(InvalidPlan, match="cross-run"):
        validate_plan_phase_b(
            existing, plan, "T1", parent, new_id_factory=lambda: "NEW", max_depth=5
        )


def test_phase_b_dangling_external_dep(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    parent = _parent_wi()
    plan = Plan(items=[WorkItemSpec(agent_name="a", deps=["nonexistent"])])
    with pytest.raises(InvalidPlan, match="not found"):
        validate_plan_phase_b(
            {parent.id: parent}, plan, "T1", parent, new_id_factory=lambda: "N", max_depth=5
        )


def test_phase_b_depth_exceeded(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    parent = _parent_wi()
    parent.depth = 5
    plan = Plan(items=[WorkItemSpec(agent_name="a")])
    with pytest.raises(InvalidPlan, match="max_depth"):
        validate_plan_phase_b(
            {parent.id: parent}, plan, "T1", parent, new_id_factory=lambda: "N", max_depth=5
        )


def test_phase_b_enforces_max_plan_size(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    parent = _parent_wi()
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="a", local_id="w1"),
            WorkItemSpec(agent_name="a", local_id="w2"),
        ]
    )
    with pytest.raises(InvalidPlan, match="max_plan_size"):
        validate_plan_phase_b(
            {parent.id: parent},
            plan,
            "T1",
            parent,
            new_id_factory=lambda: "N",
            max_depth=5,
            max_plan_size=1,
        )


def test_phase_b_validator_policy_enforces_hard_floor_and_stricter_overrides(monkeypatch):
    _patch_registry(monkeypatch, {"a", "validator"})
    parent = _parent_wi()
    no_validator_plan = Plan(
        items=[
            WorkItemSpec(agent_name="a", local_id="w1"),
            WorkItemSpec(agent_name="a", local_id="w2"),
            WorkItemSpec(agent_name="a", local_id="w3"),
        ]
    )
    with pytest.raises(
        InvalidPlan,
        match="3 or more concrete non-planner items must include at least one terminal validator",
    ):
        validate_plan_phase_b(
            {parent.id: parent},
            no_validator_plan,
            "T1",
            parent,
            new_id_factory=lambda: "N1",
            max_depth=5,
        )
    too_many_validators_plan = Plan(
        items=[
            WorkItemSpec(agent_name="validator", local_id="v1"),
            WorkItemSpec(agent_name="validator", local_id="v2"),
        ]
    )
    with pytest.raises(InvalidPlan, match="submitted plans may have at most 1"):
        validate_plan_phase_b(
            {parent.id: parent},
            too_many_validators_plan,
            "T1",
            parent,
            new_id_factory=lambda: "N2",
            max_depth=5,
            max_validators_per_plan=1,
        )


def test_phase_b_requires_terminal_validator_for_three_developers(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    parent = _parent_wi()
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(agent_name="developer", local_id="dev3"),
        ]
    )

    with pytest.raises(
        InvalidPlan,
        match="plans with 3 or more concrete non-planner items must include at least one terminal validator",
    ):
        validate_plan_phase_b(
            {parent.id: parent},
            plan,
            "T1",
            parent,
            new_id_factory=lambda: "N1",
            max_depth=5,
        )


def test_phase_b_accepts_terminal_validator_for_three_developers(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    parent = _parent_wi()
    counter = {"n": 0}

    def fresh_id():
        counter["n"] += 1
        return f"N{counter['n']}"

    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(agent_name="developer", local_id="dev3"),
            WorkItemSpec(agent_name="validator", local_id="val1", deps=["dev1", "dev2", "dev3"]),
        ]
    )

    new_items = validate_plan_phase_b(
        {parent.id: parent},
        plan,
        "T1",
        parent,
        new_id_factory=fresh_id,
        max_depth=5,
    )

    assert [wi.local_id for wi in new_items] == ["dev1", "dev2", "dev3", "val1"]
    assert new_items[3].deps == [new_items[0].id, new_items[1].id, new_items[2].id]


def test_phase_b_allows_two_developers_plus_child_planner_without_parent_validator(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "team_planner", "validator"})
    parent = _parent_wi()
    counter = {"n": 0}

    def fresh_id():
        counter["n"] += 1
        return f"N{counter['n']}"

    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(
                agent_name="team_planner",
                local_id="child",
                kind=WorkItemKind.EXPANDABLE,
            ),
        ]
    )

    new_items = validate_plan_phase_b(
        {parent.id: parent},
        plan,
        "T1",
        parent,
        new_id_factory=fresh_id,
        max_depth=5,
    )

    assert [wi.local_id for wi in new_items] == ["dev1", "dev2", "child"]


def test_phase_b_rejects_three_validators(monkeypatch):
    _patch_registry(monkeypatch, {"validator"})
    parent = _parent_wi()
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="validator", local_id="val1"),
            WorkItemSpec(agent_name="validator", local_id="val2"),
            WorkItemSpec(agent_name="validator", local_id="val3"),
        ]
    )

    with pytest.raises(
        InvalidPlan,
        match="plan has 3 validator items; submitted plans may have at most 2",
    ):
        validate_plan_phase_b(
            {parent.id: parent},
            plan,
            "T1",
            parent,
            new_id_factory=lambda: "N1",
            max_depth=5,
        )


def test_phase_b_rejects_nonterminal_only_validator(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator", "a"})
    parent = _parent_wi()
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="validator", local_id="val1", deps=["dev1"]),
            WorkItemSpec(agent_name="a", local_id="followup", deps=["val1"]),
        ]
    )

    with pytest.raises(
        InvalidPlan,
        match="plans with validator items must leave at least one validator as a terminal end-of-chain guard",
    ):
        validate_plan_phase_b(
            {parent.id: parent},
            plan,
            "T1",
            parent,
            new_id_factory=lambda: "N1",
            max_depth=5,
        )


def test_phase_b_rejects_multiple_terminal_validators(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    parent = _parent_wi()
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="developer", local_id="dev2"),
            WorkItemSpec(agent_name="validator", local_id="val1", deps=["dev1"]),
            WorkItemSpec(agent_name="validator", local_id="val2", deps=["dev2"]),
        ]
    )

    with pytest.raises(
        InvalidPlan,
        match="plans with validator items must keep exactly one validator as the terminal end-of-chain guard",
    ):
        validate_plan_phase_b(
            {parent.id: parent},
            plan,
            "T1",
            parent,
            new_id_factory=lambda: "N1",
            max_depth=5,
        )


def test_phase_b_allows_midchain_validator_when_plan_still_ends_with_validator(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator", "a"})
    parent = _parent_wi()
    counter = {"n": 0}

    def fresh_id():
        counter["n"] += 1
        return f"N{counter['n']}"

    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1"),
            WorkItemSpec(agent_name="validator", local_id="val_mid", deps=["dev1"]),
            WorkItemSpec(agent_name="a", local_id="followup", deps=["val_mid"]),
            WorkItemSpec(agent_name="validator", local_id="val_end", deps=["followup"]),
        ]
    )

    new_items = validate_plan_phase_b(
        {parent.id: parent},
        plan,
        "T1",
        parent,
        new_id_factory=fresh_id,
        max_depth=5,
    )

    assert [wi.local_id for wi in new_items] == ["dev1", "val_mid", "followup", "val_end"]
    assert new_items[1].deps == [new_items[0].id]
    assert new_items[2].deps == [new_items[1].id]
    assert new_items[3].deps == [new_items[2].id]


def test_phase_b_accepts_one_grouped_validator_for_five_developers(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    parent = _parent_wi()
    counter = {"n": 0}

    def fresh_id():
        counter["n"] += 1
        return f"N{counter['n']}"

    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id=f"dev{i}")
            for i in range(1, 6)
        ]
        + [
            WorkItemSpec(
                agent_name="validator",
                local_id="val1",
                deps=["dev1", "dev2", "dev3", "dev4", "dev5"],
            )
        ]
    )

    new_items = validate_plan_phase_b(
        {parent.id: parent},
        plan,
        "T1",
        parent,
        new_id_factory=fresh_id,
        max_depth=5,
    )

    assert [wi.local_id for wi in new_items] == ["dev1", "dev2", "dev3", "dev4", "dev5", "val1"]
    assert new_items[5].deps == [
        new_items[0].id,
        new_items[1].id,
        new_items[2].id,
        new_items[3].id,
        new_items[4].id,
    ]


def test_phase_b_accepts_one_terminal_validator_for_six_developers(monkeypatch):
    _patch_registry(monkeypatch, {"developer", "validator"})
    parent = _parent_wi()
    counter = {"n": 0}

    def fresh_id():
        counter["n"] += 1
        return f"N{counter['n']}"

    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id=f"dev{i}")
            for i in range(1, 7)
        ]
        + [
            WorkItemSpec(agent_name="validator", local_id="val1", deps=["dev1", "dev2", "dev3"])
        ]
    )

    new_items = validate_plan_phase_b(
        {parent.id: parent},
        plan,
        "T1",
        parent,
        new_id_factory=fresh_id,
        max_depth=5,
    )

    assert [wi.local_id for wi in new_items] == ["dev1", "dev2", "dev3", "dev4", "dev5", "dev6", "val1"]
    assert new_items[6].deps == [new_items[0].id, new_items[1].id, new_items[2].id]


def test_phase_b_rejects_agent_without_supported_kind(monkeypatch):
    from agents.types import AgentDefinition
    from team.planning import validation as _v

    atomic_only = AgentDefinition(
        name="atomic_only", description="d", supported_kinds=["atomic"]
    )
    monkeypatch.setattr(_v, "_get_definition", lambda n: atomic_only if n == "atomic_only" else None)
    parent = _parent_wi()
    plan = Plan(
        items=[
            WorkItemSpec(
                agent_name="atomic_only", local_id="w1", kind=WorkItemKind.EXPANDABLE
            )
        ]
    )
    with pytest.raises(InvalidPlan, match="does not support kind"):
        validate_plan_phase_b(
            {parent.id: parent}, plan, "T1", parent, new_id_factory=lambda: "N", max_depth=5
        )


def test_phase_a_rejects_agent_without_supported_kind(monkeypatch):
    from agents.types import AgentDefinition
    from team.planning import validation as _v

    atomic_only = AgentDefinition(
        name="atomic_only", description="d", supported_kinds=["atomic"]
    )
    monkeypatch.setattr(
        _v,
        "_get_definition",
        lambda n: atomic_only if n == "atomic_only" else None,
    )
    monkeypatch.setattr(_v, "_agent_exists", lambda n: n == "atomic_only")
    plan = Plan(
        items=[
            WorkItemSpec(
                agent_name="atomic_only", local_id="w1", kind=WorkItemKind.EXPANDABLE
            )
        ]
    )

    issues = validate_plan_phase_a(plan)

    assert any("does not support kind 'expandable'" in issue["msg"] for issue in issues)


def test_phase_a_accepts_custom_atomic_agent_with_supported_kind(monkeypatch):
    from agents.types import AgentDefinition
    from team.planning import validation as _v

    worker = AgentDefinition(name="worker", description="d", supported_kinds=["atomic"])
    monkeypatch.setattr(
        _v,
        "_get_definition",
        lambda n: worker if n == "worker" else None,
    )
    monkeypatch.setattr(_v, "_agent_exists", lambda n: n == "worker")
    plan = Plan(items=[WorkItemSpec(agent_name="worker", local_id="w1")])

    assert validate_plan_phase_a(plan) == []


def test_phase_b_rejects_validator_depending_on_expandable_sibling(monkeypatch):
    from agents.types import AgentDefinition
    from team.planning import validation as _v

    developer_def = AgentDefinition(name="developer", description="d")
    planner_def = AgentDefinition(name="team_planner", description="d")
    validator_def = AgentDefinition(name="validator", description="d")
    monkeypatch.setattr(
        _v,
        "_get_definition",
        lambda n: {
            "developer": developer_def,
            "team_planner": planner_def,
            "validator": validator_def,
        }.get(n),
    )

    counter = {"n": 0}

    def fresh_id():
        counter["n"] += 1
        return f"NEW{counter['n']}"

    parent = _parent_wi()
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1", kind=WorkItemKind.ATOMIC),
            WorkItemSpec(
                agent_name="team_planner",
                local_id="child",
                kind=WorkItemKind.EXPANDABLE,
                deps=["dev1"],
            ),
            WorkItemSpec(
                agent_name="validator",
                local_id="val1",
                kind=WorkItemKind.ATOMIC,
                deps=["child"],
            ),
        ]
    )

    with pytest.raises(
        InvalidPlan,
        match="validator items must not depend on expandable siblings",
    ):
        validate_plan_phase_b(
            {parent.id: parent}, plan, "T1", parent, new_id_factory=fresh_id, max_depth=5
        )


def test_phase_b_combined_cycle(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    existing_parent = _parent_wi()
    # Existing WI W0 depends on (future) new item we'll try to emit that then
    # depends back on W0 — combined graph is cyclic.
    w0 = WorkItem(
        id="W0",
        team_run_id="T1",
        agent_name="a",
        status=WorkItemStatus.RUNNING,
        deps=["NEW1"],  # forward-referencing a soon-to-be-created item
    )
    graph = {existing_parent.id: existing_parent, w0.id: w0}
    plan = Plan(items=[WorkItemSpec(agent_name="a", local_id="lid", deps=["W0"])])

    def fresh_id():
        return "NEW1"

    with pytest.raises(InvalidPlan, match="cycle"):
        validate_plan_phase_b(
            graph, plan, "T1", existing_parent, new_id_factory=fresh_id, max_depth=5
        )


# ---------- ArtifactStore ----------------------------------------------------


def test_artifact_store_byte_caps():
    budgets = BudgetConfig(max_artifact_bytes=50, max_total_artifact_bytes=80)
    state = BudgetState()
    store = InMemoryArtifactStore(budgets, state)
    store.save("a", "x" * 40)
    assert state.artifact_bytes_used >= 40
    with pytest.raises(ArtifactTooLarge):
        store.save("b", "x" * 100)  # per-artifact cap
    with pytest.raises(ArtifactTooLarge):
        store.save("c", "x" * 45)  # total cap


def test_artifact_store_replace_releases_old_bytes():
    budgets = BudgetConfig(max_artifact_bytes=1000, max_total_artifact_bytes=200)
    state = BudgetState()
    store = InMemoryArtifactStore(budgets, state)
    store.save("a", "x" * 150)
    store.save("a", "y" * 10)  # replace — should free the 150
    assert state.artifact_bytes_used == 10


# ---------- Briefing ---------------------------------------------------------


def test_briefing_artifact_xor():
    b = Briefing(name="auth", source="artifact", ref="art1")
    assert b.ref == "art1" and b.inline is None


def test_briefing_inline_xor():
    b = Briefing(name="auth", source="inline", inline="notes")
    assert b.inline == "notes" and b.ref is None


def test_briefing_rejects_both_ref_and_inline():
    with pytest.raises(ValueError):
        Briefing(name="a", source="artifact", ref="r", inline="i")


def test_briefing_rejects_missing_payload():
    with pytest.raises(ValueError):
        Briefing(name="a", source="artifact")
    with pytest.raises(ValueError):
        Briefing(name="a", source="inline")


def test_briefing_rejects_empty_name():
    with pytest.raises(ValueError):
        Briefing(name="", source="inline", inline="x")


def test_briefing_rejects_bad_source():
    with pytest.raises(ValueError):
        Briefing(name="a", source="bogus", inline="x")


def test_plan_from_dict_preserves_briefings():
    data = {
        "items": [
            {
                "agent_name": "a",
                "local_id": "w1",
                "briefings": [
                    {"name": "ctx", "source": "inline", "inline": "hi"},
                    {"name": "art", "source": "artifact", "ref": "A1"},
                ],
            }
        ]
    }
    plan = Plan.from_dict(data)
    assert len(plan.items[0].briefings) == 2
    assert plan.items[0].briefings[0].inline == "hi"
    assert plan.items[0].briefings[1].ref == "A1"


def test_phase_a_duplicate_briefing_names(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    plan = Plan(
        items=[
            WorkItemSpec(
                agent_name="a",
                local_id="w1",
                briefings=[
                    Briefing(name="dup", source="inline", inline="x"),
                    Briefing(name="dup", source="inline", inline="y"),
                ],
            )
        ]
    )
    issues = validate_plan_phase_a(plan)
    assert any("duplicate briefing" in i["msg"] for i in issues)


def test_phase_a_inline_briefing_bytes_cap(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    big = "x" * 5000
    plan = Plan(
        items=[
            WorkItemSpec(
                agent_name="a",
                local_id="w1",
                briefings=[Briefing(name="b", source="inline", inline=big)],
            )
        ]
    )
    issues = validate_plan_phase_a(plan)
    assert any("inline briefing bytes" in i["msg"] for i in issues)


def test_phase_b_preserves_local_id_and_briefings(monkeypatch):
    _patch_registry(monkeypatch, {"a"})
    parent = _parent_wi()
    plan = Plan(
        items=[
            WorkItemSpec(
                agent_name="a",
                local_id="w1",
                briefings=[Briefing(name="b", source="inline", inline="hi")],
            )
        ]
    )
    new_items = validate_plan_phase_b(
        {parent.id: parent}, plan, "T1", parent, new_id_factory=lambda: "NEW1", max_depth=5
    )
    assert new_items[0].local_id == "w1"
    assert len(new_items[0].briefings) == 1
    assert new_items[0].briefings[0].inline == "hi"
    assert new_items[0].dep_artifacts == []


def test_phase_a_rejects_worker_depending_on_subagent_sibling(monkeypatch):
    from agents.types import AgentDefinition
    from team.planning import validation as _v

    scout_def = AgentDefinition(name="scout", description="d", agent_type="subagent")
    worker_def = AgentDefinition(name="worker", description="d")
    monkeypatch.setattr(
        _v,
        "_get_definition",
        lambda n: {"scout": scout_def, "worker": worker_def}.get(n),
    )
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="scout", local_id="s1"),
            WorkItemSpec(
                agent_name="worker",
                local_id="w1",
                deps=["s1"],
                kind=WorkItemKind.ATOMIC,
            ),
        ]
    )
    issues = validate_plan_phase_a(plan)
    assert any("subagent sibling" in i["msg"] for i in issues)


def test_phase_a_allows_expandable_planner_depending_on_subagent(monkeypatch):
    from agents.types import AgentDefinition
    from team.planning import validation as _v

    scout_def = AgentDefinition(name="scout", description="d", agent_type="subagent")
    planner_def = AgentDefinition(name="planner", description="d")
    monkeypatch.setattr(
        _v,
        "_get_definition",
        lambda n: {"scout": scout_def, "planner": planner_def}.get(n),
    )
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="scout", local_id="s1"),
            WorkItemSpec(
                agent_name="planner",
                local_id="p1",
                deps=["s1"],
                kind=WorkItemKind.EXPANDABLE,
            ),
        ]
    )
    issues = validate_plan_phase_a(plan)
    assert not any("subagent sibling" in i["msg"] for i in issues)


def test_phase_a_allows_ready_expandable_item_in_mixed_plan(monkeypatch):
    from agents.types import AgentDefinition
    from team.planning import validation as _v

    developer_def = AgentDefinition(name="developer", description="d")
    planner_def = AgentDefinition(name="team_planner", description="d")
    monkeypatch.setattr(
        _v,
        "_get_definition",
        lambda n: {"developer": developer_def, "team_planner": planner_def}.get(n),
    )
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1", kind=WorkItemKind.ATOMIC),
            WorkItemSpec(
                agent_name="team_planner",
                local_id="p1",
                kind=WorkItemKind.EXPANDABLE,
            ),
        ]
    )

    issues = validate_plan_phase_a(plan)

    assert not issues


def test_phase_a_rejects_validator_depending_on_expandable_sibling(monkeypatch):
    from agents.types import AgentDefinition
    from team.planning import validation as _v

    developer_def = AgentDefinition(name="developer", description="d")
    planner_def = AgentDefinition(name="team_planner", description="d")
    validator_def = AgentDefinition(name="validator", description="d")
    monkeypatch.setattr(
        _v,
        "_get_definition",
        lambda n: {
            "developer": developer_def,
            "team_planner": planner_def,
            "validator": validator_def,
        }.get(n),
    )
    plan = Plan(
        items=[
            WorkItemSpec(agent_name="developer", local_id="dev1", kind=WorkItemKind.ATOMIC),
            WorkItemSpec(
                agent_name="team_planner",
                local_id="child",
                kind=WorkItemKind.EXPANDABLE,
                deps=["dev1"],
            ),
            WorkItemSpec(
                agent_name="validator",
                local_id="val1",
                kind=WorkItemKind.ATOMIC,
                deps=["child"],
            ),
        ]
    )

    issues = validate_plan_phase_a(plan)

    assert any(
        "validator items must not depend on expandable siblings" in issue["msg"]
        for issue in issues
    )


def test_submit_plan_item_parses_briefings_and_kind():
    from tools.posthook.submit_plan import _SubmitPlanItem

    item = _SubmitPlanItem.model_validate(
        {
            "agent_name": "a",
            "kind": "expandable",
            "briefings": [
                {"name": "ctx", "source": "inline", "inline": "hello"},
            ],
        }
    )
    assert item.kind == WorkItemKind.EXPANDABLE
    assert item.briefings[0].name == "ctx"
    assert item.briefings[0].source == "inline"


def test_submit_plan_input_extracts_items_from_embedded_json_array_string():
    from tools.posthook.submit_plan import SubmitPlanInput

    parsed = SubmitPlanInput.model_validate(
        {
            "items": 'planner said: [{"agent_name": "developer", "local_id": "w1"}] thanks',
            "rationale": "keep it moving",
        }
    )

    assert len(parsed.items) == 1
    assert parsed.items[0].agent_name == "developer"
    assert parsed.items[0].local_id == "w1"


def test_submit_replan_input_extracts_cancel_ids_from_embedded_json_array_string():
    from tools.posthook.submit_replan import SubmitReplanInput

    parsed = SubmitReplanInput.model_validate(
        {
            "add_items": [],
            "cancel_ids": 'cancel these -> ["W1", "W2"] <- now',
        }
    )

    assert parsed.cancel_ids == ["W1", "W2"]


def test_artifact_store_snapshot_restore():
    budgets = BudgetConfig()
    state = BudgetState()
    store = InMemoryArtifactStore(budgets, state)
    store.save("a", {"v": 1})
    store.save("b", {"v": 2})
    snap = store.snapshot()
    store.save("a", {"v": 999})
    store.delete("b")
    store.restore(snap)
    assert store.load("a") == {"v": 1}
    assert store.load("b") == {"v": 2}
