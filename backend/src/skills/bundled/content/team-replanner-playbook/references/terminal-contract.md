# Replanner Terminal Contract

Load this reference before drafting any `submit_replan(...)` payload.

## Call Shape

```ts
submit_replan({ new_tasks: NewTaskSpec[], cancel_ids: string[] })
```

```ts
type NewTaskSpec = {
  id: string;
  description: string;
  name: "developer" | "validator";
  spec: string;
  deps: string[];
  scope_paths: string[];
};
```

Top-level input has only required `new_tasks` and required `cancel_ids`; include `cancel_ids: []` when no cancellation is needed. `new_tasks` must contain at least one corrective task; empty or cancel-only replans are rejected. New task objects have only `id`, `description`, `name`, `spec`, `deps`, and `scope_paths`.

Never include `output`, `summary`, `background`, `parent_id`, `new_sibling_tasks`, `new_children_tasks`, `expected_projection`, or prose outside the terminal call.

## Field Rules

| Field | Rule |
| --- | --- |
| `id` | Unique lower-kebab id in this payload. Local deps reference this exact string. |
| `description` | Short non-blank corrective outcome label. |
| `name` | Use only `developer` or terminal `validator`. Never use `team_planner`, `root_planner`, `scout`, `team_replanner`, or any other role. The replanner owns recovery synthesis and cannot delegate planning. |
| `spec` | Must contain `1. Goal:`, `2. Task Details:`, `3. Acceptance Criteria:` in order, each on its own line with body text after the colon. If `Task Details` uses `Classification: unresolved_blocker`, it must also include the exact field `Diagnostics decision: trivial_direct_replan` or `Diagnostics decision: deep_diagnostics`. |
| `deps` | Prefer local payload ids. Existing ids require fresh graph proof that they are schedulable and not downstream of this replanner or the failed task. Validators depend on local payload ids. |
| `scope_paths` | Non-empty repo-relative production paths. Verification-only tests stay in `spec`; a replanner must not invent test ownership from failing benchmark evidence. |

`cancel_ids` may include only stale non-terminal direct siblings of this replanner. Never include the failed task id, the original `request_replan` task, this replanner id, terminal tasks, or nested descendants. Same-parent graph position does not make the failed task cancellable. Compare every `cancel_ids` entry against the failed task id from the prompt before submission. Cancel the stale sibling root only; cascade handles descendants and dependents.

Same-owner-file repairs are not scope expansion. If the repair remains under any failed-task `scope_paths` entry, use `Classification: unresolved_blocker` with the appropriate diagnostics decision.

Replacement tasks may include a sibling's scope only when that sibling id appears in `cancel_ids`.

## Validator Guidance

Validator tasks are optional. Add one only when a distinct verification lane is useful and no preserved downstream validator already covers the repair surface. A validator must depend on at least one upstream local repair id; a terminal validator should cover the terminal repair leaves it verifies.

A validator is never a way to close a known red fail-to-pass command as environmental, non-fixable, unsupported, or residual risk. If no repair task exists yet, add a production diagnostic developer first; if the diagnostic still cannot identify a production repair, its terminal summary should preserve the unresolved trace for another replan.

## Spec Contents

`2. Task Details:` should name:

- failure classification
- diagnostics decision when classification is `unresolved_blocker`
- root cause mechanism or unresolved trace gap
- exact production scope
- sibling/cancel handling
- dependency context
- uncertainty and evidence source

`3. Acceptance Criteria:` should name concrete verification commands or pytest ids and require reporting command output, exit codes, changed behavior, and residual risk.

Acceptance criteria must not use `-k`, parametrization filters, or prose like "do not treat this as a repair target" to avoid a named failing fail-to-pass variant. If a command is narrowed for speed, another local task, preserved validator, or residual risk line must still own each omitted failing variant as production evidence.

Acceptance criteria must not be satisfied by documenting that a fail-to-pass command is expected to fail. A corrective developer should change or diagnose production behavior; a validator should verify a repair, not ratify a known red command.

Every named failing variant from the failed task summary must be represented in a repair or diagnostic task, or in an explicitly identified live repair owner whose task details or terminal summary covers that same variant and production seam. A preserved downstream validator may verify the repair, but it is not a substitute for repair ownership. Do not bury a named variant only in residual-risk text, "out of scope" text, unsupported/test-design prose, broad validator coverage, or a validator with no upstream repair.

If the failed task proposes a concrete rule or one-line fix, the replan must verify that rule against every observed expected/actual row from the same failing assertion. A rule that fixes one value while breaking another is not a direct repair; create a diagnostic developer to derive the correct production rule.

## Examples

### Direct Scope Expansion

