"""Typed lifecycle events for TaskCenter.

This module defines a typed event vocabulary that subsumes the three
ad-hoc callback shapes currently in use:

- ``on_attempt_closed: Callable[[str], None]`` on
  :class:`EpisodeManager` — replaced by :class:`AttemptClosed`.
- ``ClosureReportSink = Callable[[EpisodeClosureReport], None]`` on
  :class:`EpisodeManager` — replaced by :class:`EpisodeClosed`.
- ``MissionClosureReportSink = Callable[[MissionClosureReport], None]``
  on :class:`MissionHandler` — replaced by :class:`MissionClosed`.

The :class:`EventBus` protocol is the publish surface. Each subscriber
filters by event type at the handler boundary. Future subscribers
(metrics, replay, persistence) attach without rewiring constructors.

This module declares the contract. Migration of the three sinks to a
single bus is intentionally staged: the callback typedefs remain in place
and accept Bus dispatch via thin adapters. Direct consumers can opt into
the event bus by subscribing instead of overriding callbacks.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


# Forward references; concrete report types live in their lifecycle modules.
@dataclass(frozen=True, slots=True)
class AttemptClosed:
    """Emitted when an :class:`AttemptOrchestrator` finalises one attempt.

    Subscribers should treat ``attempt_id`` as a foreign key; the closed
    Attempt row is committed before the event fires.
    """

    attempt_id: str


@dataclass(frozen=True, slots=True)
class EpisodeClosed:
    """Emitted when :class:`EpisodeManager` finalises one episode.

    Carries the :class:`EpisodeClosureReport` so subscribers can branch on
    the closure outcome without re-reading the episode row.
    """

    # Typed at use-time to avoid an eager mission/episode import cycle.
    report: object  # EpisodeClosureReport at runtime


@dataclass(frozen=True, slots=True)
class MissionClosed:
    """Emitted when :class:`MissionHandler.close_mission` runs.

    Carries the :class:`MissionClosureReport` so the close-report router
    (or any other subscriber) can drive parent-task resumption.
    """

    # Typed at use-time to avoid an eager mission/episode import cycle.
    report: object  # MissionClosureReport at runtime


# Discriminated union for handlers that want a single signature.
LifecycleEvent = AttemptClosed | EpisodeClosed | MissionClosed


class EventBus(Protocol):
    """Process-local pub-sub for :class:`LifecycleEvent` instances.

    Implementations should fan out synchronously and propagate handler
    exceptions back to the publisher unless explicitly documented to
    isolate (e.g. metrics handlers).
    """

    def publish(self, event: LifecycleEvent) -> None: ...

    def subscribe(
        self,
        handler: Callable[[LifecycleEvent], None],
    ) -> None: ...


class InMemoryEventBus:
    """Default :class:`EventBus` implementation."""

    def __init__(self) -> None:
        self._handlers: list[Callable[[LifecycleEvent], None]] = []

    def publish(self, event: LifecycleEvent) -> None:
        for handler in self._handlers:
            handler(event)

    def subscribe(
        self,
        handler: Callable[[LifecycleEvent], None],
    ) -> None:
        self._handlers.append(handler)


__all__ = [
    "AttemptClosed",
    "EpisodeClosed",
    "EventBus",
    "InMemoryEventBus",
    "LifecycleEvent",
    "MissionClosed",
]
