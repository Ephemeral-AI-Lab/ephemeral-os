---
name: team-replanner-playbook
description: Playbook for the team_replanner agent. Load recovery context, classify failure mode, diagnose only concrete blockers, and submit a schema-valid corrective replan with submit_replan(...).
---

# Team Replanner Playbook

Produce the smallest corrective DAG justified by failed-task evidence and the failed task's original contract. Finish with exactly one `submit_replan(...)` call and make no later tool calls.

Replanner-created tasks use `developer` repairs, `validator` checks, or a `team_planner` redraft only when recovery is still broad. The replanner coordinates recovery; it does not patch code and does not create scout or replanner children.

<Forbid Rule>
Never plan test suite or test-file related tasks.
Never assign subagents to explore test suites or test files.
</Forbid Rule>

| Recovery role | Use when |
| --- | --- |
| `developer` repair child | Concrete, owner-scoped fix justified by verified failed-task evidence. |
| `validator` continuation | Same-payload verification or carrying uncompleted acceptance criteria forward. |
| `team_planner` redraft | Recovery surface is still broad/unresolved after diagnostics. |

## Overall Stage Flow

```text
Caption: replanner recovery path. Stages run in order; references load only at the stage that uses them.

  +---------------------+    +-----------+    +---------+    +----------------+
  |  load recovery ctx  | -> |  classify | -> |   act   | -> |  submit_replan |
  +---------------------+    +-----------+    +---------+    +----------------+
            |                      |              |                  |
     evidence ledger          classification   corrective       submit_replan(...)
     (verified vs gaps)       + diagnostics    mapping
            |                      |              |                  |
       exit: ledger        exit: line written  exit: action     exit: one tool
       complete + graph    + scouts done if    reference        call, no prose
       read                deep_diagnostics    loaded + lanes
                                               drafted
```

| # | Stage | Input | Exit gate | Reference |
| --- | --- | --- | --- | --- |
| 1 | load recovery context | Replanning header (failed/own/parent/dep UUIDs) | Evidence ledger built; graph topology read; relevant siblings classified. | none |
| 2 | classify | Evidence ledger | Single `Classification:` line written. For `unresolved_blocker`, single `Diagnostics decision:` line written; for `deep_diagnostics`, scout wave returned with notes. | none |
| 3 | act | Classification + diagnostics output | Action reference loaded, corrective mapping covers original contract. | `load_skill_reference(skill_name="team-replanner-playbook", reference_name="action-add-tasks")` (add-only) **or** `load_skill_reference(skill_name="team-replanner-playbook", reference_name="action-cancel-and-redraft")` (cancel-redraft). |
| 4 | submit_replan | Drafted recovery lanes | Exactly one `submit_replan({ new_tasks, cancel_ids })` call, no later tool calls or prose. | `load_skill_reference(skill_name="team-replanner-playbook", reference_name="terminal-contract")` — load before drafting the payload. |

## 1. Load Recovery Context

Enter from the replanning header. Use exact UUIDs; do not classify, scout, or load references yet.

| Context item | Action |
| --- | --- |
| Own, parent, failed, dependency tasks | Read with `read_task_details(task_id=...)`. |
| Graph topology | Call `read_task_graph()` only after required task reads return. |
| Relevant siblings | Read only siblings you may preserve, cancel, depend on, or avoid. |
| Failed evidence | Separate verified command/trace/fix-location facts from unresolved gaps. |

```text
Caption: evidence ledger. Recovery plans should not treat gaps as facts.

failed task
  |-- verified: command, exit, trace, mechanism, candidate fix
  |-- unresolved: owner, rule, value mapping, missing path
  |-- original contract: assigned goal, criteria, scope, uncompleted work
  |-- live siblings: useful work to preserve
  `-- stale siblings: running/pending/ready direct siblings that may be cancelled
