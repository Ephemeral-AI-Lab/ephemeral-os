# Plan: rewrite MockSquadRunner as a real-loop mock runner + ultra bundled scenario

Status: DESIGN LOCKED (2026-05-29). Loop realization = **shared `query.py` loop + injected event-source**.
Provider: **docker only** (`EOS_SANDBOX_PROVIDER=docker`, persistent `sweevo-<instance_id>`).

The user's directive: **re-write `MockSquadRunner` as a runner that reuses the real engine loop
(`backend/src/engine/query/loop.py`) and differs only at the LLM seam** — real = provider
`stream_message`; mock = scripted thinking/text/tool-call turns. This SUPERSEDES the earlier
two-engine split (MockSquadRunner-bypass + a separate FakeScriptedProvider) and the `factory.py:195`
hack: one loop now drives every role, so notifications, the 150% hard-ceiling, prehooks, text-only
retry, and real `ask_advisor` flow are all exercised by construction.

---

## 1. Architecture — one loop, one seam

`_run_query_loop` (loop.py:199-298) is provider-agnostic except a single call inside
`_consume_provider_stream` (loop.py:162-197): `context.api_client.stream_message(run_request.request)`
(line 170). Everything around it — notification dispatch (212-223), `StreamingToolExecutor`,
`dispatch_assistant_tools`→`execute_tool_once`→`run_pre_hooks`, `state.final_message`,
`_count_tool_dispatch`, `streamed_tool_use_ids`, terminal detection (275-294), the 150% hard-fail
(`terminal_submission_failed`, 41-47) — is shared.

**The seam:** make the event source injectable on `QueryContext`, defaulting to the provider. The mock
threads a per-agent `ScenarioEventSource` onto the context it already builds. Because the source yields
the SAME event types the loop already handles, all loop contracts hold for free.

```
# engine/query/context.py — new optional field
event_source: EventSource | None = None
#   EventSource = Callable[[QueryContext, QueryRunRequest], AsyncIterator[StreamEvent]]

# engine/query/loop.py  _consume_provider_stream (~line 170) — the only behavioral diff
- async for event in context.api_client.stream_message(run_request.request):
+ source = context.event_source or _provider_event_source
+ async for event in source(context, run_request):
      ... (all existing handling unchanged) ...
# _provider_event_source(context, run_request) -> context.api_client.stream_message(run_request.request)
```

Default `None` ⇒ production behavior byte-identical. The source reads history from
`run_request.request.messages` (carries prior `ToolResultBlock`s), so it needs nothing beyond `context`
+ `run_request` — verified against loop.py:162-197 (only `run_request.request` is consumed there).

**Contract the mock source MUST honor — tool_use deltas + the complete event:** per turn yield one
`ToolUseDeltaEvent` per tool call (ids matching) THEN one `AssistantMessageCompleteEvent(Message(
content=[ThinkingBlock?, TextBlock?, ToolUseBlock...]))`. The **tool_use deltas are REQUIRED for budget
parity**: a 20-agent parity audit found that emitting *only* the complete event diverges from the real
path on tool-call budget counting for background tools (`run_subagent`/explorer) and batch-rejected tools
(the real path counts them at stream-time, loop.py:173; a delta-free mock misses them), shifting the 150%
ceiling. Thinking/text deltas remain **optional** (reporting-only — the complete message carries the
blocks). With tool_use deltas present, the loop's handling is byte-identical to the real provider; only
the event *content* (scripted vs LLM) differs. Detail + verification: `mock_event_source_IMPL_PLAN.md` §7.

---

## 2. ScenarioEventSource + the turn-coroutine scenario model

`ScenarioEventSource` is built **per agent** (the runner is already invoked per-agent), closing over the
scenario + this agent's role/task + a per-agent **agent coroutine**. Each `stream_message`-equivalent
call: parse the latest `ToolResultBlock`s from `run_request.request.messages`, `send()` them into the
coroutine, receive the next turn, and yield it as events.

