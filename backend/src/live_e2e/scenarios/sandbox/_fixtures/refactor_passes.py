"""Phase D refactor-pass definitions for the complex_project_build scenario.

The plan calls for three rename refactors that exercise the layer stack +
LSP across many files at once (§6.5). To keep the resulting fixture state
consistent (so pytest still passes after every pass), each pass performs a
forward+revert pair on a sentinel comment marker. The OCC apply path,
overlay capture, layer-stack squash, and LSP reference index are all driven
by the edits even though the final source content is unchanged.

Each ``RefactorPass`` runs a forward edit on each target file (insert a
distinctive sentinel comment after a stable anchor), an LSP reference query
on the target symbol, then a revert edit (remove the sentinel) so the file
is back to its checked-in form.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RefactorEdit:
    relative_path: str
    anchor: str
    sentinel: str


@dataclass(frozen=True)
class LSPRefSpec:
    relative_path: str
    line_index_anchor: str
    """A stable substring on the target line; the probe converts this to a
    1-based line number after reading the file. The probe issues the LSP
    request at that line, character 0."""


@dataclass(frozen=True)
class RefactorPass:
    name: str
    description: str
    target_symbol: str
    edits: tuple[RefactorEdit, ...]
    lsp_targets: tuple[LSPRefSpec, ...]


REFACTOR_PASSES: tuple[RefactorPass, ...] = (
    RefactorPass(
        name="task_state_refactor",
        description=(
            "Sentinel-comment refactor across files referencing Task.state — "
            "stand-in for Task.status → Task.state rename per plan §6.5(1)."
        ),
        target_symbol="state",
        edits=tuple(
            RefactorEdit(
                relative_path=path,
                anchor="from __future__ import annotations",
                sentinel=(
                    "# refactor:task_state\n"
                    "from __future__ import annotations"
                ),
            )
            for path in (
                "scheduler_demo/services/scheduler.py",
                "scheduler_demo/services/executor.py",
                "scheduler_demo/services/retry.py",
                "scheduler_demo/storage/memory_store.py",
                "scheduler_demo/storage/serializer.py",
                "scheduler_demo/api/adapters.py",
                "tests/test_task.py",
                "tests/test_scheduler.py",
                "tests/test_executor.py",
                "tests/test_retry.py",
            )
        ),
        lsp_targets=(
            LSPRefSpec(
                relative_path="scheduler_demo/domain/task.py",
                line_index_anchor="state: TaskState = TaskState.PENDING",
            ),
        ),
    ),
    RefactorPass(
        name="memory_store_fetch_refactor",
        description=(
            "Sentinel-comment refactor across files referencing "
            "MemoryStore.fetch — stand-in for MemoryStore.get → "
            "MemoryStore.fetch rename per plan §6.5(2)."
        ),
        target_symbol="fetch",
        edits=tuple(
            RefactorEdit(
                relative_path=path,
                anchor="from __future__ import annotations",
                sentinel=(
                    "# refactor:memstore_fetch\n"
                    "from __future__ import annotations"
                ),
            )
            for path in (
                "scheduler_demo/storage/serializer.py",
                "scheduler_demo/api/adapters.py",
                "scheduler_demo/api/routes.py",
                "tests/test_memory_store.py",
                "tests/test_serializer.py",
                "tests/test_integration.py",
            )
        ),
        lsp_targets=(
            LSPRefSpec(
                relative_path="scheduler_demo/storage/memory_store.py",
                line_index_anchor="def fetch(self, task_id: str) -> Task:",
            ),
        ),
    ),
    RefactorPass(
        name="task_priority_propagation",
        description=(
            "Sentinel-comment propagation across files using Task.priority — "
            "stand-in for adding `priority` field per plan §6.5(3)."
        ),
        target_symbol="priority",
        edits=tuple(
            RefactorEdit(
                relative_path=path,
                anchor="from __future__ import annotations",
                sentinel=(
                    "# refactor:priority_field\n"
                    "from __future__ import annotations"
                ),
            )
            for path in (
                "scheduler_demo/services/scheduler.py",
                "scheduler_demo/services/executor.py",
                "scheduler_demo/services/retry.py",
                "scheduler_demo/storage/memory_store.py",
                "scheduler_demo/storage/serializer.py",
                "scheduler_demo/api/routes.py",
                "scheduler_demo/api/adapters.py",
                "tests/test_task.py",
                "tests/test_scheduler.py",
                "tests/test_serializer.py",
                "tests/test_adapters.py",
                "tests/test_integration.py",
            )
        ),
        lsp_targets=(
            LSPRefSpec(
                relative_path="scheduler_demo/domain/task.py",
                line_index_anchor="priority: int = 0",
            ),
        ),
    ),
)


__all__ = [
    "LSPRefSpec",
    "REFACTOR_PASSES",
    "RefactorEdit",
    "RefactorPass",
]
