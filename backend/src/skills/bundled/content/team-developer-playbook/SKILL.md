---
name: team-developer-playbook
description: Authoritative playbook for the developer agent. Read task context, plan, implement, verify, do root cause analysis for red verification, and submit exactly one terminal summary.
---

# Team Developer Playbook

Read the following sections to complete one bounded coding task, then finish with exactly one `submit_task_summary(...)` call.

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

## Environment invariants

Treat the benchmark sandbox as shared evidence. Do not mutate dependencies, interpreter state, package managers, lockfiles, virtualenvs, site-packages, OS packages, or global tooling.

Forbidden commands include `pip install`, `uv add`, `uv sync`, `conda install`, `apt install`, `npm install`, `pnpm add`, `yarn add`, `poetry add`, and equivalent install, add, sync, update, or upgrade operations. Package-manager commands are not verification.

If a missing dependency, missing optional extra, or different dependency version appears necessary, do not install it. Capture the exact command, exit code, import error or version evidence, and submit `type="request_replan"` with trigger `unresolved_blocker` unless the trace proves the required production repair is outside `scope_paths`, in which case use `scope_expansion`.

## Never

1. Do not edit test files unless the original user request explicitly asks to repair tests rather than production behavior. In benchmark/fail-to-pass work, tests are evidence only even if a child task mistakenly assigns a test file.
2. Do not use `daytona_codeact` for file-content reads, writes, moves, or deletes. Use the Daytona read, search, or mutation tools above.
3. Do not skip, xfail, rewrite verification, change pytest config, install packages, alter dependency versions, or patch around root/OS permission behavior to turn a command green.
4. Do not call `read_task_graph()`; developers address tasks only via UUIDs from the prompt header.
5. Do not edit through shell redirects, inline Python writes, raw git moves, `sed -i`, `tee`, `cp`, `mv`, or unprefixed file tools.
6. Do not prefix CodeAct commands with host paths like `/Users/...` or sandbox-root hops like `cd /testbed &&`; commands already start at the sandbox repo root. Use repo-relative commands such as `python -m pytest ...`.

## Route

```mermaid
flowchart TD
    A["1. Read task details"] --> B["2. Plan"]
    B --> C{"In scope and deps done?"}
    C -- "No" --> R["Stage 6: request_replan"]
    C -- "Yes" --> D["3. Implement"]
    D --> E["4. Verify"]
    E --> F{"Verification green?"}
    F -- "Yes" --> S["Stage 6: success"]
    F -- "No" --> G["5. Root cause analysis"]
    G --> H{"One scoped fix remains?"}
    H -- "Yes" --> D
    H -- "No" --> R
```



## 1. Read task details

Do this before probes, file reads, diagnostics, CodeAct, or edits.

1. Call `read_task_details(task_id="<uuid>")` for your task, parent task, and each dependency id from the prompt header.
2. Use exact UUIDs only. Do not use slugs, short prefixes, scout ids, or fabricated ids. Only the UUIDs exposed in your prompt header and their dependencies are addressable.
3. Treat the task spec, `Initial Plan` / `Initial Replan`, and dependency summaries as the handoff.
4. After those required UUID reads, call `read_file_note(file_path="...")` for each file you expect to touch before any file read, diagnostic, CodeAct command, or edit. For multi-file scopes, compare against the expected touched files and read each distinct path once; do not duplicate one path while omitting another. Empty notes are valid freshness checks. Do not batch file-note reads with source file reads, diagnostics, CodeAct commands, or edits.

Exit with: objective, acceptance criteria, scope paths, dependency status, expected code files, and file-note freshness.

## 2. Plan

Write a code-focused plan before the first edit:

1. Name the production files and symbols you expect to inspect or change.
2. State the current code behavior that must change.
3. State the intended code behavior after the change.
4. Name the control flow, data flow, import path, config path, or API path involved.
5. List the exact edit boundary: what will change and what will stay untouched.
6. List the exact verification command and diagnostics to run after the edit.

Planning checks:

