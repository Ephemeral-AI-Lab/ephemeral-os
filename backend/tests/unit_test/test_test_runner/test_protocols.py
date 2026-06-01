"""Protocols/dataclasses surface — round-trip instantiation.

These assertions exist so a future regression that removes a documented
field fails at import/use time.
"""

from __future__ import annotations

import asyncio
from dataclasses import fields
from pathlib import Path
from typing import Any

from task_center_runner.core import (
    AttachExisting,
    LifecycleHooks,
    NoopLifecycle,
    PipelineReport,
    RunConfig,
    RunContext,
    SandboxLease,
    SandboxProvisioner,
)


def test_sandbox_lease_carries_id_and_metadata() -> None:
    lease = SandboxLease(sandbox_id="sb-1", metadata={"image": "ubuntu"})
    assert lease.sandbox_id == "sb-1"
    assert lease.metadata == {"image": "ubuntu"}


def test_attach_existing_release_is_noop() -> None:
    """Per plan locked decision #3, AttachExisting overrides release() to no-op."""
    attach = AttachExisting("sb-existing")
    lease = asyncio.run(attach.provision(ctx=None))  # type: ignore[arg-type]
    assert lease.sandbox_id == "sb-existing"
    asyncio.run(attach.release(lease))  # must not raise


def test_noop_lifecycle_implements_all_four_hooks() -> None:
    lifecycle = NoopLifecycle()
    for name in ("before_run", "on_event", "after_run", "on_aborted"):
        assert hasattr(lifecycle, name), f"NoopLifecycle missing {name}"


def test_run_config_documented_fields_present() -> None:
    field_names = {f.name for f in fields(RunConfig)}
    expected = {
        "entry_prompt",
        "repo_dir",
        "sandbox",
        "runner_factory",
        "lifecycle",
        "bootstrap",
        "stores",
        "audit_dir",
        "run_label",
        "run_dir_factory",
        "sandbox_provisioner_factory",
        "instance_id",
        "max_duration_s",
        "extras",
    }
    missing = expected - field_names
    assert not missing, f"RunConfig missing documented fields: {missing}"


def test_run_config_round_trip_with_minimal_stubs() -> None:
    """Build a RunConfig with stub provisioner + runner_factory; verify defaults."""

    class _StubProvisioner:
        async def provision(self, ctx: Any) -> SandboxLease:
            return SandboxLease(sandbox_id="stub")

        async def release(self, lease: SandboxLease) -> None:
            return None

    config = RunConfig(
        entry_prompt="hello",
        repo_dir="/tmp/repo",
        sandbox=_StubProvisioner(),
        runner_factory=lambda ctx: None,
    )
    assert config.entry_prompt == "hello"
    assert config.repo_dir == "/tmp/repo"
    assert isinstance(config.lifecycle, NoopLifecycle)
    assert config.bootstrap is None
    assert config.stores is None
    assert config.run_label == "task_center_runner"
    assert config.audit_dir == Path(".sweevo_runs")
    assert config.run_dir_factory is None
    assert config.instance_id == ""
    assert config.max_duration_s is None
    assert config.extras == {}


def test_pipeline_report_documented_fields_present() -> None:
    field_names = {f.name for f in fields(PipelineReport)}
    expected = {
        "status",
        "request_id",
        "request_id",
        "sandbox_id",
        "instance_id",
        "run_dir",
        "request_status",
        "duration_s",
        "task_count",
        "tasks_completed",
        "tasks_failed",
        "metrics",
        "aborted_by_timeout",
        "lifecycle_extras",
        "performance_report_task",
    }
    missing = expected - field_names
    assert not missing, f"PipelineReport missing documented fields: {missing}"


def test_pipeline_report_has_no_runner_extras_field() -> None:
    """Plan §3: mock-only side-channels travel via MOCK_* audit events, not via the report."""
    field_names = {f.name for f in fields(PipelineReport)}
    assert "runner_extras" not in field_names


def test_run_context_carries_config_bundle_bus() -> None:
    field_names = {f.name for f in fields(RunContext)}
    assert field_names == {"config", "bundle", "bus"}


def test_protocol_types_importable_for_static_typing() -> None:
    """The Protocols import without runtime side effects."""
    assert LifecycleHooks is not None
    assert SandboxProvisioner is not None
