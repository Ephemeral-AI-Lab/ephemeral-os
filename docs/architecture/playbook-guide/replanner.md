# Replanner Playbook Guide

This guide governs `team-replanner-playbook` and its companion references under
`backend/config/skills/team-replanner-playbook/references`.

The replanner is the team harness recovery coordinator. It does not repair code
directly. It reads the failed lane, preserves live work, cancels stale work only
when justified, and submits the smallest corrective DAG that lets TaskCenter
resume scheduling safely while still covering the failed lane's original
contract.

## Harness Role

```text
Caption: replanner position in the team harness.

developer / validator
  -> request_replan(reason)
      |
      v
TaskCoordinator
  -> marks original task request_replan
  -> creates replanner task
  -> rewires pending dependents to replanner
      |
      v
team_replanner
  -> loads recovery context
  -> classifies failure
  -> designs corrective DAG
  -> submit_replan(new_tasks, cancel_ids)
      |
      v
TaskCoordinator
  -> applies cancellations
  -> inserts replan children
  -> dispatches recovery work
```

| Responsibility | Replanner behavior |
| --- | --- |
| Preserve graph integrity | Treat TaskCenter as graph owner; submit only `submit_replan(...)`. |
| Coordinate team work | Keep valid live siblings, cancel stale siblings, and add repair/validation lanes. |
| Convert evidence to tasks | Turn failed summaries, root-cause traces, notes, and graph state into executable child tasks. |
| Preserve original contract | Cover the failed developer/validator goal, criteria, and required evidence after the blocker is fixed. |
| Avoid code repair | Diagnose only enough to plan recovery; do not patch files. |
| Unblock dependents | Produce the recovery gate that downstream rewired tasks can wait on. |

## Coordination Contract

The replanner is spawned after a worker has already declared that the lane cannot
finish locally. Its output must be graph-shaped, not narrative.

| Surface | Contract |
| --- | --- |
| Input | Own replanner task, failed task, parent, dependencies, relevant siblings, file notes, and failure evidence. |
| Output | Exactly one `submit_replan({ new_tasks, cancel_ids })`. |
| New task agents | `developer` repair lanes and `validator` verification lanes only. |
| New task parentage | Runtime inserts new tasks as direct children of the replanner; do not set `parent_id`. |
| Cancellation | Only stale non-terminal direct siblings of the replanner. |
| Summary | No free-text summary, output field, or post-submit prose. |
| References | Check and stage-load `action-add-tasks`, `action-cancel-and-redraft`, and `terminal-contract`. |

## Recovery Flow

```text
Caption: replanner stage machine. Each stage narrows coordination risk before
the terminal replan.

[1 Load recovery context]
  |
  v
[2 Classify failure]
  |-- scope_expansion -----------+
  |-- wrong_owner_or_role -------+--> [3 Choose recovery action]
  |-- unresolved_blocker --------+
          | trivial evidence
          |----------------------+
          | deep diagnostics needed
          v
     [Diagnostic scout wave]
          |
          v
[3 Choose recovery action]
  |-- add-only recovery --------> load action-add-tasks
  `-- cancel and redraft -------> load action-cancel-and-redraft
          |
          v
[4 Submit]
  load terminal-contract -> self-check -> submit_replan(...)
```

## Load Recovery Context

The first job is not to plan new work. It is to reconstruct the coordination
state: what failed, what still matters, and what must not be disturbed.

| Context item | Why it matters |
| --- | --- |
| Own replanner task | Defines this recovery lane and prompt-provided ids. |
| Failed task | Carries root-cause trace, failure reason, scope paths, and attempted verification. |
| Original contract | The failed task's goal, detail, acceptance criteria, scope paths, and uncompleted work. |
| Parent task | Defines sibling region and inherited objective. |
| Dependencies | Reveal upstream work this recovery must preserve or depend on. |
| Task graph | Shows live, terminal, stale, and downstream nodes. |
| Relevant siblings | Needed for cancel-vs-preserve decisions. |
| File notes | Durable evidence from scouts and previous lanes. |

Split evidence from gaps before planning:

```text
Caption: evidence ledger. Recovery plans should not treat gaps as facts.

failed task
  |-- verified evidence: command, exit, trace, mechanism, fix location
  |-- unresolved gaps: owner, rule, value mapping, missing path
  |-- original contract: assigned goal, criteria, scope, uncompleted work
  |-- live siblings: still useful or terminal work
  `-- stale siblings: superseded non-terminal work in cancellation region
```

## Classify Failure

Classification tells the harness what kind of coordination repair is needed.

| Classification | Use when | Coordination implication |
| --- | --- | --- |
| `scope_expansion` | The next repair is outside the failed task's bounded scope. | Add a correctly scoped repair lane; cancel stale work if it depends on the old scope. |
| `wrong_owner_or_role` | Evidence proves a different owner or role must handle the work. | Redirect to the proper developer or validator lane. |
| `unresolved_blocker` | A production trace gap remains. | Diagnose only the gap, then add a repair/diagnostic lane. |

For `unresolved_blocker`, choose one diagnostics path:

| Decision | Use when |
| --- | --- |
| `trivial_direct_replan` | Existing task details, notes, and CI evidence already name every seam needed for corrective tasks. |
| `deep_diagnostics` | Any owner, path, rule, value mapping, or production seam remains unresolved. |

## Diagnostic Scout Coordination

Diagnostic scouts are coordination tools. They answer missing trace questions so
the replanner can shape recovery work; they do not replace child planners.

```text
Caption: diagnostic scout fanout. Each scout answers one trace-gap objective.

gap: failing command + suspected owner + missing rule
  -> scout(target_paths=[...], objective="resolve owner/rule for recovery DAG")
  -> read_file_note(file_paths=[...])
  -> corrective mapping
```

