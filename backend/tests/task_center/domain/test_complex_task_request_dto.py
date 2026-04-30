"""Domain DTO tests for ComplexTaskRequest."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from task_center.domain.complex_task_request import (
    ComplexTaskCloseReport,
    ComplexTaskRequest,
    ComplexTaskRequestStatus,
)


def _request(**overrides) -> ComplexTaskRequest:
    base = dict(
        id="r1",
        task_center_run_id="run1",
        requested_by_task_id="t1",
        goal="goal",
        status=ComplexTaskRequestStatus.OPEN,
        task_segment_ids=(),
        final_outcome=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=None,
    )
    base.update(overrides)
    return ComplexTaskRequest(**base)


def test_with_appended_segment_returns_new_dto_unchanged_original():
    original = _request(task_segment_ids=("s1",))
    updated = original.with_appended_segment("s2")
    assert original.task_segment_ids == ("s1",)
    assert updated.task_segment_ids == ("s1", "s2")
    assert isinstance(updated.task_segment_ids, tuple)


def test_latest_segment_id_returns_last():
    assert _request(task_segment_ids=()).latest_segment_id is None
    assert (
        _request(task_segment_ids=("a", "b", "c")).latest_segment_id == "c"
    )


def test_is_open_matches_status():
    assert _request(status=ComplexTaskRequestStatus.OPEN).is_open is True
    assert _request(status=ComplexTaskRequestStatus.SUCCEEDED).is_open is False
    assert _request(status=ComplexTaskRequestStatus.FAILED).is_open is False
    assert _request(status=ComplexTaskRequestStatus.CANCELLED).is_open is False


def test_request_dto_is_frozen():
    req = _request()
    with pytest.raises(FrozenInstanceError):
        req.status = ComplexTaskRequestStatus.SUCCEEDED  # type: ignore[misc]


def test_close_report_constructs():
    rep = ComplexTaskCloseReport(
        complex_task_request_id="r1",
        requested_by_task_id="t1",
        outcome="success",
        final_segment_id="s1",
        final_harness_graph_id="g1",
    )
    assert rep.outcome == "success"
