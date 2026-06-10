# EOS Agent Core Rust to TypeScript Migration - Phase 04.6 Agent Runtime E2E

Status: Expansion draft (baseline E2E-01..11 completed; E2E-35..38 observed; remaining E2E-12..60 target)
Date: 2026-06-11
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Boundary: test-only, additive - `packages/agent-runtime/e2e/`; no `src/`
changes in any package, Rust `agent-core/` untouched
Depends on: Phase 04.5 (`@eos/agent-runtime`), Phase 02.5 (live e2e harness,
`codex_coding_plan` profile, `vitest.e2e.config.ts`)

## 1. Intent

Phase 04.5's §13 integration suite pins exact composed semantics over
`MockLlmClient` scripts. Phase 04.6 re-verifies the load-bearing subset end
to end against the LIVE configured codex coding plan: a real SSE stream, a
real model choosing tools, real multi-run concurrency, real files. The
scenarios are synthetic - temp profiles, mocked deterministic tools,
directive prompts - but nothing between `startRun` and the provider socket
is faked.

What only this suite can prove:

- the §4 wiring holds against provider latency and streaming (not
  microtask-ordered scripts),
- a live model actually drives the loop through the tool families
  (subagent spawn, settle notification, guard error recovery, cancel),
- scaled tool pressure still preserves the engine/tool contracts: maximum
  parallel tool-call batches, the concurrency cap, result order, and abort
  salvage,
- turn ceilings fail as `max_turns` when an agent reaches the configured
  maximum turn limit, even after live tool-use loops or child-run failures,
- batch policy is enforced at the public tool flag:
  `isBatchExecutionForbidden` rejects whole mixed batches, while solo flagged
  calls still run,
- hook execution remains observable end to end with real processes:
  deny, input rewrite, warnings, timeout/abort, and `hook_context`
  notification publishing,
- notification triggers wake parked runs and obey drain semantics:
  background settlement, hook context, coalescing, steer priority, delivered
  tags, and safe rendering,
- interrupt/steer windows work against in-flight network calls,
- cancellation is covered at every lifecycle boundary: before provider
  output, during provider streaming, during tool execution, while parked on
  background work, after child settlement, and after parent disposal,
- recursive cancellation is proved while descendant background work is live:
  the parent cancels only its immediate session and each child run disposes
  its own background supervisor,
- the engine's salvaged provider history is accepted BY the provider as
  restart input, not merely shaped correctly,
- the raw `AgentEvent` sequence holds over real SSE chunk timing.

## 2. Design Decisions

1. **Same harness as `llm-client/e2e`.** `*.e2e.ts` under
   `packages/agent-runtime/e2e/`, picked up by the root
   `vitest.e2e.config.ts` include; invisible to the unit runner and
   `pnpm run check`; manual, laptop-only, never CI. Multi-turn scenarios
   override the 60s default with per-test timeouts.
2. **Skip-not-fail, probed through the runtime's own loader.**
   `loadConfiguredCodexRuntime()` resolves `.eos-agents/llm_clients.json`
   (`EOS_LLM_CLIENTS_PATH` override, then cwd, then `cwd/..`) and calls
   `loadLlmClientRegistry().require("codex_coding_plan")` in a try/catch.
   Loading is local (config + JWT-claim validation, no network), so a
   missing or stale credential is a suite skip carrying the loader's own
   startup error - the probe and the production path cannot drift.
3. **Structural assertions only.** Statuses, failure kinds, interrupt
   reasons, ids, transcript line kinds, event ordering, `tool_result`
   flags, hook metadata, notification payloads, and batch rejection shape.
   Prose is asserted only where the prompt pins an exact token (the mocked
   codeword, a literal summary). Most prompts are numbered with "one tool
   call per turn" so live runs stay on rails; batch rows intentionally
   instruct multiple tool calls in one assistant turn and assert the
   assembled structure, not free-form wording.
4. **Determinism comes from mocked tools, not timing.** `wait` pins a
   mid-run window (exposes a `started` promise; settles as an error result
   on its execution signal) for interrupt/steer/cancel tests;
   `lookup_codeword` pins a data flow the submission must echo;
   `finish_task` is the engine-direct terminal. `finish_task` carries a
   real `summary` schema: a propertyless spec makes the live model send
   `{}` (observed), which would leave nothing to assert.
5. **The event source is verified engine-direct.** Inside the runtime the
   transcript subscriber is the stream's single consumer (Phase 04.5
   §2.5), so raw `AgentEvent` ordering over live SSE is only observable
   via `startAgentRun` + `buildToolExecutor` directly. One file does
   exactly that; everything else goes through `createAgentRuntime`.
6. **Agent-tool scope is `run_subagent` + cancelling an agent run.**
   Cancellation of a live subagent run is exercised through
   `cancel_background_session` (the model-initiated path) - there is no
   separate `cancel_agent_run` tool by design (Phase 04.5 §8).
   `read_agent_run_transcript` rides the round-trip. `ask_advisor` adds no
   new live invariant over §13.6 and stays integration-covered.
7. **Budget guard.** The completed baseline pass is ~25-30 small provider
   calls (~60-90s wall clock); one scenario is an instant auth rejection
   and two runs are interrupted mid-wait. The expanded target is shardable
   and materially larger (~80-120 provider calls depending on model
   compliance). Each file states its provider-call budget and may expose an
   opt-in shard marker for the expensive fanout rows.
