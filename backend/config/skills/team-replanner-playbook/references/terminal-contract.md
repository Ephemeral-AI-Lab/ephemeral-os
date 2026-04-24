# Replanner Terminal Contract

Use while drafting and checking the final `submit_replan(...)` payload.

## Call Shape

```ts
submit_replan({ new_tasks: NewTaskDefinition[], cancel_ids: string[] })
```

```ts
type NewTaskDefinition = {
  id: string;
  agent: "developer" | "validator";
  spec: {
    goal: string;
    detail: string;
    acceptance_criteria: string;
  };
  deps: string[];
  scope_paths: string[];
};
```

Top-level input has only `new_tasks` and `cancel_ids`; use `cancel_ids: []` when no sibling should be cancelled. `new_tasks` is non-empty.

## Field Rules

| Field | Rule |
| --- | --- |
| `id` | Unique lower-kebab id in this payload. |
| `agent` | Only `developer` or `validator`. |
| `spec` | Non-empty `goal`, `detail`, and `acceptance_criteria`. |
| `deps` | Prefer local payload ids; existing ids require fresh graph proof that they are schedulable and not downstream of this replanner or the failed task. |
| `scope_paths` | Repo-relative production paths; tests and benchmark harnesses stay in `spec` unless the original request asked for test repair. |
| `cancel_ids` | Only stale non-terminal direct siblings; never failed task, original request-replan task, this replanner, terminal tasks, or nested descendants. |

`spec.detail` names classification, diagnostics decision for `unresolved_blocker`, root-cause mechanism or gap, production scope, sibling/cancel handling, dependency context, evidence, and uncertainty.

`spec.acceptance_criteria` names concrete commands or pytest ids and asks for command output, exit codes, changed behavior, and residual risk. Named fail-to-pass variants stay owned by a repair/diagnostic task or preserved live owner; validator-only closure is not enough.

## Compact Examples

```json
{
  "new_tasks": [
    {
      "id": "repair-config-path",
      "agent": "developer",
      "spec": {
        "goal": "Repair the config regression in the production loader path.",
        "detail": "Classification: scope_expansion. The failed task traced the root cause to pkg/config.py. Preserve named failing test evidence; test paths remain acceptance-only.",
        "acceptance_criteria": "Run uv run pytest tests/test_config.py -q and report commands, exit codes, changed behavior, and residual risk."
      },
      "deps": [],
      "scope_paths": ["pkg/config.py"]
    }
  ],
  "cancel_ids": []
}
```

## Final Checklist

| # | Check |
| --- | --- |
| 1 | Top-level input has only `new_tasks` and `cancel_ids`. |
| 2 | `new_tasks` contains at least one corrective task. |
| 3 | Every task has only `id`, `agent`, `spec`, `deps`, and `scope_paths`. |
| 4 | Every `agent` is `developer` or `validator`. |
| 5 | Local deps name another task in this payload; existing deps are freshly proven schedulable. |
| 6 | Every task has non-empty production `scope_paths`. |
| 7 | Every unresolved-blocker spec includes `Diagnostics decision: trivial_direct_replan` or `Diagnostics decision: deep_diagnostics`. |
| 8 | Named fail-to-pass variants are not dropped as unsupported, test design, residual risk, or validator-only coverage. |
| 9 | No task asks for test/benchmark/pytest-config mutation unless the user requested test repair. |
| 10 | `cancel_ids` contains only stale non-terminal direct siblings and never the failed task id. |
| 11 | The final assistant action is the `submit_replan(...)` tool call, not prose. |
