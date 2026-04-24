---
name: team-validator-playbook
description: Authoritative playbook for the validator agent. Read task context, build a validation plan, run diagnostics and exact verification, analyze red evidence, optionally apply one scoped correction, and submit exactly one terminal summary.
---

# Team Validator Playbook

Verify the assigned developer or child-planner outcome from live repo evidence. Finish with exactly one `submit_task_success(...)` or `request_replan(...)` call, then stop.

Read the handoff first, plan exact evidence, verify before substitutes, and apply at most one obvious local correction only when red evidence proves it belongs inside validator scope.

## Workflow Map

| Stage | Purpose | Output contract |
| --- | --- | --- |
| 1. Read task details | Load validation task, parent, dependencies, and file notes. | Criteria, handoff status, touched files, scope paths, freshness. |
| 2. Build validation plan | Map every criterion to direct evidence. | Acceptance map, command order, diagnostics, public-surface guardrail decision. |
| 3. Run diagnostics and exact verification | Prove current repo state. | Green evidence for Stage 6, or red evidence for Stage 4. |
| 4. Analyze red evidence | Trace the first wrong mechanism. | Root-cause packet plus correction or replan decision. |
| 5. Apply one scoped correction | Patch only an obvious local defect. | One correction plus fresh verification route. |
| 6. Submit terminal summary | Emit the only terminal outcome. | One success or replan call; no later tools. |

**Diagram caption:** Validator route. The validator first builds an evidence map, runs direct verification, then either succeeds, applies one local correction, or asks for replanning.

Decision flow:

```text
+----------------------------+
| assigned validation task   |
+-------------+--------------+
              |
              v
+----------------------------+
| 1. read task + notes       |
+-------------+--------------+
              |
              v
+----------------------------+       invalid handoff
| 2. map criteria to evidence|--------------------------+
+-------------+--------------+                          |
              |                                         v
              v                              +----------------------+
+----------------------------+      red      | 6. request_replan   |
| 3. diagnostics + exact cmd |-------------->+----------------------+
+-------------+--------------+               ^          ^
              | green                        |          |
              v                              |          |
+----------------------------+               |          |
| 6. submit success          |               |          |
+----------------------------+               |          |
                                             |          |
                              no local fix   |          | same surface
                        +--------------------+          |
                        |                               |
                        v                               |
              +----------------------------+  one fix   |
              | 4. red evidence trace      |----------->|
              +-------------+--------------+            |
                            |                           |
                            v                           |
              +----------------------------+            |
              | 5. one scoped correction   |------------+
              +----------------------------+
```

## Reference Map

No loadable references. Use this playbook directly.

## Tools

| Purpose | Signature |
|---|---|
| Read a known task by UUID | `read_task_details(task_id="<uuid>")` |
| Read notes for paths | `read_file_note(file_paths=[...])` |
| Diagnose one file | `ci_diagnostics(file_path="...")` |
| Run tests or shell | `daytona_shell(command="...")` |
| Edit by exact text | `daytona_edit_file(file_path=..., old_text=..., new_text=...)` or `(file_path, edits=[...])` |
| Create a proven new file | `daytona_write_file(file_path=..., content=...)` |
| Terminal success | `submit_task_success({ summary: string })` |
| Terminal replan request | `request_replan({ reason: string })` |

## Guardrail Matrix

| Surface | Compact rule |
| --- | --- |
| Shell boundary | Do not use `daytona_shell` for file reads, writes, moves, deletes, introspection, or wrapper health checks. Use Daytona read, search, or mutation tools. |
| Shell edits | Do not edit through shell redirects, inline Python writes, raw git moves, `sed -i`, `tee`, `cp`, `mv`, or unprefixed file tools. |
| Verification integrity | Do not skip, xfail, rewrite verification, change pytest config, install packages, or patch around root/OS permission behavior to turn a command green. |
| Test files | Do not edit test files unless the task explicitly owns a test-only bug. |
| Duplicate work | Do not launch duplicate equivalent verification commands in parallel. One exact command per suite is enough unless sharding after a transient no-output failure. |
| Evidence freshness | Do not claim success from stale, partial, indirect, or wrapper evidence. |
| Working directory | daytona_shell commands already start at the sandbox repo root. Use repo-relative commands such as `python -m pytest ...`. Never prefix commands with `cd /testbed &&`, and never `cd` to a host/local workspace path. |
| Pytest overrides | Do not suppress or alter pytest configuration with `-o`, `--override-ini`, `filterwarnings=`, `addopts=`, `-W ignore`, `--disable-warnings`, `PYTHONWARNINGS`, or `-p no:...`. Those commands are invalid verification evidence. |

