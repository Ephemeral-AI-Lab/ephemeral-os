"""Subagent tools — spawn focused worker subagents."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field, field_validator

from agents import list_dispatchable_subagent_names
from tools._framework.core.base import BaseTool, ToolExecutionContextService, ToolResult
from tools._framework.core.hooks import validate_hook_targets
from tools.subagent.run_subagent import run_subagent


def _allowed_subagent_names() -> tuple[str, ...]:
    return tuple(list_dispatchable_subagent_names())


def _build_restricted_input_model(allowed_agent_names: tuple[str, ...]):
    allowed_list = list(allowed_agent_names)
    description = "Name of a dispatchable subagent target."
    if allowed_list:
        description += f" Allowed targets for this caller: {', '.join(allowed_list)}."
    else:
        description += " No dispatchable subagent targets are available for this caller."

    class RestrictedRunSubagentInput(run_subagent.input_model):  # type: ignore[misc, valid-type]
        _allowed_agent_names: ClassVar[tuple[str, ...]] = allowed_agent_names

        agent_name: str = Field(
            description=description,
            json_schema_extra={"enum": allowed_list},
        )

        @field_validator("agent_name")
        @classmethod
        def _validate_agent_name(cls, value: str) -> str:
            if not cls._allowed_agent_names:
                raise ValueError(
                    "No dispatchable subagent targets are available for this caller."
                )
            if value not in cls._allowed_agent_names:
                allowed = ", ".join(cls._allowed_agent_names)
                raise ValueError(
                    f"agent_name must be one of the dispatchable subagent targets: {allowed}"
                )
            return value

    RestrictedRunSubagentInput.__name__ = "RestrictedRunSubagentInput"
    return RestrictedRunSubagentInput


class RestrictedRunSubagentTool(BaseTool):
    """Caller-aware wrapper that narrows run_subagent's agent_name schema."""

    __doc__ = run_subagent.__doc__

    def __init__(self, *, allowed_agent_names: tuple[str, ...]) -> None:
        self._delegate = run_subagent
        # Copy every BaseTool contract attribute the framework reads so that
        # future hooks/context_requirements/is_terminal_tool changes to
        # `run_subagent` are not silently dropped on the restricted shim.
        for attr in (
            "name",
            "description",
            "short_description",
            "output_model",
            "background",
            "task_type",
            "is_terminal_tool",
            "pre_hooks",
            "post_hooks",
            "context_requirements",
        ):
            setattr(self, attr, getattr(run_subagent, attr))
        self.input_model = _build_restricted_input_model(allowed_agent_names)
        # Re-validate hook targets since the wrapping tool's name must match
        # the hook target_tool. (No-op today because run_subagent has no
        # hooks, but keeps the invariant explicit.)
        validate_hook_targets(
            tool_name=self.name,
            pre_hooks=tuple(self.pre_hooks or ()),
            post_hooks=tuple(self.post_hooks or ()),
        )

    async def execute(self, arguments, context: ToolExecutionContextService) -> ToolResult:  # type: ignore[override]
        return await self._delegate.execute(arguments, context)


def make_subagent_tools() -> list[BaseTool]:
    """Return caller-scoped subagent dispatch tools."""
    return [RestrictedRunSubagentTool(allowed_agent_names=_allowed_subagent_names())]


def make_subagent_tool_from_context(ctx: object) -> BaseTool:
    """Return the caller-scoped ``run_subagent`` tool for a factory context."""
    del ctx
    return make_subagent_tools()[0]


__all__ = [
    "RestrictedRunSubagentTool",
    "make_subagent_tool_from_context",
    "make_subagent_tools",
]
