# Verification — Deferred goal handoff + nested depth 2 + planner@depth-2 cannot defer

Area key: `deferred_goal_depth` (domain: agent-core)
Verifier independently re-derived from source. Python = ground truth; Rust under audit.
Reviewed file: `docs/reviews/rust_parity/areas/deferred_goal_depth.md`.

---

## Invariant verdict table

| # | Invariant | independent_status | severity | decisive bilateral anchor |
|---|---|---|---|---|
| 1 | An iteration can end with a deferred goal that hands off to the next iteration | **investigator_missed** (happy-path match; failure-compensation diverges) | medium | PY `lifecycle.py:122-147` + `_start_deferred_iteration` 207-231 (try/except → cancel next iteration, deregister next coordinator, close workflow FAILED; `finally` deregister) vs RUST `lifecycle.rs:164-184` (continuation = bare `create_and_start_first_attempt().await.map(|_|())`, NO compensation; `deregister` at :183 skipped by `?` early-return at :168) |
| 2 | Nesting depth "capped at 2" (delegate_workflow at/beyond depth 2 rejected) | confirmed_disparity (checklist wording wrong; NO hard cap in either impl) | medium | PY `starter.py:59-133`, `delegate_workflow.py:48-124` (no depth compare) vs RUST `starter.rs:46-114`, `model_tools/workflow.rs` (no depth compare). Doc `lifecycle.html:249-250` keeps generator delegation when nested |
| 3 | Planner at depth 2 CANNOT submit a deferred goal — explicitly enforced | confirmed_disparity (partial port; fail-OPEN vs fail-CLOSED + not wired into a live planner path) | high | PY `disallow_nested_planner_deferral.py:38-50` (context-resolve error ⇒ `HookResult.fail`, DENY) vs RUST `hooks.rs:611-616` (`workflow_id`/`workflow_control` None ⇒ `pass()`, ALLOW); RUST `agent_runner.rs:71` `workflow_control: None`, `tool_context.rs:81` `plan_submission: None` |
| 4 | Depth tracked + propagated through delegate_workflow nesting | confirmed_disparity (low; behaviorally equivalent for the sole consumer) | low | PY `workflow_depth.py:10-49` (multi-hop walk + cycle guard, `>1`) vs RUST `ports.rs:228-236` (single-hop `parent.workflow_id.is_some()`) |

Tally: 0 confirmed_match, 3 confirmed_disparity, 1 investigator_missed.

---

## Disparity adjudication

### D1 (invariant 3) — CONFIRMED (high)
Independently reproduced every load-bearing anchor:
- RUST `hooks.rs:614-615`: `let (Some(workflow_id), Some(control)) = (&ctx.workflow_id, &ctx.workflow_control) else { return Ok(HookOutcome::pass()); };` — unset context ⇒ deferral ALLOWED (fail-OPEN).
- PY `disallow_nested_planner_deferral.py:38-41`: on `AttemptSubmissionContextError` returns `HookResult.fail(...)` ⇒ DENY (fail-CLOSED). I traced `resolve_attempt_submission_context` → `context/attempt.py:41-126`: it raises whenever runtime/task/attempt/iteration/workflow/orchestrator cannot be resolved, so "context cannot resolve" maps to a DENY. The two impls go in OPPOSITE directions on the unresolved-context branch — disparity is structural, not cosmetic.
- Non-wiring CONFIRMED: `agent_runner.rs:71` `workflow_control: None`; `tool_context.rs:21-23` doc comment ("None for workflow agents in Phase 6"); `tool_context.rs:81` `plan_submission: None`; `agent_runner.rs:1-13` module doc ("`plan_submission = None`, so the run never yields a typed terminal"). Root path `root_agent.rs:67-68` has `workflow_id: None` + `workflow_control: Some(...)`, and root is never nested (`is_nested_workflow` false; root `workflow_id == None`).
- Orchestrator is NOT a nesting backstop — CONFIRMED. RUST `apply_plan_submission` (`orchestrator.rs:386-401`) does `assert_submission_attempt` + `validate_run_concurrency` + kind↔defer match ONLY; no `is_nested` call anywhere in that method. So the `hooks.rs:599-601` comment ("the orchestrator still enforces on apply") is inaccurate with respect to NESTING. (It is accurate only for kind↔defer consistency.)
Adjudication: investigator's D1 is correct on all three sub-claims. Severity high upheld. Net effect today: a nested planner cannot defer only because nested planners do not execute a typed terminal yet; once wired, fail-open would silently ALLOW a nested deferral unless `workflow_control` is populated for planner contexts.

### D2 (invariant 2 emergent cap) — CONFIRMED (medium)
Re-read PY `starter.py:59-133` (validates nonblank prompt, request id, parent RUNNING, no open child — no depth compare) and `delegate_workflow.py:48-124` (only blocks a second outstanding workflow). RUST `starter.rs:46-114` mirrors exactly (trim/empty prompt, parent running, no open child). No `>= 2` / `> 1` / `MAX_DEPTH` guard on `delegate_workflow` on either side. Doc `lifecycle.html:249-250` confirms generator delegation stays available when nested. The "cap" is emergent from invariant 3, and a nested GENERATOR can structurally delegate to depth 3+. Investigator correct; this is a checklist-wording disparity, not a code disparity. Documentation-only fix.

