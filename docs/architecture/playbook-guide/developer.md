# Developer Playbook Guide

This guide governs developer-style playbooks under `backend/config/skills`,
especially the plan, root-cause analysis, and terminal decision flow for
`team-developer-playbook`.

## Developer Route

Developer playbooks should keep the lane bounded: plan from the handoff, repair
one traced mechanism, verify with fresh evidence, then choose exactly one
terminal outcome.

```text
Caption: developer route. Plan first, verify fresh evidence, and run required
RCA for every verification failure.

handoff + notes
  |
  v
[Plan boundary]
  |-- wrong owner / broad / blocked -------> request_replan(...)
  |
  v
[Implement one mechanism]
  |
  v
[Verify]
  |-- green + current + criteria met ------> submit_task_success(...)
  |
  v
[Required RCA]
  |-- one scoped production defect --------> implement next bounded fix
  `-- unclear / broad / stale / budget ----> request_replan(...)
```

## Plan

The plan stage should name the repair boundary before the first edit. Keep it as
a compact checklist, not a long narrative.

| Planning item | Expected content |
| --- | --- |
| Production owner | File/module/symbol or owned directory, plus why it belongs to this task. |
| Current vs intended behavior | The value, branch, import, config, state, or output that is wrong. |
| Edit boundary | One mechanism to change; adjacent files only when coupled by evidence. |
| Verification | Exact post-edit command plus diagnostics for edited files. |
| Replan check | Wrong owner, broad scope, missing evidence, dependency/env mutation, or budget risk. |

## Root Cause Analysis

RCA is required after every red, absent, or invalid verification result before
another edit or `request_replan`. The packet should be short enough for a
replanner to act on if the lane exits.

```text
Caption: RCA packet. Trace from command failure to the first wrong production
mechanism, not just the failing assertion.

failing command -> failing id/error -> expected vs actual
  -> production trace -> first wrong mechanism -> fix location
```

| Field | Purpose |
| --- | --- |
| Failing command | Exact command and exit code. |
| Failing signal | Test id, exception, diagnostic, import error, assertion, or unmet criterion. |
| Expected vs actual | Concrete wrong value, branch, state, symbol, output, or behavior. |
| Trace | Entry point through production calls/imports/config to the first wrong mechanism. |
| Fix location | File and symbol, or the unresolved owner gap. |
| Next action | Bounded fix, or `request_replan` with the blocker/scope reason. |

## Terminal Decision

```text
Caption: terminal decision. Success needs current direct evidence; everything
else returns to planning through replan.

latest required verification passed
  + edited-file diagnostics clean
  + criteria satisfied
      -> submit_task_success(...)

red / absent / stale / invalid / partial
  or broad / wrong-owner / budget-risk
      -> request_replan(...)
```

| Decision | Use when |
| --- | --- |
| `submit_task_success` | Latest required verification is green, diagnostics are clean, and every acceptance criterion has fresh evidence. |
| `request_replan` | Verification is red, absent, stale, invalid, partial, blocked, too broad, wrong-owner, or budget is insufficient for valid verification. |
| Another bounded fix | RCA names one assigned-scope production defect and there is enough budget to edit and verify. |

## Replan Triggers

Developers and validators should file replan instead of stretching a lane when
the work no longer fits the assigned boundary.

| Trigger | Replan reason |
| --- | --- |
| Concrete blocker with no valid local route | `unresolved_blocker` |
| Required owner or role is different | `wrong_owner_or_role` |
| Repair becomes broad or ambiguous | `scope_expansion` |
| Budget is nearly exhausted before valid verification | `unresolved_blocker` or `scope_expansion`, whichever describes the remaining work. |
| Multiple outside-scope edits are required | `scope_expansion` |

A few lightweight outside-scope production writes, moves, deletes, or creates
are acceptable only when live evidence ties them to the same mechanism. The
third outside-scope mutation, a blocked move/delete, or any broad/ambiguous
outside-scope change should replan.

## Playbook Evolution

| Change style | Rule |
| --- | --- |
| Net size | Prefer negative net change. Add text only when it removes ambiguity or repeated failures. |
| Format | Prefer diagrams and tables with captions over long prose. |
| Constraints | Use light constraints and decision gates; reserve hard rules for runtime invariants or safety. |
| Logic | Express workflows as stage flows that an LLM can follow without backtracking. |
| References | Check any companion `references/` files for drift before changing playbook behavior. |

## Review Checklist

| Check | Expected result |
| --- | --- |
| Developer plan | Guidance requires a production owner, edit boundary, exact verification, and pre-edit replan check. |
| Developer RCA | Every verification failure is traced to the first wrong production mechanism before another edit or replan. |
| Replan path | Blockers, budget exhaustion, and broad scope changes exit through `request_replan`. |
| Success path | `submit_task_success` requires current green verification and clean diagnostics. |
| Reference files | Companion `references/` files are checked, updated, split, or deleted when playbook behavior changes. |
| Simplification | The diff removes more ambiguity than it adds, preferably with negative net text. |
| Runtime contract | Terminal submission still uses `submit_task_success(...)` or `request_replan(...)`. |