8. **Expanded rows are targets until implemented.** E2E-01..11 record the
   observed 2026-06-11 suite. E2E-12..60 are required coverage targets for
   the larger live-runtime battery; they should not be summarized as green
   until matching `*.e2e.ts` rows exist and have been run live.
9. **Recursive cancellation is boundary-observed, not globally orchestrated.**
   There is no recursive canceller API. The target tests prove the existing
   ownership chain: `supervisor.dispose("run finished")` cancels immediate
   sessions, `run_subagent` maps that to `caller_disposed`, and the cancelled
   child run's own loop disposes its own supervisor. Assertions follow
   transcripts, parent links, session rows, and absence of leaked live runs.

## 3. Scope

In scope: the §5 matrix over `packages/agent-runtime/e2e/` (target: 11 spec
files, 4 support modules), run by `pnpm run test:e2e` or by a focused
`vitest.e2e.config.ts` shard for the expanded suite.

Out of scope (and why):

- `ask_advisor` live (decision 6),
- `max_tokens` truncation - the codex wire omits `max_output_tokens`, so
  the case is not reachable on this provider,
- retry/backoff, retry-after, idle watchdog, malformed-tool-arg recovery -
  provider-fault injection; pinned by `llm-client` unit fixtures,
- provider stream/auth contract battery - `llm-client/e2e` owns it; this
  suite only re-checks the composed surfaces (E2E-04, E2E-10, E2E-11),
- future notification-rule publishers that do not exist yet. The expanded
  matrix covers today's notification trigger sources (background
  settlement and hook context), plus the generic inbox semantics that
  future trigger rules will depend on.

## 4. Layout

```
packages/agent-runtime/e2e/
├─ support/
│  ├─ codex-runtime.ts        decision 2 probe + corrupted-signature config
│  │                          writer (claims stay valid; the 401 happens live)
│  ├─ fixtures.ts             TERSE_BODY/SLEEPER_BODY prompts; mocked tools
│                             (lookup_codeword, echo_n, wait, finish_task,
│                             batch_lock, hook_probe); nested sleeper
│                             profiles; runtime
│                             fixture (temp profiles + dataDir over the real
│                             llm_clients.json, optional hooks); polling +
│                             provider-history assertion helpers
│  ├─ hook-fixtures.ts        real node hook writers for deny, rewrite,
│  │                          warnings, timeout, and additionalContext
│  └─ notification-fixtures.ts engine-direct inbox publishers and assertions
├─ agent-loop.e2e.ts          E2E-01..04
├─ subagent-supervisor.e2e.ts E2E-05..06
├─ interrupt-steer.e2e.ts     E2E-07..09
├─ engine-events.e2e.ts       E2E-10..11 (engine-direct, decision 5)
├─ tool-limits.e2e.ts         E2E-12..16
├─ batch-policy.e2e.ts        E2E-17..22
├─ hooks-notifications.e2e.ts E2E-23..34
├─ subagent-fanout.e2e.ts     E2E-35..40
├─ cancellation.e2e.ts        E2E-41..48
├─ recursive-cancel.e2e.ts    E2E-49..56
└─ cancellation-isolation.e2e.ts E2E-57..60
```

Support reuses `tests/support.ts` (profile writer, transcript reader,
message builders) and `@eos/testkit` (`scriptedTool`, `scriptedRunState`) -
the same intra-package test-helper precedent as `llm-client/e2e`.

## 5. Coverage Matrix

Task items: (1) agent loop, (2) background supervisor / subagents,
(3) interrupt / wake / steering, (4) tool calls - background / agent /
submission / mocked, (5) event source / SSE, (6) scale and limits,
(7) hooks, (8) notification triggers, (9) cancellation / disposal,
(10) recursive background-session cancellation.

E2E-01..11 are the completed baseline. E2E-12..60 are the expanded target
coverage that must be implemented before this expansion can be called done.

