"""Adapt overlay NDJSON gitinclude changes into OCC ``OperationChange`` values.

See ``docs/architecture/overlay-sandbox-plan.md`` §4.1 and Slice 5a of the
sandbox-api-runtime-refactor. Overlay never invokes OCC: the auditor
produces ``OperationChange`` values via :meth:`to_operation_changes`
and hands them off to its caller through ``OverlayRunOutcome``. The
caller drives :meth:`WriteCoordinator.commit_operation_against_base`.
"""

from __future__ import annotations

from collections.abc import Sequence

from sandbox.code_intelligence.core.hashing import content_hash
from sandbox.code_intelligence.core.types import OperationChange
from sandbox.code_intelligence.overlay.types import OverlayChange


class OverlayCommandCommitter:
    """Translate one overlay op's gitinclude changes into OCC inputs.

    Strict-base only: ``base_content`` always comes from the command-start
    overlay lowerdir (plan §4.1 invariant). The class is now purely a
    data-shape translator — the actual OCC commit happens in the
    auditor's caller (Slice 5a's correctness boundary).
    """

    def __init__(self, *, workspace_root: str) -> None:
        self._workspace_root = workspace_root.rstrip("/")

    def to_operation_changes(
        self, changes: Sequence[OverlayChange]
    ) -> list[OperationChange]:
        """Convert NDJSON-parsed ``OverlayChange`` into strict-base OCC values."""
        op_changes: list[OperationChange] = []
        for change in changes:
            op_changes.append(
                OperationChange(
                    file_path=self._live_path(change.path),
                    base_content=change.base_content,
                    base_hash=content_hash(change.base_content) if change.base_existed else "",
                    final_content=change.final_content,
                    base_existed=change.base_existed,
                    strict_base=True,
                )
            )
        return op_changes

    def _live_path(self, rel: str) -> str:
        rel = rel.replace("\\", "/").lstrip("/")
        return f"{self._workspace_root}/{rel}"


__all__ = ["OverlayCommandCommitter"]
