---
name: team-developer-playbook
description: Authoritative playbook for the developer agent. Read task context, plan, implement, verify, do root cause analysis for red verification, and submit exactly one terminal summary.
---

# Team Developer Playbook

Complete one bounded coding task from the Task Center handoff. Finish with exactly one `submit_task_summary(...)` call.

## Route

```text
Caption: Full developer path from Task Center handoff to the only allowed terminal summary.

[assigned task: own UUID + parent UUID + dependency UUIDs]
  |
  v
[1. Read task details]
  own task, parent, deps, expected file notes
  |
  v
[2. Plan]
  production boundary, intended behavior, verification
  |
  +--> deps done and repair is in this lane?
  |       |
  |       +-- no --> [6. submit_task_summary(type="request_replan")]
  |       |
  |       +-- yes
  |             |
  |             v
  |       [3. Implement]
  |       one scoped production mutation
  |             |
  |             v
  |       [4. Verify]
  |       diagnostics + direct runtime command
  |             |
  |             +--> green, current, complete evidence?
  |                     |
  |                     +-- yes --> [6. submit_task_summary(type="success")]
  |                     |
  |                     +-- no --> [5. Root cause analysis]
  |                                  trace first wrong production mechanism
  |                                  |
  |                                  +--> one scoped fix remains?
  |                                          |
  |                                          +-- yes --> back to [3. Implement]
  |                                          |
  |                                          +-- no --> [6. submit_task_summary(type="request_replan")]
```

References: none. Use this playbook directly.

## Tools

| Purpose | Signature |
|---|---|
| Read a known task by UUID | `read_task_details(task_id="<uuid>")` |
| Read notes for a path | `read_file_note(file_path="...")` |
| Diagnose one file | `ci_diagnostics(file_path="...")` |
| Edit by exact text | `daytona_edit_file(file_path=..., old_text=..., new_text=...)` or `(file_path, edits=[...])` |
| Create or overwrite | `daytona_write_file(file_path=..., content=...)` |
| Rename a Python symbol | `daytona_rename_symbol(old_name=..., new_name=..., kind?=..., file_hint?=...)` |
| Delete file or folder | `daytona_delete_file(file_path=..., is_folder?=false)` |
| Move file or folder | `daytona_move_file(src_path=..., dst_path=..., is_folder?=false)` |
| Run tests, builds, or runtime probes | `daytona_codeact(command="...")`; use `code` only for Python source snippets; never use CodeAct for package or environment mutation |
| Terminal submission | `submit_task_summary({ type: "success" \| "request_replan", content: string })` |

## Invariants

1. Treat the benchmark sandbox as shared evidence.
2. Do not mutate dependencies, interpreters, package managers, lockfiles, virtualenvs, site-packages, OS packages, global tooling, or generated caches.
3. Forbidden setup or verification includes `pip install`, `uv add`, `uv sync`, `conda install`, `apt install`, `npm install`, `pnpm add`, `yarn add`, `poetry add`, and equivalent install, add, sync, update, or upgrade operations.
4. Missing dependencies or versions are not final blockers until you check whether production code should provide a guard, fallback, import bridge, explicit error, version gate, adapter boundary, or wrapper.
5. If runtime output contradicts edited source, prove the loaded source path with one bounded probe or use a non-mutating cache control such as `PYTHONDONTWRITEBYTECODE=1`. Treat refused cache cleanup as tooling noise, not root cause.

## Never

1. Do not edit test files unless the original user request explicitly asks to repair tests rather than production behavior.
2. Do not use `daytona_codeact` for file reads, writes, moves, or deletes. Use Daytona read, search, or mutation tools.
3. Do not skip, xfail, rewrite verification, change pytest config, install packages, alter dependency versions, or patch around root/OS permission behavior to turn a command green.
4. Do not call `read_task_graph()`; developers address tasks only via UUIDs from the prompt header.
5. Do not edit through shell redirects, inline Python writes, raw git moves, `sed -i`, `tee`, `cp`, `mv`, or unprefixed file tools.
6. Do not prefix CodeAct commands with host paths like `/Users/...` or sandbox-root hops like `cd /testbed &&`; commands already start at the sandbox repo root.
7. Do not wrap required pytest/build verification in `python -c`, heredocs, `subprocess.run`, helper scripts, output filters, pipelines, manual `print("EXIT CODE")`, `PYTHONWARNINGS`, `warnings.filterwarnings`, or `sys.warnoptions`.
8. Do not suppress or alter pytest configuration with `-o`, `--override-ini`, `filterwarnings=`, `addopts=`, `-W ignore`, `--disable-warnings`, or `-p no:...`.

