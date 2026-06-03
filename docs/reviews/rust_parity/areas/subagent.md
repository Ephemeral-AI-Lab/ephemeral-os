# Rust Parity Audit — Subagent (launched as background task)

Domain: agent-core. Audited HEAD of the Rust port against the Python behavioral
ground truth. **Headline: the Rust subagent supervisor is a hollow stub.** Its
`spawn` inserts an in-memory record but never launches a child agent run, never
forwards a terminal result, and never tracks a real lifecycle. The launch tool
returns `[SUBAGENT LAUNCHED]`, but no subagent ever runs. This is the exact
"silent miss" the audit hunts for, and it is **not** flagged as a known residual
anywhere in the code. Worse, the phantom record is pinned `Running` forever
(grep: `complete()` is test-only, `push_progress()` has no callers), which
**permanently blocks the parent's terminal submission** through the no-inflight
hook (D9).

## Ground truth

Docs (corroboration):
- `docs/architecture/tools/subagent.html` — registration, launch workflow,
  progress/results, workflow boundary. Lead: "`run_subagent` is the supervised
  async-session launch surface for focused worker agents." Section
  `#progress-and-results` (lines 86-91): the child must call a terminal tool;
  `run_ephemeral_agent` returns an `EphemeralRunResult`; `run_subagent` forwards
  `terminal_result.output`, `is_error`, terminal metadata, plus
  `subagent_terminal_called=true`. Crashes/no-terminal → `subagent_terminal_called=false`.
- `docs/architecture/agent_loops/background-operations.html` — supervisor record
  fields (line 80), status lifecycle `RUNNING -> {COMPLETED,FAILED,CANCELLED} -> DELIVERED`,
  terminal precedence latch (lines 104-110), parent-exit
  `terminate_for_parent_exit(reason="non_cancellation_tool_request")` (line 110),
  `background_tool.*` daemon-ring audit events (lines 148-156).

Python (GROUND TRUTH):
- `backend/src/tools/subagent/run_subagent/run_subagent.py`
  - `_validate_run_subagent_request` (lines 104-151): rejects missing
    `runtime_config`, blank prompt, recursive caller (`agent_type=="subagent"`),
    unknown agent, non-subagent agent.
  - `run_subagent` body (lines 163-251): imports `run_ephemeral_agent`, registers
    a live-peek progress provider snapshotting `agent.messages`
    (`_on_spawned`, lines 190-204), seeds the child with a **static explorer
    guidance prompt** as the run prompt plus the caller prompt as
    `initial_messages[0]` (lines 212-226), `persist_agent_run=False`, then forwards
    `terminal.output`/`terminal.is_error`/terminal metadata with
    `subagent_terminal_called=True` (lines 246-251). Crash → `is_error`,
    `subagent_terminal_called=False` (231-236); no terminal → `is_error`,
    `subagent_terminal_called=False` (237-245).
  - `format_last_n_messages` (lines 68-83) renders `[text]/[think]/[tool]/[result]`
    blocks, `PEEK_MESSAGE_MAX = 10` (line 40).
- `backend/src/tools/subagent/control.py`
  - `CheckSubagentProgressInput`: `subagent_session_id` (min_length=1),
    `last_n_messages` `Field(default=5, ge=1, le=10)` (lines 23-25).
  - `_subagent_status_and_result` (lines 64-89) status taxonomy:
    `terminated` / `cancelled` / `running` / `finished` (COMPLETED|DELIVERED and
    `subagent_terminal_called`) / `failed`.
  - `mark_subagent_delivered` on terminal check (lines 129-134). JSON payload
    output (lines 136-146).
  - `CancelSubagentTool` → `manager.cancel_subagent_session(id, reason)` (168).
