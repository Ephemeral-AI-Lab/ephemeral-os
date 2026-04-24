---
name: team-replanner-playbook
description: Playbook for the team_replanner agent. Load recovery context, classify failure mode, diagnose only concrete blockers, and submit a schema-valid corrective replan with submit_replan(...).
---

# Team Replanner Playbook

Produce the smallest corrective DAG justified by the failed task evidence. Finish with exactly one `submit_replan(...)` call and make no later tool calls.

Replanner-created tasks use only `developer` repair lanes and `validator` verification lanes. The replanner owns recovery synthesis; it does not create planner, replanner, or scout tasks in `new_tasks`.

## Workflow Map

| Stage | Output |
| --- | --- |
| 1. Load recovery context | Failed-task evidence vs gaps, graph structure, sibling states. |
| 2. Classify failure mode | `Classification: <scope_expansion|wrong_owner_or_role|unresolved_blocker>` plus diagnostics decision when needed. |
| 3. Act | Corrective mapping, cancel-vs-add decision, matching action reference read. |
| 4. Submit | `terminal-contract` reference read, payload checked, one `submit_replan(...)`. |

```text
Caption: replanner recovery path. References support action and submit time.

[1 Load recovery context]
  |
  v
[2 Classify]
  | scope_expansion
  | wrong_owner_or_role
  | unresolved_blocker + trivial_direct_replan
  |--------------------------------------+
  |                                      v
  |                         [3 Act: direct replan]
  |
  | unresolved_blocker + deep_diagnostics
  v
[Diagnostics scouts / note harvest]
  |
  v
[3 Act]
  load action-add-tasks OR action-cancel-and-redraft
  |
  v
[4 Submit]
  load terminal-contract -> self-check -> submit_replan(...)
```

The intended path uses one action reference in Stage 3 and `terminal-contract` in Stage 4. Prefer reading references when the current stage has the evidence it needs.

## Workflow Details

### 1. Load recovery context

Use exact UUIDs from the replanning header.

1. Read own task, parent task, failed task, and each declared dependency with `read_task_details(task_id=...)`.
2. Wait for all required `read_task_details` results before calling `read_task_graph()`. Do not batch `read_task_graph()` with any required task-detail read.
3. Read sibling details only for siblings you may preserve, cancel, depend on, or avoid.
4. Extract verified failed-task evidence separately from unresolved gaps: final summary, failure reason, root-cause trace, failing command, exit code, snippet, trace path, production mechanism, and candidate fix location.

```text
Caption: preserve the failed evidence and the remaining gap separately.

failed task summary
  |-- verified: command, exit code, trace path, mechanism, fix location
  `-- unresolved: missing owner, missing rule, missing value mapping, unclear sibling
```

### 2. Classify failure mode

State exactly one classification line:

```text
Classification: <scope_expansion|wrong_owner_or_role|unresolved_blocker>
```

Use:

- `scope_expansion` when evidence proves the repair belongs outside the failed task's assigned production scope.
- `wrong_owner_or_role` when evidence proves a different owner or role must handle the repair.
- `unresolved_blocker` when a concrete production trace gap remains. If the fix target remains under any failed-task `scope_paths` entry, use `unresolved_blocker`.

For `unresolved_blocker`, add one diagnostics line:

```text
Diagnostics decision: trivial_direct_replan
```

or:

```text
Diagnostics decision: deep_diagnostics
```

Choose `trivial_direct_replan` only when file notes and CI already name every failing production seam. Choose `deep_diagnostics` when any seam is still unresolved.

Before `trivial_direct_replan`, check proposed one-line fixes against every observed value in the same failing assertion:

```text
Caption: value-rule sanity check.

input/state | observed | expected | proposed rule | proposed result | decision
------------+----------+----------+---------------+-----------------+---------
int64 path  | int64    | uint64   | astype(uint8) | uint8           | diagnostic
```

A failed task's "test design issue" label does not drop a named fail-to-pass variant.

### 3. Act

Enter this stage after classification is written and diagnostics are complete or explicitly skipped. Use the action reference matching the final cancellation decision:

```text
# Add-only recovery
load_skill_reference(
  skill_name="team-replanner-playbook",
  reference_name="action-add-tasks"
)

# Cancel stale sibling work and replace it
load_skill_reference(
  skill_name="team-replanner-playbook",
  reference_name="action-cancel-and-redraft"
)
```

```text
Caption: cancellation boundary.

same parent:
  failed task A (request_replan)  -> never cancel
  replanner R                    -> never cancel
  stale sibling S (non-terminal) -> may appear in cancel_ids
  terminal sibling T             -> preserve

Cancel only stale non-terminal direct siblings; cascade handles their descendants.
```

#### Direct replan

Use for `scope_expansion`, `wrong_owner_or_role`, and `unresolved_blocker` with `trivial_direct_replan`.

1. Preserve valid live siblings and downstream validators.
2. Drop test-edit, doc-only, benchmark-only, and value-table contradiction candidates.
3. Map every named failing variant to a repair/diagnostic task or an explicitly preserved live repair owner.
4. Decide add-only vs cancel-and-redraft, then use the matching action reference above.

The failed/original request_replan task can appear as a same-parent sibling in `read_task_graph()`; it is never stale sibling work and stays out of `cancel_ids`.

#### Diagnostics

Use for `unresolved_blocker` with `deep_diagnostics`.

```text
Caption: diagnostic scout fanout.

trace gap triplet:
  failing test/cluster + suspected production path + named symbol/seam
      -> scout(target_paths=["scoped production path"])
      -> read_file_note(file_paths=["scoped production path"])
      -> repair mapping
```

1. Read existing file notes for suspected production paths; skip scouting when notes already contain root-cause-grade evidence.
2. Enumerate distinct trace-gap triplets in visible reasoning before scout calls.
3. Launch one scout per remaining triplet with `run_subagent(agent_name="scout", input={"target_paths": ["<one or more scoped production paths for that one triplet>"], "context": "Diagnostic for <triplet>; ..."})`. Use multiple paths only when they belong to the same triplet and each path needs its own durable note. Keep failing tests in scout `context`, not `target_paths`.
4. Queue the scout wave before checking progress; then use `check_background_progress` / `wait_for_background_task`.
5. Harvest notes with `read_file_note(file_paths=[...])` for every path in every launched scout's `target_paths`. Missing notes create uncertainty for that path only.
6. Synthesize the repair mapping yourself from confirmed, partial, and disproved findings.
7. Decide add-only vs cancel-and-redraft, then use the matching action reference above.

### 4. Submit

Enter this stage after the matching Stage 3 action reference has shaped the corrective mapping. Use the terminal contract while checking the payload:

```text
load_skill_reference(
  skill_name="team-replanner-playbook",
  reference_name="terminal-contract"
)
```

Then self-check:

- Top-level keys are only `new_tasks` and `cancel_ids`.
- `new_tasks` is non-empty.
- Every `agent` is `developer` or `validator`.
- Every spec is a structured object with non-empty `goal`, `detail`, and `acceptance_criteria`.
- `cancel_ids` contains only stale non-terminal direct siblings.
- No `cancel_ids` entry equals the failed task id from the prompt.

Emit exactly one `submit_replan({ new_tasks, cancel_ids })` call. Make no further tool calls.
