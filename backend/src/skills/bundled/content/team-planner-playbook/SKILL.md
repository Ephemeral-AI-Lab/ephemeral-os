---
name: team-planner-playbook
description: Playbook for the team_planner agent. Load inherited Task Center context, scout only missing production ownership, and submit a schema-valid child plan with submit_plan(...).
---

# Team Planner Playbook

Read the following sections to produce a child task DAG from inherited Task Center context, then finish with exactly one `submit_plan(...)` call.

## Hierarchical Planning Principle

Team plans are hierarchical: each planner submits a local child DAG, and another child `team_planner` can continue exploration and decomposition below it. Explore only enough at your current layer to separate exact owner work from broad or unresolved regions. Do not try to fully decompose every descendant task in one payload.

Prefer another child `team_planner` when the remaining uncertainty is broad, shared across multiple owner families, or would require detailed implementation-level exploration beyond your assigned layer. Your job is top-down routing for this layer, not exhaustive single-layer discovery.

Clear owner names do not automatically mean direct developer lanes are best. For broad benchmark, migration, or compatibility requests with many failing tests, several production families, or a test matrix that naturally splits into subproblems, route broad families to another child `team_planner` when depth allows. Reserve direct `developer` lanes for narrow exact-owner fixes with a small, coherent implementation surface.

Depth rules:

- Read the Planning depth section in your user prompt before deciding whether to create child planners.
- Tasks submitted in your plan run at `current_depth + 1`; a child `team_planner` needs another level below that to submit useful children.
- When `current_depth + 2 <= max_depth`, broad, clustered, or unresolved regions must be routed to child `team_planner` lanes.
- When `current_depth + 2 > max_depth`, do not create child `team_planner` lanes; emit direct `developer` and `validator` tasks with broader scopes instead.

Clustering-job checkpoint:

- Treat benchmark, fail-to-pass, migration, compatibility, and broad upgrade requests as clustering jobs when they contain many failing tests, several production families, or multiple failure clusters under one broad subsystem.
- When the checkpoint triggers and depth allows, include at least one child `team_planner` in this payload. That child planner owns the next cluster-level split and may create developer leaves below it.
- A clustering payload with four or more independent developer lanes and no child `team_planner` is invalid, even when scouts named plausible owners or files. Stop and replace broad developer groups with child `team_planner` lanes before submitting.
- Use child planners for production families that still contain multiple failing tests, engines, dtypes, formats, or API surfaces. Do not flatten those families into sibling developers at the current layer just because owner files are known.
- Do not collapse independent failure mechanisms into one developer lane because they share nearby files or verification commands. Overlapping `scope_paths` are allowed; split by mechanism when the work is otherwise independent.
- Keep `developer` lanes only for small leaf fixes with a single narrow production surface, one coherent failure mechanism, and a coherent verification command.

## Workflow

```mermaid
flowchart TD
    A{"1. Load task context"} --> B{"Owner boundaries clear?"}
    B -- "No" --> C["2. Launch scouts"]
    C --> D["check_background_progress -> wait_for_background_task -> read_file_note"]
    D --> E["3. Synthesize results"]
    B -- "Yes" --> E
    E --> F{"Another scout wave needed?"}
    F -- "Only for a new distinct owner slice" --> C
    F -- "No" --> G["4. Draft plan and submit"]
    G --> H["submit_plan({ new_tasks: [...] })"]
```

### 1. Load task context

Goal: consume inherited Task Center evidence before any exploration or planning.

Tools:

- `read_task_details(task_id="<header uuid>")` for your task, parent, and dependencies.
- `read_task_graph()` after required detail reads to inspect dependency topology.
- Reasoning for intent classification and owner-ledger setup.

Steps:

1. Use only the exact UUIDs printed in the assigned planner task header.
2. Call `read_task_details(task_id=<task id>)` for your inherited spec and recent notes.
3. Call `read_task_details(task_id=<parent task id>)` for the parent plan and validator expectations.
4. Call `read_task_details(task_id=<dep id>)` for every declared dependency before planning around that output.
5. Call `read_task_graph()` to inspect dependency topology; do not read sibling details from graph output.
6. Classify the assigned work as bugfix, refactor, feature, migration, or mixed.
7. Write the mental owner ledger as: inherited owner slices, unresolved owner slices, dependency outputs to respect, and benchmark evidence to pass to children.

