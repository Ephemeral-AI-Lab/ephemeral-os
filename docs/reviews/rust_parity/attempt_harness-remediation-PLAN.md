# Attempt harness — Rust parity remediation plan (PLAN ONLY)

Status: **plan only, do not implement.** Scope: the `agent-core / attempt_harness`
findings in `docs/reviews/rust_parity/REPORT.html` (areas/attempt_harness + verify):
the HIGH production-drivability gap (**D5**) and its drive-model corollary (**D4**),
plus the two other real, separable gaps — **D6** (generator role gate) and **D8**
(workflow-audit events). The low/intentional items (D1, D2, D3, D7) are recorded at
the end as one-liners.

Verified against the Python reference in `backend/src/workflow/attempt` + the
`backend/src/tools/submission` seam, and the current Rust under
`agent-core/crates/{eos-workflow,eos-runtime,eos-tools}`.

> **Decision changed (2026-06-03).** An earlier draft of this plan committed §2 to
> *return-value capture* (Path B). It now commits to **Path A-recording** — the
> tool-port drive option that `D4` itself sanctions ("pick ONE drive path: return-value
> capture OR tool-port"). Recording is net-negative (deletes the `AgentTerminal`
> machinery instead of adding a capturing shim), strictly closer to Python's drive
> shape, and restores agent-facing validation feedback that capture silently drops.
> Capture is retained as a viable alternative in §9.

---

## 0. The issue in one paragraph

The attempt-harness *logic* in `eos-workflow` is a faithful, well-tested Rust port
(PLAN→RUN→CLOSED, the reducer exit gate, needs-driven dispatch, the per-attempt
registry, the full close cascade, and the entire constant/operator set all match
Python). The defect is **production wiring, not logic**: the only non-test
`AgentRunner` — `RuntimeAgentRunner` — runs every workflow agent with
`plan_submission = None` (`eos-runtime/src/tool_context.rs:90`) and **always** returns
`AgentRunReport::no_terminal(...)` (`eos-runtime/src/agent_runner.rs:111-115`). So in a
default build no planner/generator/reducer can ever produce a terminal: the planner is
synthesized to `run_exhausted` and the attempt closes FAILED/TASK_FAILED **before any
plan runs**. The reducer exit gate (invariant 3) and DAG dispatch (invariant 4) are
correct in unit tests but **unreachable in the live runtime**, so the close cascade can
only ever reach `Workflow = Failed`, never `Succeeded`. This is the single largest
functional parity gap in the area — flagged in the module doc as "Phase-7," so it is
documented incompleteness, not a silent bug. The fix is to complete Phase-7.

---

## 1. Root cause: terminal capture was deferred, leaving zero working drive paths

Python's drive model (ground truth, `backend/src/workflow/attempt/launch.py`):

- The launcher runs the agent fire-and-forget through the engine
  (`EphemeralAttemptAgentLauncher._run_launch`, `launch.py:182-265`), attaching
  `attempt_runtime=runtime` to the run's `ExecutionMetadata` (`launch.py:203`).
- **The submission tool drives the harness during the run.** A
  `submit_planner/generator/reducer_outcome` resolves `attempt_runtime` from metadata →
  the per-attempt orchestrator → `apply_*_submission`, mutating attempt state and
  advancing the DAG *inside* the agent run.
- **The runner's return value is not the terminal.** After the run, the launcher only
  asks "did the task stay RUNNING?" (i.e. the agent never submitted) and, if so,
  synthesizes the matching failure (`_report_unfinished_running_task`, `launch.py:267-281`;
  the still-RUNNING guard at `:276-279` is load-bearing). On the happy path the tool
  already advanced, so this is a no-op.

Rust deferred terminal capture, and the deferral disabled **both** possible drive paths:

- **Tool-port path (Python's model) is dead.** The Rust submit tools *do* call
  `ctx.require_plan_submission()` (`eos-tools/.../submission.rs:227,278,396`) against an
  `ExecutionMetadata.plan_submission` port (`eos-tools/src/metadata.rs:76`) and already
  map the result `Accepted → ToolResult::ok` / `Rejected → error`
  (`submission.rs:303-308`). But `RuntimeAgentRunner` builds metadata with
  `plan_submission: None` (`tool_context.rs:90`), so every workflow-agent terminal fails
  `ToolError::MissingPort("plan_submission")` (`metadata.rs:183-186`).
- **Return-value path (Rust's intended design) is also unfed.** The harness is *built*
  for it — the single-writer JoinSet loop applies a returned `AgentRunReport::terminal`
  via the non-advancing `record_*` variants (`run_stage.rs:195-241`,
  `orchestrator.rs:491-543`), and the planner leg consumes `report.terminal`
  (`orchestrator.rs:132-164`). But `RuntimeAgentRunner` never captures a terminal, so it
  always returns `no_terminal`.

Net: neither path fed → every run = exhaustion → attempt FAILED.

The `PlanSubmissionAdapter` that *would* implement the tool-port path in production
(`eos-workflow/src/ports.rs:26-84`) is exported (`lib.rs:39`) but **has zero `::new`
call sites** — dead. Its methods call the *advancing* orchestrator variants
`apply_generator_submission` / `apply_reducer_submission` (`ports.rs:69,82` →
`orchestrator.rs:470-489`), whose only caller is that dead adapter.

---

## 2. Design decision: Path A-recording (tool writes, loop watches)

There are **three** drive options, not two — the distinction that decides this plan:

| Option | Tool calls | Who advances the DAG | Single-writer? |
|---|---|---|---|
| **A-advancing** | `apply_*_submission` (advancing) | the tool, mid-run (spins a *fresh* advancer) | **no — D4 hazard** |
| **A-recording** ✅ | `record_*` (non-advancing) | the one owning loop | **yes** |
| **B-capture** | nothing (port stashes a slot) | the owning loop, from the runner's returned terminal | yes |

**Decision: A-recording.** Wire a *recording* `PlanSubmissionPort` (the repurposed
`PlanSubmissionAdapter`, pointed at the non-advancing `record_*` variants) into the
workflow-agent metadata. The submit tool writes the agent's real submission straight to
the orchestrator and returns the orchestrator's real ack; the single `advance_run_stage`
loop stays the sole launcher and sole closure-decider; the runner shrinks to a thin
engine-run wrapper that reports only success/error. Then delete the `AgentTerminal`
machinery and the advancing variants.

**Why recording, not the other two:**

- **vs A-advancing (rejected — the D4 hazard):** wiring the *existing* adapter routes the
  tool into `apply_*_submission`, which each spin a fresh `AttemptStageAdvancer`
  (`orchestrator.rs:475,486`). A submit firing inside `runner.run()` would launch a
  nested advancer racing the owning JoinSet loop, and the loop's `apply_report` would
  then hit "task ... is not running" (`orchestrator.rs:565-569`) and error the stage.
  Recording dodges this entirely: the tool only *marks its own task*; advancing stays the
  loop's exclusive job.
- **vs B-capture (viable, but heavier — see §9):** capture keeps the `AgentTerminal`
  enum, `AgentRunReport.terminal`, `apply_terminal`, and a per-run `Mutex` slot, and —
  decisively — its capturing port *always* returns `Accepted`, so a stale/invalid
  submission is silently accepted and the validation error surfaces out-of-band in the
  loop, invisible to the agent. Recording returns the orchestrator's real ack →
  `ToolResult::error` → the agent retries (terminal tools stamp loop-exit only on
  success). That is Python's `is_error` behavior; capture introduces a parity gap there.

**Relationship to the audit:** this is **not** a reversal of a finding — it is a
selection from D4's *own* sanctioned menu ("return-value capture OR tool-port"). It does
diverge from the `agent_runner.rs:1-13` module doc and D5's suggested-fix wording, both
of which named *capture*; this plan overrides that wording on simplicity + fidelity
grounds (CLAUDE.md biases hard to net-negative end-state).

**Two parity/quality wins recording gets that capture loses:**
1. **Agent-facing validation feedback** — real `Accepted`/`Rejected` ack reaches the
   agent (Python parity).
2. **No reconstruction hop** — the tool persists the submission the agent actually made,
   rather than the runner rebuilding it into an enum for the loop to re-apply.

---

## 3. The drive workflow after the change

```
 start() → upsert planner Task=Running ; spawn_planner_run(launch)          [eos-workflow]
       │
       ▼ RuntimeAgentRunner.run(planner launch)                            [eos-runtime]
         ├─ metadata.plan_submission = Some(recording)   ◄══ the one wiring change
         └─ run_ephemeral_agent → agent calls submit_planner_outcome(plan)  [eos-tools]
                  recording.apply_plan(plan) ─► orchestrator.record_plan(plan)        [eos-workflow]
                      ├─ validate shape / acyclic / ROLE (D6)
                      ├─ materialize generator+reducer Tasks (Pending)
                      ├─ planner Task=Done ; stage:=RUN          ◄── NO advance here
                      └─ return Ack
                  tool: Accepted→ok | Rejected→error → agent RETRIES
       ◄── run ends (failure_summary?) ──┘
       ▼ settle_planner(planner task):
           Done?           → advance_run_stage() ─────────────────────────┐
           Running (died)? → synthesize_planner_failure → close_attempt(FAILED)
                                                                          ▼
   advance_run_stage()   ══ SINGLE WRITER: owns launching + closure ══
     loop:
       for each ready (needs==Done), while in-flight < max_concurrent_task_runs:
           Task:=Running ; JoinSet.spawn( runner.run(gen/reducer launch) )
                 └─ engine → submit_generator/reducer_outcome
                      recording.submit_generator(sub) ─► orchestrator.record_generator_submission(sub)
                          └─ mark_execution_task: Task:=Done/Failed + outcome + terminal_tool_result
                      tool: real ack → ok/error → loop exits
       join_next():
           task still Running? → synthesize_failure(run_exhausted)   ◄══ the ONLY post-run job
           else                → noop (tool already recorded)
       dag_status: all_done → close_attempt(PASSED) ; any_failed → close_attempt(FAILED) ; else continue
```

The entire right column of capture — slot, `AgentTerminal`, runner reconstruction,
`apply_terminal` re-application — collapses to "the tool already wrote it; the loop only
catches a dead agent."

---

## 4. The changes (diff table)

`✎` modify · `✚` add · `🗑` delete-content · `▷` no change (semantics shift only)

| # | File (crate) | Δ | What changes | Why |
|---|---|---|---|---|
| 1 | `eos-workflow/attempt/launch.rs` | 🗑✎ | **Delete `AgentTerminal`** (4-variant enum, `:21-30`); thin `AgentRunReport` to `{ failure_summary: Option<String> }` (ctors → `ok()`/`failed(s)`) | Scaffolding for the capture round-trip; recording doesn't ferry a submission back. |
| 2 | `eos-workflow/ports.rs` | ✎ | **Repurpose `PlanSubmissionAdapter`** (`:48-84`): `apply_plan`→`record_plan`, `submit_generator`→`record_generator_submission`, `apply_reducer`→`record_reducer_submission` | Turns the dead tool-port into the live **recording** driver — single-writer-safe (marks tasks only). |
| 3 | `eos-workflow/attempt/orchestrator.rs` | 🗑✎✚ | **Delete** advancing `apply_generator_submission`/`apply_reducer_submission` (`:469-489`); **split** `record_plan` (materialize+RUN, *no* advance) out of `apply_plan_submission` (drop the `advance_run_stage()` tail at `:440-442`); **replace** `apply_planner_report` (`:132-164`) with `settle_planner` (task-status check, not enum-match); **add** D6 `AgentRole::Generator` gate in `materialize_plan_tasks` (`:224-229`) | Removes the second drive path (closes D4). `record_plan` lets the planner tool persist without blocking on the whole run stage. `settle_planner` swaps enum dispatch for a status check. |
| 4 | `eos-workflow/attempt/run_stage.rs` | 🗑✎ | **Delete `apply_terminal`** (`:222-241`); replace `apply_report` (`:195-220`) with `settle_or_synthesize(launch)` = *"task still Running? → synthesize `run_exhausted` : noop"* | The loop stops applying a returned terminal; its only post-join job is Python's still-RUNNING exhaustion guard. |
| 5 | `eos-workflow/attempt/run_stage.rs` | ✚ | *(D8, separable)* emit `task_ready`/`task_launched`/`task_failed` via `self.orchestrator.deps().audit_sink` | Closes the dropped audit subsystem (observability only). |
| 6 | `eos-runtime/agent_runner.rs` | ✎🗑 | `run()` wires `plan_submission = Some(recording)`, runs the engine, returns `AgentRunReport{ failure_summary: run.error }`. **No slot, no capture, no terminal build.** Rewrite the module doc | The runner collapses to a thin engine-run wrapper — simpler than capture (no per-run slot). |
| 7 | `eos-runtime/tool_context.rs` | ✎ | Add `plan_submission: Option<Arc<dyn PlanSubmissionPort>>` to `MetadataParams` (`:16-34`, default `None`); stamp onto `ExecutionMetadata` (`:90`). **Root stays `None`** (`root_agent.rs:68`) | Carries the recording port into workflow-agent tool calls without gating root. |
| 8 | `eos-runtime/entry.rs` | ✎ | Construct `PlanSubmissionAdapter::new(orchestrator_registry.clone())` (registry already at `:140`); pass into `RuntimeAgentRunner::new(...)` (`:151`) | The single construction site that makes the path live. The adapter is **stateless + shared** across all runs. |
| 9 | `eos-tools/model_tools/submission.rs` | ▷ | **No code change.** Already builds the typed submission, calls the port, maps `Accepted→ok`/`Rejected→error` | Tool layer was *already* recording-shaped; the ack is now the orchestrator's real verdict (parity win surfaces here for free). |
| 10 | `eos-tools/ports.rs`, `eos-tools/metadata.rs` | ▷ | No change — `PlanSubmissionPort` trait (`ports.rs:169`) + `plan_submission` field (`metadata.rs:76`) already exist | Only the impl *behind* them changes. |
| 11 | `eos-workflow/testsupport.rs` + unit tests | ✎ | `QueueRunner`/`ScriptedRunner.run()` (`testsupport.rs:657-680`) **record via the orchestrator** instead of returning `AgentTerminal`; tests push submissions, not reports | The main cost. Upside: doubles drive the *real* tool→record→loop path. |
| 12 | `eos-workflow/lib.rs`, `attempt/mod.rs` | ✎ | Drop the `AgentTerminal` re-export (`lib.rs:25`, `mod.rs:8`) | Follows the type deletion. |

**Detailed notes:**

- **The spine (rows 1–4, 6–8) is one coherent move.** Today the submission makes a
  U-turn — built in the tool, stashed, wrapped into `AgentTerminal` by the runner,
  shipped back, unwrapped by the loop, written. Recording deletes the U-turn: the tool
  hands its typed submission straight to `record_*`/`record_plan`. The runner shrinks to
  "run engine, report error string"; the loop's post-join logic shrinks to one branch;
  `plan_submission` flips `None → Some(recording adapter)`.
- **Why `record_plan` is split out (row 3).** `apply_plan_submission` ends with
  `advance_run_stage()`. If the planner *tool* triggered that, `submit_planner_outcome`
  would block until every generator and the reducer finished. So `record_plan` does only
  materialize + set RUN + planner Done; `settle_planner` (running in the planner's
  spawned continuation — exactly where the advance happens today) kicks
  `advance_run_stage()` once. Same execution context as now; only the plan's *arrival*
  moves from "captured return value" to "tool call during the run."
- **D4 closed by construction.** Tool calls `record_*` (mark only); one
  `advance_run_stage` owns launches + close. Exactly one writer; advancing variants and
  their nested-advancer hazard are deleted.
- **Concurrency confirmed.** Different runs record different task rows; the orchestrator
  holds no in-memory mutable attempt state (re-reads `fresh_attempt`). Ordering holds:
  the tool's `record_*` completes before the run future resolves, so when the loop joins
  and checks "still Running?", a recorded task already reads Done. The registry `get()`
  is a short `parking_lot::Mutex` lock with no await held, and `record_*` takes no
  registry lock — no deadlock against the parked loop.

---

## 5. What stays exactly as-is (do not change)

- **The full close cascade — unchanged; it sits *above* the drive seam.** Path A only
  changes how RUN-stage tasks get marked Done/Failed. From `close_attempt` upward the
  chain is byte-for-byte the same:
  ```
  advance_run_stage → close_attempt(PASSED|FAILED)          orchestrator.rs:599
     → coordinator.handle_attempt_closed                    iteration/mod.rs:147
        Passed → close_iteration_passed (Iteration=Succeeded)
        Failed → retry_or_close_failed (retry within budget, else Iteration=Failed)
     → on_iteration_closed → handle_iteration_closed        lifecycle.rs:157
        succeeded & deferred_goal → new iteration (continuation; workflow stays OPEN)
        succeeded & no goal       → close_workflow(true)  (Workflow=Succeeded)
        failed                    → close_workflow(false) (Workflow=Failed)
     → close_workflow: set_status + outcomes, ZERO TaskStore writes   lifecycle.rs:188
  ```
  What D5/Path A fixes is the **success** path — making `all_done → close_attempt(PASSED)`
  reachable, which is what lets the cascade end in `Workflow = Succeeded` instead of
  always `Failed`. Closure semantics, retry budget, continuation, and "parent never
  mutated at close" (GC-eos-workflow-01) are untouched.
- **The harness logic.** PLAN→RUN→CLOSED, the reducer exit gate, `dag_status`
  quiescence, `ready_pending_plan_ids` (`== Done`), the `record_*` variants,
  `mark_execution_task`'s belongs/role/running guards (`orchestrator.rs:545-597`),
  `close_attempt`, `synthesize_*`, the per-attempt registry — all faithful; leave them.
- **The single-writer JoinSet loop** skeleton (`advance_run_stage`, `run_stage.rs:41-112`)
  and the planner's one-shot `spawn_planner_run`. Recording plugs in without restructuring
  them.
- **The per-attempt concurrency cap** `max_concurrent_task_runs` (D3) — keep.

---

## 6. Final file/folder structure

**Zero files added, zero files removed.** Every touched file gets smaller except the
two-line wiring in `entry.rs`/`tool_context.rs`.

```
agent-core/crates/
├── eos-workflow/src/
│   ├── attempt/
│   │   ├── launch.rs            ✎  −AgentTerminal enum; AgentRunReport → {failure_summary}
│   │   ├── orchestrator.rs      ✎  −apply_generator/reducer_submission; +record_plan;
│   │   │                            apply_planner_report → settle_planner; +D6 role gate
│   │   ├── run_stage.rs         ✎  −apply_terminal; apply_report → settle_or_synthesize;
│   │   │                            (+D8 audit emits, optional)
│   │   ├── plan_dag.rs          ▷  readiness / dag_status / acyclic — unchanged
│   │   ├── orchestrator_registry.rs  ▷  unchanged
│   │   └── mod.rs               ✎  drop AgentTerminal re-export
│   ├── ports.rs                 ✎  PlanSubmissionAdapter → record_* (recording, not advancing)
│   ├── iteration/mod.rs         ▷  close cascade — unchanged
│   ├── lifecycle.rs             ▷  workflow close / continuation — unchanged (see §10 note)
│   ├── testsupport.rs           ✎  QueueRunner/ScriptedRunner record via orchestrator
│   ├── lib.rs                   ✎  drop AgentTerminal re-export
│   └── starter.rs ids.rs error.rs util.rs context/   ▷  unchanged
│
├── eos-runtime/src/
│   ├── agent_runner.rs          ✎  thin wrapper: wire recording port, run, return failure_summary
│   ├── tool_context.rs          ✎  +plan_submission in MetadataParams (root stays None)
│   ├── entry.rs                 ✎  construct PlanSubmissionAdapter, pass into RuntimeAgentRunner::new
│   ├── root_agent.rs            ▷  build_metadata default None — unchanged
│   ├── tests.rs                 ✎  reconcile to recording seam (inject ApprovingAdvisor)
│   └── agent_loop.rs app_state.rs main.rs observability.rs lib.rs   ▷  unchanged
│
└── eos-tools/src/
    ├── model_tools/submission.rs   ▷  NO CHANGE (already record-shaped: Accepted→ok / Rejected→error)
    ├── ports.rs metadata.rs        ▷  PlanSubmissionPort + plan_submission field — unchanged
    └── (everything else)           ▷  unchanged
```

Optional cosmetic: rename `PlanSubmissionAdapter` → `RecordingPlanSubmission`. Keep the
trait method names (`apply_plan`/`submit_generator`/`apply_reducer`) — `submission.rs`
already calls them; only the impl semantics change.

---

## 7. Verification (success criteria)

Scope to what this lane can prove **in isolation** (see §8 for the cross-lane gate):

- **Harness completes with the real recording runner (isolated).** With an injected
  `ApprovingAdvisor` + a seeded agent registry (planner/generator/reducer profiles) +
  the real `RuntimeAgentRunner` (recording port wired), a delegated workflow drives
  planner → generators → reducer to `AttemptStatus::Passed` → (cascade) →
  `WorkflowStatus::Succeeded` — **without** the `QueueRunner`/`ScriptedRunner` doubles.
  This is the proof D5 is closed and the success path of §5's cascade is reachable.
- **Validation feedback (the recording win).** A stale/out-of-stage submission returns
  `ToolResult::error` to the agent (the orchestrator's `Rejected` ack), not a silent
  success.
- **Captured-failure vs no-submission.** A generator that submits `status=failed` closes
  the attempt FAILED via `record_generator_submission`; a generator that never submits
  closes FAILED via synthesized `run_exhausted` (distinct `fail_reason`) — port the
  existing `dead_agent_synthesizes_failure` test (`run_stage.rs:362-400`).
- **Single writer.** No nested advancer; the only `advance_run_stage` is the one
  `settle_planner` kicks. `fanout_respects_concurrency_cap` still passes.
- **D6:** a generator task bound to a non-generator profile is rejected; a generator
  profile passes. **D8:** a run emits `task_ready`/`task_launched` per launch and
  `task_failed` on launch failure (recording audit sink).
- **D4 dead-code:** `grep` proves no non-test caller of the advancing variants before
  deletion; crate builds after removal.

**Cross-lane-gated (NOT provable here):** a true *non-injected* end-to-end run
(`root → delegate_workflow → planner → … → reducer → terminal`) in a default build is
**also** blocked by the advisor deny-all stub (the `AdvisorApproval` pre-hook denies
`submit_planner/generator/reducer_outcome` before the executor reaches the recording
port) and the empty-registry binary (request_completion NF1). D5 is **necessary but not
sufficient**; the `backend/src`-deletion gate needs this lane **and** the advisor lane
**and** the registry-wiring lane.

---

## 8. Coordination / sequencing

- **Compounds with the advisor lane.** Those three terminals are advisor-gated; until the
  advisor lane ships a working approval, isolation tests here must inject
  `ApprovingAdvisor`. Independent to build; both must land before a non-injected E2E run.
- **Concurrent edits to `agent_runner.rs`.** The command-session-supervision commit
  (`c855a1521`) recently added `command_session_supervisor` to `RuntimeAgentRunner`;
  rebase rows 6–8 onto that — add the recording-port wiring alongside, don't stomp it.
- **Dependency order:** row 2 (recording adapter) → row 3 (`record_plan` split + delete
  advancing) → rows 1,4,6,7 (delete `AgentTerminal`, thin runner + loop) → row 8 (wire in
  `entry.rs`) → rows 11–12 (tests + exports). Rows 5 (D8) and the D6 gate are independent.
- This is the Phase-7 `attempt_harness` lane in `REPORT.html` §"Rollout"; it parallels
  the `advisor`, `subagent ⊕ query_engine`, and `request_completion` lanes.

---

## 9. Alternatives considered

**B-capture (viable; rejected on simplicity + fidelity).** Add a capturing
`PlanSubmissionPort` in `eos-runtime` that stashes the typed submission in a per-run slot
and returns `Accepted`; the runner reads the slot and returns
`AgentRunReport::terminal(...)`; the existing loop applies it via `record_*`. Pros:
smaller diff, reuses the tested loop + doubles, matches the `agent_runner.rs` doc's
committed direction. Cons: keeps `AgentTerminal` + `AgentRunReport.terminal` +
`apply_terminal` + a per-run `Mutex` slot (net-additive), and its port **always returns
`Accepted`**, dropping Python's agent-facing validation feedback (a new parity gap).
Choose this only if "fastest to green, least churn" outweighs end-state simplicity.

**A-advancing (rejected outright).** Wire the *existing* `PlanSubmissionAdapter` (which
calls the advancing `apply_*_submission`). Re-creates the D4 double-spawn hazard: each
advancing variant spins a fresh `AttemptStageAdvancer` (`orchestrator.rs:475,486`), so a
submit inside `runner.run()` launches a nested advancer racing the owning loop, which
then errors on "task is not running." Making it safe requires dismantling the
single-writer loop — strictly more work for identical behavior.

---

## 10. Lower-severity items (record + dispatch; not the spine)

- **D1 (LOW, was MEDIUM — corrected by verifier):** the gen↔reducer id-collision headline
  is a FALSE ALARM (lane-shape + dangling checks reject it). Real residual: a
  reducer↔reducer duplicate id slips through (`reducer_ids`/`by_needs` are `BTreeSet`/`BTreeMap`
  while `materialize_plan_tasks` pushes from the `Vec`, `orchestrator.rs:262`). Fix:
  union-dedup `plan.tasks ∪ plan.reducers` in `validate_plan_shape` (mirror
  `plan_dag.py:47-55`). One-liner.
- **D2 (LOW):** topo (Kahn) ordering of `generator_task_ids`/`reducer_task_ids` dropped
  (Rust persists raw plan order). Model-visible only via `project_attempt_outcomes`
  ordering; dispatch unaffected. Fix: reorder by the rank `assert_acyclic` already
  computes, or accept + update doc §2.
- **D3 (LOW, intentional):** `max_concurrent_task_runs` cap has no Python analogue. Keep;
  document as an intentional Rust addition.
- **D7 (LOW, moot):** no `_fail_unowned_attempt` path. Under recording the owning
  orchestrator is always in scope; a submission to a deregistered attempt returns
  `Rejected` to the agent (registry `get` → None). Nothing to port.

**Adjacent (separate area, noted not owned):** the workflow-close *continuation* branch
(`lifecycle.rs:164-184`, `create_and_start_first_attempt` with no rollback on failure) is
the `workflow_lifecycle` area's HIGH finding ("deferred-iteration start-failure has no
compensation"). It lives in the close cascade this plan deliberately leaves unchanged —
fix it under that lane, not this one.
