# Implementation plan: ScenarioEventSource + remove MockSquadRunner

Status: IMPLEMENTATION PLAN. Builds on `ultra_complex_bundled_scenario_TEST_PLAN.md` (decisions: shared
`query.py` loop + injected event-source; full `AssistantMessageCompleteEvent` per turn, no deltas;
**remove the mock-only role lifecycle events and assert on real store state**).

Goal: delete the imperative, loop-bypassing `MockSquadRunner` (2043 LoC) and drive every mock agent
through the **real** `query.py` loop via an injected per-agent event source, re-homing the few
non-execution responsibilities the harness needs and **deleting** the redundant lifecycle events.

---

## 0. Two findings that shape the removal

**(a) The role lifecycle events are mock-only AND redundant — DELETE them (don't re-home).**
`*_INVOKED`/`EXECUTOR_SUCCESS`/`EXECUTOR_FAILURE`/`PLANNER_COMPLETES_GOAL_PLAN`/`PLANNER_DEFERS_GOAL_PLAN`/
`VERIFIER_*`/`EVALUATOR_*`/`RECURSIVE_GOAL_REQUESTED`/`RECURSIVE_GOAL_COMPLETED` are emitted only by the mock
(`files=0` in `task_center/`+`engine/`) and consumed only by the mock harness (scenario
`expected_event_sequence` + focused-test `min_event_counts`/`absent_events`). The V3 perf report does NOT
use them (keys off `sandbox.*`/`plugin.*`). Everything they signal already lives in `report.graph_summary`
(real store state via `_graph_summary`, core/runner.py:90-142): goal `status`/`origin_kind`/
`requested_by_task_id`/`final_outcome`, iteration `status`/`creation_reason`/`deferred_goal_for_next_iteration`,
attempt `sequence_no`/`stage`/`status`/`fail_reason`/`deferred_goal_for_next_iteration`/`task_ids`/`tasks`.
Tests ALREADY assert on it in parallel (`_recursive_goal_count` via `origin_kind=="task"`;
`test_deferred_parent_planner_terminal_routing` asserts `len(goals)==2`). → **Delete the events; migrate
the focused-scenario assertions to `graph_summary`** — strictly more faithful (tests the real TaskCenter,
not a mock echo). Event→store mapping in §4.1.

**(b) A thin runner still survives** — but thinner. It keeps the `AttemptAgentRunner` seam (launch.py:40,
called at launch.py:134), prompt inspection, the `MOCK_*` records (`tool_calls`/`sandbox_checks`/
`prompt_inspections`), and returning `EphemeralRunResult`. It does **not** publish any lifecycle events.
"Remove MockSquadRunner" = delete the imperative engine (`_run_*`/`_call_tool`/`_approve_terminal`/probe
methods, ~1900 LoC) + the lifecycle-event publishing, leaving a ~120-LoC `ScenarioLoopRunner` that
delegates execution entirely to `run_query`.

---

## 1. Components

### A. The seam (production code — tiny, default-off)
- `engine/query/context.py`: add `event_source: EventSource | None = None`
  (`EventSource = Callable[[QueryContext, QueryRunRequest], AsyncIterator[StreamEvent]]`).
- `engine/query/loop.py` `_consume_provider_stream` (~170): swap the one call —
  ```
  source = context.event_source or _provider_event_source
  async for event in source(context, run_request):  # rest of the body unchanged
  ```
  `def _provider_event_source(context, run_request): return context.api_client.stream_message(run_request.request)`.
- `runtime/app_factory.py`: add `RuntimeConfig.event_source_factory: Callable[[AgentDefinition], EventSource] | None = None`.
- `engine/agent/factory.py` `spawn_agent` (~387): after building `QueryContext`, set
  `context.event_source = config.event_source_factory(agent_def) if config.event_source_factory else None`.
- Default `None` everywhere ⇒ the event-source swap is byte-identical to production when off.
- **Parity note (§7):** the seam swap is the ONLY engine change. Budget parity is handled mock-side — the
  `ScenarioEventSource` emits one `ToolUseDeltaEvent` per tool_use so stream-time counting matches the real
  provider. No production-loop change, no test change.

### B. `ScenarioEventSource` (NEW — the LLM mock)
Built per agent by the factory closure over `(scenario, agent_def role, task_id, mutable_state)`; holds the
per-agent agent coroutine (§C). Each call (one per loop turn):
1. Parse the latest `ToolResultBlock`s from `run_request.request.messages` (trailing user message).
2. `coro.asend(results)` → next `Turn`.
3. Yield one `ToolUseDeltaEvent` per tool_use (ids matching) THEN one
   `AssistantMessageCompleteEvent(Message(content=[ThinkingBlock?, TextBlock?, ToolUseBlock...]))`. The
   tool_use deltas are REQUIRED for budget parity (§7); thinking/text deltas are optional. The loop's
   dispatch executes all `final_message.tool_uses`, enforces terminal-alone, and emits `ToolExecution*`.

### C. Scenario turn-coroutine model (replaces per-role decision methods)
`ScenarioBase` gains per-role coroutines that `yield Turn(...)` and receive tool results:
```
async def executor_turns(ctx):                      # was executor_actions + _run_executor
    res = yield Turn(thinking="…", calls=[ToolCall("read_file", {...})])
    yield Turn(calls=[ToolCall("submit_execution_success", {...})])   # terminal, alone
```
`Turn = {thinking?, text?, calls: list[ToolCall]}`; `ToolCall = {name, input}`. **Probe adapter:** wrap an
imperative probe as a coroutine where `call_tool(name, args)` becomes `result = yield ToolCall(name, args)`
(adapter-all vs rewrite decided after the Phase-0 spike). `MutableMockState` (`consume_failure`,
`replace_next_planner_response`) is read inside the coroutine to yield a failure-terminal / overridden turn.

### D. `ScenarioLoopRunner` (NEW thin runner — replaces MockSquadRunner)
Keeps the exact `AttemptAgentRunner` signature so launch.py + builder wiring are unchanged. Body (~120 LoC):
1. Publish `MOCK_LAUNCH_RECORDED`; `_inspect_prompt` → `MOCK_PROMPT_INSPECTED`; `_record_initial_messages` (PRESERVED).
2. Set `config.event_source_factory = lambda ad: ScenarioEventSource(scenario, ad, task_id, mutable_state)`; call the real `run_ephemeral_agent`/`run_query`.
3. Drain the loop's event stream → forward to `on_event`; translate `ToolExecutionCompletedEvent` →
   `MOCK_TOOL_CALL_RECORDED` (report.tool_calls); probe coroutines emit sandbox checks via a recorder
   callback (`MOCK_SANDBOX_CHECK_RECORDED`/`SANDBOX_CONFLICT_DETECTED`/`SANDBOX_BATCH_EDIT_APPLIED`).
4. Return the real loop's `EphemeralRunResult`.
**No lifecycle-event publishing** — those are deleted (§0a); workflow shape is asserted via `graph_summary`.

---

## 2. Re-homing table (MockSquadRunner responsibility → new home)

| Today in `MockSquadRunner` | New home |
|---|---|
| `_run_planner/_run_executor/_run_verifier/_run_evaluator` | **DELETE** → scenario turn-coroutines via `ScenarioEventSource` |
| `_call_tool` (manual exec + hand-emitted `ToolExecution*`) | **DELETE** → real loop dispatch (`execute_tool_call_streaming`) |
| `_approve_terminal` + `agent/mock/_advisor_approval.py` | **DELETE** → real `ask_advisor` scripted turn through the loop |
| `_run_*_probe` + `PreparedToolScriptEngine` + `full_stack_tool_scripts` | **MIGRATE** → turn-coroutines (adapter) |
| role `*_INVOKED` events | **DELETE** → assert on `graph_summary` (count role tasks in `attempt["tasks"]`/`task_ids`) |
| role outcome events (`EXECUTOR_SUCCESS`, `PLANNER_COMPLETES_GOAL_PLAN`, `RECURSIVE_GOAL_REQUESTED`, …) | **DELETE** → assert on `graph_summary` (attempt/task `status`+`fail_reason`, `deferred_goal_for_next_iteration`, child goals `origin_kind=="task"`) |
| `_inspect_prompt` → `MOCK_PROMPT_INSPECTED` (`report.prompt_inspections`) | **PRESERVE** → `ScenarioLoopRunner` step 1 |
| `LaunchRecord`/`MOCK_LAUNCH_RECORDED`, `_record_initial_messages` | **PRESERVE** → step 1 |
| `MOCK_TOOL_CALL_RECORDED` (`report.tool_calls`) | **RE-HOME** → step 3, bridged from the loop's `ToolExecutionCompletedEvent`s |
| sandbox checks (`MOCK_SANDBOX_CHECK_RECORDED`/`SANDBOX_CONFLICT_DETECTED`/`SANDBOX_BATCH_EDIT_APPLIED`) | **RE-HOME** → probe coroutines emit via a recorder callback |
| `EphemeralRunResult` construction | **DELETE** → comes from the real loop |
| `MutableMockState` injection | **PRESERVE** → read inside coroutines |
| builder `_make_runner` / `runner_factory` (builder.py:53-70) | **PRESERVE** → factory returns `ScenarioLoopRunner` (same seam) |

---

## 3. Deletion list
- `agent/mock/runner.py`: remove `_run_planner/_run_executor/_run_verifier/_run_evaluator`, `_call_tool`,
  `_approve_terminal`, all `_run_*_probe` dispatchers, `_record_tool_check`, `_script_engine`, the
  `_*_EVENT_BY_TOOL` maps, and all lifecycle `_publish(EventType.*_INVOKED/…)` calls. Keep (move into
  `ScenarioLoopRunner`): `_metadata_for`, `_inspect_prompt`, `_record_initial_messages`, `_invocation_payload`,
  the `MOCK_*` publishers, `_current_attempt_and_iteration`, `_probe_path` helpers.
- Delete `agent/mock/_advisor_approval.py` + its unit re-export.
- **Lifecycle EventType cleanup:** remove the 14 lifecycle entries from `audit/events.py:61-75`; remove
  `Scenario.expected_event_sequence` (base.py:51,74) + every per-scenario declaration (~20 scenarios);
  remove `RunReport.seen_event_types` + `_assert_ordered_subsequence`/`_assert_event_counts` machinery in
  `_focused_scenario_contracts.py`. **Touch-point:** `hooks/builtins.py` also emits
  `VERIFIER_INVOKED`/`VERIFIER_SUCCESS` (lines 28-31,135,162) — drop those hook emissions (the verifier
  outcome is in attempt/task state).
- Repoint `tests/mock/contracts/test_advisor_gate_negative_path.py`: negatives are now scriptable as real
  `ask_advisor` turns (a blocked terminal is no longer a `_call_tool` raise).

---

## 4. Test-contract preservation (must stay green)
- **`report.task_center_status`, `graph_summary`**: unchanged — driven by real TaskCenter store, not the runner.
- **`report.prompt_inspections` / `passed_prompt_inspections`**: `_inspect_prompt` preserved (step 1).
- **`report.tool_calls`, `report.sandbox_checks`**: re-homed (step 3 + recorder callback).
- **`report.performance_report_task` / V3 sections**: unchanged — built from daemon `sandbox_events.jsonl`
  produced by the REAL tool execution (same sandbox RPCs, now via loop dispatch instead of `_call_tool`).
- **Event-count / sequence assertions**: **MIGRATED to `graph_summary`** (§0a, §4.1), not preserved. The
  `_assert_ordered_subsequence` order-check is dropped — the real TaskCenter enforces role ordering.
- **Existing engine tests** (`test_hard_ceiling_behavior`, `test_terminal_call_reminder`, …): unaffected — seam default = provider.

### 4.1 Event → `graph_summary` assertion mapping (the migration)
| Removed event | Assert instead (from `report.graph_summary`) |
|---|---|
| `PLANNER_INVOKED`/`EXECUTOR_INVOKED`/`VERIFIER_INVOKED`/`EVALUATOR_INVOKED` (counts) | count tasks of that role in `attempt["tasks"]`/`task_ids` across iterations |
| `EXECUTOR_SUCCESS` / `EXECUTOR_FAILURE` / `VERIFIER_*` | per-task `status` (done/failed) in `attempt["tasks"]` |
| `EVALUATOR_SUCCESS` / `EVALUATOR_FAILURE` | attempt `status` + `fail_reason` (e.g. `evaluation_failed`) |
| `PLANNER_COMPLETES_GOAL_PLAN` vs `PLANNER_DEFERS_GOAL_PLAN` | `iteration/attempt["deferred_goal_for_next_iteration"]` (None ⇒ closed, set ⇒ deferred) |
| `RECURSIVE_GOAL_REQUESTED` / `RECURSIVE_GOAL_COMPLETED` | child goal `origin_kind=="task"` + `requested_by_task_id` + its `status`/`final_outcome` (`_recursive_goal_count` pattern already exists) |
Net: ~20 focused-scenario cases migrate from `min_event_counts`/`expected_event_sequence` to graph-shape
helpers; several already assert on `graph_summary`, so add shared helpers (`count_role_tasks`,
`attempt_outcome`, `recursive_goals`) to `_focused_scenario_contracts.py`.

---

## 5. Phases
0. **Seam + portability spike (gate):** `event_source` field + loop swap + `event_source_factory` +
   `spawn_agent` wiring (default-off). Minimal `ScenarioEventSource` + `Turn`/`ToolCall` + adapter. Port ONE
   probe to a coroutine; run through the **real** `run_ephemeral_agent` on a docker sandbox; assert effect +
   budget/terminal contracts + terminal-alone. Confirm `run_ephemeral_agent`'s event/`on_event` contract +
   that returned `messages` expose the terminal tool_use. The mock emits tool_use deltas (per §7) so budget
   counts identically — NO engine change beyond the seam, no test change. Assert `tool_calls_used` parity
   for a background turn and a rejected-batch turn as part of the spike.
1. **`ScenarioLoopRunner`:** thin runner (§1.D); wire into `builder.py:_make_runner` behind a flag; run
   CorrectnessTesting green through it (assert via `graph_summary`, not events).
2. **Migrate scenarios/probes** to turn-coroutines + **migrate focused-scenario assertions to `graph_summary`**
   (§4.1); add the shared graph-shape helpers; flip the registry; keep `test_scenario_suite_imports.py` green.
3. **Delete** the old imperative runner internals, `_advisor_approval.py`, the 14 lifecycle EventTypes,
   `expected_event_sequence`, and the event-count assertion machinery (§3).
4. **Ultra bundle** (`ultra.full_system_bundle`) + five-area coverage, all as turn-scripts; assert via store state.

---

## 6. Risks
- **Assertion migration (§4.1)** — the main test churn. `graph_summary` already captures the equivalents and
  several tests use it; add shared helpers and migrate ~20 cases. Lower risk than it looks.
- **`hooks/builtins.py` verifier-event emission** — a non-obvious second emit site; drop with the rest.
- **Probe portability** — imperative→coroutine; prove one in Phase 0 before committing.
- **Outcome derivation no longer needed** in the runner (events deleted) — simpler than re-homing.
- **Event forwarding** — the thin runner bridges the loop's stream to `on_event` + `MOCK_TOOL_CALL_RECORDED`
  without double-emitting `ToolExecution*`. Confirm `run_ephemeral_agent`'s contract in Phase 0.
- **`spawn_agent` needs a model registration** even with `event_source` set (api_client built but unused);
  the mock already requires `database_configured()` — keep a model row.

---

## 7. Parity audit results (20-agent adversarial workflow, 2026-05-29)

**Verdict: 8/10 dimensions provably SHARED/SEAM/ADDITIVE** (identical except the event source). **One
root-cause divergence** refuted the literal "everything else is identical" claim, with two symptoms.

Confirmed identical (refuted=false): seam isolation (SEAM — sole provider call at loop.py:170; field
defaults None ⇒ byte-identical), QueryContext construction, notification rules, prehook+advisor (scripted
`ask_advisor` reaches the real gate the same way), terminal detection, subagent/explorer path (the spawned
explorer also gets `event_source` via the per-agent factory — no `needs_fresh_client` change needed),
request/usage no-ops (mock ignores `MessageRequest` + emits zero usage; nothing downstream depends on
either — the 150% ceiling keys on tool-call counts, not tokens), TaskCenter control flow (removal of the
lifecycle events is observability-only — nothing branches on them), runner instrumentation (ADDITIVE — the
loop emits `ToolExecution*`; the runner only adds `MOCK_*` records, no double-emit, no execution effect).

**Root cause — budget counting is delta-time.** `_count_tool_dispatch(context)` runs at stream-time on
every `ToolUseDeltaEvent` (loop.py:173). The delta-free mock can't replicate it, and the dispatch-time
count doesn't compensate symmetrically:
- **Symptom A — batch-rejected tools** (terminal-alone violation, or >1 lifecycle tool in a batch): real
  counts them at loop.py:173; mock doesn't — dispatch early-returns before `execute_tool_call_streaming`
  (dispatch.py:351/393). → `tool_calls_used` diverges → the 150% ceiling trips on a different turn.
  Encoded by existing test `test_tool_execution.py:946-1030`.
- **Symptom B — background tools** (`run_subagent` background='always', shell `background=True`): real
  counts TWICE (stream delta + ungated body — `background/dispatch.py:191` calls
  `execute_tool_call_streaming` without `consume_budget`), mock once. → real=2, mock=1. Also a latent
  real-provider double-count bug.

**Why it matters here:** both are latent in today's mock suite, BUT the ultra bundle exercises
`run_subagent`/explorer (background) and the #2 ceiling path — so it WOULD trigger them.

**Fix — the mock emits tool_use deltas (RECOMMENDED; ZERO engine change):** have `ScenarioEventSource`
emit one `ToolUseDeltaEvent` per tool_use (id matching the `AssistantMessageCompleteEvent`'s
`ToolUseBlock`s) *before* the complete event — the mock event stream then mirrors the real provider's
shape. Now the stream-time count at loop.py:173 fires identically on both paths, `streamed_tool_use_ids`
is populated identically (so dispatch's `consume_budget` gating matches), and foreground/background/
rejected-batch counts all agree. Verified across all three cases: foreground real=mock=1; background
real=mock=2; rejected-batch real=mock=2 (trips the ceiling identically). Thinking/text deltas stay
OPTIONAL (audit: reporting-only — the complete message carries the blocks). **This keeps the engine to the
single event-source seam**; the mock's event *handling* is byte-identical to real and only the event
*content* (scripted vs LLM) differs — exactly the intended seam. Refines the earlier "complete-only, no
deltas" convenience call: tool_use deltas are cheap (one per tool, no chunking) and required for budget parity.

**Alternative (independent real-bug cleanup, NOT needed for parity):** relocate budget counting to a single
delta-independent dispatch-time site (count each `final_message.tool_uses` once; drop loop.py:173). This
also fixes the latent real-provider background double-count (Symptom B real=2 should be 1), but it changes
real-LLM counting semantics and updates `test_tool_execution.py:946-1030` — so treat it as a separate
optional engine cleanup, decoupled from the mock work.

**Honest parity statement:** with the mock emitting tool_use deltas, the engine change stays at the single
event-source seam and the mock path ≡ the real-LLM path except the event *content*. The "only the event
source differs" goal holds.
