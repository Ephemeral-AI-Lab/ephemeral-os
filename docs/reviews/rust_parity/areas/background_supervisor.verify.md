# Verification — Background supervisor (exec/subagent/workflow bg, exec status from daemon, terminal-block)

Area: `background_supervisor` (agent-core). Independent re-derivation from primary source.

> **Investigation file absent.** `docs/reviews/rust_parity/areas/background_supervisor.md` does **not** exist
> (confirmed via `ls`, `git log` returning nothing for that path, and a repo-wide grep). There were therefore
> **zero investigator claims** to adjudicate, so no verdict can be `investigator_overstated` /
> `investigator_missed` — both require a prior claim. Every verdict below is a pure independent finding. A prior
> `background_supervisor.verify.md` existed in the tree; this file re-derives everything from primary source and
> **corrects two of its factual claims** (see "Corrections to the prior verify").

Primary anchors (all read in full or at the cited line ranges):
- Rust supervisor: `agent-core/crates/eos-engine/src/background/supervisor.rs`
- Rust policy/dispatch/mod: `agent-core/crates/eos-engine/src/background/{policy,dispatch,mod}.rs`
- Rust production engine loop: `agent-core/crates/eos-engine/src/query/loop_.rs:98-209`
- Rust production tool dispatch: `agent-core/crates/eos-engine/src/tool_call/dispatch.rs:181-303`
- Rust pre-hook runner: `agent-core/crates/eos-tools/src/execution.rs:30-72` (hook loop 48-62)
- Rust terminal-block hook: `agent-core/crates/eos-tools/src/hooks.rs:499-570`; wiring `meta.rs:58-88`
- Rust daemon pull RPC: `agent-core/crates/eos-sandbox-api/src/tool_api/control.rs:71-84`
- Rust run_subagent tool + port: `agent-core/crates/eos-tools/src/model_tools/subagent.rs:79-106,173-208`; `supervisor.rs:251-304`
- Rust runtime wiring: `agent-core/crates/eos-runtime/src/entry.rs:116-117,191`; `agent_runner.rs:30-72`
- Python supervisor: `backend/src/engine/background/task_supervisor.py`
- Python policy/dispatch: `backend/src/engine/background/{policy,dispatch}.py`
- Python terminal-block hook: `backend/src/tools/_hooks/require_no_inflight_background_tasks.py`
- Python loop drive: `backend/src/engine/query/loop.py:113-171,280-331`
- Python daemon pull RPC: `backend/src/sandbox/api/daemon_invocations.py:70-83`

---

## Invariant verdict table

