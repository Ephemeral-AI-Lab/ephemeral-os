"""US-011: advisor / resolver parent inheritance with priority demotion."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.models  # noqa: F401 — populate Base.metadata
from db.base import Base
from db.stores.context_packet_store import ContextPacketStore
from task_center.context_engine.core import (
    ContextEngineDeps,
    ContextEngineError,
    RecipeScopeError,
)
from task_center.context_engine.packet import (
    ContextBlock,
    ContextPacket,
    ContextPriority,
    ContextRefs,
)
from task_center.context_engine.recipes.advisor_resolver import (
    _advisor_build,
    demote_priority,
    _resolver_build,
)
from task_center.context_engine.scope import ContextScope


@pytest.fixture
def packet_store():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    store = ContextPacketStore()
    store.initialize(sf)
    yield store
    engine.dispose()


@pytest.fixture
def deps_with_packet_store(
    goal_store, iteration_store, attempt_store, task_store, packet_store
) -> ContextEngineDeps:
    return ContextEngineDeps(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        context_packet_store=packet_store,
    )


def _seed_parent_packet(packet_store) -> ContextPacket:
    packet = ContextPacket(
        target_role="planner",
        target_id="g-parent",
        canonical_refs=ContextRefs(goal_id="req-A", attempt_id="g-parent"),
        blocks=[
            ContextBlock(
                kind="iteration_statement",
                priority=ContextPriority.REQUIRED,
                text="parent goal",
            ),
            ContextBlock(
                kind="prior_iteration_summary",
                priority=ContextPriority.HIGH,
                text="parent summary",
            ),
            ContextBlock(
                kind="dependency_summary",
                priority=ContextPriority.MEDIUM,
                text="dep info",
            ),
            ContextBlock(
                kind="background",
                priority=ContextPriority.LOW,
                text="bg info",
            ),
        ],
    )
    packet_store.insert(packet)
    return packet


def _seed_parent_task(task_store, *, task_center_run_id, task_id, question):
    task_store.upsert_task(
        task_id=task_id,
        task_center_run_id=task_center_run_id,
        role="generator",
        agent_name="executor",
        context_message=question,
        status="running",
        summaries=[],
        needs=[],
        task_center_attempt_id="g-parent",
        spawn_reason="attempt_generator",
    )


def test_demotion_table_covers_all_priorities():
    assert demote_priority(ContextPriority.REQUIRED) == ContextPriority.HIGH
    assert demote_priority(ContextPriority.HIGH) == ContextPriority.MEDIUM
    assert demote_priority(ContextPriority.MEDIUM) == ContextPriority.LOW
    assert demote_priority(ContextPriority.LOW) == ContextPriority.LOW


def test_advisor_emits_only_demoted_inherited_parent_context(
    deps_with_packet_store, packet_store, task_store, task_center_run_id
):
    parent_packet = _seed_parent_packet(packet_store)
    _seed_parent_task(
        task_store,
        task_center_run_id=task_center_run_id,
        task_id="t-parent",
        question="advise me on X",
    )
    scope = ContextScope(
        goal_id="req-A",
        task_id="helper-1",
        parent_packet_id=parent_packet.id,
        parent_task_id="t-parent",
    )
    packet = _advisor_build(scope, deps_with_packet_store)

    assert packet.target_role == "advisor"

    inherited = packet.blocks
    assert len(inherited) == 4
    expected_priorities = [
        ContextPriority.HIGH,    # required → high
        ContextPriority.MEDIUM,  # high → medium
        ContextPriority.LOW,     # medium → low
        ContextPriority.LOW,     # low → low (floor)
    ]
    for block, expected in zip(inherited, expected_priorities, strict=False):
        assert block.priority == expected
        assert block.metadata["inherited_from_parent"] == "true"


def test_resolver_same_shape_target_role_resolver(
    deps_with_packet_store, packet_store, task_store, task_center_run_id
):
    parent_packet = _seed_parent_packet(packet_store)
    _seed_parent_task(
        task_store,
        task_center_run_id=task_center_run_id,
        task_id="t-parent",
        question="resolve question",
    )
    scope = ContextScope(
        goal_id="req-A",
        task_id="resolver-1",
        parent_packet_id=parent_packet.id,
        parent_task_id="t-parent",
    )
    packet = _resolver_build(scope, deps_with_packet_store)
    assert packet.target_role == "resolver"
    assert packet.blocks[0].kind == "iteration_statement"


def test_missing_parent_packet_raises_context_engine_error(
    deps_with_packet_store, task_store, task_center_run_id
):
    _seed_parent_task(
        task_store,
        task_center_run_id=task_center_run_id,
        task_id="t-parent",
        question="q",
    )
    scope = ContextScope(
        goal_id="req-A",
        task_id="helper-1",
        parent_packet_id="missing-packet",
        parent_task_id="t-parent",
    )
    with pytest.raises(ContextEngineError):
        _advisor_build(scope, deps_with_packet_store)


def test_missing_packet_store_raises_context_engine_error(
    goal_store, iteration_store, attempt_store, task_store, task_center_run_id
):
    deps = ContextEngineDeps(
        goal_store=goal_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        task_store=task_store,
        context_packet_store=None,
    )
    _seed_parent_task(
        task_store,
        task_center_run_id=task_center_run_id,
        task_id="t-parent",
        question="q",
    )
    scope = ContextScope(
        goal_id="req-A",
        task_id="helper-1",
        parent_packet_id="any",
        parent_task_id="t-parent",
    )
    with pytest.raises(ContextEngineError):
        _advisor_build(scope, deps)


def test_helper_required_scope_fields_enforced():
    """Recipe registry's scope assertion fires before recipe build."""
    from task_center.context_engine.recipes.advisor_resolver import (
        ADVISOR_RECIPE,
        RESOLVER_RECIPE,
    )
    for recipe in (ADVISOR_RECIPE, RESOLVER_RECIPE):
        scope = ContextScope(goal_id="r")
        with pytest.raises(RecipeScopeError):
            scope.assert_fields(recipe.required_scope_fields)