- `backend/src/engine/background/task_supervisor.py`
  - `BackgroundTaskRecord` carries `asyncio_task: asyncio.Task[ToolResult]`
    (line 83) and `progress_provider` (line 109).
  - `launch()` (290-397): `asyncio.create_task(coro)` (310), `_done_callback`
    (329-382) applies terminal status via precedence latch; appends `[started: …]`
    progress line (324-325); emits `background_tool.started` (327).
  - `next_subagent_session_id` → `subagent_<n>` (280-283); `bg_<n>` alias (275-278);
    `wf_<n>` (285-288).
  - `_TERMINAL_PRECEDENCE` (56-62): RUNNING=0, CANCELLED=1, FAILED=2, COMPLETED=3,
    DELIVERED=4. `_apply_terminal_status_transition` uses strict `>` (`new_rank <= current_rank` → drop, line 919).
  - `cancel()` (667-689): subagents get **cooperative early-stop**
    (`_request_subagent_early_stop`, 222-235) so a partial result can be salvaged;
    non-subagents hard-cancel the asyncio task.
  - `terminate_for_parent_exit` (794-826): cancels RUNNING subagents with
    `stop_mode=parent_exit`, reason `non_cancellation_tool_request` (line 31),
    returns completion notifications.
  - `count_by_agent` (439-457): running **sandbox-bound** tasks for **one
    `agent_id`** + command sessions + outstanding workflows.
  - `background_tool.*` audit emit `_emit_background_tool` (173-210); heartbeat
    `_HEARTBEAT_INTERVAL_S` env default `60` (24).
- `backend/src/engine/background/dispatch.py` — `launch_background_tool`
  (74-193): routes `run_subagent` through `background_tasks.launch(...)` with the
  real `_run_background_tool` coroutine; emits the `[SUBAGENT LAUNCHED]` ack
  (179-187). `dispatch_background_tool_call` (196-248) is the query-loop entry.
- `backend/src/engine/background/policy.py` — `is_engine_background_tool` /
  `needs_background_manager` (25-39).

## Rust mapping

- `agent-core/crates/eos-tools/src/model_tools/subagent.rs` — the three tools.
  `RunSubagent::execute` (80-106) validates blank `agent_name`/`prompt`, then
  `ctx.require_subagent_supervisor()?.spawn(agent_name, prompt)` and renders the
  `[SUBAGENT LAUNCHED]` ack (`launch_result`, 63-77). `CheckSubagentProgress`
  (118-147) validates `subagent_session_id` non-empty and `last_n ∈ 1..=10`, then
  `.progress(...)`. `CancelSubagent` (151-171) → `.cancel(id, reason)`. Module
  doc (lines 5-9) **promises** the recursion/exists/is-subagent validation
  "lives in the port implementor (`eos-engine`)".
- `agent-core/crates/eos-engine/src/background/supervisor.rs` — the wired port.
  `BackgroundTaskRecord` (68-88) has **no task handle and no coroutine** —
  `task_id, tool_name, tool_input, task_kind, status, cancel_reason, stop_mode,
  result, progress_lines`. `SharedSubagentSupervisor` (227-304) is the
  `SubagentSupervisorPort` impl: `spawn` (253-265) inserts a `register_running`
  record and returns the id — **nothing else**; `progress` (267-283) returns
  `format!("{:?}: {}", record.status, lines)`; `cancel` (285-299) flips status;
  `background_inflight_count` (301-303) ignores `agent_id`.
  `register_running` (122-157) mints `subagent_<n>`/`wf_<n>`/`bg_<n>`.
  `complete` (167-179) and `cancel` (182-194) implement a precedence latch;
  `terminate_for_parent_exit` (197-208).
- `agent-core/crates/eos-engine/src/background/dispatch.rs` /
  `policy.rs` — `launch_background_tool`, `is_engine_background_tool`,
  `needs_background_manager`. **Dead code**: never called outside the `mod.rs`
  re-export (verified by grep).
- `agent-core/crates/eos-runtime/src/entry.rs:116` — the wired supervisor is
  `SharedSubagentSupervisor::default()` (the stub). `:191` passes it as the
  subagent port. `:73` calls `terminate_for_parent_exit` only on request
  `shutdown`.
- `agent-core/crates/eos-runtime/src/agent_loop.rs:49` — a real
  `run_ephemeral_agent` exists, but is called **only** by `root_agent.rs:73` and
  `agent_runner.rs:86` (delegated-workflow Task runs). The subagent supervisor
  never reaches it.

## Invariant table

