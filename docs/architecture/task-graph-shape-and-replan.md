# Task Graph Shape and Replan Lifecycle

This document describes the *shape* of the EphemeralOS task graph and walks
through how replanning mutates that shape. It complements
`replan-workflow-sequence-diagrams.md`, which covers the control-flow
timeline; here we focus on the graph topology and state transitions.

## Shape classification

EphemeralOS's task graph is a **Hierarchical Task DAG (HT-DAG)** —
equivalently, a *tree of DAGs*:

- **Tree backbone** via `Task.parent_id`: every planner expansion creates
  children that reference the planner as their parent. `max_depth` bounds
  recursion.
- **Per-level sibling DAG** via `Task.deps`: children of the same parent
  form a local directed acyclic graph that controls `READY` / `PENDING`
  scheduling.
- **Submitted plans are local**: each `submit_plan` dependency must name
  another task in the same payload. Existing task IDs are not valid
  `submit_plan` deps.
- **AND-decomposition**: a planner in status `EXPANDED` is gated on its
  subtree. It can only promote to `DONE` once its children have resolved.
- **No cross-subtree deps by construction**: replan dep resolution only
  admits local aliases or tasks in the replanner's *allowed region*
  (see `PlanExpander.apply_replan`).

Contrast with a *flat DAG* (e.g. Ralphinho RFC pipeline): EphemeralOS
decomposition is **deferred and recursive**, not one-shot at the root.

### Diagram legend

```
  ●  pending      ◐  ready       ◑  running     ◯  expanded
  ✕  failed       ⊗  cancelled   ⟲  request_replan
  ══  parent_id   ──▶ deps       ╳  detached (failed/cancelled/request_replan)
```

A task is **detached** when it can no longer contribute a `DONE` outcome
to its parent. `FAILED` and `CANCELLED` are detached by definition
(`Task.detached`). `REQUEST_REPLAN` is treated as detached for graph
scheduling purposes: dependents of a replanning node must remain `PENDING`
until they are rewired, per the `GraphInvariantViolation` rule.

## Replan lifecycle — four frames

### Frame 1 — Steady state (T2 running, T3/T4 waiting on T2)

```
                 ┌─────────────┐
                 │ ROOT planner│ ◯ expanded
                 └──────┬──────┘
                        ║ parent_id
         ┌──────────────╬──────────────┐
         ║              ║              ║
         ▼              ▼              ▼
      ┌─────┐        ┌─────┐        ┌─────┐
      │ T1  │ ◑     │ T2  │ ◑     │ T3  │ ●
      │ dev │run    │ dev │run    │ dev │pend
      └─────┘       └──┬──┘       └─────┘
                       │
                       └────dep────▶ T3
                       └────dep────▶ T4

                                    ┌─────┐
                                    │ T4  │ ●
                                    │ dev │pend
                                    └─────┘
```

### Frame 2 — T2 issues `request_replan` (T2 becomes detached)

```
                 ┌─────────────┐
                 │ ROOT planner│ ◯
                 └──────┬──────┘
                        ║
         ┌──────────────╬──────────────┐
         ║              ║              ║
         ▼              ▼              ▼
      ┌─────┐        ┌─────┐        ┌─────┐
      │ T1  │ ◑     │ T2  │ ⟲ ╳  │ T3  │ ●
      │ dev │       │ dev │detach │ dev │pend
      └─────┘       └──┬──┘       └─────┘
                       │  (stale deps point at a detached task;
                       │   dependents must stay `pending` per the
                       │   GraphInvariantViolation invariant)
                       ▼
                   T3, T4 cannot schedule
```

### Frame 3 — Create `REPLAN_T2`, rewire deps, keep T2 as REQUEST_REPLAN

