---
title: "Live E2E Capacity Suite - Scenario-Based Testing Plan"
tags: ["live-e2e", "capacity", "scenario-suite", "sandbox", "task-center", "lsp", "context-engine", "guardrails"]
created: 2026-05-12T00:00:00.000Z
updated: 2026-05-12T00:00:00.000Z
sources: ["live-e2e-scenario-suite-design.md", "task-center-pipeline.md", "sandbox-subsystem.md", "tools-hooks-guardrails-agents-notifications-messages.md", "context-engine-recipes.md"]
links: ["live-e2e-capacity-suite-scenario-packs.md", "live-e2e-capacity-suite-harness-actions.md", "live-e2e-capacity-suite-verification.md"]
category: decision
confidence: high
schemaVersion: 1
---

# Live E2E Capacity Suite - Scenario-Based Testing Plan

## Purpose

Build a complex, scenario-driven live E2E suite that proves EphemeralOS capacity across the whole runtime:

- Task graph lifecycle, retries, nested missions, dependency fan-in/fan-out, and failure quiescence.
- Real sandbox mutations through public agent tools: `write_file`, `edit_file`, `read_file`, `shell`, batch edit, direct `sandbox.api`, and plugin/LSP calls.
- OCC, overlay capture, layer-stack publish/squash/lease behavior, daemon routing, and workspace-base rebinding.
- Tool guardrails, hook failures, terminal-tool exclusivity, notification delivery, and budget limits.
- Context engine packet/rendering correctness across planner, generator, evaluator, helpers, and entry executor.
- Audit durability in `.sweevo_runs/scenario_logs`, not just pytest pass/fail.

This plan intentionally combines two styles:

1. Focused scenarios that isolate one subsystem and make failures cheap to debug.
2. Composite capacity scenarios that chain many subsystems together and test the operational ceiling of the harness.

## Non-Negotiable Invariants

- The harness remains deterministic at the model seam: real TaskCenter, real stores, real sandbox/tool execution, mocked agent choices through `MockSquadRunner`.
- Sandbox mutations must be driven by agent tool scripts. Direct pytest-side filesystem mutation does not count as live coverage.
- User-input scenarios must use the already-rendered prompt passed into TaskCenter. They must not reconstruct or enrich SWE-EVO prompt text from the CSV inside the scenario.
- A scenario is not verified until its `.sweevo_runs/scenario_logs/<scenario>/<run>/` artifacts are inspected for graph shape, event shape, message streams, sandbox events, and forbidden signatures.
- Reused sandboxes must rebuild the public-tool workspace base before each scenario through the shared workspace reset boundary.
- Focused failures should identify an owning package: `task_center`, `sandbox`, `tools`, `context_engine`, `plugins`, or `live_e2e`.

## Suite Tiers

| Tier | Marker | Runtime | Goal | Typical duration |
|---|---|---|---|---|
| T0 offline conformance | `live_e2e_offline` | Postgres or in-memory stores, no Daytona | Registry, protocol, context packet rendering, invalid-plan rejection | seconds |
| T1 focused live | `live_e2e_daytona` | Real Daytona sandbox, mocked agent decisions | One subsystem per scenario, real tool execution | minutes |
| T2 composite capacity | `live_e2e_capacity` | Real Daytona sandbox, high tool volume | Cross-subsystem load and adversarial interactions | 15-60 minutes |
| T3 soak/perf | `live_e2e_soak` | Fresh and reused Daytona sandboxes | Long-run stability, perf regression detection, artifact integrity | nightly |

T1 closes the known docs/wiki coverage gaps. T2 and T3 test the full capacity of the system.

## Capacity Dimensions

The suite should scale across several axes instead of only increasing tool-call count:

| Axis | Capacity target | Evidence |
|---|---|---|
| Graph width | 20-40 runnable tasks with mixed dependency shape | task snapshots, launch order, blocked/done counts |
| Graph depth | 5+ dependency levels plus recursive missions | graph summary, child mission parent linkage |
| Retry pressure | planner, generator, evaluator, verifier, and child mission failures | attempt sequence, fail reasons, recovery attempts |
| File volume | 50-200 files, nested dirs, tests, configs, symlinks | tri-source projection checks, pytest/import logs |
| Mutation mix | write/edit/shell/batch/direct API/LSP after edit | `message.jsonl`, tool metadata, sandbox events |
| Layer depth | natural squash threshold crossing and post-squash reads | `SANDBOX_LAYER_STACK_LAYERS_SQUASHED`, timing metadata |
| LSP semantics | diagnostics, hover, definitions, references, symbols after edits | semantic assertions by tool and by file |
| Context size | retry landscape, dependency summaries, prior episodes, inherited helper context | captured packets and rendered prompt checks |
| Guardrail pressure | illegal terminal batches, helper role mismatch, request mission after edit | hook-specific tool result payloads |
| Audit durability | no missing task/message/sandbox rows under high event volume | run tree consistency verifier |

## Document Set

- `live-e2e-capacity-suite-scenario-packs.md`: scenario catalog, names, objectives, paths, and acceptance criteria.
- `live-e2e-capacity-suite-harness-actions.md`: required runner actions, prepared tool scripts, fixtures, and adapter changes.
- `live-e2e-capacity-suite-verification.md`: execution tiers, commands, artifact inspection, metrics gates, and rollout phases.

## Target Repository Shape

Use the existing taxonomy for focused tests and add one explicit capacity package for cross-cutting composites:

```text
backend/src/live_e2e/scenarios/
  pipeline/
  sandbox/
  tools/
  context/
  planner_validation/
  capacity/
    full_system_capacity_matrix.py
    recursive_release_train.py
    workspace_churn_soak.py
    guardrail_recovery_gauntlet.py

backend/src/live_e2e/squad/
  capacity_actions/
    __init__.py
    graph.py
    workspace.py
    lsp.py
    guardrails.py
    audit.py

backend/src/live_e2e/tests/
  pipeline/
  sandbox/
  tools/
  context/
  planner_validation/
  capacity/
```

The `capacity/` package is for scenarios that intentionally span multiple owners. Focused scenarios should stay in the owner package.

## Done Criteria

A capacity-suite implementation is done when:

- Every scenario in the docs/wiki scenario matrix is either implemented or explicitly superseded by a named scenario with equal or stronger assertions.
- T0 runs locally without Daytona.
- T1 focused live runs against a fresh Daytona sandbox and a reused-reset sandbox.
- T2 composite capacity emits a complete `.sweevo_runs` tree and a metrics artifact with tool, graph, sandbox, LSP, context, and audit sections.
- T3 nightly can run repeatedly without stale workspace-base leakage, missing message logs, or hidden LSP path exclusions.
- The verification report names exact scenario run directories, not only pytest output.
