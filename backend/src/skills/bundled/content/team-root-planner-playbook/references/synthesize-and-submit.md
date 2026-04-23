# Root Planner Synthesize and Submit Reference

Lookup reference loaded on demand in Stage 3 when drafting a non-trivial `submit_plan(...)` payload. Decision-gating rules (Clustering, Lane Order, Pre-submit Checklist) live inline in the playbook's Stage 3 — this file covers the content-shaping rules, the tool contract, and case examples.

If a newly revealed production owner slice needs scouting before root routing, return to Scout before drafting. After the payload is ready, the final assistant action is the `submit_plan(...)` tool call.

## Synthesis Inputs

Start from the Stage 1 owner ledger plus Stage 2 scout notes and uncertainty. Produce a same-payload DAG with task ids, lane names, `deps`, `scope_paths`, and validator coverage. Every named failing cluster must have a repair/decomposition owner or be explicitly handed to a child `team_planner`; a terminal validator is never an owner for otherwise unassigned failures.

## Coverage and Evidence Rules

1. Build a coverage ledger for benchmark/fail-to-pass requests. Track every named failing cluster, variant, or command from the user request and scout notes.
2. Drop exact files disproved by live evidence. Use the nearest stable production boundary instead.
3. Treat any scout conclusion that names benchmark tests, skips, xfails, rewrites, pytest configuration, or benchmark harness edits as evidence only. Translate it into a production, dependency, environment, or uncertainty hypothesis.
4. Put benchmark tests and verification commands in `spec`, not `scope_paths`, unless tests are explicitly the owned surface.
5. Never write a developer goal or task details that instruct the child to edit, skip, xfail, rewrite, or reconfigure benchmark tests unless the original user request explicitly asks to repair tests rather than production behavior.
6. Add a `validator` only when a distinct same-layer verification owner is useful.
7. Make a terminal validator depend on every same-payload non-validator id it verifies, including child `team_planner` ids.

## Submission Rules

Build one `new_tasks` JSON list from the decided DAG.

1. Use repo-relative production `scope_paths` for every task, including validators.
2. Put owner evidence and sequencing in `2. Task Details:`. `Task Details` must name owner evidence, exact production scope, constraints, and dependency context.
3. Put concrete test-suite expectations in `3. Acceptance Criteria:`. `Acceptance Criteria` must be test-suite focused with concrete commands or pytest ids.
4. Use `deps` only for real output ordering or same-payload planner/validator ordering.
5. Ensure every `deps` entry resolves to another id in this same `new_tasks` list.
6. For a terminal validator, list every same-payload non-validator id it validates.
7. For fail-to-pass work, do not close a named target with skip, xfail, clear `ImportError`, missing optional dependency, or "not supported" prose.
8. Submit with top-level `new_tasks` only. Do not include summary, output, parent ids, or trailing prose.

OCC resolves concurrent edits to the same file. Overlapping sibling `scope_paths` are allowed; do not invent deps or merge lanes just to keep scopes disjoint.

## Terminal Tool Contract

The root has no graph to inherit. Every `deps` entry must resolve to another id in this `new_tasks` payload.

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

Field contract:

| Field | Contract |
| --- | --- |
| `id` | Unique lower-kebab id in this payload. Other tasks reference this exact string in `deps`. |
| `description` | Short non-blank owner/outcome label. |
| `name` | Exactly `developer`, `team_planner`, or `validator`. |
| `spec` | One string with `1. Goal:`, `2. Task Details:`, and `3. Acceptance Criteria:` in order. Each label starts its own line and has body text after the colon on that same line. |
| `deps` | List of ids from this same payload. Independent work uses `[]`. Validators must depend on at least one upstream same-payload task. |
| `scope_paths` | Non-empty list of repo-relative production paths owned or verified by the task. Use directories for broad planner or validator scopes. |

## Case Examples

### Case 1: Narrow Exact-Owner Fix

Use direct `developer` for one named production owner with no decomposition signal. Add a validator only when separate verification is useful.

