# Verification — User request → completion (sandbox_id binding, root task, submit_root_outcome)

Independent re-derivation of `docs/reviews/rust_parity/areas/request_completion.md`.
Every verdict below was re-confirmed by opening both sides; bilateral anchors are cited.
Source precedence: Python = ground truth, Rust mapped by behavior + grep.

## Invariant verdict table

| # | Invariant | independent_status | severity | decisive bilateral anchor |
|---|-----------|--------------------|----------|---------------------------|
| 1 | User request BOUND to a sandbox_id | confirmed_match | — | PY `entry.py:129-138` (`prepare_for_run` → `create_request(sandbox_id=binding.sandbox_id)`) + `task_store.py:52-73` (`RequestRecord(sandbox_id=sandbox_id)` persisted) ↔ RS `entry.rs:104-113` (`prepare_for_run` → `create_request(... Some(&binding.sandbox_id) ...)`) + `request_task.rs:47-60` (`INSERT INTO requests (... sandbox_id ...) .bind(sandbox_id.map(SandboxId::as_str))`). Same `request-<8hex>` name + `origin=workflow,request_id` labels: `sandbox_provisioning.py:66-72` ↔ `provisioning.rs:31-43`. |
| 2 | Mint root Task(role=root, workflow_id=None); run root agent directly via entry path | confirmed_match (logic) — see NF1 for the unwired binary | — | PY `entry.py:142-158` (`role=ROOT`, `RUNNING`, `workflow_id=None`, `agent_name="root"`, `set_root_task_id`) + `:179-212` (`run_ephemeral_agent(agent_def=root_def)` directly) ↔ RS `entry.rs:157-197` (`TaskRole::Root`, `Running`, `workflow_id:None`, `agent_name:Some("root")`, `set_root_task_id`, `tokio::spawn(run_root_agent)`) + `root_agent.rs:73-85` (`run_ephemeral_agent(... agent: root_def ...)`). Id format `root-<16hex>`: PY `entry.py:143` `f"root-{uuid4().hex[:16]}"` ↔ RS `entry.rs:158` `format!("root-{}", &Uuid::new_v4().simple().to_string()[..16])`. No workflow wrapping on either side. |
| 3 | User-facing result comes from `submit_root_outcome()` | confirmed_match | LOW (D1 schema relocation) | PY `submit_root_outcome.py:70-86` (sole writer: `set_task_status(DONE\|FAILED, ...)` + `finish_request`) ↔ RS `submission.rs:145-167`. Decisively bilateral: the engine never independently stamps the root task — `grep "set_task_status\|finish_request" backend/src/engine/` = **0 hits**, and the same grep over `agent-core/crates/eos-engine/src/` = **0 hits**. So on BOTH sides `submit_root_outcome` (and the `_fail_unfinished_root` guard) is the only writer of the root result. |
| 4 | Root MAY delegate_workflow but final result STILL from submit_root_outcome | confirmed_match (boundary) | — | Shared profile `backend/src/agents/profile/main/root.md:8-30` (one file, loaded by both runtimes) grants `delegate_workflow`/`check_workflow_status`/`cancel_workflow` in `allowed_tools`, terminal = exactly `submit_root_outcome`. Tools are real + callable: RS `model_tools/workflow.rs:1-79` + registered in `model_tools/mod.rs:67` (`workflow::register`). `delegate_workflow` is non-terminal: `meta.rs:26-27` (in non-terminal `is_terminal=false` arm). Workflow close never mutates the parent task: RS `agent_runner.rs:9-13` doc + `synthesize_planner_failure`. Delegated execution is a Phase-6 stub (EF3 — correctly carved out). |
| 5 | Request finishes through submit_root_outcome (a terminal submission) | confirmed_match | — | Terminality stop-signal: RS `execution.rs:104-115` `stamp_terminal` sets `is_terminal=true` iff `tool.is_terminal && !result.is_error`, where `tool.is_terminal = meta::is_terminal(SubmitRootOutcome) = TerminalTool::from_tool_name(..).is_some() = true` (`meta.rs:49-51`, `submission.rs:586-595`). Loop exits `ToolStop` on `terminal_result.is_terminal` (`loop_.rs:199-206`); the gate `first_terminal_result` (`dispatch.rs:82-94`) requires BOTH the registry flag AND `result.is_terminal`. PY: `@tool(..., is_terminal_tool=True)` (`submit_root_outcome.py:41`) stamped by `execute_tool_once`; `TOOL_STOP` in engine loop. Behaviorally identical for root. |

## Disparity adjudication

