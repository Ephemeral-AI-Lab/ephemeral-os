---
name: team-developer-playbook
description: Authoritative playbook for the developer agent. Read task context, plan, implement, verify, do root cause analysis for red verification, and submit exactly one terminal summary.
---

# Team Developer Playbook

Complete one bounded coding task from the Task Center handoff. Finish with exactly one `submit_task_success(...)` or `request_replan(...)` call.

## Stage Flow

```text
Caption: developer route. Plan one mechanism, verify fresh evidence, then submit success only from green evidence.

handoff UUIDs
  -> [1 Read context]
  -> [2 Plan boundary]
       | wrong owner / broad / blocked / budget risk -> request_replan
  -> [3 Implement one mechanism]
  -> [4 Verify]
       | green + current + criteria met -> submit_task_success
       ` red / absent / invalid -> [5 RCA]
  -> [5 RCA]
       | one scoped production defect + budget -> Stage 3
       ` unclear / broad / stale / budget -> request_replan
```

| Stage | Gate |
| --- | --- |
| 1. Read context | Own task, parent, deps, and file notes are loaded first. |
| 2. Plan boundary | Production owner, intended behavior, edit boundary, and verification are concrete. |
| 3. Implement | One production mechanism is changed with Daytona mutation tools. |
| 4. Verify | Edited-file diagnostics and direct runtime evidence are fresh. |
| 5. RCA | Red evidence is traced to the first wrong production mechanism. |
| 6. Submit | The terminal tool is the final action. |

## Tools

| Purpose | Signature |
| --- | --- |
| Read task context | `read_task_details(task_id="<uuid>")` |
| Read file notes | `read_file_note(file_paths=[...])` |
| Diagnose one file | `ci_diagnostics(file_path="...")` |
| Edit / write / delete / move | `daytona_edit_file(...)`, `daytona_write_file(...)`, `daytona_delete_file(...)`, `daytona_move_file(...)` |
| Run verification | `daytona_shell(command="...")` |
| Terminal success | `submit_task_success({ summary: string })` |
| Terminal replan | `request_replan({ reason: string })` |

## Operating Guardrails

| Surface | Compact rule |
| --- | --- |
| Shared sandbox | Treat it as shared evidence; avoid setup or cleanup churn. |
| Dependency/env mutation | Do not mutate packages, interpreters, lockfiles, virtualenvs, site-packages, OS packages, global tooling, generated caches, tests, pytest config, or verification itself. |
| Shell boundary | Use `daytona_shell` only for tests, builds, or runtime probes, not reads, writes, moves, deletes, redirects, inline file writes, or cleanup commands. |
| Verification integrity | Use raw required commands. Warning suppression, pytest overrides, wrappers, helper scripts, output filters, or inner-exit-code tricks are invalid evidence. |
| Graph reads | Developers work from prompt UUIDs and task details, not `read_task_graph()`. |

## 1. Read Context

```text
Caption: UUID reads precede notes; notes precede source reads, diagnostics, commands, and edits.

own task -> parent task -> dependency tasks -> file notes
   -> goal + criteria + scope paths + dependency status + freshness
```

Use exact UUIDs from the prompt header. Treat task and dependency details as the handoff. Read each expected file note once; empty notes are valid.

## 2. Plan Boundary

```text
Caption: planning gate. Continue only when the repair lane is concrete.

context
  -> production owner + current/intended behavior + runtime path
  -> one edit boundary + diagnostics + exact verification command
```

| Planning item | Expected content |
| --- | --- |
| Production owner | File/module/symbol or owned directory, plus why it belongs here. |
| Behavior delta | Concrete wrong value, branch, import, config, state, output, or API behavior. |
| Edit boundary | One mechanism; adjacent files only when live evidence couples them. |
| Verification | Exact post-edit command plus diagnostics for edited files. |
| Replan check | Wrong owner, broad scope, missing proof, invalid verification, dependency/env mutation, or budget risk. |

Tests and benchmark ids are evidence, not edit surfaces, unless the original request assigns test repair. Missing optional deps, older versions, and unavailable engines are not final blockers when a production guard, fallback, compatibility error, bridge, adapter, or wrapper path can satisfy expected behavior.

## 3. Implement