### D3 (invariant 4 single-hop collapse) — CONFIRMED (low)
PY `workflow_depth.py:10-49` does the multi-hop ancestry walk with cycle guard (`:16-17`) and returns `> 1`. RUST `ports.rs:228-236` returns `parent.workflow_id.is_some()` (single hop). Equivalence rests on `attempt_id.is_some() ⟺ workflow_id.is_some()` for all system tasks: confirmed Rust-side that `Task.workflow_id`/`iteration_id`/`attempt_id` are all `Option`, all `None` only on root (`eos-state/src/task.rs`). The Python first-hop returns `depth` as soon as `parent_attempt_id` is empty (`:30-31`), so `> 1` is decided by whether the parent task has an attempt — equivalent to Rust's `parent.workflow_id.is_some()`. Lost cycle detection + multi-hop are unreachable for valid state. Boolean matches for all valid states; only a defensive error path (Python raises on cycle) is dropped. Investigator correct, low severity.

---

## New findings

### N1 — invariant 1 continuation failure-compensation diverges (the FALSE MATCH) — severity medium
Investigator marked invariant 1 `match` and wrote E5 asserting the Rust deregister "mirrors Python's `finally` ... Behavior matches." Re-deriving the FAILURE path refutes both:

- **No continuation-attempt compensation.** PY `_start_deferred_iteration` (`lifecycle.py:207-231`) wraps `create_and_start_first_attempt()` in try/except: on failure it logs, sets the next iteration `CANCELLED`, deregisters the next coordinator, and `close_workflow(succeeded=False)`. RUST `lifecycle.rs:169-172` is bare `coordinator.create_and_start_first_attempt().await.map(|_| ())` — error propagates with NO compensation. On a continuation-start failure the Rust impl leaves the next iteration OPEN, its coordinator REGISTERED, and the workflow OPEN — a stuck/leaked state Python explicitly avoids. The Rust codebase knows this pattern (`starter.rs:69 compensate_failed_start` for the INITIAL attempt) but the continuation path drops it. Confirmed via grep: `lifecycle.rs` has no `compensate`, no `_start_deferred_iteration`, no `Cancelled`/`set_status` cancel path, and only one `deregister` (`:183`).
- **deregister is NOT unconditional.** PY deregisters in a `finally` that always runs. RUST `handle_iteration_closed` early-returns via `?` at `lifecycle.rs:168` (`create_iteration_with_coordinator` error) BEFORE reaching `deregister` at `:183`. So the closing iteration's coordinator is NOT deregistered when next-iteration creation fails. This refutes E5's "unconditional" claim.

Decisive bilateral anchor: PY `lifecycle.py:132-147,207-231` vs RUST `lifecycle.rs:157-185` (full method re-read; no helper between `handle_iteration_closed` and `close_workflow`).
Net: invariant 1 ports the happy path faithfully (set deferred goal → close succeeded → signal → next iteration; `iteration/mod.rs:189-217` vs `attempt_coordinator.py:194-211` confirmed identical) but the deferred-handoff FAILURE path is materially divergent. Reclassify invariant 1 `match → investigator_missed`; downgrade E5 from "behavior matches" to "happy-path only; error-path diverges." Severity medium (reachable on continuation-attempt startup failure; produces a leaked open workflow/iteration vs Python's clean FAILED close).

### N2 — E2 "byte-identical" is overstated (immaterial)
Investigator E2 says the kind↔defer messages are "byte-identical." They differ in case: PY `orchestrator.py:113,117` "Full plans cannot..."/"Partial plans require..." vs RUST `orchestrator.rs:392,397` "full plans cannot..."/"partial plans require...". Cosmetic only; behavior (the WorkflowInvariantViolation raise on completes+deferred / defers+null) matches. E3 (BLOCKED message) IS genuinely byte-identical (PY `disallow_nested_planner_deferral.py:16-20` vs RUST `hooks.rs:456`) — confirmed.

### N3 — `is_bailout_submission` fail-open is a DIFFERENT hook (no impact on D1)
Confirmed RUST `hooks.rs:477-488 is_bailout_submission` and PY `require_no_inflight_background_tasks.py:122 _is_bailout_submission` are the daemon-unavailable fail-open path for `RequireNoInflightBackgroundTasks`, NOT for `DisallowNestedPlannerDeferral`. Investigator correctly kept these separate; D1's fail-open claim stands independently of bailout semantics.

---

## Overall verdict

The investigation is broadly sound and its headline finding (D1/invariant 3, high) is fully reproduced: the nested-planner-deferral guard is fail-OPEN in Rust vs fail-CLOSED in Python, is not wired into any live planner path (`workflow_control: None`, `plan_submission: None`), and the orchestrator does NOT backstop nesting (the `hooks.rs:599-601` comment is inaccurate). D2 (emergent cap, not a hard ceiling) and D3 (single-hop collapse, behaviorally equivalent, low) are also confirmed.

One FALSE MATCH was caught: **invariant 1**. The investigator marked it `match` and asserted (E5) that the Rust iteration deregister mirrors Python's `finally`. Re-deriving the FAILURE path shows Rust drops Python's continuation compensation (cancel next iteration + deregister next coordinator + close workflow FAILED) and skips the closing-iteration deregister on the `?` early-return. Reclassified `investigator_missed`, severity medium. Trivial overstatement N2 (E2 capitalization) noted.

No false alarms found — every flagged disparity (D1/D2/D3) survives independent re-derivation.

---

DONE deferred_goal_depth: 0 confirmed_match, 3 confirmed_disparity, 1 unproven=0; investigator_missed=YES (invariant 1 — continuation failure-compensation path diverges; E5 overstated).
