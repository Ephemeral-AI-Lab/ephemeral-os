"""Pure XML rendering for :class:`AgentContext`."""

from __future__ import annotations

from html import escape

from task_center._core.outcomes import ExecutionTaskOutcome
from task_center.context_engine.context import AgentContext, ContextSection


def render_context_xml(context: AgentContext) -> str:
    root = ContextSection(
        tag="context",
        attrs={"role": context.role},
        children=context.sections,
    )
    return render_section(root) + "\n"


def render_task_outcome(outcome: ExecutionTaskOutcome) -> ContextSection:
    return ContextSection(
        tag="task",
        attrs={
            "task_id": outcome.task_id,
            "role": outcome.role,
            "status": outcome.status,
        },
        text=outcome.outcome,
    )


def render_section(section: ContextSection) -> str:
    attrs = _render_attrs(section.attrs)
    open_tag = f"<{section.tag}{attrs}>"
    body_parts: list[str] = []
    if section.text is not None:
        body_parts.append(escape(section.text))
    body_parts.extend(render_section(child) for child in section.children)
    body = "\n".join(body_parts)
    return f"{open_tag}\n{body}\n</{section.tag}>"


def _render_attrs(attrs: object) -> str:
    if not isinstance(attrs, dict):
        attrs = dict(attrs)  # type: ignore[arg-type]
    if not attrs:
        return ""
    return "".join(
        f' {escape(str(key), quote=True)}="{escape(str(value), quote=True)}"'
        for key, value in attrs.items()
    )


__all__ = ["render_context_xml", "render_section", "render_task_outcome"]
