"""Load hooks from settings."""

from __future__ import annotations

from collections import defaultdict
from ephemeralos.hooks.events import HookEvent
from ephemeralos.hooks.schemas import HookDefinition


class HookRegistry:
    """Store hooks grouped by event."""

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookDefinition]] = defaultdict(list)

    def register(self, event: HookEvent, hook: HookDefinition) -> None:
        """Register one hook."""
        self._hooks[event].append(hook)

    def get(self, event: HookEvent) -> list[HookDefinition]:
        """Return hooks registered for an event."""
        return list(self._hooks.get(event, []))

    def summary(self) -> str:
        """Return a human-readable hook summary."""
        lines: list[str] = []
        for event in HookEvent:
            hooks = self.get(event)
            if not hooks:
                continue
            lines.append(f"{event.value}:")
            for hook in hooks:
                matcher = getattr(hook, "matcher", None)
                detail = getattr(hook, "command", None) or getattr(hook, "prompt", None) or getattr(hook, "url", None) or ""
                suffix = f" matcher={matcher}" if matcher else ""
                lines.append(f"  - {hook.type}{suffix}: {detail}")
        return "\n".join(lines)


def load_hook_registry(settings, plugins=None) -> HookRegistry:
    """Load hooks from the current settings object."""
    registry = HookRegistry()
    for raw_event, hooks in settings.hooks.items():
        try:
            event = HookEvent(raw_event)
        except ValueError:
            continue
        for hook in hooks:
            registry.register(event, hook)
    for plugin in plugins or []:
        if not plugin.enabled:
            continue
        for raw_event, hooks in plugin.hooks.items():
            try:
                event = HookEvent(raw_event)
            except ValueError:
                continue
            for hook in hooks:
                registry.register(event, hook)
    return registry
