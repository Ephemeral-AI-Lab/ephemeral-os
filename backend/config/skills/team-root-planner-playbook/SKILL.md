---
name: team-root-planner-playbook
description: Playbook for the root_planner agent. Analyze, cluster, scout owner rows, synthesize, then submit a schema-valid root DAG with submit_plan(...).
---

# Team Root Planner Playbook

Produce the top-level task DAG from the user request. Finish with exactly one `submit_plan(...)` call.

<Forbid Rule>
Never plan test suite or test-file related tasks for `developer` or `validator` lanes — agents do not own test files. `scope_paths` must be production paths only.
Scouts MAY inspect test files when reading them helps confirm production ownership; do not put test paths in lane `scope_paths` or assign test-editing work to any agent.
</Forbid Rule>

## Recursive Exploration Principle

You are the **top of a recursive planner tree**, not a single planner that must reach atomic owners. Explore only enough to *group* the request into owner families and confirm each group has a real production directory; defer leaf-level RCA to child `team_planner` lanes.

```text
Caption: each planner level does partial exploration; child planners deepen.

root_planner (this agent)
  -> coarse cluster (PR / changelog section / subsystem family)
  -> shallow scout wave (confirm directory + owner family per row)
  -> route: team_planner (default) | developer (only if scout proved exact owner + seam) | validator
                                |
                                v
                        team_planner (child, recursive)
                          -> finer cluster, shallow scout, route deeper or to developer
```

| Principle | What it means at this level |
| --- | --- |
| Shallow scouting | At least one scout wave is required; default mode is `directory_superficial`. The scout's job is to *confirm the owner directory/family exists*, not to find the edit seam. |
| Recursive subdivision | The default lane is `team_planner`. A row only becomes a `developer` here when one shallow scout already proved exact owner file + likely edit seam. |
| No exhaustive exploration | For large multi-PR inputs (e.g. changelog bundles with many PR sections), cluster by PR / changelog axis / subsystem and let each cluster recurse — do not try to scout every PR's owner from the root. |
| Budget | Cap scouts to one per coarse cluster row in one parallel wave. If a cluster has unclear shape after one shallow scout, route it to `team_planner`; do not re-scout from root. |

| Route | Use when |
| --- | --- |
| `developer` | Atomic slice already proven by this level's shallow scout: exact owner + one mechanism + small failure surface. |
| `team_planner` | **Default** for any broad, matrix, clustered, multi-PR, or unresolved-after-shallow-scout row — defer deeper exploration to the child planner. |
| `validator` | Same-payload verification after producer lanes. |

## Overall Stage Flow

```text
Caption: root planner stage machine. Stages run in order; each has its own entry gate, exit gate, and (optional) reference.

  +-----------+    +-----------+    +-----------+    +-------------+    +---------------+
  |  analyze  | -> |  cluster  | -> |   scout   | -> |  synthesize | -> |  submit_plan  |
  +-----------+    +-----------+    +-----------+    +-------------+    +---------------+
        |                |                |                 |                   |
   request facts    owner ledger     scout notes       draft lanes        submit_plan(...)
        |                |                |                 |                   |
    exit: evidence   exit: one        exit: wave       exit: ref loaded   exit: one tool
    vs production    owner family     returned or      + lanes pass       call, no prose
    split            per row          skip justified   checklist
```

| # | Stage | Input | Exit gate | Reference |
| --- | --- | --- | --- | --- |
| 1 | analyze | User request | Request facts split from test/benchmark evidence and production clues. | none |
| 2 | cluster | Analyze output | Every owner row carries one owner family + changelog axes. | none |
| 3 | scout | Cluster ledger | Notes harvested for every scouted production path, or the row is marked unresolved. | none |
| 4 | synthesize | Scout findings | Reference loaded; no new scouts or note reads; lanes drafted and checklist passes. | `load_skill_reference(skill_name="team-root-planner-playbook", reference_name="synthesize-and-submit")` — load before drafting any lane. |
| 5 | submit_plan | Drafted lanes | Exactly one `submit_plan({ "new_tasks": [...] })` call, no later tool calls or prose. | none |

## 1. Analyze

Enter from the user request. Do not cluster, scout, or load references yet.

```text
Caption: split the request into evidence and production clues.

request
  |-- commands / benchmark ids / failing tests -> evidence (spec only)
  |-- exact production file or symbol ---------> production clue (exact)
  |-- broad family / matrix / migration -------> production clue (broad)
  `-- guessed or test-derived owner -----------> production clue (guess)
