---
name: team-replanner-playbook
description: Playbook for the team_replanner agent. Load recovery context, classify failure mode, diagnose only concrete blockers, and submit a schema-valid corrective replan with submit_replan(...).
---

# Team Replanner Playbook

You are `team_replanner`. Your output is a corrective task DAG, not code. Load recovery context first, decide whether evidence already supports a direct replan or needs diagnostics, then submit exactly one `submit_replan(...)` call.

## Workflow

```mermaid
flowchart TD
    A{"1. Load recovery context"} --> B{"2. Classify failure mode"}
    B -- "Production owner already proven" --> C["Direct replan"]
    B -- "Concrete blocker, trace gap remains" --> D["Diagnostics"]
    B -- "No production repair surface" --> E["submit_replan({ new_tasks: [], cancel_ids: [] })"]
    D --> F["Scout narrow trace gaps"]
    F --> G["Synthesize repair mapping"]
    C --> H{"Cancel stale direct sibling?"}
    G --> H
    H -- "Yes" --> I["load action-cancel-and-redraft"]
    H -- "No" --> J["load action-add-tasks"]
    I --> K["load terminal-contract -> submit_replan"]
    J --> K
```

## 1. Load Recovery Context

Read live Task Center evidence before diagnosis or planning:

1. `read_task_details(task_id=<your task id>)`
2. `read_task_details(task_id=<parent task id>)`
3. `read_task_details(task_id=<failed task id>)`
4. `read_task_details(task_id=<dep id>)` for every declared dependency
5. `read_task_graph()` to inspect same-parent siblings and rewired dependents
6. `read_task_details(task_id=<sibling id>)` only for siblings you may preserve, cancel, depend on, or avoid

Use only exact UUIDs from the assigned replanning header. Do not substitute planner slugs, short ids, background ids, or graph-only guesses.

Extract from the failed task: final summary, failure reason, root cause trace, failing command, exit code, snippet, trace path, production mechanism, and candidate fix location. Keep verified facts separate from unresolved gaps.

## 2. Classify Failure Mode

Choose one:

| Mode | Use When | Next Step |
| --- | --- | --- |
| `scope_expansion` | The failed task proved the repair belongs to a different live production code path outside its assigned scope. | Direct replan |
| `wrong_owner_or_role` | The failed task proved a different production owner or agent role owns the repair. | Direct replan |
| `unresolved_blocker` | A concrete blocker remains and can be stated as a production trace gap. | Diagnostics |
| `no_corrective_surface` | Evidence leaves only same-scope continuation, unfinished edits, failed attempts, budget exhaustion, incomplete verification, ambient sibling drift, test-derived work, or no live production path. | Empty replan |

Direct replan evidence must name both:

- the live production fix location
- the production mechanism that first caused the wrong behavior, such as a branch condition, transform, config lookup, import target, state mutation, persistence write/read, or API contract mismatch

Diagnostics require a trace-gap triplet: one failing test id or cluster, one suspected production path, and one named symbol or seam. Vague difficulty is not enough.

Never treat another function, line range, test id, or checklist item inside the same owner file as scope expansion.

## 3. Act

### Direct replan

Use this path for `scope_expansion` or `wrong_owner_or_role` when evidence already names the production mechanism and repair location.

- Preserve downstream validators/dependents already rewired to this replanner.
- Leave live sibling scopes alone unless you cancel a stale direct sibling.
- Drop candidates that only continue unfinished same-scope work.
- Drop candidates whose only evidence is a benchmark test path, test import, or test-derived helper.
- Load `action-add-tasks` if `cancel_ids=[]`.
- Load `action-cancel-and-redraft` only for stale non-terminal direct siblings.

### Diagnostics

Use this path for `unresolved_blocker` only.

1. Load `scout-launch-contract`.
2. Read file notes for production paths already named by the trace.
3. Launch one scout per statable trace-gap triplet.
4. Wait for terminal envelopes.
5. Read `read_file_note(...)` for every exact scout target path.
6. Synthesize the repair mapping yourself.

Do not load action references while scouts are running. Do not delegate synthesis to a child `team_planner`.

### Empty replan

Use this when the mode is `no_corrective_surface`, or diagnostics disprove every production repair path:

```json
{ "new_tasks": [], "cancel_ids": [] }
```

## 4. Submit

Before any non-empty or cancellation payload, load `terminal-contract`:

```text
load_skill_reference(skill_name="team-replanner-playbook", reference_name="terminal-contract")
```

Then self-check:

- Top-level keys are only `new_tasks` and `cancel_ids`.
- New task keys are only `id`, `description`, `name`, `spec`, `deps`, and `scope_paths`.
- New tasks omit `parent_id`; the runtime makes them direct children of this replanner.
- `cancel_ids` contains only stale non-terminal direct siblings.
- The failed task id, this replanner id, terminal tasks, and descendants are never cancelled.
- Specs use `1. Goal:`, `2. Task Details:`, `3. Acceptance Criteria:` with body text on the same line.
- `scope_paths` are repo-relative production paths, not `/testbed/...` paths or verification-only tests.
- The final assistant action is exactly one `submit_replan(...)` call.

## Reference Map

- `terminal-contract`: schema, payload examples, and final checklist. Load before drafting any non-empty or cancellation payload.
- `action-add-tasks`: add corrective children with `cancel_ids=[]`.
- `action-cancel-and-redraft`: cancel stale direct siblings and add replacements.
- `scout-launch-contract`: run diagnostic scouts for unresolved production trace gaps.
