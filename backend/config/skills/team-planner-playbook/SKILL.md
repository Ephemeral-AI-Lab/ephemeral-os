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

## Overall Stage Flow

```text
Caption: child planner stage machine. Stages run in order; each has its own entry gate, exit gate, and (optional) reference.

  +-----------+    +-----------+    +-----------+    +-------------+    +---------------+
  |  analyze  | -> |  cluster  | -> |   scout   | -> |  synthesize | -> |  submit_plan  |
  +-----------+    +-----------+    +-----------+    +-------------+    +---------------+
        |                |                |                 |                   |
   inherited        owner ledger     scout notes       draft lanes        submit_plan(...)
   context          (axis-grouped)
        |                |                |                 |                   |
    exit: own/       exit: one        exit: wave       exit: ref loaded   exit: one tool
    parent/dep       owner family     returned or      + lanes pass       call, no prose
    notes read       per row          skip justified   checklist
```

| # | Stage | Input | Exit gate | Reference |
| --- | --- | --- | --- | --- |
| 1 | analyze | Assigned planner task + inherited Task Center context | Own/parent/dep details + graph topology read; tests split from production clues. | none |
| 2 | cluster | Analyze output | Every owner row carries one owner family + changelog axes. | none |
| 3 | scout | Cluster ledger | Scout wave returns, or every broad row has a documented "no scout" reason. | none |
| 4 | synthesize | Scout findings | Reference loaded, lanes drafted, draft checklist passes. | `load_skill_reference(skill_name="team-planner-playbook", reference_name="submit-child-plan")` — load before drafting any lane. |
| 5 | submit_plan | Drafted lanes | Exactly one `submit_plan({ "new_tasks": [...] })` call, no later tool calls or prose. | none |

## 1. Analyze

Enter from the assigned planner task. Read inherited context first; do not cluster, scout, or load references yet.

```text
Caption: inherited context becomes routing rows; parent directories are not grouping keys.

parent / deps / notes
  |-- proven owner + mechanism -----------> inherited owner clue
  |-- broad family / matrix / benchmark --> broad clue
  |-- missing or ambiguous owner ---------> unresolved clue
  |-- upstream result needed here --------> inbound dep
  `-- pytest ids / commands / repro ------> evidence (spec only)
```

| Step | Action |
| --- | --- |
| Read context | Call `read_task_details(task_id=...)` for own task, parent, and each dep UUID. |
| Inspect topology | Call `read_task_graph()` for dependency topology only. |
| Classify intent | Mark bugfix, refactor, feature, migration, benchmark, or mixed. |
| Tag clues | Tag each production clue as exact file/symbol, broad family, or guess. |
| Forbid | Never inspect, scout, or assign test paths. |

**Exit:** own/parent/dep details and graph topology are read; production clues are split from test/benchmark evidence.

## 2. Cluster

Enter after Analyze. Build the owner ledger; do not scout yet.

```text
Caption: every owner row is a routing decision until a scout returns or inherited evidence is already root-cause-grade.

clues
  |-- exact file or symbol -----------> atomic owner row
  |-- mechanism / engine / format ----> mechanism owner row
  |-- package / subsystem ------------> directory owner row
  `-- guess / test-derived -----------> unresolved owner row
```

| Check | Planner action |
| --- | --- |
| Clustering axes | Group by changelog axes (owner, mechanism, API, engine, format). F2P/P2P ids are acceptance criteria, not grouping axes. |
| Cluster name | One cluster = one owner family. Multi-owner names like "CLI/Config/Compat" or "Storage I/O" are defects — split before scout. |
| Inherited evidence | Keep tests and ids in spec context, not workspace or scout targets. |

Planner exploration stops at owner rows; HDF, JSON, parquet, groupby, utils, CLI, config, and compatibility remain separate rows unless live evidence proves a tight producer-consumer pair.

**Exit:** every owner row has a single owner family and recorded changelog axes.

## 3. Scout

Enter after Cluster. One scout per row; unrelated rows go in one parallel wave.

