# Team Planner Submit Child Plan Reference

This reference supports Stage 3 synthesis. Load it with:

```text
load_skill_reference(
  skill_name="team-planner-playbook",
  reference_name="submit-child-plan"
)
```

It is most useful after task context is loaded, the owner ledger is written, and any useful scout wave has joined or been intentionally skipped. Loading it does not freeze the workflow: keep newly discovered uncertainty explicit, make only bounded routing checks, and delegate unresolved slices to another child planner when depth allows, or to a max-depth diagnostic/repair lane.

## Routing Flow

```text
Caption: child planner routing with depth.

owner slice
  |-- exact owner + one mechanism -----------------> developer
  |-- expandable + grandchild_depth <= max_depth --> team_planner
  |-- expandable + max depth reached -------------> broader developer/validator split
  `-- same-payload verification ------------------> validator
```

| Slice signal | Route |
| --- | --- |
| One owner, one mechanism, focused verification | `developer` |
| Broad, clustered, matrix-shaped, mixed, or unresolved and depth remains | child `team_planner` |
| Broad or unresolved at max depth | direct per-mechanism `developer` tasks plus verification/diagnostic wording |
| Same-layer evidence sweep | optional `validator` depending on the producers it checks |

## Atomic vs Expandable

```text
Caption: preserve hierarchy instead of flattening clusters.

single clear leaf -> developer
cluster           -> child planner when depth allows
max-depth cluster -> split by mechanism with uncertainty in detail
```

| Atomic enough for current-layer `developer` | Expandable path |
| --- | --- |
| Live or inherited evidence names one owner file, symbol, or tight production surface | Owner is guessed, inherited as a cluster, or still unresolved |
| One failure mechanism explains the slice | Multiple mechanisms, APIs, backends, formats, or public entry points |
| Verification is focused and coherent | Verification is a broad benchmark, migration, compatibility, or matrix sweep |
| Scope is small enough for one developer pass | Four or more independent leaf fixes |

When uncertain and depth remains, use `team_planner`. At max depth, keep the uncertainty visible in `spec.detail` and avoid one catch-all developer task.

## DAG Level Size

Each level should be easy to scan and schedule.

| Situation at this level | Action |
| --- | --- |
| Crowded with many siblings | Group by owner family or mechanism; delegate the cluster to a child `team_planner` when depth remains. |
| One broad `developer` task alone | Check whether it should be a `team_planner` instead. |
| Many tiny variants under one mechanism | One atomic task or one child planner — not many thin-wrapper siblings. |
| Unrelated owner families | Several siblings or child planners, grouped by boundary. |

## Coverage And Evidence

| Item | Rule |
| --- | --- |
| Inherited failing targets | Preserve concrete pytest ids, variants, and file-level commands verbatim in `spec.detail` or `spec.acceptance_criteria`. |
| Tests and benchmark ids | Treat as evidence, not `scope_paths`, unless the user asked for test repair. |
| Scout gaps | Missing notes, cold paths, and adjacent-owner hypotheses stay as uncertainty unless live scout evidence proves the path. |
| Fail-to-pass closure | Do not close named targets by skip, xfail, clear `ImportError`, missing optional dependency, or "not supported" prose. |
| Validators | Optional at this layer. If included as terminal verification, depend on every same-payload producer it verifies, including child planners. |

## Submission Shape

```ts
submit_plan({ new_tasks: NewTaskDefinition[] })
```

```ts
type TaskSpec = {
  goal: string;
  detail: string;
  acceptance_criteria: string;
};

type NewTaskDefinition = {
  id: string;
  agent: "developer" | "validator" | "team_planner";
  spec: TaskSpec;
  deps: string[];
  scope_paths: string[];
};
```

| Field | Contract |
| --- | --- |
| `id` | Unique lower-kebab id in this payload. |
| `agent` | `developer`, `team_planner`, or `validator`. |
| `spec.goal` | Concrete outcome expected from the task. |
| `spec.detail` | Owner evidence, inherited context, exact scope, constraints, and uncertainty. |
| `spec.acceptance_criteria` | Concrete verification commands or pytest ids and expected evidence. |
| `deps` | Same-payload ids only; parent/dependency UUIDs are context, not `deps` entries. |
| `scope_paths` | Repo-relative production paths proven by inherited context or scout evidence. |

Submit top-level `new_tasks` only. Do not include `summary`, `output`, `parent_id`, sandbox-absolute paths, or prose after the tool call.

## DAG And Dependency Patterns

Use separate cases to reason about child-plan DAG shape. The final payload's `deps` must match the chosen diagram, and parent/dependency UUIDs from task context must not be copied into `deps`.

### Sequential Case

```text
Caption: sequential child-planner output dependency.

seq-contract (team_planner) -> seq-consumer (team_planner)
```

Rationale: use a sequential edge when the second child task consumes concrete output from the first task, such as a narrowed contract, generated migration primitive, normalized schema, or established API behavior. The nodes may be `team_planner` tasks when depth remains and each slice still needs further decomposition.

Check before submitting:

| Check | Rule |
| --- | --- |
| Output dependency | The consumer cannot safely start until the producer finishes. |
| Edge direction | The dependent task lists the producer id in `deps`; the producer has no reverse edge. |
| Context ids | Parent task ids and inherited dependency UUIDs stay in `spec.detail`, not `deps`. |
| Depth | Use `team_planner` only while depth remains; at max depth split into direct repair/diagnostic lanes. |

### Parallel Case

```text
Caption: independent child tasks with no output dependency.

