# Rust parity audit — User request → completion (sandbox_id binding, root task, submit_root_outcome)

Domain: agent-core. Scope: the request-entry bootstrap (mint root Task, bind sandbox,
run root agent directly), and request completion via `submit_root_outcome`.

Verdict: the core request→completion dynamic is faithfully ported. 5/5 checklist
invariants hold (4 `match`, 1 `match` with a documented type-relocation noted as a
disparity). The disparities are all LOW: a typed-outcome-row relocation, two dropped
entry guards, and an advisor-only metadata omission. No correctness bug in the
request→completion path. Delegated *execution* is an acknowledged Phase-6 stub (does
not affect the boundary invariant).

## Ground truth

Docs:
- `docs/architecture/tools/submission.html:73` — root row: "Marks the root Task done/failed and finishes the request."
- `docs/architecture/tools/submission.html:100` — "The root request is a root Task finished by `submit_root_outcome`, not a workflow-wrapped request."
- `docs/architecture/tools/submission.html:112-113` — `is_terminal_tool=True` stamped by `execute_tool_once()`; `TOOL_STOP` enforced in engine loop/dispatch, not in the tool.
- `docs/architecture/workflow/index.html:53` — "A top-level request mints a root Task and runs the root agent directly; delegated decomposition uses Workflow → Iteration → Attempt only when an agent calls `delegate_workflow`."
- `docs/architecture/workflow/index.html:79-80,117-118,160` — runtime entry creates request + root Task + sandbox binding; root agent runs directly; `submit_root_outcome` marks root task and request done/failed.
- `docs/architecture/agent_loops/main-loop.html:123,219` — "first terminating result becomes `terminal_result`"; `TOOL_STOP → terminal_result = ToolResult` (success), `TERMINAL_NOT_SUBMITTED → terminal_result = None` (soft fail).

Python:
- `backend/src/runtime/entry.py:127-140` `_create_top_level_request` — sandbox provision + `create_request(sandbox_id=binding.sandbox_id)`.
- `backend/src/runtime/entry.py:142-158` `_create_root_task` — `role=AgentRole.ROOT`, `agent_name="root"`, `status=RUNNING`, `workflow_id=None`, then `set_root_task_id`.
- `backend/src/runtime/entry.py:179-225` `_run_root_agent` — runs `run_ephemeral_agent(agent_def=root_def, …)` directly; on `status=="failed" or terminal_result is None` → `_fail_unfinished_root`.
- `backend/src/runtime/entry.py:227-250` `_fail_unfinished_root` — read-then-write guard, only if status still `RUNNING`; writes typed `outcomes[]` row + `terminal_tool_result={"fail_reason":"root_run_exhausted"}` + `finish_request(failed)`.
- `backend/src/runtime/sandbox_provisioning.py:50-79` `prepare_for_run` — explicit-id → `start_sandbox`; else `create_sandbox(name="request-<8hex>", labels={origin:workflow, request_id})`; empty created id → `RuntimeError`.
- `backend/src/tools/submission/root/submit_root_outcome/submit_root_outcome.py:44-94` — the terminal: validates context, ownership, role; writes `set_task_status(DONE|FAILED, outcomes=[{status,role=root,task_id,outcome}], terminal_tool_result={status,outcome})` then `finish_request(done|failed)`.
- `backend/src/db/stores/task_store.py:92-104` `finish_request` — idempotent on `done|failed`; `203-226` `set_task_status_if_current` — CAS on expected status.
- `backend/src/task/task.py:12-17` `TaskStatus = {pending,running,done,failed,blocked}`; `agents.AgentRole.ROOT`.
- `backend/src/agents/profile/main/root.md:8-30` — root profile: `allowed_tools` includes `delegate_workflow`, `check_workflow_status`, `cancel_workflow`, `run_subagent`, `ask_advisor`; `terminals: [submit_root_outcome]`; `role: root`.

## Rust mapping

