"""Phase 3 release-gate evaluators.

These helpers turn a stream of normalized daemon-audit events into a verdict
for each of the 4 release gates documented in
``docs/daemon-audit-pull-consolidation-v3/phase-3-report-and-release-gates.md``
§Gate matrix.

The evaluators are intentionally pure functions over the event payload so
they work against either:

- the puller's recorded JSONL events (puller on)
- a one-shot ``api.audit.pull`` snapshot of the daemon ring (puller off)

This matches the V3 §Safety-gate-vs-toggle resolution: the isolated-
workspace HARD BLOCK gate does NOT depend on the runtime puller toggle.
The release-gate harness is expected to call ``api.audit.pull`` directly
to obtain the event stream when the puller is disabled.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def evaluate_isolated_workspace_gate(
    events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate the isolated-workspace HARD BLOCK gate.

    Per §Gate matrix: passes iff every ``isolated_workspace.exited``
    reports zero orphan counts AND zero ``holder_pid_alive`` AND the
    final handle-id accounting reports zero open handles, no terminal event
    without a matching enter, and no lifecycle event missing a handle id.
    """
    exits: list[Mapping[str, Any]] = []
    holder_alive_after_exit = 0
    orphan_holder = 0
    orphan_cgroup = 0
    orphan_scratch = 0
    lifecycle_events: list[Mapping[str, Any]] = []
    for event_row in events:
        event_type = event_row.get("event_type")
        if event_type == "isolated_workspace.entered":
            lifecycle_events.append(event_row)
            continue
        if event_type == "isolated_workspace.evicted":
            lifecycle_events.append(event_row)
            continue
        if event_type == "isolated_workspace.exited":
            lifecycle_events.append(event_row)
            section = _promoted_payload_section(event_row, "isolated_workspace")
            exits.append(section)
            orphan_holder += int(section.get("orphan_holder_count") or 0)
            orphan_cgroup += int(section.get("orphan_cgroup_count") or 0)
            orphan_scratch += int(section.get("orphan_scratch_count") or 0)
            if bool(section.get("holder_pid_alive")):
                holder_alive_after_exit += 1
            continue
        if event_type == "isolated_workspace.orphan_check_completed":
            section = _promoted_payload_section(event_row, "isolated_workspace")
            orphan_holder += int(section.get("orphan_holder_count") or 0)
            orphan_cgroup += int(section.get("orphan_cgroup_count") or 0)
            orphan_scratch += int(section.get("orphan_scratch_count") or 0)
            continue
    handle_summary = summarize_isolated_workspace_handles(lifecycle_events)
    open_handle_count = int(handle_summary["open_handle_count"])
    pass_orphan = (
        orphan_holder == 0 and orphan_cgroup == 0 and orphan_scratch == 0
    )
    pass_holder_pid = holder_alive_after_exit == 0
    pass_handles = (
        open_handle_count == 0
        and int(handle_summary["terminal_without_enter_count"]) == 0
        and int(handle_summary["missing_handle_event_count"]) == 0
    )
    return {
        "passed": pass_orphan and pass_holder_pid and pass_handles,
        "exits_observed": handle_summary["closed"],
        "orphan_holder_count": orphan_holder,
        "orphan_cgroup_count": orphan_cgroup,
        "orphan_scratch_count": orphan_scratch,
        "holder_pid_alive_after_exit": holder_alive_after_exit,
        "open_handle_count": open_handle_count,
        "terminal_without_enter_count": handle_summary[
            "terminal_without_enter_count"
        ],
        "missing_handle_event_count": handle_summary[
            "missing_handle_event_count"
        ],
        "verdict": {
            "orphan_counts_zero": pass_orphan,
            "holder_pid_dead_after_exit": pass_holder_pid,
            "open_handles_zero": pass_handles,
            "handle_ids_consistent": pass_handles,
        },
    }


