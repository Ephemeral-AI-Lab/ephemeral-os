# Per-Call Snapshot Layer Stack Migration Plan

This folder splits the migration into implementation-sized phases. Each phase
document has the same shape:

1. task specification
2. main data objects
3. file/folder structure change
4. workflow demonstration
5. naming conventions and rationale

## Phase Order

| Phase | Document | Outcome |
|---|---|---|
| 01 | `phase-01-layer-stack-foundation.md` | Durable manifest, lease, merged-view, layer delta, and publish primitives live under `sandbox/layer_stack/`. |
| 02 | `phase-02-overlay-snapshot-runtime.md` | Overlay becomes only per-call mount, command execution, and upperdir capture. |
| 03 | `phase-03-occ-changeset-routing.md` | OCC accepts typed changesets from write/edit APIs and shell capture adapters. |
| 04 | `phase-04-occ-commit-transaction.md` | OCC final validation and layer publish become one atomic active-manifest transaction. |
| 05 | `phase-05-squash-lease-budget-gc.md` | Squash, lease pressure, and GC preserve leased snapshot readability. |
| 06 | `phase-06-integration-cutover.md` | Public APIs are routed to the new modules and old production paths are removed. |

## Supplemental Architecture Plans

| Document | Outcome |
|---|---|
| `three-server-workspace-overlay-integration-plan.md` | Defines `/testbed` as the default workspace repo, keeps `layer_stack_root` separate, and specifies the three-server overlay integration where `/testbed` is replaced by a frozen layer-stack snapshot during command execution. |
| `real-fs-request-snapshot-overlay-plan.md` | Replaces layer-stack-as-truth with real `/testbed` as truth plus per-request frozen snapshots. |
| `request-snapshot-performance-experiment-plan.md` | Defines the Phase 0 live Daytona experiment for snapshot create time, destroy time, 1/5/10 concurrent creation, and parallel factor before implementation. |

## Target Dependency Rule

```text
runtime/overlay_shell -> overlay + occ + layer_stack
overlay               -> layer_stack only
occ                   -> layer_stack only
layer_stack           -> no overlay, no occ, no git
```

## Non-Negotiable Design Rules

```text
OCC may prepare concurrently.
Final active-manifest validation + layer publish is atomic.
base_hash comes from the leased snapshot manifest.
Shell-captured tracked writes are strict full-file CAS writes.
Any shell tracked conflict publishes no shell layer.
Squash and GC never remove data needed by active leases.
```