| # | Invariant | independent_status | severity | decisive bilateral anchor |
|---|-----------|--------------------|----------|---------------------------|
| 1 | Supervisor handles exec_command, subagent, AND workflow as background tasks | **confirmed_disparity** | HIGH | PY has three distinct record types — `BackgroundTaskRecord` (`task_supervisor.py:76-113`, subagent/agent), `CommandSessionRecord` (`117-126`), `WorkflowBackgroundRecord` (`129-148`) — with registrars (`register_command_session:589`, `register_workflow:459`, `launch:290`). RUST collapses all three into one `BackgroundTaskRecord` discriminated by `BackgroundTaskKind::{Agent,Subagent,Workflow}` (`supervisor.rs:46-54,69-88`). Only the **Subagent** kind has a reachable registration path (`SharedSubagentSupervisor::spawn:253-265` via `run_subagent`); `register_running` for `Agent`/`Workflow` has **zero production callers** (grep: only `dispatch.rs:16` `launch_background_tool`, itself uncalled, and tests). Command sessions are **not locally tracked at all** in Rust (only daemon-pulled, see §2). Net: exec_command (local) and workflow background tracking are unported. |
| 2 | exec_command status PULLS from the sandbox daemon (not a provider-level persistent shell session) | **confirmed_match** | — | Both pull a daemon count over the transport. RUST `control.rs:71-84` `command_session_count` (DaemonOp over `SandboxTransport`), consumed by the gate at `hooks.rs:528-534`. PY `daemon_invocations.py:70-83` `command_session_count`, consumed at `require_no_inflight_background_tasks.py:75`. Control timeout identical: RUST `CONTROL_TIMEOUT_S=15` (`control.rs:14`) == PY `_CONTROL_TIMEOUT_S=15` (`daemon_invocations.py:17`). No provider-level persistent shell session on either side. |
| 3 | Agent CANNOT submit terminal while any background task is still running (hard gate exists **and is enforced**) | **confirmed_match** | — | Gate wired on the terminals in `meta.rs:72-84` (`tool_hooks`) and **actually run** by the production pipeline: `dispatch_assistant_tools` → `dispatch_foreground_tools` → `execute_foreground_tool` → `execute_tool_once` (`execution.rs:30`), whose hook loop (`48-62`) short-circuits on the first `HookOutcome::Deny` **before** the body (`65`). `run_require_no_inflight` (`hooks.rs:501-570`) = `max(local, daemon)` with deny reasons `ephemeral_jobs_in_flight` / `command_session_count_unavailable` and `daemon_unavailable_bailout` fail-open — identical to PY `require_no_inflight_background_tasks.py:53-128`. *(Rust additionally gates `SubmitRootOutcome` (`meta.rs:72`), an explicit documented EOS scope-divergence, not a parity break of this invariant. The `local` population differs — NF-1; and the phantom-subagent block — NF-3.)* |
| 4 | Background completion surfaces back to the agent (notification / result injection) | **confirmed_disparity** | HIGH | PY query loop drives delivery: `loop.py:168` `collect_subagent_completion_notifications`, `:170` `collect_command_session_completion_notifications`, `:306` `terminate_for_parent_exit` on terminal — each emits `notify_system`. RUST: the completion path is **dead** — `BackgroundTaskSupervisor::complete`/`push_progress` (`supervisor.rs:167,220`) have **no production caller** (grep outside `supervisor.rs` is empty; only the unit test at `:332`). The Rust `run_query` loop (`loop_.rs:98-209`) has **no** `collect_*_completion` / per-terminal drain. Subagent/bg results never surface back. |
| 5 | Background execution is an engine dispatch mode (policy decides what is backgroundable) | **confirmed_disparity** | HIGH | PY: `context.py:53` `enable_background_tasks`, `loop.py:113-116` builds the supervisor when true, `loop.py:72` `defer_background_dispatch` routes a launch, `dispatch.py:196-248` `dispatch_background_tool_call` → async `launch`. RUST: `policy.rs` (`is_engine_background_tool`, `needs_background_manager`) and `dispatch.rs` (`launch_background_tool`) exist but have **zero production callers** (only re-exported in `mod.rs`). `enable_background_tasks` is **set** in prod (`agent/factory.rs:138`) but **never read** — `dispatch_assistant_tools` (`tool_call/dispatch.rs:205-303`) sends every runnable tool through `dispatch_foreground_tools` (inline `execute_tool_once`); there is no background-dispatch branch. `run_subagent` runs inline through the port (which only registers, §1/NF-3). The dispatch *mode* is not wired. |

Tally: **2 confirmed_match, 3 confirmed_disparity, 0 unproven. No `investigator_missed` (no investigation file existed).**

---

## Disparity adjudication

No investigation file `background_supervisor.md` existed, so there were no investigator-flagged disparities to
confirm / refute / adjust. All disparities below are independent New findings. The two invariants that hold
(2, 3) are recorded as `confirmed_match` to guard against a false-alarm "the whole area is dead code" reading:
the daemon command-session pull (§2) and the hard terminal-block hook (§3) are genuinely ported **and live in the
production dispatch path** (the hook loop in `execution.rs:48-62` was independently confirmed to run).

---

## Corrections to the prior verify

Two factual claims in the prior `background_supervisor.verify.md` are wrong against source and are corrected here:

- **`terminate_for_parent_exit` is NOT caller-less.** It has a real production caller at
  `entry.rs:67-73` (`RequestEntryHandle::shutdown` parent-exits the supervisor). It is, however, only the
  **request-teardown** drain — there is no **per-agent-terminal** drain equivalent to PY `loop.py:306`. The §4
  disparity stands on that narrower (correct) basis.
- **`enable_background_tasks` IS set in production** (`agent/factory.rs:138`), not test-only. The accurate
  statement is that it is set but **never read** in production dispatch/loop, so it gates nothing — the §5
  disparity stands.

---

## New findings

**NF-1 (HIGH) — the gate's `local` population differs structurally.**
PY `_local_count` calls `count_by_agent` (`task_supervisor.py:439-457`), which sums **(a)** running
*sandbox-bound* tasks (`_running_sandbox_task` requires `tracked.uses_sandbox`, `:213-219`), **(b)** running
`CommandSessionRecord`s for the agent, **(c)** *outstanding* `WorkflowBackgroundRecord`s — and by the
`uses_sandbox` filter excludes non-sandbox subagent work (docstring `require_no_inflight_background_tasks.py:8-9`:
"subagent / non-sandbox background work is not counted"). RUST `background_inflight_count` → `inflight_count`
(`supervisor.rs:212-217`) counts **every** record whose status is `Running`, regardless of kind or
sandbox-binding, and the Rust supervisor has no command-session/workflow records to add. Net: Rust counts
subagents that Python's sandbox-bound filter omits, and omits command sessions + workflows that Python counts
locally (Rust covers command sessions only via the daemon pull, §2). Different population on both ends.

