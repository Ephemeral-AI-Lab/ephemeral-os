---
title: "Live E2E Capacity Suite - Harness And Action Plan"
tags: ["live-e2e", "capacity", "harness", "tool-scripts", "audit", "fixtures"]
created: 2026-05-12T00:00:00.000Z
updated: 2026-05-12T00:00:00.000Z
sources: ["live-e2e-capacity-suite-plan.md", "live-e2e-capacity-suite-scenario-packs.md"]
links: ["live-e2e-capacity-suite-plan.md", "live-e2e-capacity-suite-scenario-packs.md", "live-e2e-capacity-suite-verification.md"]
category: decision
confidence: high
schemaVersion: 1
---

# Live E2E Capacity Suite - Harness And Action Plan

## Design Principle

Keep scenario classes declarative. Put operational complexity in prepared tool scripts and action modules. Scenario classes should describe graph shape and expected events; action modules should drive real tools and collect evidence.

## Action Module Layout

```text
backend/src/live_e2e/squad/capacity_actions/
  __init__.py
  graph.py            # dynamic DAG, recursive mission, retry/recovery actions
  workspace.py        # multi-file write/edit/shell/API operations
  lsp.py              # semantic LSP suites and diagnostics refresh helpers
  guardrails.py       # invalid tool batches, hook failure probes
  context.py          # prompt/packet capture assertions
  audit.py            # run-dir inspection helpers
  metrics.py          # summary/perf artifact builders
```

`MockSquadRunner` should dispatch a small stable action vocabulary to these modules instead of growing long inline branches in `runner.py`.

## Stable Action Vocabulary

| Action | Owner module | Purpose |
|---|---|---|
| `capacity_graph:<shape>` | `graph.py` | run dynamic DAG branches, fan-in, retry, recursive mission |
| `capacity_workspace:<profile>` | `workspace.py` | generate and mutate multi-file project fixtures |
| `capacity_lsp:<profile>` | `lsp.py` | run semantic LSP checks after public mutations |
| `capacity_guardrail:<case>` | `guardrails.py` | provoke one typed guardrail failure and optional recovery |
| `capacity_context:<case>` | `context.py` | assert captured packet/rendered prompt shape |
| `capacity_audit:<case>` | `audit.py` | inspect run-dir artifacts inside the scenario |
| `capacity_metrics:<case>` | `metrics.py` | write scenario-local summary artifacts |

The action string is still visible in `message.jsonl` through the tool sequence it drives. Every complex action should write a small summary artifact under `/ephemeral-os/.metrics/` or the scenario workspace, then read it back through a public tool.

## Prepared Tool Script Contract

Every prepared script returns:

```python
@dataclass(frozen=True)
class CapacityActionResult:
    name: str
    summary: str
    artifact_path: str | None
    expected_errors: tuple[str, ...]
    counters: Mapping[str, int | float | str]
```

The runner converts this into `submit_execution_success` or into a typed failure path. This keeps the scenario class free of filesystem detail while preserving tool-script evidence.

## Fixture Strategy

Use three fixture profiles:

| Profile | File count | Use |
|---|---:|---|
| `mini` | 8-15 | focused live scenarios and smoke tests |
| `project` | 40-80 | normal capacity scenarios |
| `soak` | 100-200 | nightly workspace churn and perf checks |

Fixture files should include:

- Python package modules with cross-file imports.
- Tests that exercise the package.
- Config files and docs.
- Symlinks where supported.
- Files intentionally used as conflict probes.
- Files intentionally used as LSP diagnostic probes.

Generated fixture content should be deterministic and stdlib-only. It must avoid hidden root directories because Pyright auto-excludes dotpaths.

## Generic Adapter

Most focused scenarios do not need a SWE-EVO benchmark instance. Add a generic adapter after the current SWE-EVO path is stable:

```text
backend/src/live_e2e/generic_adapter.py
backend/src/live_e2e/tests/conftest.py
```

Responsibilities:

- provision a minimal Daytona sandbox.
- set a writable workspace root, for example `/ephemeral-os` or `/workspace`.
- build workspace base before first mutation.
- expose a per-test reset boundary equivalent to the SWE-EVO `workspace` fixture.
- pass a simple entry prompt into `run_scenario`.

Until then, new capacity scenarios may use `live_e2e.sweevo_adapter` as long as tests reset through the shared `workspace` fixture and do not mutate through `sweevo_sandbox` directly.

## Prompt And Context Capture

Capacity scenarios need a first-class prompt/packet capture seam:

- Capture every `LaunchBundle` before runner dispatch.
- Persist packet metadata into the run dir.
- Assert block kind, priority, heading, inherited metadata, and rendered text.
- Keep prompt assertions structural; avoid brittle full-string equality except for entry prompt source-of-truth tests.

Suggested artifact:

```text
.sweevo_runs/scenario_logs/<scenario>/<run>/
  context_packets/
    <agent_run_id>.json
```

## Guardrail Injection

Some guardrail scenarios require illegal tool batches or role mismatches that normal action strings do not express. Add explicit runner hooks for these cases:

- `replace_next_tool_batch(...)` for terminal-tool exclusivity.
- `force_agent_role(...)` for helper/harness gate mismatch.
- `set_tool_call_limit(...)` for max-step tests.
- `inject_notification_state(...)` for notification dedupe tests.

These hooks must be scenario-local and must write their activation into `metrics.json` or a dedicated audit artifact.

## Metrics Artifacts

Every T2/T3 capacity scenario should write a scenario summary:

```json
{
  "schema": "live_e2e.capacity.v1",
  "scenario": "capacity.full_system_capacity_matrix",
  "graph": {
    "missions": 0,
    "episodes": 0,
    "attempts": 0,
    "tasks": 0,
    "max_depth": 0,
    "max_width": 0
  },
  "tool_use": {
    "total": 0,
    "write_file": 0,
    "edit_file": 0,
    "read_file": 0,
    "shell": 0,
    "lsp": 0,
    "expected_errors": 0,
    "unexpected_errors": 0
  },
  "sandbox": {
    "occ_commits": 0,
    "overlay_captures": 0,
    "squashes": 0,
    "conflicts": 0
  },
  "context": {
    "packets": 0,
    "failed_packet_checks": 0
  },
  "audit": {
    "message_logs": 0,
    "task_logs": 0,
    "sandbox_event_rows": 0
  }
}
```

The test should read this artifact back through `sandbox.api.read_file` or `read_file`, then compare it with `RunReport` and persisted JSONL rows.

## Avoiding Runner Bloat

If one action grows past roughly 150 lines, split it into:

- a small action dispatch function.
- one fixture builder.
- one tool-call driver.
- one artifact verifier.

This keeps the harness reviewable while still allowing complex scenarios.