The scenario authoring model shifts from "one decision + an imperative probe" to **per-role turn
coroutines**:
```
async def executor_script(ctx):
    res = yield Turn(thinking="check baseline", calls=[ToolCall("read_file", {...})])
    # res carries the read_file ToolResult — react to it
    yield Turn(text="writing fix", calls=[ToolCall("edit_file", {...})])
    yield Turn(calls=[ToolCall("submit_execution_success", {...})])   # terminal, alone
```
A `Turn` = optional thinking + optional text + a list of tool calls. The loop executes the calls via the
**real** dispatch (real prehooks, real sandbox) and feeds results back on the next `send()`.

**Probe portability is the load-bearing risk** (not the loop shape): today's probes are imperative
`await self._call_tool(...)` sequences; the real loop returns each result a turn *later*. An **adapter**
can wrap an imperative probe as a coroutine — `call_tool(name, args)` becomes `result = yield
ToolCall(name, args)` — so probe bodies port with minimal churn. Whether to adapter-wrap all ~50
scenarios or rewrite to declarative turn lists is **deferred until the Phase-0 spike proves one probe
end-to-end**.

---

## 3. What the unified loop unlocks (per area) — all via ONE mechanism

| # | Coverage under the rewrite |
|---|---|
| 1 | All tools via scripted turns. **Explorer**: script an executor turn calling `run_subagent`; the explorer subagent runs the SAME mock loop with its own scripted turns → `submit_exploration_result`. No `factory.py:195` change. |
| 2 | Natural: a turn-script that never submits crosses 75/100/125% of `tool_call_limit` (assert the reminder's live budget text) → **150%** `TERMINAL_NOT_SUBMITTED` (loop.py:41-47) → TaskCenter `run_exhausted` propagation (launch.py:254-296). text-only turn → reminder → corrective turn. **No tiered rules exist; the single reminder is asserted at those marks** (per your direction; reminder = `notification/rules/factories.py:17`, ceiling in engine = `loop.py:46`). |
| 3 | Unchanged: real TaskCenter drives planner→DAG→evaluator; defer→new-iteration, handoff→nested depth-2 guardrail, retry, 5 failure modes. The mock loop is just the per-agent runner. |
| 4 | Real prehooks run in dispatch already. Now `ask_advisor` is a **real scripted turn** through the loop → real verdict in `conversation_messages` → real `AdvisorApprovalPreHook`. A blocked terminal returns `is_error`, the loop nudges (reminder) and the script can submit a corrective turn — so the 4 cases (approve / reject / wrong-tool / missing) AND the two other prehooks are scriptable **in-scenario** (the old "keep negatives as focused tests" constraint was a `MockSquadRunner._call_tool`-raises artifact that this rewrite removes). |
| 5 | Unchanged: tool calls hit the real sandbox; ephemeral/OCC/IWS-port-3000 probes run as scripted turns. |

---

## 4. Per-area findings (verified reference)

| # | Current state (anchors) |
|---|---|
| 1 | All file/shell/grep/glob/bg/IWS tools + 11 terminals have probes/actions; `run_subagent`→explorer spawns a real subagent (`run_subagent.py:228`). |
| 2 | One reminder rule `make_terminal_call_reminder` (`factories.py:17`, no tiers); 150% ceiling (`loop.py:41-47`); `run_exhausted` (`launch.py:254-296`). Engine unit tests use `_fake_provider.py`. |
| 3 | Lifecycle in `task_center/*`; nested depth>1 strips `submit_execution_handoff` (`terminal_tool_routing.py:51-59,137-150`); attempt budget=2 (`primitives.py:47`). |
| 4 | Prehooks run via `execute_tool_once`→`run_pre_hooks`; advisor binds approval to `tool_name` (`advisor_approval.py:87`); `build_advisor_approval_messages` is parameterized. |
| 5 | Lease/OCC (`occ/changeset.py:144-163`), ephemeral OCC-publish, per-IWS `unshare --net` netns (`namespace_runtime.py:79-116`). Scenarios 0–3 pass on docker. |

---

## 5. Implementation phases

**Phase 0 — seam + portability spike (PROVE THE DESIGN FIRST).**
- Add `event_source` field to `QueryContext` (context.py); add the `_provider_event_source` default + the ~5-line swap in `_consume_provider_stream` (loop.py). Default None ⇒ no production change.
- Build a minimal `ScenarioEventSource` + `Turn`/`ToolCall` types + the imperative→coroutine adapter.
- Port **one** representative probe (OCC-conflict or background mixed-op) to the turn-coroutine shape.
- Test: run it through the **real** `run_query`/`run_ephemeral_agent` against a real docker sandbox; assert the probe's effect, that `_count_tool_dispatch`/terminal detection fired, and terminal-alone held. **Gate: this must pass before committing the scenario model.**

**Phase 1 — runner rewrite.** Replace `MockSquadRunner.__call__` internals: build the per-agent
`QueryContext(event_source=ScenarioEventSource(scenario, role, task))` (reuse `spawn_agent` assembly) and
return its `EphemeralRunResult`. Role dispatch moves into the scenario's per-role coroutines. Delete the
loop-bypass path, `_approve_terminal`, and the synthetic advisor pair (`_advisor_approval.py`).

**Phase 2 — scenario migration.** Port scenarios/probes to turn coroutines via the adapter; decide
adapter-wrap-all vs selective rewrite based on Phase 0. Keep the registry + `test_scenario_suite_imports`.

**Phase 3 — the ultra bundle.** Author `ultra.full_system_bundle` as turn-scripts: iter1 defer → iter2
DAG (ephemeral/OCC + background mixed-op + IWS-port-3000 + handoff→nested-depth-2 + verifier-retry) →
evaluator close; weave #2 (no-submit→reminder→150%→run_exhausted) and #4 (real ask_advisor
approve/reject/wrong-tool/missing + bg-cleanup + iws-block) as scripted turns.

