---
name: team-planner-playbook
description: Playbook for the team_planner agent. Load inherited Task Center context, optionally scout routing-changing production ownership, and submit a schema-valid child plan with submit_plan(...).
---

# Team Planner Playbook

Produce a child task DAG from inherited Task Center context. Finish with exactly one `submit_plan(...)` call.

<Forbid Rule>
Never plan test suite or test-file related tasks.
Never assign subagents to explore test suites or test files.
</Forbid Rule>

| Route | Use when |
| --- | --- |
| `developer` | Atomic slice: exact owner + one mechanism + small failure surface. |
| `team_planner` | Broad, matrix, clustered, complex, or unresolved row while depth remains. |
| `developer` / `validator` fallback | Max depth reached; split by mechanism and keep uncertainty in `spec.detail`. |
| `validator` | Same-layer verification after producer lanes. |

| Gate | Action |
| --- | --- |
| Owner questions change this DAG | Scout one production owner-family row or small independent wave. |
| Test-only evidence | Keep in task/spec context, not workspace or scout targets. |

## Stage Flow

```text
Caption: child planner stage machine. Each reference is loaded only at the stage that uses it.

assigned planner task
  |
  v
[1 Load context]
  | task + parent + deps + graph topology -> owner ledger
  |
  | owner questions would change this level's routing?
  |-- yes --> [2 Scout row wave] -> harvest notes -> update ledger
  |-- several rows -> [2 Scout row wave] -> sibling lanes
  |-- no / test-only -> carry uncertainty in expandable spec
  |
  v
[3 Synthesize]
  Stage 2 closed -> load submit-child-plan -> submit_plan(...)
```

| Stage | Output |
| --- | --- |
| 1. Load context | Owner ledger: inherited owners, scout candidates, unresolved clusters, deps, verification evidence. |
| 2. Scout | Superficial directory/multi-file maps or deep tight-seam checks; production `target_paths` only. |
| 3. Synthesize | After scouts or no-scout decision, load reference and emit local DAG. |

## 1. Load Context

```text
Caption: inherited context becomes routing rows.

parent/deps/notes
  |-- proven owner + mechanism -----------> inherited owner
  |-- broad family / matrix / benchmark --> scout candidate
  |-- missing or ambiguous owner ---------> unresolved
  |-- upstream result needed here --------> deps
  `-- pytest ids / commands / repro ------> evidence
```

| Step | Action |
| --- | --- |
| Read context | Call `read_task_details(task_id=...)` for own task, parent, and each dep UUID. |
| Inspect topology | Call `read_task_graph()` for dependency topology only. |
| Classify intent | Mark bugfix, refactor, feature, migration, benchmark, or mixed. |
| Build ledger | Group inherited owners, mechanisms, deps, and evidence; keep tests in spec context. |

Planner exploration stops at routing; use scouts for owner maps and preserve uncertainty instead of proving leaves.

## 2. Scout

```text
Caption: scout fan-out follows owner-family rows, not individual tests.

owner ledger
  |-- exact file/symbol row ------> one deep single/multi-path scout
  |-- package/engine row ---------> one superficial directory scout
  |-- unrelated rows -------------> separate scouts or handoff
  `-- still broad after map ------> team_planner handoff
```

| Scout shape | Use when |
| --- | --- |
| Single/multi-path | One likely production owner, tight dependency, entrypoint, adapter, or shared mechanism. |
| Directory | Package, subsystem, engine matrix, or package-like import path; keep superficial. |
| Row wave | Independent production families; separate scouts, then stop after the first routing-changing wave. |

Use `input`, not `prompt`, so assigned `target_paths` reach the scout. Keep paths production-only; tests stay context only. Missing or disproved targets become a superficial directory scout or expandable handoff, not ad hoc replacement searching.

## 3. Synthesize

Enter after context and scout/no-scout closure. Load the Stage 3 reference first; use it for synthesis, not scout decisions.

```text
Caption: child routing with depth.

atomic + small surface               -> developer
broad / matrix cluster + depth       -> team_planner sibling
max-depth cluster                    -> per-mechanism developer/validator split
same-layer evidence                  -> validator with production scopes
```

| Draft check | Expected result |
| --- | --- |
| Coverage | Every inherited cluster has a producer owner or sibling `team_planner`; tiny slices stay separate. |
| Developer lanes | Exact owner, one mechanism, small failure surface unless max-depth fallback. |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. |
| Validators | Depend on every same-layer producer they verify; `scope_paths` are production surfaces. |
| Payload | `id`, `agent`, `spec`, `deps`, and `scope_paths` only. |

Run this checklist, then make `submit_plan({ "new_tasks": [...] })` the final assistant action: no summary, output, parent ids, trailing prose, or later tool calls.
