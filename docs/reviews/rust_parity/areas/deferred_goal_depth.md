# Rust parity audit — Deferred goal handoff + nested depth 2 + planner@depth-2 cannot defer

Area key: `deferred_goal_depth` (domain: agent-core)
Ground truth: Python `backend/src/workflow/**`, corroborated by `docs/architecture/workflow/{lifecycle,agent-roles}.html`.
Rust under audit: `agent-core/crates/eos-workflow/src/**` plus the cross-cutting hook in `agent-core/crates/eos-tools/src/hooks.rs` and its port in `agent-core/crates/eos-workflow/src/ports.rs`.

---

## Ground truth

### G1 — Deferred goal hands off to the next iteration
- A passing attempt may carry `deferred_goal_for_next_iteration`. The iteration coordinator denormalizes it onto the iteration row and signals close with that goal:
  - `backend/src/workflow/iteration/attempt_coordinator.py:194-211` `_close_iteration_passed` → `set_deferred_goal_for_next_iteration(...)`, `close_succeeded(...)`, then `on_iteration_closed(iteration_id, succeeded=True, deferred_goal=attempt.deferred_goal_for_next_iteration)`.
- The workflow lifecycle starts the NEXT iteration only when `succeeded and deferred_goal is not None`:
  - `backend/src/workflow/lifecycle.py:122-147` `handle_iteration_closed`; the continuation branch calls `create_iteration_with_coordinator(...)` then `_start_deferred_iteration(...)`.
- The continuation iteration's goal is the predecessor's deferred goal, with a guard:
  - `backend/src/workflow/lifecycle.py:98-113` (reason `DEFERRED_GOAL_CONTINUATION`, `iteration_goal = deferred_goal`).
  - Guard `assert_predecessor_has_deferred_goal_for_next_iteration` requires the predecessor to be `SUCCEEDED` AND to have non-null deferred text: `backend/src/workflow/_core/invariants.py:48-58`.
- Docs corroboration: `docs/architecture/workflow/lifecycle.html:122` ("A passed Attempt can either terminally close the Workflow or carry `deferred_goal_for_next_iteration`, which closes the current Iteration as succeeded and starts the next Iteration under the same Workflow.") and `:158` (deferral gate).

### G2 — "Cap at depth 2": there is NO hard cap that rejects `delegate_workflow`
- `WorkflowStarter.start` performs NO depth check. It validates only: nonblank prompt, request id present, parent running, no open child workflow. `backend/src/workflow/starter.py:59-133`.
- `delegate_workflow` tool performs NO depth check either; it only blocks a second outstanding workflow for the same parent and forwards to `WorkflowStarter.start`. `backend/src/tools/workflow/delegate_workflow.py:48-124`.
- Depth is consumed ONLY by `is_nested_workflow` (see G3). `workflow_depth` integer has exactly one non-test consumer (`is_nested_workflow`): `backend/src/workflow/_core/workflow_depth.py:46-49` and grep over `backend/src` shows no other caller.
- Therefore the "cap" is EMERGENT, not enforced: a nested (depth-2) planner cannot defer, so its iteration chain cannot extend; but a nested GENERATOR may still call `delegate_workflow`, structurally allowing depth 3+.
  - Docs make this explicit — `docs/architecture/workflow/lifecycle.html:249-250`: "A workflow launched by the root task is the first workflow depth. A workflow launched by a workflow generator is nested. Nested planning disables deferred planner submissions; generator delegation remains available but is terminal-gated through workflow handles."

### G3 — Planner at depth 2 CANNOT defer (the enforced invariant)
- Pre-hook `DisallowNestedPlannerDeferral` rejects a nonblank `deferred_goal_for_next_iteration` on `submit_planner_outcome` when the submission's workflow is nested:
  - `backend/src/tools/_hooks/disallow_nested_planner_deferral.py:30-50`.
  - Fail behavior: if a nonblank deferred goal is present AND `resolve_attempt_submission_context` RAISES, Python returns `HookResult.fail(str(exc), ...)` (FAIL-CLOSED): `disallow_nested_planner_deferral.py:38-41`.
  - Blocked message constant: `backend/src/tools/_hooks/disallow_nested_planner_deferral.py:16-20` ("BLOCKED: nested workflow planners cannot set deferred_goal_for_next_iteration...").
