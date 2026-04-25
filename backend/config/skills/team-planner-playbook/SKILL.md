---
name: team-planner-playbook
description: Playbook for the team_planner agent. Analyze inherited context, cluster owner rows, scout, synthesize, and submit a schema-valid child DAG with submit_plan(...).
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
| Owner questions change this DAG | Scout one production owner row or small independent row wave. |
| Test-only evidence | Keep in task/spec context; do not inspect, scout, or assign test paths. |

## Stage Flow

```text
Caption: child planner stage machine. Each reference loads only at the stage that uses it.

assigned planner task
  |
  v
analyze -> cluster -> scout -> synthesize -> DAG
  |         |          |        |             |
context   owner rows  notes    draft lanes   submit_plan(...)
```

| Stage | Output |
| --- | --- |
| Analyze | Read own, parent, deps, notes, and topology; split tests from production clues. |
| Cluster | Owner ledger grouped by owner, mechanism, API, engine, and format. |
| Scout | One production row per scout; unrelated rows use a small parallel wave. |
| Synthesize | After scout/no-scout closure, load reference and draft lanes. |
| DAG | Submit the schema-valid child task graph. |

## 1-2. Analyze + Cluster

```text
Caption: inherited context becomes routing rows; parent directories are not grouping keys.

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
| Build ledger | Group by changelog axes (owner, mechanism, API, engine, format). F2P/P2P ids are acceptance criteria, not grouping axes; keep tests in spec context. |

Planner exploration stops at routing; HDF, JSON, parquet, groupby, utils, CLI, config, and compatibility remain separate rows unless live evidence proves a tight producer-consumer pair.

## 2. Scout

```text
Caption: scout fan-out by cluster shape: one row per call, package maps for broad rows, no parent-dir batching.

owner ledger
  |-- exact file/symbol row ------> one deep single-path scout
  |-- package/engine row ---------> one superficial directory scout
  |-- unrelated rows -------------> separate scouts or handoff
  `-- still broad after map ------> team_planner handoff
```

| Scout shape | Use when |
| --- | --- |
| Single/multi-path | One owner row or one tight coupled pair (engine+adapter, producer+consumer); same parent directory is insufficient. |
| Directory | Package, subsystem, engine matrix, or package-like import path; keep superficial. |
| Row wave | Independent production families; split `cli.py`+`config.py`+`compat.py`, HDF+JSON/parquet, and groupby+utils into separate scouts. |

Use `input`, not `prompt`, so assigned `target_paths` reach the scout. Keep paths production-only; tests stay context only; never call workspace/scout tools on tests. Missing or disproved targets become a superficial directory scout or expandable handoff, not ad hoc replacement searching.

## 4-5. Synthesize + DAG

Enter after context and scout/no-scout closure. Load the synthesize reference; synthesize scout findings into the DAG — it need not mirror clustering.

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
| Developer lanes | Exact owner, one mechanism, small failure surface unless max-depth fallback; sibling lanes converging on a shared dispatch file (e.g. engine selector, adapter registry) collapse into one lane or chain via deps, never run as parallel siblings. |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. |
| Validators | Depend on every same-layer producer they verify; `scope_paths` are production surfaces. |
| Payload | `id`, `agent`, `spec`, `deps`, and `scope_paths` only. |

Run this checklist, then make `submit_plan({ "new_tasks": [...] })` the final assistant action: no summary, output, parent ids, trailing prose, or later tool calls.
