"""Outcome types and callable protocols for platform tool hooks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from message.stream_events import StreamEvent
    from tools.core.base import ToolExecutionContext, ToolResult


EmitStreamEvent = Callable[["StreamEvent"], Awaitable[None]]


@dataclass(frozen=True)
class PreHookOutcome:
    """One pre-hook result.

    ``tool_input`` replaces the current parsed arguments for later pre-hooks
    and the eventual tool body. Advisories are user-only stream events. Error
    outcomes stop the chain and cannot also mutate or advise.
    """

    tool_input: BaseModel | None = None
    has_error: bool = False
    error_message: str | None = None
    advisories: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.has_error and not self.error_message:
            raise ValueError("error_message is required when has_error=True")
        if self.has_error and self.tool_input is not None:
            raise ValueError("error outcomes cannot also mutate tool_input")
        if self.has_error and self.advisories:
            raise ValueError("error outcomes cannot also emit advisories")


@dataclass(frozen=True)
class PostHookOutcome:
    """One post-hook result.

    Post-hooks are advisory or blocking; they do not mutate tool arguments.
    Error outcomes stop the post-hook chain and replace the API-facing result.
    """

    has_error: bool = False
    error_message: str | None = None
    advisories: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.has_error and not self.error_message:
            raise ValueError("error_message is required when has_error=True")
        if self.has_error and self.advisories:
            raise ValueError("error outcomes cannot also emit advisories")


@dataclass(frozen=True)
class PreHookPipelineResult:
    """Terminal result of a pre-hook chain."""

    tool_input: BaseModel
    has_error: bool = False
    error_message: str | None = None

    def __post_init__(self) -> None:
        if self.has_error and not self.error_message:
            raise ValueError("error_message is required when has_error=True")


class PreToolHook(Protocol):
    """Pre-phase platform hook callable."""

    def __call__(
        self,
        tool_name: str,
        args: BaseModel,
        context: "ToolExecutionContext",
    ) -> PreHookOutcome | Awaitable[PreHookOutcome]: ...


class PostToolHook(Protocol):
    """Post-phase platform hook callable."""

    def __call__(
        self,
        tool_name: str,
        args: BaseModel,
        context: "ToolExecutionContext",
        result: "ToolResult",
    ) -> PostHookOutcome | Awaitable[PostHookOutcome]: ...
