"""Hook + HookSet + MutableMockState — insertion-ordered hook firing.

Per plan §9. ``Hook`` declares which event/phase it cares about; ``HookSet``
fires hooks in registration order. ``MutableMockState`` is a small bag the
hooks (and scenarios) can read/write between firings.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from benchmarks.sweevo.live_test.audit.events import Event, EventType


@dataclass(frozen=True, slots=True)
class HookResult:
    """One firing of a hook."""

    name: str
    asserted: bool = False
    failed_reason: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class MutableMockState:
    """Cross-hook mutable state.

    Hooks can write to ``flags`` and read ``seen_events`` (every Event the bus
    publishes is appended to ``seen_events`` by the runtime — populated by the
    scenario runner when wired in S-07). Methods that mutate the next agent
    response are intentionally stubs in this phase.
    """

    __slots__ = ("seen_events", "flags")

    def __init__(self) -> None:
        self.seen_events: list[EventType] = []
        self.flags: dict[str, Any] = {}

    def inject_failure(self, *, role: str, attempt_id: str) -> None:
        raise NotImplementedError("inject_failure wired in next phase")

    def replace_next_planner_response(self, spec: Any) -> None:
        raise NotImplementedError("replace_next_planner_response wired in next phase")


@dataclass(frozen=True, slots=True)
class Hook:
    """A registered hook."""

    name: str
    event: EventType
    when: Literal["pre", "post"]
    fn: Callable[[Event, MutableMockState], HookResult]


class HookSet:
    """Insertion-ordered registry of Hooks."""

    def __init__(self) -> None:
        self._hooks: list[Hook] = []

    def register(self, hook: Hook) -> None:
        self._hooks.append(hook)

    def fire(
        self,
        event: Event,
        when: Literal["pre", "post"],
        state: MutableMockState,
    ) -> Iterable[HookResult]:
        results: list[HookResult] = []
        for hook in self._hooks:
            if hook.event != event.type or hook.when != when:
                continue
            try:
                result = hook.fn(event, state)
            except Exception as exc:  # noqa: BLE001
                result = HookResult(
                    name=hook.name,
                    asserted=False,
                    failed_reason=f"{type(exc).__name__}: {exc}",
                )
            results.append(result)
        return results

    def __len__(self) -> int:
        return len(self._hooks)


__all__ = ["Hook", "HookResult", "HookSet", "MutableMockState"]