| invariant | status | severity | python file:line | rust file:line | note |
|---|---|---|---|---|---|
| 1. Subagents launched as BACKGROUND tasks (not inline blocking) | divergent | critical | dispatch.py:165-177 (`background_tasks.launch` + `asyncio.create_task` coro); task_supervisor.py:310 | subagent.rs:100-104 → supervisor.rs:253-265 | Rust `spawn` is non-blocking only because it is **vacuous** — it inserts a HashMap record and returns. No tokio task, no child agent, no coroutine. The record type literally has no task handle (supervisor.rs:68-88) vs Python's `asyncio_task` (task_supervisor.py:83). "Background" is satisfied trivially because there is nothing to run. |
| 2. Subagent result surfaces back to launching agent | missing | critical | run_subagent.py:217-251 (`run_ephemeral_agent` → forward `terminal_result.output/is_error` + `subagent_terminal_called`); control.py:64-89 (finished/failed/terminated taxonomy) | supervisor.rs:267-283 (`progress` returns `"{status:?}: {lines}"`) | The child never runs, so there is no terminal result to surface. `progress` returns `"Running: "` (empty lines) indefinitely. No `subagent_terminal_called`, no `finished`/`failed`/`terminated` status, no JSON payload, no `mark_subagent_delivered`. The launching model is told a subagent launched and can never retrieve a result. |
| 3. Lifecycle tracked by background supervisor | partial | high | task_supervisor.py:290-397, 667-826 (launch/done-callback/cancel/early-stop/parent-exit/notifications) | supervisor.rs:122-225 | A record is created and a precedence latch + parent-exit flip exist, but they track a **phantom**: status only ever changes via external `complete`/`cancel` calls that nothing invokes for a real child. No done-callback (no task to await), no early-stop salvage, no completion notifications, no `[started]` progress line. |

## Disparities

### D1 — `spawn` never launches a child agent run (the whole dynamic is absent) — CRITICAL
**Evidence.** Python `run_subagent` calls `run_ephemeral_agent(parent_cfg, role_text, agent_def=sub_def, …, initial_messages=[Message.from_user_text(prompt)])` (run_subagent.py:217-226), and the engine backs the tool with `background_tasks.launch(..., _run_background_tool(), …)` where `_run_background_tool` actually executes the tool body (dispatch.py:147-177). Rust `SharedSubagentSupervisor::spawn` (supervisor.rs:253-265) does only:
```rust
let task_id = self.inner.lock().await.register_running("run_subagent", input, BackgroundTaskKind::Subagent);
Ok(StartedSubagent { subagent_session_id: task_id.parse()? })
```
`BackgroundTaskRecord` (supervisor.rs:68-88) has no `asyncio_task`/coroutine/`JoinHandle` field, so by construction it cannot drive a child. A real `run_ephemeral_agent` exists at agent_loop.rs:49 but is wired only to root/workflow runs, never to the subagent port.
**Why it matters.** A model that calls `run_subagent("explorer", "...")` gets `[SUBAGENT LAUNCHED] … status=running`, then `check_subagent_progress` returns `"Running: "` forever and the findings never arrive. The core feature is non-functional, and nothing surfaces an error.
**Fix.** Replace the stub port with a real implementor in `eos-runtime` (it has `AppState` + `run_ephemeral_agent` + the agent registry). On `spawn`: validate (D2), build subagent `ExecutionMetadata` (`agent_type=subagent`, role), seed `initial_messages=[user(prompt)]` + a static guidance run-prompt (D5), `tokio::spawn` `run_ephemeral_agent(persist_agent_run=false)`, store the `JoinHandle` on the record, and forward `terminal_result` on completion (D3).

### D2 — Documented validation gates (recursion / exists / is-subagent) are not enforced anywhere — CRITICAL
**Evidence.** subagent.rs:5-9 explicitly states: "The downstream validation (caller is not a subagent, the agent exists and is a subagent) lives in the port implementor (`eos-engine`, which has the agent registry)." ports.rs:201-205 repeats: the implementor "validates the agent (exists, is a subagent, no recursion)." The implementor `SharedSubagentSupervisor::spawn` (supervisor.rs:253-265) performs **none** of these. Python enforces all three in `_validate_run_subagent_request` (run_subagent.py:125-150): recursive caller (`agent_type=="subagent"`) → error; unknown agent → error; `agent_type != SUBAGENT` → error.
**Why it matters.** A subagent could recursively spawn subagents (breaks the focused-worker contract the docs call "a hard contract"), and arbitrary/non-subagent agent names are accepted instead of rejected. The `agent_name` enum in the tool schema is only a soft hint (the model can still send any string in the JSON), so the runtime gate is the real enforcement and it is gone. This is a documented-contract-vs-code mismatch — the canonical silent miss.
**Fix.** Implement the three checks in the real port impl (D1) before spawning, mirroring run_subagent.py:125-150, returning `ToolError`/error `ToolResult`.

