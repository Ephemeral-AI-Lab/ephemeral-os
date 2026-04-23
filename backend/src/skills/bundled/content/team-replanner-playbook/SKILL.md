---
name: team-replanner-playbook
description: Playbook for the team_replanner agent. Load recovery context, classify failure mode, diagnose only concrete blockers, and submit a schema-valid corrective replan with submit_replan(...).
---

# Team Replanner Playbook

Produce a corrective task DAG from the failed task's evidence. Finish with exactly one `submit_replan(...)` call.

Replanner-created tasks are limited to `developer` repair lanes and `validator` verification lanes. Do not create `team_planner`, `root_planner`, `team_replanner`, `scout`, or other agent roles in `new_tasks`; the replanner owns recovery synthesis itself.

## Workflow Map

| Stage | Purpose | Output contract |
| --- | --- | --- |
| 1. Load recovery context | Gather all live Task Center evidence before diagnosis. | Failed-task evidence vs gaps, graph structure, sibling states. |
| 2. Classify failure mode | Map evidence to one of three failure modes and a diagnostics decision. | One `Classification:` line plus, for `unresolved_blocker`, one `Diagnostics decision:` line. |
| 3. Act | Execute Direct replan or Diagnostics path based on classification. | Drafted corrective DAG with cancel-vs-add decision and action reference loaded. |
| 4. Submit | Load terminal-contract, self-check, emit replan. | Exactly one `submit_replan({ new_tasks, cancel_ids })` call and no later tools. |

Decision flow:

```text
[failed task evidence]
  |
  v
[1. Load recovery context]
  - read own, parent, failed-task, dep details
  - read_task_graph
  - read selective siblings
  - extract evidence vs gaps
  |
  v
[2. Classify Failure Mode]
  - map to scope_expansion | wrong_owner_or_role | unresolved_blocker
  - emit Classification line
  |
  +-- scope_expansion -----------------------> [3. Act: Direct replan]
  |                                                  |
  +-- wrong_owner_or_role ------------------> [3. Act: Direct replan]
  |                                                  |
  +-- unresolved_blocker                             |
        |                                            |
        +-- trivial_direct_replan -----------> [3. Act: Direct replan]
        |                                            |
        +-- deep_diagnostics -----> [3. Act: Diagnostics]
                                          |
                                          read file notes
                                          enumerate trace-gap triplets
                                          launch scouts (run_subagent)
                                          supervise + harvest notes
                                          Synthesize repair mapping
                                          |
                                          v
                               cancel or add decision
                                          |
  <---------------------------------------+
  [3. Act: Direct replan — choose action reference]
        |
        +-- cancel_ids=[] ----> load action-add-tasks
        |
        +-- stale sibling ----> load action-cancel-and-redraft
        |
        v
  [4. Submit]
        load terminal-contract
        self-check checklist
        submit_replan(...)
```

Every branch must load the matching action reference and then `terminal-contract` before drafting the payload. Do not skip those loads because the failure seems obvious.

## Reference Map

Loadable references used at specific stages below via `load_skill_reference(skill_name="team-replanner-playbook", reference_name="...")`:

- `terminal-contract`: schema, payload examples, and final checklist. Load in Stage 4 before drafting the payload.
- `action-add-tasks`: add corrective children with `cancel_ids=[]`. Load in Stage 3 when no sibling needs cancellation.
- `action-cancel-and-redraft`: cancel stale direct siblings and add replacements. Load in Stage 3 when a stale non-terminal direct sibling must be cancelled.

## Workflow Details

### 1. Load recovery context

| Section | Contract |
| --- | --- |
| **Input** | Assigned replanning header with exact UUIDs for own id, parent id, and failed-task id. |
| **Output** | Failed-task evidence vs gaps, graph structure, and sibling state sufficient for classification. |
| **Forbidden** | Reading siblings not relevant to preservation, cancel, or dependency; batching `read_task_graph()` with required detail reads; substituting graph slugs or short ids for exact UUIDs. |

