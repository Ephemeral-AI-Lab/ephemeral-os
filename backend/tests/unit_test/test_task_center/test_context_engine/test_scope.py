"""US-004: ContextScope.assert_fields behavior."""

from __future__ import annotations

import pytest

from task_center.context_engine.core import RecipeScopeError
from task_center.context_engine.scope import ContextScope


def test_assert_fields_passes_when_all_present():
    scope = ContextScope(
        mission_id="r",
        episode_id="s",
        attempt_id="g",
        task_id="t",
    )
    scope.assert_fields(frozenset({"mission_id", "episode_id", "attempt_id"}))


def test_assert_fields_rejects_missing_episode():
    scope = ContextScope(mission_id="r")
    with pytest.raises(RecipeScopeError) as exc:
        scope.assert_fields(frozenset({"mission_id", "episode_id"}))
    assert "episode_id" in str(exc.value)


def test_assert_fields_lists_all_missing_fields_sorted():
    scope = ContextScope(mission_id="r")
    with pytest.raises(RecipeScopeError) as exc:
        scope.assert_fields(
            frozenset({"task_id", "episode_id", "attempt_id"})
        )
    # Sorted output for deterministic error messages.
    msg = str(exc.value)
    assert "attempt_id" in msg
    assert "episode_id" in msg
    assert "task_id" in msg
    # Check sorted ordering.
    assert msg.index("attempt_id") < msg.index("episode_id")
    assert msg.index("episode_id") < msg.index("task_id")


def test_helper_scope_fields_round_trip():
    scope = ContextScope(
        mission_id="r",
        task_id="helper-1",
        parent_packet_id="pkt-1",
        parent_task_id="parent-task",
    )
    scope.assert_fields(
        frozenset(
            {"mission_id", "task_id", "parent_packet_id", "parent_task_id"}
        )
    )
