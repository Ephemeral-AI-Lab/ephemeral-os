# Rust parity audit — Workflow lifecycle (workflow → iteration → attempt creation rules, delegate_workflow)

Domain: agent-core. Reviewed against Python ground truth + `docs/architecture/workflow/lifecycle.html`.

## Ground truth

The durable model is **Workflow → Iteration → Attempt**, all launched from a *running* Task. Key anchors:

- `delegate_workflow` is a **non-terminal** tool (`is_terminal_tool=False`) that calls `WorkflowStarter.start(prompt=goal, parent_task_id=current_task_id)` and returns a workflow handle; the parent Task keeps running.
  - `backend/src/tools/workflow/delegate_workflow.py:37-47` (`is_terminal_tool=False`), `:83-87` (`WorkflowStarter(runtime=runtime).start(...)`).
- `WorkflowStarter.start(*, prompt, parent_task_id)` — strips/validates prompt; asserts parent **RUNNING** and **no open delegated child**; creates Workflow row, Iteration 1 + coordinator, first Attempt; never mutates parent. `backend/src/workflow/starter.py:59-99`, guard `:116-133`, compensation `:135-181`.
- Iteration creation: first iteration is `sequence_no=1`, `IterationCreationReason.INITIAL`, goal = `workflow.workflow_goal`; a later iteration is `previous.sequence_no+1`, `DEFERRED_GOAL_CONTINUATION`, goal = predecessor's `deferred_goal_for_next_iteration`, and the predecessor must be **SUCCEEDED** with non-null deferred text. `backend/src/workflow/lifecycle.py:82-120`; invariants `backend/src/workflow/_core/invariants.py:40-58`.
- Iteration is created when the workflow is initialized (starter) OR on a **deferred-goal handoff** (`handle_iteration_closed` when `succeeded and deferred_goal is not None`). `backend/src/workflow/lifecycle.py:122-147`.
- Attempt creation: `previous_attempt_id=None` → first attempt (`sequence_no=1`, rejected if any attempt exists); with `previous_attempt_id` → retry requiring remaining budget and `latest_attempt_id == previous_attempt_id`, `sequence_no = attempt_count+1`. `backend/src/workflow/iteration/attempt_coordinator.py:78-111`.
- Attempt is created when the iteration is initialized OR when the previous attempt ends **FAILED** with budget remaining (`_retry_or_close_failed`). `backend/src/workflow/iteration/attempt_coordinator.py:219-244`.
- Close routing: passing attempt with no deferred goal → workflow SUCCEEDED; passing + deferred goal → next iteration; failed/exhausted → workflow FAILED. `close_workflow` persists the latest-iteration projection on the Workflow row and performs **zero** parent-task writes. `backend/src/workflow/lifecycle.py:149-166`, `_core/outcomes.py:130-138` (`workflow_outcomes` = latest iteration by `sequence_no`).
- Default attempt budget = **2** (`WorkflowLifecycleConfig.default_attempt_budget`). `backend/src/workflow/_core/primitives.py:39-47`.
- `has_budget_remaining` uses strict `<`: `attempt_count < attempt_budget`. `backend/src/workflow/_core/state.py:108-110`.
- Inspect/cancel via `check_workflow_status` / `cancel_workflow` (both non-terminal); cancel tears down child state (tasks FAILED with `{"fail_reason":"workflow_cancelled"}`, attempt FAILED `TASK_FAILED`, iteration + workflow CANCELLED) and does not mutate the parent. `backend/src/tools/workflow/check_workflow_status.py:28-35`, `cancel_workflow.py:27-34` & `:65-66`, `_runtime.py:118-177`.

## Rust mapping

| Concern | Rust anchor |
|---|---|
| `WorkflowLifecycle` create/iterate/close | `agent-core/crates/eos-workflow/src/lifecycle.rs` |
| `WorkflowStarter.start` + compensation | `agent-core/crates/eos-workflow/src/starter.rs` |
| `IterationAttemptCoordinator` create/retry/close | `agent-core/crates/eos-workflow/src/iteration/mod.rs` |
| ids + `WorkflowLifecycleConfig{default_attempt_budget}` | `agent-core/crates/eos-workflow/src/ids.rs` |
| `WorkflowControlPort` adapter (status/cancel/find_outstanding/is_nested) | `agent-core/crates/eos-workflow/src/ports.rs` |
| `delegate_workflow` / `check_workflow_status` / `cancel_workflow` tools | `agent-core/crates/eos-tools/src/model_tools/workflow.rs` |
| budget/`has_budget_remaining`, `latest_iteration` | `agent-core/crates/eos-state/src/iteration.rs`, `src/outcomes.rs` |