```
                 ┌─────────────┐
                 │ ROOT planner│ ◯
                 └──────┬──────┘
                        ║ parent_id
         ┌──────────────╬───────────────┬──────────────┐
         ║              ║               ║              ║
         ▼              ▼               ▼              ▼
      ┌─────┐       ┌─────┐         ┌─────────┐     ┌─────┐
      │ T1  │ ◑    │ T2  │ ⟲ ╳    │REPLAN_T2│ ◐  │ T3  │ ●
      │ dev │      │ dev │detach  │ replnr  │ready│ dev │pend
      └─────┘      └─────┘         └────┬────┘     └─────┘
                                        │
                                        │◀── dep rewired ── T3
                                        │◀── dep rewired ── T4
                                        ▼
                                     (T3, T4 now wait on REPLAN_T2)
                                     ┌─────┐
                                     │ T4  │ ●
                                     └─────┘
```

Key moves:
- T2 enters and stays `REQUEST_REPLAN` (terminal, detached). When recovery succeeds, the runtime records `replanned_by:<replanner_id>` on T2's failure reason rather than changing its status.
- `REPLAN_T2` is inserted as a sibling of T2 under ROOT.
- Every dependent of T2 has its `deps` rewritten to point at
  `REPLAN_T2`, restoring the "dependents must be pending" invariant
  without leaking stale edges onto a detached node.

### Frame 4 — `REPLAN_T2` runs `apply_replan` (cancel region + add children)

```
                 ┌─────────────┐
                 │ ROOT planner│ ◯
                 └──────┬──────┘
                        ║
         ┌──────────────╬───────────────┬───────────────┐
         ║              ║               ║               ║
         ▼              ▼               ▼               ▼
      ┌─────┐       ┌─────┐        ┌─────────┐      ┌─────┐
      │ T1  │ ◑    │ T2  │ ✕ ╳   │REPLAN_T2│ ◯   │ T3  │ ⊗ ╳
      │ dev │      │ dev │        │ EXPANDED│expnd │ dev │cancel
      └─────┘      └─────┘        └────┬────┘      └─────┘
                                       ║ parent_id
                                       ║         ┌─────┐
                                       ║         │ T4  │ ⊗ ╳
                                       ║         │ dev │cancel
                                       ║         └─────┘
                     ┌─────────────────╬────────────────┐
                     ║                 ║                ║
                     ▼                 ▼                ▼
                  ┌─────┐           ┌─────┐          ┌─────┐
                  │ N1  │ ◐       │ N2  │ ●        │ N3  │ ●
                  │ dev │ready    │ dev │pend      │ dev │pend
                  └─────┘         └──┬──┘          └─────┘
                                     │
                                     └──dep──▶ N3
```

Effects of `apply_replan`:
- `REPLAN_T2` cancels **T3, T4** (they sit in its allowed region —
  former dependents of the failed T2).
- `REPLAN_T2` submits a plan adding `N1, N2, N3` as **direct children of
  REPLAN_T2** (enforced by the `misplaced` check in `PlanExpander`).
- `REPLAN_T2` enters `EXPANDED`, waiting on its local subgraph.
- ROOT's success now depends on: `T1` completing **and** `REPLAN_T2`'s
  subtree completing.

## State-machine summary

```
T2:        RUNNING ─▶ REQUEST_REPLAN ╳ ─▶ FAILED ╳   (terminal)
REPLAN_T2:    (new) ─▶ READY ─▶ RUNNING ─▶ EXPANDED ─▶ DONE
T3, T4:    PENDING ─▶ (rewired) ─▶ CANCELLED ╳       (evicted by apply_replan)
N1..N3:    (new children of REPLAN_T2; local sibling DAG)
```

Three detached conditions (`FAILED`, `CANCELLED`, `REQUEST_REPLAN`) share a
single rule: **a detached task cannot gate live work**. Its dependents are
either moved back to `pending` awaiting a rewire, or cancelled as part of
the replanner's allowed region.

## Related code

- `backend/src/team/models.py` — `Task`, `TaskStatus`, `TaskDefinition`,
  `Plan`, `ReplanPlan`.
- `backend/src/team/planning/expander.py` — `expand_submitted_plan`,
  `apply_replan`.
- `backend/src/team/planning/replan_validation.py` — allowed-region rules.
- `backend/src/team/persistence/task_graph.py` — adjacency + atomic
  replan commit.
- `docs/architecture/replan-workflow-sequence-diagrams.md` — companion
  sequence diagrams.