def test_helper_inheritance_filters_role_instruction(
    deps_with_packet_store, packet_store, task_store, task_center_run_id
):
    """Inherited blocks must NOT include kind=='role_instruction'.

    Two-user-message launch shape: the helper tool appends its OWN
    role_instruction post-compose; inheriting the parent's role_instruction
    would concatenate two unrelated instructions into the helper's user msg 2.
    """
    parent_packet = ContextPacket(
        target_role="planner",
        target_id="g-parent",
        canonical_refs=ContextRefs(goal_id="req-A", attempt_id="g-parent"),
        blocks=[
            ContextBlock(
                kind="iteration_statement",
                priority=ContextPriority.REQUIRED,
                text="parent goal",
            ),
            ContextBlock(
                kind="role_instruction",
                priority=ContextPriority.REQUIRED,
                text="parent role instruction — must not leak to helper",
            ),
            ContextBlock(
                kind="dependency_summary",
                priority=ContextPriority.MEDIUM,
                text="dep info",
            ),
        ],
    )
    packet_store.insert(parent_packet)
    _seed_parent_task(
        task_store,
        task_center_run_id=task_center_run_id,
        task_id="t-parent",
        question="advise me",
    )

    scope = ContextScope(
        goal_id="req-A",
        task_id="helper-1",
        parent_packet_id=parent_packet.id,
        parent_task_id="t-parent",
    )
    helper_packet = _advisor_build(scope, deps_with_packet_store)

    inherited_kinds = [b.kind for b in helper_packet.blocks]
    assert "role_instruction" not in inherited_kinds
    # The other blocks still inherit (priority-demoted).
    assert "iteration_statement" in inherited_kinds
    assert "dependency_summary" in inherited_kinds
    # Sanity: the parent's role_instruction text is NOT present anywhere.
    for block in helper_packet.blocks:
        assert "parent role instruction" not in block.text