```json
{
  "new_tasks": [
    {
      "id": "dev-replan-rewire",
      "description": "Fix replan dependency rewiring",
      "name": "developer",
      "spec": "1. Goal: Rewire pending downstream dependents through the spawned replanner after a worker failure.\n2. Task Details: Own backend/src/team/task_center.py. The user named the owner file and the work is one coherent TaskCenter behavior change. Preserve executor and DispatchQueue boundaries.\n3. Acceptance Criteria: Run uv run pytest backend/tests/team/test_replan_workflow.py -q; the suite proves pending dependents point at the replanner, non-pending dependents raise invariant failures, and all commands plus exit codes are reported.",
      "deps": [],
      "scope_paths": ["backend/src/team/task_center.py"]
    },
    {
      "id": "val-replan-rewire",
      "description": "Validate replan rewiring behavior",
      "name": "validator",
      "spec": "1. Goal: Verify the replan rewiring fix after implementation finishes.\n2. Task Details: Verify backend/src/team/task_center.py and dependency behavior from dev-replan-rewire. This validator depends on the only same-payload repair lane.\n3. Acceptance Criteria: Run uv run pytest backend/tests/team/test_replan_workflow.py -q and report pass/fail output with any remaining owner scope.",
      "deps": ["dev-replan-rewire"],
      "scope_paths": ["backend/src/team/task_center.py"]
    }
  ]
}
```

### Case 2: Broad Cluster With Child Planner

Use child `team_planner` for broad decomposition. Keep narrow leaf fixes as direct developers.

```json
{
  "new_tasks": [
    {
      "id": "dev-codeact-fallback",
      "description": "Fix codeact optional-dep fallback",
      "name": "developer",
      "spec": "1. Goal: Fix the optional-dependency fallback path so the codeact tool no longer raises under the benchmark.\n2. Task Details: Own backend/src/tools/daytona_toolkit/codeact_tool.py. This is a narrow leaf fix; benchmark ids are evidence and stay in this spec, not scope_paths.\n3. Acceptance Criteria: Run uv run pytest backend/tests/benchmarks/sweevo -q -k codeact; failures close through production fallback behavior, not ImportError, skip, or xfail.",
      "deps": [],
      "scope_paths": ["backend/src/tools/daytona_toolkit/codeact_tool.py"]
    },
    {
      "id": "plan-routing-and-submission",
      "description": "Decompose routing and submission benchmark failures",
      "name": "team_planner",
      "spec": "1. Goal: Decompose routing dtype failures and submission schema drift across their production owner families.\n2. Task Details: Own decomposition under backend/src/code_intelligence/routing and backend/src/tools/submission. Scout evidence shows multiple engines, dtypes, and schema versions, so this root plan must not flatten the work into sibling developer lanes.\n3. Acceptance Criteria: Child plan emits exact owner lanes, one child-layer validator, and coverage for uv run pytest backend/tests/benchmarks/sweevo -q -k 'routing or submission' plus focused unit tests named by child evidence.",
      "deps": [],
      "scope_paths": ["backend/src/code_intelligence/routing", "backend/src/tools/submission"]
    },
    {
      "id": "val-benchmark-cluster",
      "description": "Validate benchmark cluster closure",
      "name": "validator",
      "spec": "1. Goal: Verify direct and decomposed benchmark repair lanes.\n2. Task Details: Verify backend/src/tools/daytona_toolkit/codeact_tool.py, backend/src/code_intelligence/routing, and backend/src/tools/submission after both same-payload non-validator lanes finish.\n3. Acceptance Criteria: Run uv run pytest backend/tests/benchmarks/sweevo -q; all named clusters close through production fixes and no cluster is closed by skip, xfail, ImportError, or missing optional dependency.",
      "deps": ["dev-codeact-fallback", "plan-routing-and-submission"],
      "scope_paths": ["backend/src/tools/daytona_toolkit/codeact_tool.py", "backend/src/code_intelligence/routing", "backend/src/tools/submission"]
    }
  ]
}
```

### Case 3: Sequential Dependency

Use `deps` only when one task consumes another task's output. Do not use deps for scope hygiene.