### D1 — typed `outcomes[]` row relocated into `terminal_tool_result` — **CONFIRMED (LOW)**
Verified bilaterally. PY `submit_root_outcome.py:70-85` writes BOTH a typed `outcomes=[{status,role:"root",task_id,outcome}]` AND `terminal_tool_result={status,outcome}`. RS `submission.rs:158-164` writes only `terminal_tool_result` and passes `outcomes=None` to `set_task_status`. Same relocation in the fail guard: PY `entry.py:240-248` (typed row) ↔ RS `root_agent.rs:108-120` (summary in `terminal_tool_result`, `outcomes=None`). Type constraint independently confirmed: `eos-state/src/outcomes.rs:30-37` `ExecutionRole` has ONLY `Generator`/`Reducer` — no `Root` variant — so the typed `Vec<ExecutionTaskOutcome>` literally cannot represent a root outcome. The user-facing payload `{status,outcome}` is preserved verbatim in `terminal_tool_result`; the COALESCE-based update (`request_task.rs:18-21`) leaves the existing `outcomes` column untouched on a `None` bind, exactly mirroring Python `set_task_status`'s `if outcomes is not None` guard (`task_store.py`). Investigator's "data relocated, not lost; no in-tree runtime reader of the root `outcomes` column" matches what I see. **LOW, behavior preserved.**

