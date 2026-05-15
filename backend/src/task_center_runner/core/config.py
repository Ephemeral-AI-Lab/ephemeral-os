"""``RunConfig`` — the single input to the unified ``run_pipeline``.

Mock scenarios, real-agent freeform runs, and benchmark runs all reach
``run_pipeline`` through this dataclass. The 5 fields that differ across
modes — ``runner_factory``, ``lifecycle``, ``bootstrap``, ``sandbox``,
``run_label`` — are documented in the plan §4 mode-delta table.

``RunContext`` is the minimal handle passed to lifecycle hooks and the
sandbox provisioner. It is intentionally narrow (config + bundle + bus) so
that lifecycle implementations cannot reach into engine internals.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from task_center.trial.launch import AttemptAgentRunner
from task_center_runner.core.lifecycle import LifecycleHooks, NoopLifecycle
from task_center_runner.core.sandbox import SandboxProvisioner

if TYPE_CHECKING:
    from task_center.entry import TaskCenterSandboxBridge

    from task_center_runner.audit.bus import AuditEventBus
    from task_center_runner.core.stores import TaskCenterStoreBundle


@dataclass(frozen=True, slots=True)
class RunConfig:
    """Engine input. ``runner_factory`` returning ``None`` selects the real-LLM path."""

    entry_prompt: str
    repo_dir: str
    sandbox: SandboxProvisioner
    runner_factory: Callable[["RunContext"], AttemptAgentRunner | None]
    lifecycle: LifecycleHooks = field(default_factory=NoopLifecycle)
    bootstrap: Callable[[], None] | None = None
    stores: "TaskCenterStoreBundle | None" = None
    audit_dir: Path = Path(".sweevo_runs")
    run_label: str = "task_center_runner"
    run_dir_factory: Callable[[Path, "RunContext"], Path] | None = None
    bridge_factory: Callable[[], "TaskCenterSandboxBridge"] | None = None
    instance_id: str = ""
    max_duration_s: float | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RunContext:
    """Per-run handle passed to lifecycle hooks + the sandbox provisioner."""

    config: RunConfig
    bundle: "TaskCenterStoreBundle"
    bus: "AuditEventBus"


__all__ = ["RunConfig", "RunContext"]