par-submission (team_planner)
par-prompt (team_planner)
```

Rationale: use parallel siblings when inherited evidence already separates owner families and neither task needs the other's output. This keeps the child level schedulable without inventing ordering.

Check before submitting:

| Check | Rule |
| --- | --- |
| Independence | Each task can start from inherited context and notes. |
| No hidden consumer | Neither task's acceptance criteria require the other task's changed behavior. |
| No fake ordering | Both tasks use `deps: []` unless a validator later joins them. |
| Boundary clarity | If paths are adjacent, state ownership boundaries in `spec.detail` instead of adding a dependency. |

### Mixed Case

```text
Caption: sequential and parallel child producers join into one validator.

mix-contract (team_planner) -> mix-bridge (team_planner) --\
                                                            -> mix-validator (validator)
mix-runtime (team_planner) --------------------------------/
```

Rationale: use a mixed shape when one lane has true sequence, another lane can progress independently, and a later validator needs both outputs. This captures required ordering without serializing unrelated child work.

Check before submitting:

| Check | Rule |
| --- | --- |
| Local sequence | Only the true consumer depends on the producer. |
| Parallel lane | Independent work keeps `deps: []` and starts immediately. |
| Join task | The validator depends on every same-payload producer it verifies. |
| Max-depth fallback | If depth is exhausted, replace planner nodes with focused direct tasks and keep uncertainty visible in `spec.detail`. |

Use `deps` only for same-payload output ordering or validator coverage. Do not add a dependency merely because two tasks touch related directories.

## Detailed Task Spec Payload Pattern

Show sophisticated `spec.goal`, `spec.detail`, and `spec.acceptance_criteria` only inside a complete `submit_plan(...)` payload.

```text
Caption: detailed child payload with a focused repair, child planning lane, and validator join.

focused-rewire-repair (developer) -----------\
                                               +--> child-output-validator (validator)
submission-policy-planning (team_planner) ----/
```

```ts
submit_plan({
  new_tasks: [
    {
      id: "focused-rewire-repair",
      agent: "developer",
      spec: {
        goal: "Repair the focused replan rewire invariant inherited from the parent task.",
        detail: "Own backend/src/team/task_center.py and adjacent team tests. Parent context and notes point to one mutation path: pending dependents are rewired at replan request time, but readiness must still be refreshed after the new edge is applied. Keep graph mutation policy inside TaskCenter and preserve inherited failing pytest ids verbatim.",
        acceptance_criteria: "Add or update a focused backend/tests/team test that fails before the fix and passes after it. Run uv run pytest backend/tests/team/test_replan_workflow.py -q. Report command exit code, changed scheduling behavior, and any remaining inherited failing ids."
      },
      deps: [],
      scope_paths: ["backend/src/team/task_center.py", "backend/tests/team"]
    },
    {
      id: "submission-policy-planning",
      agent: "team_planner",
      spec: {
        goal: "Plan the inherited submission policy work across schema, runtime policy, and prompt rendering.",
        detail: "Inherited evidence spans multiple owner families below backend/src/tools/submission and backend/src/prompt. Depth remains, so preserve hierarchy instead of flattening this into one broad developer task. Split child work by owner/mechanism, keep uncertain owner hypotheses explicit, and preserve inherited pytest ids and benchmark targets in the child plan.",
        acceptance_criteria: "The resulting child plan accounts for every inherited submission/prompt failure id or records why an id is out of scope. It assigns each cluster to an owner or diagnostic lane and does not count skip, xfail, missing optional dependency, or test rewrite closure as success."
      },
      deps: [],
      scope_paths: ["backend/src/tools/submission", "backend/src/prompt", "backend/tests"]
    },
    {
      id: "child-output-validator",
      agent: "validator",
      spec: {
        goal: "Verify the completed child-level producer lanes close the inherited behavior together.",
        detail: "Depends on every same-payload producer it verifies. Check focused-rewire-repair and submission-policy-planning output as one same-layer evidence sweep. If a producer leaves uncovered failures, report failing ids and route them to the owning lane instead of editing code.",
        acceptance_criteria: "Run uv run pytest backend/tests/team/test_replan_workflow.py -q and the focused commands reported by submission-policy-planning. Report command exit codes, failing ids, owner gaps, and whether closure relies on skipped, xfailed, or missing-dependency tests."
      },
      deps: ["focused-rewire-repair", "submission-policy-planning"],
      scope_paths: ["backend/src/team", "backend/src/tools/submission", "backend/src/prompt", "backend/tests"]
    }
  ]
})
```

## Final Checklist

| # | Check |
| --- | --- |
| 1 | Each task has `id`, `agent`, `spec`, `deps`, and `scope_paths`. |
| 2 | `deps` resolve within this payload. |
| 3 | Expandable slices use `team_planner` while depth remains. |
| 4 | At max depth, split broad work by mechanism instead of one catch-all lane. |
| 5 | Inherited test ids and benchmark targets stay verbatim in `spec`. |
| 6 | The final assistant action is `submit_plan(...)`. |