### D2 — entry-time `_assert_stores_ready` guard dropped — **CONFIRMED (LOW), refined**
PY `entry.py:103-108,288-301` raises `RuntimeError("Request stores are not ready.")`. RS `entry.rs` has no equivalent; `grep is_ready|stores_ready agent-core/crates/eos-runtime/src` = none. The stores are `Arc<dyn …>` constructed eagerly in `AppState::build` and are usable by construction (parse-don't-validate). Intentional shape simplification, not a behavior gap. **LOW.**

### D3 — `validate_agent_definitions_resolved()` not per-request — **CONFIRMED (LOW)**
PY `entry.py:278` calls it per request bootstrap. RS validates once at `AppState` build via `validate_agent_tools` (`app_state.rs:539-557`), checking each `allowed_tools`/`terminals` entry resolves to a registered tool. Registry is immutable after build, so per-request is redundant. Behaviorally equivalent. **LOW.** (Note: this validation is the same machinery whose absence in the demo binary causes NF1's empty-registry path — but the validation runs and passes whenever a registry is injected, e.g. `tests.rs:99-104`.)

### EF1 — `metadata["active_terminals"]` not threaded into Rust — **CONFIRMED (LOW), out of scope**
PY `entry.py:200` sets `metadata["active_terminals"] = list(root_def.terminals)`, consumed only by the advisor/ask-helper compose path. RS `build_metadata` (`tool_context.rs`) / `MetadataParams` (`root_agent.rs:58-71`) carries no such field. Confined to advisor-guidance fidelity; never touches request→completion or terminal enforcement. Correctly flagged for the advisor-area audit.

### EF2 — `fail_unfinished_root` uses CAS in Rust vs read-then-write in Python — **CONFIRMED (IMPROVEMENT, not a bug)**
PY `entry.py:234-249` reads then writes unconditionally (TOCTOU window). RS `root_agent.rs:112-137` uses `set_task_status_if_current(Running→Failed)` (`request_task.rs:201-239`, atomic in a transaction; `Ok(None)`→no-op). Strictly stronger; identical under no race. Confirmed.

### EF3 — delegated-workflow *execution* is a Phase-6 stub — **CONFIRMED, correctly carved out**
RS `agent_runner.rs:51-104` always returns `AgentRunReport::no_terminal(...)` (`plan_submission=None`, Phase-7 gate). The orchestrator closes the attempt without mutating the parent task, so the boundary invariant (4) holds: the user result still comes only from `submit_root_outcome`. A root that delegates real work currently gets an empty/failed workflow result in Rust. Belongs to the workflow-execution audit; not a request→completion disparity. Confirmed.

## New findings

### NF1 — INVESTIGATOR MISSED: the sole Rust binary ships with an EMPTY agent registry, so root never resolves (MEDIUM as shipped; logic parity LOW)
The investigation marked invariants 2/4/5 `match` while silently assuming a populated registry. Re-derivation shows the only `agent-core` binary does not populate one:
- `main.rs:20` builds via `AppState::builder().build()` with **no `.agents_dir(...)`** and no injected `agent_registry`.
- `app_state.rs:421-423`: with `agent_registry=None` and `agents_dir=None`, `build_agent_registry(None)` returns `AgentRegistryBuilder::new().build()` — an **empty** registry (`app_state.rs:511-514`). There is no config/env fallback that sets `agents_dir` (`grep agents_dir agent-core` finds only the builder setter and tests; `.agents_dir(` has **zero production callers**).
- Consequence: `run_root_agent` resolves `root` → `agent_registry.get("root")` → `None` → `fail_unfinished_root` (`root_agent.rs:42-55`). Every request from `main.rs` fails immediately with "root agent definition 'root' is not registered" — the root agent never actually runs.

**Severity discriminator (the check that sets severity):** `agent-core` has full end-to-end integration tests that DO wire a registry — via `agent_registry` **injection**, not `agents_dir` — and drive the complete path:
- `tests.rs:23-30` `root_agent()` fixture (`terminals=["submit_root_outcome"]`).
- `tests.rs:122-158` `start_request_mints_root_task_no_workflow` — asserts root task minted, `role=Root`, `workflow_id=None`, no workflow (invariant 2).
- `tests.rs:167-200` `successful_root_keeps_engine_terminal` — drives `start_request` → engine emits `submit_root_outcome` → asserts the terminal is kept (invariants 3, 5).
- Injection seam: `tests.rs:99-109` `.agent_registry(Arc::new(registry))`.

So the **design intent** is "the application harness supplies the registry via `agent_registry` injection; `main.rs` is a thin demo (its own doc: 'All logic lives in the library') that omits the wiring." That makes the request→completion **logic** a genuine match — fully exercised end-to-end when a registry is present — so the logic-parity severity is LOW. But the **shipped binary** as-is fails every request, which the investigator's blanket "match" never surfaced. I rate the shipped-behavior gap MEDIUM and flag it because it is invisible in the investigation.

**Honest Python comparison (narrow):** Python's registry (`agents/definition/registry.py:11` `_DEFINITIONS={}`) is ALSO empty until startup wiring calls `register_definition`/`load_agents_tree`, and `entry.py:187-192` fails-fast identically when `get_definition("root")` is `None`. I did **not** locate the production registry-population harness on either side within the audited scope (`grep register_definition|load_agents_tree backend/src` finds only loaders/tests; `entry.py` is a library bootstrap, not the server entrypoint). So I do not claim "fully symmetric" as proven — only that both registries start empty and both fail-fast identically, and that the intended Rust seam is registry injection (evidenced by the integration tests). Confirming the production server harness on each side is the app-bootstrap area's concern, not request→completion.

### NF2 — invariant-5 mechanism: investigator_overstated the "extrinsic vs intrinsic" gap (cuts toward match)
The investigation framed Rust terminality as derived "extrinsically from the profile's `terminals` list rather than Python's per-tool `is_terminal_tool=True` flag." The source shows that is backwards: the `TOOL_STOP` stop-signal is driven by the **intrinsic** per-tool registry flag — `execution.rs:104-115 stamp_terminal` → `tool.is_terminal` → `meta::is_terminal(name)` → `TerminalTool::from_tool_name(name).is_some()` (`meta.rs:49-51`), the direct mirror of Python's `is_terminal_tool=True`. The profile `terminals` list (`factory.rs:87-105`) gates tool *availability + the termination-condition prompt + a registry-consistency check* (`factory.rs:96-105` errors if a profile names a non-terminal as terminal), not the stop signal. Conclusion unchanged (still a match) but the mechanism is a *cleaner* match than written. Logged as an adjustment, not a disparity.

## Overall verdict

The request→completion **logic** is faithfully ported. All 5 checklist invariants hold at the logic level, re-derived bilaterally:
- sandbox binding persists on both sides (1),
- the root `Task(role=root, workflow_id=None)` is minted and the root agent runs directly through the engine, no workflow wrapping (2),
- `submit_root_outcome` is the sole writer of the user result on both sides — the engine never independently stamps the root task (3),
- `delegate_workflow` is a real non-terminal tool and the delegated outcome never becomes the user result (4),
- terminality + loop exit are behaviorally identical, via the intrinsic per-tool terminal flag (5).

D1/D2/D3/EF1 are all LOW and correctly characterized; EF2 is an improvement; EF3 is correctly carved to the workflow-execution area. The one thing the investigation missed is **NF1**: the only shipped Rust binary builds an empty agent registry (no `agents_dir`, no injected registry, no config fallback), so it fails every request at root resolution; the logic is proven correct only by the injection-based integration tests, which set the intended production seam. No false-alarm disparities were found (D1–D3/EF1 are real LOW deviations). No correctness regression in the ported logic.