```

| Check | Action |
| --- | --- |
| Test/benchmark evidence | Keep paths and failing-test labels out of owner-row names and `scope_paths`. Scouts may reference test paths when triangulating production owners; lanes (developer/validator) own production paths only. |
| Production clues | Tag each as exact file/symbol, broad family, or guess. |
| Agent ownership | No `developer` or `validator` lane owns or edits test files. Production-only `scope_paths`; tests live as evidence in `spec.detail` / `spec.acceptance_criteria`. |

**Exit:** request facts are split from test/benchmark evidence and production clues.

## 2. Cluster

Enter after Analyze. Build the owner ledger; do not scout yet.

```text
Caption: every owner row is a scout target until a scout returns. Pre-scout, all production claims are guesses from test names.

production clues
  |-- proven exact file or symbol ----> atomic owner row
  |-- mechanism / engine / format ----> mechanism owner row
  |-- package / subsystem ------------> directory owner row
  `-- guess / test-derived -----------> unresolved owner row
```

| Check | Root-planner action |
| --- | --- |
| Clustering axes | Make one row per owner family, then tag changelog axes (owner, mechanism, API, engine, format). |
| Multi-PR / changelog input | When the request bundles many PRs (e.g. a release changelog with multiple PR sections), cluster at the **PR or changelog-section axis** first. One row per PR cluster (or per tightly coupled PR group within the same subsystem) is the correct coarse grouping at root depth — do not pre-atomize across all PRs. |
| Coarse-grouping bias | Prefer fewer, broader rows that each become a `team_planner` lane over many thin rows. Granular owner rows are the child planner's job. |
| Cluster name | One row = one owner family or one coherent PR cluster. Slash/plus names that combine unrelated concerns signal unrelated owners; split now. |
| Mixed support rows | Entrypoints, config loaders, compatibility helpers, dataframe utilities, and storage formats are separate rows unless live evidence proves one tight pair. Three or more files are never one bundled row. |
| Benchmark evidence | Exact means explicit production path/symbol from user/notes or `ci_workspace_structure` on the parent dir. Before scouting or scoping a test-derived filename, verify it; if absent or replaced by a package directory, use the directory row. |

Routing stops at owner rows; unrelated concerns remain separate unless live evidence proves one tight producer-consumer pair. If several appear in one row, split it.

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

**Default at root depth: `directory_superficial`.** The scout's only job is to *confirm the owner directory/family exists* and to map the production paths inside it. Do not ask root-level scouts to find the edit seam — that is the child planner's job.

| Scout shape | Use when |
| --- | --- |
| Directory superficial (default) | Package, subsystem, engine matrix, PR cluster, or package-like import path; map files and relationships without deep leaf RCA. |
| Bundled superficial | Several paths in one owner family or exactly one tight pair; same parent directory, call chain, or "small row" status is not enough. Ask only for relationship map and handoff seams. |
| Trivial deep | Rare at root. Reserve for one proven exact file/symbol where the user or inherited evidence already names the owner; ask for line-level functions, likely edit seam, and concrete gaps. Guessed or test-derived filenames do not qualify. |
| Row wave | Independent families; issue one `run_subagent` per row in one wave. Never batch unrelated owner families, and split any 3+ path idea before dispatch. |
| Budget | Cap at one scout per coarse cluster row, one wave. If a row stays broad after its shallow scout, accept it and route to `team_planner`; do not re-scout from root. |

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
load_skill_reference(skill_name="team-root-planner-playbook", reference_name="synthesize-and-submit")
```

Synthesize from the exploration-note ledger, not the Stage-2 cluster ledger. Missing notes or guessed root causes stay unresolved and route to `team_planner`, not `developer`.

```text
Caption: root routing during synthesis.

note proves exact owner + edit seam -> developer
note maps relationship / unresolved gap -> team_planner
notes reveal shared dispatch file ----> collapse or chain deps
same-payload evidence ---------------> validator
```

| Draft check | Expected result |
| --- | --- |
| Coverage | Every note-backed owner family or PR cluster has a lane; unresolved gaps still get a lane (as `team_planner`). Stage-2 cluster rows map roughly 1:1 to lanes — coarse stays coarse. |
| Default route | At root depth, `team_planner` is the default lane type. Use `developer` only when a shallow scout already proved exact owner file + likely edit seam; otherwise hand the cluster to `team_planner` for recursive subdivision. |
| Note quality | Notes must name a production owner (file/directory/symbol). Test paths cited as triangulation hints are fine; if a note only points at tests with no production owner identified, route the gap to `team_planner`. |
| Developer lanes (rare at root) | Exactly one production owner file (or one tight coupled pair within one mechanism), proven by this turn's scout; ≥2 unrelated owner files in `scope_paths` force a `team_planner` lane instead — a call chain across unrelated owners is not "one mechanism". |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. Each `team_planner` lane carries one PR cluster / owner family with its scout-confirmed production directory in `scope_paths`. |
| Validators | Required when any producer lane writes a same-payload suite; depend on every such producer; `scope_paths` are production surfaces. |
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
| Tests in scope | None — tests stay in `spec`, never in `scope_paths`. |

**Exit:** one `submit_plan` tool call emitted; no summary, output, parent ids, trailing prose, or later tool calls.