## Workflow Details

### 1. Read task details

| Section | Contract |
| --- | --- |
| **Input** | The assigned validation task header with own UUID, parent UUID, and dependency UUIDs. |
| **Output** | Goal, detail, acceptance criteria, parent guidance, dependency handoff status, touched files, scope paths, and file-note freshness. |
| **Forbidden** | daytona_shell, CI, notes, file reads, edits, diagnostics, references, or graph reads before required context reads; fabricated, short, slug, or scout ids. |

**Diagram caption:** Stage 1 context order. Required UUID reads come first; file notes follow and must precede source reads, diagnostics, tests, or edits.

#### Steps

```text
[prompt header UUIDs]
      |
      v
own task -> parent task -> dependency tasks -> touched/owned file notes
      |
      v
[criteria + handoff evidence + scope paths + freshness gaps]
```

1. Call `read_task_details(task_id="<uuid>")` for your task, parent task, and every dependency from the prompt header.
2. Use exact UUIDs only; never planner slugs, short prefixes, fabricated ids, or scout ids.
3. Treat your task spec as the validation contract. Treat dependency task details and parent details as the implementation handoff.
4. After required UUID reads, call `read_file_note(file_paths=[...])` once with every touched or owned production file before source reads, diagnostics, tests, or edits. Empty notes count.
5. Record missing, boilerplate, stale, or evidence-free dependency summaries as validation gaps.

### 2. Build validation plan

| Section | Contract |
| --- | --- |
| **Input** | Stage 1 validation task, parent guidance, dependency handoffs, touched files, scope paths, and file notes. |
| **Output** | Acceptance-criterion map, exact command order, diagnostics list, guardrail decision, and any handoff gaps. |
| **Forbidden** | Substituting broad, unrelated, narrowed, or duplicate commands before the exact required command; expanding correction scope from tests, acceptance criteria, or import errors alone. |

**Diagram caption:** Stage 2 evidence map. Every criterion receives a direct diagnostic, command, or probe before validation begins; invalid handoffs exit to replanning.

#### Steps

```text
[validation context]
      |
      v
[criteria] -> [exact command first] -> [diagnostics] -> [guardrail?]
      |
      +--> dependency/handoff/scope invalid --> request_replan
      |
      v
Stage 3
```

Plan before the first diagnostic, runtime command, or corrective edit:

| Planning item | Compact rule |
| --- | --- |
| Criteria map | Map each acceptance criterion to the command, diagnostic, or probe that verifies it. |
| Exact command | Put the exact required command from the task or handoff before substitutes, broad suites, unrelated suites, or narrowed confirmation. |
| Diagnostics | Name owned files for `ci_diagnostics(file_path="...")`. |
| Guardrail | Add one nearby public-surface guardrail only when the touched surface affects public serialization, schema shape, API/CLI/docs-visible output, or prompts. |
| Scope expansion | Acceptance criteria, dependency handoffs, and test outcomes never expand `scope_paths` by themselves. A new production file may extend scope only through `daytona_write_file` when live production evidence proves a missing module, serialization lane, engine bridge, shim, re-export, or bridge and no other worker owns that path. |
| Tests | Prefer a proven production fix over a test rewrite. Do not edit tests unless explicitly assigned a test-only bug. |

Call `request_replan` now when a dependency is not done, the handoff does not identify what to validate, the required verification belongs to another owner, the validation surface has no workflow-valid evidence path, or The only apparent correction would edit, move, rename, or delete an existing file outside assigned `scope_paths` and dependency handoff files. Also replan for an out-of-scope test edit, an unproven missing compatibility module, or a new production file whose `daytona_write_file` scope expansion was blocked or conflicted.

### 3. Run diagnostics and exact verification