- `agent-core/crates/eos-runtime/src/entry.rs:95-207` `start_request` — provision → `create_request(Some(sandbox_id))` → wire per-request `AttemptDeps`/`WorkflowStarter`/`WorkflowControlAdapter` → mint `root-<hex16>` Task (`role=Root`, `status=Running`, `workflow_id=None`, `agent_name="root"`) → `set_root_task_id` → `tokio::spawn(run_root_agent)`.
- `agent-core/crates/eos-runtime/src/root_agent.rs:31-94` `run_root_agent` — resolves `root` def from registry, builds metadata, runs `run_ephemeral_agent` directly; on `error.is_some() || terminal_result.is_none()` → `fail_unfinished_root`.
- `agent-core/crates/eos-runtime/src/root_agent.rs:102-138` `fail_unfinished_root` — `set_task_status_if_current(Running→Failed, outcomes=None, terminal={fail_reason:"root_run_exhausted", summary})` then `finish_request(failed)`.
- `agent-core/crates/eos-tools/src/model_tools/submission.rs:97-184` `SubmitRootOutcome` — context/ownership/role checks; `set_task_status(Done|Failed, None, terminal={status,outcome})` + `finish_request("done"|"failed")`; metadata `submission_kind=root_success|root_failure`.
- `agent-core/crates/eos-sandbox-host/src/provisioning.rs:31-88` `prepare_for_run` — explicit-id (trimmed) → `lifecycle.start`; else `lifecycle.create(fresh_create_spec)` with `name="request-<8hex>"`, labels `origin=workflow,request_id`.
- `agent-core/crates/eos-db/src/repositories/request_task.rs:39-127` `create_request`/`set_root_task_id`/`finish_request` (idempotent on `done|failed` at :110); `:175-239` `set_task_status`/`set_task_status_if_current` (CAS at :222).
- `agent-core/crates/eos-engine/src/query/loop_.rs:200-204` — `ToolStop` exit when `terminal_result.is_some_and(|r| r.is_terminal)`; `:85` `TerminalNotSubmitted` else.
- `agent-core/crates/eos-runtime/src/app_state.rs:511-556` — `build_agent_registry` loads `root.md` et al. via `load_agents_tree(dir)`; `validate_agent_tools` checks every `allowed_tools`/`terminals` entry is a registered tool.
- `agent-core/crates/eos-agent-def/src/model.rs:147-181` — `AgentDefinition` carries `allowed_tools`, `terminals`, `role` parsed from the same frontmatter.

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | User request BOUND to a sandbox_id | match | — | entry.py:129-138; sandbox_provisioning.py:50-79 | entry.rs:104-113; provisioning.rs:61-88 | Same flow: provisioner resolves binding (explicit `start` vs fresh `create`), then `create_request(sandbox_id=binding.sandbox_id)`. Same `request-<8hex>` name and `origin=workflow,request_id` labels. |
| 2 | Mint root Task(role=root, workflow_id=None); run root agent directly via entry path | match | — | entry.py:142-158,179-212 | entry.rs:157-197; root_agent.rs:31-85 | Rust mints `root-<hex16>` (`role=Root`, `Running`, `workflow_id=None`, `agent_name="root"`), `set_root_task_id`, then spawns `run_root_agent` → `run_ephemeral_agent(root_def)`. No workflow wrapping. Id format `root-<16 hex>` matches Python `f"root-{uuid4().hex[:16]}"`. |
| 3 | User-facing result comes from `submit_root_outcome()` | match | LOW (see D1) | submit_root_outcome.py:68-94 | submission.rs:145-184 | Both write `terminal_tool_result={status,outcome}` (verbatim same keys/values) + status `Done`/`Failed` + `finish_request("done"/"failed")`. Engine loop does NOT independently stamp the root task (no `set_task_status` in `backend/src/engine/` or `loop_.rs`), so `submit_root_outcome` is the sole writer of the success result on both sides. The typed `outcomes[]` row differs → D1. |
| 4 | Root MAY call delegate_workflow but final result STILL from submit_root_outcome | match | — | root.md:8-30; submission.html:100 | model_tools/workflow.rs:1-2,47; model_tools/mod.rs:61-67; entry.rs:148-155,185-193; root_agent.rs:64-71; factory.rs:87-96 | The three workflow tools are real, callable model tools: defined in `model_tools/workflow.rs` and registered into the **dispatch-time** registry by `workflow::register` inside `build_default_registry` (mod.rs:67, the same registry the loop builds at agent_loop.rs:97). Root profile (same `root.md`) grants `delegate_workflow`/`check_workflow_status`/`cancel_workflow` in `allowed_tools`; `entry.rs` wires `WorkflowControlAdapter` into the root's `workflow_control` port. `submit_root_outcome` is root's only `terminal`; delegate_workflow is non-terminal. Delegated workflow outcome never becomes the user result (workflow close does not mutate the parent Task — agent_runner.rs:9-13). Caveat: delegated *execution* is a Phase-6 stub (see Extra findings EF3). |
| 5 | Request finishes through submit_root_outcome (a terminal submission) | match | — | submission.html:112-113; main-loop.html:219 | submission.rs:587-595 (registered as terminal); loop_.rs:200-204 | Terminality is enforced via `agent.terminals` → `ctx.terminal_tools` (factory.rs:87-96), and the loop exits `ToolStop` when `terminal_result.is_terminal`. Rust derives terminality *extrinsically* from the profile's `terminals` list rather than Python's per-tool `is_terminal_tool=True` flag — behaviorally equivalent for root since `root.md` lists exactly `submit_root_outcome`. |