```

**Exit:** evidence ledger complete; graph topology read; relevant siblings classified into preserve/stale/avoid.

## 2. Classify Failure

Enter after the evidence ledger is complete. State exactly one classification line:

```text
Classification: <scope_expansion|wrong_owner_or_role|unresolved_blocker>
```

| Classification | Use when |
| --- | --- |
| `scope_expansion` | Repair belongs outside the failed task's assigned production scope. |
| `wrong_owner_or_role` | Another owner or role must handle the repair. |
| `unresolved_blocker` | A production trace gap remains, including same-scope fixes without enough evidence. |

For `unresolved_blocker`, add one line:

```text
Diagnostics decision: <trivial_direct_replan|deep_diagnostics>
```

| Diagnostics route | Use when |
| --- | --- |
| `trivial_direct_replan` | Failed task names every production seam, **and** no prior sibling/ancestor already failed on the same scope, **and** rich RCA traces a single defect. |
| `deep_diagnostics` (default for chained or repeated failures) | Owner, path, rule, value mapping, or production seam remains unresolved; **or** a prior task in the same lineage already filed `request_replan` on the same scope; **or** the failed task admitted partial/out-of-scope/blocked-by-pre-existing/unfixable in its summary. **Fan out a parallel scout wave** plus `ci_query_symbol` on each unfamiliar seam **before** drafting recovery children; submitting `submit_replan` after a `deep_diagnostics` decision without at least one scout/`ci_query_symbol` call this turn is a hard defect. |

A rich-looking RCA from a developer who repeatedly hit the same red is a symptom, not proof; default to `deep_diagnostics` whenever the failure is chained or the same scope has been touched by a prior failed sibling.

### Diagnostic Scout Fanout

Only enter when `Diagnostics decision: deep_diagnostics` was written.

```text
Caption: deep_diagnostics scout fanout — one scout per unresolved seam, all in parallel.

unresolved seams (S1, S2, ..., Sn)
  -> parallel wave: scout(S1), scout(S2), ..., scout(Sn) + ci_query_symbol(unclear callers)
  -> harvest notes -> recovery children mapped 1:1 to seam findings
```

| Seam shape in failed RCA | Scout to spawn |
| --- | --- |
| Single named file with unclear callers | One proven exact production file scout + `ci_query_symbol` on each suspect symbol. Guessed or test-derived filenames use the package/directory row. |
| Two coupled files (engine + adapter, producer + consumer) | One multi-path scout for that pair. |
| Whole subsystem / package boundary unclear | One directory scout for the package. |
| Multiple independent unresolved seams | One scout per seam, dispatched as one parallel wave. |
| No scout | Existing notes already provide root-cause-grade evidence for that seam. |

Dispatch each scout with `run_subagent(agent_name="scout", prompt="<scout prompt>")` — `prompt` is the only channel; production paths must be named inline. Production paths only; never name test paths, test ids, benchmark filenames, F2P/P2P ids, or failing-test labels in a scout prompt, and never call workspace/scout tools on tests. Harvest notes for every assigned production path; missing notes create uncertainty for that path only.

### Scout Prompt Format

Every diagnostic scout prompt uses these three sections, in order:

```text
## Task
<one-line recovery question this scout answers>

## Exploration Path
<production path 1>
<production path 2>

## Terminal Contract
submit_file_note(paths=[<exploration_paths>], content="<finding>")
```

| Section | Contains |
| --- | --- |
| `## Task` | The single recovery question this scout answers; no test path, test id, F2P/P2P id, benchmark file name, or failing-test label. |
| `## Exploration Path` | Repo-relative production paths only — no test paths, no globs, no parent-dir batching. |
| `## Terminal Contract` | Literal `submit_file_note(paths=[...], content="...")` call template. Every path in `## Exploration Path` must appear in the `paths` argument of at least one submitted note. |

**Exit:** classification line written. For `unresolved_blocker` + `deep_diagnostics`, the parallel scout wave has returned and notes are harvested.

## 3. Act

Enter after classification is written and diagnostics are complete or intentionally skipped.

**Required first action this stage — before drafting any corrective lane:** load the action reference matching your decision.

