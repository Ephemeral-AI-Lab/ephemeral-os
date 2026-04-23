---
name: team-root-planner-playbook
description: Playbook for the root_planner agent. Analyze the user request, scout missing production ownership, then synthesize and submit a schema-valid root plan with submit_plan(...).
---

# Team Root Planner Playbook

Produce the root task DAG from the user request. Finish with exactly one `submit_plan(...)` call.

The root planner routes top-down. Identify owner families, delegate broad or clustered decomposition to child `team_planner` lanes, and reserve direct `developer` lanes for narrow exact-owner work.

## Workflow Map

| Stage | Purpose | Output contract |
| --- | --- | --- |
| 1. Analyze | Classify the request and build an owner ledger. | Clear owners, unresolved owners, and verification evidence. |
| 2. Scout | Resolve unresolved production ownership only. | Scout notes or explicit uncertainty for each launched target. |
| 3. Synthesize and submit | Convert evidence into a schema-valid same-payload DAG and emit the terminal payload. | One `submit_plan({ "new_tasks": [...] })` call and no later tools. |

Decision flow:

```text
User request
  |
  v
[1. Analyze]
  - classify intent
  - separate verification evidence from production ownership
  - flag any clustering signal
  - build owner ledger
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
                                      load synthesize-and-submit
                                                |
                                                v
                              apply clustering + lane routing
                                                |
                                                v
                                      draft and submit_plan
```

## Reference Map

Loadable reference used in Stage 3 via `load_skill_reference(skill_name="team-root-planner-playbook", reference_name="...")`:

- `synthesize-and-submit`: clustering and lane selection, coverage/evidence rules, `submit_plan` contract plus `NewTaskSpec` field table, valid and invalid payload examples, task-spec examples for `developer`, `team_planner`, and `validator`, and dependency DAG examples with rationale. Load in Stage 3 before drafting any `submit_plan(...)` payload.

## Workflow Details

### 1. Analyze

| Section | Contract |
| --- | --- |
| **Input** | User request. |
| **Output** | Owner ledger split into `{ clear, unresolved, evidence }`, plus any clustering signal. Every requested slice is classified as a clear owner, an unresolved owner, or verification evidence for `spec`. |
| **Forbidden** | Patching, validating, or reading production files yourself; guessing owners from benchmark imports, filename similarity, or directory listings; treating test-edit / skip / xfail / pytest-reconfiguration as owners. |

#### Steps

```text
[user request]
    |
    v
(1) Frame the request                       -> reason only
    Classify intent (bugfix | refactor | feature | migration | benchmark | mixed)
    and raise a clustering flag when the request spans many failing tests,
    several production families, or an engine/dtype/format/API matrix under
    one broad subsystem.
    |
    v
(2) Partition evidence vs ownership         -> reason only
    Evidence = failing tests, benchmark ids, verification commands -> passed
    through `spec` to children. Owners = concrete production files, dirs,
    or symbols that must be edited.
    |
    v
(3) Resolve only what would be guessed      -> ci_workspace_structure | ci_query_symbol
    Run at most one targeted CI call to confirm a live package/file boundary
    or a named symbol owner. Skip entirely when the user already named paths.
    |
    v
(4) Write the owner ledger                  -> reason only
    Group into { clear production slices, unresolved production slices,
    verification evidence }. Unresolved slices drive Stage 2; an empty
    unresolved group routes straight to Stage 3.
```

The root planner has no parent, no deps, and no Task Center graph context to load. Use this stage only to decide what must be known before routing; lane decisions happen in Stage 3.

### 2. Scout

| Section | Contract |
| --- | --- |
| **Input** | Unresolved production slices from Stage 1. Skip the stage when the ledger has none. |
| **Output** | One scout note per launched target, explicit uncertainty carried forward for any canceled or missing note, and no active scouts remaining. |
| **Forbidden** | Scouting benchmark tests or verification targets; bundling unrelated owner families into one scout; canceling a healthy scout whose output would change root routing. |

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
    Poll and wait while any scout is `running`. Cancel only when a scout is
    halted, blocked, off-scope, or its peek buffer is unchanged across two
    checks -- carry that slice as explicit uncertainty.
    |
    v
(4) Harvest notes                           -> read_file_note(file_path=...)
    Read every available note and forward notes + uncertainty to Stage 3.
```

If any candidate target matches `*/tests/*`, `test_*.py`, a benchmark harness, or a verification-only path, do not launch a scout on it — move that path into scout `context` and keep `target_paths` production-only.

Launch scouts only for owner information that changes root routing. Do not scout to confirm exact files already named by the user.

### 3. Synthesize and submit

| Section | Contract |
| --- | --- |
| **Input** | Stage 1 owner ledger plus Stage 2 scout notes and uncertainty. |
| **Output** | Exactly one valid `submit_plan(...)` call, no later tool calls, and every named failing cluster owned by a repair/decomposition task or handed to a child `team_planner`. |
| **Forbidden** | Routing an expandable multi-family slice as a single `developer` (catch-all hiding); decomposing an expandable slice inline at the root instead of delegating it to a child `team_planner`; routing a narrow atomic slice through a `team_planner`; treating a terminal validator as the owner of an unassigned failing cluster; inserting `deps` to serialize otherwise-parallel work or to keep scopes disjoint; preserving scout suggestions to edit / skip / xfail / rewrite / reconfigure tests unless the user asked for test repair; including `scout` or `team_replanner` in `new_tasks`; any tool call after `submit_plan(...)`. |

#### Steps

```text
[owner ledger + scout notes + uncertainty]
    |
    v
(1) Load synthesis reference                -> load_skill_reference(
                                                  skill_name="team-root-planner-playbook",
                                                  reference_name="synthesize-and-submit")
    Use its Clustering, Lane Selection, and Dependency DAG rules to decide
    which slices go to developer, team_planner, or validator and how they
    connect. Merge user evidence, CI checks, and scout notes (with
    uncertainty) as you decide.
    |
    v
(2) Draft tasks                             -> reason only
    Draft each task with id, description, name, deps, scope_paths, and a
    `spec` structured as `1. Goal:`, `2. Task Details:` (owner evidence +
    constraints), `3. Acceptance Criteria:` (concrete verification). Follow
    the reference for the field contract, field-content rules, coverage/
    evidence rules, task-spec examples, and dependency DAG examples.
    |
    v
(3) Close routing gaps                      -> ci_workspace_structure | ci_query_symbol
                                              (or return to Stage 2)
    If a new production owner slice must be known before routing, return to
    Stage 2 before drafting. Otherwise use at most one targeted CI call to
    tighten a task boundary or prevent a bad scope.
    |
    v
(4) Run the Final Checklist, then emit
                                            -> submit_plan({ "new_tasks": [...] })
    Walk the checklist in the synthesize-and-submit reference. Submit with
    top-level `new_tasks` only -- no summary, output, parent ids, or trailing
    prose -- and make no further tool calls.
```