### D3 — No terminal-result forwarding / `subagent_terminal_called` / status taxonomy — CRITICAL
**Evidence.** Python forwards `terminal.output`, `terminal.is_error`, terminal metadata + `subagent_terminal_called=True` (run_subagent.py:246-251); crash/no-terminal set `subagent_terminal_called=False` (231-245). `control._subagent_status_and_result` (control.py:64-89) derives `terminated`/`cancelled`/`running`/`finished`/`failed` from those flags. Rust `progress` (supervisor.rs:267-283) returns a debug string `"{record.status:?}: {joined progress_lines}"` — no terminal output, no `subagent_terminal_called`, no `finished`/`failed`/`terminated` distinction, no JSON payload (Python emits `json.dumps(payload, indent=2)` at control.py:143), and never calls anything like `mark_subagent_delivered` (control.py:129-134).
**Why it matters.** Even if D1 were fixed, the progress/result surface still would not match: the model cannot tell a delivered result from a crashed/abandoned one, and the structured snapshot the parent consumes is absent.
**Fix.** As part of the real impl, store the child's `EphemeralRun.terminal_result` on the record, stamp `subagent_terminal_called`, and have `progress` return the Python status taxonomy + JSON payload.

### D4 — Cancel is a hard status-flip, not cooperative early-stop salvage — HIGH
**Evidence.** Python `cancel()` (task_supervisor.py:683-685) routes subagents through `_request_subagent_early_stop` (222-235): set `stop_mode=early_stop`, `asyncio.sleep(0)`, `task.cancel()`, `sleep(0)` — letting the child salvage a partial result before settling; the done-callback then marks `completion_mode=early_stopped`. Rust `cancel` (supervisor.rs:182-194) just flips status to `Cancelled` and writes a synthetic `ToolResult::error("Background task cancelled: …")` — no task to cancel, no salvage, no early-stop mode (`StopMode::EarlyStop` is declared at supervisor.rs:62 but never used in `cancel`).
**Why it matters.** Partial subagent findings are lost on cancel; the documented salvage path (subagent.html `#progress-and-results`, lines 90) is absent. (Largely moot until D1 lands, but the cancel semantics are wrong even in isolation.)
**Fix.** Once a real child task exists, request an early-stop and give the child an await cycle to salvage before reporting cancelled.

### D5 — No static guidance prompt; no `initial_messages` seeding — HIGH
**Evidence.** Python builds `role_text = build_explorer_launch_prompt()` and passes it as the **run prompt** while the caller prompt becomes `initial_messages[0]` (run_subagent.py:212-226). The docs make this a load-bearing isolation invariant: "The child does not inherit the parent's Workflow `AgentContext`. It receives the parent's free-text prompt as its initial user message and a static subagent guidance prompt as the run prompt" (subagent.html line 81). Rust `spawn` (supervisor.rs:253-265) takes `prompt` only to store it in the record's `tool_input`; there is no guidance-prompt assembly and no `initial_messages` seeding because there is no run. No `build_explorer_launch_prompt` equivalent exists in Rust (grep: only test/loader references to `explorer`).
**Why it matters.** When D1 is implemented, omitting the static guidance prompt changes the child's behavior contract; the split (guidance = run prompt, caller text = first user message) is the documented isolation boundary.
**Fix.** Port `explorer_guidance.build_explorer_launch_prompt` and wire the same split into the real spawn.

### D9 — Stuck-`Running` record permanently blocks the parent's terminal submission — CRITICAL
**Evidence.** The no-inflight terminal pre-hook `run_require_no_inflight` (hooks.rs:503-521) calls `supervisor.background_inflight_count(agent_id)`; if `local > 0` it returns `HookOutcome::Deny` with `in_flight_message` = "BLOCKED: {count} sandbox-bound background task(s) are still in flight … Finish or interrupt active command sessions before calling {tool}, then retry." `inflight_count()` counts records in `Running` (supervisor.rs:212-217). Grep confirms the only non-test caller of `complete()` is the in-module test (supervisor.rs:332), and `push_progress()` has **zero** call sites — so a subagent record minted by `spawn` (status `Running`, supervisor.rs:149) **never transitions** out of `Running`. Python avoids this because the real `_done_callback` (task_supervisor.py:329-382) settles the task to a terminal status when the child run finishes, dropping it out of `count_by_agent` (which also requires `uses_sandbox`, task_supervisor.py:441-445/213-219).
**Why it matters.** This is active harm beyond D1's "result silently never arrives": once an agent calls `run_subagent` even once, the in-flight count is pinned at ≥1 forever, so the no-inflight hook **permanently denies that agent's terminal tools** ("BLOCKED … then retry" — but retry can never clear). Compounded by D6 (count ignores `agent_id`), it can also block unrelated agents sharing the supervisor. The parent agent is wedged: it cannot deliver the subagent's (non-existent) result, and it cannot submit its own terminal.
**Fix.** Subsumed by D1 (a real child run settles the record to terminal) + D6 (filter by `agent_id` and the sandbox-bound predicate). Until D1 lands, the stub should at minimum not leave records pinned `Running` (e.g., immediately settle, or exclude phantom subagent records from the inflight count).

