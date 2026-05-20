"""US-005: AgentDefinition variants + context_recipe round-trip."""

from __future__ import annotations

import pytest

from agents import AgentDefinition, AgentVariant


def test_variant_round_trips_through_pydantic():
    defn = AgentDefinition(
        name="planner",
        description="planner",
        context_recipe="planner",
        variants=[
            AgentVariant(
                when="nested_goal_depth_gt_1",
                use="planner_full",
                note="depth >1 — nested planner inside another goal's attempt",
            )
        ],
    )
    payload = defn.model_dump()
    restored = AgentDefinition.model_validate(payload)
    assert restored.context_recipe == "planner"
    assert restored.variants[0].use == "planner_full"
    assert restored.variants[0].when == "nested_goal_depth_gt_1"


def test_definition_default_variants_is_empty():
    defn = AgentDefinition(name="x", description="x")
    assert defn.variants == []
    assert defn.context_recipe is None


def test_variant_extra_fields_rejected():
    with pytest.raises(Exception):
        AgentVariant(
            when="x", use="y", note="", unknown="bad"  # type: ignore[arg-type]
        )