- "Nested" = `workflow_depth(...) > 1`: `backend/src/workflow/_core/workflow_depth.py:46-49` (operator `>`, literal `1`).
- A complementary launch-time reminder fires the same nesting predicate: `backend/src/tools/submission/notification_triggers/nested_planner_deferral_disabled.py` + `_workflow_depth.py`.
- The attempt orchestrator separately enforces kind↔defer CONSISTENCY (not nesting):
  - `backend/src/workflow/attempt/orchestrator.py:106-118`: `completes` + non-null deferred → reject; `defers` + null deferred → reject.

### G4 — Depth tracking + propagation
- No depth counter is stored anywhere. `workflow_depth` is reconstructed on demand by walking ancestry via `Workflow.parent_task_id` → parent task → parent attempt → parent iteration → `parent_iteration.workflow_id`, counting workflow ancestors and detecting cycles:
  - `backend/src/workflow/_core/workflow_depth.py:10-43` (cycle guard at `:16-17`; loop walks up at `:21-43`).
- `is_nested_workflow = depth > 1` (`:49`). Because the loop returns `depth` as soon as the parent task has no `attempt_id` (`:30-31`), the predicate `> 1` is decided ENTIRELY at the first hop: `depth` reaches 2 the instant the workflow's parent task has a non-empty `attempt_id`. The deeper walk only increments a count that `is_nested_workflow` never reads.

---

## Rust mapping

| Concern | Rust anchor |
|---|---|
| Deferred goal close signal | `agent-core/crates/eos-workflow/src/iteration/mod.rs:189-217` (`close_iteration_passed`) |
| Next-iteration-on-defer | `agent-core/crates/eos-workflow/src/lifecycle.rs:157-185` (`handle_iteration_closed`), continuation `:164-172` |
| Continuation goal + SUCCEEDED/defer guard | `agent-core/crates/eos-workflow/src/lifecycle.rs:88-122` |
| `delegate_workflow` start (no depth cap) | `agent-core/crates/eos-workflow/src/starter.rs:46-114`; tool `agent-core/crates/eos-tools/src/model_tools/workflow.rs:79` |
| kind↔defer consistency | `agent-core/crates/eos-workflow/src/attempt/orchestrator.rs:389-401` |
| nested-no-defer hook | `agent-core/crates/eos-tools/src/hooks.rs:599-625` (`run_disallow_nested_planner_deferral`); message const `:456` |
| hook registration on planner terminal | `agent-core/crates/eos-tools/src/meta.rs:80-84` |
| `is_nested_workflow` port impl | `agent-core/crates/eos-workflow/src/ports.rs:228-236` |
| metadata wiring of the port | `agent-core/crates/eos-runtime/src/tool_context.rs:21-23, 80-81`; `root_agent.rs:68`; `agent_runner.rs:71` |

---

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|---|---|---|---|---|---|
| 1 | An iteration can end with a deferred goal that hands off to the next iteration | match | none | `iteration/attempt_coordinator.py:194-211`; `lifecycle.py:122-147` | `iteration/mod.rs:189-217`; `lifecycle.rs:157-185` | Faithful port: set deferred goal → close succeeded → signal → next iteration. |
| 2 | Nesting depth capped at 2 (delegate_workflow at/beyond depth 2 rejected) | divergent | medium | `starter.py:59-133`; `workflow_depth.py:46-49`; doc `lifecycle.html:249-250` | `starter.rs:46-114`; `model_tools/workflow.rs:79` | Three-way disagreement: NO hard cap in EITHER impl. "Cap" is emergent from invariant 3 only; depth-3 generator delegation is structurally allowed. Checklist wording is inaccurate vs ground truth. |
| 3 | Planner at depth 2 CANNOT submit a deferred goal — explicitly enforced | partial | high | `disallow_nested_planner_deferral.py:30-50`; `workflow_depth.py:49` | `hooks.rs:599-625`; `ports.rs:228-236`; `tool_context.rs:21-23,81` | Hook + port exist and are unit-tested, but NOT wired into a live planner path: `workflow_control=None` and `plan_submission=None` for workflow agents (Phase-6 "nested delegation deferred"). Hook also fail-OPENS where Python fails-CLOSED. |
| 4 | Depth tracked + propagated correctly through delegate_workflow nesting | divergent | low | `workflow_depth.py:10-49` (full walk + cycle guard) | `ports.rs:228-236` (single-hop `parent.workflow_id.is_some()`) | No stored counter on either side. Rust collapses the ancestry walk to a one-hop parent check — behaviorally equivalent for the only consumer (`>1`), but drops the multi-hop walk and cycle detection. |

---

## Disparities

