---
name: team-planner-playbook
description: Playbook for the team_planner agent. Load inherited Task Center context, optionally scout routing-changing production ownership, and submit a schema-valid child plan with submit_plan(...).
---

# Team Planner Playbook

Produce a child task DAG from inherited Task Center context. Finish with exactly one `submit_plan(...)` call.

| Route | Use when |
| --- | --- |
| `developer` | Exact, live-proven owner plus one bounded mechanism. |
| `team_planner` | Broad, clustered, matrix-shaped, mixed, unresolved, or more than 3 owner families while depth remains. |
| `developer` / `validator` fallback | Max depth is reached; split by mechanism and keep uncertainty in `spec.detail`. |
| `validator` | Same-layer verification after producer lanes. |

| Gate | Action |
| --- | --- |
| 1-3 owner questions change this DAG | Scout by production owner family. |
| More than 3 owner families | Skip scouts; submit child `team_planner` clusters. |
| Test or benchmark path | Keep as evidence in `spec`, not `target_paths`. |

## Stage Flow

```text
Caption: child planner stage machine. Each reference is loaded only at the stage that uses it.

assigned planner task
  |
  v
[1 Load context]
  | task + parent + deps + graph topology -> owner ledger
  |
  | 1-3 owner questions would change this level's routing?
  |-- yes --> [2 Scout] -> harvest notes -> update ledger
  |-- no / >3 rows / test-only -> carry uncertainty in child spec
  |
  v
[3 Synthesize]
  Stage 2 closed -> draft -> checklist -> submit_plan(...)
```

| Stage | Output |
| --- | --- |
| 1. Load context | Owner ledger: inherited owners, scout candidates, unresolved clusters, deps, verification evidence. |
| 2. Scout | Small owner-family wave; avoid per-assertion or all-purpose scouts. |
| 3. Synthesize | Child local DAG with `developer`, `team_planner`, and optional `validator` nodes. |

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
| Build ledger | Group inherited owners, scout candidates, unresolved slices, dependency outputs, and evidence. |

Keep inherited wording intact when passing parent or dependency context to child specs.

## 2. Scout

Use this stage only when live evidence changes this level's DAG.

```text
Caption: one scout per owner-ledger row.

row: parquet family -> scout(["pkg/io/parquet"]) -> read_file_note(["pkg/io/parquet"])
row: config seam    -> scout(["pkg/config", "pkg/options"])
row: prompt family  -> scout(["pkg/prompt"])
```

| Scout shape | Use when |
| --- | --- |
| Single path | One file or module is the likely owner. |
| Multi-path | Paths form one dependency, entrypoint, adapter, or shared mechanism. |
| Directory | Owner is a package/subsystem and exact files are unknown. |
| Small wave | 1-3 independent owner-family rows would change lanes. |
| Too many rows | Submit child planners instead of scouting. |
| Test path | Keep in context only. |
| No scout | Leaf discovery or unrelated candidates; route to `team_planner` when depth allows. |

Keep `target_paths` to one directory or a short production file list. Put tests, benchmark ids, commands, optional-dependency signals, and hypotheses in scout context. Launch the useful wave before polling; missing notes, cold CI, canceled scouts, or disproved exact files become uncertainty for that path only.

## 3. Synthesize

Enter after context is loaded, the ledger is complete, and scouts are done or intentionally skipped.

```text
Caption: child routing with depth.

atomic exact owner                         -> developer
expandable + grandchild_depth <= max_depth -> team_planner
expandable + grandchild_depth > max_depth  -> broader developer/validator split
same-layer evidence                        -> validator with deps=[verified producers]
```

| Draft check | Expected result |
| --- | --- |
| Coverage | Every inherited cluster has a producer owner or child `team_planner`. |
| Developer lanes | Exact owner and one mechanism, unless this is a max-depth fallback. |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. |
| Validators | Depend on every same-payload producer they verify. |
| Payload | `id`, `agent`, `spec`, `deps`, and `scope_paths` only. |

Run this checklist, then make `submit_plan({ "new_tasks": [...] })` the final assistant action: no summary, output, parent ids, trailing prose, or later tool calls.