```json
{
  "new_tasks": [
    {
      "id": "repair-config-path",
      "description": "Repair config loader path",
      "name": "developer",
      "spec": "1. Goal: Repair the config regression in the production loader path identified by the failed task.\n2. Task Details: Classification: scope_expansion. The failed task proved the original assigned file was not the source of the wrong value; the root cause mechanism is the config lookup branch in pkg/config.py. Own pkg/config.py, run ci_diagnostics(file_path=\"pkg/config.py\") first, preserve the named failing test evidence in the summary, and do not edit benchmark tests. Verification test paths appear in acceptance only; scope_paths stays on the production file.\n3. Acceptance Criteria: Run uv run pytest tests/test_config.py -q and the focused failing test id from the failed summary; report commands, exit codes, and whether the config lookup branch now matches the expected production behavior.",
      "deps": [],
      "scope_paths": ["pkg/config.py"]
    }
  ],
  "cancel_ids": []
}
```

### Cancel Stale Sibling

```json
{
  "new_tasks": [
    {
      "id": "repair-shared-auth-path",
      "description": "Repair shared auth path after stale sibling cancellation",
      "name": "developer",
      "spec": "1. Goal: Replace stale auth work with the production path proven by the failed task.\n2. Task Details: Classification: wrong_owner_or_role. Cancel sibling dev-auth-wrapper because it is non-terminal, shares this replanner's parent, and is still working from the invalid wrapper assumption. Own backend/src/auth/session.py; run ci_diagnostics(file_path=\"backend/src/auth/session.py\") first; keep cancelled sibling scope out of all uncancelled work.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_auth/test_session.py -q and report command output, exit codes, and any residual risk.",
      "deps": [],
      "scope_paths": ["backend/src/auth/session.py"]
    }
  ],
  "cancel_ids": ["dev-auth-wrapper"]
}
```

### Diagnostic Repair With Validator

```json
{
  "new_tasks": [
    {
      "id": "repair-index-state",
      "description": "Repair index state mutation",
      "name": "developer",
      "spec": "1. Goal: Repair the state mutation confirmed by diagnostic scouts.\n2. Task Details: Classification: unresolved_blocker. Diagnostics decision: deep_diagnostics. Scout notes for backend/src/index/state.py confirmed the failing cluster reaches the stale mutation path in apply_index_update. Own backend/src/index/state.py, run ci_diagnostics(file_path=\"backend/src/index/state.py\") first, and preserve the exact failing ids from the failed task summary.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_index/test_state.py -q and report commands, exit codes, changed behavior, and residual risk.",
      "deps": [],
      "scope_paths": ["backend/src/index/state.py"]
    },
    {
      "id": "val-index-recovery",
      "description": "Validate index recovery repairs",
      "name": "validator",
      "spec": "1. Goal: Verify the corrective index repair after diagnostic child work finishes.\n2. Task Details: Validate backend/src/index/state.py after repair-index-state. This is the terminal validator for the local replan payload; downstream validators already rewired to the replanner should not be duplicated.\n3. Acceptance Criteria: Run uv run pytest backend/tests/test_index -q; all pass or failures identify the owning repair scope with command, exit code, and failing assertion.",
      "deps": ["repair-index-state"],
      "scope_paths": ["backend/src/index/state.py"]
    }
  ],
  "cancel_ids": []
}
```

## Final Checklist

- Top-level input has only required `new_tasks` and required `cancel_ids`, with `cancel_ids: []` when no sibling should be cancelled.
- `new_tasks` contains at least one corrective task; if no task is justified yet, look deeper into the issues and come back with a concrete corrective task.
- Every task has only `id`, `description`, `name`, `spec`, `deps`, and `scope_paths`.
- Every `name` is exactly `developer` or `validator`.
- Every id is unique.
- Every local dep names another task in this payload.
- Existing deps, if used, are freshly proven schedulable and not downstream of this replanner or the failed task.
- Every task has non-empty repo-relative production `scope_paths`.
- Every spec uses `1. Goal:`, `2. Task Details:`, `3. Acceptance Criteria:`.
- Every spec with `Classification: unresolved_blocker` also includes `Diagnostics decision: trivial_direct_replan` or `Diagnostics decision: deep_diagnostics` inside `2. Task Details:`.
- No named fail-to-pass variant is dropped as a test design issue, unsupported parametrization, cross-engine mismatch, or "not a repair target".
- No named fail-to-pass variant appears only as residual risk, "out of scope", unsupported/test-design prose, broad validator coverage, or validator-only closure without an upstream repair.
- No proposed one-line rule contradicts another observed value in the same failing assertion.
- No task has documentation-only or validation-only acceptance criteria for a known red fail-to-pass command.
- `cancel_ids` contains only stale non-terminal direct siblings.
- No `cancel_ids` entry equals the failed task id from the prompt, even if that task appears as a same-parent sibling in `read_task_graph()`.
- No benchmark tests, `*/tests/*`, `test_*.py`, benchmark harness files, pytest configuration, skip/xfail work, or verification rewrites are scoped unless the original user request explicitly asked to repair tests rather than production behavior.
- The final assistant action is the `submit_replan(...)` tool call, not prose.
