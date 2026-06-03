# Subagent — Rust parity remediation plan (PLAN ONLY)

Status: **plan only, do not implement.** Scope: the `agent-core / subagent`
findings in `docs/reviews/rust_parity/REPORT.html` (`areas/subagent.md` +
`areas/subagent.verify.md`) — D1–D9 in full, verified against the Python
reference in `backend/src`.

This plan honors the user's binding constraints, each verified against Python:

1. **The subagent lifecycle is owned by the engine background supervisor**
   (`BackgroundTaskSupervisor`) — the *same* supervisor that already owns
   `workflow` (`wf_<n>`) records and `command_session` lifecycle. The runtime only
   supplies and *drives* the child future; it must never fork a parallel ledger.
   This mirrors Python, where `BackgroundTaskSupervisor.launch(coro)` drives
   **every** kind (subagent / agent / workflow / command session) through one
   record store, one precedence latch, and one `count_by_agent`.
2. **The real runner lives in `eos-runtime`** (Runner-seam decision: *delete the
   dead dispatch path; the runtime adapter spawns directly*). Only `eos-runtime`
   can build a `run_ephemeral_agent` future (it owns `AppState` + the agent
   registry), so the adapter constructs and `tokio::spawn`s the child, then feeds
   every lifecycle transition back into the engine supervisor (constraint #1).
3. **No new mechanism / match Python.** Reuse the existing building blocks —
   `BackgroundTaskSupervisor`, `run_ephemeral_agent` (`agent_loop.rs:54`), the
   `RuntimeAgentRunner` launch pattern (`agent_runner.rs`), the agent registry's
   `AgentType::Subagent` filter, and `build_explorer_launch_prompt`. No invented
   ports beyond making the existing `SubagentSupervisorPort` real.

---

## 1. Root cause: one structural omission, eight visible symptoms

The Rust port replicated the *shape* of the subagent surface — the three tools
(`subagent.rs`), the typed handle (`StartedSubagent`), the precedence latch and
typed id prefixes (`supervisor.rs`) — but **never wired the one thing that makes
it work: a driver that launches the child agent run and settles the record when
it finishes.** Python has that driver; Rust does not.

Python's mechanism (ground truth):

- `run_subagent` (`backend/src/tools/subagent/run_subagent/run_subagent.py:217`)
  `await run_ephemeral_agent(parent_cfg, role_text=build_explorer_launch_prompt(),
  agent_def=sub_def, persist_agent_run=False, initial_messages=[user(prompt)],
  on_agent_spawned=_on_spawned)`, driven as a real coroutine by
  `dispatch.py → task_supervisor.launch():310` (`asyncio.create_task(coro)`).
- `launch()` adds a `_done_callback` (`task_supervisor.py:329-382`) that settles the
  record to a terminal status via the precedence latch; on terminal-tool success it
  forwards `terminal.output/is_error/metadata` + `subagent_terminal_called=True`
  (`run_subagent.py:246-251`); crash/no-terminal → `is_error`,
  `subagent_terminal_called=False` (`:231-245`).
- **The terminal gate never counts a running subagent.** `run_subagent` declares no
  `context_requirements`, so `uses_sandbox=False`; `count_by_agent`
  (`task_supervisor.py:439-457`) counts only `_running_sandbox_task` (which requires
  `uses_sandbox` *and* a matching `agent_id`) plus command sessions + outstanding
  workflows. A live subagent is excluded by construction.

Rust HEAD deviates at exactly the driver seam, and the deviations compound:

- **(R1) No driver.** `SharedSubagentSupervisor::spawn` (`supervisor.rs:253-265`)
  only calls `register_running` and returns. `BackgroundTaskRecord`
  (`supervisor.rs:68-88`) has **no** task handle / future / `JoinHandle`. The real
  `run_ephemeral_agent` (`agent_loop.rs:54`) is reachable only by root
  (`root_agent.rs:74`) and delegated-workflow runs (`agent_runner.rs`), never by
  this port.
- **(R2) The phantom never settles.** `complete()` has only the in-module test
  caller (`supervisor.rs:332`); `push_progress()` has **zero** callers. A record
  minted `Running` (`supervisor.rs:153`) stays `Running` forever.
- **(R3) The count is unfiltered.** `background_inflight_count` (`supervisor.rs:301-303`)
  discards `_agent_id` and returns `inflight_count()` = every `Running` record,
  with no `uses_sandbox`/kind predicate.

Net effect (matches the report): a model that calls `run_subagent` once gets
`[SUBAGENT LAUNCHED] … status=running`; `check_subagent_progress` returns
`"Running: "` forever; the findings never arrive (D1/D2/D3); and the pinned
`Running` record (R2) is counted by the no-inflight terminal pre-hook (R3),
**permanently denying that agent's `submit_*_outcome` / `enter|exit_isolated_workspace`**
(D9 — active harm). No `TODO`/`stub`/`Phase` marker flags this, and
`ports.rs:202-204` *affirmatively but falsely* claims the implementor "validates
the agent … and supervises terminal-result delivery out of band."

---

## 2. Design decision: supervisor owns the ledger, runtime owns the driver

**Decision.** Split responsibilities exactly as Python does, along the crate
boundary the Rust port already enforces:

- **Engine (`eos-engine/.../background/supervisor.rs`) — the ledger + lifecycle
  machinery.** `BackgroundTaskSupervisor` remains the single owner of every
  subagent record: registration, the precedence latch, progress lines, cancel /
  parent-exit, and the in-flight count the no-inflight hook reads. This is the
  Rust analog of Python's `task_supervisor.py`, and it is where `workflow` and
  `command_session` lifecycle already live (constraint #1). It gains a
  *launch+settle* surface (a future driver) so it can own subagent task lifecycle
  the way it owns command sessions.
- **Runtime (`eos-runtime`) — the driver + validator.** A new
  `RuntimeSubagentSupervisor` implements `SubagentSupervisorPort`, holds the shared
  `Arc<Mutex<BackgroundTaskSupervisor>>` (the *same* instance from `entry.rs`, via
  `supervisor.inner()`) plus `AppState`. Its `spawn` validates against the registry,
  builds the `run_ephemeral_agent` future, hands it to the supervisor's launch
  surface, and routes `progress`/`cancel`/`background_inflight_count` straight to
  the supervisor.

**Why this is the faithful choice (the clinching argument).** "Use the background
supervisor like workflow and command_session" + "the runner lives in `eos-runtime`"
are not in tension: in Python the *supervisor* drives the task (`launch(coro)`)
but the *coroutine itself* (`run_ephemeral_agent`) is built by the tool body, which
in Rust can only be built in `eos-runtime`. So the future crosses the boundary
(runtime → engine) exactly once, at launch; everything else (count, cancel,
progress, settle, audit) stays on the one engine ledger. Relocating the ledger
into runtime, or forking a second runtime-local record store, would split the
in-flight count away from the no-inflight hook and leave D9 wedged (see §6
invariant + the rejected alternative §7).

**Reconciliation with the audit.** The report's D1 Fix says "replace the stub port
with a real implementor in `eos-runtime`," and D7's Fix offers "wire *or* delete"
the dead dispatch path. This plan takes the **delete** branch of D7 and keeps the
engine supervisor as the ledger — observable behavior matches the report's intended
fix; the only refinement is that the runtime adapter drives `run_ephemeral_agent`
*directly* rather than resurrecting the unreferenced `dispatch.rs`/`policy.rs`
indirection that has no live call site.

---

## 3. The changes (all faithful ports; anchors are current `main`)

### 3a. Engine supervisor: own subagent task lifecycle (launch + settle) — backs D1/D2/D3

Extend `BackgroundTaskSupervisor` (`supervisor.rs`) so it drives a future the way
Python's `launch()` drives a coroutine, **without** breaking the record's
`Debug, Clone, PartialEq` derives (used by the existing tests):

- Add `pub agent_id: Option<String>` to `BackgroundTaskRecord` (`:68-88`) — the
  owner needed for the agent-scoped count (Python `BackgroundTaskRecord.agent_id`).
  `Option<String>` is `Clone + PartialEq`, so the derives stand.
- Add a side map `handles: HashMap<String, tokio::task::AbortHandle>` (or a
  `tokio_util::sync::CancellationToken` per task) to `BackgroundTaskSupervisor`
  (`:107-116`) — **not** on the record (a `JoinHandle`/`AbortHandle` is neither
  `Clone` nor `PartialEq`; the record stays cloneable). This is the Rust analog of
  Python's `BackgroundTaskRecord.asyncio_task`, parked off the value type.
- Add `register_running` variant / param to stamp `agent_id` + the kind at mint
  time (extend `:126-161`).
- Add a `settle` method that mirrors Python's `_done_callback` precedence latch
  (`task_supervisor.py:329-382`): apply a terminal status **classified by
  `subagent_terminal_called`, not by `result.is_error`** — a subagent that called
  its terminal with `is_error=true` is still `Completed` (the error rides in the
  payload, and `check_subagent_progress` reports `finished`); only crash / no-terminal
  / exception settle to `Failed`. Keep the strict-`>` precedence guard (`:178`) so a
  cancel-vs-finish race resolves to `Completed` (already covered by the
  `parent_exit_and_cancel_complete_race` test). Populate `progress_lines` from the
  settled result's output (Python `task_supervisor.py:381`).

The supervisor does **not** import `run_ephemeral_agent` (crate-boundary intact);
it only stores/aborts the handle and settles the record. The future is supplied by
3b.

### 3b. Runtime `RuntimeSubagentSupervisor`: validate, drive, forward — D1/D2/D3/D5/D8

New module in `eos-runtime` (model it on `agent_runner.rs::RuntimeAgentRunner`,
which already does `build_metadata` + `run_ephemeral_agent`). It implements
`SubagentSupervisorPort` (`ports.rs`) and holds `AppState` +
`Arc<Mutex<BackgroundTaskSupervisor>>` + the per-request `NotificationService`.

**Caller-identity seam (the one cross-crate edit — `eos-tools`).** Threading the
caller's identity into `spawn` is the single place the runtime-only framing leaks
back into `eos-tools`, so enumerate it: widen `SubagentSupervisorPort::spawn`
(`ports.rs`) to `spawn(agent_name, prompt, caller_agent_name, caller_agent_id)`, and
update its sole call site `RunSubagent::execute` (`subagent.rs:100-104`) to pass
`ctx.agent_name` (`metadata.rs:44`) + `ctx.agent_id()` (`metadata.rs:110`). The
in-crate `FakeSubagentSupervisor` test impl (`subagent.rs:229-263`) takes the same
two params. No other crate sees the change.

`spawn(agent_name, prompt, caller_agent_name, caller_agent_id)`:

- **Validate (D2) — faithful port of `run_subagent.py:125-150`, using the registry
  Rust already exposes:**
  - *recursion*: resolve `caller_agent_name` via `state.agent_registry.get(...)`
    (`registry.rs:63`); if its `agent_type == AgentType::Subagent`, reject
    ("subagents may not spawn further subagents").
  - *exists + is-subagent*: `agent_name ∈ dispatchable_subagent_names()`
    (`registry.rs:75-79`, which filters `AgentType::Subagent`) — one check covers
    both Python branches (`sub_def is None` and `agent_type != SUBAGENT`). Return the
    same error texts as Python.
  - The tool-schema `enum` (`text_spec_with_agent_enum`, `subagent.rs:177-182`) is a
    soft hint only; this runtime gate is the real enforcement (the model can send any
    string). Validation must run **before** any record is minted.
- **Seed + split (D5):** build `role_text = build_explorer_launch_prompt()` (port
  `explorer_guidance.py`; it is a static string, trivially ported) as the run prompt
  and `initial_messages = [Message::from_user_text(prompt)]` — the documented
  isolation split (guidance = run prompt, caller text = first user message;
  `subagent.html:81`). Stamp `agent_type=subagent` + `role` into the child's
  `ExecutionMetadata` (Python `sub_meta`, `run_subagent.py:186-188`); the child gets
  no parent scope.
- **Register on the shared supervisor (constraint #1):** `register_running("run_subagent",
  input, Subagent, agent_id=caller_agent_id)` → `subagent_<n>` (unchanged id
  minting, `supervisor.rs:133-136`). Emit `background_tool.started` (D8).
- **Drive directly (D1):** `tokio::spawn` a task that
  `run_ephemeral_agent(&state, EphemeralRunInput{ agent: sub_def,
  initial_messages, task_id: None, persist_agent_run: false, tool_metadata,
  notifier, … }, on_event).await` (`agent_loop.rs:54`), then locks the supervisor and
  `settle`s the record (3a). Store the task's `AbortHandle` in the supervisor's side
  map for cancel (3d).
- **Live peek (DESIGN ITEM, not a straight port — D3 progress-while-running):**
  Python's `_on_spawned` closes over the child's live `agent.messages` and snapshots
  on demand (`run_subagent.py:190-204`). Rust's `run_ephemeral_agent` exposes **no**
  such handle — messages accumulate inside `run_query(&mut ctx, &mut initial_messages)`
  (`agent_loop.rs:136`) and only `on_event` escapes. So this is a mechanism to design,
  not port: accumulate rendered `[text]/[think]/[tool]/[result]` blocks (port
  `format_last_n_messages`, `run_subagent.py:56-83`) from the `on_event` callback into
  a shared buffer that the supervisor's `progress` reads under the same lock. Lower
  priority than D1/D3 terminal forwarding (which works without it); call it out so it
  is not mistaken for a one-line port.
- **Forward terminal (D3):** map the `EphemeralRun` to the settled `ToolResult`
  exactly as `run_subagent.py:231-251` — terminal present → `terminal.output`,
  `terminal.is_error`, `{**terminal.metadata, subagent_terminal_called: true}`;
  `run.error.is_some()` → crash text + `subagent_terminal_called:false`;
  terminal `None` → no-terminal text + `subagent_terminal_called:false`. Emit
  `background_tool.completed/failed` (D8, `task_supervisor.py:173-210`).

Return `StartedSubagent { subagent_session_id }`; the `[SUBAGENT LAUNCHED]` ack
(`subagent.rs:63-77`, already parity-good per E1) is unchanged.

### 3c. `progress` / `cancel` taxonomy + payload — D3 (and E5/E6)

`progress` (replace `supervisor.rs:267-287`'s debug string) must reproduce
`control.py::_subagent_status_and_result` (`control.py:64-89`) + the JSON payload
(`control.py:136-146`):

- Map record status → `terminated` / `cancelled` / `running` / `finished`
  (COMPLETED|DELIVERED **and** `subagent_terminal_called`) / `failed`, using the
  live-peek provider while running and the settled `result.output` when finished.
- Return `json.dumps(payload, indent=2)` shape: `{subagent_session_id, status,
  agent_name, result}`, and `mark_subagent_delivered` on terminal observation
  (`control.py:129-134`) so the record advances to `Delivered`.
- **E5 fix:** a genuinely-missing session must return `is_error=true`
  (Python `control.py:117-122`), not the current non-error `ToolResult::ok`
  (`subagent.rs:145` + `supervisor.rs:273-278`).

`cancel` (D4 + **E6**): see 3d; an unknown-session cancel must return `is_error=true`
(Python `control.py:172-180`), not the current non-error ok.

### 3d. Cancel = cooperative early-stop salvage — D4

Replace the hard status-flip (`supervisor.rs:186-198`) with the Python early-stop
path for subagents (`task_supervisor.py:222-235`, `:683-685`): set
`stop_mode=EarlyStop` (the `StopMode::EarlyStop` variant at `supervisor.rs:62` is
declared but unused today), signal the child's `CancellationToken` / `AbortHandle`,
give it one scheduler yield (`tokio::task::yield_now().await`, the analog of
`asyncio.sleep(0)`) so a salvaged partial terminal can settle first, then let the
settle path (3a) record the terminal status. Parent-exit
(`terminate_for_parent_exit`, `supervisor.rs:201-212`) keeps its current shape but
must also abort the stored handle. True salvage requires the child loop to observe
the token; if that is out of scope for this lane, settle as `Cancelled` with the
partial peek and note the residual (do **not** silently claim full salvage).

### 3e. Fix the in-flight count — D6 / D9 (the unwedge; load-bearing per N1)

Rewrite the count the no-inflight hook reads so a running subagent never blocks the
terminal, mirroring Python `count_by_agent` + `_running_sandbox_task`
(`task_supervisor.py:213-219, 439-457`):

- `background_inflight_count(agent_id)` (`supervisor.rs:301-303` →
  delegated through the runtime adapter to the supervisor) must count, **for this
  `agent_id` only**: sandbox-bound running tasks (command sessions) + outstanding
  workflows — and **exclude `BackgroundTaskKind::Subagent` records** (the Rust analog
  of Python's `uses_sandbox=False` exclusion, since `run_subagent` is not
  sandbox-bound). Per N1 this exclusion is load-bearing on its own: even a correctly
  driven subagent (3a/3b) must not count while in flight.
- This consumes the `agent_id` field added in 3a. The hook call site
  (`hooks.rs:507-509` `run_require_no_inflight`) and the wiring
  (`meta.rs:58-84`: `RequireNoInflightBackgroundTasks` on `SubmitRootOutcome`,
  `SubmitGenerator/ReducerOutcome`, `SubmitPlannerOutcome`, `Enter/ExitIsolatedWorkspace`)
  are unchanged — only the count's filter changes.

### 3f. Delete the dead dispatch path — D7

Remove `eos-engine/.../background/dispatch.rs` and `policy.rs` and their `mod.rs`
re-exports (`mod.rs:5,7,11,13` → `launch_background_tool`,
`is_engine_background_tool`, `needs_background_manager`) — all unreferenced outside
the re-export (grep-confirmed; verify doc D7). Stop setting `enable_background_tasks`
in the 5 set-but-never-read sites (`loop_.rs:282`, `notifications/mod.rs:262`,
`streaming.rs:64`, `tool_call/dispatch.rs:425`, `agent/factory.rs:138`) and drop the
field if nothing else reads it (only the `Debug` impl does, per verify doc). The
runtime adapter (3b) is the live launch path; the dead module is not resurrected.

Deleting `dispatch.rs` removes the **only** producer of `BackgroundTaskKind::Agent`
/ `bg_<n>` (`dispatch.rs:16`). `Agent` is a test-only alias in Python too
(`next_alias() → "bg_{n}"`, "for internal supervisor tests"); real command sessions
use daemon-minted `cmd_<n>`, not `bg_<n>`. So also remove the `Agent` variant from
`BackgroundTaskKind` (`supervisor.rs:46-54`), the `counter` field and its match arm
(`:108, 141-144`), and update the `background_ids_use_typed_prefixes` test
(`:366-392`) to assert only `subagent_<n>` / `wf_<n>`. The supervisor's record kinds
reduce to exactly the production set — `Subagent` + `Workflow` — alongside the
`command_sessions` map.

---

## 4. What stays exactly as-is (do not change)

- **The typed-id minting and precedence latch.** `subagent_<n>`/`wf_<n>`
  (`supervisor.rs:128-145`) and `precedence()` RUNNING=0…DELIVERED=4 with strict `>`
  (`:30-38, 178`) are confirmed-correct in isolation (E2/E3); reuse them. (The
  `Agent`/`bg_<n>` kind is **removed**, not reused — see 3f; it has no production
  producer once the dead dispatch path is deleted.)
- **The three tools' input validation.** Blank `agent_name`/`prompt`
  (`subagent.rs:90-99`), `last_n_messages ∈ 1..=10` default 5 (`:49-51, 135`),
  empty-session-id errors (`:110-116`) all match Python (E4); unchanged. The new
  validation in 3b is the *engine-side* recursion/exists/is-subagent gate, additive.
- **The `[SUBAGENT LAUNCHED]` ack** (`subagent.rs:63-77`) — parity-good (E1).
- **The single shared supervisor instance.** `entry.rs:120-141` mints one
  `SharedSubagentSupervisor` serving the subagent port, the command-session port, and
  the heartbeat (`supervisor.inner()`). Keep it as the one ledger; the runtime
  adapter wraps `supervisor.inner()` so it settles the same records the hook counts
  (see §6). `SharedSubagentSupervisor` keeps implementing `CommandSessionSupervisorPort`
  (command sessions need no runtime); only the `SubagentSupervisorPort` impl relocates.
- **The no-inflight hook ordering and wiring** (`meta.rs:55-84`) — unchanged; 3e only
  changes what the count returns.

---

## 5. Verification (success criteria)

- **End-to-end:** a root run calls `run_subagent("explorer", …)`; a real child agent
  runs, calls `submit_exploration_result`, and `check_subagent_progress` returns
  `status:"finished"` with the terminal output — no `#[cfg(test)]` fake supervisor.
- **Unwedge (the D9 regression test):** after `run_subagent`, the root can still call
  `submit_root_outcome` **while the subagent is in flight** and **after** it settles —
  `background_inflight_count(agent_id)` returns 0 for a subagent-only ledger. This is
  the test that proves N1 (the `Subagent`-kind exclusion) and D1 (settle) together.
- **Validation (D2):** recursion (a subagent calling `run_subagent`), unknown agent,
  and non-subagent agent each return the Python error text and mint **no** record.
- **Result forwarding (D3):** terminal-called-with-`is_error=true` reports `finished`
  (not `failed`); crash → `failed` + `subagent_terminal_called:false`; no-terminal →
  `failed` + `subagent_terminal_called:false`. Missing-session and unknown-cancel
  return `is_error=true` (E5/E6).
- **Cancel (D4):** a cancel sets `EarlyStop`/`Cancelled` and surfaces the partial peek;
  parent-exit aborts the handle and settles `Cancelled`.
- **Audit (D8):** `background_tool.started/completed/failed/cancelled` fire from
  agent-core for `task_kind=subagent`.
- **Dead-code (D7):** `dispatch.rs`/`policy.rs` removed; no `enable_background_tasks`
  writes remain; build is green.
- Port the intent of the Python subagent tests under
  `backend/src/test_runner/tests/.../subagent*` for the taxonomy + unwedge paths.

---

## 6. Coordination / sequencing

- **Load-bearing invariant (state it in code + a test):** the runtime driver settles
  the **same** `Arc<Mutex<BackgroundTaskSupervisor>>` that backs
  `background_inflight_count`. If a refactor ever gives the runtime adapter its own
  record store, the settle becomes invisible to the no-inflight hook and D9 re-wedges.
  The regression test in §5 guards this.
- **Concurrent refactor:** `notifications.rs → notifications/mod.rs` + `notifications/rules/`
  is under active edit (git status); 3f touches the `enable_background_tasks` write at
  `notifications/mod.rs:262`. Rebase onto that refactor; do not stomp `rules/`.
- **Dependency order:** 3a (ledger launch/settle + `agent_id` field) → 3b (driver) →
  3c (taxonomy) in one lane; 3e (count fix) can land with 3a and **independently
  unwedges D9 even before 3b** (settle a stub immediately, or exclude `Subagent` kind
  from the count) — land 3e early to stop the active harm. 3d, 3f, 3d's audit follow.
- This is the Phase-1 hard-gate `subagent ⊕ query_engine` lane in `REPORT.md`
  §"Rollout at a glance"; it parallels the `advisor`, `attempt_harness`, and
  `request_completion` lanes and must land before `backend/src` deletion.

---

## 7. Alternatives considered (rejected)

- **Wire the dead `dispatch.rs`/`policy.rs` path (D7's other branch).** It mirrors
  Python's `dispatch.py → launch_background_tool → background_tasks.launch(coro)`
  literally, but it is unreferenced scaffolding and still cannot build the
  `run_ephemeral_agent` future without a runtime hop — so wiring it adds an indirection
  layer for identical behavior. Rejected per the user's Runner-seam decision (delete +
  direct spawn) and constraint #3 (no extra mechanism). If a future "match Python's
  module shape exactly" constraint appears, revisit.
- **Keep the `SubagentSupervisorPort` impl in `eos-engine` and inject a runner port.**
  Add an `Arc<dyn EphemeralSubagentRunner>` from runtime into the engine supervisor so
  `spawn` stays engine-side. This invents a new port with no Python analog (Python's
  supervisor calls a coroutine, it does not hold a runner abstraction) and adds a
  second indirection. Rejected; the runtime-adapter-as-`SubagentSupervisorPort` is the
  smaller seam.
- **Relocate the whole ledger (record store + count) into `eos-runtime`.** Violates
  constraint #1 and would split the in-flight count away from the command-session /
  workflow records on the engine supervisor, breaking the no-inflight hook and the
  heartbeat that both read `supervisor.inner()`. Rejected outright.
