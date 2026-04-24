---
name: team-root-planner-playbook
description: Playbook for the root_planner agent. Analyze the user request, scout risk-bearing production ownership, then synthesize and submit a schema-valid root plan with submit_plan(...).
---

# Team Root Planner Playbook

Produce the top-level task DAG from the user request. Finish with exactly one `submit_plan(...)` call.

| Route | Use when |
| --- | --- |
| `developer` | Exact, live-proven owner plus one bounded mechanism. |
| `team_planner` | Broad, clustered, matrix-shaped, mixed, or unresolved owner boundary. |
| `validator` | Same-payload verification after producer lanes. |

## Stage Flow

```text
Caption: root planner stage machine. Each reference is loaded only at the stage that uses it.

user request
  |
  v
[1 Load context]
  | request evidence -> owner ledger
  |
  | scout would change this level's routing?
  |-- yes --> [2 Scout] -> harvest notes -> update ledger
  |-- no ---> carry uncertainty in child spec
  |
  v
[3 Synthesize]
  load synthesize-and-submit
  draft -> checklist -> submit_plan(...)
```

| Stage | Output |
| --- | --- |
| 1. Load context | Owner ledger: clear owners, scout candidates, unresolved clusters, verification evidence. |
| 2. Scout | Optional small scout wave, grouped by owner family. |
| 3. Synthesize | Top-level local DAG with `developer`, `team_planner`, and optional `validator` nodes. |

## 1. Load Context

```text
Caption: split evidence from ownership before making lanes.

request
  |-- commands / benchmark ids / failing tests -> evidence
  |-- exact production file or symbol ---------> clear owner
  |-- broad family / matrix / migration -------> scout candidate
  `-- guessed or test-derived owner -----------> unresolved
```

| Check | Root-planner action |
| --- | --- |
| Intent | Mark bugfix, refactor, feature, migration, benchmark, or mixed. |
| Clustering | Group many failures by owner family, mechanism, API, dtype, engine, or format. |
| Benchmark evidence | Keep tests and ids as verification evidence, not owner proof. |
| Boundary probe | Use at most one targeted CI structure/symbol query when it changes scout shape. |

Avoid implementation work in this stage. Preserve uncertain ownership in the child task instead of proving every leaf.

## 2. Scout

Use this stage only when live evidence changes this level's DAG.

```text
Caption: scout fan-out follows owner-ledger rows.

row: parquet family -> scout(["pkg/io/parquet"]) -> read_file_note(["pkg/io/parquet"])
row: CLI family     -> scout(["pkg/cli"])        -> read_file_note(["pkg/cli"])
row: config seam    -> scout(["pkg/config", "pkg/options"])
```

| Scout shape | Use when |
| --- | --- |
| Single path | One file or module is the likely owner. |
| Multi-path | Paths form one dependency, entrypoint, adapter, or shared mechanism. |
| Directory | Owner is a package/subsystem and exact files are unknown. |
| Separate scouts | Candidate owner families are independent. |
| No scout | Exploration becomes decomposition; route to `team_planner`. |

Keep scout `target_paths` as exact production coverage keys: one directory or a short file list, not a parent directory mixed with nested files or tests. Put tests, benchmark ids, optional-dependency signals, commands, and hypotheses in scout context. Launch the useful wave before polling, then read notes for every assigned path. Missing notes become uncertainty for that path only.

## 3. Synthesize

Enter after the ledger is complete and scouts are done or intentionally skipped. Load the Stage 3 reference only now:

```text
load_skill_reference(
  skill_name="team-root-planner-playbook",
  reference_name="synthesize-and-submit"
)
```

```text
Caption: root routing during synthesis.

atomic exact owner        -> developer
expandable boundary      -> team_planner
same-payload evidence    -> validator with deps=[verified producers]
```

| Draft check | Expected result |
| --- | --- |
| Coverage | Every named cluster has a producer owner or child `team_planner`. |
| Developer lanes | Exact owner and one mechanism; not a hidden broad cluster. |
| Planner lanes | Preserve uncertainty and evidence without leaf-level overexploration. |
| Validators | Depend on every same-payload producer they verify. |
| Payload | `id`, `agent`, `spec`, `deps`, and `scope_paths` only. |

Run the reference checklist, then make `submit_plan({ "new_tasks": [...] })` the final assistant action: no summary, output, parent ids, trailing prose, or later tool calls.