Never:

- Skip context reads because the prompt prose seems sufficient.

Exit when: your own task, parent task, and every dependency detail has been read, and you can state which owner slices are clear or unresolved.

### 2. Launch scouts

Goal: explore only unresolved production ownership.

Tools:

- `ci_workspace_structure` only to confirm a live package/file boundary before scouting or scoping.
- `ci_query_symbol` only when a named symbol, class, function, or module needs an owner path.
- `run_subagent(agent_name="scout", input={"target_paths": [...], "context": "..."})`: launch one scout for one unresolved production owner slice.
- `check_background_progress(task_id="all")`: inspect scout status after the wave is queued, and again after timeouts or ambiguous state.
- `wait_for_background_task(task_id="all")`: join the scout wave when no foreground planning work remains.
- `cancel_background_task(task_id="...")`: cancel a scout only when progress shows it is halted, blocked, or no longer useful.
- `read_file_note(file_path="...")`: read the durable scout note for each exact launched target path after scouts finish.

Steps:

1. Scrub `target_paths`: each entry must be a live production file/directory unless tests are explicitly the owned surface. If any candidate target matches `*/tests/*`, `test_*.py`, a benchmark harness, or a verification-only path, do not launch; move that path into scout `context`.
2. Put benchmark tests, failing ids, missing test-derived paths, skipped variants, optional-dependency errors, and verification commands in scout `context`, not `target_paths`.
3. Launch all useful scouts for the wave before checking progress.
4. Call `check_background_progress(task_id="all")` after launch, then `wait_for_background_task(task_id="all")` to join. Any status other than `running` (`completed`, `failed`, `cancelled`, `delivered`) is terminal; loop progress/wait only while at least one scout is still `running`.
5. If `check_background_progress` shows a scout still `running` with an unchanged peek buffer across two consecutive checks, or off-scope wandering, call `cancel_background_task(task_id="<that bg id>")` and carry the missing evidence as uncertainty.
6. When scouts are terminal or canceled, call `read_file_note(file_path="...")` for every exact launched target path that produced a note.
7. On cold CI, a canceled scout, or a disproved exact file, fall back to the nearest stable production boundary instead of preserving a guessed exact path.

Never:

- Scout benchmark tests, verification targets, `*/tests/*`, `test_*.py`, unconfirmed test-derived paths, or missing test-derived paths when production owners exist.
- Scout an exact file after symbol/structure evidence disproves it and shows a live directory or nested owner for the same family.
- Bundle unrelated exact files or the whole first-wave ledger into one scout.
- Relaunch scouts to repair weak notes, prove cold files, or re-check usable owner boundaries.

Exit when: every scout in the wave is finished or canceled, every available target note is read, and residual uncertainty is ready to carry into task specs.

### 3. Synthesize results

Goal: turn inherited context and scout evidence into a child-layer DAG.

Tools:

- Use reasoning for DAG shape, dependency ordering, and validator coverage.
- Use targeted `read_file_note`, `ci_workspace_structure`, or `ci_query_symbol` only if it will change a task boundary or prevent a bad scope.

Steps:

1. Merge own task detail, parent plan, dependency summaries, CI/symbol checks, and scout notes into one owner ledger.
2. For benchmark/fail-to-pass work, build a coverage ledger of every named failing cluster or variant inherited from the parent, dependencies, and scout notes. Each entry must be owned by a repair/decomposition lane in this payload or explicitly handed to another child `team_planner`; a terminal validator is not an owner for otherwise unassigned failures.
3. Drop exact files disproved by live evidence; use the nearest stable production boundary when needed.
4. Treat any scout conclusion that names benchmark tests, skips, xfails, rewrites, pytest configuration, or benchmark harness edits as evidence only. Translate it into a production, dependency, environment, or uncertainty hypothesis before planning; do not preserve the test-edit recommendation in child specs.
5. Split exact owners into `developer` lanes only when each lane has one coherent failure mechanism; if several mechanisms remain under one broad assigned subsystem, route that subsystem to a child `team_planner` when depth allows.
6. Use another child `team_planner` lane for broad, shared, unresolved, multi-family, clustered, or large benchmark/test-matrix work instead of forcing exhaustive current-layer exploration.
7. Add `validator` lanes only when a distinct verification owner is useful.
8. When a validator is terminal, make it depend on every same-payload terminal non-validator id it validates, including child planner ids.
9. Add other `deps` only for real output ordering, known same-file edit ordering, or child `team_planner` sequencing when the id is in this same payload.
10. Launch another scout wave only for a newly revealed, distinct production owner slice that must be known before this layer can route work; otherwise route the uncertainty to another child `team_planner`.

