---
name: team-planner-playbook
description: Playbook for the team_planner agent. Load inherited Task Center context, scout risk-bearing production ownership, and submit a schema-valid child plan with submit_plan(...).
---

# Team Planner Playbook

Produce a child task DAG from inherited Task Center context. Finish with exactly one `submit_plan(...)` call.

| Route | Use when |
| --- | --- |
| `developer` | Exact, live-proven owner plus one bounded mechanism. |
| `team_planner` | Broad, clustered, matrix-shaped, mixed, or unresolved owner boundary while depth remains. |
| `developer` / `validator` fallback | Max depth is reached; split by mechanism and keep uncertainty in `spec.detail`. |
| `validator` | Same-layer verification after producer lanes. |

## Stage Flow

```text
Caption: child planner stage machine. Each reference is loaded only at the stage that uses it.

assigned planner task
  |
  v
[1 Load context]
  | task + parent + deps + graph topology -> owner ledger
  |
  | scout would change this level's routing?
  |-- yes --> [2 Scout] -> harvest notes -> update ledger
  |-- no ---> carry uncertainty in child spec
  |
  v
[3 Synthesize]
  load submit-child-plan
  draft -> checklist -> submit_plan(...)
```

| Stage | Output |
| --- | --- |
| 1. Load context | Owner ledger: inherited owners, scout candidates, unresolved clusters, deps, verification evidence. |
| 2. Scout | Optional small scout wave, grouped by owner family. |
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
| Separate scouts | Candidate owner families are independent. |
| No scout | Exploration becomes decomposition; route to `team_planner` when depth allows. |

Keep scout `target_paths` as exact production coverage keys: one directory or a short file list, not a parent directory mixed with nested files or tests. Put tests, benchmark ids, optional-dependency signals, commands, and hypotheses in scout context. Launch the useful wave before polling, then read notes for every assigned path. Missing notes, cold CI, canceled scouts, or disproved exact files become uncertainty for that path only.

## 3. Synthesize

Enter after context is loaded, the ledger is complete, and scouts are done or intentionally skipped. Load the Stage 3 reference only now:

```text
load_skill_reference(
  skill_name="team-planner-playbook",
  reference_name="submit-child-plan"
)
```

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

Run the reference checklist, then make `submit_plan({ "new_tasks": [...] })` the final assistant action: no summary, output, parent ids, trailing prose, or later tool calls.
