"""Unit tests for the planner recipe's failed-attempt (retry) emitters.

``recipes/attempts.py`` is gone; the failed-attempt body + metadata now live in
``recipes/planner.py`` as ``_failed_attempt_blocks`` / ``_render_failed_attempt_body``.
These exercise the emitters directly (the store-driven end-to-end path is in
``test_recipes_planner_closes_or_defers.py``):

* current-attempt exclusion + sequence ordering + HIGH priority,
* the attempt-no-only group metadata,
* body shapes for STARTUP_FAILED / TASK_FAILED (no store) / terminal tasks
  (generators + reducers) — there is no ``<plan_spec>`` / ``<evaluator_summary>``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from task_center._core.state import (
    Attempt,
    AttemptFailReason,
    AttemptStage,
    AttemptStatus,
    Iteration,
    IterationCreationReason,
    IterationStatus,
)
from task_center.context_engine.packet import ContextPriority
from task_center.context_engine.recipes.planner import (
    _failed_attempt_blocks,
    _render_failed_attempt_body,
)


class _FakeTaskStore:
    def __init__(self, rows: dict[str, dict]) -> None:
        self._rows = rows

    def get_task(self, task_id: str):
        return self._rows.get(task_id)


def _iteration(sequence_no: int = 1) -> Iteration:
    now = datetime.now(UTC)
    return Iteration(
        id="seg-1",
        workflow_id="g-1",
        sequence_no=sequence_no,
        creation_reason=IterationCreationReason.INITIAL,
        iteration_goal="iteration goal",
        attempt_budget=2,
        status=IterationStatus.OPEN,
        attempt_ids=(),
        deferred_goal_for_next_iteration=None,
        created_at=now,
        updated_at=now,
        closed_at=None,
    )


def _attempt(
    sequence_no: int,
    *,
    attempt_id: str | None = None,
    status: AttemptStatus = AttemptStatus.FAILED,
    generator_task_ids: tuple[str, ...] = (),
    reducer_task_ids: tuple[str, ...] = (),
    fail_reason: AttemptFailReason | None = None,
) -> Attempt:
    now = datetime.now(UTC)
    return Attempt(
        id=attempt_id or f"attempt-{sequence_no}",
        iteration_id="seg-1",
        attempt_sequence_no=sequence_no,
        stage=AttemptStage.CLOSED,
        status=status,
        planner_task_id=None,
        generator_task_ids=generator_task_ids,
        reducer_task_ids=reducer_task_ids,
        deferred_goal_for_next_iteration=None,
        fail_reason=fail_reason,
        created_at=now,
        updated_at=now,
        closed_at=now,
    )


def _blocks(attempts, *, current_attempt_id=None, task_store=None):
    return _failed_attempt_blocks(
        current_attempt_id=current_attempt_id,
        iteration=_iteration(),
        attempts=attempts,
        task_store=task_store,
    )


# ---------------------------------------------------------------------------
# Selection + ordering + metadata.
# ---------------------------------------------------------------------------


def test_excludes_current_attempt_and_non_failed():
    current = _attempt(3, attempt_id="current", fail_reason=AttemptFailReason.TASK_FAILED)
    blocks = _blocks(
        [
            current,
            _attempt(2, fail_reason=AttemptFailReason.TASK_FAILED),
            _attempt(4, status=AttemptStatus.RUNNING),
            _attempt(1, fail_reason=AttemptFailReason.STARTUP_FAILED),
        ],
        current_attempt_id=current.id,
    )
    assert [block.source_id for block in blocks] == ["attempt-1", "attempt-2"]
    assert all(block.priority == ContextPriority.HIGH for block in blocks)


def test_prior_attempt_block_metadata_carries_attempt_no_only():
    blocks = _failed_attempt_blocks(
        current_attempt_id=None,
        iteration=_iteration(sequence_no=3),
        attempts=[_attempt(1, fail_reason=AttemptFailReason.STARTUP_FAILED)],
        task_store=None,
    )
    block = blocks[0]
    assert block.metadata["group_id"] == "iteration_3_current"
    assert block.metadata["group_tag"] == "iteration"
    assert block.metadata["group_attrs"] == 'iteration_no="3" position="current"'
    assert block.metadata["child_tag"] == "attempt"
    assert block.metadata["attrs"] == 'attempt_no="1"'
    assert block.metadata["pre_rendered_xml"] == "true"


def test_all_failed_attempts_render_in_sequence_order():
    attempts = [
        _attempt(3, fail_reason=AttemptFailReason.TASK_FAILED),
        _attempt(1, fail_reason=AttemptFailReason.TASK_FAILED),
        _attempt(2, fail_reason=AttemptFailReason.TASK_FAILED),
    ]
    blocks = _blocks(attempts)
    assert [b.source_id for b in blocks] == ["attempt-1", "attempt-2", "attempt-3"]
    assert [b.metadata["attrs"] for b in blocks] == [
        'attempt_no="1"',
        'attempt_no="2"',
        'attempt_no="3"',
    ]


# ---------------------------------------------------------------------------
# Body shapes — failure-only and terminal-task bodies.
# ---------------------------------------------------------------------------


def test_startup_failed_renders_failure_only_body():
    body = _render_failed_attempt_body(
        _attempt(1, fail_reason=AttemptFailReason.STARTUP_FAILED), task_store=None
    )
    assert body == "<failure>\nagent_launch_failed\n</failure>"


def test_task_failed_without_store_renders_no_detail():
    body = _render_failed_attempt_body(
        _attempt(1, fail_reason=AttemptFailReason.TASK_FAILED), task_store=None
    )
    assert body == "<failure>\n(no detail recorded)\n</failure>"


def test_body_emits_terminal_task_children_then_failure_line():
    store = _FakeTaskStore(
        {
            "att-1:gen:t-a": {"status": "done", "outcomes": [{"outcome": "built catalog slice"}]},
            "att-1:gen:t-b": {"status": "failed", "outcomes": [{"outcome": "boom"}]},
            # Un-started (no row) task is excluded — terminal-only.
            "att-1:gen:t-missing": None,
        }
    )
    body = _render_failed_attempt_body(
        _attempt(
            1,
            attempt_id="att-1",
            generator_task_ids=("att-1:gen:t-a", "att-1:gen:t-b", "att-1:gen:t-missing"),
            fail_reason=AttemptFailReason.TASK_FAILED,
        ),
        task_store=store,
    )
    assert "<plan_spec>" not in body
    assert "<evaluator_summary>" not in body
    assert 'id="t-missing"' not in body
    assert body == (
        '<task id="t-a" status="success">\n'
        "built catalog slice\n"
        "</task>\n"
        '<task id="t-b" status="failure">\n'
        "boom\n"
        "</task>\n"
        "<failure>\n"
        "generator t-b: boom\n"
        "</failure>"
    )


def test_body_renders_failed_reducer_task():
    """A failed reducer surfaces as a terminal ``<task>`` child + a
    ``reducer <local_id>:`` failure line."""
    store = _FakeTaskStore(
        {
            "att-1:gen:t-a": {"status": "done", "outcomes": [{"outcome": "ok"}]},
            "att-1:red:r-a": {"status": "failed", "outcomes": [{"outcome": "gate rejected"}]},
        }
    )
    body = _render_failed_attempt_body(
        _attempt(
            1,
            attempt_id="att-1",
            generator_task_ids=("att-1:gen:t-a",),
            reducer_task_ids=("att-1:red:r-a",),
            fail_reason=AttemptFailReason.TASK_FAILED,
        ),
        task_store=store,
    )
    assert body == (
        '<task id="t-a" status="success">\n'
        "ok\n"
        "</task>\n"
        '<task id="r-a" status="failure">\n'
        "gate rejected\n"
        "</task>\n"
        "<failure>\n"
        "reducer r-a: gate rejected\n"
        "</failure>"
    )