```json
{
  "new_tasks": [
    {
      "id": "dev-prompt-helpers",
      "description": "Update prompt helper formatting",
      "name": "developer",
      "spec": "1. Goal: Update prompt helper formatting for the planner task detail contract.\n2. Task Details: Own backend/src/prompt/helpers.py. This lane produces helper wording consumed by runtime prompt rendering.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_prompts/test_prompt_helpers.py -q; formatting assertions pass and failures name the helper function.",
      "deps": [],
      "scope_paths": ["backend/src/prompt/helpers.py"]
    },
    {
      "id": "dev-runtime-prompt",
      "description": "Integrate helper output into runtime prompts",
      "name": "developer",
      "spec": "1. Goal: Integrate updated helper output into runtime prompt rendering.\n2. Task Details: Own backend/src/prompt/runtime_prompt.py. Depends on dev-prompt-helpers because this renderer consumes the helper wording from that lane.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_prompts/test_runtime_prompt.py -q and uv run pytest backend/tests/test_prompts -q; prompt rendering passes with the new helper contract.",
      "deps": ["dev-prompt-helpers"],
      "scope_paths": ["backend/src/prompt/runtime_prompt.py"]
    },
    {
      "id": "val-prompt-rollout",
      "description": "Validate prompt rollout",
      "name": "validator",
      "spec": "1. Goal: Verify helper and runtime prompt updates together.\n2. Task Details: Verify backend/src/prompt after dev-prompt-helpers and dev-runtime-prompt complete. This terminal validator depends on both same-payload non-validator ids.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_prompts -q; all prompt tests pass or failures identify the owning prompt path.",
      "deps": ["dev-prompt-helpers", "dev-runtime-prompt"],
      "scope_paths": ["backend/src/prompt"]
    }
  ]
}
```

### Case 4: Validator-Only Coverage Is Invalid

Do not put a named failing cluster only in a validator spec. Give it a production repair lane or hand it to a child `team_planner`.

Invalid shape:

```json
{
  "new_tasks": [
    {
      "id": "dev-codeact-fallback",
      "description": "Fix codeact optional-dep fallback",
      "name": "developer",
      "spec": "1. Goal: Fix the codeact fallback.\n2. Task Details: Own backend/src/tools/daytona_toolkit/codeact_tool.py.\n3. Acceptance Criteria: Run focused codeact tests.",
      "deps": [],
      "scope_paths": ["backend/src/tools/daytona_toolkit/codeact_tool.py"]
    },
    {
      "id": "val-full-benchmark",
      "description": "Validate all benchmark clusters",
      "name": "validator",
      "spec": "1. Goal: Verify codeact, routing, and submission clusters.\n2. Task Details: Routing and submission have no repair or decomposition owner in this payload, so this validator is covering unassigned failures.\n3. Acceptance Criteria: Run uv run pytest backend/tests/benchmarks/sweevo -q.",
      "deps": ["dev-codeact-fallback"],
      "scope_paths": ["backend/src/tools/daytona_toolkit/codeact_tool.py", "backend/src/code_intelligence/routing", "backend/src/tools/submission"]
    }
  ]
}
```

Valid replacement:

```json
{
  "new_tasks": [
    {
      "id": "dev-codeact-fallback",
      "description": "Fix codeact optional-dep fallback",
      "name": "developer",
      "spec": "1. Goal: Fix the codeact fallback.\n2. Task Details: Own backend/src/tools/daytona_toolkit/codeact_tool.py.\n3. Acceptance Criteria: Run focused codeact tests.",
      "deps": [],
      "scope_paths": ["backend/src/tools/daytona_toolkit/codeact_tool.py"]
    },
    {
      "id": "plan-routing-submission",
      "description": "Decompose routing and submission clusters",
      "name": "team_planner",
      "spec": "1. Goal: Decompose routing and submission benchmark clusters.\n2. Task Details: Own backend/src/code_intelligence/routing and backend/src/tools/submission because those named clusters need production repair owners below this root layer.\n3. Acceptance Criteria: Child plan assigns every routing and submission cluster to repair or validation lanes and names focused tests.",
      "deps": [],
      "scope_paths": ["backend/src/code_intelligence/routing", "backend/src/tools/submission"]
    },
    {
      "id": "val-full-benchmark",
      "description": "Validate all benchmark clusters",
      "name": "validator",
      "spec": "1. Goal: Verify codeact and decomposed benchmark clusters.\n2. Task Details: Verify codeact directly and routing/submission through plan-routing-submission. The validator depends on every same-payload non-validator id.\n3. Acceptance Criteria: Run uv run pytest backend/tests/benchmarks/sweevo -q; failures, if any, identify the remaining production owner.",
      "deps": ["dev-codeact-fallback", "plan-routing-submission"],
      "scope_paths": ["backend/src/tools/daytona_toolkit/codeact_tool.py", "backend/src/code_intelligence/routing", "backend/src/tools/submission"]
    }
  ]
}
```

