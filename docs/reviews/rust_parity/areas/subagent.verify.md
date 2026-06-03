# Verification — Subagent (launched as background task)

Independent re-derivation from source. Every claim below was re-opened in the
Rust and Python files directly; nothing was taken on the investigation's word.
The investigation's core thesis (the Rust subagent supervisor is a hollow stub
that never launches a child, never surfaces a result, and pins a `Running`
record that wedges the parent's terminal) is **CONFIRMED on every load-bearing
anchor**. If anything the investigation slightly *understates* the D6↔D9
causal link (see New findings N1).

## Invariant verdict table

| invariant | independent_status | severity | decisive bilateral anchor |
|---|---|---|---|
| 1. Subagents launched as BACKGROUND tasks (not inline blocking) | confirmed_disparity | critical | PY launches a real coroutine: run_subagent.py:217 `await run_ephemeral_agent(...)` driven by dispatch.py:165-169 `background_tasks.launch(..., _run_background_tool())` → task_supervisor.py:310 `asyncio.create_task(coro)`. RUST: subagent.rs:100-104 → supervisor.rs:253-265 `spawn` only calls `register_running` and returns; `BackgroundTaskRecord` (supervisor.rs:68-88) has **no** task handle/coroutine/`JoinHandle` field. "Background" is satisfied vacuously — there is nothing to run. The real `run_ephemeral_agent` exists (agent_loop.rs:49) but is reached only by root_agent.rs:73 and agent_runner.rs:86, never by the supervisor. |
| 2. Subagent result surfaces back to launching agent | confirmed_disparity | critical | PY forwards `terminal.output`/`is_error`/metadata + `subagent_terminal_called=True` (run_subagent.py:246-251); control.py:64-89 derives `terminated/cancelled/running/finished/failed`; control.py:136-146 returns `json.dumps(payload, indent=2)` + `mark_subagent_delivered` (control.py:129-134). RUST `progress` (supervisor.rs:267-283) returns `format!("{:?}: {}", record.status, lines)` over `progress_lines` that are **never written** (no `push_progress` caller). No terminal result, no `subagent_terminal_called`, no JSON payload, no delivered-mark. The launching model can never retrieve a result. |
| 3. Lifecycle tracked by background supervisor | confirmed_disparity (partial scaffold, phantom) | high | PY: launch/done-callback/cancel/early-stop/parent-exit (task_supervisor.py:290-397, 667-689, 222-235) settle real tasks. RUST: a record + precedence latch + parent-exit flip exist (supervisor.rs:122-208), but `complete()` has **only** the in-module test caller (supervisor.rs:332) and `push_progress()` has **zero** callers (grep). A phantom `Running` record never transitions. No done-callback (no task to await), no early-stop salvage, no `[started: …]` line, no completion notifications. |

## Disparity adjudication

- **D1 — `spawn` never launches a child agent run — CONFIRMED (critical).**
  supervisor.rs:253-265 verified verbatim; record type has no task handle
  (supervisor.rs:68-88). `run_ephemeral_agent` caller set confirmed by grep
  (only root_agent.rs:73, agent_runner.rs:86). Investigator's quoted Rust
  snippet matches the source.
- **D2 — Documented validation gates (recursion/exists/is-subagent) unenforced
  — CONFIRMED (critical).** PY enforces all three: run_subagent.py:126-134
  (recursion), :136-141 (unknown agent), :142-150 (non-subagent type). RUST
  `spawn` performs none; subagent.rs:5-9 and ports.rs:202-204 both *promise* the
  implementor does it. This is the canonical documented-contract-vs-code silent
  miss. The tool-layer only checks blank `agent_name`/`prompt` (subagent.rs:90-99).
- **D3 — No terminal forwarding / `subagent_terminal_called` / status taxonomy
  — CONFIRMED (critical).** PY anchors run_subagent.py:231-251 +
  control.py:64-89, 129-146 verified. RUST `progress` debug-string verified
  (supervisor.rs:282). Refuted nothing.
- **D4 — Cancel is a hard status-flip, not cooperative early-stop — CONFIRMED
  (high).** PY cancel routes subagents to `_request_subagent_early_stop`
  (task_supervisor.py:683-685 → 222-235: `stop_mode=early_stop`,
  `asyncio.sleep(0)`, `task.cancel()`, `sleep(0)`). RUST `cancel`
  (supervisor.rs:182-194) flips to `Cancelled` + synthetic
  `ToolResult::error("Background task cancelled: …")`; `StopMode::EarlyStop`
  (supervisor.rs:62) is declared but unused in `cancel`. Confirmed.