| Decision | Required reference |
| --- | --- |
| Add-only | `load_skill_reference(skill_name="team-replanner-playbook", reference_name="action-add-tasks")` |
| Cancel-and-redraft | `load_skill_reference(skill_name="team-replanner-playbook", reference_name="action-cancel-and-redraft")` |

```text
Caption: cancellation boundary.

same parent:
  failed request_replan/origin -> preserve; never cancel
  this replanner     -> preserve
  terminal/validator -> preserve
  live useful sibling -> preserve
  stale running/pending/ready sibling -> may appear in cancel_ids
```

| Action | Use when |
| --- | --- |
| Add-only | Only new corrective work is needed, or the failed task itself is the only stale item. |
| Cancel and redraft | Direct siblings in `running`, `pending`, or `ready` are stale, duplicate, or depend on the failed assumption. |
| Preserve terminal work | Sibling is `done`, `failed`, `cancelled`, `request_replan`, outside the stale region, or a validator continuation. |
| Preserve live useful work | Objective remains valid after corrective work. |

| Coverage row | Action |
| --- | --- |
| Named failing variant | Map to a repair/diagnostic child or preserved live repair owner. |
| Validator-discovered child-owned suite or uncompleted criterion | Map to repair plus validator, or preserved live owner. |
| Blocker-only fix leaves the failed task's goal/criteria/F2P ids uncovered | **Required**: add a continuation `developer` (or `team_planner` for broad rows) with `deps=[<repair_child_id>]`, carrying every uncompleted goal, acceptance criterion, F2P id, and production scope from the failed contract. Shipping repair-only without the continuation child is a hard defect. |
| Test/benchmark/pytest-config restore/edit, skip/xfail, doc-only, contradictory value rule, or failed dev's claim that F2P ids are "unfixable"/"benchmark-authored"/"cannot be edited" | Evidence only; never silently drop F2P ids — emit a production diagnostic or `wrong_owner_or_role` escalation that carries every dropped id forward. |

**Exit:** action reference is loaded; corrective mapping covers every coverage row above.

## 4. submit_replan

Enter after the Stage 3 reference has shaped the corrective mapping.

**Required first action this stage — before drafting the payload:**

```text
load_skill_reference(skill_name="team-replanner-playbook", reference_name="terminal-contract")
```

| Submit check | Expected result |
| --- | --- |
| Top-level keys | Only `new_tasks` and `cancel_ids`; **default `cancel_ids: []`**. Add an id only after confirming it is a stale running/pending/ready direct sibling per the matrix below. |
| New tasks | Direct repair/check children or Planner handoff redraft; agents are `developer`, `validator`, or `team_planner`. |
| Specs | Structured `goal`, `detail`, and `acceptance_criteria`; unresolved blockers include diagnostics decision and planner redrafts include Planner handoff. |
| Dependencies | Local recovery ids or freshly proven schedulable existing ids only. |

`cancel_ids` membership matrix — id is **forbidden** if any row matches; the runtime returns `Validation failed: replanner cannot cancel ...` and the retry must remove the id, never re-add it.

| Forbidden id type | Why |
| --- | --- |
| The failed task that triggered this replan (status `request_replan` or `failed`) | Immutable evidence; runtime finalizes it. |
| Any other task already in `request_replan`, `failed`, `done`, or `cancelled` | Terminal — cannot re-cancel. |
| The replanner's own task id or any of its descendants | A replanner cannot cancel itself or its placeholder subtree. |
| Any `team_replanner` task | Replanners coordinate recovery; they are never cancelled here. |
| Any validator-continuation task that already has live dependencies pointing at the new repair children | Preserves §2b fix-then-continue edges. |
| Anything outside the failed task's direct sibling set | Out-of-region cancels corrupt the parent DAG. |

When in doubt, ship `cancel_ids: []` — add-only replans are always valid.

```text
Caption: terminal contract.

drafted recovery lanes
  -> submit_replan({ new_tasks, cancel_ids })
  -> end (no further tool calls, no trailing prose)
```

**Exit:** one `submit_replan` tool call emitted; no summary, output, parent ids, trailing prose, or later tool calls.