### D6 — `background_inflight_count` ignores `agent_id` — MEDIUM
**Evidence.** Python `count_by_agent(agent_id)` (task_supervisor.py:439-457) counts only running **sandbox-bound** tasks whose `agent_id` matches, plus that agent's command sessions and outstanding workflows. Rust `background_inflight_count` (supervisor.rs:301-303) discards the `_agent_id` argument and returns `inflight_count()` = every running record regardless of owner or sandbox binding (supervisor.rs:212-217).
**Why it matters.** This count backs the "no in-flight background tasks" terminal pre-hook (hooks.rs:503-521; see D9). A global, owner-agnostic count can block a terminal submission because of an unrelated agent's work, or — conversely — count non-sandbox work Python would ignore (Python's `_running_sandbox_task` requires `uses_sandbox`, task_supervisor.py:213-219). This is no longer hypothetical: combined with D9's stuck-`Running` records, it actively blocks terminals.
**Fix.** Filter by `agent_id` (and the sandbox-bound predicate) once records carry an `agent_id`. (Records currently have no `agent_id` field at all — supervisor.rs:68-88 — so this needs a field addition too.)

### D7 — Background dispatch path (`dispatch.rs` + `policy.rs`) is dead code; `enable_background_tasks` is set-but-never-read — MEDIUM
**Evidence.** `launch_background_tool` (dispatch.rs:11), `is_engine_background_tool` (policy.rs:7), `needs_background_manager` (policy.rs:16) are referenced only by the `mod.rs` re-export (grep found zero call sites). `enable_background_tasks` is set to `true` in 5 places (loop_.rs:282, context.rs:69, notifications.rs:262, tool_call/dispatch.rs:425, streaming.rs:64, agent/factory.rs:138) and **read in 0**. Python instead routes `run_subagent` through this exact path: the query loop calls `dispatch_background_tool_call` → `launch_background_tool` → `background_tasks.launch(coro)` (dispatch.py:165-193).
**Why it matters.** The Rust port replicated the *shape* of the background dispatch/policy module but left it unwired; the tool executes through the normal synchronous tool path instead. The dead module gives a false impression of parity and would mislead a future maintainer into thinking subagents are dispatched here.
**Fix.** Either wire the loop to route `is_engine_background_tool` tools through `launch_background_tool` (matching Python), or delete the dead module and stop setting `enable_background_tasks`.

### D8 — No `background_tool.*` subagent audit emissions from agent-core — MEDIUM
**Evidence.** Python `_emit_background_tool` (task_supervisor.py:173-210) emits `background_tool.started/completed/failed/cancelled/delivered` (and sample-lane `heartbeat`) with `task_kind` including `subagent`, from inside the supervisor (agent-core). In Rust, agent-core emits **no** `background_tool.*` events for subagents (grep over agent-core `.rs` found only the dead policy/dispatch function names). The only `background_tool.*` emissions in the whole Rust repo are on the sandbox side (`sandbox/crates/eos-daemon/src/dispatcher.rs:3300-3575`) and are scoped to **`command_session`** kind, not subagent. The schema slot exists (`eos-protocol/src/audit.rs:274`) but agent-core never fills it for subagents.
**Why it matters.** Per the cross-boundary rule I checked ownership: Python emits subagent background-tool audit from agent-core (the supervisor), and the Rust daemon only covers command sessions — so this is a genuine agent-core-side gap, not a misattributed sandbox concern. Subagent lifecycle becomes invisible to the audit ring.
**Fix.** Emit `background_tool.*` from the real supervisor impl (D1), mirroring task_supervisor.py:173-210, with `task_kind=subagent`.

