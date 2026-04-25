# Root Planner Submit Plan Reference

Load this reference in the synthesize stage before drafting; do not use it to decide whether to scout.

## Routing Flow

```text
Caption: root planner routes exploration-note facts, not Stage-2 rows.

note ledger
  |-- exact owner + edit seam ---------------------> developer
  |-- relationship map / missing note / package ---> team_planner
  `-- same-payload verification after producers ---> validator
```

**Default at root depth: `team_planner`.** Recursive subdivision is the routing default. `developer` is the exception, used only when a shallow scout already proved an exact owner + edit seam at this level.

| Slice signal | Route |
| --- | --- |
| Verified shallow-scout notes name one owner file/symbol plus likely edit seam | `developer` (rare at root) |
| Notes confirm an owner directory / package / PR cluster but not the edit seam | `team_planner` (default) |
| Notes only map relationships, package boundaries, or unresolved ownership | `team_planner` |
| Notes only point at tests without naming a production owner | Route the unresolved gap to `team_planner`. |
| Multi-PR / changelog input — many owner clusters | One `team_planner` lane per PR cluster / subsystem family; do not flatten into root-level developer tasks. |
| Notes reveal several mechanisms, APIs, engines, formats, or public entry points | One `team_planner` lane per note-backed mechanism; deeper splits happen inside the child planner. |
| Root-level evidence sweep after producers finish | `validator` with producer deps |

## Level Shape

| Situation | Action |
| --- | --- |
| Crowded level | Group by changelog/mechanism row. |
| One broad `developer` | Make it expandable (`team_planner`) unless note evidence makes it atomic. |
| Missing note, package row, or unresolved owner | `team_planner`; do not reopen scouting in synthesize. |
| Many tiny variants under one mechanism | One task or one expandable task, not many thin siblings. |

## DAG Patterns

```text
Caption: parallel producers with one validator join.

api-serializer (developer) ----\
cli-renderer (team_planner) ----> same-payload-validator (validator)
```

```text
Caption: sequential output dependency.

compat-guard (developer) -> adapter-callsite (developer) -> adapter-validator (validator)
```

```text
Caption: exact work runs beside expandable work.

config-loader-fix (developer) ----------------\
storage-engine-planning (team_planner) --------> root-output-validator (validator)
```

| Dependency check | Rule |
| --- | --- |
| Output dependency | Add an edge only when one task consumes another task's output. |
| Validator coverage | A validator depends on every producer it verifies. |
| Related files | Shared directories alone do not create `deps`. |
| Uncertainty | Put uncertain ownership in `spec.detail`; do not assert root cause. |

## Payload Shape

```ts
submit_plan({ new_tasks: NewTaskDefinition[] })
```

```ts
type NewTaskDefinition = {
  id: string;
  agent: "developer" | "validator" | "team_planner";
  spec: {
    goal: string;
    detail: string;
    acceptance_criteria: string;
  };
  deps: string[];
  scope_paths: string[];
};
```

| Field | Contract |
| --- | --- |
| `id` | Unique lower-kebab id in this payload. |
| `agent` | `developer`, `team_planner`, or `validator`. |
| `spec.goal` | Concrete outcome expected from the task. |
| `spec.detail` | Owner evidence, scope, uncertainty, and dependency context. |
| `spec.acceptance_criteria` | Commands, pytest ids, expected evidence, and no skip/xfail closure. |
| `deps` | Same-payload ids only. |
| `scope_paths` | Repo-relative production paths or directories; tests stay in `spec`. |

## Final Checklist

| # | Check |
| --- | --- |
| 1 | Every task has `id`, `agent`, `spec`, `deps`, and `scope_paths`. |
| 2 | `deps` resolve within this payload and express output order or validator coverage. |
| 3 | Expandable or unresolved slices use `team_planner`. |
| 4 | Named failing clusters have a producer owner or expandable owner. |
| 5 | Tests stay in `spec`; unresolved, contaminated-note, or missing-note rows use production directories in `scope_paths`. |
| 6 | The final assistant action is `submit_plan(...)` with no trailing prose. |