Note: there are **no `// PORT backend/...` comments** in `eos-workflow/src/`; mapping was done by structure + grep.

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|---|---|---|---|---|---|
| 1 | `delegate_workflow` is NON-terminal; parent Task keeps running (no synthetic root workflow / waiting status) | match | — | `tools/workflow/delegate_workflow.py:46` (`is_terminal_tool=False`) | `eos-tools/src/model_tools/workflow.rs:181-192` (registered `OutputShape::Text`, non-terminal executor); `eos-workflow/src/starter.rs:182-196` test asserts parent byte-identical | Rust tool never marks terminal; no parent mutation in `start`. |
| 2 | `WorkflowStarter.start(prompt, parent_task_id)` creates delegated state and leaves parent running | match | — | `workflow/starter.py:59-99` | `eos-workflow/src/starter.rs:46-81` | Same order: assert parent → create workflow → iteration+coordinator → first attempt. |
| 3 | Iteration created on init OR on deferred-goal handoff | partial | high | `workflow/lifecycle.py:94-120, 122-147, 207-231` | `eos-workflow/src/lifecycle.rs:88-122, 157-185` | Happy-path creation matches (initial seq=1/INITIAL/goal; continuation seq+1/DEFERRED_GOAL_CONTINUATION/deferred goal; handoff only when `succeeded && deferred_goal.is_some()`). **But the continuation start-failure compensation is missing — see D5.** |
| 4 | Attempt created on iteration init OR after previous attempt FAILED (budget remaining) | match | — | `workflow/iteration/attempt_coordinator.py:78-111, 219-244` | `eos-workflow/src/iteration/mod.rs:66-115, 219-236` | Retry loop folds startup-failed retry back into the decision (`latest_failed_attempt_after`). |
| 5 | Inspect/cancel via check/cancel tools; agent submits own terminal outcome; no close-time parent mutation, no legacy delegation-link column | match | — | `tools/workflow/check_workflow_status.py`, `cancel_workflow.py`; `lifecycle.py:149-166` | `eos-tools/src/model_tools/workflow.rs:108-179`; `eos-workflow/src/lifecycle.rs:188-216`; test `close_does_not_touch_parent` `lifecycle.rs:252-290` | `parent_task_id` is the only backward link (`eos-state/src/workflow.rs`); no mutation at close. |
| C1 | Default attempt budget = 2 | match | — | `_core/primitives.py:47` (`default_attempt_budget: int = 2`) | `eos-workflow/src/ids.rs:12-18` (`default_attempt_budget: 2`) | Literal match. |
| C2 | Budget check operator is strict `<` (`attempt_count < attempt_budget`) | match | — | `_core/state.py:110` | `eos-state/src/iteration.rs` `has_budget_remaining` (`attempt_count() < attempt_budget`) | Same strict `<`. |
| C3 | First-attempt sequence = 1; retry sequence = `attempt_count+1` | match | — | `attempt_coordinator.py:98,107` | `iteration/mod.rs:92,100` | `attempt_count() as i64 + 1` vs `1`. |
| C4 | Iteration contiguity: `new_seq == len(iteration_ids)+1` | match | — | `invariants.py:40-45`, `lifecycle.py:114` | `lifecycle.rs:123-128` (`expected = iteration_ids.len()+1`) | Equivalent guard inlined. |
| C5 | `workflow.outcomes` = latest iteration by `sequence_no` | match | low | `_core/outcomes.py:130-138` (`max(..., key=sequence_no)`) | `lifecycle.rs:227-239` (`max_by_key(sequence_no)`) | Selection matches; serialization differs (see D2). |
| C6 | Compensation saga: attempt STARTUP_FAILED → iteration CANCELLED → workflow CANCELLED → deregister | match | — | `starter.py:135-181` | `starter.rs:116-161`; test `compensation_rolls_back` `:263-302` | Same order; parent untouched. |
| C7 | `is_nested_workflow` gate for nested-planner-deferral hook | divergent | low | `_core/workflow_depth.py:10-49` (`workflow_depth(...) > 1`, walks full ancestry) | `eos-workflow/src/ports.rs:228-236` (`parent.workflow_id.is_some()`, single hop) | Same boolean for well-formed trees; differs on error handling (see D1). |
| C8 | `delegate_workflow` short-circuits when an outstanding workflow already exists | partial | medium | `delegate_workflow.py:62-81` (returns `is_error=True`) | `eos-tools/src/model_tools/workflow.rs:67-77` (returns `ToolResult::ok`, NOT error) | Behavioral flag divergence (see D3). |

## Disparities

