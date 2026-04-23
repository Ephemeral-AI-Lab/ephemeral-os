---
name: team-planner-playbook
description: Playbook for the team_planner agent. Load inherited Task Center context, scout only missing production ownership, and submit a schema-valid child plan with submit_plan(...).
---

# Team Planner Playbook

Produce a child task DAG from inherited Task Center context. Route clear exact-owner work to `developer` lanes, push broad or clustered work down to another child `team_planner`, and reserve `validator` lanes for distinct same-layer verification. Finish with exactly one `submit_plan(...)` call.

## Hierarchical Planning Principle

Team plans are hierarchical: each planner submits a local child DAG, and another child `team_planner` can continue exploration and decomposition below it. Explore only enough at your current layer to separate exact owner work from broad or unresolved regions. Do not try to fully decompose every descendant task in one payload.

Prefer another child `team_planner` when the remaining uncertainty is broad, shared across multiple owner families, or would require detailed implementation-level exploration beyond your assigned layer. Your job is top-down routing for this layer, not exhaustive single-layer discovery.

Clear owner names do not automatically mean direct developer lanes are best. For broad benchmark, migration, or compatibility requests with many failing tests, several production families, or a test matrix that naturally splits into subproblems, route broad families to another child `team_planner`. Reserve direct `developer` lanes for narrow exact-owner fixes with a small, coherent implementation surface including large benchmark/test-matrix work that must be decomposed below this layer.

Clustering-job checkpoint: treat benchmark, fail-to-pass, migration, compatibility, and broad upgrade requests as clustering jobs when they contain many failing tests, several production families, or multiple failure clusters under one broad subsystem. When the checkpoint triggers, include at least one child `team_planner` in this payload. That child planner owns the next cluster-level split and may create developer leaves below it. A clustering payload with four or more independent developer lanes and no child `team_planner` is invalid, even when scouts named plausible owners or files — stop and replace broad developer groups with child `team_planner` lanes before submitting, and never ship a flat all-developer fan-out for multi-cluster benchmark repair. Use child planners for production families that still contain multiple failing tests, engines, dtypes, formats, or API surfaces. Do not flatten those families into sibling developers at the current layer just because owner files are known. Do not collapse independent failure mechanisms into one developer lane because they share nearby files or verification commands; overlapping `scope_paths` are allowed, split by mechanism when the work is otherwise independent. Keep `developer` lanes only for small leaf fixes with a single narrow production surface, one coherent failure mechanism, and a coherent verification command.

## Workflow Map

| Stage | Purpose | Output contract |
| --- | --- | --- |
| 1. Load context | Consume inherited Task Center evidence and build an owner ledger for this layer. | Owner ledger split into inherited, unresolved, deps, and evidence groups. |
| 2. Scout | Resolve unresolved production ownership only. | Scout notes or explicit uncertainty for each launched target. |
| 3. Synthesize and submit | Convert inherited context and scout evidence into a schema-valid child DAG and emit the terminal payload. | One `submit_plan({ "new_tasks": [...] })` call and no later tools. |

Decision flow:

```text
[assigned planner task]
  |
  v
[1. Load context]
  - read own, parent, and dep details
  - read_task_graph topology
  - classify intent
  - write owner ledger
  |
  +--> any unresolved production owners?
  |       |
  |       +-- yes --> [2. Scout]
  |       |             launch one scout per unresolved production slice
  |       |             join wave and read notes
  |       |             carry missing notes as uncertainty
  |       |             |
  |       |             v
  |       +----------> [3. Synthesize and submit]
  |
  +-- no -------------------------------------> [3. Synthesize and submit]
                                                |
                                                v
                                      load submit-child-plan
                                                |
                                                v
                              apply clustering + lane routing
                                                |
                                                v
                              newly-revealed distinct owner slice?
                                                |
                                  +-- yes --> back to [2. Scout]
                                                |
                                  +-- no --> draft and submit_plan
```

