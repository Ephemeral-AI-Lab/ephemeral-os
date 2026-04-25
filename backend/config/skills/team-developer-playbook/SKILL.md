---
name: team-developer-playbook
description: Authoritative playbook for the developer agent. Read task context, plan, implement, verify, do root cause analysis for red verification, and submit exactly one terminal summary.
---

# Team Developer Playbook

Complete one bounded coding task from the Task Center handoff. Finish with exactly one `submit_task_success(...)` or `request_replan(...)` call.

<Forbid Rule>
Never try to edit test files or test suites to pass acceptance criteria.
</Forbid Rule>

## Stage Flow

```text
Caption: developer route. Plan one mechanism, verify fresh evidence, and run required RCA for every verification failure.

handoff UUIDs
  -> [1 Read context]
  -> [2 Plan boundary]
       | wrong owner / broad / blocked / budget fully spent + incomplete -> request_replan
  -> [3 Implement one mechanism]
  -> [4 Verify]
       | green/current/no target reds -> submit_task_success
       ` red / absent / invalid
   -> [Required RCA]
       | one scoped production defect + budget remains -> Stage 3
       ` unclear / broad / stale / budget fully spent + incomplete -> request_replan
```

| Stage | Gate |
| --- | --- |
| 1. Read context | Own task, parent, deps, and file notes are loaded first. |
| 2. Plan boundary | Production owner, intended behavior, edit boundary, and verification are concrete. |
| 3. Implement | One production mechanism is changed with Daytona mutation tools. |
| 4. Verify | Edited-file diagnostics and direct runtime evidence are fresh; every red, absent, or invalid result must produce RCA before any edit or replan. |
| 5. Submit | The terminal tool is the final action. |

## Tools

| Purpose | Signature |
| --- | --- |
| Read task context | `read_task_details(task_id="<uuid>")` |
| Read file notes | `read_file_note(file_paths=[...])` |
| Diagnose one file | `ci_diagnostics(file_path="...")` |
| Query a symbol's definition + callers + cross-file usage | `ci_query_symbol(name="...", file_path="...")` |
| Edit / write / delete / move | `daytona_edit_file(...)`, `daytona_write_file(...)`, `daytona_delete_file(...)`, `daytona_move_file(...)` |
| Run verification | `daytona_shell(command="...")` |
| Terminal success | `submit_task_success({ summary: string })` |
| Terminal replan | `request_replan({ reason: string })` |

## Operating Guardrails

| Surface | Compact rule |
| --- | --- |
| Shared sandbox | Treat it as shared evidence; avoid setup or cleanup churn. |
| Dependency/env mutation | Do not mutate packages, interpreters, lockfiles, virtualenvs, site-packages, OS packages, global tooling, generated caches, tests, pytest config, or verification itself. |
| Shell boundary | Use `daytona_shell` for tests/probes from sandbox cwd; avoid host paths, reads/writes, redirects, cleanup. Python one-liners are probes only, never pytest substitutes. |
| Mutation channel | Production source mutations MUST go through `daytona_edit_file` / `daytona_write_file` / `daytona_delete_file` / `daytona_move_file`. Writing a helper script (`edit_*.py`, `apply_fix.py`, `hack_*.py`, etc.) and running it via `daytona_shell` to mutate source — directly or via `Path.write_text`, `open(..., 'w')`, `sed -i`, `tee`, or `>` redirects — is a forbidden bypass even if the script itself is in-scope. |
| Verification integrity | Latest red raw command controls status; pytest overrides, wrappers, filters, or inner-exit-code tricks are RCA-only. |
| Graph reads | Developers work from prompt UUIDs and task details, not `read_task_graph()`. |

## 1. Read Context

```text
Caption: UUID reads precede notes; notes precede source reads, diagnostics, commands, and edits.

own task -> parent task -> dependency tasks -> file notes
   -> goal + criteria + scope paths + dependency status + freshness
```

Use exact UUIDs from the prompt header. Treat task and dependency details as the handoff. Call `read_file_note(file_paths=[...])` for every assigned scope path and every file you intend to inspect with `daytona_read_file`; empty notes are valid but the call is mandatory before the first source read.

Prefer `ci_query_symbol(name=..., file_path=...)` over wide `daytona_read_file` ranges to map a symbol's definition, callers, and cross-file usage in one call; reach for it whenever the trace, owner, or call site is unclear.

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
| Replan check | Wrong owner, broad scope, missing proof, invalid verification, dependency/env mutation, or fully spent budget with work incomplete. |

Missing optional deps, older versions, and unavailable engines are not final blockers when a production guard, fallback, compatibility error, bridge, adapter, or wrapper path can satisfy expected behavior. Never repair missing packages by installing them; prove a production seam or request `unresolved_blocker` after RCA.

## 3. Implement

```text
Caption: mutation gate. Each mutation needs production proof.

bounded edit plan -> prove file/symbol/rename target -> one Daytona mutation -> Stage 4
```

| Mutation check | Route |
| --- | --- |
| Assigned or proven adjacent production path | Edit with the narrowest Daytona mutation tool. |
| A few light outside-scope operations tied to one mechanism | Continue with evidence and record them. |
| Multiple outside-scope files or broad/unclear boundary | `request_replan` with `scope_expansion`. |
| Test edit, dependency edit, environment edit, or verification rewrite | `request_replan` with the fitting trigger. |

After a red command, do not edit again until Stage 4 RCA is emitted (see below).

## 4. Verify

