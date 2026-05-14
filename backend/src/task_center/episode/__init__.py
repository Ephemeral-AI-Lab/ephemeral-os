"""Episode package facade.

Episode DTOs/enums live in :mod:`task_center.episode.state`; lifecycle
coordination lives in :mod:`task_center.episode.manager`.
"""

from __future__ import annotations

from task_center.episode.manager import (
    AttemptClosedCallback,
    ClosureReportSink,
    EpisodeManager,
    EpisodeManagerRegistry,
    OrchestratorFactory,
)
from task_center.episode.state import (
    AttemptedPlanEntry,
    AttemptPlanFailed,
    ClosureOutcome,
    Episode,
    EpisodeClosureReport,
    EpisodeCreationReason,
    EpisodeStatus,
    SuccessContinue,
    TerminalSuccess,
)

__all__ = [
    "AttemptPlanFailed",
    "AttemptedPlanEntry",
    "ClosureOutcome",
    "Episode",
    "EpisodeClosureReport",
    "EpisodeCreationReason",
    "EpisodeStatus",
    "SuccessContinue",
    "TerminalSuccess",
    "AttemptClosedCallback",
    "ClosureReportSink",
    "EpisodeManager",
    "EpisodeManagerRegistry",
    "OrchestratorFactory",
]