| Section | Contract |
| --- | --- |
| **Input** | Stage 2 validation plan. |
| **Output** | Command/probe results mapped to criteria, diagnostics status, guardrail result when applicable, and red evidence when present. |
| **Forbidden** | Stale, partial, indirect, wrapper, warning-suppressed, pytest-config-overridden, duplicate-equivalent, or missing verification evidence. |

**Diagram caption:** Stage 3 evidence gate. Diagnostics and exact commands produce either workflow-valid green evidence or red evidence for tracing.

#### Steps

```text
[validation plan]
      |
      +--> ci_diagnostics(owned/touched production files)
      |
      +--> daytona_shell(exact required command first)
      |
      +--> daytona_shell(bounded guardrail, if planned)
      |
      v
[criteria result table]
      |
      +--> all green --> Stage 6 success
      +--> any red/invalid/absent --> Stage 4
```

| Evidence item | Compact rule |
| --- | --- |
| Diagnostics | Run `ci_diagnostics(file_path="...")` on every owned or touched production file before terminal completion. Error-severity diagnostics on owned files are red evidence unless explicitly pre-existing and irrelevant. |
| Exact command | Run the exact required runtime command first. Use `daytona_shell(command="...")` for shell, build, and test commands. |
| Working directory | Run daytona_shell from the sandbox repo root with repo-relative paths, or `cd frontend/web && ...` for a subdirectory. Never prefix commands with `cd /testbed &&`, and never `cd` to a host/local workspace path. |
| Exit judgment | Judge pass/fail by exit code and failing ids. Pytest exit `4`, `0` collected items, or a missing named node is red evidence. |
| Invalid overrides | Warning suppression, plugin disabling, or pytest-config overrides are invalid evidence unless the task owns pytest config. Re-run the raw command, repair in-scope production import/config, or request replanning. |
| Policy block | If policy blocks the command, request replanning with trigger `unresolved_blocker` only when no valid equivalent can preserve the needed evidence. |
| Evidence packet | Capture exact command, exit code, failing ids, diagnostics, and the shortest useful output snippet. |

### 4. Analyze red evidence

| Section | Contract |
| --- | --- |
| **Input** | Red, invalid, partial, unmet, or absent evidence from Stage 3. |
| **Output** | One root-cause packet and either a scoped correction target or a terminal replan summary. |
| **Forbidden** | Treating symptoms as root causes; correcting outside validator scope; repeated validator repairs without a new local defect. |

**Diagram caption:** Stage 4 trace route. Preserve the failure, identify the first wrong production mechanism, and choose local correction only when the boundary is proven.

#### Steps

```text
[red evidence]
      |
      v
[exact failure + exit + ids + snippet]
      |
      v
[boundary: local | handoff | outside scope | tooling | unclear]
      |
      +--> obvious local correction --> Stage 5
      |
      +--> anything else -------------> Stage 6 request_replan
```

Build one root-cause packet:

```json
{
  "failing_command_or_probe": "exact command/probe and exit code",
  "failing_test_diagnostic_or_error": "test id, diagnostic id, exception, import error, warning, or assertion",
  "expected_vs_actual": "what the criterion expected and what the repo produced",
  "boundary": "owned local surface | dependency handoff | outside scope | environment/tooling | unclear",
  "trace": ["verification entry", "production call/import/config path", "first wrong value, branch, state, or API result"],
  "hypothesized_root_cause": "specific code defect or trace gap",
  "candidate_fix": "file and symbol if local, otherwise replanner decision needed",
  "next_action": "apply one scoped correction | request_replan"
}
```

Rules:

1. A failing id, assertion mismatch, import error, or wrong value is a symptom, not a root cause.
2. A valid local correction needs evidence for the exact file, symbol, statement, branch, config lookup, import target, state transition, or serializer that first creates the wrong result.
3. Request replanning when the trace points outside owned scope, crosses into another role, requires broad design, would edit tests not explicitly owned, depends on missing handoff context, or remains ambiguous.
4. Stop cycling if the same command stays red after one validator correction and the trace does not identify a new local defect.

### 5. Apply one scoped correction

| Section | Contract |
| --- | --- |
| **Input** | Stage 4 root-cause packet with an obvious local correction target. |
| **Output** | One scoped correction and fresh verification evidence, or a terminal replan summary if the correction is not allowed. |
| **Forbidden** | Broad refactors, speculative owner changes, test edits, pytest config changes, environment workarounds, shell edits, or bypassing mutation-tool scope warnings. |