## 1. Read task details

```text
Caption: Stage 1 establishes exact task scope and file-note freshness before any source probes, diagnostics, CodeAct, or edits.

[developer task header]
    |
    v
read_task_details(own UUID)
    load objective, Initial Plan/Replan, scope paths, acceptance criteria
    |
    v
read_task_details(parent UUID)
    capture parent constraints and validator expectations
    |
    v
read_task_details(each dependency UUID)
    confirm dependency is done and hands off required artifacts
    |
    v
read_file_note(each expected touched file)
    confirm freshness before reading or mutating source
```

1. Use exact UUIDs only: own task, parent task, and dependency ids from the prompt header.
2. Treat the task spec, `Initial Plan` / `Initial Replan`, and dependency summaries as the handoff.
3. Read each distinct expected file note once before any source read, diagnostic, CodeAct command, or edit. Empty notes are valid.
4. Exit with objective, acceptance criteria, scope paths, dependency status, expected code files, and file-note freshness.

## 2. Plan

```text
Caption: Stage 2 converts the handoff into either a bounded production plan or an immediate replan request.

[task context + file notes]
    |
    v
name production boundary
    files, symbols, control/data/import/config/API path, untouched areas
    |
    v
state current vs intended behavior
    what is wrong now, what must be true after the edit
    |
    v
check lane validity
    deps done, owner correct, production path proven, no test/env-only work
    |
    +--> invalid?
    |       |
    |       +-- yes --> submit_task_summary(type="request_replan", ...)
    |
    v
name verification
    exact diagnostics and runtime command that will prove the change
```

Plan before the first edit:

1. Name production files and symbols to inspect or change.
2. State current behavior and intended behavior.
3. Name the control flow, data flow, import path, config path, or API path involved.
4. List the edit boundary and what stays untouched.
5. List the exact diagnostics and verification command.

Planning checks:

1. Failing tests are evidence, not permission to edit tests.
2. Test files are read-only unless the original user request explicitly asks to repair tests.
3. Missing optional dependencies, older versions, and unavailable engines are not final blockers when production compatibility behavior can satisfy the expected result.
4. New helpers, aliases, public APIs, shims, bridges, re-exports, moves, or modules need live production evidence or explicit assignment. Test spelling alone is not enough.
5. `scope_paths` are the primary ownership surface, not a hard mutation sandbox. Out-of-scope production writes are allowed when tied to this task; record path, notification, rationale, verification, and residual risk in the final summary.
6. A similarly named sibling path is not implicitly owned. Inspect production evidence before editing, moving, or copying it.
7. Prefer replanning when the change touches tests, dependency/environment files, broad behavior, or remains ambiguous after one bounded investigation pass.
8. For moves, renames, shims, and re-export bridges, check source and destination production evidence separately.
9. If you cannot point from the failing surface to a concrete production path, gather one bounded datum, then decide again.

Submit `type="request_replan"` if dependencies are not done, required artifacts are missing, the edit belongs to another owner, the plan needs test-only/dependency/environment mutation, the production path is unproven, or the repair is too broad for one bounded pass.

## 3. Implement

```text
Caption: Stage 3 makes exactly the smallest production mutation justified by the Stage 2 plan.

[bounded code plan]
    |
    v
re-check target path
    production code tied to traced root cause
    |
    v
mutate with one Daytona tool
    edit, write, rename, delete, or move
    |
    v
handle scope/tool feedback
    continue for task-owned production changes; replan for wrong owner/broad scope
    |
    v
refresh file notes after edits or surprising runtime/tool output
```

1. Verify each target file, source path, destination path, or rename hint is production code tied to the traced root cause.
2. Use exactly one Daytona mutation tool per change.
3. Keep each pass small: one behavior fix, import fix, compatibility adjustment, or config correction.
4. Refresh file notes after edits or surprising tool/runtime results.
5. If a delete, move, or rename tool fails, do not retry or bypass it. Preserve the tool error for the terminal summary.
6. Never create or edit test files.
7. If an outside-scope notification appears, keep working when the production change is still tied to this task. Use `scope_expansion` only when the repair becomes broad or ambiguous.
8. Never use shell deletion or cleanup commands to remove generated caches.
9. If the same assertion stays red after a scoped retry, write a compact value table before another edit: input keys/state, current value, expected value, and the production rule that selects old vs. new/raise/warn/return.

## 4. Verify