## Extra findings

- **E1 — `[SUBAGENT LAUNCHED]` ack parity is good.** Rust `launch_result` (subagent.rs:63-77) and Python (dispatch.py:179-187) produce the same wording and metadata (`subagent_session_id`, `status=running`, `agent_name`). One nuance: Rust also stamps `agent_name` into metadata (subagent.rs:68); Python's background-dispatch ack does not set tool-result metadata. Cosmetic, low risk.
- **E2 — ID prefixes match.** `subagent_<n>` / `wf_<n>` / `bg_<n>` (supervisor.rs:129-141 vs task_supervisor.py:275-288), confirmed by `background_ids_use_typed_prefixes` (supervisor.rs:361-388).
- **E3 — Terminal precedence values + strict `>` match.** Rust `precedence()` RUNNING=0/CANCELLED=1/FAILED=2/COMPLETED=3/DELIVERED=4 (supervisor.rs:30-38) equals Python `_TERMINAL_PRECEDENCE` (task_supervisor.py:56-62). Rust `complete` uses `status.precedence() > record.status.precedence()` (supervisor.rs:174) — strict `>`, matching Python's `new_rank <= current_rank → drop` (task_supervisor.py:919). The cancel-vs-complete race test (supervisor.rs:312-343) asserts COMPLETED wins, matching Python's documented behavior. This sub-mechanism is correct in isolation; it just has no real events to arbitrate.
- **E4 — `last_n_messages` validation matches.** Rust schema + runtime both enforce `1..=10` with default 5 (subagent.rs:34-52, 135-140); Python `Field(default=5, ge=1, le=10)` (control.py:25). Note Rust uses `u8` so the schema/runtime guard is the only bound; values >10 are rejected at runtime (subagent.rs:135).
- **E5 — `progress` "not tracked" wording differs.** Rust returns `"No subagent session \`{id}\` is tracked."` (supervisor.rs:273-278); Python returns `"No subagent session found with ID: {id}"` (control.py:117-122) as an `is_error=True` result. Rust returns it as a **non-error** `ToolResult::ok` (subagent.rs:145), so a genuinely-missing session reads as success. Minor, but worth aligning.
- **E6 — `cancel` returns success for unknown sessions inconsistently.** Rust `cancel` returns `Ok("No subagent session … tracked")` as a **non-error** `ToolResult::ok` (subagent.rs:169 + supervisor.rs:291-298); Python returns `is_error=True` "Could not cancel … may have already completed or does not exist" (control.py:172-180). Minor surface mismatch.
- **E7 — `PEEK_MESSAGE_MAX` cap absent.** Python caps the peek tail at `min(n, 10)` (run_subagent.py:72, `PEEK_MESSAGE_MAX=10`). Rust `progress` uses `progress_lines[len-last_n..]` with `last_n` already bounded to ≤10 at the tool layer, so functionally equivalent — but the cap lives in the wrong layer and would diverge if `progress` were ever called with an unbounded `n`.

## Open questions

- **OQ1 — Is the subagent stub an intentional staged port or an accidental gap?**
  No `TODO`/`stub`/`unimplemented`/`residual`/`Phase` marker exists in
  supervisor.rs, ports.rs, dispatch.rs, or policy.rs (grep confirmed). The codebase
  *does* flag known residuals elsewhere (agent_loop.rs:7 "documented Phase-6
  residual"), so the absence of a marker here, combined with ports.rs:204's
  affirmative-but-false claim that the implementor "supervises terminal-result
  delivery out of band," suggests this is an unflagged silent miss rather than a
  tracked stub. Confirm against the port plan whether subagent execution was
  deferred deliberately.
- **OQ2 — Is the dead `background/dispatch.rs`+`policy.rs` intended to become the
  wiring (D7) or is it abandoned scaffolding?** It mirrors Python's dispatch path
  precisely but is unreferenced; intent determines whether to wire or delete.
- **OQ3 — Heartbeat / sandbox-bound subagent work.** Python's supervisor runs a
  60s heartbeat loop and sandbox-invocation cancel for sandbox-bound background
  tasks (task_supervisor.py:24, 951-1013). Subagents themselves are not directly
  sandbox-bound (the child's *tools* route through the sandbox), so this may be
  out of scope for the subagent area specifically; confirm whether any subagent
  heartbeat parity is expected on the agent-core side vs the daemon side.
