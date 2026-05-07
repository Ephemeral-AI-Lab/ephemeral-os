"""Phase 04 executor/verifier profile gate tests."""

from __future__ import annotations

import pytest

from tools.execution.tool_call import execute_tool_once
from tools.submission.main_agent.generator import request_mission_solution
from tools.submission.main_agent.generator.executor import (
    submit_execution_failure,
    submit_execution_success,
)
from tools.submission.main_agent.generator.verifier import (
    submit_verification_failure,
    submit_verification_success,
)

from .submission_test_utils import (
    apply_single_generator_plan,
    build_harness_fixture,
    make_tool_context,
)

pytestmark = pytest.mark.asyncio


async def _noop_emit(event) -> None:
    del event


async def test_executor_profile_required_for_mission(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        request_mission_solution,
        {"goal": "delegated"},
        make_tool_context(fixture, generator_id, role="verifier"),
        emit=_noop_emit,
    )

    assert result.is_error
    assert "executor agent profile" in result.output


async def test_executor_profile_required_for_execution_terminals(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture)

    success = await execute_tool_once(
        submit_execution_success,
        {"summary": "done", "artifacts": []},
        make_tool_context(fixture, generator_id, role="verifier"),
        emit=_noop_emit,
    )
    failure = await execute_tool_once(
        submit_execution_failure,
        {"summary": "failed", "reason": "blocked", "details": []},
        make_tool_context(fixture, generator_id, role="verifier"),
        emit=_noop_emit,
    )

    assert success.is_error
    assert failure.is_error
    assert "executor agent profile" in success.output
    assert "executor agent profile" in failure.output


async def test_verifier_profile_required_for_verification_terminals(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture, agent_name="verifier")

    success = await execute_tool_once(
        submit_verification_success,
        {"summary": "verified", "checks": []},
        make_tool_context(fixture, generator_id, role="executor"),
        emit=_noop_emit,
    )
    failure = await execute_tool_once(
        submit_verification_failure,
        {"summary": "failed", "unresolved_issues": []},
        make_tool_context(fixture, generator_id, role="executor"),
        emit=_noop_emit,
    )

    assert success.is_error
    assert failure.is_error
    assert "verifier agent profile" in success.output
    assert "verifier agent profile" in failure.output


async def test_executor_profile_can_call_executor_terminals(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture)

    result = await execute_tool_once(
        submit_execution_success,
        {"summary": "done", "artifacts": []},
        make_tool_context(fixture, generator_id, role="executor"),
        emit=_noop_emit,
    )

    assert not result.is_error


async def test_verifier_profile_can_call_verifier_terminals(
    mission_store, episode_store, attempt_store, task_store, composer
) -> None:
    fixture = build_harness_fixture(
        mission_store=mission_store,
        episode_store=episode_store,
        attempt_store=attempt_store,
        task_store=task_store,
        composer=composer,
    )
    generator_id = apply_single_generator_plan(fixture, agent_name="verifier")

    result = await execute_tool_once(
        submit_verification_success,
        {"summary": "verified", "checks": []},
        make_tool_context(fixture, generator_id, role="verifier"),
        emit=_noop_emit,
    )

    assert not result.is_error
