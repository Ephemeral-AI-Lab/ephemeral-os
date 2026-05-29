# Plan (1/2): isolated-workspace gates — prehooks (CODE)

Status: APPROVED (ralplan consensus — Architect SOUND-WITH-CHANGES → folded; Critic APPROVE).
Owner: planner handoff
Mode: SHORT consensus
Scope: two engine-layer prehooks + one thin engine wrapper over an EXISTING daemon op
(scenarios 4, 5, 6). Capabilities under test ALREADY EXIST; this is gate-hardening.

> **Companion:** `docs/plans/iws_concurrency_scenarios_TEST_PLAN.md` covers scenarios 0–3
> (replace_all/multi_edit complex+perf, concurrent background tasks, concurrent isolated
> workspaces + disk-O(1), 3-server-same-port + discard). That test plan's correctness depends on
> the gates in THIS plan being in place; this plan's exit-gate forces the
> `test_background_exit_iws_drains_agent_tasks` update (§5.5) that the test plan leaves alone.

---

## 0. Framing — what is actually missing

| # | Request | Current state | Real gap |
|---|---------|---------------|----------|
| 4 | Enter/exit isolated only when no background tasks | `enter` rejects in-flight bg at `isolated_workspace_lifecycle.py:48-60`; `exit` cancels/drains. | **Exit-side guard** + a uniform tool-boundary prehook on enter. **Genuine gap (exit).** |
| 5 | No `ask_advisor` while isolated | Not implemented. Plugin/LSP blocked at daemon (`dispatcher.py:251` `_plugin_block_decision`), but `ask_advisor` is an engine tool the daemon gate cannot see. The daemon op `api.isolated_workspace.status` (`dispatcher.py:326-345`, registered L422) already exposes `get_handle`; only the **engine wrapper** is missing. | A prehook on `ask_advisor` + a thin engine wrapper over the EXISTING `status` op. **Genuine gap.** |
| 6 | (added) No terminal submission while background tasks in flight | Only `AdvisorApprovalPreHook` on the 9 main terminals. | Same bg-count prehook on the 9 main terminals. **Genuine gap.** |

**Net:** two prehooks on the existing `@tool(pre_hooks=...)` slot + one thin engine wrapper over
the existing `api.isolated_workspace.status` op. No new subsystem, **no new daemon op**, no
engine state mirror.

---

## 1. PRD

### 1.1 Problem
Three lifecycle invariants are unenforced or only partially enforced:
- An agent can close out an attempt (terminal submission) while a sandbox-bound background task
  is still mutating the workspace → orphaned writes / lost results.
- `exit_isolated_workspace` silently cancels/drains in-flight background work rather than
  refusing — the agent never learns its work was dropped.
- `ask_advisor` can be called inside isolated mode, where helper-agent spawning and the
  not-OCC-published private workspace make advisor review semantically wrong.

### 1.2 Goals
1. **G1 (scenarios 4+6).** One reusable prehook `RequireNoInflightBackgroundTasks` that rejects a
   tool call when the calling agent has in-flight sandbox-bound background tasks. Wired to
   `enter_isolated_workspace`, `exit_isolated_workspace`, and the **9 main-role terminals**. Runs
   **before** `AdvisorApprovalPreHook` on terminals.
2. **G2 (scenario 5).** One prehook `BlockInIsolatedMode` on `ask_advisor` that rejects when the
   calling agent is in an active isolated workspace, reading isolated-state from the daemon
   (single source of truth) via a thin engine wrapper `sandbox.api.isolated_active(...)` over the
   EXISTING `api.isolated_workspace.status` op.

### 1.3 Non-goals
- **No new daemon op.** `api.isolated_workspace.status` already returns the `get_handle` verdict
  (`dispatcher.py:326-345`); G2 adds only the missing engine-side wrapper. A *new* parallel op is
  rejected as redundant (Architect finding).