| # | Scenario (file :: test) | Items | Important items to check | Spec anchors |
| --- | --- | --- | --- | --- |
| E2E-01 | `agent-loop` :: completes a main run through the terminal tool | 1, 4-submission, 5 | `completed` outcome with object `submission`; live `usage` input/output > 0; `turns >= 1`; runtime handle's event stream rejects a second consumer; `steer()` after finish returns `false`; registry row reaches `finished`; transcript: dense `seq`, first line `user/initial`, exactly one `run_finished` and it is last, assistant + tool_result lines present | 04.5 §13.3/§13.4/§13.9, 03 §5/§8 |
| E2E-02 | `agent-loop` :: round-trips a mocked tool | 1, 4-mocked | mocked tool executed >= 1; its clean (`is_error: false`) result line lands before the terminal result line; the looked-up codeword reaches `submission.summary` (model consumed the result, not its prior) | 03 §14.2, 04.5 §13.3 |
| E2E-03 | `agent-loop` :: fails with kind `max_turns` | 1 | `failed { kind: "max_turns" }` with `turns === 1` under a 1-turn budget; transcript `run_finished` records `outcome_status: "failed"`; a tool-use turn alone never completes a run | 03 §14.11, 04 decision 20 |
| E2E-04 | `agent-loop` :: classifies a live auth rejection | 1, 5 | tampered JWT signature passes local startup validation, then fails live as `failed { kind: "provider_error" }`; transcript still closes with `run_finished`; registry still reaches `finished` | 02 error taxonomy, 02.5 §6.3-6, 03 §14.12 |
| E2E-05 | `subagent-supervisor` :: subagent round-trip | 2, 3-wake, 4-agent | `run_subagent` returns the child `run_id` in its tool result; child row carries `parent` = caller and `agent_kind: "subagent"`; caller parks then wakes on the drained `"session_settled"` notification naming the child run id (asserted via `outcome.llm`, quoted-JSON needle); `read_agent_run_transcript` returns the child's flushed `run_finished`; child leaves its own complete transcript | 04.5 §13.5, 04 §9, 04 §15.3/§15.6 |
| E2E-06 | `subagent-supervisor` :: submission guard + model-initiated cancel | 2, 4-background, 4-submission | early submit while the session is open fails with `cannot submit while ...` (`is_error: true`) and the LIVE MODEL recovers; `list_background_sessions` + `cancel_background_session` acknowledge; after delivery the resubmit succeeds; sleeper transcript records `cancelled` with `interrupt_reason: "model_cancelled"` | 04 §15.16/§15.9, 04.5 §8 |
| E2E-07 | `interrupt-steer` :: interrupt mid-tool | 3-interrupt | `cancelled` outcome carries the caller's reason verbatim (`operator_stop`); the in-flight `wait` call's execution signal aborted; `outcome.llm` has NO unanswered `tool_use` (provider-valid at cancellation); transcript `run_finished` records the `interrupt_reason` | 03 §14.6/§14.7/§9, 04.5 §8 |
| E2E-08 | `interrupt-steer` :: steer at the next boundary | 3-steering | `steer()` returns `true` while live; the steered instruction redirects the run (`submission.summary` = steered token); the steered message sits in `outcome.llm` AFTER the first assistant turn (boundary drain, not history rewrite) | 03 §14.8/§9 |
| E2E-09 | `interrupt-steer` :: steer wakes a parked run | 3-wake+steering, 2, 4-agent | park observed as a bare-text assistant turn with a live session (auto-wait); the steer wakes it; the model cancels the sleeper (`model_cancelled` in its transcript) and submits; `turns` stays bounded - parking consumed no provider calls | 04 §15.3, 03 §9 `waitForSteer`, 04.5 §13.5 |
| E2E-10 | `engine-events` :: live SSE event golden ordering | 5, 1 | first event `turn_started: 1`, turn numbers count up by 1; exactly one `assistant_message_complete` per turn; >= 1 incremental delta arrived (live SSE streamed); the mocked tool call surfaced as an assembled `tool_use_delta`; `tool_execution_started`/`completed` pair per id with `start <= end`; terminal flags correct per tool; `run_finished` exactly once and last; `handle.outcome` resolves to the `run_finished` payload (same object); second consumption attempt throws | 03 §8/§14.18, 02 §4.5 |
| E2E-11 | `engine-events` :: interrupt salvage + live restart | 3, 5, 1 | interrupt mid-`wait` yields `cancelled` with the passed reason and zero unanswered `tool_use` ids; `startAgentRun({ initialMessages: [...outcome.llm, newUser] })` is ACCEPTED by the live provider and completes - the synthesized error results are wire-valid, not just shape-valid | 03 §14.6/§14.7, 03 §9 redirect pattern, 04 §15.21 |
| E2E-12 | `tool-limits` :: maximum tool-call batch over the concurrency cap | 4-mocked, 5, 6 | live model emits 12 `echo_n` calls in one assistant message; executor instrumentation observes `maxInflight === 8`; 12 `tool_execution_started` and 12 completed events; one tool-result user message preserves provider `tool_use` order; terminal submission cites all 12 returned values | 03 §14.3, 04 §7, 04 §15.1 |
| E2E-13 | `tool-limits` :: interrupt while maximum batch is partially in flight | 3-interrupt, 4-mocked, 6 | 12-call batch starts; interrupt after at least one settled result; outcome is `cancelled`; settled calls keep real results, queued/unfinished calls get synthetic `"interrupted"` results; no tool completion event lands after `run_finished`; `outcome.llm` has no unanswered `tool_use` | 03 §14.7, 04 §7 normalization |
| E2E-14 | `tool-limits` :: agent reaches the maximum turn limit after tool-only loops | 1, 4-mocked, 6 | profile `max_turns` set to a small number; prompt forces repeated nonterminal tool calls and forbids submission; outcome is `failed { kind: "max_turns" }`; `turns` equals the configured ceiling; transcript `run_finished` has no submission and still follows the final tool result | 03 §14.11, 04 decision 20 |
| E2E-15 | `tool-limits` :: restart from a max-turn failure | 1, 5, 6 | `outcome.llm` from E2E-14 is reused with a fresh user message and larger budget; live provider accepts the history; second run completes through terminal submission; previous failure is not replayed as a dangling tool-use error | 03 §9, 03 §14.11 |
| E2E-16 | `tool-limits` :: late steer does not hide maximum-turn failure | 1, 3-steering, 6 | steer is queued as the run is about to exhaust its budget; outcome remains `failed { kind: "max_turns" }`; steered message is absent from the transcript when the budget check wins; no terminal outcome is synthesized after the ceiling is reached | 03 §14.10/§14.11 |
| E2E-17 | `batch-policy` :: independent batch tool calls execute together | 4-mocked, 5, 6 | live model emits two distinct nonterminal tool calls in one turn; both execute before the next provider call; results are one tool-result user message in request order; model consumes both values before submission | 03 §14.3, 04 §7 |
| E2E-18 | `batch-policy` :: thrown sibling does not suppress successful sibling | 4-mocked, 6 | one batched tool throws and one succeeds; failed result has `is_error: true`; successful sibling result is clean; model receives both and recovers through terminal submission | 03 §14.4, 04 §7 |
| E2E-19 | `batch-policy` :: default terminal `isBatchExecutionForbidden` rejects a mixed batch | 4-submission, 6 | live model intentionally batches `submit_main_outcome` with a sibling; executor dispatches nothing; every result is `is_error: true` and `is_terminal: false`; model then retries a solo submission and completes | 04 §15.1, 04 §5 `defineTool` defaults |
| E2E-20 | `batch-policy` :: nonterminal `isBatchExecutionForbidden` rejects a mixed batch but solo recovers | 4-mocked, 6 | custom nonterminal `batch_lock` has `isBatchExecutionForbidden: true`; mixed batch is rejected wholesale without executing siblings; a later solo `batch_lock` call executes and does not terminate; final submission still succeeds | 04 §15.1, 04 §7 |
| E2E-21 | `batch-policy` :: batch-forbidden rejection names are deduped and sorted | 4-mocked, 6 | model emits duplicate calls to two flagged tools plus siblings; rejection message names each flagged tool once in sorted order; all calls receive paired error results; no `tool_execution_started` events fire | 04 §15.1, `packages/tool/tests/executor.test.ts` |
| E2E-22 | `batch-policy` :: explicitly relaxed terminal call may batch and still terminate | 4-submission, 6 | test-only terminal tool sets `isTerminal: true, isBatchExecutionForbidden: false`; mixed batch dispatches; terminal result keeps `is_terminal: true`; run completes from that result and sibling result is still recorded before finish | 04 §5 defaults, 04 §7 |
| E2E-23 | `hooks-notifications` :: real pre-hook denies based on `transcript_path` | 7, 4-mocked | spawned node hook reads the live transcript path from `HookPayload`; denial prevents tool execution; tool result carries the hook reason as `is_error: true`; model recovers with an allowed tool and submits | 04 §6, 04.5 §13.8 |
| E2E-24 | `hooks-notifications` :: real pre-hook rewrites input and the model consumes the rewrite | 7, 4-mocked | hook returns `updatedInput`; pipeline re-validates against the same Zod schema; executed tool sees the rewritten payload; submission cites the rewritten codeword, not the prompt's original value | 04 §6, 04 §15.12 |
| E2E-25 | `hooks-notifications` :: hook `additionalContext` triggers a model-visible notification | 7, 8 | post-hook returns `additionalContext`; tool result metadata carries `hook_contexts`; engine publishes a `hook_context` notification at the next loop boundary; model sees the notification after the tool result and uses its exact token in submission | 04.5 decision 11, 04 §6 |
| E2E-26 | `hooks-notifications` :: hook warnings are non-blocking and transcript-visible | 7, 4-mocked | one hook exits nonzero without deny or emits invalid JSON; tool still runs; result metadata contains `hook_warnings`; transcript `tool_result` line preserves warnings; run completes cleanly | 04 §8, 04 §15.13 |
| E2E-27 | `hooks-notifications` :: hook timeout and run abort do not hang child processes | 3-interrupt, 7 | long-running hook starts; run interrupt aborts the hook command; outcome is `cancelled` or cleanly records an interrupted error result; no spawned hook process remains after outcome settles | 04 §8, 04.5 §8 |
| E2E-28 | `hooks-notifications` :: malformed hook config fails at runtime creation | 7 | invalid `hooks.json` produces a startup error naming the Zod issue before any provider call; missing hook config still means no hooks and the baseline run succeeds | 04.5 §7/§13.8 |
| E2E-29 | `hooks-notifications` :: background settlement notification trigger wakes a parked run | 2, 3-wake, 8 | caller parks after spawning a sleeper subagent; child settlement publishes `session_settled`; parked loop wakes without burning provider turns; drained notification names the child run id and marks the session delivered | 04 §9, 04 §15.3 |
| E2E-30 | `hooks-notifications` :: hook-context notification trigger wakes a parked run | 3-wake, 7, 8 | run returns bare text while a hook-context notification is pending; `NotificationInbox.waitForNext` wakes the loop; notification is drained before the next provider call; model submits using the hook context | 04 §7 `NotificationInbox`, 04.5 decision 11 |
| E2E-31 | `hooks-notifications` :: same-key notifications coalesce before drain | 8, 1 | engine-direct test publishes two messages with the same key and different tags before loop drain; only the latest message reaches `outcome.llm`; `onDrained` receives only the latest tag; provider run completes with that latest value | 04 §7 `NotificationInbox` |
| E2E-32 | `hooks-notifications` :: steer priority beats notification at the same boundary | 3-steering, 8 | a steer and notification are queued while the run is parked; loop drains steer messages before system notifications; `outcome.llm` ordering proves user steer priority; final submission follows the steer instruction | 04 §7, 03 §9 |
| E2E-33 | `hooks-notifications` :: notification rendering cannot spoof tag boundaries | 8 | notification payload includes `<system_notification>`-like text; rendered message escapes `<` inside JSON; provider-visible text has one outer notification tag; model extracts the intended payload value only | 04 §7 `systemNotificationMessage` |
| E2E-34 | `hooks-notifications` :: notification drain enables guarded submission | 2, 4-submission, 8 | submission is blocked while a session is settled-but-undelivered; draining the notification fires delivery bookkeeping; immediate resubmit succeeds; transcript records the blocked result before the final terminal result | 04 §9, 04 §15.16 |
| E2E-35 | `subagent-fanout` :: fans out two subagents in one batch and submits only after both settle | 2, 3-wake, 4-agent, 6, 8 | main run emits two `run_subagent` calls in one assistant turn; launch tool results return both child ids before any child completion notification; both rows have `parent` = caller and `agent_kind: "subagent"`; both child transcripts end `completed`; exactly one `session_settled` notification per child reaches the parent before submission | 04.5 §13.5, 04 §9 |
| E2E-36 | `subagent-fanout` :: child reaches maximum turn limit and parent recovers | 1, 2, 6, 8 | subagent profile has a tiny `max_turns`; child fails with `max_turns`; settlement notification reports failed child outcome and summary; parent remains alive and still submits | 03 §14.11, 04.5 §5/§13.5 |
| E2E-37 | `subagent-fanout` :: nested subagent completion stays in the owning inbox | 2, 3-wake, 4-agent, 8 | main starts relay; relay starts leaf; parent links form main -> relay -> leaf; relay receives the leaf settlement and submits; main receives exactly the relay settlement and never sees the grandchild's notification | 04.5 §2.1/§13.5, 04 §9 |
| E2E-38 | `subagent-fanout` :: model cancels one background subagent while another completes | 2, 4-background, 6, 8 | parent starts a helper and a sleeper in one batch; `list_background_sessions` exposes the open child rows; model calls `cancel_background_session` for the sleeper; cancelled child records `model_cancelled`, helper completes, and both settlement notifications reach the parent before submission | 04.5 §8, 04 §15.9 |
| E2E-39 | `subagent-fanout` :: subagent cannot start a main profile through the agent tool | 2, 4-agent | a child run attempts `run_subagent` against a main-only profile; tool result is `is_error: true` and names the invalid profile/kind boundary; parent remains alive and can submit a guarded failure summary | 04.5 §4, runtime test "rejects starting a main profile from inside a run" |
| E2E-40 | `subagent-fanout` :: transcript offset reads scale across child runs | 2, 4-agent, 6 | parent reads each child transcript with offset windows; offsets advance monotonically; reread from previous offset returns only increments; final reads include each child's `run_finished` and no duplicate lines | 04.5 §6/§13.9 |
| E2E-41 | `cancellation` :: caller aborts before the first provider turn commits | 1, 3-interrupt, 9 | outcome is `cancelled` with the supplied reason; no assistant or tool_result lines are written; transcript still ends with exactly one `run_finished`; registry reaches `finished`; `steer()` after the abort returns `false` | 03 §8/§14.6, 04.5 §2.10 |
| E2E-42 | `cancellation` :: caller aborts during the live provider stream before any tool use is complete | 1, 3-interrupt, 5, 9 | provider request is aborted through the run signal; outcome classifies as `cancelled`, not `provider_error`; `outcome.llm` has no unanswered `tool_use`; `run_finished` is last even if the SSE stream closes with an abort-shaped error | 02.5 §6.3-5, 03 §7/§14.6 |
| E2E-43 | `cancellation` :: double interrupt preserves the first cancellation reason | 1, 3-interrupt, 9 | two `handle.interrupt(...)` calls race while the run is live; the transcript and outcome carry only the first reason; exactly one `run_finished` event is emitted; no second cancellation notification or transcript tail appears | 03 §14.6, 04.5 §8 |
| E2E-44 | `cancellation` :: interrupt after a nonterminal tool result but before the next provider call | 1, 3-interrupt, 4-mocked, 9 | the tool_result is present and answered; the next loop-top check exits as `cancelled` before another `turn_started`; provider history remains restart-valid; transcript has no partial second assistant turn | 03 §7/§14.7, 04 §15.21 |
| E2E-45 | `cancellation` :: interrupt wakes a run parked on auto-wait with a live background session | 2, 3-wake, 9 | parent has emitted a bare assistant text turn and is waiting on `waitForWake`; interrupt wakes the race; parent finishes `cancelled`; its `finally` disposes the live session; no extra provider call is made after the park | 04 §15.3, 04 §2.17, 04.5 §8 |
| E2E-46 | `cancellation` :: cancelling an unknown background session is tool-level recoverable | 2, 4-background, 9 | bad `{ type, id }` returns an error tool_result; the live model sees the error, lists sessions, chooses the valid id, cancels it, and submits; no run-level failure is recorded | 04 §15.9, 04.5 §8 |
| E2E-47 | `cancellation` :: explicit cancel after the child naturally settled is a no-op the model can recover from | 2, 4-background, 9 | child has terminal status but its settlement is undelivered; `cancel_background_session` returns the already-terminal/unknown no-op result rather than throwing; no duplicate `session_settled` is published; parent drains the original settlement and submits | 04 §9, 04 §15.16 |
| E2E-48 | `cancellation` :: register-after-dispose latch cancels late handles | 2, 9 | parent aborts while `run_subagent` has started a child but before the tool continuation registers the handle; the latched supervisor immediately calls the incoming handle's cancel; no row is registered or published into the dead parent; child is cancelled as `caller_disposed` | 04 §9 dispose latch, 04.5 §8 |
| E2E-49 | `recursive-cancel` :: parent interrupt cancels child while the child has a running background task | 2, 4-agent, 9, 10 | main spawns child A; A spawns sleeper B and parks; interrupting main cancels A through main's session handle; A's own `finally` disposes B; transcripts show main `operator_stop`, A `caller_disposed`, B `caller_disposed`; no live rows remain in any supervisor | 04.5 §2.1/§8, 04 §2.17/§9 |
| E2E-50 | `recursive-cancel` :: model cancels child while the child has a running background task | 2, 4-background, 4-agent, 9, 10 | main calls `cancel_background_session` for A; A transcript records `model_cancelled`; A's disposal still cancels B as `caller_disposed`; main receives one cancelled settlement for A and can read both transcripts before submitting | 04.5 §8, 04 §9 |
| E2E-51 | `recursive-cancel` :: child failure disposes its own running background task | 2, 4-agent, 9, 10 | A fails after starting B; A's session settles `failed` to main; B is cancelled by A's exit path as `caller_disposed`; main drains a failed `session_settled`, verifies the B transcript, and submits a failure summary | 04 §9 rejection/failure mapping, 04.5 §13.9 |
| E2E-52 | `recursive-cancel` :: child cannot submit while its own background task is open | 2, 4-submission, 10 | A starts B and attempts `submit_subagent_outcome`; A gets the submission guard error, cancels or waits for B, then submits; main sees A complete only after A's own `openCount()` reaches zero | 04 §15.16, 04.5 §13.5 |
| E2E-53 | `recursive-cancel` :: grandchild settled-undelivered when child is cancelled | 2, 3-wake, 9, 10 | B has terminal status in A's supervisor but A has not drained the notification; cancelling A does not recancel B or publish into A after death; B remains naturally completed in its transcript, while A is cancelled per trigger reason | 04 §9 delivered/evicted lifecycle, 04.5 §8 |
| E2E-54 | `recursive-cancel` :: three-deep cancellation chain unwinds by ownership | 2, 9, 10 | main -> A -> B -> C all have live sleeper descendants; cancelling main reaches only A directly; B and C are cancelled by their own callers' disposal; every transcript has one `run_finished`, every descendant reason is `caller_disposed`, and parent links form the expected chain | 04.5 §2.1/§2.6/§8 |
| E2E-55 | `recursive-cancel` :: late grandchild registration after ancestor cancellation is latched | 2, 9, 10 | main cancellation lands while A's tool continuation is between starting B and registering it; A's already-latched supervisor cancels B immediately; B cannot leak as an untracked live run and no notification is published to A | 04 §9 dispose latch |
| E2E-56 | `recursive-cancel` :: cancelling one nested branch does not cancel a sibling branch | 2, 4-background, 9, 10 | main starts A and S as sibling sessions; A has running child B; model cancels A only; A/B end cancelled, S remains running and blocks submit until separately settled/cancelled; sibling isolation is visible in `list_background_sessions` | 04.5 §2.1, 04 §9 |
| E2E-57 | `cancellation-isolation` :: two live main runs isolate cancellation and background sessions | 1, 2, 9, 10 | run A and run B concurrently, each with a sleeper subagent; cancel A; A's child is `caller_disposed`; B's child remains running and B can still complete; transcripts and registries do not cross-contaminate ids or notifications | 04.5 §2.1/§2.3, 04 §9 |
| E2E-58 | `cancellation-isolation` :: one run cancels many background sessions on disposal | 2, 9, 10 | parent starts N sleeper subagents under a small fixed N; parent interrupt causes all N child transcripts to finish `caller_disposed`; teardown is bounded by the file's timeout; no unhandled rejections from any handle | 04 §9 dispose, 04.5 §8 |
| E2E-59 | `cancellation-isolation` :: repeated start/cancel cycles leave no stale sessions | 2, 9 | repeat a small loop of start sleeper -> cancel -> drain -> submit/restart in one temp dataDir; run ids stay unique; `list_background_sessions` is empty after each cycle; transcripts stay separate | 04.5 §2.3/§13.9 |
| E2E-60 | `cancellation-isolation` :: clean skip path applies to every scaled shard | harness | with missing credentials, cancellation/background/recursive/scale files all skip through `loadConfiguredCodexRuntime()` with the loader reason; no target shard performs a network call before the skip | 02.5 §6.3, 04.6 decision 2 |

