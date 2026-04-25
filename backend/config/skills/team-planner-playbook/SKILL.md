---
name: team-planner-playbook
description: Playbook for the team_planner agent. Analyze inherited context, cluster owner rows, scout, synthesize, and submit a schema-valid child DAG with submit_plan(...).
---

# Team Planner Playbook

Produce a child task DAG from inherited Task Center context. Finish with exactly one `submit_plan(...)` call.

<Forbid Rule>
Never plan test suite or test-file related tasks for `developer` or `validator` lanes — agents do not own test files. `scope_paths` must be production paths only.
Scouts MAY inspect test files when reading them helps confirm production ownership; do not put test paths in lane `scope_paths` or assign test-editing work to any agent.
</Forbid Rule>

## Recursive Exploration Principle

You sit **inside a recursive planner tree**. Your parent already grouped the work coarsely; your job is to refine the grouping one step further, do a shallow scout to confirm the next layer of owner families, and either (a) route to `developer` if a row is now atomic, or (b) hand the still-broad rows to `team_planner` while depth remains.

```text
Caption: each child planner level scouts shallowly and recurses.

parent planner / root
  -> this team_planner (one inherited cluster)
       -> finer cluster (mechanism / API / engine / format inside the inherited row)
       -> shallow scout wave (confirm owner file or sub-directory per row)
       -> route: developer (atomic) | team_planner (still broad, depth remains) | validator
                                                  |
                                                  v
                                            team_planner (deeper child)
```

| Principle | What it means at this level |
| --- | --- |
| Shallow scouting | At least one scout wave is required when the inherited evidence is not already root-cause-grade; default mode is `directory_superficial` or `bundled_superficial`. The scout's job is to confirm the next layer of ownership, not to fully RCA the bug. |
| Recursive subdivision | Default broad/unresolved rows to `team_planner` while depth remains. Use `developer` when this level's notes (inherited or freshly scouted) already prove exact owner + edit seam. |
| Depth awareness | If grandchild depth is still within max depth, prefer recursion over flattening. At max depth, fall back to per-mechanism `developer` / `validator` split (see Stage 4). |
| Budget | Cap scouts to one per refined cluster row, one parallel wave. Do not exhaustively explore the inherited cluster — let your own children explore further. |

| Route | Use when |
| --- | --- |
| `developer` | Atomic slice proven by inherited notes or this level's shallow scout: exact owner + one mechanism + small failure surface. |
| `team_planner` | **Default** for broad, matrix, clustered, complex, or unresolved-after-shallow-scout rows while depth remains. |
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
| 3 | scout | Cluster ledger | Notes harvested for every scouted production path, or the row is marked unresolved. | none |
| 4 | synthesize | Scout findings | Reference loaded; no new scouts or note reads; lanes drafted and checklist passes. | `load_skill_reference(skill_name="team-planner-playbook", reference_name="submit-child-plan")` — load before drafting any lane. |
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
| Evidence boundary | Keep test paths and failing-test labels out of owner-row names and lane `scope_paths`. Scouts may include test paths as triangulation hints; lanes (developer/validator) own production paths only. |
| Agent ownership | No `developer` or `validator` lane owns or edits test files. Production-only `scope_paths`; tests live as evidence in `spec.detail` / `spec.acceptance_criteria`. |

**Exit:** own/parent/dep details and graph topology are read; production clues are split from test/benchmark evidence.

## 2. Cluster

Enter after Analyze. Build the owner ledger; do not scout yet.

```text
Caption: every owner row is a routing decision until a scout returns or inherited evidence is already root-cause-grade.

clues
  |-- proven exact file or symbol ----> atomic owner row
  |-- mechanism / engine / format ----> mechanism owner row
  |-- package / subsystem ------------> directory owner row
  `-- guess / test-derived -----------> unresolved owner row
```

| Check | Planner action |
| --- | --- |
| Clustering axes | Group by changelog axes (owner, mechanism, API, engine, format). |
| Refine one step | Refine the inherited cluster one level finer (e.g. inherited `dataframe-groupby-cluster` → rows for `cov-non-numeric`, `dtypes-deprecation`, `index-numeric-dtypes`); do not try to land at atomic owners in one shot when depth still allows recursion. |
| Cluster name | One row = one owner family. Slash/plus names that combine unrelated concerns signal unrelated owners; split now. |
| Mixed support rows | Entrypoints, config loaders, compatibility helpers, dataframe utilities, and storage formats are separate rows unless live evidence proves one tight pair. Three or more files are never one bundled row. |
| Inherited evidence | Keep tests and ids in spec context. Never invent `<test-stem>.py`; package/engine clues stay directory rows until proven exact. |

Planner exploration stops at owner rows; unrelated concerns remain separate unless live evidence proves a tight producer-consumer pair.

**Exit:** every owner row has a single owner family and recorded changelog axes.

## 3. Scout

Enter after Cluster. One scout per row; unrelated rows go in one parallel wave.

```text
Caption: scout mode is proportional to certainty: trivial exact files get depth; bundles/directories get relationship maps.