| Scout shape | Use when |
| --- | --- |
| Single file, deep | The failed trace names one likely file or symbol. |
| Multiple files, deep | The suspected seam crosses a small coupled call chain. |
| Directory, superficial | The likely owner is a package/subsystem and exact files are unknown. |
| Parallel scout wave | Independent trace gaps block different recovery lanes. |
| No scout | Existing notes already carry root-cause-grade evidence. |

Prompts should be objective based:

```text
Caption: replanner scout prompt. The objective is the missing recovery decision.

Objective: decide whether the failed dtype conversion belongs in parser.py or
adapter.py, and identify the repair lane.
Targets: ["pkg/parser.py", "pkg/adapter.py"]
Evidence: failed task saw command X, assertion Y, trace Z.
Depth: deep across this call chain.
Return: owner seam, first wrong mechanism, repair task boundary, and gaps.
```

## Choose Recovery Action

The replanner chooses between preserving the existing sibling graph and replacing
stale sibling work.

| Action | Use when |
| --- | --- |
| Add-only | Existing live siblings remain valid and only new corrective work is needed. |
| Cancel and redraft | Non-terminal siblings are stale, depend on the failed assumption, or would duplicate the new recovery lane. |
| Preserve terminal work | A sibling is already `done`, `failed`, `cancelled`, or otherwise outside the stale non-terminal region. |
| Preserve live useful work | A non-terminal sibling still has a valid objective after the corrective work. |

Cancellation boundary:

```text
Caption: only stale non-terminal direct siblings are cancellable.

same parent:
  failed origin task        -> never cancel
  this replanner            -> never cancel
  terminal sibling          -> preserve
  live useful sibling       -> preserve
  stale non-terminal sibling -> may appear in cancel_ids
  nested descendant         -> do not name directly; cascade handles it
```

## Design Corrective DAG

Corrective DAGs should be small, executable, and tied to the failed evidence.
They are not blocker-only patches: because the failed origin remains terminal at
`request_replan`, the new DAG must also own any uncompleted part of that
origin's developer/validator contract unless a preserved live sibling already
owns it.

| Task shape | Use |
| --- | --- |
| Repair developer | One owner, one mechanism, clear scope, exact acceptance criteria. |
| Continuation developer | Remaining original implementation work that becomes possible after the blocker repair. |
| Diagnostic developer | A bounded production investigation that must produce a code repair or precise blocker. |
| Validator | Same-payload verification of all recovery producers and original failed-task criteria. |
| Existing dependency | Only when the existing task is schedulable and does not already depend on the replanner/origin pair. |

```text
Caption: add-only recovery. Existing sibling work remains valid.

replanner R
  |-- N1 developer: repair missing compatibility guard
  `-- N2 validator: run failed command and guardrail checks
        deps=[N1]
```

```text
Caption: cancel-and-redraft recovery. Stale sibling is replaced by recovery DAG.

cancel_ids=[S1]

replanner R
  |-- N1 developer: repair corrected owner path
  |-- N2 developer: update dependent adapter after N1
  |     deps=[N1]
  `-- N3 validator: verify original failure and adapter behavior
        deps=[N2]
```

Task specs should include:

| Spec field | Expected content |
| --- | --- |
| `goal` | One clear recovery outcome. |
| `detail` | Failed evidence, root-cause trace, owner boundary, scope, and preserved uncertainty. |
| `acceptance_criteria` | Exact commands, diagnostics, expected behavior, and original-task criteria still requiring proof. |
| `deps` | Local recovery ordering or valid existing schedulable dependency. |
| `scope_paths` | Production owner paths, not benchmark/test ownership unless explicitly assigned. |

## Submit Contract

Before `submit_replan(...)`, load and apply the terminal contract reference.

| Check | Expected result |
| --- | --- |
| `new_tasks` | Non-empty, all direct recovery children of the replanner by runtime insertion. |
| `agent` | Only `developer` or `validator`. |
| Original-contract coverage | The blocker fix plus every uncompleted original criterion is owned by a new task or preserved live owner. |
| `cancel_ids` | Only stale non-terminal direct siblings; never the failed origin or replanner. |
| Dependencies | Local new-task ids or allowed existing schedulable ids only. |
| Spec | Structured `goal`, `detail`, and `acceptance_criteria` are non-empty. |
| Terminal action | Exactly one `submit_replan(...)`; no later tools or prose. |

## Reference Files

Replanner playbook changes must check companion references:

| Reference | Check for |
| --- | --- |
| `action-add-tasks` | Add-only recovery criteria, task shape, deps, and preservation language. |
| `action-cancel-and-redraft` | Cancellation boundary, stale sibling criteria, and cascade assumptions. |
| `terminal-contract` | Payload keys, schema, terminal exclusivity, and invalid recovery cases. |

Keep reference loading stage-specific. If a reference starts covering unrelated
stages, split it or move the shared rule back into the playbook body.

## Review Checklist

| Check | Expected result |
| --- | --- |
| Harness role | The replanner is described as recovery coordinator, not coder or executor. |
| Context ledger | Failed evidence, gaps, live siblings, and stale siblings are separated. |
| Classification | Recovery path uses `scope_expansion`, `wrong_owner_or_role`, or `unresolved_blocker`. |
| Diagnostics | Scout fanout answers trace gaps with objective-based prompts and bounded depth. |
| Cancellation | `cancel_ids` can name only stale non-terminal direct siblings. |
| Corrective DAG | New tasks are small recovery children using only `developer` and `validator`. |
| Original contract | Replan output covers both blocker repair and uncompleted failed-task work. |
| Reference files | Companion references are checked, updated, split, or deleted when behavior changes. |
| Runtime contract | Terminal submission still uses exactly one `submit_replan(...)`. |
