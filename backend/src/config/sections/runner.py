"""Task-center runner config."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field

from config.base import ModuleConfigBase


class LiveE2EConfig(ModuleConfigBase):
    """Live/e2e runner gates."""

    heavy_enabled: bool = False
    capacity_enabled: bool = False
    real_agent_max_duration_s: float = Field(default=1800.0, gt=0)


class DaemonAuditPullConfig(ModuleConfigBase):
    """Daemon audit pull runtime toggle (V3 Phase 3 §Default-on rollout).

    ``enabled`` defaults to ``True`` once the 4 release gates pass on the
    heavy live-e2e fixture (see
    ``docs/daemon-audit-pull-consolidation-v3/phase-3-report-and-release-gates.md``).
    Operators can opt out via ``EOS__RUNNER__DAEMON_AUDIT_PULL__ENABLED=false``
    or the shorter ``EOS_DAEMON_AUDIT_PULL_ENABLED=false`` env binding
    consumed by :mod:`task_center_runner.audit.recorder`.
    """

    enabled: bool = True


class RunnerConfig(ModuleConfigBase):
    """TaskCenter runner defaults."""

    audit_dir: Path = Path(".sweevo_runs")
    run_label: str = "task_center_runner"
    live_e2e: LiveE2EConfig = Field(default_factory=LiveE2EConfig)
    sandbox_reuse_mode: Literal["fresh", "reuse", "force_fresh"] = "fresh"
    sandbox_quota: int = Field(default=5, ge=0)
    daemon_audit_pull: DaemonAuditPullConfig = Field(
        default_factory=DaemonAuditPullConfig
    )
