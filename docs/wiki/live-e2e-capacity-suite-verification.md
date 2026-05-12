---
title: "Live E2E Capacity Suite - Verification Plan"
tags: ["live-e2e", "capacity", "verification", "pytest", "daytona", "audit", "metrics"]
created: 2026-05-12T00:00:00.000Z
updated: 2026-05-12T00:00:00.000Z
sources: ["live-e2e-capacity-suite-plan.md", "live-e2e-capacity-suite-scenario-packs.md", "live-e2e-capacity-suite-harness-actions.md"]
links: ["live-e2e-capacity-suite-plan.md", "live-e2e-capacity-suite-scenario-packs.md", "live-e2e-capacity-suite-harness-actions.md"]
category: decision
confidence: high
schemaVersion: 1
---

# Live E2E Capacity Suite - Verification Plan

## Test Commands

T0 offline:

```bash
uv run pytest backend/src/live_e2e/tests/test_scenario_suite_imports.py -q
uv run pytest backend/src/live_e2e/tests/context backend/src/live_e2e/tests/planner_validation -q
```

T1 focused live:

```bash
EPHEMERALOS_DATABASE_URL="$EPHEMERALOS_DATABASE_URL" \
uv run pytest backend/src/live_e2e/tests/pipeline backend/src/live_e2e/tests/sandbox backend/src/live_e2e/tests/tools -q
```

T2 composite capacity:

```bash
EPHEMERALOS_DATABASE_URL="$EPHEMERALOS_DATABASE_URL" \
EPHEMERALOS_RUN_CAPACITY_LIVE_E2E=1 \
uv run pytest backend/src/live_e2e/tests/capacity -q
```

T3 soak/perf:

```bash
EPHEMERALOS_DATABASE_URL="$EPHEMERALOS_DATABASE_URL" \
EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1 \
EPHEMERALOS_FORCE_FRESH_SANDBOX=1 \
uv run pytest backend/src/live_e2e/tests/capacity/test_workspace_churn_soak.py -q
```

Exact env names can follow the existing `EPHEMERALOS_RUN_HEAVY_LIVE_E2E` pattern, but capacity and soak should be explicit opt-ins.

## Required Artifact Inspection

For every live scenario, inspect:

```text
.sweevo_runs/scenario_logs/<scenario>/<run>/
  run.json
  metrics.json
  sandbox_events.jsonl
  tasks/**/task.json
  tasks/**/message.jsonl
```

The test must assert:

- `run.json.status` matches expected scenario outcome.
- every launched task has a `task.json`.
- every launched agent run has a non-empty `message.jsonl`.
- sandbox-derived events are mirrored into `sandbox_events.jsonl`.
- expected errors are typed and counted.
- unexpected errors are zero.
- no row contains `internal_error`, stale layer-stack lowerdir errors, hidden scratch path exclusions, or untyped conflict failures.

## Graph Verification

Every pipeline or capacity scenario should validate:

- mission count.
- episode count per mission.
- attempt count per episode.
- task count per attempt.
- max graph depth.
- max graph width.
- terminal status counts.
- blocked task count when a failure branch is expected.
- child mission parent linkage for recursive cases.

For dynamic DAG scenarios, persist the planned graph shape as an artifact before execution:

```text
/ephemeral-os/.metrics/planned_graph.json
```

Then compare planned edges to TaskCenter persisted edges after the run.

## Sandbox Verification

Every sandbox or capacity scenario should validate:

- `SANDBOX_OCC_CHANGESET_RECEIVED` and `SANDBOX_OCC_CHANGES_COMMITTED` for public mutations.
- `SANDBOX_LAYER_STACK_LAYERS_SQUASHED` when crossing the squash threshold.
- `SANDBOX_CONFLICT_DETECTED` for intentional conflicts only.
- `ShellResult.changed_paths` for shell mutations.
- direct API and toolkit read consistency for selected files.
- final pytest or import checks when the scenario builds a Python project.

Tri-source checks should use:

1. toolkit `read_file`, stripped of line-number decoration.
2. shell `cat`.
3. direct `sandbox.api.read_file`.

## LSP Verification

LSP scenarios should report by tool:

- call count.
- semantic check count.
- failed semantic check count.
- cold-start timing.
- warm-session timing.
- diagnostics wait timing.

Required semantic checks:

- diagnostics detect a broken file and clear after repair.
- hover contains the expected symbol.
- definition location matches expected file and line within a small tolerance.
- references include source files and test files where applicable.
- workspace symbols include newly written files and do not report deleted stale paths.

## Context Verification

Context scenarios should inspect packet structure and rendered text:

- block count.
- block kind sequence.
- priorities.
- headings.
- source ids.
- inherited parent metadata.
- packet target role and target id.

Avoid brittle whole-prompt equality. Whole-prompt equality belongs only to the user-input source-of-truth test.

## Guardrail Verification

Guardrail scenarios should assert:

- the failing hook name appears in `hookSpecificOutput`.
- the failing tool result is marked error.
- the failure does not mutate TaskCenter state unless explicitly expected.
- recovery action still runs when the scenario says it should.
- notifications are delivered exactly once per trigger condition.

## Performance Gates

Perf gates should be trend-aware and workload-aware. Do not gate tiny scenarios on percentage changes.

Recommended metrics:

| Metric | Gate |
|---|---|
| unexpected tool errors | `== 0` |
| expected tool errors | exact count |
| missing message logs | `== 0` |
| missing task logs | `== 0` |
| context packet failures | `== 0` |
| LSP semantic failures | `== 0` |
| tri-source projection failures | `== 0` |
| full capacity p95 shell edit wall time | report, compare against prior nightly |
| full capacity p95 LSP warm call | report, compare against prior nightly |
| workspace churn squash count | `>= expected threshold` |

## Rollout Phases

### Phase 1 - Gap-closing focused scenarios

Implement the missing docs/wiki matrix cells with minimal action scripts:

- pipeline fan-in, diamond, blocked descendants, nested mission, planner retry, generator retry.
- planner validation unknown dep, cycle, partial without continuation, unknown agent, empty tasks.
- context generator/evaluator/helper packet scenarios.
- tools guardrail scenarios.
- sandbox overlay, command exec, and focused LSP scenarios.

### Phase 2 - Harness action cleanup

Add `capacity_actions/`, generic adapter, context packet artifacts, and metrics schema.

### Phase 3 - Composite capacity scenarios

Implement:

- `capacity.full_system_capacity_matrix`.
- `capacity.recursive_release_train`.
- `capacity.guardrail_recovery_gauntlet`.

### Phase 4 - Soak/perf

Implement:

- `capacity.workspace_churn_soak`.
- fresh sandbox run.
- reused sandbox reset run.
- report artifact that names run dirs and compares metrics.

### Phase 5 - CI policy

Wire markers so:

- T0 runs on normal PRs.
- T1 runs in Daytona-enabled PR tier.
- T2 runs on demand or nightly.
- T3 runs nightly only.

## Completion Report Template

Each capacity validation report should include:

```text
Scenario:
Run dir:
Sandbox id:
Instance id:
Graph: missions= episodes= attempts= tasks= max_depth= max_width=
Tools: total= expected_errors= unexpected_errors=
Sandbox: commits= squashes= conflicts= overlay_captures=
LSP: total= failures= warm_p95_ms= cold_p95_ms=
Context: packets= failures=
Audit: task_logs= message_logs= sandbox_events=
Verdict:
```
