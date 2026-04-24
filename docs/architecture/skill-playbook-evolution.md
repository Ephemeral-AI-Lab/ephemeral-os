# Skill Playbook Evolution Rules

This reference governs changes to bundled team playbooks, skill references, and
team-runtime tool guidance under `backend/config/skills`.

## Planning Shape

Team planning uses a tree of local DAGs. The root planner and child planners do
not need to fully explore every unresolved slice before submitting a plan.

```text
Caption: planners split boundaries, then delegate depth.

request slice
  |-- exact owner + bounded mechanism ----------> developer
  |-- broad / matrix / unresolved boundary -----> child team_planner
  |-- completed producer needs same-payload check -> validator
```

Planner and replanner exploration is for routing, not exhaustive discovery.
They may fan out scouts when live evidence would change the current layer's
task split.

| Situation | Preferred action |
| --- | --- |
| Atomic, live-proven owner | Assign a `developer` task. |
| Expandable or ambiguous owner | Assign a child `team_planner` when depth allows. |
| Broad failure matrix | Split by owner family or route to child planning. |
| Scout would change routing | Launch a small scout wave, usually 1-3 owner families. |
| Scout would only chase details | Preserve uncertainty in the child task spec. |

Avoid both extremes: one scout per failing test is too small-grained, and one
all-purpose scout is too broad.

## Replan Triggers

Developers and validators should file replan instead of stretching a lane when
the work no longer fits the assigned boundary.

```text
Caption: worker lanes either finish bounded work or return control to the graph.

bounded edit + fresh verification green -> submit_task_success(...)

blocker / budget exhaustion / wrong owner / broad scope
  -> request_replan(...)
```

| Trigger | Replan reason |
| --- | --- |
| Concrete blocker with no valid local route | `unresolved_blocker` |
| Required owner or role is different | `wrong_owner_or_role` |
| Repair becomes broad or ambiguous | `scope_expansion` |
| Budget is nearly exhausted before valid verification | `unresolved_blocker` or `scope_expansion`, whichever describes the remaining work. |
| Multiple outside-scope edits are required | `scope_expansion` |

A few lightweight outside-scope production writes, moves, deletes, or creates
are acceptable only when live evidence ties them to the same mechanism. The
third outside-scope mutation, a blocked move/delete, or any broad/ambiguous
outside-scope change should replan.

## Simplification Bias

Skill, playbook, and toolkit evolution should simplify the system by default.

| Change style | Rule |
| --- | --- |
| Net size | Prefer negative net change. Add text only when it removes ambiguity or repeated failures. |
| Format | Prefer diagrams and tables with captions over long prose. |
| Constraints | Use light constraints and decision gates; reserve hard rules for runtime invariants or safety. |
| Logic | Express workflows as stage flows that an LLM can follow without backtracking. |
| Tooling | Remove redundant tools or overlapping guidance before adding new surfaces. |

When tightening behavior, first ask whether the current instruction can be
shortened into a gate, table row, or diagram edge. Avoid duplicating a rule in a
playbook body and reference file.

## Reference Loading

References are stage-specific aids, not startup reading assignments. A playbook
may name a reference at the stage or step where it becomes useful, but agents
should not load references immediately after loading the main playbook.

```text
Caption: reference loading follows stage entry.

load_skill(...)
  |
  v
main workflow stages
  |
  +-- enter synthesis/action/submit stage
        |
        v
      load the matching reference for that stage
```

Reference guidance:

| Pattern | Use |
| --- | --- |
| Stage-local reference call | Preferred. Put the `load_skill_reference(...)` call inside the stage that needs it. |
| Reference map table | Avoid. It encourages early loading and duplicates workflow structure. |
| First-load reference instruction | Avoid unless the first stage cannot proceed safely without it. |
| Large reference | Split by stage/action so only the active step is loaded. |
| Optional reference | Phrase as available at that stage, not required before the workflow starts. |

Each reference should support one stage, action, or terminal contract. If a
reference applies to multiple unrelated steps, split it or move the shared rule
back into the concise playbook body.

## Review Checklist

Before merging a skill or playbook change:

| Check | Expected result |
| --- | --- |
| DAG split | Planner guidance separates atomic developer lanes from expandable child planning. |
| Scout scope | Scout fanout is bounded and owner-family based. |
| Replan path | Developer and validator blockers, budget exhaustion, and broad scope changes exit through `request_replan`. |
| Simplification | The diff removes more ambiguity than it adds, preferably with negative net text. |
| Reference timing | References are loaded at stage entry, not as a map at playbook load time. |
| Runtime contract | Terminal submission rules still match `submit_plan`, `submit_replan`, `submit_task_success`, and `request_replan`. |