- **No new dispatch-layer denylist subsystem and no engine "isolated" mirror flag.** Scenario 5
  is a single-member per-tool prehook; isolated-state comes from the daemon, the same source
  `_plugin_block_decision` uses. A mirror flag would be a second definition of "isolated" that
  can diverge — rejected (§6.2).
- **No change to `enter`'s lifecycle-layer check.** The prehook supplements (front-line at the
  tool boundary); the lifecycle check stays (authoritative, under entry lock, also covers the
  daemon count). Documented dedup, not accidental duplication (§6.1).
- **No removal of the exit drain path.** The exit prehook refuses while bg in-flight; drain
  remains as defense-in-depth and closes the check→complete race (§6.5). (Q2 = "keep
  exit-drain + add guard".)

### 1.4 Success criteria
- **SC1.** With ≥1 in-flight sandbox-bound bg task, each of `enter`/`exit`/the 9 terminals is
  rejected with a typed reason; with zero, all pass.
- **SC2.** On terminals, when both gates would fire, the **background** rejection surfaces
  (order: bg-count → advisor — confirmed: `hook_pipeline.py:75-84` short-circuits on the first
  `fail`), asserted by a wiring + ordering test.
- **SC3.** `ask_advisor` is rejected while isolated and passes when not; the verdict is sourced
  from the daemon `status` op, proven by an op/wrapper test.
- **SC5.** All new prehooks have unit + wiring tests; `test_advisor_gate_wiring.py`'s analog is
  extended for the bg-count gate; the **changed** mock scenario
  (`test_background_exit_iws_drains_agent_tasks`) is updated to the new exit contract; `ruff` +
  `mypy` clean on changed lines.

---

## 2. Architecture facts (verified against code; Architect-confirmed)

- **Prehook mechanism.** `ToolPreHook` protocol (`tools/_framework/core/hooks.py:66-77`),
  pipeline `run_pre_hooks` (`tools/_framework/execution/hook_pipeline.py:50-108`) iterates
  `getattr(tool, "pre_hooks", ())` sequentially and **short-circuits on the first `fail`**
  (`hook_pipeline.py:75-84`). A prehook failure produces a **`hook_failure` ToolResult**
  (`hook_pipeline.py:283-328`), NOT a `LifecycleError(kind=...)`. `validate_hook_targets`
  (`hooks.py:110-116`) requires each hook's `target_tool == tool_name`.
- **Prehook context reach (feasibility gate — GREEN).** `ExecutionMetadata`
  (`tools/_framework/core/runtime.py`) exposes typed fields `sandbox_id` (L31),
  `background_task_manager` (L59), `agent_run_id` (L32), `agent_name` (L33), reachable via
  `context.get(...)`. Prehooks are `async`, so awaiting the daemon op is fine.
- **Background count sources.** Engine-local:
  `BackgroundTaskSupervisor.count_by_agent(agent_id)`
  (`engine/background/task_supervisor.py:352-360`) counts **running, sandbox-bound** tasks only
  (`_running_sandbox_task` gates on `uses_sandbox`). Daemon-visible:
  `sandbox.api.inflight_count(sandbox_id, agent_id)` (`sandbox/api/daemon_invocations.py:51-65`;
  dispatched via the **plain non-slot path** — `_is_plugin_op` only matches
  `api.plugin.`/`plugin.`, `dispatcher.py:136-137`). The lifecycle check already takes
  `max(local, daemon)` (`isolated_workspace_lifecycle.py:49-51`) and already **fail-safe-blocks**
  on daemon error (`isolated_workspace_lifecycle.py:78-86`, `kind="inflight_count_unavailable"`).
- **Isolated-state authority + EXISTING op.** `IsolatedPipeline.get_handle(agent_id)`
  (`sandbox/isolated_workspace/pipeline.py:156`) is the daemon-internal truth;
  `_plugin_block_decision` (`dispatcher.py:251-274`) uses `get_active_pipeline().get_handle(...)`.
  `_isolated_workspace_status` (`dispatcher.py:326-345`, registered `api.isolated_workspace.status`
  at L422) returns `{"success": True, "open": bool}` from `get_handle`, and on
  no-bootstrapped-pipeline returns the error payload (no `open` key). **No engine wrapper exists**
  in `daemon_invocations.py` → §5 adds a thin `isolated_active` wrapper reading
  `response.get("open", False)`.
- **Main-terminal set (9), Architect-verified.** All already carry `AdvisorApprovalPreHook`
  (e.g. `submit_execution_success.py:34`): `submit_execution_success|blocker|handoff`,
  `submit_plan_closes_goal|defers_goal`, `submit_evaluation_success|failure`,
  `submit_verification_success|failure`. Helper terminals `submit_advisor_feedback`,
  `submit_exploration_result` are intentionally ungated — same scope. The contract test
  `test_advisor_gate_wiring.py:20-57` `isinstance`-filters `AdvisorApprovalPreHook` (L49-51), so
  **adding the bg hook to the tuple is additive and does not break it**.

---

## 3. Decisions (locked)

| # | Decision | Source |
|---|----------|--------|
| D1 | Scenario 5 = single-member per-tool prehook `BlockInIsolatedMode` on `ask_advisor`; not a dispatch denylist subsystem. The user's "denylist" = a one-tool prehook, extensible by wiring the same hook to more tools. | user + advisor |
| D2 | Isolated-state read from the **daemon** via the EXISTING `api.isolated_workspace.status` op (thin engine wrapper `isolated_active`); **no new daemon op, no engine mirror flag**. | Architect + advisor |
| D3 | Background tasks may still be launched in isolated mode; exit is **gated by prehook** (reject while in-flight). Drain stays as fallback and closes the check→complete race (§6.5). | user Q2 |
| D4 | One reusable prehook `RequireNoInflightBackgroundTasks` on enter/exit/9-terminals; checks `max(local count_by_agent, daemon inflight_count) > 0`. | scope unification |
| D5 | "No background tasks" = **running sandbox-bound** tasks (matches `count_by_agent`/`inflight_count`). Subagent/non-sandbox bg tasks are NOT counted. Documented in prehook + prompt. | code semantics |
| D6 | Bg-count prehook runs **before** `AdvisorApprovalPreHook` on terminals (the bg rejection is the one surfaced — short-circuit confirmed). | advisor + `hook_pipeline.py:75-84` |
| D7 | Gate applies to **all 9 main terminals**. **Confirmed-in-flight** (`max>0`) blocks all 9; escape = `cancel_background_task`. On the **daemon-error branch** (count indeterminate, local==0), success/handoff terminals **fail-safe-block** but **failure/blocker terminals fail-open** so the agent can always bail. Bail-out set: `submit_execution_blocker`, `submit_evaluation_failure`, `submit_verification_failure`, `submit_plan_defers_goal`. | Architect tradeoff |
| D8 | Enter prehook **supplements** the lifecycle check (front-line at tool boundary); lifecycle check retained (authoritative, under entry lock). | advisor |
| D10 | Scenario-6 integration test runs **non-isolated** (else the advisor gate, not the bg gate, blocks — `ask_advisor` is blocked while isolated, so advisor-gated terminals are de-facto unsubmittable while isolated). | advisor |

---

## 4. Consequence to document: advisor-gate × isolated mode

Because (a) every main terminal requires a prior `ask_advisor` approval and (b) G2 blocks
`ask_advisor` while isolated, **terminal submission is impossible while isolated** — the agent
must `exit_isolated_workspace` before submitting. Intended; matches the isolated-mode model
(private scratch; exit to publish/handoff). State it in the `ask_advisor` prompt and the
isolated-workspace architecture page. The scenario-6 test runs non-isolated (D10). (On a
daemon-error bail-out, `BlockInIsolatedMode` fails open — §6.4 — so the failure/blocker bail path
of D7 remains viable.)

---

## 5. File-by-file change list

### Workstream A — `RequireNoInflightBackgroundTasks` (scenarios 4 + 6)

1. **NEW `backend/src/tools/_hooks/require_no_inflight_background_tasks.py`** (neutral home so
   both `submission/` and `isolated_workspace/` import without a layering cycle).
   - Class with `target_tool: str`, `name = f"no_bg_tasks:{target_tool}"`, async `run`.
   - `agent_id = (context.get("agent_run_id") or context.get("agent_name") or "").strip()`.
   - `local = mgr.count_by_agent(agent_id)` (`mgr = context.get("background_task_manager")`, 0 if
     absent). `sandbox_id = context.get("sandbox_id")`; if empty → `daemon = 0`. Else
     `daemon = await sandbox_api.inflight_count(sandbox_id, agent_id)` inside `try/except`.
   - **Decision logic:**
     - `local > 0` → **fail** (confirmed in-flight), regardless of target.
     - daemon call OK and `daemon > 0` → **fail** (confirmed in-flight).
     - daemon call **errors** and `local == 0` (indeterminate): if `target_tool` in the
       **bail-out set** (D7) → **pass** (fail-open, log reason `daemon_unavailable_bailout`);
       else → **fail** (fail-safe-block, reason `inflight_count_unavailable`).
   - `HookResult.fail(...)` carries a parallel **reason tag** in `metadata` (e.g.
     `ephemeral_jobs_in_flight`, `inflight_count_unavailable`); the agent-facing message names the
     count and points at `cancel_background_task`. NOTE: failure is a `hook_failure` ToolResult
     shape (`hook_pipeline.py:283-328`), distinct from the lifecycle `LifecycleError` shape (§6.1).
2. **`backend/src/tools/isolated_workspace/enter_isolated_workspace/definition.py`** — add
   `RequireNoInflightBackgroundTasks("enter_isolated_workspace")` to `pre_hooks`.
3. **`backend/src/tools/isolated_workspace/exit_isolated_workspace/definition.py`** — add
   `RequireNoInflightBackgroundTasks("exit_isolated_workspace")` to `pre_hooks`.
4. **The 9 main-terminal definition modules** — add
   `RequireNoInflightBackgroundTasks("<own_name>")` as the **first** element of `pre_hooks`,
   before the existing `AdvisorApprovalPreHook` (D6). Files: `submission/executor/...`,
   `submission/planner/...`, `submission/evaluator/...`, `submission/verifier/...` (the 9 in §2).
5. **UPDATE `backend/src/task_center_runner/tests/mock/sandbox/background_tool/test_background_exit_iws_drains_agent_tasks.py`** and its probe
   **`backend/src/task_center_runner/agent/mock/background_shell_probe.py` (~L1128-1281)** — this
   scenario drives BOTH the **enter** and **exit** tools (prehooked) with a live sandbox-bound bg
   task. **Two** assertions change because both calls now short-circuit at the bg prehook into a
   `hook_failure` ToolResult (no `LifecycleError` payload):
   - **Enter (probe ~L1166-1175; assert L55-57):** today asserts
     `summary["blocked_enter_payload"]["error"]["kind"] == "ephemeral_jobs_in_flight"` (the
     `LifecycleError` shape). With the enter prehook the call returns a `hook_failure` ToolResult,
     so **retarget** the assertion to the prehook's metadata reason-tag
     (`HookResult.metadata["reason"] == "ephemeral_jobs_in_flight"`, §5.1), NOT
     `payload["error"]["kind"]`.
   - **Exit (probe ~L1224; assert L61):** today asserts drain-success
     (`assert not summary["iws_exit"]["is_error"]`). **Inverts** to the **new contract**: exit is
     **refused** while bg in-flight → the agent **cancels via the `cancel_background_task` tool**
     (route through the real tool, NOT the probe's current direct `default_task.cancel()`, so the
     test exercises the agent path — this adds a probe dependency on that tool) → exit succeeds.
   The *unchanged* host-coroutine drain semantics remain covered by
   `test_isolated_workspace_lifecycle_background.py` (L57/98/137), which call the coroutines
   directly and **bypass** the tool prehook (they keep asserting the `LifecycleError` shape).
   (Architect change #2 + Critic Finding 1 — blocks correctness/coverage.)

### Workstream B — `BlockInIsolatedMode` + engine wrapper over the EXISTING status op (scenario 5)

6. **`backend/src/sandbox/api/daemon_invocations.py`** — add
   `async def isolated_active(sandbox_id, agent_id, *, transport=None) -> bool` calling the
   EXISTING op `api.isolated_workspace.status` with `{"agent_id": agent_id}` and returning
   `bool(response.get("open", False))` (the no-pipeline error payload has no `open` key → False).
   Add to `__all__`. If the literal-vs-constant convention requires it, add a
   `DAEMON_OP_ISOLATED_WORKSPACE_STATUS = "api.isolated_workspace.status"` constant in
   `transport.py`; the builtin registration currently uses the literal string. **No daemon-side
   change.**
7. **NEW `backend/src/tools/_hooks/block_in_isolated_mode.py`** — prehook
   `target_tool="ask_advisor"`; `agent_id` from context; `sandbox_id = context.get("sandbox_id")`
   (empty → pass, cannot be isolated without a sandbox). `active = await
   sandbox_api.isolated_active(sandbox_id, agent_id)` inside `try/except`; on daemon error **log +
   pass** (fail-open — advisor is read-only; a stuck agent is worse than a rare missed block,
   §6.4). `HookResult.fail("BLOCKED: ask_advisor is unavailable inside an isolated workspace;
   exit_isolated_workspace first.")` when `active`.
8. **`backend/src/tools/ask_helper/ask_advisor/ask_advisor.py`** — add
   `pre_hooks=(BlockInIsolatedMode("ask_advisor"),)` to the `@tool(...)` decorator.
9. **`backend/src/tools/ask_helper/ask_advisor/` prompt** — one line: not callable inside an
   isolated workspace; exit first. Note the §4 consequence.

### Prehook tests

- `test_require_no_inflight_background_tasks.py` (unit): pass at 0; fail when local>0; fail when
  daemon>0; **daemon-error branch** — success/handoff fail-safe-block, failure/blocker fail-open
  (D7); agent-id resolution; subagents/non-sandbox not counted (D5).
- `test_block_in_isolated_mode.py` (unit): block when active; pass when inactive / no sandbox_id /
  **daemon error (fail-open)**.
- Wrapper/op test: `isolated_active` returns True iff `status.open` True; False on no-pipeline
  error payload.
- Wiring test (extend/mirror `test_advisor_gate_wiring.py`): bg-count prehook present on
  enter/exit + all 9 terminals, absent on helpers; **per terminal assert the hook's `target_tool`
  equals that terminal's own name** (mirror `advisor_hooks[0].target_tool == name` at
  `test_advisor_gate_wiring.py:55`; guards the 11-site copy-paste against a wrong `<own_name>`,
  which would otherwise throw via `validate_hook_targets`, `hooks.py:110-116`); ordered **before**
  advisor on terminals (D6); `ask_advisor` carries `BlockInIsolatedMode`.

---

## 6. Considerations / risks

### 6.1 Enter double-gating + failure-shape (D8)
Enter prehook and `isolated_workspace_lifecycle.py:48-60` both reject on in-flight bg work —
**intentional layered defense**: the prehook fails fast at the tool boundary; the lifecycle check
runs later under the entry lock and is authoritative during namespace setup (and is what the
daemon-side mock tests assert). **Shape difference:** a prehook failure is a `hook_failure`
ToolResult (`hook_pipeline.py:283-328`), NOT a `LifecycleError(kind="ephemeral_jobs_in_flight")`.
**Known tool-driven assertion this changes:** `test_background_exit_iws_drains_agent_tasks` drives
BOTH the enter and exit *tools* with a live bg task — §5.5 retargets its enter assertion to the
prehook's `HookResult.metadata["reason"]` and inverts its exit assertion. During execution, **grep
`ephemeral_jobs_in_flight` across tests and confirm the only tool-driven (not coroutine)
assertions are the two §5.5 updates** — the host-coroutine unit tests assert the `LifecycleError`
shape and are unaffected (they bypass prehooks). The bg prehook carries the same reason tag in
`metadata`, so the retargeted assertion stays semantically equivalent. If single-gating is
preferred, the fallback is enter→lifecycle only + prehook on exit/terminals, but then enter/exit
gate at different layers (the inconsistency D4 avoids).

### 6.2 Why no engine mirror flag (D2)
A `BlockInIsolatedMode` reading an engine-local "isolated" flag would be a second definition of
isolated-state that can diverge from the daemon on drain/teardown/crash paths. `ask_advisor` is
rare and expensive (spawns an ephemeral agent), so one daemon round-trip per call is negligible.
Querying the daemon (`status`) keeps a single source of truth. Rejected alternative: the
runner-side audit signal `set_isolated_active`
(`backend/src/task_center_runner/audit/daemon_pull.py:180`) — audit-cadence, not authoritative
per-agent.

### 6.3 Terminal trap vs escape (D7)
Confirmed-in-flight gates all 9 terminals; escape = `cancel_background_task` (named in the
message). We accept this (a live sandbox-bound task closing alongside attempt closure orphans
writes — true even for failure terminals). The **daemon-error** branch is the footgun the
Architect flagged: a flaky daemon could otherwise lock the agent out of *bailing out* (and
`cancel_background_task` itself routes to the daemon), compounded by no-terminal turns counting
toward the hard ceiling (commit `d0db7b3`). D7 resolves it: on daemon error with local==0,
**failure/blocker terminals fail-open** so the agent can always bail; success/handoff still
fail-safe-block.

### 6.4 Daemon op failure-mode asymmetry (deliberate)
- **bg-count gate** (close-out path): confirmed-in-flight → block; daemon error → block
  (success/handoff) / pass (failure/blocker, D7). Refusing to orphan a live task dominates.
- **`ask_advisor` block** (read-only path): daemon error → **log + pass**. A stuck agent is worse
  than a rare missed advisor block.
Both branches are tested (§5 Prehook tests).

### 6.5 Exit-gate vs drain race — closed by keeping the drain (D3)
The TOCTOU window between the exit prehook's check and a task completing/relaunching is harmless
**because the drain is retained**: any task that slips past the prehook is still drained by
`_cancel_by_agent` (`isolated_workspace_lifecycle.py:111`). Removing the drain would reopen this
race — hence D3 keeps it. The bg gate's `max(local, daemon)` is the lifecycle formula; a count
dropping to zero between check and execution only causes a correct pass on retry (no false-pass).

### 6.6 Slot-skip rationale (doc, prevents a false bug-flag)
`isolated_active` (via `status`) and `inflight_count` **do not** need `acquire_dispatch_slot`,
unlike `_plugin_block_decision` (`dispatcher.py:94,251-257`). The slot exists there to hold
`inflight>0` across the check **and the subsequent in-band plugin op** so `begin_exit_drain` can't
tear down mid-op. These queries have **no subsequent operation** — a single atomic
`get_handle`/registry-count on the daemon's single asyncio loop — and `inflight_count` already
dispatches via the plain non-slot path (`dispatcher.py:115,136-137`). For enter/exit/ask_advisor
(the calling agent's own serial tool calls) a microsecond-stale verdict at worst causes a retry,
never a dangerous false-pass.

---

## 7. RALPLAN-DR summary

### Principles
1. Reuse the existing prehook slot **and the existing daemon op**; add no new gate subsystem and
   no new daemon op.
2. Single source of truth for isolated-state (daemon `get_handle` via `status`).
3. Fail-safe on the close-out path (with a daemon-error bail-out exemption); fail-open on the
   read-only advisor path.
4. Every gate behavior is asserted (unit + wiring + ordering + the changed scenario).

### Decision drivers (top 3)
1. Minimal mechanism (the user simplified twice — bias to least structure).
2. Correctness of attempt closure (no orphaned background writes; but never hard-lock the bail
   path).
3. No divergent definitions of "isolated" / "in-flight."

### Options
- **Gate mechanism — A (CHOSEN): two per-tool prehooks on `@tool(pre_hooks=...)`.** Pros: reuses
  shipped machinery, instance-per-target like `AdvisorApprovalPreHook`, no engine state, wiring
  test is additive. Cons: 11 attach points (mechanical; covered by a wiring test).
- **Gate mechanism — B: dispatch-layer policy + engine isolated flag.** Pros: one attach point.
  Cons: new subsystem + a second isolated-state definition; contradicts drivers 1, 3. Rejected.
- **Isolated-state — A (CHOSEN): thin engine wrapper over the EXISTING `api.isolated_workspace.status`.**
  Pros: single source of truth, **zero new daemon code**. Cons: must map the no-pipeline error
  payload → not-isolated (handled by `response.get("open", False)`).
- **Isolated-state — B: new `isolated_active` daemon op.** Pros: graceful `open:false` when no
  pipeline. Cons: duplicates an existing registered op; contradicts driver 1. Rejected.
- **Isolated-state — C: engine mirror flag.** Cons: divergence risk (driver 3). Rejected (§6.2).

---

## 8. ADR

**Title.** Isolated-workspace gates (two prehooks + reuse of the existing isolated-status op).

**Decision.** Add `RequireNoInflightBackgroundTasks` (enter/exit/9 main terminals, before the
advisor gate; daemon-error bail-out exemption for failure/blocker terminals) and
`BlockInIsolatedMode` (`ask_advisor`) as per-tool prehooks; add a thin engine wrapper
`isolated_active` over the EXISTING `api.isolated_workspace.status` daemon op so the engine reads
isolated-state from the authoritative `get_handle`. Update the one existing mock scenario the exit
gate changes.

**Drivers.** Minimal mechanism; correct attempt closure without hard-locking the bail path; single
definition of isolated/in-flight.

**Alternatives considered.** (a) Dispatch-layer denylist + engine isolated flag — rejected
(subsystem + divergent state). (b) New `isolated_active` daemon op — rejected as redundant with
the existing `status` op. (c) Engine mirror flag — rejected (divergence). (d) Fail-safe-block ALL
terminals on daemon error — rejected (hard-locks the bail path; D7 exempts failure/blocker).

**Why chosen.** The user simplified twice; two per-tool prehooks on the shipped slot + a thin
wrapper over an existing op is the least mechanism that enforces all three invariants.

**Consequences.**
- Positive: invariants enforced with ~2 small hook modules + 1 thin wrapper + 11 one-line
  wirings; isolated-state has one definition; the wiring contract test stays green (additive).
- Negative / accepted: terminal submission is impossible while isolated (§4, intended); all 9
  terminals gated when confirmed-in-flight (escape via `cancel_background_task`, §6.3); enter is
  double-gated by design (§6.1); daemon-op failure modes are asymmetric by design (§6.4); one
  existing mock scenario must be updated to the new exit contract (§5.5).

---

## 9. Verification (run with `.venv/bin/pytest`, never global pytest)
- Unit + wiring + ordering + daemon-error-branch tests (§5 Prehook tests) → SC1, SC2, SC3, SC5.
- Updated mock scenario `test_background_exit_iws_drains_agent_tasks` (new exit contract) → SC5.
- During execution: grep `ephemeral_jobs_in_flight` across tests and confirm the only tool-driven
  assertions are the two §5.5 updates; coroutine-driven tests keep the `LifecycleError` shape
  (§6.1).
- `ruff check` + `mypy` clean on changed lines.

## 10. Open questions (for user override)
1. Confirm the §4 consequence (no terminal submission while isolated) is acceptable as the
   intended workflow.
2. (Resolved by D7) Failure/blocker terminal exemption is scoped to the **daemon-error branch
   only**, not a blanket exemption. Flag if a blanket exemption is desired instead.
