# Team Planner Submit Child Plan Reference

Load this reference in Stage 3 only, after task context is loaded, the owner ledger is complete, and useful scouts have joined or been skipped.

## Routing Flow

```text
Caption: child planner routes owner-ledger rows without exploring every leaf.

owner slice
  |-- exact owner + one mechanism + focused verification -> developer
  |-- expandable + grandchild_depth <= max_depth --------> team_planner
  |-- expandable + grandchild_depth > max_depth ---------> developer/validator split
  `-- same-layer verification after producers -----------> validator
```

| Slice signal | Route |
| --- | --- |
| Live or inherited evidence names one owner file, symbol, or tight production surface | `developer` |
| Several mechanisms, APIs, engines, formats, or public entry points and depth remains | child `team_planner` |
| Broad or unresolved at max depth | Direct per-mechanism `developer` tasks plus validation/diagnostic wording |
| Same-layer evidence sweep after producers finish | `validator` with producer deps |

## Level Shape

| Situation | Action |
| --- | --- |
| Crowded level | Group by owner family or mechanism. |
| One broad `developer` | Route to child `team_planner` while depth remains. |
| Many tiny variants under one mechanism | One task or one child planner, not many thin siblings. |
| Unrelated owner families | Several siblings or child planners, grouped by boundary. |

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
| Max-depth fallback | Replace planner nodes with focused direct tasks and preserve uncertainty. |

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
| `scope_paths` | Repo-relative production paths proven by inherited context or scout evidence. |

## Compact Example

```text
Caption: one atomic repair, one expandable child plan, one validator join.

focused-rewire-repair (developer) --------\
submission-policy-planning (team_planner) -> child-output-validator (validator)
```

```ts
submit_plan({
  new_tasks: [
    { id: "focused-rewire-repair", agent: "developer", spec: { goal: "...", detail: "...", acceptance_criteria: "..." }, deps: [], scope_paths: ["backend/src/team/task_center.py"] },
    { id: "submission-policy-planning", agent: "team_planner", spec: { goal: "...", detail: "...", acceptance_criteria: "..." }, deps: [], scope_paths: ["backend/src/tools/submission", "backend/src/prompt"] },
    { id: "child-output-validator", agent: "validator", spec: { goal: "...", detail: "...", acceptance_criteria: "..." }, deps: ["focused-rewire-repair", "submission-policy-planning"], scope_paths: ["backend/src/team", "backend/src/tools/submission", "backend/src/prompt"] }
  ]
})
```

## Final Checklist

| # | Check |
| --- | --- |
| 1 | Every task has `id`, `agent`, `spec`, `deps`, and `scope_paths`. |
| 2 | `deps` resolve within this payload and express output order or validator coverage. |
| 3 | Expandable slices use `team_planner` while depth remains. |
| 4 | At max depth, split broad work by mechanism instead of one catch-all lane. |
| 5 | Inherited test ids and benchmark targets stay verbatim in `spec`. |
| 6 | The final assistant action is `submit_plan(...)` with no trailing prose. |