**NF-2 (MED) — exec_command command sessions are not locally tracked in Rust.**
`model_tools/sandbox.rs:6-10` states command-session *registration with the background supervisor* +
recover/mark-reported were "relocated to `eos-engine`," but no such registration exists there:
`register_command_session` / `get_command_session_result` / `mark_..._reported` have **no Rust equivalent and no
caller**. For exec_command the only in-flight signal in Rust is the daemon's `command_session_count` (invariant 2
still holds via the daemon pull), but the Python local-completion path
(`collect_command_session_completion_notifications`, `loop.py:170`) is absent — long-running command results are
never injected back into the Rust transcript.

**NF-3 (HIGH) — `run_subagent` registers a phantom record that never executes and never completes.**
`run_subagent` is registered into the production registry (`model_tools/mod.rs:68`), so an agent can call it.
Production wires `SharedSubagentSupervisor::default()` (`entry.rs:116`). Its `spawn` (`supervisor.rs:253-265`)
**only** calls `register_running(..., BackgroundTaskKind::Subagent)` and returns a session id — it does **not**
execute the named subagent (no agent runner is launched; `RuntimeAgentRunner` is the *workflow*-agent runner,
`agent_runner.rs:30-72`, which merely passes the supervisor into tool metadata so the agent *can call*
`run_subagent`). Nothing ever calls `complete()` (`supervisor.rs:167`, zero production callers), so the record
stays `Running`, `inflight_count` counts it (NF-1), and the enforced gate (§3, confirmed in `execution.rs:48-62`)
**denies the agent's terminal for the rest of the run**. Two correctness gaps: (a) the subagent never runs and
its result never surfaces (= §4), and (b) the terminal is blocked. **Escapable, not permanent:** the agent can
clear the gate by calling `cancel_subagent`, which sets `Running→Cancelled` (`supervisor.rs:182-194`) and so
drops out of `inflight_count` (counts only `Running`). **Divergent dynamic vs Python:** PY does not trap the
agent — on terminal it *drains* running subagents (`terminate_for_parent_exit`, `loop.py:306`) and surfaces a
completion notification (`task_supervisor.py:794-839`); and a PY subagent actually runs (`dispatch.py:147-177`).
This is the most load-bearing behavioral divergence in the area.

**NF-4 (LOW) — value-level parity that DOES hold (false-alarm guards).**
- Terminal-status precedence is identical: PY `_TERMINAL_PRECEDENCE` (`task_supervisor.py:56-62`: running 0,
  cancelled 1, failed 2, completed 3, delivered 4) == RUST `BackgroundTaskStatus::precedence`
  (`supervisor.rs:30-38`). Cancel-vs-finish race resolves to COMPLETED on both (RUST `complete:174`, PY
  `_apply_terminal_status_transition:916-918`).
- Typed id prefixes match: `bg_`, `subagent_`, `wf_` (RUST `register_running:128-141`).
- These structures are correct in isolation but are exercised by **no live Rust completion path** (NF-2/NF-3) —
  parity of dormant data, not of behavior.

---

## Overall verdict

The Rust `background_supervisor` area is a **data-structure-faithful but behaviorally partial port**. Two
invariants hold for real and are *enforced* in the production path, and must not be flagged as disparities: the
daemon pull of exec status (§2) and the hard terminal-block hook, which `execute_tool_once` actually runs (§3).
The other three fail: the supervisor collapses three record types into one and only the Subagent kind is reachable
(and stubbed), with command sessions/workflows untracked locally (§1); nothing drives background completion back
to the agent — `complete`/`push_progress` have no production caller and the loop has no per-terminal drain (§4);
and the engine never enters a background-dispatch mode because the policy/dispatch helpers and
`enable_background_tasks` are never consulted, so tools execute inline (§5). The sharpest concrete bug is NF-3: a
real `run_subagent` call registers a never-executing, never-completing `Running` record that the (over-broad,
NF-1) Rust gate counts, blocking the agent's terminal for the rest of the run unless it manually cancels the
phantom subagent — whereas Python actually runs the subagent, drains on terminal, and surfaces the result.