### D1 — `is_nested_workflow` uses a 1-hop check instead of full ancestry-depth walk (divergent, low)
- Python `workflow_depth` (`_core/workflow_depth.py:10-43`) walks the chain workflow → parent_task → parent_attempt → parent_iteration → parent_workflow, raising `WorkflowInvariantViolation` on a cycle or any missing row, and `is_nested_workflow = depth > 1` (`:46-49`).
- Rust `WorkflowControlAdapter::is_nested_workflow` (`ports.rs:228-236`) just loads the workflow's parent task and returns `parent.workflow_id.is_some()`.
- **Why it matters:** for a well-formed tree the boolean is identical (a parent task that belongs to a workflow ⇔ depth>1), so the **hook decision is preserved**. But the Rust version (a) does not detect ancestry cycles, (b) silently returns `false` on missing parent/workflow rows where Python raises, and (c) is purely structural — it never confirms the parent task actually has a live attempt/iteration. This is an intentional simplification, not a functional regression of the deferral gate, but it loses the defensive cycle/integrity checks.
- **Suggested fix:** acceptable as-is for the gate; if integrity parity matters, port the chain walk (or document the 1-hop equivalence in a code comment). Severity low.

### D2 — `close_workflow` outcomes JSON is passed through raw rather than re-normalized (divergent, low)
- Python `WorkflowLifecycle.close_workflow` writes `records_json(workflow_outcomes(...))` (`lifecycle.py:157-159`), i.e. it **parses** the latest iteration's `outcomes` into typed `ExecutionTaskOutcome`s and **re-serializes** the canonical `{status,role,task_id,outcome}` shape.
- Rust `workflow_outcomes_json` (`lifecycle.rs:227-239`) returns `latest.outcomes.clone().unwrap_or("[]")` — the iteration's stored string verbatim, with no parse/normalize round-trip.
- **Why it matters:** functionally equivalent because `latest.outcomes` is itself produced by `records_json`/`project_iteration_outcomes` at iteration close, so it is already canonical; the Python round-trip is idempotent here. The only divergence is if a stored iteration `outcomes` string ever carried extra/legacy keys — Python would strip them, Rust would copy them onto the workflow row. Low risk under the current writers.
- **Suggested fix:** none required; optionally normalize for defense-in-depth. Severity low.

