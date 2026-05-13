from __future__ import annotations

from live_e2e.squad.complex_project_build_shell_edit_lsp_probe import (
    _hover_expectations,
    _symbol_cursor_offset,
)
from live_e2e.scenarios.sandbox._fixtures.lsp_expectations import LspExpectation


def test_symbol_cursor_offset_targets_symbol_interior() -> None:
    assert _symbol_cursor_offset("class Schedule:", "class Schedule:", "Schedule") == 10


def test_symbol_cursor_offset_falls_back_to_anchor_start() -> None:
    assert _symbol_cursor_offset("    @dataclass", "@dataclass", "Schedule") == 4


def test_hover_expectations_prefer_use_site_anchors() -> None:
    definition_anchor = LspExpectation(
        symbol="Schedule",
        source_path="scheduler_demo/domain/schedule.py",
        source_anchor="class Schedule:",
        definition_path="scheduler_demo/domain/schedule.py",
        definition_anchor="class Schedule:",
        min_references=2,
        hover_contains=("Schedule",),
    )
    use_anchor = LspExpectation(
        symbol="Task",
        source_path="tests/test_task.py",
        source_anchor='task = Task(task_id="t")',
        definition_path="scheduler_demo/domain/task.py",
        definition_anchor="class Task:",
        min_references=2,
        hover_contains=("Task",),
    )

    assert _hover_expectations((definition_anchor, use_anchor)) == (use_anchor,)


def test_hover_expectations_return_empty_for_definition_only_anchors() -> None:
    definition_anchor = LspExpectation(
        symbol="Schedule",
        source_path="scheduler_demo/domain/schedule.py",
        source_anchor="class Schedule:",
        definition_path="scheduler_demo/domain/schedule.py",
        definition_anchor="class Schedule:",
        min_references=2,
        hover_contains=("Schedule",),
    )

    assert _hover_expectations((definition_anchor,)) == ()