## Disparities

### D1 — `submit_root_outcome`/`fail_unfinished_root` relocate the typed `outcomes[]` row into `terminal_tool_result` (LOW)

Evidence:
- Python `submit_root_outcome.py:70-85` writes BOTH a typed `outcomes=[{"status":status,"role":"root","task_id":task_id,"outcome":outcome}]` AND `terminal_tool_result={"status":status,"outcome":outcome}`.
- Rust `submission.rs:158-164` writes only `terminal_tool_result={status,outcome}` and passes `outcomes = None` to `set_task_status`. Comment at `submission.rs:155-157` explains: root is not an `ExecutionRole` (`eos-state` models only `Generator|Reducer`), so the typed column is left unchanged.
- Same relocation in the failure guard: Python `entry.py:240-247` writes a typed `outcomes=[{status:"failed",role:"root",task_id,outcome:summary}]`; Rust `root_agent.rs:108-120` puts `summary` into `terminal_tool_result.summary` with `outcomes=None`.
- Type constraint confirmed: `eos-state/src/outcomes.rs:30-37` `ExecutionRole` has only `Generator`/`Reducer` variants; the typed `Task.outcomes: Vec<ExecutionTaskOutcome>` cannot represent a root outcome.

Why it matters: the user-facing payload (`{status, outcome}`) is preserved identically in `terminal_tool_result`, so the request result the user receives is unchanged. The dropped piece is the *typed outcome row* on the root task. A grep for readers of the root task's `outcomes` field in `backend/src/runtime/` and `engine/agent/lifecycle.py` returned nothing — root outcomes are not projected (outcome projection in `outcomes.rs:101-169` walks only attempt generator/reducer task ids, never the root task). So the relocation is observable only to a direct DB inspector of `tasks.outcomes` for the root row, not to any runtime consumer. Data is relocated, not lost.

Suggested fix: none required for behavior. If exact DB-column parity is desired, either (a) add a `Root` variant to `ExecutionRole` and write the typed row, or (b) document this as an accepted schema deviation in the migration notes (the code comments already do this inline).

### D2 — Entry-time `_assert_stores_ready` guard dropped (LOW)

Evidence:
- Python `entry.py:103-108,288-301` calls `_assert_stores_ready(...)` raising `RuntimeError("Request stores are not ready.")` when any of task/workflow/iteration/attempt store `.is_ready` is false, before minting anything.
- Rust `start_request` (entry.rs:95-207) has no equivalent readiness assertion; stores are `Arc<dyn …>` handed in from `AppState`. Grep for `is_ready`/`stores_ready` in `eos-runtime/src/` returned nothing.

