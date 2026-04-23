---
name: team-planner-playbook
description: Playbook for the team_planner agent. Load inherited Task Center context, scout only missing production ownership, and submit a schema-valid child plan with submit_plan(...).
---

# Team Planner Playbook

Produce a child task DAG from inherited Task Center context. Route clear exact-owner work to `developer` lanes, push broad or clustered work down to another child `team_planner`, and reserve `validator` lanes for distinct same-layer verification. Finish with exactly one `submit_plan(...)` call.

## Workflow Map

| Stage | Purpose | Output contract |
| --- | --- | --- |
| 1. Load context | Consume inherited Task Center evidence and build an owner ledger for this layer. | Owner ledger split into inherited, unresolved, deps, and evidence groups. |
| 2. Scout | Resolve unresolved production ownership only. | Scout notes or explicit uncertainty for each launched target. |
| 3. Synthesize and submit | Convert inherited context and scout evidence into a schema-valid child DAG and emit the terminal payload. | One `submit_plan({ "new_tasks": [...] })` call and no later tools. |

Decision flow:

```text
[assigned planner task]
  -> [1. Load context: task details, graph topology, owner ledger]
  -> unresolved production owners?
     -> yes: [2. Scout production-only owner slices, then read notes]
     -> no: [3. Synthesize and submit]
  -> [3. load submit-child-plan, apply clustering + lane routing]
     -> newly-revealed distinct owner slice: back to [2. Scout]
     -> route complete: submit_plan({ "new_tasks": [...] })
```

## Reference Map

Loadable reference used in Stage 3 via `load_skill_reference(skill_name="team-planner-playbook", reference_name="...")`:

- `submit-child-plan`: synthesis and submission rules, `submit_plan` contract plus `NewTaskSpec` field table, valid and invalid payload examples, task-spec examples for `developer`, `team_planner`, and `validator`, dependency DAG examples with rationale, and the final checklist. Load in Stage 3 before drafting any `submit_plan(...)` payload.

## Workflow Details

### 1. Load context

| Step | Action |
| --- | --- |
| Read context | Call `read_task_details(task_id=...)` for own task, parent, and each dep UUID from the prompt header. |
| Inspect topology | Call `read_task_graph()` for dependency topology only; do not read sibling task details from graph output. |
| Classify intent | Mark bugfix, refactor, feature, migration, or mixed; raise a clustering flag for many failing tests, several production families, or a matrix under one broad subsystem. |
| Build owner ledger | Group inherited owner slices, unresolved owner slices, dependency outputs, and evidence to pass to children. |

Keep `2. Task Details:` wording intact when carrying parent or dependency context. The output of this stage is an owner ledger plus any clustering signal; unresolved slices drive Stage 2, and an empty unresolved group routes straight to Stage 3.

### 2. Scout

| Step | Action |
| --- | --- |
| Shape wave | Launch one scout per unresolved production owner family. Keep tests, `test_*.py`, benchmark harnesses, verification paths, missing test-derived files, skipped variants, optional-dependency errors, and verification commands in scout `context`, not `target_paths`. |
| Launch and supervise | Fire every useful scout before polling. Poll while scouts are `running`; cancel halted, blocked, off-scope, or unchanged scouts and carry that slice as explicit uncertainty. |
| Harvest notes | Read every available note for exact launched target paths. On cold CI, canceled scouts, or disproved exact files, fall back to the nearest stable production boundary. |

If any candidate target matches `*/tests/*`, `test_*.py`, a benchmark harness, or a verification-only path, do not launch a scout on it. Move that path into scout `context` and keep `target_paths` production-only.

### 3. Synthesize and submit

| Section | Contract |
| --- | --- |
| **Input** | Stage 1 owner ledger plus Stage 2 scout notes and uncertainty. |
| **Output** | Exactly one valid `submit_plan(...)` call and no later tool calls. Every named failing cluster is owned by a repair/decomposition task or handed to another child `team_planner`; a coverage ledger of every named failing cluster or variant is built before drafting, and a terminal validator is not an owner for otherwise unassigned failures; no named failing cluster may appear only in a validator spec. |
| **Forbidden** | Hiding multi-owner work in a catch-all developer; submitting a child `team_planner` together with its imagined child tasks; preserving scout recommendations to edit, skip, xfail, rewrite, or reconfigure tests unless the user asked for test repair; including `scout` or `team_replanner` in `new_tasks`; any tool call after `submit_plan(...)`. |

| Step | Action |
| --- | --- |
| Load synthesis reference | `load_skill_reference(skill_name="team-planner-playbook", reference_name="submit-child-plan")`. |
| Draft tasks | Use id, description, name, deps, scope_paths, and a `spec` with `1. Goal:`, `2. Task Details:`, and `3. Acceptance Criteria:`. |
| Route lanes | Use child `team_planner` lanes for broad, shared, unresolved, multi-family, clustered, or large benchmark/test-matrix work only when `grandchild_depth <= max_depth`; otherwise emit broader direct `developer` or `validator` tasks. |
| Close gaps | If a new distinct production owner slice must be known first, return to Stage 2. Use at most one targeted CI call to tighten a boundary or prevent a bad scope. |
| Submit | Walk the Final Checklist in the reference, then submit top-level `new_tasks` only: no summary, output, parent ids, trailing prose, or later tools. |

Put owner evidence, exact production scope, constraints, and dependency context inside each `Task Details` body so downstream workers inherit the routing you decided at this layer.