**Phase 4 — cleanup.** Remove dead bypass code, `_fake_provider` duplication if superseded, and the
obsolete two-engine notes. `ruff`/`mypy` clean.

---

## 6. Risks
- **Probe portability** (Phase-0 gate): imperative `call_tool` sequences vs turn-by-turn result feedback. Prove one before committing.
- **Contract drift in the source**: the mock source MUST emit matching `tool_use_id`s across `ToolUseDeltaEvent` and the `final_message` ToolUseBlocks, and set `state.final_message`; else terminal detection / 150% ceiling silently break. Covered by Phase-0 test.
- **`run_query` reuse for subagents**: confirm the explorer subagent path also routes through a context with `event_source` set (the runner builds subagent contexts too) — validate in the explorer turn-script.
- **Big rewrite blast radius**: ~50 scenarios + probes. Adapter keeps churn low; stage behind the registry.
- **macOS**: scenario IWS path (proven on docker), not the standalone RPC suite.

## 7. Verification (`.venv/bin/pytest`, never global)
```
env EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 EOS_SANDBOX_PROVIDER=docker \
    EOS_ISOLATED_WORKSPACE_ENABLED=true EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
    EPHEMERALOS_DATABASE_URL=sqlite:///./.ephemeralos/ephemeralos.db \
    uv run pytest -vv --tb=short -p no:randomly <phase-0 spike test, then bundle + migrated scenarios>
```
- Phase 0: one probe runs E2E through the real loop; contracts hold.
- Existing engine tests (`test_hard_ceiling_behavior`, `test_terminal_call_reminder`, …) stay green (seam default = provider).
- Bundle: `task_center_status=="done"`, graph-shape, OCC shapes, V3 sections; #2 reminder-marks→150%→run_exhausted; #4 advisor cases.
- Full mock suite green after migration; `test_scenario_suite_imports.py` green.