Never:

- Hide multi-owner work in a catch-all developer.
- Submit a child `team_planner` together with its imagined child tasks.
- Fully decompose a broad region in this layer when another child `team_planner` can own top-down exploration below that region.

Exit when: either a new distinct production owner slice requires another scout wave, or the DAG is ready for submission.

### 4. Draft plan and submit

Goal: build the terminal payload and submit it.

Tools:

- Use `submit_plan({ "new_tasks": [...] })` exactly once.
- Use no other tool after the payload is ready.

Steps:

1. Build one `new_tasks` JSON list from the decided DAG.
2. Use repo-relative production `scope_paths` for every task, including validators; never submit `/testbed/...` paths.
3. Put benchmark tests and verification commands in `spec`, not `scope_paths`, unless tests are explicitly the owned surface.
4. Never write a developer goal or task details that instruct the child to edit, skip, xfail, rewrite, or reconfigure benchmark tests, benchmark harness files, or pytest configuration unless the original user request explicitly asks to repair tests rather than production behavior.
5. Put owner evidence, dependency context, and uncertainty in `2. Task Details:`.
6. Put concrete test-suite expectations in `3. Acceptance Criteria:`.
7. Use `deps` only for valid same-payload ids. Overlapping `scope_paths` between sibling tasks are allowed — the runtime uses OCC to resolve concurrent edits to the same file, so do not invent dependencies, narrow scopes, or merge developer lanes just to keep `scope_paths` disjoint.
8. For each terminal validator, compute the full set of same-payload non-validator ids it validates, including every `team_planner` id, and put that complete set in `deps`.
9. For fail-to-pass or benchmark work, acceptance criteria must not close a named target by saying it may be skipped, expected to fail, or produce a clear `ImportError`. Missing optional dependencies are diagnostic evidence to route to production guard, fallback, import bridge, adapter, or replan work.
10. For fail-to-pass or benchmark work, no named failing cluster may appear only in a validator spec. Give it a production repair/decomposition owner or hand it to another child `team_planner` as unresolved production evidence.
11. Check the Terminal Tool Contract below.
12. Submit with `new_tasks` only; the runtime generates the outcome summary after children terminate, so the payload must not carry a summary field or trailing prose.

Exit when: `submit_plan({ "new_tasks": [...] })` has been called exactly once.

## Terminal Tool Contract

Call:

```ts
submit_plan({ new_tasks: NewTaskSpec[] })
```

Task object:

```ts
type NewTaskSpec = {
  id: string;
  description: string;
  name: "developer" | "validator" | "team_planner";
  spec: string;
  deps: string[];
  scope_paths: string[];
};
```

`new_tasks` is a JSON list. Each element is one task object:

