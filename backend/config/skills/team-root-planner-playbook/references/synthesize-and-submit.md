# Root Planner Synthesize and Submit Reference

This reference supports Stage 3 synthesis. Load it with:


It is most useful after the owner ledger is complete and any useful scout wave has joined or been intentionally skipped. Loading it does not freeze the workflow: keep newly discovered uncertainty explicit, make only bounded routing checks, and route unresolved slices to a child `team_planner` or diagnostic task.

## Routing Flow

```text
Caption: root planner routing from owner-ledger rows.

owner slice
  |-- exact owner + one mechanism + bounded verification -> developer
  |-- broad, clustered, matrix-shaped, mixed, unresolved -> team_planner
  `-- same-payload verification after producers ---------> validator
```

| Slice signal | Route |
| --- | --- |
| One production owner, one clear mechanism, one coherent verification path | `developer` |
| Benchmark, fail-to-pass, migration, compatibility, multi-API, multi-family, or unresolved owner | `team_planner` |
| Root-level evidence sweep after producers finish | `validator` depending on the producers it checks |

Prefer top-down decomposition. The root should split boundaries and hand expandable regions to child planners; it does not need to discover every leaf fix.

## Atomic vs Expandable

```text
Caption: classification is a routing aid, not a proof burden.

clear leaf      -> developer
unclear cluster -> team_planner
too many leaves -> team_planner
```

| Atomic enough for `developer` | Expandable, route to `team_planner` |
| --- | --- |
| Live evidence names one owner file, symbol, or tight production surface | Owner is guessed, shortlisted, or only test-derived |
| One failure mechanism explains the slice | Several mechanisms, APIs, backends, formats, or public entry points |
| Verification is focused and coherent | Verification is a benchmark matrix, migration sweep, or broad suite |
| Scope is small enough for one developer pass | Four or more independent leaf fixes, even if each is small |

When unsure, route to `team_planner` and preserve the uncertainty in `spec.detail`.

## DAG Level Size

Each level should be easy to scan and schedule.

| Situation at this level | Action |
| --- | --- |
| Crowded with many siblings | Group by owner family or mechanism; delegate the cluster to a child `team_planner`. |
| One broad `developer` task alone | Check whether it should be a `team_planner` instead. |
| Many tiny variants under one mechanism | One atomic task or one child planner — not many thin-wrapper siblings. |
| Unrelated owner families | Several siblings or child planners, grouped by boundary. |

## Coverage And Evidence

| Item | Rule |
| --- | --- |
| Named failing clusters | Give each cluster a repair/decomposition owner, or explicitly hand it to a child planner. |
| Tests and benchmark ids | Keep them in `spec.detail` or `spec.acceptance_criteria`, not `scope_paths`, unless the user asked for test repair. |
| Scout gaps | Keep missing notes, cold paths, and disproved exact files as uncertainty; do not turn guesses into `scope_paths`. |
| Skip/xfail/import closure | Do not treat skipped, expected-failed, missing optional dependency, or clear `ImportError` outcomes as passing fail-to-pass closure. |
| Validator | Use one terminal validator when the root payload needs a same-layer join. It depends on every producer it verifies, including child planners. |

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
| `spec.detail` | Owner evidence, scope, constraints, uncertainty, and dependency context. |
| `spec.acceptance_criteria` | Concrete commands, pytest ids, expected evidence, and no skip/xfail closure. |
| `deps` | Same-payload ids only; use edges for real output ordering or validator coverage. |
| `scope_paths` | Repo-relative production paths or directories. |

Submit top-level `new_tasks` only. Do not include `summary`, `output`, `parent_id`, or prose after the tool call.

## DAG And Dependency Patterns

Use separate cases to reason about DAG shape. The final payload's `deps` must match the chosen diagram.

### Sequential Case

```text
Caption: sequential output dependency.

seq-contract (team_planner) -> seq-consumer (team_planner)
```

Rationale: use a sequential edge when the second task consumes concrete output from the first task, such as a repaired contract, generated artifact, migration primitive, or established API behavior. The nodes may be `team_planner` tasks when each slice still needs further decomposition; the edge is about output dependency, not developer-only routing.

Check before submitting:

| Check | Rule |
| --- | --- |
| Output dependency | The consumer cannot safely start until the producer finishes. |
| Edge direction | The dependent task lists the producer id in `deps`; the producer has no reverse edge. |
| Scope overlap | Shared directories alone are not enough to create the edge. |
| Acceptance criteria | The consumer's criteria mention the producer output it must verify against. |

### Parallel Case

```text
Caption: independent tasks with no output dependency.

