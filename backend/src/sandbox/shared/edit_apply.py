"""Pure search/replace primitive shared by OCC and tool_primitives apply sites.

Single source of truth for ``replace_all``/occurrence-count semantics so the
two independent apply sites (OCC ``_apply_edit_content`` and tool_primitives
``edit_file``) cannot diverge. The helper takes already-decoded ``str`` and
raises on failure; each call site adapts the raise to its own surface
(``FileResult`` for OCC, propagated ``ValueError`` for tool_primitives).
"""

from __future__ import annotations


class SearchReplaceError(ValueError):
    """Raised when a search/replace edit cannot be applied as requested."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def apply_search_replace(
    text: str,
    old: str,
    new: str,
    *,
    replace_all: bool,
) -> str:
    """Apply one search/replace to already-decoded text. Pure; raises on failure.

    - ``old`` must be non-empty.
    - ``replace_all=True``: replace every occurrence; ``count == 0`` aborts with
      "anchor not found".
    - ``replace_all=False``: the anchor must occur exactly once; ``count == 0``
      aborts with "anchor not found", ``count > 1`` with
      "anchor occurrence count mismatch".
    """
    if not old:
        raise SearchReplaceError("edit anchor old_text must be non-empty")
    count = text.count(old)
    if replace_all:
        if count == 0:
            raise SearchReplaceError("anchor not found")
        return text.replace(old, new)
    if count != 1:
        raise SearchReplaceError(
            "anchor not found" if count == 0 else "anchor occurrence count mismatch"
        )
    return text.replace(old, new, 1)


__all__ = ["SearchReplaceError", "apply_search_replace"]