| Field | Meaning |
| --- | --- |
| `id` | Unique lower-kebab id in this payload (e.g. `dev-runtime-policy`). Other tasks reference this exact string in `deps`. |
| `description` | Short non-blank label naming the owner and outcome. Blank strings are rejected. |
| `name` | Use only `developer`, `team_planner`, or `validator`: `developer` for exact owner work, `team_planner` for decomposition, `validator` for a distinct verification lane. Never put `scout` or `team_replanner` in `new_tasks`; scouts run via `run_subagent(...)`, replanners are spawned reactively by the runtime. |
| `spec` | One string with three numbered colon labels in order, each on its own line with body continuing after the colon: `1. Goal:`, `2. Task Details:`, `3. Acceptance Criteria:`. `Task Details` must describe owner evidence, exact production scope, important constraints, and dependency context. `Acceptance Criteria` must be test-suite focused (named commands, focused pytest ids, broadened suites, and evidence expected in the final summary). Markdown headings, one-liners that cram every label together, and labels whose body starts on the next line are rejected. |
| `deps` | JSON list of task ids that must finish first. Each id must name another task in this same `new_tasks` payload. Independent work uses `[]`. Validators must depend on at least one upstream same-payload task. A terminal validator must list every same-payload non-validator id it validates, including `team_planner` ids whose descendants will run later. |
| `scope_paths` | Non-empty JSON list of repo-relative production paths the task owns or verifies. Use directories for broad planner/validator scopes. |

### Examples

#### Parallel + Terminal Validator

```json
{
  "new_tasks": [
    {
      "id": "dev-replan-rewire",
      "description": "Fix replan dependency rewiring",
      "name": "developer",
      "spec": "1. Goal: Rewire pending downstream dependents through the spawned replanner after a worker failure.\n2. Task Details: Own backend/src/team/task_center.py. Preserve executor and DispatchQueue boundaries, keep the original failed-task terminal path unchanged, and carry benchmark evidence from backend/tests/team/test_replan_workflow.py into the implementation summary.\n3. Acceptance Criteria: Run uv run pytest backend/tests/team/test_replan_workflow.py -q; the suite proves pending dependents point at the replanner, non-pending dependents raise invariant failures, and all commands plus exit codes are reported.",
      "deps": [],
      "scope_paths": ["backend/src/team/task_center.py"]
    },
    {
      "id": "plan-submission-policy",
      "description": "Decompose submission policy updates",
      "name": "team_planner",
      "spec": "1. Goal: Decompose submission policy work across schema, runtime policy, and prompts.\n2. Task Details: Own decomposition under backend/src/tools/submission, backend/src/team/runtime, and backend/src/prompt. Scout evidence shows multiple owner families; the child planner must preserve production-only scopes and avoid future child ids in this root payload.\n3. Acceptance Criteria: Child plan includes exact owner lanes, one child-layer validator, and test-suite coverage for uv run pytest backend/tests/test_engine backend/tests/team -q plus any focused prompt or submission-tool tests named by child evidence.",
      "deps": [],
      "scope_paths": ["backend/src/tools/submission", "backend/src/team/runtime", "backend/src/prompt"]
    },
    {
      "id": "dev-skill-registration",
      "description": "Update bundled skill registration",
      "name": "developer",
      "spec": "1. Goal: Keep bundled team playbook registration aligned with the root planner changes.\n2. Task Details: Own backend/src/skills and related registration surfaces. This lane is independent from the TaskCenter and submission-policy lanes, so it runs in parallel while still being covered by the terminal validator.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_team/test_builtin_agent_registration.py -q and uv run pytest backend/tests/test_skills/test_team_playbook_quality.py -q; both suites pass and registration failures include exact missing skill ids.",
      "deps": [],
      "scope_paths": ["backend/src/skills"]
    },
    {
      "id": "val-parallel-root-plan",
      "description": "Validate parallel root plan outputs",
      "name": "validator",
      "spec": "1. Goal: Verify all parallel implementation and decomposition outputs.\n2. Task Details: Verify backend/src/team/task_center.py, backend/src/tools/submission, backend/src/team/runtime, backend/src/prompt, and backend/src/skills after all parallel lanes finish. This terminal validator depends on every same-payload non-validator task.\n3. Acceptance Criteria: Run uv run pytest backend/tests/team/test_replan_workflow.py -q, uv run pytest backend/tests/test_engine backend/tests/team -q, uv run pytest backend/tests/test_team/test_builtin_agent_registration.py -q, and uv run pytest backend/tests/test_skills/test_team_playbook_quality.py -q; all suites pass or failures identify the owning scope.",
      "deps": ["dev-replan-rewire", "plan-submission-policy", "dev-skill-registration"],
      "scope_paths": ["backend/src/team/task_center.py", "backend/src/tools/submission", "backend/src/team/runtime", "backend/src/prompt", "backend/src/skills"]
    }
  ]
}
```