## Reference Map

Loadable reference used in Stage 3 via `load_skill_reference(skill_name="team-planner-playbook", reference_name="...")`:

- `submit-child-plan`: synthesis and submission rules, `submit_plan` contract plus `NewTaskSpec` field table, parallel-plus-terminal-validator and mixed-sequential-and-parallel payload examples, and the final checklist. Load in Stage 3 before drafting any `submit_plan(...)` payload.

## Workflow Details

### 1. Load task context

| Section | Contract |
| --- | --- |
| **Input** | The assigned planner task header with its own UUID, parent UUID, and dep UUIDs. |
| **Output** | Owner ledger split into `{ inherited, unresolved, deps, evidence }`, plus an intent classification (bugfix, refactor, feature, migration, or mixed) and any clustering signal. |
| **Forbidden** | Skipping context reads because the prompt prose seems sufficient; reading sibling task details from `read_task_graph` output; using UUIDs that were not printed in the assigned planner task header. |

#### Steps

```text
[assigned planner task header]
    |
    v
(1) Read own detail                         -> read_task_details(task_id=<own uuid>)
    Load the inherited spec, including `2. Task Details:` wording and any
    recent notes produced by the parent or predecessors.
    |
    v
(2) Read parent detail                      -> read_task_details(task_id=<parent uuid>)
    Capture parent plan intent, validator expectations, and constraints that
    bound this layer.
    |
    v
(3) Read each dep detail                    -> read_task_details(task_id=<dep uuid>)
    For every declared dependency, load the output shape this layer must
    respect before planning around it.
    |
    v
(4) Inspect topology                        -> read_task_graph()
    Inspect dependency topology only. Do not read sibling task details from
    graph output.
    |
    v
(5) Classify intent                         -> reason only
    Classify the assigned work as bugfix, refactor, feature, migration, or
    mixed, and raise a clustering flag when the slice spans many failing
    tests, several production families, or a test matrix under one broad
    subsystem.
    |
    v
(6) Write owner ledger                      -> reason only
    Group into { inherited owner slices, unresolved owner slices, dependency
    outputs to respect, evidence to pass to children }. Unresolved slices
    drive Stage 2; an empty unresolved group routes straight to Stage 3.
```

### 2. Scout

| Section | Contract |
| --- | --- |
| **Input** | Unresolved production slices from Stage 1. Skip the stage when the ledger has none. |
| **Output** | One scout note per launched target, explicit uncertainty carried forward for any canceled or missing note, and no active scouts remaining. |
| **Forbidden** | Scouting benchmark tests, verification targets, `*/tests/*`, `test_*.py`, unconfirmed test-derived paths, or missing test-derived paths when production owners exist; scouting an exact file after symbol/structure evidence disproves it and shows a live directory or nested owner for the same family; bundling unrelated exact files or the whole first-wave ledger into one scout; relaunching scouts to repair weak notes, prove cold files, or re-check usable owner boundaries. |

#### Steps

```text
[unresolved production slices]
    |
    v
(1) Shape the wave                          -> reason only
    One scout per unresolved owner family. Keep `target_paths` production-only
    -- tests, test_*.py, benchmark harnesses, verification paths, and missing
    test-derived files all move into `context`, alongside failing ids,
    skipped variants, optional-dependency errors, and verification commands.
    Use `ci_workspace_structure` or `ci_query_symbol` only to confirm a live
    package/file boundary or a named symbol owner before scouting.
    |
    v
(2) Launch the wave                         -> run_subagent(
                                                  agent_name="scout",
                                                  input={"target_paths": [...],
                                                         "context": "..."})
    Fire every useful scout before polling.
    |
    v
(3) Supervise until quiet                   -> check_background_progress(task_id="all")
                                              wait_for_background_task(task_id="all")
                                              cancel_background_task(task_id=id)
    Poll and wait while any scout is `running`. Any status other than
    `running` (`completed`, `failed`, `cancelled`, `delivered`) is terminal.
    Cancel only when a scout is halted, blocked, off-scope, or its peek
    buffer is unchanged across two consecutive checks -- carry that slice as
    explicit uncertainty.
    |
    v
(4) Harvest notes                           -> read_file_note(file_path=...)
    Read every available note for each exact launched target path and carry
    notes plus uncertainty to Stage 3. On cold CI, a canceled scout, or a
    disproved exact file, fall back to the nearest stable production
    boundary instead of preserving a guessed exact path.
```