- **D5 — No static guidance prompt; no `initial_messages` seeding — CONFIRMED
  (high).** PY: `role_text = build_explorer_launch_prompt()` as run-prompt,
  caller prompt as `initial_messages[0]` (run_subagent.py:212-226). RUST: grep
  for `build_explorer_launch_prompt`/`explorer_guidance` in agent-core = **zero
  hits**; `spawn` only stores `prompt` in `tool_input`. Confirmed.
- **D6 — `background_inflight_count` ignores `agent_id` AND drops the
  `uses_sandbox` predicate — CONFIRMED, and UNDERSTATED at "medium".** RUST
  `background_inflight_count` (supervisor.rs:301-303) discards `_agent_id` and
  calls `inflight_count()` (supervisor.rs:212-217) = every `Running` record. PY
  `count_by_agent` (task_supervisor.py:439-457) filters via
  `_running_sandbox_task` which **requires `tracked.uses_sandbox`**
  (task_supervisor.py:217). Critically, `run_subagent`'s `@tool` decorator
  passes **no** `context_requirements` (run_subagent.py:154-162; default `()` at
  decorator.py:59), so `uses_sandbox = SANDBOX_CONTEXT in ()` = **False**
  (dispatch.py:127). Therefore in Python a *running subagent never counts* — it
  never blocks the parent's terminal. The missing `uses_sandbox` filter (not
  just the missing agent_id) is the precise reason the Rust phantom blocks the
  terminal. Adjusted: severity is effectively higher because D6 is the causal
  mechanism behind D9. See N1.
- **D7 — `dispatch.rs`+`policy.rs` dead code; `enable_background_tasks`
  set-but-never-read — CONFIRMED (medium).** Grep: `launch_background_tool`,
  `is_engine_background_tool`, `needs_background_manager` referenced only by the
  `mod.rs` re-export (mod.rs:7-8); no call sites. `enable_background_tasks` set
  in 5 places (loop_.rs:282, notifications.rs:262, streaming.rs:64,
  tool_call/dispatch.rs:425, factory.rs:138); the only `.enable_background_tasks`
  read is the Debug impl (context.rs:103) — functionally unread. Note:
  dispatch.rs:16 internally calls `register_running`, but the enclosing
  `launch_background_tool` is itself never invoked, so the path is dead.
  Confirmed.
- **D8 — No `background_tool.*` subagent audit from agent-core — CONFIRMED
  (medium).** Grep `background_tool\.` over agent-core `.rs` = **zero hits**. PY
  `_emit_background_tool` fires `background_tool.started/completed/failed/
  cancelled/delivered` from the supervisor (task_supervisor.py:327 confirmed at
  launch). Confirmed agent-core-side gap.
- **D9 — Stuck-`Running` record permanently blocks the parent's terminal —
  CONFIRMED (critical, active harm).** Full chain re-derived end to end:
  (1) one `SharedSubagentSupervisor::default()` is created at entry.rs:116 and
  threaded as `supervisor_port`;
  (2) entry.rs:191 hands it to `RootAgentParams.subagent_supervisor`;
  (3) root_agent.rs:69 stamps it into the run's `subagent_supervisor: Some(...)`;
  (4) the SAME `Arc` receives `spawn`'s `register_running` (Running) AND backs
  the no-inflight hook's `background_inflight_count`;
  (5) `tool_hooks` (meta.rs:58) attaches `RequireNoInflightBackgroundTasks` to
  `SubmitRootOutcome` (meta.rs:72-75), `SubmitGenerator/Reducer/PlannerOutcome`
  (meta.rs:76-84), `Enter/ExitIsolatedWorkspace` (meta.rs:65-67), and the chain
  is live in production via model_tools/mod.rs:52 `.with_hooks(...)`;
  (6) `run_require_no_inflight` (hooks.rs:501-521) returns `HookOutcome::Deny`
  with `in_flight_message` (hooks.rs:491-497) whenever `local > 0`;
  (7) `complete()` is test-only and `push_progress()` has no callers, so the
  phantom never leaves `Running` and `inflight_count()` is pinned ≥1 forever.
  Result: a root agent that calls `run_subagent` even once can NEVER call
  `submit_root_outcome` — permanent wedge. PY avoids this both via the real
  done-callback settling the task (task_supervisor.py:329-382) AND the
  `uses_sandbox=False` exclusion (D6/N1). Confirmed.