par-api (team_planner)
par-runtime (team_planner)
```

Rationale: use parallel siblings when tasks have independent owners and neither task needs the other's output. This keeps the level schedulable without inventing ordering. Prefer `team_planner` for broad or unresolved slices so the root graph does not become a developer-only fan-out.

Check before submitting:

| Check | Rule |
| --- | --- |
| Independence | Each task can start immediately with the evidence already available. |
| No hidden consumer | Neither task's acceptance criteria require the other task's changed behavior. |
| No fake ordering | Both tasks use `deps: []` unless a validator later joins them. |
| Conflict risk | If they touch nearby files, mention boundaries in `spec.detail` instead of adding a dependency. |

### Mixed Case

```text
Caption: sequential and parallel producers join into one dependent task.

mix-contract (team_planner) -> mix-consumer (team_planner) --\
                                                               -> mix-join (validator)
mix-api (team_planner) ---------------------------------------/
```

Rationale: use a mixed shape when one lane has real sequence and another lane can progress independently, then a later task needs both outputs. This captures necessary ordering without serializing unrelated work. The producer nodes can be `team_planner` tasks, and the join can be a `validator` when it only checks producer output.

Check before submitting:

| Check | Rule |
| --- | --- |
| Local sequence | Only the true consumer depends on the producer. |
| Parallel lane | Independent work keeps `deps: []` and starts immediately. |
| Join task | The join task depends on every producer output it consumes. |
| Validator distinction | Use a validator join only for verification; use a developer join only when code changes must integrate outputs. |

Use `deps` only for same-payload output ordering or validator coverage. Do not add a dependency merely because two tasks touch related directories.

## Detailed Task Spec Payload Pattern

Show sophisticated `spec.goal`, `spec.detail`, and `spec.acceptance_criteria` only inside a complete `submit_plan(...)` payload.

```text
Caption: detailed task specs with focused producers and a validator join.

focused-invariant-repair (developer) ---------\
                                               +--> same-payload-validator (validator)
independent-api-planning (team_planner) -------/
```

```ts
submit_plan({
  new_tasks: [
    {
      id: "focused-invariant-repair",
      agent: "developer",
      spec: {
        goal: "Repair task graph readiness so rewired dependents become schedulable after a successful replan.",
        detail: "Own backend/src/team/task_center.py and adjacent tests. Evidence: the failed worker path rewires pending dependents to the replanner, but readiness is not refreshed for the new dependency edge. Keep mutation policy inside TaskCenter; executors should only report terminal submissions.",
        acceptance_criteria: "Add or update a focused backend/tests/team test that fails before the fix and passes after it. Run uv run pytest backend/tests/team/test_replan_workflow.py -q. Report the changed scheduling behavior and any remaining red tests by id."
      },
      deps: [],
      scope_paths: ["backend/src/team/task_center.py", "backend/tests/team"]
    },
    {
      id: "independent-api-planning",
      agent: "team_planner",
      spec: {
        goal: "Plan the independent team API serialization repair.",
        detail: "Own backend/src/server/team_api.py and adjacent API tests. Evidence: the failure is isolated to response serialization for team task status payloads and does not need output from focused-invariant-repair. Preserve existing API field names and avoid changing persistence models unless the failing assertion proves the model boundary is wrong.",
        acceptance_criteria: "The resulting plan assigns the serialization failure to focused owner tasks, includes the narrowest API verification command, and does not rely on skipped, xfailed, or missing-dependency tests for closure."
      },
      deps: [],
      scope_paths: ["backend/src/server/team_api.py", "backend/tests"]
    },
    {
      id: "same-payload-validator",
      agent: "validator",
      spec: {
        goal: "Verify the completed producer lanes close the requested behavior together.",
        detail: "Depends on every same-payload producer it verifies. Check the focused invariant repair and independent API planning output as one root-level evidence sweep. If a producer leaves uncovered failures, report the failing ids and route them to the owning lane instead of editing code.",
        acceptance_criteria: "Run uv run pytest backend/tests/team/test_replan_workflow.py -q and the focused API command reported by independent-api-planning. Report command exit codes, failing ids, and whether closure relies on skipped, xfailed, or missing-dependency tests."
      },
      deps: ["focused-invariant-repair", "independent-api-planning"],
      scope_paths: ["backend/src/server", "backend/src/team", "backend/tests"]
    }
  ]
})
```

## Final Checklist

| # | Check |
| --- | --- |
| 1 | Each task has `id`, `agent`, `spec`, `deps`, and `scope_paths`. |
| 2 | `deps` resolve within this payload. |
| 3 | Expandable or unresolved slices use `team_planner`, not a catch-all `developer`. |
| 4 | Named failing clusters have a producer owner or child planner. |
| 5 | Tests stay as evidence in `spec`; production paths stay in `scope_paths`. |
| 6 | The final assistant action is `submit_plan(...)`. |