Run inventory per scenario: E2E-01..04 one main run each (E2E-04 fails at
the first provider call); E2E-05/06/09 one main + one subagent run; E2E-07/08
one main run; E2E-10/11 engine-direct handles (E2E-11 starts two).
Expanded rows: E2E-12..22 are one main run each except E2E-13 may be
engine-direct for precise abort timing; E2E-23..34 are one main run each
or engine-direct inbox probes for E2E-31/33; E2E-35..40 create 2-4 child
runs each; E2E-41..48 are cancellation timing rows; E2E-49..56 create
nested subagent chains with live descendant background sessions; E2E-57..60
cover process isolation, fanout cleanup, repeatability, and shard skip
behavior. Each expanded file must carry an explicit provider-call budget and
should keep expensive fanout rows opt-in when running the whole live battery
on laptops.

## 6. Edge-Case Disposition (mined from the migration specs)

Candidate edge cases from phases 02-04.5, with where each is (or is not)
covered. "Unit" = the owning package's mock/fixture suite; "§13" = the
Phase 04.5 integration suite.

| Edge case | Source | Disposition |
| --- | --- | --- |
| Exactly one `assistant_message_complete` per provider call | 02 §4.5 | E2E-10 (live), unit goldens |
| Abort classified by `signal.aborted`, never error type | 02.5 §6.3-5 | E2E-07/11 composed; `llm-client/e2e` contract |
| Auth failure taxonomy (`authentication` -> run `provider_error`) | 02, 02.5 §6.3-6 | E2E-04 composed; `llm-client/e2e` contract |
| Caller abort before provider output and during provider streaming | 02.5 §6.3-5, 03 §14.6 | E2E-41/42 expanded target; E2E-07/11 remain mid-tool baseline |
| Double interrupt first reason wins | 03 §14.6, 04.5 §8 | E2E-43 expanded target |
| Retry only before visible output; retry-after; idle watchdog | 02 §4.6/§4.7 | Unit only - needs provider-fault injection |
| Malformed streamed tool-arg JSON -> `{}` input | 02 §4.4 | Unit only - not forcible live |
| `run_finished` always last; single-consumer stream | 03 §8 | E2E-10 (engine), E2E-01 (runtime handle) |
| Every `tool_use` answered at every exit | 03 §7 | E2E-07 (cancel), E2E-11 (restart proof), E2E-13 (max batch interrupt) |
| `outcome.llm` is provider-valid restart input | 03 §9 | E2E-11 and E2E-15 - live acceptance, the part mocks cannot prove |
| Maximum parallel tool-call batch and concurrency cap | 03 §14.3, 04 §7 | E2E-12/13 expanded target; unit cap test remains the deterministic source |
| Batch result order and one result message | 03 §14.3, 04 §7 | E2E-12/17 expanded target |
| Tool throw does not suppress siblings | 03 §14.4, 04 §7 | E2E-18 expanded target, unit runner |
| Steer drains at boundary; outranks nothing mid-turn | 03 §14.8 | E2E-08 (position asserted in `outcome.llm`), E2E-32 (notification priority) |
| Steer at/after finish returns `false` | 03 §14.10 | E2E-01 |
| Steer during final no-tool turn keeps the loop alive | 03 §14.9 | E2E-16 expanded target; unit remains the exact timing proof |
| `maxTurns` check precedes steer drain | 03 §14.11 | E2E-03 (failure kind), E2E-14/16 expanded target |
| Restart after `max_turns` failure | 03 §9/§14.11 | E2E-15 expanded target |
| `max_tokens` truncation surfaced as `stop_reason` | 03 §14.14 | Not coverable on codex (wire omits `max_output_tokens`) |
| Bare text never terminates; terminal tool is the only completion | 04 decision 20 | E2E-03 (tool turn + budget), E2E-09 (text turn parks instead of finishing) |
| Auto-wait parks instead of burning calls; settle/steer wakes | 04 §15.3 | E2E-05 (notification wake), E2E-09 (steer wake, bounded `turns`), E2E-29/30 expanded target |
| Interrupt wakes auto-wait with live background work | 04 §15.3, 04.5 §8 | E2E-45 expanded target |
| Submission guard: running or undelivered session blocks submit | 04 §15.16 | E2E-06, E2E-34 expanded target, with live-model recovery |
| Supervisor settle -> notify -> drain marks delivered -> evict | 04 §9 | E2E-05/06 baseline; E2E-29/34 expanded target |
| Terminal-solo / batch-forbidden policy | 04 §15.1 | E2E-19 default terminal, E2E-20 nonterminal flag, E2E-21 names, E2E-22 explicit relaxation |
| `isBatchExecutionForbidden` fail-closed defaults | 04 §5 | E2E-19/20 expanded target plus `defineTool` unit table |
| Hook deny over `transcript_path` | 04.5 §13.8 | E2E-23 expanded target; §13 already proves real spawned process plumbing |
| Hook input rewrite / warning / timeout behavior | 04 §6/§8 | E2E-24/26/27 expanded target; unit remains the precise protocol proof |
| Hook `additionalContext` -> `hook_context` notification | 04.5 decision 11 | E2E-25/30 expanded target |
| Notification key coalescing and drained tags | 04 §7 `NotificationInbox` | E2E-31 expanded target |
| Notification/steer same-boundary priority | 04 §7, 03 §9 | E2E-32 expanded target |
| Notification rendering escapes spoofed tags | 04 §7 `systemNotificationMessage` | E2E-33 expanded target |
| Dispose latch + `caller_disposed` cascade | 04.5 §8, §13.7 | E2E-37/45/48 expanded target; §13 remains deterministic baseline |
| Unknown and already-terminal background cancel recovery | 04 §15.9, 04 §9 | E2E-46/47 expanded target |
| Recursive cancellation while descendant background work is running | 04.5 §8, 04 §9 | E2E-49/50/54 expanded target |
| Child failure or child submission guard with descendant background work | 04 §15.16, 04 §9 | E2E-51/52 expanded target |
| Grandchild settled-undelivered when parent branch is cancelled | 04 §9 | E2E-53 expanded target |
| Late descendant registration after ancestor cancellation | 04 §9 dispose latch | E2E-55 expanded target |
| Nested branch cancellation leaves sibling branch alive | 04.5 §2.1, 04 §9 | E2E-56/57 expanded target |
| Fanout disposal and repeated cancel cycles leave no stale sessions | 04 §9, 04.5 §13.9 | E2E-58/59 expanded target |
| Multi-subagent fanout and transcript reads | 04.5 §13.5/§13.9 | E2E-35/40 expanded target |
| Subagent child `max_turns` failure recovery | 03 §14.11, 04.5 §5 | E2E-36 expanded target |
| Mixed subagent cancel/complete statuses | 04.5 §8, 04 §15.9 | E2E-38 expanded target |
| Subagent profile/kind boundary | 04.5 §4 | E2E-39 expanded target, runtime unit baseline |
| Transcript flush gates reads; finished after flush | 04.5 §13.9 | E2E-05 (child read sees `run_finished`), E2E-01/04 (registry `finished` after flush, even on failure), E2E-40 expanded target |
| `model_cancelled` vs `caller_disposed` reason recording | 04.5 §8 | E2E-06/09 (`model_cancelled`); E2E-37/38/49/50 expanded target covers both reasons together |
| Notification/steer user messages in the transcript file | 04.5 §6 | Known gap until the broadcaster phase gives the writer a live source; E2E-25/29/32/34 assert `outcome.llm` ordering and inbox delivery instead |
| Codex token expiry mid-run / refresh-on-read | 04.5 §10 | Deferred seam, untested |