### D1 — Invariant 3 enforcement is present in the library but NOT reachable end-to-end; and fail-OPEN vs Python fail-CLOSED (severity: high, status: partial)

Evidence:
- Rust `run_disallow_nested_planner_deferral` (`agent-core/crates/eos-tools/src/hooks.rs:614-616`):
  ```rust
  let (Some(workflow_id), Some(control)) = (&ctx.workflow_id, &ctx.workflow_control) else {
      return Ok(HookOutcome::pass());   // unset context => deferral ALLOWED
  };
  ```
- Python `disallow_nested_planner_deferral.py:38-46` does the OPPOSITE on the failure branch: if the deferred goal is nonblank and the submission context cannot resolve, it returns `HookResult.fail(...)` (deny), not pass.
- The Rust metadata builder hard-codes the port to `None` for every non-root agent:
  - `agent-core/crates/eos-runtime/src/tool_context.rs:21-23`: "`workflow_control`: ... `None` for workflow agents in Phase 6 (nested delegation is deferred)."
  - `agent-core/crates/eos-runtime/src/tool_context.rs:81`: `plan_submission: None`.
  - `agent-core/crates/eos-runtime/src/agent_runner.rs:71`: `workflow_control: None`; and `agent_runner.rs:10`: "`plan_submission = None`, so the run never yields a typed terminal."
  - Only the root agent gets `workflow_control: Some(...)` (`root_agent.rs:68`), and the root is never nested (`is_nested_workflow` returns false; root task `workflow_id = None`), and root does not call `submit_planner_outcome`.
- The orchestrator is NOT a backstop. The Rust comment at `hooks.rs:599-601` claims "the orchestrator still enforces on apply," but `apply_plan_submission` (`orchestrator.rs:389-401`) only enforces kind↔defer consistency, NOT nesting — identical to Python `orchestrator.py:106-118`. So the hook is the SOLE nesting enforcement on both sides; the justifying comment is inaccurate.
- Additionally, the `eos-workflow` agent seam (`attempt/launch.rs:19-39` `AgentTerminal`/`AgentRunReport`) routes planner terminals as a typed `PlannerPlan` directly into `apply_plan_submission`, bypassing the `eos-tools` hook pipeline entirely. So even when workflow agents are wired later, the hook only fires if those agents execute through the `eos-tools` dispatch hooks.

Why it matters: Invariant 3 is the OWNER invariant for this area and the only mechanism that bounds nesting (invariant 2 is emergent from it). In the current Rust runtime a nested planner cannot defer simply because nested planners do not run yet; once they do, the fail-open fallback means a nested planner whose context lacks `workflow_control` would be ALLOWED to defer, diverging from Python's fail-closed guard and silently extending a nested iteration chain.

Suggested fix:
1. When workflow agents are wired (post Phase-6), populate `workflow_control` (and `plan_submission`) for planner/generator/reducer contexts in `tool_context.rs`, OR enforce nesting inside `apply_plan_submission` so it is not dependent on hook wiring.
2. Make `run_disallow_nested_planner_deferral` fail-CLOSED to match Python: when a nonblank deferred goal is set but `workflow_id`/`workflow_control` is missing, return a deny/fault rather than `pass()`.
3. Correct the `hooks.rs:599-601` comment — the orchestrator does NOT enforce nesting on apply.

### D2 — "Cap at depth 2" is emergent, not enforced, in BOTH impls — checklist wording diverges from ground truth (severity: medium, status: divergent)

Evidence: `starter.py:59-133` and `delegate_workflow.py:48-124` (Python) and `starter.rs:46-114` and `model_tools/workflow.rs:79` (Rust) contain no depth comparison. Docs `lifecycle.html:249-250` explicitly keep generator delegation available when nested. There is no literal `>= 2`, `> 1`, or `MAX_DEPTH` constant guarding `delegate_workflow` on either side.

Why it matters: The invariant as worded ("delegate_workflow at/beyond depth 2 is rejected") does not exist as a hard rule. A nested generator at depth 2 can delegate to depth 3, etc. The only thing that stops UNBOUNDED iteration deferral is invariant 3 (no nested defer). Treating invariant 2 as a "match" would hide that there is no structural delegation-depth ceiling.

Suggested fix: Documentation/spec only — restate invariant 2 as "delegate at depth >= 2 is permitted; what is capped is deferred-goal continuation, which is disabled for nested planners (invariant 3)." No code change implied unless a true delegation-depth ceiling is intended (then it must be added to BOTH `starter.py`/`starter.rs`).