#### Mixed Sequential And Parallel

```json
{
  "new_tasks": [
    {
      "id": "dev-agent-runtime-state",
      "description": "Update agent runtime state",
      "name": "developer",
      "spec": "1. Goal: Update agent runtime state handling for the new planner contract.\n2. Task Details: Own backend/src/engine/runtime/agent.py. Runs in parallel with prompt-helper work; downstream prompt rendering waits on this output.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_engine/test_spawn_agent.py -q; all pass and the summary names the state fields changed.",
      "deps": [],
      "scope_paths": ["backend/src/engine/runtime/agent.py"]
    },
    {
      "id": "dev-prompt-helpers",
      "description": "Update prompt helper formatting",
      "name": "developer",
      "spec": "1. Goal: Update prompt helper formatting for the new task detail and acceptance criteria text.\n2. Task Details: Own backend/src/prompt/helpers.py and backend/src/prompt/__init__.py. Parallel with runtime state work; the final prompt renderer depends on both outputs.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_prompts/test_prompt_helpers.py -q; the suite passes and formatting snapshots reflect the current labels.",
      "deps": [],
      "scope_paths": ["backend/src/prompt/helpers.py", "backend/src/prompt/__init__.py"]
    },
    {
      "id": "dev-runtime-prompt",
      "description": "Update runtime prompt rendering",
      "name": "developer",
      "spec": "1. Goal: Integrate runtime state and prompt helper outputs into runtime prompt rendering.\n2. Task Details: Own backend/src/prompt/runtime_prompt.py. Depends on dev-agent-runtime-state and dev-prompt-helpers because this renderer consumes state and helper wording from those parallel lanes.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_prompts/test_runtime_prompt.py -q and uv run pytest backend/tests/test_prompts -q; both pass and failures include exact prompt sections.",
      "deps": ["dev-agent-runtime-state", "dev-prompt-helpers"],
      "scope_paths": ["backend/src/prompt/runtime_prompt.py"]
    },
    {
      "id": "val-mixed-rollout",
      "description": "Validate mixed rollout",
      "name": "validator",
      "spec": "1. Goal: Verify the parallel helper/runtime work and dependent prompt rendering.\n2. Task Details: Verify same-layer outputs from dev-agent-runtime-state, dev-prompt-helpers, and dev-runtime-prompt. Confirm the parallel starts and the dependent renderer used valid same-payload ids.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_engine/test_spawn_agent.py -q and uv run pytest backend/tests/test_prompts -q; all pass or failures are reported with command, exit code, and owning scope.",
      "deps": ["dev-agent-runtime-state", "dev-prompt-helpers", "dev-runtime-prompt"],
      "scope_paths": ["backend/src/engine/runtime/agent.py", "backend/src/prompt"]
    }
  ]
}
```

### Final checklist

- Top-level input has only `new_tasks`; any extra key is rejected.
- Every task has only the six allowed fields (`id`, `description`, `name`, `spec`, `deps`, `scope_paths`).
- Every id is unique; every `deps` string names another id in this same `new_tasks` payload.
- Validator tasks are optional; when present, each validator has upstream deps, and terminal validators cover the terminal non-validator leaves they validate.
- Every `name` is `developer`, `team_planner`, or `validator` — never `scout` or `team_replanner`.
- Every task has a non-blank `description` and non-empty production `scope_paths`.
- Every `spec` contains the three numbered colon labels in order (`1. Goal:`, `2. Task Details:`, `3. Acceptance Criteria:`), each on its own line with body after the colon on the same line.
- Every `Acceptance Criteria` is test-suite focused, with concrete commands or pytest ids and expected evidence.
- No fail-to-pass acceptance criterion treats skipped tests, expected failures, clear `ImportError`, or missing optional dependencies as passing closure for a named target.
- No named fail-to-pass cluster is covered only by a validator without a repair/decomposition owner.
- Any clustering job with available depth includes at least one child `team_planner`; do not submit a flat all-developer fan-out for multi-cluster benchmark repair.
- The final assistant action is the `submit_plan(...)` tool call, not prose.
