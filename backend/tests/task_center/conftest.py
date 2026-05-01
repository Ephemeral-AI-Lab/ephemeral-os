"""Shared fixtures for task_center tests: in-memory SQLite DB + stores."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents import registry as agents_registry
from agents.types import AgentDefinition
from db.base import Base
import db.models  # noqa: F401  - populates Base.metadata
from db.models.task_center import TaskCenterRequestRecord, TaskCenterRunRecord
from db.stores.complex_task_request_store import ComplexTaskRequestStore
from db.stores.context_packet_store import ContextPacketStore
from db.stores.harness_graph_store import HarnessGraphStore
from db.stores.task_center_store import TaskCenterStore
from db.stores.task_segment_store import TaskSegmentStore
from task_center.context_engine.composer import ContextComposer
from task_center.context_engine.engine import ContextEngine, ContextEngineDeps
from task_center.agent_launch.predicates import (
    PredicateRegistry,
    register_builtin_predicates,
)
from task_center.context_engine.recipes import register_builtin_recipes
from task_center.context_engine.recipes_registry import RecipeRegistry


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    # Seed parent task_center_run for FK satisfaction.
    with sf() as s:
        s.add(
            TaskCenterRequestRecord(
                id="req1",
                cwd="/tmp",
                sandbox_id=None,
                request_prompt="prompt",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        s.add(
            TaskCenterRunRecord(
                id="run1",
                request_id="req1",
                status="running",
                started_at=datetime.now(UTC),
            )
        )
        s.commit()
    yield sf
    engine.dispose()


@pytest.fixture
def request_store(session_factory) -> ComplexTaskRequestStore:
    store = ComplexTaskRequestStore()
    store.initialize(session_factory)
    return store


@pytest.fixture
def segment_store(session_factory) -> TaskSegmentStore:
    store = TaskSegmentStore()
    store.initialize(session_factory)
    return store


@pytest.fixture
def graph_store(session_factory) -> HarnessGraphStore:
    store = HarnessGraphStore()
    store.initialize(session_factory)
    return store


@pytest.fixture
def task_store(session_factory) -> TaskCenterStore:
    store = TaskCenterStore()
    store.initialize(session_factory)
    return store


@pytest.fixture
def context_packet_store(session_factory) -> ContextPacketStore:
    store = ContextPacketStore()
    store.initialize(session_factory)
    return store


@pytest.fixture
def task_center_run_id() -> str:
    return "run1"


# ---------------------------------------------------------------------------
# Composer fixture for harness-graph lifecycle tests
# ---------------------------------------------------------------------------
#
# Production paths (orchestrator + dispatcher + entry coordinator) require a
# ``ContextComposer`` on ``HarnessGraphRuntime``. Lifecycle tests that exercise
# planner/generator/evaluator launches need (a) a composer wired into the
# runtime, (b) registered context recipes + predicates, and (c) minimal test
# agent definitions so the resolver can look up a target agent.
#
# Tests opt in by depending on the ``composer`` fixture below.


@pytest.fixture
def isolated_agent_registries():
    """Save + restore predicate / recipe / agent registries for test isolation."""
    saved_predicates = dict(PredicateRegistry._registry)
    saved_recipes = dict(RecipeRegistry._registry)
    saved_definitions = dict(agents_registry._DEFINITIONS)
    PredicateRegistry.clear()
    RecipeRegistry.clear()
    agents_registry._DEFINITIONS.clear()
    register_builtin_predicates()
    register_builtin_recipes()
    yield
    PredicateRegistry.clear()
    RecipeRegistry.clear()
    agents_registry._DEFINITIONS.clear()
    PredicateRegistry._registry.update(saved_predicates)
    RecipeRegistry._registry.update(saved_recipes)
    agents_registry._DEFINITIONS.update(saved_definitions)


@pytest.fixture
def register_test_agents(isolated_agent_registries):
    """Register the bare-minimum agents needed by lifecycle tests.

    Provides ``planner``, ``executor``, ``generator``, ``evaluator`` definitions
    each wired to its corresponding ``*_v1`` recipe. Tests that need a
    different shape can register their own definitions on top — agent names
    are unique per test thanks to ``isolated_agent_registries`` cleanup.
    """
    agents_registry.register_definition(
        AgentDefinition(
            name="planner",
            description="test planner",
            role="planner",
            context_recipe="planner_v1",
            terminals=["submit_full_plan", "submit_partial_plan"],
        )
    )
    agents_registry.register_definition(
        AgentDefinition(
            name="executor",
            description="test executor",
            role="executor",
            context_recipe="generator_v1",
            terminals=[
                "request_complex_task_solution",
                "submit_execution_success",
                "submit_execution_failure",
            ],
        )
    )
    agents_registry.register_definition(
        AgentDefinition(
            name="generator",
            description="test generator",
            role="generator",
            context_recipe="generator_v1",
            terminals=["submit_execution_success", "submit_execution_failure"],
        )
    )
    agents_registry.register_definition(
        AgentDefinition(
            name="evaluator",
            description="test evaluator",
            role="evaluator",
            context_recipe="evaluator_v1",
            terminals=["submit_evaluation"],
        )
    )
    agents_registry.register_definition(
        AgentDefinition(
            name="verifier",
            description="test verifier",
            role="generator",
            context_recipe="generator_v1",
            terminals=["submit_execution_success", "submit_execution_failure"],
        )
    )
    yield


@pytest.fixture
def composer(
    request_store,
    segment_store,
    graph_store,
    task_store,
    context_packet_store,
    register_test_agents,
) -> ContextComposer:
    """Real ContextComposer wired against the in-memory stores."""
    deps = ContextEngineDeps(
        request_store=request_store,
        segment_store=segment_store,
        graph_store=graph_store,
        task_store=task_store,
        context_packet_store=context_packet_store,
    )
    return ContextComposer.default(ContextEngine(deps))