1. Use failing tests as evidence, not permission to edit tests. A test import or collection blocker is still evidence; fix production when the traced root cause points to a production path this lane can responsibly repair.
2. Test files are read-only unless the original user request explicitly asks to repair tests rather than production behavior. A child task that assigns test skip/xfail/rewrite work in a benchmark is invalid; request replanning instead.
3. New helpers, aliases, public APIs, shims, bridges, re-exports, moves, or modules need live production evidence or an explicit assignment. Test spelling alone is not enough.
4. When live production evidence proves a missing compatibility module, serialization lane, engine bridge, shim, or re-export path, prefer creating or editing the production file over updating tests. Do not turn a proven production gap into a test-file rewrite.
5. `scope_paths` are the primary ownership surface, not a hard mutation sandbox for developers. You may widen reads, diagnostics, and test commands to prove ownership. Developers may write, copy, or create production files outside `scope_paths` when that is needed for the assigned task. The tooling may emit an outside-scope system notification; treat it as coordination guidance, not a stop condition, and include the path, notification, rationale, verification, and residual risk in the final summary.
6. A similarly named sibling path is not implicitly owned. For example, ownership of `pkg/compatibility.py` does not by itself prove `pkg/_compatibility.py` is the right repair path; inspect enough production evidence to justify the path before editing or copying it.
7. Prefer replanning instead of editing when the change would touch tests, dependency/environment files, require a broad behavior rewrite, or remain ambiguous after one bounded investigation pass.
8. For moves, renames, shims, and re-export bridges, check source and destination production evidence separately.
9. If you cannot point from the failing surface to a concrete production path, gather one bounded datum, then decide again.

Submit `type="request_replan"` now if any of these hold:

1. A dependency read in Stage 1 is not `done` or its summary does not hand off the code artifacts this task needs.
2. The next required edit belongs to another role or code path.
3. The next required change would be a broad or ambiguous production change that this lane cannot responsibly finish.
4. The next required edit is test-only, skip/xfail, pytest configuration, or verification rewrite in benchmark/fail-to-pass work.
5. The plan requires an unproven missing module, shim, re-export, helper, dependency install, or dependency version change.
6. The required fix is a benchmark test edit/skip/xfail/rewrite, dependency/config/generated-file mutation, or an ambiguous new production file whose missing path and mechanism are not proven by live production evidence.
7. The required change is too complex or ambiguous for one bounded pass.

Exit with: a concrete bounded plan, or a terminal replan summary.

## 3. Implement

Make one minimal production change that matches the plan.

1. Before every mutation, verify the target file path, source path, destination path, or rename file hint is a production path tied to the traced root cause. Out-of-scope production writes, copies, and new files are allowed for developers; use the Daytona mutation tools so write-scope notifications are recorded. If the target is a test file, dependency/environment file, or clearly different owner, submit `type="request_replan"` instead.
2. Use exactly one Daytona mutation tool per change (see Tools).
3. Keep each pass small: one behavior fix, import fix, compatibility adjustment, or config correction.
4. Refresh file notes after edits or surprising tool/runtime results.
5. If a delete, move, or rename tool fails, do not retry or bypass it. Preserve the tool error for the terminal summary.
6. Never create or edit test files
7. If an outside-scope notification appears, treat it as coordination context and keep working when the production change is still tied to this task. If the required change becomes broad or ambiguous, submit `type="request_replan"` with trigger `scope_expansion`. If a mutation reports a verification-surface warning, pause and re-check the scope and code path before continuing.

Exit with: the smallest justified edit ready for verification.

## 4. Verify

Prove the latest edit. Do not claim success from stale or partial evidence.

1. Run `ci_diagnostics(file_path="...")` on every edited file before terminal completion.
2. Run the narrowest relevant runtime command after each edit. Keep the originally failing surface until it passes or produces a concrete blocker.
3. For `daytona_codeact(...)`, use `command` for every shell, build, or test command; never pass a shell command string in `code`.
4. Run CodeAct commands from the sandbox repo root. Use repo-relative paths, or `cd frontend/web && ...` for a repo subdirectory. Never prefix commands with `cd /testbed &&`, and never `cd` to a host/local workspace path from AGENTS or environment context.
5. Do not run package-manager or environment-mutation commands as setup or verification. A command that would install, add, sync, update, upgrade, or pin dependencies is invalid evidence.
6. Judge runtime pass/fail from the command exit code and failing ids. If pytest exits `4`, collects `0` items, or the named node is missing, treat that as red evidence.
7. Record command, exit code, failing ids, diagnostics, and the shortest useful output snippet. If a command is blocked by policy, submit `type="request_replan"` with trigger `unresolved_blocker` only when no valid equivalent can preserve the needed evidence.