## 7. Verification

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm run test:e2e                       # whole live battery (llm-client + agent-runtime)
pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e   # this suite only
pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e/tool-limits.e2e.ts
pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e/batch-policy.e2e.ts
pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e/hooks-notifications.e2e.ts
pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e/subagent-fanout.e2e.ts
pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e/cancellation.e2e.ts
pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e/recursive-cancel.e2e.ts
pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e/cancellation-isolation.e2e.ts
EOS_LLM_CLIENTS_PATH=/nonexistent pnpm exec vitest run --config vitest.e2e.config.ts packages/agent-runtime/e2e   # skip path
```

Baseline observed 2026-06-11 on this machine (`gpt-5.5`, medium effort):

- live: 4 files, 11/11 passed, ~63s wall clock,
- skip path: 11 skipped in <1s, warning carries the loader's reason,
- `pnpm run check` untouched by this phase: typecheck + lint clean, 282
  unit tests green (e2e files excluded from the unit runner),
- `git diff --stat -- agent-core` empty.

Expanded shard observed 2026-06-11 on this machine:

- `subagent-fanout.e2e.ts`: 1 file, 4/4 passed, 46.87s wall clock,
- `EOS_LLM_CLIENTS_PATH=/nonexistent` clean-skips the shard: 4 skipped in
  320ms,
- `pnpm run check` clean (282 unit tests), and `git diff --check` clean.

Expanded-target verification still required:

- implement the remaining E2E-12..60 target rows not yet backed by matching
  live tests,
- run each expanded shard live and record file/test counts plus wall clock,
- run the absent-credential skip path over the expanded suite,
- run `pnpm run check` to prove unit/lint/typecheck remains unaffected,
- verify `git diff --stat -- agent-core` stays empty.

Known live-suite caveats: scenarios bet on the model following numbered
tool-use instructions (the same bet as the `llm-client/e2e` battery). The
expanded batch rows deliberately ask for multiple tool calls in one
assistant turn; a disobedient model fails structurally, not silently.
Retries are disabled (`retry: 0`), so a flake reads as a real failure to
investigate, not noise.

## 8. Acceptance Criteria

Phase 04.6 baseline remains accepted on the E2E-01..11 evidence above.
The expanded Phase 04.6 coverage is accepted when:

- `packages/agent-runtime/e2e/` contains the full §4 expanded layout and
  the §5 matrix is implemented one row per test, with budget-guard
  comments per file,
- the suite passes live against the configured `codex_coding_plan` entry
  and clean-skips (with the loader's reason) when credentials are absent,
- E2E-12..60 cover maximum tool-call pressure, max-turn exhaustion,
  batch calls, `isBatchExecutionForbidden`, hook verification,
  notification triggers, subagent scale, cancellation timing, recursive
  cancellation with live descendant background sessions, and isolation
  cleanup as named in the matrix,
- no `src/` file in any package changed; `pnpm run check` is unaffected;
  the Rust `agent-core/` tree is byte-for-byte unchanged,
- and the migration `index.md` separates the original 11-row completion
  from the expanded coverage completion, with current verification.