```text
Caption: scout fan-out by cluster shape: one row per call, package maps for broad rows, no parent-dir batching.

owner ledger
  |-- exact file/symbol row ------> one deep single-path scout
  |-- package/engine row ---------> one superficial directory scout
  |-- unrelated rows -------------> separate scouts in one parallel wave
  `-- still broad after map ------> team_planner handoff
```

| Scout shape | Use when |
| --- | --- |
| Single/multi-path | One owner row or one tight coupled pair (engine+adapter, producer+consumer); same parent directory is insufficient. |
| Directory | Package, subsystem, engine matrix, or package-like import path; keep superficial. |
| Row wave | Independent production families; split `cli.py`+`config.py`+`compat.py`, HDF+JSON/parquet, and groupby+utils into separate scouts. |
| No scout | Inherited notes already provide root-cause-grade evidence for this row. |

Dispatch each scout with `run_subagent(agent_name="scout", prompt="<scout prompt>")` — `prompt` is the only channel; production paths must be named inline. Keep paths production-only; never name a test path in a scout prompt and never call workspace/scout tools on tests. Missing or disproved targets become a superficial directory scout or expandable handoff, not ad hoc replacement searching.

### Scout Prompt Format

Every scout prompt uses these three sections, in order:

```text
## Task
<one-line routing question this scout answers>

## Exploration Path
<production path 1>
<production path 2>

## Terminal Contract
submit_file_note(paths=[<exploration_paths>], content="<finding>")
```

| Section | Contains |
| --- | --- |
| `## Task` | The single routing question this scout answers (one owner row, one tight coupled pair, or one directory). |
| `## Exploration Path` | Repo-relative production paths only — no test paths, no globs, no parent-dir batching. |
| `## Terminal Contract` | Literal `submit_file_note(paths=[...], content="...")` call template. Every path in `## Exploration Path` must appear in the `paths` argument of at least one submitted note. |

**Exit:** the scout wave returns, or every broad row has a documented reason for skipping scout.

## 4. Synthesize

Enter after the scout wave returns (or every broad row has a documented skip reason).

**Required first action this stage — before drafting any lane:**

```text
load_skill_reference(skill_name="team-planner-playbook", reference_name="submit-child-plan")
```

Synthesize scout findings into lanes; the DAG need not mirror clustering.

```text
Caption: child routing with depth.

atomic + small surface          -> developer
broad / matrix cluster + depth  -> team_planner sibling
max-depth cluster               -> per-mechanism developer/validator split
same-layer evidence             -> validator with production scopes
```

| Draft check | Expected result |
| --- | --- |
| Coverage | Every inherited cluster has a producer owner or sibling `team_planner`; tiny slices stay separate. |
| Developer lanes | Exact owner, one mechanism, small failure surface unless max-depth fallback; sibling lanes converging on a shared dispatch file (e.g. engine selector, adapter registry) collapse into one lane or chain via deps, never run as parallel siblings. |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. |
| Max-depth fallback | At max depth, replace planner nodes with focused per-mechanism `developer` (and `validator`) tasks; preserve uncertainty in `spec.detail`. |
| Validators | Depend on every same-layer producer they verify; `scope_paths` are production surfaces. |
| Payload | `id`, `agent`, `spec`, `deps`, and `scope_paths` only. |

**Exit:** the reference is loaded and every lane passes the draft checklist.

## 5. submit_plan

Enter after the draft checklist passes. Make `submit_plan({ "new_tasks": [...] })` the final assistant action.

```text
Caption: terminal contract.

draft lanes
  -> submit_plan({ "new_tasks": [...] })
  -> end (no further tool calls, no trailing prose)
```

| Submit check | Expected result |
| --- | --- |
| Tool count | Exactly one `submit_plan(...)` call this turn. |
| Trailing prose | None — `submit_plan` is the final assistant action. |
| Schema | Each task has `id`, `agent`, `spec`, `deps`, and `scope_paths` only. |
| Inherited UUIDs | Stay in `spec.detail`; never appear in `deps`. |
| Tests in scope | None — tests stay in `spec`, never in `scope_paths`. |

**Exit:** one `submit_plan` tool call emitted; no summary, output, parent ids, trailing prose, or later tool calls.