No investigator disparity was refuted. Extra findings E1-E7 spot-checked: E2
(ID prefixes `subagent_<n>`/`wf_<n>`/`bg_<n>` — supervisor.rs:128-141 vs
task_supervisor.py:280-288), E3 (precedence 0/1/2/3/4 + strict `>` at
supervisor.rs:174 vs task_supervisor.py done-callback latch), E4
(`last_n_messages` 1..=10 default 5 — subagent.rs:49-51, 135 vs control.py
Field), E5 (missing-session returns a NON-error `ToolResult::ok` in Rust —
subagent.rs:145 + supervisor.rs:273-278 — vs PY `is_error=True` control.py:117-122),
and E6 (cancel of unknown session returns non-error in Rust — subagent.rs:169 +
supervisor.rs:291-298 — vs PY `is_error=True`) all hold as written. E1 ack
wording matches dispatch.py:179-187 vs subagent.rs:69-76.

## New findings

- **N1 (severity escalation, not a new disparity) — D6's missing `uses_sandbox`
  filter is the root cause of D9, so the "medium" rating undersells it.** A
  *correctly working* Rust subagent (if D1 were fixed) would STILL wrongly block
  the parent's terminal, because `inflight_count()` counts every `Running`
  record regardless of `uses_sandbox`, whereas Python deliberately excludes
  subagent records (`uses_sandbox=False`, run_subagent.py:154-162 →
  dispatch.py:127 → task_supervisor.py:217). Fixing D1 alone (settling the
  record on child completion) does not fully restore parity for the
  *in-flight* window: while a real subagent runs, Rust would block the terminal
  and Python would not. The fix must also add the `uses_sandbox` predicate (and
  `agent_id`) to the inflight count. This strengthens, not contradicts, the
  investigation.
  - Decisive hook anchor (closes the one inferred link): the Python no-inflight
    PRE-HOOK on `submit_*`/`enter/exit_isolated_workspace`
    (`tools/_hooks/require_no_inflight_background_tasks.py:83-88` `_local_count`)
    calls `manager.count_by_agent(agent_id)` — the `agent_id`+`uses_sandbox`
    filtered counter (task_supervisor.py:439-457, 217-219). It is NOT
    `has_pending()`/`iter_running()`. (`engine/query/loop.py:330 has_pending()`
    is a separate quiescence check, not the terminal gate.) So D6 is a genuine
    disparity (not a false alarm) and N1 holds: Python's terminal gate never
    counts a running subagent, Rust's would.
- **N2 (corroboration of OQ1) — the false affirmative claim is in shipped
  code, not just docs.** ports.rs:202-204 (the trait the tool depends on)
  states the implementor "validates the agent (exists, is a subagent, no
  recursion) and supervises terminal-result delivery out of band." The sole
  implementor (`SharedSubagentSupervisor`) does neither. No
  `TODO`/`stub`/`unimplemented`/`Phase` marker on supervisor.rs/ports.rs/
  dispatch.rs/policy.rs (grep confirmed). This is an unflagged silent miss, not
  a tracked staged port. OQ1 resolves toward "accidental/unflagged gap."
- **N3 (no over-claim found).** I specifically hunted for a FALSE ALARM — a
  flagged disparity that is actually implemented across the eos-protocol
  boundary. None found. The subagent supervisor is agent-core-local
  (`SharedSubagentSupervisor`, in-process `Arc<Mutex<…>>`); there is no
  eos-protocol/daemon round-trip that could be hiding the child run. The only
  `background_tool.*` emissions in the whole Rust repo are daemon-side and
  scoped to `command_session`, not subagent — confirming D8 is genuinely an
  agent-core gap and not a misattributed sandbox concern.

## Overall verdict

The investigation is **accurate and well-anchored**. All three invariants are
confirmed_disparity, three at critical severity (the subagent feature is
non-functional and D9 is active harm that wedges the parent's terminal). No
false alarms; no refuted disparities. Every Rust↔Python anchor cited by the
investigation reproduced under independent reading. The single adjustment is an
escalation: D6's dropped `uses_sandbox` predicate (rated medium) is the causal
mechanism that turns D9 into a terminal-blocker and must be fixed alongside D1
to restore in-flight parity (N1). The "FALSE MATCH" failure mode the
verification primarily hunts for is exactly what the investigation already
caught — and it caught it correctly.