If any candidate target matches `*/tests/*`, `test_*.py`, a benchmark harness, or a verification-only path, do not launch a scout on it — move that path into scout `context` and keep `target_paths` production-only.

### 3. Synthesize and submit

| Section | Contract |
| --- | --- |
| **Input** | Stage 1 owner ledger (inherited slices, unresolved slices, dep outputs, evidence) plus Stage 2 scout notes and uncertainty. |
| **Output** | Exactly one valid `submit_plan(...)` call and no later tool calls. Every named failing cluster is owned by a repair/decomposition task or handed to another child `team_planner`; a coverage ledger of every named failing cluster or variant is built before drafting, and a terminal validator is not an owner for otherwise unassigned failures; no named failing cluster may appear only in a validator spec. |
| **Forbidden** | Hiding multi-owner work in a catch-all developer; submitting a child `team_planner` together with its imagined child tasks; fully decomposing a broad region in this layer when another child `team_planner` can own top-down exploration below that region; preserving scout recommendations to edit / skip / xfail / rewrite / reconfigure tests unless the user asked for test repair; including `scout` or `team_replanner` in `new_tasks`; any tool call after `submit_plan(...)`. |

#### Steps

```text
[owner ledger + scout notes + uncertainty]
    |
    v
(1) Load synthesis reference                -> load_skill_reference(
                                                  skill_name="team-planner-playbook",
                                                  reference_name="submit-child-plan")
    Use its Synthesis Rules, Submission Rules, Terminal Tool Contract, payload
    examples, and Final Checklist to decide which slices go to developer,
    team_planner, or validator lanes and how they connect. Merge own task
    detail, parent plan, dependency summaries, CI/symbol checks, and scout
    notes (with uncertainty) into one owner ledger before drafting.
    |
    v
(2) Draft tasks                             -> reason only
    Draft each task with id, description, name, deps, scope_paths, and a
    `spec` structured as `1. Goal:`, `2. Task Details:` (owner evidence +
    constraints + dependency context), `3. Acceptance Criteria:` (concrete
    verification with commands or pytest ids). Use another child
    `team_planner` lane for broad, shared, unresolved, multi-family,
    clustered, or large benchmark/test-matrix work instead of forcing
    exhaustive current-layer exploration. Do not preserve test-edit
    recommendations in child specs.
    |
    v
(3) Close routing gaps                      -> ci_workspace_structure | ci_query_symbol
                                              (or return to Stage 2)
    If a new distinct production owner slice must be known before this
    layer can route work, return to Stage 2 before drafting; otherwise
    route the uncertainty to another child `team_planner`. Use at most one
    targeted CI call to tighten a task boundary or prevent a bad scope.
    |
    v
(4) Run the Final Checklist, then emit
                                            -> submit_plan({ "new_tasks": [...] })
    Walk the checklist in the submit-child-plan reference. Submit with
    top-level `new_tasks` only -- no summary, output, parent ids, or
    trailing prose -- and make no further tool calls.
```

Put owner evidence, exact production scope, constraints, and dependency context inside each `Task Details` body so downstream workers inherit the routing you decided at this layer.

Lane-selection reminder: Use another child `team_planner` lane for broad, shared, unresolved, multi-family, clustered, or large benchmark/test-matrix work instead of forcing exhaustive current-layer exploration.

Coverage guard: No named fail-to-pass cluster is covered only by a validator without a repair/decomposition owner.
