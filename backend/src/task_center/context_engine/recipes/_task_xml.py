"""Shared ``<task>`` XML rendering for the recipe layer.

Single source of truth for turning an :class:`Outcome` into the
``<task id="<local_id>" status="<status>">…</task>`` element used in the
planner failed-attempt body, prior-iteration children, generator/reducer
``<needs>``, and the handoff roll-up. Handoff outcomes nest their child
``<task>`` elements (recursively) plus an optional ``<failure>`` child.

This module owns hostile-body sanitization (it embeds user-supplied text into
hand-assembled XML, so it needs :class:`ContextEngineError`); the
presentation-free data helpers live in ``task_center._core.outcomes``.
"""

from __future__ import annotations

from task_center._core.outcomes import (
    EMPTY_OUTCOME_PLACEHOLDERS,
    Outcome,
)
from task_center.context_engine.exceptions import ContextEngineError

# Placeholder for an empty ``<task>`` body — presence-defensive, never
# self-closing.
EMPTY_TASK_BODY = "(no outcome recorded)"

# Closers a hand-assembled body MUST refuse to leak from embedded user text.
STRUCTURAL_CLOSERS: tuple[str, ...] = (
    "</task>",
    "</failure>",
    "</needs>",
    "</assigned_task>",
    "</assigned_prompt>",
    "</attempt>",
    "</iteration>",
    "</goal>",
    "</iteration_goal>",
)


def sanitize_fragment(text: str, source_id: str) -> str:
    """Raise if user-supplied *text* contains a structural closer we emit."""
    for closer in STRUCTURAL_CLOSERS:
        if closer in text:
            raise ContextEngineError(
                f"Context body for {source_id!r} contains structural "
                f"closer {closer!r}. Rewrite the offending field to avoid this "
                "closer, or surface it under a different ContextBlockKind."
            )
    return text


def has_nested_body(outcome: Outcome) -> bool:
    """True when the outcome renders a nested body (handoff roll-up)."""
    return bool(outcome.children) or outcome.failure is not None


def render_task_children(outcome: Outcome, *, source_id: str = "task") -> str:
    """Render a handoff outcome's inner body: child ``<task>``s + ``<failure>``."""
    parts = [render_task_element(child, source_id=source_id) for child in outcome.children]
    if outcome.failure is not None:
        parts.append(f"<failure>\n{sanitize_fragment(outcome.failure, source_id)}\n</failure>")
    return "\n".join(parts)


def render_task_body(outcome: Outcome, *, source_id: str = "task") -> str:
    """Render just the body inside a ``<task>`` (no surrounding tag)."""
    if has_nested_body(outcome):
        return render_task_children(outcome, source_id=source_id)
    if outcome.outcome and outcome.outcome not in EMPTY_OUTCOME_PLACEHOLDERS:
        return sanitize_fragment(outcome.outcome, source_id)
    return EMPTY_TASK_BODY


def render_task_element(outcome: Outcome, *, source_id: str = "task") -> str:
    """Render a full ``<task id status>body</task>`` element (recursive)."""
    body = render_task_body(outcome, source_id=source_id)
    return f'<task id="{outcome.local_id}" status="{outcome.status}">\n{body}\n</task>'


def block_task_body(outcome: Outcome) -> tuple[str, bool]:
    """Body + ``pre_rendered_xml`` flag for a renderer-wrapped ``<task>`` block.

    A handoff outcome returns its sanitized nested body and ``True`` (the block
    must opt out of the renderer's structural-closer guard). A plain outcome
    returns its text (or ``""`` for a placeholder/empty value) and ``False`` —
    the renderer guard sanitizes the verbatim body itself.
    """
    if has_nested_body(outcome):
        return render_task_children(outcome, source_id=outcome.local_id), True
    if outcome.outcome and outcome.outcome not in EMPTY_OUTCOME_PLACEHOLDERS:
        return outcome.outcome, False
    return "", False


def task_attrs(outcome: Outcome) -> str:
    """The ``id="…" status="…"`` attribute fragment for a ``<task>``."""
    return f'id="{outcome.local_id}" status="{outcome.status}"'


__all__ = [
    "EMPTY_TASK_BODY",
    "STRUCTURAL_CLOSERS",
    "block_task_body",
    "render_task_element",
    "sanitize_fragment",
    "task_attrs",
]