def summarize_isolated_workspace_handles(
    events: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    """Summarize isolated-workspace lifecycle state by handle id.

    TTL eviction emits both ``isolated_workspace.exited`` and
    ``isolated_workspace.evicted`` for the same handle. Counting events as
    ``entered - exited - evicted`` turns that clean lifecycle into ``-1``.
    Matching terminal events by ``workspace_handle_id`` keeps the count at zero
    and also catches mismatched IDs that scalar counts can hide.
    """
    entered_ids: set[str] = set()
    exited_ids: set[str] = set()
    evicted_ids: set[str] = set()
    entered_count = 0
    exited_count = 0
    evicted_count = 0
    missing_handle_event_count = 0
    for event_row in events:
        event_type = event_row.get("event_type")
        if event_type not in {
            "isolated_workspace.entered",
            "isolated_workspace.exited",
            "isolated_workspace.evicted",
        }:
            continue
        handle_id = _workspace_handle_id(event_row)
        if not handle_id:
            missing_handle_event_count += 1
            continue
        if event_type == "isolated_workspace.entered":
            entered_count += 1
            entered_ids.add(handle_id)
        elif event_type == "isolated_workspace.exited":
            exited_count += 1
            exited_ids.add(handle_id)
        else:
            evicted_count += 1
            evicted_ids.add(handle_id)
    terminal_ids = exited_ids | evicted_ids
    open_ids = entered_ids - terminal_ids
    terminal_without_enter = terminal_ids - entered_ids
    return {
        "opened": entered_count,
        "closed": exited_count,
        "evicted": evicted_count,
        "open_handle_count": len(open_ids),
        "terminal_without_enter_count": len(terminal_without_enter),
        "missing_handle_event_count": missing_handle_event_count,
    }


def evaluate_drop_free_pull_gate(
    puller_stats: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Per §Gate matrix: ``dropped_event_count == 0`` AND ``lost_before_seq == 0``."""
    stats = puller_stats or {}
    stats_present = bool(puller_stats)
    dropped = int(stats.get("dropped_event_count") or 0)
    lost = int(stats.get("lost_before_seq") or 0)
    return {
        "passed": stats_present and dropped == 0 and lost == 0,
        "stats_present": stats_present,
        "dropped_event_count": dropped,
        "lost_before_seq": lost,
    }


def evaluate_audit_overhead_gate(
    overhead_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Per §Gate matrix: 4 thresholds on a paired-bootstrap CI upper bound.

    Returns ``passed=False`` if any threshold is exceeded, if the input
    is empty (overhead measurements absent — operator must run the gate
    suite before claiming a pass), or if methodology metadata is missing
    (Phase 3 deferral D14: ``methodology_present`` is required, otherwise
    n_paired_runs==0 is ambiguous between "no measurement" and "0 paired
    runs").
    """
    if not overhead_metadata:
        return {
            "passed": False,
            "methodology_present": False,
            "reason": "no overhead measurement supplied",
        }
    # If methodology fields are entirely absent, treat it as a no-go too.
    methodology_present = bool(
        overhead_metadata.get("n_paired_runs")
        or overhead_metadata.get("n_calls")
        or overhead_metadata.get("bootstrap_resamples")
    )
    latency_ci_upper = float(
        overhead_metadata.get("p95_delta_ci_upper")
        or overhead_metadata.get("tool_latency_p95_delta_ms")
        or 0.0
    )
    daemon_rss_delta_mib = float(
        overhead_metadata.get("daemon_rss_delta_mib") or 0.0
    )
    runner_cpu_pct = float(overhead_metadata.get("runner_cpu_pct_p99") or 0.0)
    sandbox_disk_delta = int(
        overhead_metadata.get("sandbox_disk_delta_bytes") or 0
    )
    pass_latency = latency_ci_upper <= 5.0
    pass_daemon_rss = daemon_rss_delta_mib <= 16.0
    pass_runner_cpu = runner_cpu_pct <= 0.5
    pass_sandbox_disk = sandbox_disk_delta == 0
    return {
        "passed": all(
            (
                methodology_present,
                pass_latency,
                pass_daemon_rss,
                pass_runner_cpu,
                pass_sandbox_disk,
            )
        ),
        "methodology_present": methodology_present,
        "latency_p95_delta_ci_upper_ms": latency_ci_upper,
        "daemon_rss_delta_mib": daemon_rss_delta_mib,
        "runner_cpu_pct_p99": runner_cpu_pct,
        "sandbox_disk_delta_bytes": sandbox_disk_delta,
        "verdict": {
            "latency": pass_latency,
            "daemon_rss": pass_daemon_rss,
            "runner_cpu": pass_runner_cpu,
            "sandbox_disk": pass_sandbox_disk,
        },
    }


def evaluate_artifact_bound_gate(
    *,
    live_bytes: int,
    rotated_bytes: int,
    rotated_file_count: int,
    cap_per_rotated_bytes: int = 8 * 1024 * 1024,
    live_cap_bytes: int = 64 * 1024 * 1024,
    retention_files: int = 8,
) -> dict[str, Any]:
    """Per §Gate matrix: ``64 MiB + 8 × rotated`` host-side footprint cap."""
    cap_bytes = live_cap_bytes + retention_files * cap_per_rotated_bytes
    total_bytes = int(live_bytes) + int(rotated_bytes)
    return {
        "passed": (
            total_bytes <= cap_bytes
            and rotated_file_count <= retention_files
        ),
        "total_bytes": total_bytes,
        "cap_bytes": cap_bytes,
        "rotated_file_count": rotated_file_count,
        "retention_files": retention_files,
    }


def _promoted_payload_section(
    row: Mapping[str, Any], section_key: str
) -> Mapping[str, Any]:
    """Return the authoritative ``payload.<section>`` view for gate checks."""
    payload = row.get("payload")
    if not isinstance(payload, Mapping):
        return {}
    section = payload.get(section_key)
    return section if isinstance(section, Mapping) else {}


def _workspace_handle_id(row: Mapping[str, Any]) -> str:
    section = _promoted_payload_section(row, "isolated_workspace")
    return str(section.get("workspace_handle_id") or "")


__all__ = [
    "evaluate_artifact_bound_gate",
    "evaluate_audit_overhead_gate",
    "evaluate_drop_free_pull_gate",
    "evaluate_isolated_workspace_gate",
    "summarize_isolated_workspace_handles",
]
