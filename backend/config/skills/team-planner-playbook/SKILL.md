---
name: team-planner-playbook
description: Playbook for the team_planner agent. Load inherited Task Center context, scout risk-bearing production ownership, and submit a schema-valid child plan with submit_plan(...).
---

# Team Planner Playbook

Produce a child task DAG from inherited Task Center context. Finish with exactly one `submit_plan(...)` call.

Planner lane routing:

- Exact, live-proven, single-owner work -> `developer`.
- Broad, clustered, matrix-shaped, unresolved, or large benchmark/test-matrix work -> child `team_planner` when `grandchild_depth <= max_depth`.
- Max-depth fallback -> broader direct `developer` or `validator` tasks, with uncertainty kept in `spec.detail`.
- Same-layer verification -> `validator`.

## Workflow Map

| Stage | Output |
| --- | --- |
| 1. Load context | Owner ledger split into inherited, `scout_required`, unresolved, deps, and evidence groups. |
| 2. Scout | Scout notes by scoped path, plus explicit uncertainty for missing notes. |
| 3. Synthesize and submit | `submit-child-plan` reference read, payload checked, one `submit_plan(...)`. |

```text
Caption: child planner stage machine. Read references only when entering synthesis.

[assigned planner task]
  |
  v
[1 Load context]
  | read own task, parent, deps, and graph topology
  | build owner ledger
  |
  | unresolved or benchmark-risk owner?
  |-- yes --> [2 Scout]
  |             | join scouts
  |             | read notes by scoped path
  |             v
  +----------- update owner ledger
  |
  v
[3 Synthesize]
  first tool in stage:
    load_skill_reference(
      skill_name="team-planner-playbook",
      reference_name="submit-child-plan"
    )
  then: draft -> checklist -> submit_plan(...)
```

## Workflow Details

### 1. Load context

| Step | Action |
| --- | --- |
| Read context | Call `read_task_details(task_id=...)` for own task, parent, and each dep UUID from the prompt header. |
| Inspect topology | Call `read_task_graph()` for dependency topology only; graph output is not a license to read every sibling. |
| Classify intent | Mark bugfix, refactor, feature, migration, benchmark, or mixed; raise a clustering flag for many failing tests, several production families, or a matrix under one subsystem. |
| Build owner ledger | Group inherited owner slices, `scout_required` slices, unresolved slices, dependency outputs, and verification evidence. |

```text
Caption: inherited context becomes routing rows.

parent/deps/scout notes
  |-- proven owner + coherent mechanism ------------> inherited
  |-- broad family / matrix / benchmark cluster ----> scout_required
  |-- missing or ambiguous owner -------------------> unresolved
  |-- upstream result needed by this layer ---------> deps
  `-- pytest ids / commands / repro details --------> evidence
```

For inherited benchmark, fail-to-pass, migration, or compatibility clusters, put each broad family, matrix family, or likely expandable first-pass owner in `scout_required`. For restructured packages with multiple plausible owner files, scout first instead of assigning sibling-file owners from test names, backend labels, or file-name affinity.

Keep inherited detail wording intact when passing parent or dependency context to child specs.

### 2. Scout

```text
Caption: one scout per owner-ledger row; notes are harvested per assigned path.

row: parquet owner family
  -> run_subagent(... target_paths=["pkg/io/parquet"])
  -> read_file_note(file_paths=["pkg/io/parquet"])

row: config owner family with two scoped paths
  -> run_subagent(... target_paths=["pkg/config", "pkg/options"])
  -> read_file_note(file_paths=["pkg/config", "pkg/options"])

Different rows stay in different scout calls.
```

| Step | Action |
| --- | --- |
| Shape wave | Launch one scout per `scout_required` or unresolved production owner family with `target_paths: ["<one or more scoped production paths for that one owner family>"]`. Multi-path scouts are valid only when every path belongs to the same owner-ledger row and should produce its own durable note. |
| Keep scope clean | Keep `target_paths` production-only. Put tests, `test_*.py`, benchmark harnesses, verification paths, missing test-derived files, skipped variants, optional-dependency errors, and verification commands in scout `context`. |
| Launch and supervise | Fire every useful scout before polling. Poll while scouts are `running`; cancel halted, blocked, off-scope, or unchanged scouts and carry that slice as explicit uncertainty. |
| Harvest notes | Call `read_file_note(file_paths=[...])` with every path in every launched scout's `target_paths`. Missing notes, cold CI, canceled scouts, or disproved exact files create uncertainty only for the affected path. |

If an adjacent owner is only a hypothesis, launch a separate scout for that path or carry it as uncertainty; do not ask one scout to inspect files outside its `target_paths`.

### 3. Synthesize and submit

Enter this stage only after context is loaded, the owner ledger is written, and scouts are either done or explicitly skipped. Read the synthesis reference here:

```text
load_skill_reference(
  skill_name="team-planner-playbook",
  reference_name="submit-child-plan"
)
```

After this reference is loaded, continue with drafting and submission only. If a new distinct owner slice would require exploration, carry it as uncertainty and route it to another child `team_planner` when depth allows, or to a max-depth diagnostic/repair lane.

```text
Caption: lane routing with depth.

expandable slice + grandchild_depth <= max_depth -> team_planner
expandable slice + grandchild_depth > max_depth  -> broader developer/validator
atomic exact-owner slice                         -> developer
same-layer verification                          -> validator with producer deps
```

| Step | Action |
| --- | --- |
| Draft tasks | Use id, agent, deps, scope_paths, and a structured `spec` with non-empty `goal`, `detail`, and `acceptance_criteria`. Choose each task's agent while drafting, cover every named failing cluster with a repair/decomposition owner or child `team_planner`, and preserve concrete pytest ids or test files verbatim in child specs. |
| Submit | Walk the reference Final Checklist, then submit top-level `new_tasks` only: no summary, output, parent ids, trailing prose, or later tools. |

Put owner evidence, exact production scope, constraints, and dependency context inside each `spec.detail`. Before submit, audit every `developer` task: it either passed every atomic test, or it is an explicit max-depth per-mechanism fallback from the reference.