**Diagram caption:** Stage 5 correction gate. One validator mutation is allowed, then the same diagnostics and verification path must be refreshed.

#### Steps

```text
[obvious local correction]
      |
      v
[inside scope or proven new production file?]
      |
      +--> no --> request_replan
      |
      v
[one Daytona mutation]
      |
      v
[refresh notes + diagnostics + same verification] -> Stage 3
```

| Correction gate | Compact rule |
| --- | --- |
| Existing files | Before every mutation, verify the target file is inside an assigned `scope_paths` entry or a touched production file handed off by a dependency. |
| New files | For a new production file required by live evidence, use `daytona_write_file` and let the write-scope posthook approve and record expansion. |
| Scope warning | If an existing-file mutation is outside scope or the posthook blocks expansion, call `request_replan` with trigger `scope_expansion`. |
| Mutation tool | Use only `daytona_edit_file` or `daytona_write_file`; exactly one mutation tool per change. |
| Freshness | Refresh file notes after edits or surprising tool/runtime results. |
| Tests | Never create or edit test files. |
| Advisory warnings | If a mutation reports an outside-scope warning for an existing file, stop immediately; an advisory warning is workflow evidence, not permission to continue editing. |
| Reverify | Re-run `ci_diagnostics` and the same owned verification surface after the correction. |

### 6. Submit terminal summary

| Section | Contract |
| --- | --- |
| **Input** | Green Stage 3 evidence, or a Stage 4/5 trace and replan decision. |
| **Output** | Exactly one terminal `submit_task_success(...)` or `request_replan(...)` call. |
| **Forbidden** | Any later tool call; success with nonzero, missing, stale, partial, invalid, outside-scope, or diagnostics-only evidence. |

**Diagram caption:** Stage 6 terminal gate. Success is only for fresh green evidence mapped to every criterion; all other states request replanning.

#### Steps

```text
[terminal decision]
      |
      +--> every criterion has workflow-valid green evidence
      |       |
      |       v
      |   submit_task_success({ summary })
      |
      +--> any red / invalid / missing / stale / partial criterion
              |
              v
          request_replan({ reason })
```

Final action must be exactly one of:

```ts
submit_task_success({ summary: string })
// or
request_replan({ reason: string })
```

The `summary` (success) or `reason` (replan) field is the entire terminal payload.

Success checklist. Do not omit a line because the answer is "none":

| Required line | Must show |
| --- | --- |
| Acceptance criteria | Each criterion mapped to pass evidence. |
| Verification | Exact final commands or probes and observed outcomes. |
| Exit evidence | Exit codes or key assertions for every cited command or probe. |
| Diagnostics | Owned-file diagnostics status. |
| Guardrail | Public-surface guardrail result, or "none" if no guardrail was planned. |
| Widening rationale | Investigation or guardrail widening rationale, or "none". |
| Residual risk | `Residual risk:` plus the remaining validation caveat, follow-up risk, or "none". |

Request-replan checklist:

| Required line | Must show |
| --- | --- |
| Trigger | Exactly one of `scope_expansion`, `wrong_owner_or_role`, or `unresolved_blocker`. |
| Root-cause packet | Stage 4 packet embedded verbatim inside `content`. |
| Failing evidence | Exact failing command, diagnostic, or probe and its exit code. |
| Failing ids | Test ids, diagnostic ids, or "none available". |
| Output snippet | Shortest useful output and minimal reproduction. |
| Replanner decision | Owner, scope, sequence, or design issue the replanner must resolve. |

Use `scope_expansion` when the verified repair is outside the assigned `scope_paths`. Use `wrong_owner_or_role` when another agent role, dependency, or production owner must act before validation can pass. Use `unresolved_blocker` when verification, diagnostics, tooling, or root-cause tracing is still blocked but no different owner/scope is proven.

Call `submit_task_success` only when the latest required verification passed and every acceptance criterion is satisfied by workflow-valid evidence. Call `request_replan` for any nonzero command, error diagnostic, invalid command, pytest-config-overridden command, missing command, collection failure, partial pass, unmet criterion, ambiguous root cause, outside-scope fix, non-local repair, stale evidence, or summary that would otherwise say "partial".