```text
Caption: mutation gate. Each mutation needs production proof.

bounded edit plan -> prove file/symbol/rename target -> one Daytona mutation -> Stage 4
```

| Mutation check | Route |
| --- | --- |
| Assigned or proven adjacent production path | Edit with the narrowest Daytona mutation tool. |
| One or two lightweight outside-scope writes/moves/deletes/creates tied to the same mechanism | Continue with evidence and record in the terminal payload. |
| Third outside-scope mutation, blocked move/delete, broad change, or unclear boundary | `request_replan` with `scope_expansion`. |
| Test edit, dependency edit, environment edit, or verification rewrite | `request_replan` with the fitting trigger. |

After a red command, write a compact value table before another edit:

| input/state | current | expected | production rule | next action |
| --- | --- | --- | --- | --- |

## 4. Verify

```text
Caption: evidence gate. Diagnostics and the direct runtime command are both required before success.

post-edit repo
  -> ci_diagnostics(each edited file)
  -> daytona_shell(exact runtime command)
  -> green/current/criteria met ? success : Stage 5
```

| Evidence | Rule |
| --- | --- |
| Diagnostics | Run `ci_diagnostics` on every edited file before terminal completion. |
| Runtime command | Run the narrowest required command after each edit; keep the original failing surface until it passes or blocks. |
| Exit judgment | Use tool-reported exit code and failing ids. Collection/import/no-tests/skips/xfails/missing optional deps are red for named fail-to-pass targets. |
| Missing verification | If the required command was not run after the final edit, including due to budget, request replan. |
| Policy block | Use `unresolved_blocker` when no valid equivalent can preserve the required evidence. |

## 5. Root Cause Analysis

```text
Caption: red evidence loop. Trace the first wrong production mechanism before another edit.

failing command -> failing id/error -> expected vs actual
  -> production trace -> first wrong mechanism -> fix location
```

RCA packet:

```json
{
  "failing_command": "exact command and exit code",
  "failing_test_or_error": "test id, exception, import error, warning, or assertion",
  "expected_vs_actual": "concrete wrong value, branch, state, symbol, output, or behavior",
  "trace": ["entry point", "production call/import/config path", "first wrong mechanism"],
  "root_cause": "specific defect or unresolved trace gap",
  "fix_location": "file and symbol, or unresolved owner gap",
  "next_action": "re-implement scoped fix | request_replan"
}
```

| RCA decision | Route |
| --- | --- |
| One assigned-scope or proven adjacent production defect and enough budget | Return to Stage 3. |
| Wrong owner/role, broad change, test-only path, dependency/env mismatch with no production seam, ambiguous cause, repeated red command, or tool failure | `request_replan`. |
| Budget warning before green verification | Next tool call is `request_replan` unless already green with clean diagnostics. |

## 6. Submit Terminal Summary

```text
Caption: terminal gate. Success is only for current, direct, passing verification.

latest required verification passed + diagnostics clean + criteria met
  -> submit_task_success({ summary })

red / absent / invalid / stale / partial / blocked / broad / wrong-owner / budget-risk
  -> request_replan({ reason })
```

Success summary includes:

| Fact | Required content |
| --- | --- |
| Behavior/API change | What changed. |
| Verification commands | Exact commands, outcomes, and exit codes. |
| Diagnostics | Edited-file diagnostics status. |
| Investigation scope | Why reads/probes/tests went outside `scope_paths`, or `none`. |
| Out-of-scope mutation | Path, action, rationale, verification, or `none`. |
| Residual risk | Remaining caveat or `none`. |

Replan reason includes:

| Part | Required content |
| --- | --- |
| Trigger | First line: `replan_trigger: <scope_expansion|wrong_owner_or_role|unresolved_blocker>`. |
| Trace | Stage 5 RCA packet embedded verbatim. |
| Last evidence | Last command or diagnostic plus failing ids. |
| Needed decision | Owner, scope, sequence, or code path for the replanner. |

Trigger guide:

| Trigger | Use when |
| --- | --- |
| `scope_expansion` | Next repair is outside this lane, broad, ambiguous, or requires multiple outside-scope mutations. |
| `wrong_owner_or_role` | A dependency is not done or another owner/role must act. |
| `unresolved_blocker` | Tooling, diagnostics, budget, verification, or trace evidence is blocked with no proven different owner. |