### Case 5: Mixed Sequential + Parallel

Combine a sequential chain with independent parallel lanes in one payload. Chain only where a downstream lane consumes upstream output; let everything else fan out. Atomic developers and expandable team_planners can appear in either position.

```json
{
  "new_tasks": [
    {
      "id": "dev-schema-types",
      "description": "Update submission schema types",
      "name": "developer",
      "spec": "1. Goal: Add new schema field types consumed by the submission renderer.\n2. Task Details: Own backend/src/tools/submission/schema.py. Produces type aliases that dev-submission-renderer imports; this is real output consumption, not scope hygiene.\n3. Acceptance Criteria: Run uv run pytest backend/tests/tools/submission/test_schema.py -q; new type aliases round-trip.",
      "deps": [],
      "scope_paths": ["backend/src/tools/submission/schema.py"]
    },
    {
      "id": "dev-submission-renderer",
      "description": "Integrate new schema types into submission renderer",
      "name": "developer",
      "spec": "1. Goal: Use the new schema types in submission rendering.\n2. Task Details: Own backend/src/tools/submission/renderer.py. Depends on dev-schema-types because this renderer imports the aliases defined there.\n3. Acceptance Criteria: Run uv run pytest backend/tests/tools/submission/test_renderer.py -q; rendering tests pass under the new schema.",
      "deps": ["dev-schema-types"],
      "scope_paths": ["backend/src/tools/submission/renderer.py"]
    },
    {
      "id": "dev-codeact-fallback",
      "description": "Fix codeact optional-dep fallback",
      "name": "developer",
      "spec": "1. Goal: Fix the codeact optional-dep fallback so the tool no longer raises.\n2. Task Details: Own backend/src/tools/daytona_toolkit/codeact_tool.py. Independent of the submission chain; no output consumption between this lane and the schema/renderer pair, so it runs in parallel.\n3. Acceptance Criteria: Run uv run pytest backend/tests/benchmarks/sweevo -q -k codeact; failures close through production fallback behavior.",
      "deps": [],
      "scope_paths": ["backend/src/tools/daytona_toolkit/codeact_tool.py"]
    },
    {
      "id": "val-mixed-dag",
      "description": "Validate submission chain and codeact fix together",
      "name": "validator",
      "spec": "1. Goal: Verify the submission type->renderer chain and the codeact fallback after all three non-validator lanes finish.\n2. Task Details: Verify backend/src/tools/submission and backend/src/tools/daytona_toolkit/codeact_tool.py. Depends on every same-payload non-validator id.\n3. Acceptance Criteria: Run uv run pytest backend/tests/tools/submission -q and uv run pytest backend/tests/benchmarks/sweevo -q -k codeact; all clusters close through production code.",
      "deps": ["dev-schema-types", "dev-submission-renderer", "dev-codeact-fallback"],
      "scope_paths": ["backend/src/tools/submission", "backend/src/tools/daytona_toolkit/codeact_tool.py"]
    }
  ]
}
```

DAG shape: `dev-schema-types -> dev-submission-renderer` is a sequential chain; `dev-codeact-fallback` runs in parallel to both; all three funnel into `val-mixed-dag`.

The Pre-submit Checklist lives in the playbook's Stage 3. Walk it there before emitting `submit_plan(...)`.
