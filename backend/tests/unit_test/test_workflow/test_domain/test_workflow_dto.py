"""Domain DTO tests for Workflow."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from workflow._core.state import (
    Workflow,
    WorkflowStatus,
)


def _request(**overrides) -> Workflow:
    base = dict(
        id="r1",
        request_id="req1",
        workflow_goal="goal",
        status=WorkflowStatus.OPEN,
        iteration_ids=(),
        parent_task_id="root-task",
        outcomes=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
    )
    base.update(overrides)
    return Workflow(**base)


def test_is_open_matches_status():
    assert _request(status=WorkflowStatus.OPEN).is_open is True
    assert _request(status=WorkflowStatus.SUCCEEDED).is_open is False
    assert _request(status=WorkflowStatus.FAILED).is_open is False
    assert _request(status=WorkflowStatus.CANCELLED).is_open is False


def test_request_dto_is_frozen():
    req = _request()
    with pytest.raises(FrozenInstanceError):
        req.status = WorkflowStatus.SUCCEEDED  # type: ignore[misc]