Exit with: green evidence → Stage 6 (`type="success"`); any red, stale, or absent evidence → Stage 5.

## 5. Root cause analysis

Use this section every time verification stays red. The goal is to find the actual code defect, not just the failing symptom. Once the actual root cause is confirmed and in scope, go back to Stage 3 and implement the fix.

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

Root-cause checklist — all must hold before you re-enter Stage 3:

1. Capture the exact red command, exit code, failing id, exception/assertion, and the relevant stack frame.
2. State expected vs. actual in code terms: returned value, raised exception, imported symbol, branch taken, persisted state, or emitted output.
3. Follow the stack, import chain, fixture/input path, API call, config lookup, or state transition from the test into production code.
4. Name the first production mechanism that creates the wrong result — exact statement, branch condition, transform, config key lookup, import target, state mutation, persistence write/read, or API contract mismatch. Symptoms ("test failed", "assertion mismatch"), broad areas ("config bug", "bad state"), and guesses ("probably race", "likely missing helper") do not satisfy this step.
5. Confirm the root cause with one bounded datum: traceback frame, diagnostic, focused runtime probe, local source proof, or a before/after value on the traced path.
6. Answer three questions: what value/state/import/branch first became wrong, which code made it wrong, and why that code is incorrect for the expected behavior.
7. Fill the JSON above. If any field is "unknown" or a guess, keep tracing or request replanning — do not re-enter Stage 3 from a shallow trace.

Decision:

1. If the trace identifies one assigned-scope, actionable code defect, return to Stage 3 and implement the smallest fix at `fix_location`.
2. Request replanning when the trace points to another role or code path, scope expansion, tests not assigned to this task, unproven missing modules, missing dependencies, dependency-version mismatch, environment/runtime mismatch, ambiguous root cause, or tool failure.
3. Stop cycling if the same command stays red after a scoped retry and the trace does not identify a new code defect.

Exit with: actual root cause found and implementation started, or a terminal replan summary with the trace gap.

## 6. Submit terminal summary

Final action must be exactly one:

```ts
submit_task_summary({
  type: "success" | "request_replan",
  content: string
})
```

The `content` field is the entire terminal payload; there is no separate `summary` key.

For `type="success"`, `content` must include these labeled facts. Do not omit a line because the answer is "none":

1. behavior/API change, not just filenames;
2. exact commands run after the final edit, observed outcomes, and exit code for every cited command or probe;
3. diagnostics status for edited files;
4. investigation-scope rationale, if reads/probes/tests went outside `scope_paths`;
5. `Out-of-scope mutation:` with path, exact change/copy/new file, notification, rationale, verification, and residual risk if you made one, otherwise "none";
6. `Residual Risk:` with remaining risk, unverified surface, or "none" when no known risk remains.

For `type="request_replan"`, `content` must include:

1. first non-blank line exactly `replan_trigger: <scope_expansion|wrong_owner_or_role|unresolved_blocker>`;
2. the Stage 5 root-cause JSON trace, embedded verbatim inside `content`;
3. last command or diagnostic and failing ids;
4. what decision or code path the replanner must resolve.

Use `scope_expansion` only when the required production repair is clearly a different owner or too broad/ambiguous for this lane, not merely because a developer write/copy was outside `scope_paths` or received a system notification. Use `wrong_owner_or_role` when another agent role, dependency, or production owner must act before this task can succeed. Use `unresolved_blocker` when verification, diagnostics, tooling, budget, or root-cause tracing is still blocked but no different owner/scope is proven.

Use `type="success"` only when the latest required verification passed. Use `type="request_replan"` for red, absent, invalid, stale, incomplete, blocked, another-role/code-path, broader-scope, or too-complex verification.