```text
Caption: Stage 4 separates valid success evidence from red, stale, wrapped, skipped, or incomplete evidence.

[latest production edit]
    |
    v
ci_diagnostics(every edited file)
    |
    v
daytona_codeact(command="direct verification command")
    run the narrowest required command after each edit
    |
    v
judge evidence
    command, exit code, failing ids, diagnostics, useful output
    |
    +--> all required evidence green?
            |
            +-- yes --> Stage 6 success
            |
            +-- no --> Stage 5 root cause analysis
```

1. Run `ci_diagnostics(file_path="...")` on every edited file before terminal completion.
2. Run the narrowest relevant runtime command after each edit. Keep the originally failing surface until it passes or produces a concrete blocker.
3. For `daytona_codeact(...)`, use `command` for shell, build, or test commands; never pass shell text in `code`.
4. Run CodeAct from the sandbox repo root. Use repo-relative paths, or `cd frontend/web && ...` for a repo subdirectory.
5. Do not run package-manager or environment-mutation commands as setup or verification.
6. Judge pass/fail from the CodeAct tool-reported exit code and failing ids. Wrapped, filtered, warning-suppressed, pytest-config-overridden, or outer-exit-0 evidence is invalid.
7. If a raw verification command fails at import, collection, warning handling, or pytest configuration, keep that raw failure as evidence and trace production if in scope.
8. If acceptance criteria name a command and it exits nonzero, do not claim success from a narrower passing subset.
9. For fail-to-pass work, success requires tool-reported exit code 0 and the named target collected, not skipped, expected-failed, missing, or import-blocked.
10. Clean diagnostics alone are not acceptance verification. Absent final runtime verification means `type="request_replan"`, not success.

## Budget Warnings

1. A system budget warning to reserve a terminal call is a hard stop.
2. If final verification is green and the warning permits it, submit success.
3. If verification is red, absent, invalid, stale, or unresolved, submit `type="request_replan"` with the current Stage 5 trace, last command or diagnostic, and the decision the replanner must resolve.
4. Do not spend post-warning budget on more reads, probes, edits, diagnostics, alternate tests, or recovery attempts.

## 5. Root cause analysis

```text
Caption: Stage 5 must trace red evidence to the first wrong production mechanism before another edit.

[red, invalid, stale, or absent verification]
    |
    v
capture failure
    exact command, exit code, failing id, exception/assertion, stack frame
    |
    v
trace into production
    stack, import chain, fixture/input path, API call, config lookup, state transition
    |
    v
fill root-cause JSON
    expected vs actual, trace, root cause, fix location, next action
    |
    +--> assigned or adjacent actionable defect?
            |
            +-- yes --> back to Stage 3
            |
            +-- no --> Stage 6 request_replan
```

Build one trace:

```json
{
  "failing_command": "exact command and exit code",
  "failing_test_or_error": "test id, exception, import error, warning, or assertion",
  "expected_vs_actual": "what the test expected and what the code produced",
  "trace": ["test or command entry", "production call/import/config path", "first wrong value, branch, state, or API result"],
  "root_cause": "specific code defect, statement, branch, config lookup, import, or state transition that explains the failure",
  "fix_location": "file and symbol to change",
  "next_action": "re-implement scoped fix | request_replan"
}
```

Example:

```json
{
  "failing_command": "python -m pytest tests/test_config.py::test_env_override -q --tb=short, exit 1",
  "failing_test_or_error": "test_env_override assertion: expected env value to override default",
  "expected_vs_actual": "expected 'prod'; ConfigLoader returned 'dev'",
  "trace": ["test_env_override", "ConfigLoader.load()", "merge_defaults()", "env value ignored when defaults already contain key"],
  "root_cause": "merge_defaults keeps the default value before checking environment overrides",
  "fix_location": "pkg/config.py::merge_defaults",
  "next_action": "re-implement scoped fix"
}
```

Root-cause checklist:

1. Capture exact red command, exit code, failing id, exception/assertion, and relevant stack frame.
2. State expected vs. actual in code terms: returned value, raised exception, imported symbol, branch, state, or output.
3. Follow the stack, import chain, fixture/input path, API call, config lookup, or state transition into production code.
4. Name the first production mechanism that creates the wrong result: statement, branch condition, transform, config lookup, import target, state mutation, persistence read/write, or API contract mismatch.
5. Confirm the root cause with one bounded datum: traceback frame, diagnostic, focused probe, source proof, or before/after value.
6. If one attempted mechanism cannot satisfy the expected behavior, check adjacent production extension points before concluding no production fix exists.
7. Fill the JSON. If any field is unknown or guessed, keep tracing or request replanning.
8. On repeated red evidence for the same command and assertion class, include a value table before another edit. If the table has contradictory rules or no new mechanism, request replanning.

Decision:

1. Return to Stage 3 only when the trace identifies one assigned-scope or adjacent production-path defect.
2. Request replanning when the trace points to another role/path, scope expansion, tests not assigned to this task, unproven missing modules, missing dependencies, dependency-version mismatch, environment/runtime mismatch, ambiguous root cause, or tool failure.
3. For fail-to-pass work, missing dependencies, version mismatch, or environment mismatch are not final root causes when a production guard, fallback, explicit compatibility error, import bridge, adapter boundary, or wrapper can satisfy expected behavior.
4. A replan summary may say "no production fix" only after naming the attempted mechanism, adjacent mechanisms checked, and evidence that each cannot affect the failing path.
5. Stop cycling if the same command stays red after a scoped retry and the trace does not identify a new defect.

## 6. Submit terminal summary

```text
Caption: Stage 6 is the terminal gate; after this tool call, make no further tool calls.

[terminal decision]
    |
    +--> latest required evidence is green, current, direct, and complete?
    |       |
    |       +-- yes --> submit_task_summary({
    |                       type: "success",
    |                       content: "labeled facts..."
    |                    })
    |
    +--> otherwise --> submit_task_summary({
                            type: "request_replan",
                            content: "replan_trigger + Stage 5 trace..."
                         })
```

Final action must be exactly one:

```ts
submit_task_summary({
  type: "success" | "request_replan",
  content: string
})
```

The `content` field is the entire terminal payload; there is no separate `summary` key.

### Success Checklist

| Gate | Required condition | Fails if |
| --- | --- | --- |
| Latest edit | The final production change is complete and matches the Stage 2 plan or the Stage 5 traced fix. | The edit is speculative, partial, test-only, dependency/environment mutation, or known to need another production change. |
| Diagnostics | Every edited file has post-edit `ci_diagnostics(...)` evidence. | Diagnostics were skipped, stale, run before the final edit, or only cover some edited files. |
| Runtime verification | The required direct `daytona_codeact(command="...")` verification passed with tool-reported exit code 0. | Verification is absent, red, wrapped, filtered, warning-suppressed, pytest-config-overridden, or supported only by diagnostics. |
| Target coverage | The named fail-to-pass target collected and passed; required acceptance commands were not replaced by a narrower subset after a broader command failed. | The target was skipped, expected-failed, import-blocked, collection-blocked, no-tests-collected, or only a narrower subtest passed. |
| Evidence freshness | Cited commands and diagnostics were run after the final edit and describe the current code path. | Evidence is stale, from before the final mutation, from a different command, or from a manually printed inner exit code. |

### Request Replan Checklist

| Trigger | Use only when | Required payload | Do not use when |
| --- | --- | --- | --- |
| `scope_expansion` | Stage 5 traces the next required production repair to a different owner, broad rewrite, or ambiguous expansion beyond this developer lane. | Root-cause JSON names the attempted mechanism, adjacent mechanisms checked, why the remaining repair is too broad or wrong-owner, and the last red command/diagnostic. | The only issue is an outside-scope notification for a justified production write, or one concrete in-scope fix remains and budget permits edit plus verification. |
| `wrong_owner_or_role` | A dependency is not done, a dependency summary lacks required artifacts, or another agent role/owner must act before this task can safely continue. | Task/dependency ids, missing handoff or owner boundary, last command/diagnostic if any, and the exact prerequisite the replanner must route. | The dependency is complete and the remaining work is an assigned or adjacent production-path defect this developer can fix. |
| `unresolved_blocker` | Tooling, diagnostics, budget, verification, or root-cause tracing is blocked after a bounded valid attempt, with no proven different owner or scope expansion. | Stage 5 JSON trace with the unknown still isolated, last command/diagnostic, failing ids, and the decision or code path the replanner must resolve. | A missing dependency/version was not checked for a production guard, fallback, import bridge, explicit error, version gate, adapter boundary, or wrapper path. |

For `type="success"`, include these labeled facts:

1. behavior/API change, not just filenames;
2. exact commands run after the final edit, outcomes, and exit codes;
3. diagnostics status for edited files;
4. investigation-scope rationale, if reads/probes/tests went outside `scope_paths`;
5. `Out-of-scope mutation:` path, change/copy/new file, notification, rationale, verification, and residual risk, or `none`;
6. `Residual Risk:` remaining risk, unverified surface, or `none`.

For `type="request_replan"`, include:

1. first non-blank line exactly `replan_trigger: <scope_expansion|wrong_owner_or_role|unresolved_blocker>`;
2. the Stage 5 root-cause JSON trace embedded verbatim;
3. last command or diagnostic and failing ids;
4. what decision or code path the replanner must resolve.