```text
Caption: evidence gate. Diagnostics and the direct runtime command are both required; failed evidence must enter RCA.

post-edit repo
  -> ci_diagnostics(each edited file)
  -> daytona_shell(exact runtime command)
  -> green/current/criteria met ? submit_task_success : REQUIRED RCA
```

| Evidence | Rule |
| --- | --- |
| Diagnostics | Run `ci_diagnostics` on every edited file before terminal completion. |
| Runtime command | Run the narrowest required command after each edit; keep the original failing surface until it passes or blocks. |
| Exit judgment | Use tool-reported exit code and failing ids. Collection/import/no-tests/skips/xfails/missing optional deps are red for named fail-to-pass targets. |
| Missing verification | If the required command was not run after the final edit, including fully spent budget, request replan. |
| Policy block | Treat a blocked command or forbidden action as red evidence. Emit the RCA packet next; use `unresolved_blocker` when no valid equivalent can preserve the required evidence. |
| Verify failure | RCA is mandatory before the next edit, `submit_task_success`, or `request_replan`. |

### Required RCA For Verify Failure

After every red, absent, or invalid verification, emit the RCA packet below as a fenced ```json block in your next assistant message before any further `daytona_*`, `submit_task_success`, or `request_replan` call. Free-form prose explaining the failure does not satisfy this gate; the named keys must be present.

```text
Caption: mandatory red evidence loop. Trace the first wrong production mechanism before any edit or replan.

failing command -> failing id/error -> expected vs actual
  -> production trace -> first wrong mechanism -> fix location
```

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
| `trace`/`root_cause`/`fix_location` cites an unfamiliar symbol or unclear caller chain | Run `ci_query_symbol` on the symbol before any further edit, then refresh the packet. |
| One assigned-scope or proven adjacent production defect, new mechanism since last RCA, and enough budget | Return to Stage 3 for one more bounded fix. |
| Same target test/diagnostic stays red across two consecutive RCAs without a new local defect named in `root_cause` | `request_replan` with `unresolved_blocker`. |
| Wrong owner/role, broad change, test-only path, dependency/env mismatch with no production seam, ambiguous cause, policy block, or tool failure | `request_replan`. |
| Budget fully spent before green verification | `request_replan` unless already green with clean diagnostics. |

## 5. Submit Terminal Summary

```text
Caption: terminal gate. Success is only for current, direct, passing verification.

latest required verification green + diagnostics clean + no target-red caveats
  -> submit_task_success({ summary })

red / absent / invalid / stale / partial after Stage 4 RCA
  or blocked / broad / wrong-owner / budget fully spent + incomplete
  -> request_replan({ reason })
```

The "latest required verification" must be the unmodified required pytest command: no `-c`, `-p no:`, `-W`, `--noconftest`, no piping, and `rootdir`/`configfile` in the captured output must match the project's real config (not `/dev` or `null`). If the green evidence came from any wrappered, filtered, or override-poisoned command, treat it as red and either re-run the raw command or `request_replan`.

`submit_task_success` is forbidden if the summary admits any acceptance criterion is unmet, partial, deferred, blocked, out-of-scope, environmental, infrastructural, or pre-existing. Any phrase that excuses missing green evidence — e.g. "remaining tests fail", "still failing", "out of scope for this fix", "Out-of-Scope", "fundamental test/implementation mismatch", "not implemented", "blocked by pre-existing", "blocked by unrelated", "verification blocked", "infrastructure problem", "infrastructure issue", "environment issue", "tooling problem" — means `request_replan` with `unresolved_blocker` instead. Partial, indirect, or workaround verification (for example, importing and calling one test function in `python -c` instead of the required pytest command) is not success.

Success summary includes:

| Fact | Required content |
| --- | --- |
| Behavior/API change | What changed. |
| Verification commands | Exact commands, outcomes, and exit codes. |
| Diagnostics | Edited-file diagnostics status. |
| Investigation scope | Why reads/probes/tests went outside `scope_paths`, or `none`. |
| Out-of-scope mutation | Path, action, rationale, verification, or `none`. |
| Residual risk | Non-target caveat or `none`; target red means replan. |

Replan reason includes:

| Part | Required content |
| --- | --- |
| Trigger | First line: `replan_trigger: <scope_expansion|wrong_owner_or_role|unresolved_blocker>`. |
| Trace | Stage 4 RCA when available; otherwise blocker/scope evidence. |
| Last evidence | Last command or diagnostic plus failing ids. |
| Needed decision | Owner, scope, sequence, or code path for the replanner. |
| Remaining contract | Uncompleted parts of this task: unmet acceptance criteria, unfinished scope paths, and behavior the replanner must continue covering beyond the blocker fix. |

If evidence appears to implicate a test, benchmark, fixture, or pytest config file, do not label the work "test fix needed" and stop. Name the production import/API/compatibility seam that must satisfy the contract, or state that the production seam is unresolved, and carry every target id and acceptance criterion forward.

Trigger guide:

| Trigger | Use when |
| --- | --- |
| `scope_expansion` | Next repair is broad, ambiguous, or requires multiple outside-scope production files. Never used to justify editing tests, benchmarks, pytest config, or fixtures. |
| `wrong_owner_or_role` | A dependency is not done or another owner/role must act. |
| `unresolved_blocker` | Tooling, diagnostics, spent budget, verification, or trace evidence is blocked with no proven different owner. Also used when the only remaining work would require editing tests/benchmarks/pytest config — the production seam needs to be redesigned, not the test. |
