"""Process-global registry for platform tool hooks."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Literal

from tools.core.hooks.outcomes import PostToolHook, PreToolHook

Phase = Literal["pre", "post"]


@dataclass(frozen=True)
class HookEntry:
    """One registered platform hook."""

    tool_glob: str
    phase: Phase
    priority: int
    target: PreToolHook | PostToolHook
    name: str

    @property
    def key(self) -> tuple[str, Phase, int, str]:
        """Idempotence key for registration."""
        return (self.tool_glob, self.phase, self.priority, self.name)


class ToolHookRegistry:
    """Mutable ordered hook collection."""

    def __init__(self) -> None:
        self._pre: list[HookEntry] = []
        self._post: list[HookEntry] = []

    def register(
        self,
        tool_glob: str,
        phase: Phase,
        priority: int,
        target: PreToolHook | PostToolHook,
        *,
        name: str | None = None,
    ) -> None:
        """Register a platform hook, replacing any previous entry with the same key."""
        resolved_name = name or getattr(target, "__name__", repr(target))
        entry = HookEntry(
            tool_glob=tool_glob,
            phase=phase,
            priority=priority,
            target=target,
            name=resolved_name,
        )
        bucket = self._pre if phase == "pre" else self._post
        bucket[:] = [existing for existing in bucket if existing.key != entry.key]
        bucket.append(entry)
        bucket.sort(key=lambda item: (item.priority, item.name))

    def matching(self, tool_name: str, phase: Phase) -> list[HookEntry]:
        """Return hooks matching ``tool_name`` for ``phase``."""
        bucket = self._pre if phase == "pre" else self._post
        return [entry for entry in bucket if fnmatch.fnmatchcase(tool_name, entry.tool_glob)]

    def clear(self) -> None:
        """Remove every registration. Tests use this to isolate global state."""
        self._pre.clear()
        self._post.clear()


_DEFAULT_REGISTRY = ToolHookRegistry()


def default_registry() -> ToolHookRegistry:
    """Return the process-global platform hook registry."""
    return _DEFAULT_REGISTRY