#### Steps

```text
[replanning header UUIDs]
    |
    v
(1) Read own detail                         -> read_task_details(task_id=<your task id>)
    |
    v
(2) Read parent detail                      -> read_task_details(task_id=<parent task id>)
    |
    v
(3) Read failed-task detail                 -> read_task_details(task_id=<failed task id>)
    |
    v
(4) Read each dep detail                    -> read_task_details(task_id=<dep id>)
    One call per declared dependency.
    Skip any id already fetched above.
    |
    v
(5) Read task graph                         -> read_task_graph()
    Wait for all required `read_task_details` results before calling `read_task_graph()`.
    Do not batch `read_task_graph()` with any required task-detail read.
    |
    v
(6) Read sibling details only as needed     -> read_task_details(task_id=<sibling id>)
    Only for siblings you may preserve, cancel, depend on, or avoid.
    |
    v
(7) Extract failed-task evidence vs gaps    -> reason only
    From the failed task collect: final summary, failure reason, root cause trace,
    failing command, exit code, snippet, trace path, production mechanism, and
    candidate fix location. Keep verified facts separate from unresolved gaps.
```

### 2. Classify Failure Mode

| Section | Contract |
| --- | --- |
| **Input** | Failed-task evidence vs gaps from Stage 1. |
| **Output** | `Classification: <scope_expansion\|wrong_owner_or_role\|unresolved_blocker>` plus, for `unresolved_blocker`, `Diagnostics decision: trivial_direct_replan` or `Diagnostics decision: deep_diagnostics`. |
| **Forbidden** | Treating budget/tool-exhaustion as `scope_expansion`; treating documentation-only or test-edit as corrective; dropping a named variant into residual-risk prose; accepting stale `__pycache__` as root cause. |

#### Steps

```text
[failed-task evidence vs gaps]
    |
    v
(1) Map evidence to mode                    -> reason only
    |
    v
(2) Run the value-table check               -> reason only
    |
    v
(3) Verify in-scope vs cross-scope          -> reason only
    |
    v
(4) Emit classification line                -> reason only
```

Step detail — (1): `scope_expansion` when repair is proven to belong to a different live production path outside assigned scope. `wrong_owner_or_role` when repair is proven to need a different owner or role. `unresolved_blocker` when a concrete blocker remains and can be stated as a production trace gap. Budget exhaustion or unfinished implementation inside assigned scope is not `scope_expansion`.

Step detail — (2): When the failed summary proposes a concrete rule or one-line fix, check it against every observed value in the same failing assertion before calling it `trivial_direct_replan`. For merge/config/dispatch/state bugs, make a compact value table: input path/state, observed value, expected value, proposed rule. If the rule would break any listed value or contradicts the failed summary, treat the repair rule as unresolved — use `Diagnostics decision: deep_diagnostics` or create a diagnostic developer to derive the correct rule instead of copying the handoff into a repair task.

Step detail — (3): Never treat another function, line range, or checklist item in the same owner file as scope expansion. If the fix target remains under any failed-task `scope_paths` entry, classify as `unresolved_blocker`. A failed task's "test design issue" label does not drop a named fail-to-pass variant. Map every named variant to production repair evidence.

Step detail — (4): State one exact line: `Classification: <scope_expansion|wrong_owner_or_role|unresolved_blocker>`. For `unresolved_blocker`, also state `Diagnostics decision: trivial_direct_replan` when file notes and CI already name every failing seam, or `Diagnostics decision: deep_diagnostics` when any seam is still unresolved.

### 3. Act

| Section | Contract |
| --- | --- |
| **Input** | Classification line plus evidence from Stage 1. |
| **Output** | Drafted corrective DAG and cancel-vs-add decision; matching action reference loaded. |
| **Forbidden** | Loading action references while scouts are running; delegating synthesis to a child `team_planner`; scouting benchmark tests or `*/tests/*`. |