### D3 — `is_nested_workflow` collapses the ancestry walk + cycle detection to a single-hop parent check (severity: low, status: divergent)

Evidence:
- Python `workflow_depth.py:10-49`: multi-hop loop with cycle guard (`:16-17`) returning `depth > 1`.
- Rust `ports.rs:228-236`: `is_nested_workflow` = "does the workflow's parent task have `workflow_id.is_some()`."

Equivalence analysis (why this is divergent-but-not-a-bug):
- Python `> 1` is decided at the first hop: `depth` becomes 2 the instant the workflow's parent task has a non-empty `attempt_id` (`workflow_depth.py:29-31`); the deeper walk only increments a count `is_nested_workflow` never reads.
- For every system-created task, `attempt_id.is_some() ⟺ workflow_id.is_some()`: both are set together when the orchestrator upserts planner/generator/reducer tasks (`orchestrator.py:86-98` sets `workflow_id`, `iteration_id`, `attempt_id` together) and both are `None` only on the root task (`task.py:45-47`; Rust `task.rs:78,84`). So `parent.workflow_id.is_some()` (Rust) ≡ `parent.attempt_id non-empty` (Python first-hop) ≡ `depth > 1`.
- The lost cycle detection and multi-hop walk are reachable only in already-nested or corrupt-ancestry states and never flip the boolean for valid system state.

Why it matters: Low. The boolean result matches for all valid states. The divergence is a lost defensive error path (Python raises `WorkflowInvariantViolation` on a cycle; Rust silently returns a boolean) and a different (cheaper) algorithm.

Suggested fix: None required. Optionally document that the Rust check is a deliberate single-hop simplification of Python's ancestry walk, and that cycle detection is intentionally dropped.

---

## Extra findings

- E1 — Deferred-goal continuation guard parity is correct: Rust `lifecycle.rs:102-116` checks BOTH `previous.status == Succeeded` AND `deferred_goal_for_next_iteration` present, matching Python's two-clause `assert_predecessor_has_deferred_goal_for_next_iteration` (`invariants.py:48-58`).
- E2 — kind↔defer consistency messages are byte-identical: Rust `orchestrator.rs:391-398` ("full plans cannot set deferred_goal_for_next_iteration" / "partial plans require deferred_goal_for_next_iteration") match Python `orchestrator.py:112-117`.
- E3 — Nested-planner BLOCKED message is byte-identical: Rust `hooks.rs:456` vs Python `disallow_nested_planner_deferral.py:16-20`; metadata `reason: "nested_workflow"` and policy `"nested_planner_deferral"` also match (`hooks.rs:619-620` vs Python `:48-50`).
- E4 — Hook ordering parity on `submit_planner_outcome`: Rust `meta.rs:80-84` orders `RequireNoInflightBackgroundTasks` → `DisallowNestedPlannerDeferral` → `AdvisorApproval`. This matches Python's intent (no-inflight before deferral policy before advisor). Worth a confirming check against Python's planner terminal hook registry if strict ordering parity is required.
- E5 — `handle_iteration_closed` ordering: Rust `lifecycle.rs:183` deregisters the coordinator AFTER computing the close result but unconditionally (mirrors Python's `finally` deregister at `lifecycle.py:146-147`). Behavior matches.
- E6 — Workflow agents (planner/generator/reducer) do not execute through the live `eos-runtime` agent loop yet; the workflow `AttemptOrchestrator` consumes a typed `AgentTerminal` from an injected `AgentRunner` (`attempt/launch.rs:19-39`), driven by a `QueueRunner` stub in tests (`testsupport.rs`). This is an INTENTIONAL migration staging (Phase-6 "nested delegation deferred"), not a bug — but it is the reason invariant 3 is currently only library-level.

---

## Open questions

1. Is a true hard delegation-depth ceiling intended at all (the checklist implies depth-2 rejection of `delegate_workflow`), or is the ground-truth "emergent cap via no-nested-defer" the intended final design? Ground truth (Python + docs) clearly says the latter; the checklist wording suggests someone expected the former.
2. When workflow-agent execution is wired into `eos-runtime` (post Phase 6), will the nesting guard be enforced via the `eos-tools` hook pipeline (requires populating `workflow_control` for planner contexts) or moved into `apply_plan_submission`? The current `hooks.rs:599-601` comment assumes the latter, but the code does not do it.
3. Should the Rust hook be changed to fail-closed now (matching Python) even though the path is currently unreachable, to prevent a latent silent-allow once nested planners run?