owner ledger
  |-- proven exact file/symbol ---> deep single-path scout
  |-- package/engine row ---------> superficial directory scout
  |-- tight same-owner bundle ----> superficial relationship scout
  |-- unrelated rows -------------> separate scouts in one wave
  `-- still broad after map ------> team_planner handoff
```

**Default at child depth: shallow.** While depth remains, prefer `directory_superficial` or `bundled_superficial`; reserve `trivial_deep` for rows where inherited or scout evidence already names one exact file. Aim to confirm the next layer of ownership, not to fully RCA.

| Scout shape | Use when |
| --- | --- |
| Directory superficial | Package, subsystem, engine matrix, or package-like import path; map files and relationships without deep leaf RCA. |
| Bundled superficial | Several paths in one owner family or exactly one tight pair; same parent directory, call chain, or "small row" status is not enough. Ask only for relationship map, ownership boundaries, and handoff seams. |
| Trivial deep | One proven exact file/symbol; ask for line-level functions, likely edit seam, and concrete gaps. Guessed or test-derived filenames do not qualify. |
| Row wave | Independent families; dispatch separate scouts in one wave. Never batch unrelated owner families, and split any 3+ path idea before dispatch. |
| No scout | Inherited notes already provide root-cause-grade evidence for this row. |
| Budget | Cap at one scout per refined cluster row, one wave. Rows that stay broad after shallow scout become `team_planner` lanes (depth permitting) — do not re-scout in this turn. |

Dispatch each scout with `run_subagent(agent_name="scout", prompt="<scout prompt>")`; `prompt` is the only channel. State the scout mode in `## Task`. Missing/disproved exact targets become directory scouts in Stage 3 or unresolved handoff. The scout's job is to identify the *production owner*; test paths may appear in the prompt as triangulation hints when they help find that owner.

### Scout Prompt Format

```text
## Task
Mode: <trivial_deep | bundled_superficial | directory_superficial>. <one production routing question>

## Exploration Path
<production path 1>
<production path 2>
[<test path>]   # optional: include only when reading the test helps confirm production ownership

## Terminal Contract
submit_file_note(paths=[<exploration_paths>], content="<finding>")
```

| Section | Contains |
| --- | --- |
| `## Task` | One production routing question; the goal is identifying the production owner, not editing tests. |
| `## Exploration Path` | Repo-relative paths the scout should read. Production paths are required; test paths may be added as triangulation hints — no globs, no parent-dir batching. |
| `## Terminal Contract` | Literal `submit_file_note(paths=[...], content="...")` call template. Every path in `## Exploration Path` must appear in the `paths` argument of at least one submitted note. |

**Exit:** the scout wave returns, or every broad row has a documented reason for skipping scout.

## 4. Synthesize

Enter after the scout wave returns and notes are read. Do not backtrack to scout after loading the reference.

**Required first action this stage — before drafting any lane:**

```text
load_skill_reference(skill_name="team-planner-playbook", reference_name="submit-child-plan")
```

Synthesize from the exploration-note ledger, not the Stage-2 cluster ledger. Missing notes or guessed root causes stay unresolved and route to `team_planner`, not `developer`.

```text
Caption: child routing with depth.

note proves exact owner + edit seam -> developer
note maps relationship / gap + depth -> team_planner
max-depth unresolved gap -----------> per-mechanism developer/validator split
same-layer evidence ----------------> validator
```

| Draft check | Expected result |
| --- | --- |
| Coverage | Every note-backed owner or unresolved gap has a lane; Stage-2 clusters are not lane templates. |
| Default route (depth remains) | `team_planner` is the default lane for any row that is broad, multi-owner, or unresolved-after-shallow-scout. Use `developer` only when notes (inherited or freshly scouted) prove exact owner + edit seam at this level. |
| Note quality | Notes must name a production owner (file/directory/symbol). Test paths cited as triangulation hints are fine; if a note only points at tests with no production owner identified, route the gap to `team_planner` while depth remains. |
| Developer lanes | Exact owner, one mechanism, small failure surface unless max-depth fallback; sibling lanes converging on a shared dispatch file (e.g. engine selector, adapter registry) collapse into one lane or chain via deps, never run as parallel siblings. |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. Each `team_planner` lane carries one refined owner family/mechanism with its scout-confirmed production directory in `scope_paths`. |
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