### D3 — `delegate_workflow` "already outstanding" response is success in Rust, error in Python (divergent, medium)
- Python returns the already-outstanding payload with `is_error=True` (`delegate_workflow.py:67-81`).
- Rust returns the equivalent payload via `ToolResult::ok(...)` (`model_tools/workflow.rs:67-77`) — i.e. `is_error=false`.
- **Why it matters:** both short-circuit (no second workflow is started, invariant #1/#2 preserved) and both carry a `"message"` telling the agent to use check/cancel first. But the `is_error` flag drives loop/telemetry behavior: Python signals this as a tool error, Rust signals success. An agent (or the engine's error accounting) keyed on `is_error` will react differently. This is the most concrete behavioral parity gap in the area.
- **Suggested fix:** return `ToolResult::error(payload.to_string())` (or set the error flag) for the outstanding-workflow branch to match Python's `is_error=True`. Severity medium.

### D4 — Rust `delegate_workflow` omits the rich `metadata` keys Python emits (divergent, low)
- Python attaches `metadata` with `submission_kind`, `workflow_task_id`, `workflow_id`, `task_id`, `attempt_id`, `initial_iteration_id`, `initial_attempt_id` (`delegate_workflow.py:115-123`).
- Rust attaches only `submission_kind`, `workflow_task_id`, `workflow_id`, `task_id` (`model_tools/workflow.rs:90-103`) — it drops `attempt_id` (parent attempt), `initial_iteration_id`, and `initial_attempt_id`.
- **Why it matters:** downstream consumers of tool metadata (audit/notifications) lose the initial iteration/attempt linkage and the parent attempt id. If nothing in the Rust runtime reads those keys this is harmless; if any consumer expects them it is a silent drop. Verify consumers; otherwise port the missing keys.
- **Suggested fix:** add the three missing keys (and parent `attempt_id`) if any consumer depends on them. Severity low.

### D5 — Continuation (deferred-goal) iteration start-failure has NO compensation in Rust (missing, high)
- Python `handle_iteration_closed` (`lifecycle.py:122-147`) wraps the branch in `try/finally` so the **old** iteration's coordinator is always deregistered, and routes the deferred case through `_start_deferred_iteration` (`lifecycle.py:207-231`), which on attempt-start failure: logs, sets the **new** iteration `CANCELLED`, **deregisters** the new coordinator, and **closes the workflow FAILED**.
- Rust `handle_iteration_closed` (`lifecycle.rs:157-185`) does, on the deferred branch:
  ```rust
  let (_next, coordinator) = self.create_iteration_with_coordinator(&iteration.workflow_id).await?;
  coordinator.create_and_start_first_attempt().await.map(|_| ())
  ```
  with **no rollback**. If `create_and_start_first_attempt()` fails — a reachable path; it is exactly what the starter's own `compensation_rolls_back` test (`starter.rs:263-302`) exercises on the *initial* path — the error bubbles up and leaves the **new iteration OPEN, its coordinator REGISTERED, and the workflow OPEN**. The parent's `check_workflow_status` would then report "running" forever and the coordinator leaks in `OpenIterationCoordinatorRegistry`.
- **Asymmetry that proves this is a gap, not an intentional simplification:** Rust *does* compensate on the initial start path (`starter.rs:116-161 compensate_failed_start`) but provides no equivalent on the continuation path. Python compensates on both.
- **Secondary, same function:** Python's deregister of the *old* iteration is in `finally`, so it runs unconditionally. Rust runs `self.iteration_coordinators.deregister(&iteration.id)` only after `result` is bound (`lifecycle.rs:183`); the `await?` on `create_iteration_with_coordinator` returns *before* that line, so on a create-iteration failure the **old** coordinator also leaks.
- **Why it matters:** a single planner-launch failure on a deferred continuation strands the delegated workflow permanently OPEN and leaks process-local coordinator state — the worst kind of silent divergence because nothing surfaces an error to the parent agent.
- **Suggested fix:** mirror Python: wrap the deferred branch so the old-iteration deregister is unconditional, and on `create_and_start_first_attempt` failure cancel the new iteration, deregister its coordinator, and `close_workflow(..., false)`. Severity high.

## Extra findings

- **Cancel parity is strong.** Rust `WorkflowControlAdapter::cancel_workflow_state` (`ports.rs:240-305`) mirrors Python `cancel_workflow_state` (`_runtime.py:118-177`) precisely: only open iterations, only non-closed attempts, only Pending/Running tasks set FAILED via `set_task_status_if_current` with terminal `{"fail_reason":"workflow_cancelled"}` and a per-task failed outcome; attempt closed FAILED/`TASK_FAILED`; iteration + workflow CANCELLED with the same `{"role":"workflow"}` outcome record. Test `workflow_control_uses_runtime_handles_and_cancels_child_state` (`ports.rs:403-459`) covers it. One nuance: Python builds the per-task outcome with `role=task.get("role")` even for planner/root tasks, while Rust's `cancellation_outcomes` returns an **empty vec** for Root/Planner roles (`ports.rs:371-383`) — so a cancelled planner task gets `outcomes=&[]` in Rust but a `{"role":"planner"...}` record in Python. Minor and arguably an improvement (planner is not an execution role), but it is a divergence in the persisted task outcome for cancelled planner/root tasks. Low severity.
- **No `request_id` defensive branch in Rust.** Python `starter.py:64-68` rejects a parent task with a blank `request_id`; Rust eliminates this branch because `Task.request_id` is a non-optional `RequestId` (`starter.rs` test comment AC-eos-workflow-02). Intentional, type-driven simplification — not a bug.
- **Handle indirection differs but is intentional.** Python uses a `BackgroundTaskSupervisor` to mint/track `workflow_task_id` handles and reports status; Rust uses an in-adapter `WorkflowHandleRegistry` minting `wf_<n>` handles (`ports.rs:334-369`). The Rust `status`/`cancel` render text directly rather than the structured JSON payload Python's `workflow_progress_payload` builds (`_runtime.py:66-111`). The model-facing *shape* of `check_workflow_status` output differs (Rust: a formatted text line + raw outcomes; Python: indented JSON with iteration/attempt/task breakdown). Flagged as an output-shape divergence to verify against the loop's expectations — medium-interest but outside the strict creation-rule invariants.
- **`agent_id` scoping is dropped in Rust.** Rust `find_outstanding`/`status`/`cancel` ignore the caller `agent_id` (`ports.rs:208-212` `_agent_id`; the handle registry is global). Python scopes `find_workflow_record`/`find_outstanding_workflow_for_parent` by `agent_id` (`cancel_workflow.py:49-53`, `check_workflow_status.py:60-64`, `delegate_workflow.py:63-66`). Likely low impact (one running task ↔ one agent), but it is a real authorization-scope simplification. Low severity.
- **No depth/recursion cap on either side.** Neither Python nor Rust imposes a maximum workflow nesting depth; `workflow_depth` exists solely to feed the nested-planner-deferral hook. The checklist's hint at "depths" maps only to the `>1` comparison in D1/C7, not a configurable limit.

## Open questions

1. Does any Rust engine/loop or audit consumer key on `ToolResult.is_error` for the delegate "already outstanding" branch (D3) or read the dropped `delegate_workflow` metadata keys (D4)? If not, both drop to cosmetic.
2. Is the `check_workflow_status` output expected to be machine-parseable JSON (Python `render_payload` indented JSON) or free text? Rust returns formatted text — confirm the consuming agent prompt/parsers tolerate the shape change.
3. Should cancelled **planner** tasks carry a persisted outcome record? Python writes one with `role="planner"`; Rust writes none. Confirm which is the intended canonical behavior.
