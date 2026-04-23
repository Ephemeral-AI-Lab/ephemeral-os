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
  -> [1. Analyze]
       classify intent, split evidence from production ownership,
       flag clustering, and build the owner ledger
  -> unresolved production owners?
       yes: [2. Scout] launch one scout per unresolved production family,
            join the wave, read notes, and carry missing notes as uncertainty
       no: continue
  -> [3. Synthesize and submit]
       load synthesize-and-submit, apply clustering + lane routing,
       draft tasks, run the checklist, and submit_plan
```

## Reference Map

Loadable reference used in Stage 3 via `load_skill_reference(skill_name="team-root-planner-playbook", reference_name="...")`:

- `synthesize-and-submit`: clustering and lane selection, coverage/evidence rules, `submit_plan` contract plus `NewTaskSpec` field table, valid and invalid payload examples, task-spec examples for `developer`, `team_planner`, and `validator`, and dependency DAG examples with rationale. Load in Stage 3 before drafting any `submit_plan(...)` payload.

## Workflow Details

### 1. Analyze

Build an owner ledger before routing. The root planner has no parent, deps, or Task Center graph context to load.

- Classify intent as bugfix, refactor, feature, migration, benchmark, or mixed.
- Raise a clustering flag when the request spans many failing tests, several production families, or an engine/dtype/format/API matrix under one broad subsystem.
- Split verification evidence from production ownership. Failing tests, benchmark ids, and verification commands go into child specs; concrete production files, directories, and symbols become owner slices.
- Use at most one targeted `ci_workspace_structure` or `ci_query_symbol` call to confirm a live package/file boundary or named symbol owner. Skip this when the user already named exact paths.
- Output `{ clear, unresolved, evidence }`, where every requested slice is classified as a clear production owner, unresolved production owner, or verification evidence.

Do not patch, validate, or read production files yourself. Do not guess owners from benchmark imports, filename similarity, or directory listings. Do not treat test edits, skips, xfails, or pytest reconfiguration as production ownership.

### 2. Scout

Skip this stage when the owner ledger has no unresolved production slices.

- Launch one scout per unresolved production owner family. Use `run_subagent(agent_name="scout", input={"target_paths": [...], "context": "..."})`.
- Keep `target_paths` production-only. Put tests, `test_*.py`, benchmark harnesses, verification paths, missing test-derived files, failing ids, skipped variants, optional-dependency errors, and verification commands in scout `context`.
- Fire every useful scout before polling. Use `check_background_progress(task_id="all")` and `wait_for_background_task(task_id="all")` until no scout is running.
- Cancel only a halted, blocked, off-scope, or twice-stale scout with `cancel_background_task(task_id=id)`, then carry that slice as explicit uncertainty.
- Read every available scout note with `read_file_note(file_path=...)` and forward notes plus uncertainty to Stage 3.

If any candidate target matches `*/tests/*`, `test_*.py`, a benchmark harness, or a verification-only path, do not launch a scout on it — move that path into scout `context` and keep `target_paths` production-only.

Scout only owner information that changes root routing. Do not scout to confirm exact files already named by the user.

### 3. Synthesize and submit

Load the synthesis reference before drafting:

```text
load_skill_reference(
  skill_name="team-root-planner-playbook",
  reference_name="synthesize-and-submit"
)
```

- Use the reference's clustering, lane selection, coverage/evidence, dependency DAG, and submission rules to route each slice to `developer`, `team_planner`, or `validator`.
- Draft each task with `id`, `description`, `name`, `deps`, `scope_paths`, and a `spec` containing `1. Goal:`, `2. Task Details:`, and `3. Acceptance Criteria:`.
- Return to Stage 2 if a new production owner slice must be known before routing. Otherwise, use at most one targeted CI call to tighten a boundary or prevent a bad scope.
- Run the reference's Final Checklist, then emit `submit_plan({ "new_tasks": [...] })` as the final assistant action. Submit top-level `new_tasks` only: no summary, output, parent ids, trailing prose, or later tool calls.

Every named failing cluster must be owned by a repair/decomposition task or handed to a child `team_planner`; a terminal validator is never the owner of an unassigned cluster. Do not route expandable multi-family work as a catch-all `developer`, decompose expandable work inline at the root, route narrow atomic work through `team_planner`, insert `deps` just to serialize independent work or keep scopes disjoint, preserve scout suggestions to edit/skip/xfail/rewrite/reconfigure tests unless the user asked for test repair, or include `scout` or `team_replanner` in `new_tasks`.
