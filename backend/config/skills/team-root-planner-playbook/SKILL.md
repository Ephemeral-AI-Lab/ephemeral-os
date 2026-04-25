---
name: team-root-planner-playbook
description: Playbook for the root_planner agent. Analyze, cluster, scout owner rows, synthesize, then submit a schema-valid root DAG with submit_plan(...).
---

# Team Root Planner Playbook

Produce the top-level task DAG from the user request. Finish with exactly one `submit_plan(...)` call.

<Forbid Rule>
Never plan test suite or test-file related tasks.
Never assign subagents to explore test suites or test files.
</Forbid Rule>

| Route | Use when |
| --- | --- |
| `developer` | Atomic slice: exact owner + one mechanism + small failure surface. |
| `team_planner` | Broad, matrix, clustered, complex, or unresolved row. |
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
| 3 | scout | Cluster ledger | Scout wave returns, or every broad row has a documented "no scout" reason. | none |
| 4 | synthesize | Scout findings | Reference loaded, lanes drafted, draft checklist passes. | `load_skill_reference(skill_name="team-root-planner-playbook", reference_name="synthesize-and-submit")` — load before drafting any lane. |
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
| Test/benchmark ids | Keep in evidence; never copy into `scope_paths` or scout targets. |
| Production clues | Tag each as exact file/symbol, broad family, or guess. |
| Forbid | Never inspect, scout, or assign test paths. |

**Exit:** request facts are split from test/benchmark evidence and production clues.

## 2. Cluster

Enter after Analyze. Build the owner ledger; do not scout yet.

```text
Caption: every owner row is a scout target until a scout returns. Pre-scout, all production claims are guesses from test names.

production clues
  |-- exact file or symbol -----------> atomic owner row
  |-- mechanism / engine / format ----> mechanism owner row
  |-- package / subsystem ------------> directory owner row
  `-- guess / test-derived -----------> unresolved owner row
```

| Check | Root-planner action |
| --- | --- |
| Clustering axes | Group by changelog axes (owner, mechanism, API, engine, format). F2P/P2P ids are acceptance criteria, not grouping axes. |
| Cluster name | One cluster = one owner family. Multi-owner names like "CLI/Config/Compat" or "Storage I/O" are defects — split before scout. |
| Benchmark evidence | Keep tests and ids in evidence/spec, not workspace or scout targets. |

Routing stops at owner rows; HDF, JSON, parquet, groupby, utils, CLI, config, and compatibility are separate rows unless live evidence proves a tight producer-consumer pair.

**Exit:** every owner row has a single owner family and recorded changelog axes.

## 3. Scout

Enter after Cluster. One scout per row; unrelated rows go in one parallel wave.

```text
Caption: scout fan-out is proportional: one row per call, package maps for broad rows, no parent-dir batching.

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
| Row wave | Independent production families; separate scouts in one parallel wave, never one batched call. |
| Forbidden batch | Split `cli.py`+`config.py`+`compat.py`, HDF+JSON/parquet, groupby+utils, and HDF+parquet+groupby into separate row scouts. |

Dispatch each scout with `run_subagent(agent_name="scout", prompt="<scout prompt>")` — `prompt` is the only channel; production paths must be named inline. Production paths only; missing/disproved → directory scout or handoff. Never call workspace/scout tools on test paths and never name a test path in a scout prompt.

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
load_skill_reference(skill_name="team-root-planner-playbook", reference_name="synthesize-and-submit")
```

Synthesize scout findings into lanes; the DAG need not mirror clustering.

```text
Caption: root routing during synthesis.

atomic + small surface -> developer
broad / matrix cluster -> team_planner sibling
same-payload evidence  -> validator with production scopes
```

| Draft check | Expected result |
| --- | --- |
| Coverage | Every named cluster has a producer owner or sibling `team_planner`; tiny slices stay separate. |
| Developer lanes | Exactly one production owner file (or one tight coupled pair within one mechanism); ≥2 unrelated owner files in `scope_paths` (e.g. `cli.py`+`config.py`+`compat.py`, HDF+parquet+groupby) force a `team_planner` lane instead — a CLI→config→compat call chain is not "one mechanism". |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. |
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