Why it matters: in Python the stores were lazily-initialized objects that could be "not ready"; in Rust the stores are constructed eagerly in `AppState` and are always usable by construction (parse-don't-validate). The guard is structurally unnecessary in the Rust shape, so this is an intentional migration simplification, not a gap. Severity LOW.

Suggested fix: none; note the deviation.

### D3 — `validate_agent_definitions_resolved()` not called per-request (LOW)

Evidence:
- Python `entry.py:277-278` (`_build_composer`) calls `validate_agent_definitions_resolved()` (`backend/src/agents/definition/resolved_validation.py:19`) on every request bootstrap.
- Rust performs profile validation once at `AppState` build time via `validate_agent_tools` (`app_state.rs:540-556`), which checks `allowed_tools`/`terminals` resolve to registered tools — not per request. No per-request call exists in `start_request`.

Why it matters: the validation that matters for request→completion (root's tools resolve) runs once at startup in Rust instead of per request. Since the registry is immutable after `AppState` build, running it per-request would be redundant. Behaviorally equivalent; LOW.

Suggested fix: none; note the deviation.

## Extra findings

### EF1 — `metadata["active_terminals"]` is NOT threaded into Rust tool metadata (LOW, advisor-scoped)

Python `entry.py:200` sets `metadata["active_terminals"] = list(root_def.terminals)`. This metadata is consumed only by the ask-advisor / ask-helper compose path (`backend/src/tools/ask_helper/_lib/_compose.py:108-117`, `ask_advisor.py:65`) to tell the advisor which terminals the parent has available. The Rust `ExecutionMetadata` (`tool_context.rs:62-86`) has no `active_terminals` field and does not propagate the root's terminal list. Impact is confined to advisor-guidance fidelity (the advisor sub-run would not know the parent's active terminals); it never touches the request→completion path, terminal enforcement (which is driven by `agent.terminals` in `factory.rs`), or `submit_root_outcome`. Out of scope for this area; flagged for the advisor/ask-helper audit. Severity LOW.

### EF2 — `fail_unfinished_root` is atomic in Rust (CAS) vs read-then-write in Python — Rust is stricter (IMPROVEMENT, not a bug)

Python `entry.py:234-249` reads the task, checks `status == RUNNING`, then unconditionally writes — a TOCTOU window if the engine stamps a real terminal between the read and the write. Rust `root_agent.rs:112-121` uses `set_task_status_if_current(Running → Failed)` (atomic CAS, `request_task.rs:201-239` runs inside a transaction), so a real terminal that won the race is never clobbered (`Ok(None)` → no-op at root_agent.rs:133). This is a strict correctness improvement / divergent-but-better, not a regression. The behavioral outcome under no-race is identical.

### EF3 — Delegated-workflow *execution* is a Phase-6 stub (does not affect invariant 4)

`agent-core/crates/eos-runtime/src/agent_runner.rs:9-13,100-104`: `RuntimeAgentRunner::run` always returns `AgentRunReport::no_terminal(...)` because the workflow agent runs with `plan_submission = None` (typed-terminal capture is the deferred Phase-7 gate). The orchestrator then closes the attempt via `synthesize_planner_failure` without mutating the parent task. This keeps the *boundary* invariant intact (the user result still only comes from `submit_root_outcome`, and a delegated workflow never directly becomes the user result), but means a root agent that delegates real work will currently get a failed/empty workflow result in Rust. This belongs to the workflow-execution audit area, not request→completion; flagged here for traceability.

## Open questions

1. Is the typed root `outcomes[]` row (D1) consumed by any out-of-tree reader (CLI, API serializer, frontend) that inspects `tasks.outcomes` for the root row directly? In-tree grep found no runtime reader, but consumers outside `backend/src` were not searched.
2. EF1: does any Rust advisor/ask-helper port intend to receive the parent's active-terminal list by another channel (e.g. a dedicated field on the advisor request)? Confirm in the advisor-area audit.
3. EF3: is delegated-workflow execution (Phase-7) tracked as a known incomplete item, so that a root agent calling `delegate_workflow` for "sophisticated execution" (invariant 4's premise) actually produces useful work in the Rust port, or is delegation effectively a no-op until Phase-7 lands?
