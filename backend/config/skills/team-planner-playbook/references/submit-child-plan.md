# Team Planner Submit Plan Reference

Load this reference in the synthesize stage before drafting; do not use it to decide whether to scout.

## Routing Flow

```text
Caption: planner routes exploration-note facts, not Stage-2 rows.

note ledger
  |-- exact owner + edit seam ------------------> developer
  |-- relationship map / gap + depth ----------> team_planner
  |-- unresolved at max depth ------------------> per-mechanism split
  `-- same-layer verification after producers --> validator
```

| Slice signal | Route |
| --- | --- |
| Verified notes name one owner file/symbol plus likely edit seam | `developer` |
| Notes only map relationships, package boundaries, or unresolved ownership and depth remains | `team_planner` |
| Notes rely on test paths, test labels, or benchmark ids as proof | Keep production facts only; route the unresolved gap to `team_planner` while depth remains. |
| Notes reveal several mechanisms, APIs, engines, formats, or public entry points | Split by note-backed seams; do not mirror Stage-2 clusters. |
| Broad or unresolved at max depth | Direct per-mechanism `developer` tasks plus validation/diagnostic wording |
| Same-layer evidence sweep after producers finish | `validator` with producer deps |

## Level Shape

| Situation | Action |
| --- | --- |
| Crowded level | Group by changelog/mechanism row. |
| One broad `developer` | Make it expandable (`team_planner`) while depth remains. |
| Missing note, package row, or unresolved owner | `team_planner` while depth remains; do not reopen scouting in synthesize. |
| Many tiny variants under one mechanism | One task or one expandable task, not many thin siblings. |

## DAG Patterns

```text
Caption: parallel producers with one validator join.

task-center-repair (developer) ------\
submission-policy-plan (team_planner) -> child-output-validator (validator)
```

```text
Caption: sequential child-planner output dependency.

contract-planning (team_planner) -> bridge-planning (team_planner) -> bridge-validator (validator)
```

```text
Caption: exact work runs beside expandable work.

focused-rewire-repair (developer) -------------\
prompt-runtime-planning (team_planner) ---------> child-output-validator (validator)
```

| Dependency check | Rule |
| --- | --- |
| Output dependency | Add an edge only when one task consumes another task's output. |
| Validator coverage | A validator depends on every producer it verifies. |
| Parent/dependency UUIDs | Keep inherited UUIDs in `spec.detail`, not in `deps`. |
| Max-depth fallback | Replace planner nodes with focused tasks; keep uncertainty explicit. |

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
| `spec.detail` | Owner evidence, inherited context, scope, uncertainty, and dependency context. |
| `spec.acceptance_criteria` | Commands, pytest ids, expected evidence, and no skip/xfail closure. |
| `deps` | Same-payload ids only; prompt UUIDs are context. |
| `scope_paths` | Repo-relative production paths proven by context/scouts; tests stay in `spec`. |

## Final Checklist

| # | Check |
| --- | --- |
| 1 | Every task has `id`, `agent`, `spec`, `deps`, and `scope_paths`. |
| 2 | `deps` resolve within this payload and express output order or validator coverage. |
| 3 | Expandable slices use `team_planner` while depth remains. |
| 4 | At max depth, split broad work by mechanism instead of one catch-all lane. |
| 5 | Tests stay in `spec`; unresolved, contaminated-note, or missing-note rows use production directories in `scope_paths`. |
| 6 | The final assistant action is `submit_plan(...)` with no trailing prose. |