#### Steps

**Direct replan** (scope_expansion | wrong_owner_or_role | unresolved_blocker(trivial_direct_replan)):

```text
(1) Preserve live siblings and downstream validators  -> reason only
(2) Drop invalid candidates                           -> reason only
(3) Coverage check for every named failing variant    -> reason only
(4) Choose cancel-and-redraft or add-only             -> reason only
(5) Load action reference                             -> load_skill_reference(...)
```

Rules for Direct replan steps:

- (1) The failed/original request_replan task can appear as a same-parent sibling in `read_task_graph()`; it is never stale sibling work and must stay out of `cancel_ids`. If your draft `cancel_ids` contains the failed task id from the prompt, discard that cancellation before submitting.
- (2) Drop test-edit, doc-only, and value-table contradiction candidates. Drop candidates whose only evidence is a benchmark test path.
- (3) Each named failing variant must map to a repair/diagnostic task or a preserved live repair owner that names that production seam. Do not submit an empty or no-op replan.
- (5) If `cancel_ids=[]`, load action-add-tasks: `load_skill_reference(skill_name="team-replanner-playbook", reference_name="action-add-tasks")`. If a stale non-terminal direct sibling must be cancelled, load action-cancel-and-redraft: `load_skill_reference(skill_name="team-replanner-playbook", reference_name="action-cancel-and-redraft")`.

**Diagnostics** (unresolved_blocker(deep_diagnostics)):

```text
(1) Read existing file notes                -> read_file_note(file_path=...)
(2) Enumerate trace-gap triplets            -> reason only
(3) Launch scouts                           -> run_subagent(agent_name="scout", ...)
(4) Supervise                               -> check_background_progress / wait_for_background_task
(5) Harvest                                 -> read_file_note(file_path=...)
(6) Synthesize repair mapping               -> reason only
(7) Load action reference                   -> load_skill_reference(...)
```

Rules for Diagnostics steps:

- (1) If a note already contains root-cause-grade evidence, skip scouting that path.
- (2) Enumerate distinct trace-gap triplets in visible reasoning before any scout call: one failing test id or cluster, one suspected production path, one named symbol or seam.
- (3) Launch one scout per remaining triplet: `run_subagent(agent_name="scout", input={"target_paths": ["<one production path>"], "context": "Diagnostic for <triplet>; ..."})`. Keep failing tests in scout `context`, not `target_paths`. Queue the whole wave before checking progress.
- (6) Synthesize repair mapping yourself, including partial findings and disproved hypotheses.
- (7) Load the action reference that matches your mapping (same decision as Direct replan).

### 4. Submit

| Section | Contract |
| --- | --- |
| **Input** | Drafted payload from Stage 3. |
| **Output** | Exactly one `submit_replan({ new_tasks, cancel_ids })` call and no later tools. |
| **Forbidden** | `team_planner`, `scout`, or `team_replanner` in `new_tasks`; cancelling the failed task id; cancelling terminal or descendant tasks; singular spec label (spec sections must use the plural `2. Task Details:`); any tool call after submit. |

#### Steps

```text
[drafted payload]
    |
    v
(1) Load terminal-contract                  -> load_skill_reference(
                                                 skill_name="team-replanner-playbook",
                                                 reference_name="terminal-contract")
    |
    v
(2) Self-check against the reference's checklist  -> reason only
    Verify: top-level keys are new_tasks + cancel_ids only; new_tasks non-empty;
    every name is developer or validator; every spec uses
    1. Goal:, 2. Task Details:, 3. Acceptance Criteria:;
    cancel_ids contains only stale non-terminal direct siblings;
    no cancel_ids entry equals the failed task id from the prompt.
    |
    v
(3) Emit                                    -> submit_replan({ new_tasks, cancel_ids })
    Make no further tool calls after submit.
```
