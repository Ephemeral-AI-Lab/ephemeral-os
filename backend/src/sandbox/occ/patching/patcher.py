"""Patcher — exact search/replace edit engine."""

from __future__ import annotations

from dataclasses import dataclass

from sandbox.occ.state.constants import PATCHER_MAX_DIFF_SIZE

_MAX_EDITS_PER_BATCH = 100


@dataclass(frozen=True)
class SearchReplaceEdit:
    """Find-and-replace edit."""

    old_text: str
    new_text: str


@dataclass(frozen=True)
class PatchResult:
    """Result of applying edits to content."""

    content: str
    success: bool
    edits_applied: int
    errors: list[str]
    warnings: list[str]


class Patcher:
    """Apply edits to file content with validation.

    Parameters
    ----------
    max_edits_per_batch:
        Maximum number of edits in a single batch.
    max_diff_size:
        Maximum total diff size in characters.
    """

    def __init__(
        self,
        max_edits_per_batch: int = _MAX_EDITS_PER_BATCH,
        max_diff_size: int = PATCHER_MAX_DIFF_SIZE,
    ) -> None:
        self._max_edits_per_batch = max_edits_per_batch
        self._max_diff_size = max_diff_size

    def apply_many(
        self,
        content: str,
        edits: list[SearchReplaceEdit],
    ) -> PatchResult:
        """Apply a batch of exact search/replace edits to content."""
        if len(edits) > self._max_edits_per_batch:
            return PatchResult(
                content=content,
                success=False,
                edits_applied=0,
                errors=[f"Too many edits ({len(edits)} > {self._max_edits_per_batch})"],
                warnings=[],
            )

        result = content
        applied = 0
        errors: list[str] = []
        warnings: list[str] = []

        for i, edit in enumerate(edits):
            new_result = self._apply_search_replace(result, edit)
            if new_result is None:
                errors.append(f"Edit {i + 1}: search text not found")
            else:
                result = new_result
                applied += 1

        if len(result) - len(content) > self._max_diff_size:
            warnings.append("Edit produced very large diff")

        return PatchResult(
            content=result,
            success=applied > 0 and not errors,
            edits_applied=applied,
            errors=errors,
            warnings=warnings,
        )

    def _apply_search_replace(
        self, content: str, edit: SearchReplaceEdit,
    ) -> str | None:
        """Replace first occurrence of old_text with new_text."""
        if edit.old_text not in content:
            return None
        return content.replace(edit.old_text, edit.new_text, 1)
