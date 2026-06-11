# EOS Agent Core Rust to TypeScript Migration - Phase 04.10 Text Termination Mode

Status: Completed
Date: 2026-06-12
Owner: eos-agent-core
Migration direction: Rust -> TypeScript
Project path: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core`
Rust source boundary: none (new design; the Rust engine has no profile-optional
terminal tool, the same way Phase 03 steering was new design)
Amends: Phase 04 (terminal-only exit), Phase 04.5 (profile contract + per-run
wiring), Phase 04.9 (trigger payload + baseline rules)
Depends on: Phases 02-04.9 as landed

## 1. Intent

Let an agent profile omit `terminal_tool`. A run started from such a profile
terminates on a bare-text assistant turn — under exactly the gate the terminal
submission tools already answer to — and the final text rides the outcome as
`submission`, so parents (`run_subagent` settlements, `ask_advisor`, future
callers of `outcome.submission`) read the text through the existing path.

The first intended user is the subagent kind: a research/helper subagent
should not need a `submit_subagent_outcome` round-trip to say what it found.
The switch is per-profile config, not per-kind behavior: a subagent profile
that keeps `terminal_tool` keeps today's regime byte-for-byte.

Both halves of the current "you must call your terminal tool" policy loosen
together:

- the **loop mechanism**: bare text never finishes a run today
  (`agent-loop.ts:94-111` parks or spins until `maxTurns`),
- the **notification rule**: the baseline `remind-terminal-submission.cjs`
  nudges every gated no-tool-call turn toward `payload.terminal_tool`,

so a text-mode run finishes cleanly instead of being nudged toward a tool it
does not have.

## 2. Design Decisions

1. **Absence is the switch.** `terminal_tool` present in the profile
   frontmatter → terminal mode (today's behavior, unchanged); absent → text
   mode. No new frontmatter flag, no kind-keyed default: the profile config
   decides per agent.
2. **The engine stays profile-ignorant.** `StartAgentRunInput` gains
   `terminationMode?: "terminal_tool" | "text"`, default `"terminal_tool"`.
   The runtime derives it from the profile; an engine-direct caller that
   omits it gets today's behavior exactly.
3. **The text-exit gate is the submission guard, relocated.** The
   `no-open-background-sessions.cjs` PreToolUse hook denies every `submit_*`
   call while `background_sessions` (running plus settled-but-undelivered,
   the `listBackgroundSessions()` projection) is non-empty. The text exit
   requires the same set to be empty — `openBackgroundSessionCount() === 0`
   (`background-session-supervisor.ts:141`, whose doc comment already names
   itself the submission guard; this phase gives it its first caller) — plus
   an empty steer queue. Running sessions keep today's auto-wait park;
   settled-but-undelivered sessions fall through to `continue`, whose next
   drain delivers the settlement before any exit (no notification is ever
   silently dropped by an early finish).
4. **Steers outrank bare text.** A pending steer blocks the text exit and
   extends the run (Phase 03 §5 step 6's "none + steers queued → continue"
   returns). This is deliberately asymmetric with the terminal tool, where
   the submission outranks mid-batch steers: a terminal call is an explicit
   act of completion; bare text is implicit, so a concurrent user redirect
   wins.
5. **The exit commits synchronously.** Between `appendAssistant` and
   `finish()` the text-exit decision block contains no `await`, so no
   interrupt or steer can land between the decision and the finish. An abort
   that loses the race to a completed final stream yields `completed`
   (parity with the terminal rule "the submission outranks late
   redirection"); an abort mid-stream still classifies `cancelled` with the
   usual partial salvage.
6. **`submission` is the text projection; `final_message` is the message.**
   On a text exit, `submission = assistantText(final_message)`
   (`@eos/contracts`, text blocks only — reasoning excluded) and
   `final_message` carries the full assistant message. An empty text turn
   (e.g. reasoning-only) still terminates, with `submission === ""`:
   consistent-but-simple over salvage, and `maxTurns` still backstops the
   steer/session-extended paths.
7. **The finishing text turn skips `observer.turnCompleted`.** The 04.9
   contract "awaited after every committed assistant turn" becomes "after
   every committed turn that can still steer the run". The port is
   notification-only — a publish into a finished run informs nobody — and
   `turnCompleted` is awaited, so firing it would also delay `run_finished`
   by a spawned-script round. Non-finishing text turns (steers pending,
   sessions open) and all tool turns announce exactly as today.
8. **`TriggerPayload.terminal_tool` widens to `string | null`.** Explicit
   `null` (never an absent field) because the payload crosses a process
   boundary and operator scripts must have a checkable value. The baseline
   rules branch on it: `remind-terminal-submission.cjs` skips (text-mode
   runs exit instead of spinning, and the turns that do continue — steers,
   sessions — are not submission failures); `budget-reminder.cjs` swaps
   "submit via <tool>" for text-mode wording; `idle-wake.cjs` never read it.
9. **`planner` and `worker` keep their terminal tools.** The workflow
   transitions consume structured, schema-validated submissions through
   `SubmissionBinding`; a free-text planner outcome would synthesize
   failures. The loader rejects omission for those kinds at startup, in the
   same kind-gated style as the existing `workflow_context_script` rule.
   `main`, `advisor`, and `subagent` may omit.
10. **String submissions pass through `run_subagent` verbatim.**
    `submissionSummary` currently `JSON.stringify`s non-object submissions,
    which would hand the parent a quoted blob; a plain-string submission
    becomes the settlement summary as-is. Object-with-`summary` and
    `undefined` behavior is unchanged.
11. **No contract or event changes.** `AgentRunOutcome.submission` is
    already `JsonValue | undefined`; the `AgentEvent` union, conversation
    divergence policy, interrupt/steer semantics, salvage rules, hooks
    engine, and the `no-open-background-sessions` hook config are untouched.

## 3. Scope

In scope:

- `@eos/engine`: the `terminationMode` option and the gated text-exit branch,
- `@eos/agent-runtime`: optional `terminal_tool` in the profile
  loader/registry, per-run derivation of the mode, `null` terminal-tool
  wiring to the trigger engine,
- `@eos/notification`: the `string | null` payload/deps widening,
- `@eos/tool`: the `run_subagent` string-summary passthrough,
- repo-root `.eos-agents/notification-rules/` baseline scripts: `null`
  branches,
- test support (`ProfileSpec.terminal: string | null`, e2e fixture
  null-awareness), the §13 unit suites, and the new
  `packages/agent-runtime/e2e/text-termination.e2e.ts` shard,
- the migration `index.md` row.

Out of scope:

- any edit under `agent-core/` (Rust stays live and byte-for-byte unchanged),
- text mode for `planner`/`worker` kinds (rejected for now, decision 9),
- changes to in-repo production profiles (none exist for subagents; the
  mechanism ships with fixtures/e2e exercising it, per "disable terminal_tool
  from subagent for now"),
- early text exit on `stop_reason`, reminder seams for text mode, hook-config
  changes, server transports, compaction.

## 4. Surface and Target

| Surface | Today | Change |
| --- | --- | --- |
| `packages/engine/src/index.ts:26-61` (`StartAgentRunInput`) | no mode | add `terminationMode?: "terminal_tool" \| "text"`, default `"terminal_tool"`, threaded into `AgentLoopContext` |
| `packages/engine/src/agent-loop.ts:47-50` (doc) | "bare text never terminates" | reword: mode-dependent |
| `packages/engine/src/agent-loop.ts:94-111` (no-tool-call arm) | observer → park/continue | text-exit branch before the observer call (§5) |
| `packages/engine/src/agent-runtime-handle.ts:136-143` (completed arm docs) | `submission` = terminal content | doc: terminal content or final text |
| `packages/background/src/background-session-supervisor.ts:141` | `openBackgroundSessionCount` unused in `src/` | becomes the loop's text-exit gate read (no supervisor change) |
| `packages/agent-runtime/src/agent-profile-loader.ts:22,42` | `terminal_tool` required | optional; required for `planner`/`worker` |
| `packages/agent-runtime/src/agent-profile-registry.ts:77,82-98` | always selects/validates the terminal name | both conditional on presence |
| `packages/agent-runtime/src/runtime.ts:396,399-411` | `terminalTool: profile.terminal_tool`; no mode | `?? null` to the trigger engine; derive `terminationMode` for `startAgentRun` |
| `packages/notification/src/triggers.ts:92` (`TriggerPayload`) | `terminal_tool: string` | `string \| null` |
| `packages/notification/src/trigger-runner.ts:78` (deps) | `terminalTool: string` | `string \| null` |
| `packages/tool/src/tools/agent/run-subagent.ts:75-85` | `JSON.stringify` fallback | plain strings pass through verbatim |
| `.eos-agents/notification-rules/remind-terminal-submission.cjs` | always nudges the gated turn | skip when `terminal_tool === null` |
| `.eos-agents/notification-rules/budget-reminder.cjs` | "submit via `<tool>`" | `null` branch: "finish by replying with your final answer as plain text" |
| `packages/agent-runtime/tests/support.ts:195-230` (`ProfileSpec`) | `terminal?: string`, default-filled | `terminal?: string \| null`; `null` omits the frontmatter line |
| `packages/agent-runtime/e2e/support/fixtures.ts:82-100,326-348` | `spec.terminal ?? submit_<kind>` | null-aware (no advisor injection for text-mode profiles) |

## 5. Loop Design

The loop with the new branch (NEW marks the only behavior change; everything
else is the landed 04.x loop):

```
startAgentRun(input) ──► RunHandle { events, outcome, steer(), interrupt() }
                              │      terminationMode: "terminal_tool" | "text"
            ┌─────────────────▼─────────────────────────────────────────────┐
            │ while (true)                                  agent-loop.ts   │
            │  1. aborted?            ──► finish(cancelled)                 │
            │  2. turns ≥ maxTurns?   ──► finish(max_turns)                 │
            │  3. drain steers, then notifications ──► conversation         │
            │  4. emit turn_started; msg = runAssistantTurn(...)            │
            │  5. appendAssistant(msg); turns++; calls = toolUses(msg)      │
            │                                                               │
            │  6. calls == 0  AND  mode == "text"                           │
            │     AND no pending steers                                     │
            │     AND openBackgroundSessionCount == 0                       │
            │       ──► finish(completed,                            ◄─ NEW │
            │            submission = assistantText(msg))                   │
            │           (observer.turnCompleted is NOT announced)           │
            │                                                               │
            │  7. await observer.turnCompleted(facts)  [TurnCompleted]      │
            │  8. calls == 0?                                               │
            │     ├─ steers pending          ──► continue (step 1)          │
            │     ├─ running sessions > 0    ──► park (auto-wait); wake ──► │
            │     │                              continue (step 1)          │
            │     └─ else                    ──► continue (step 1)          │
            │         [terminal-mode spin; text-mode settled-undelivered    │
            │          drain-through — the next step 3 delivers it]         │
            │  9. executeBatch(calls) ──► normalize ──► append BOTH lists,  │
            │     publish hook contexts                                     │
            │ 10. any result is_terminal? ──► finish(completed, submission) │
            │ 11. aborted? ──► finish(cancelled); else loop                 │
            └───────────────────────────────────────────────────────────────┘
```

Disposition of a no-tool-call assistant turn, across regimes:

| Turn state | Phase 03 §5 | Current (04-04.9) | This phase, `"terminal_tool"` | This phase, `"text"` |
| --- | --- | --- | --- | --- |
| Steers pending | continue | continue | continue | continue |
| Running background sessions | n/a | park (auto-wait) | park | park |
| Settled-but-undelivered session only | n/a | continue (next drain delivers) | continue | continue (delivery before any exit) |
| None of the above | **finish `completed`** | continue → spin to `maxTurns`, reminder rule nudges | continue (unchanged) | **finish `completed`, `submission` = text (NEW)** |

Termination summary:

| Trigger | Mode | Outcome |
| --- | --- | --- |
| Terminal tool result (`is_terminal`) | `terminal_tool` | `completed` with structured `submission` (unchanged) |
| Gated bare-text turn (step 6) | `text` | `completed` with `submission = assistantText(final_message)` |
| `interrupt()` / parent signal | both | `cancelled` (unchanged) |
| `maxTurns` spent | both | `failed { kind: "max_turns" }` (unchanged) |
| `ProviderError` / internal | both | `failed` (unchanged) |

## 6. Profile Contract

`agent-profile-loader.ts`:

- `AgentProfile.terminal_tool` and `FrontmatterSchema.terminal_tool` become
  optional.
- New kind rule (same shape as the `workflow_context_script` rule): a
  `planner` or `worker` profile without `terminal_tool` fails loading with an
  error naming the path and kind. `main`, `advisor`, and `subagent` may omit.

`agent-profile-registry.ts`:

- `selectProfileDefinitions`: the wanted set is `allowed_tools` plus the
  terminal name only when present — a text-mode run exposes no `submit_*`
  spec at all.
- `validateToolSelection`: the terminal-name-in-`allowed_tools` rejection and
  the known-terminal-name check apply only when `terminal_tool` is present;
  `allowed_tools` validation is unchanged.

`runtime.ts` (`startRun`):

- `terminationMode: profile.terminal_tool === undefined ? "text" : "terminal_tool"`
  passed to `startAgentRun`,
- `terminalTool: profile.terminal_tool ?? null` passed to
  `NotificationTriggerEngine`.

A text-mode subagent profile, for reference:

```yaml
---
name: researcher
description: read-only research subagent
llm_client_id: codex_coding_plan
max_turns: 8
agent_kind: subagent
allowed_tools:
  - read_agent_run_transcript
---
You are a research subagent. Reply with your findings as plain text;
your final text answer ends the run.
```

## 7. Gate Parity with the Submission Guard

The user-visible rule: text submits under exactly the conditions a terminal
submission is allowed.

| Fact | Terminal submission (`submit_*`) | Text exit (this phase) |
| --- | --- | --- |
| Open sessions (running + settled-undelivered) | `no-open-background-sessions.cjs` denies the call; the model gets an `is_error` result, replies bare text, and the loop parks (running) or drains (undelivered) | step-6 gate `openBackgroundSessionCount() === 0`; otherwise the same park / drain-through paths run |
| Pending steers | submission executes; steers accepted mid-batch die with the run | finish blocked; the steer extends the run (decision 4) |
| Abort racing the final message | submission outranks late redirection | same: the synchronous step-6 commit wins (decision 5) |

The two counts are deliberately different reads on one supervisor:
`backgroundSessionCount()` (running only) keeps gating the park, because a
settled-but-undelivered session must not park — its settlement notification
is already published and the very next drain delivers it.
`openBackgroundSessionCount()` (running + undelivered) gates the exit,
because finishing with an undelivered settlement would drop a notification
the model was owed — the exact race the supervisor's doc comment warns
about.

## 8. Notification Policy

- `TriggerPayload.terminal_tool: string | null` and
  `NotificationTriggerEngineDeps.terminalTool: string | null`. The payload
  doc gains: "`null` when the profile terminates on text".
- The finishing text turn announces nothing (decision 7), so no
  `TurnCompleted` script runs for it. Text turns that continue (steers,
  sessions) and all tool turns announce as today, with `terminal_tool: null`
  in the payload.
- Baseline scripts (repo-root `.eos-agents/notification-rules/`, operator
  config, not `eos-agent-core/` source):
  - `remind-terminal-submission.cjs`: first guard becomes
    `p.terminal_tool === null → exit silently`.
  - `budget-reminder.cjs`: the notification tail becomes
    `p.terminal_tool === null ? "Wrap up and finish by replying with your final answer as plain text." : "Wrap up and submit via " + p.terminal_tool + "."`.
  - `idle-wake.cjs`: unchanged (never read `terminal_tool`).
- `hooks.json` is unchanged: the `no-open-background-sessions` hook is
  matcher-scoped to the five `submit_*` names, and a text-mode run selects
  none of them, so the hook never fires for it.

## 9. Outcome and Consumers

- `AgentRunStatus` completed arm: shape unchanged; docs updated —
  `final_message` is "the assistant message that carried the terminal tool
  call, or the final text reply in text mode"; `submission` is "the terminal
  tool result's structured content, or `assistantText(final_message)` on a
  text exit".
- `run_subagent` (`mapSubagentOutcome`/`submissionSummary`): a string
  submission becomes the settlement summary verbatim, so the parent's
  `session_settled` notification carries the child's text unquoted
  (decision 10).
- `ask_advisor` already reads `outcome.submission ?? fallback`; a text-mode
  advisor profile works with no change.
- The workflow launch port is unaffected: `planner`/`worker` cannot be
  text-mode (decision 9), so `launchSettlement` submissions stay structured.

## 10. Rejected (decisions, no seam kept)

- A text-mode reminder/nudge seam in the engine: the run exits instead of
  spinning, so there is nothing to nudge.
- `stop_reason`-based exit: the decision stays presence-of-`tool_use`
  (Phase 03 §5 parity); `stop_reason` remains a recorded fact.
- Kind-keyed termination behavior: the profile config decides (decision 1).
- Salvaging reasoning text into `submission`: text blocks only.
- A dormant `terminationMode` field on profiles ("text mode with a terminal
  tool also wired"): presence/absence expresses the whole space this phase
  needs.

## 11. Workspace Changes

- `packages/engine/src/index.ts`, `src/agent-loop.ts`,
  `src/agent-runtime-handle.ts` (docs), `tests/agent-loop.test.ts`.
- `packages/agent-runtime/src/agent-profile-loader.ts`,
  `src/agent-profile-registry.ts`, `src/runtime.ts`,
  `tests/agent-profile.test.ts`, `tests/runtime.test.ts`,
  `tests/support.ts`.
- `packages/notification/src/triggers.ts`, `src/trigger-runner.ts`,
  `tests/trigger-runner.test.ts`.
- `packages/tool/src/tools/agent/run-subagent.ts`.
- `packages/agent-runtime/e2e/support/fixtures.ts`,
  `e2e/text-termination.e2e.ts` (new).
- Repo root: `.eos-agents/notification-rules/remind-terminal-submission.cjs`,
  `.eos-agents/notification-rules/budget-reminder.cjs`.
- `docs/plans/agent-core-rust-to-typescript-migration/index.md` (new row).
- No new third-party dependencies; no `@eos/contracts` changes; no
  `agent-core/` edits.

## 12. Migration Steps

1. Widen the notification contract (`triggers.ts`, `trigger-runner.ts`)
   -> verify: U15 plus `pnpm run typecheck`.
2. Engine: `terminationMode` option, the step-6 exit, the observer skip,
   doc updates -> verify: U1-U9; the pre-existing loop suite green
   unmodified.
3. Runtime: loader optionality + kind rule, registry conditionals, `startRun`
   derivation/wiring; `ProfileSpec.terminal: string | null` in test support
   -> verify: U10-U13.
4. `run_subagent` string passthrough -> verify: U14.
5. Baseline scripts' `null` branches -> verify: piped-payload smoke
   (`node remind-terminal-submission.cjs` with `terminal_tool: null` prints
   nothing; `budget-reminder.cjs` prints the text-mode wording), then T1/T4
   live.
6. E2e: null-aware fixtures + `text-termination.e2e.ts` -> verify: T1-T4
   live green on this machine; clean-skip without credentials.
7. Update the migration `index.md` row -> verify: tracker discipline fields
   present.

## 13. Verification

### Unit test checklist

All in-process, no network, green under `pnpm run check` from
`eos-agent-core/`. Engine rows use the existing harness
(`MockLlmClient`, `scriptedExecutor`, `backgroundSessionHandle`,
`recordingObserver`, `startMockRun`).

| # | Suite | Case (behavior sentence) | Asserts |
| --- | --- | --- | --- |
| U1 | `engine/tests/agent-loop.test.ts` | text mode completes on a bare-text turn | one provider call; `completed`; `submission === assistantText(final_message)`; `final_message` intact (reasoning included); `stop_reason` surfaced; `run_finished` last; `outcome.llm` provider-valid |
| U2 | engine | text mode still round-trips tool calls before the text exit | turn-2 request carries the tool-result message; `completed` after turn 2 with turn-2 text as `submission` |
| U3 | engine | a steer landing during the final text turn extends the run | steered message in the next request; run completes on a later text turn (Phase 03 §14 case 9 parity) |
| U4 | engine | a running background session parks the text turn instead of finishing | no finish while running; settlement publish wakes the park; the `session_settled` notification precedes the final text in `llm`; `completed` after |
| U5 | engine | a settled-but-undelivered session blocks the text exit until drained | session settles between turns (open > 0, running == 0); first text turn does not finish; next drain delivers; following text turn completes; the settlement is in `llm` at finish |
| U6 | engine | the finishing text turn never announces turnCompleted | `recordingObserver` sees no call for the finishing turn; sees calls for extended text turns (steers pending / sessions open) and tool turns |
| U7 | engine | terminationMode defaults to terminal_tool | with the option omitted, bare text continues and only an `is_terminal` result finishes (regression pin; existing suite untouched) |
| U8 | engine | maxTurns still backstops an endlessly extended text run | steers keep arriving on each text turn -> `failed { kind: "max_turns" }` |
| U9 | engine | an empty assistant turn terminates a text run with an empty submission | reasoning-only final message -> `completed`, `submission === ""` |
| U10 | `agent-runtime/tests/agent-profile.test.ts` | terminal_tool may be omitted for main, advisor, and subagent kinds | profiles load; `terminal_tool === undefined` |
| U11 | agent-runtime profile suite | planner and worker profiles must keep a terminal tool | load error names the path and kind |
| U12 | agent-runtime profile suite | a no-terminal profile selects no submission definition | `selectProfileDefinitions` returns no `submit_*`; `allowed_tools` validation and the present-terminal rules unchanged |
| U13 | `agent-runtime/tests/runtime.test.ts` | a no-terminal run completes by text over the mock client | request tool specs contain no `submit_*`; `completed`; `outcome.submission` equals the scripted text; registry finishes; `result.jsonl` rollup records `completed` |
| U14 | agent-runtime runtime suite | a parent reads a text child's settlement summary verbatim | `run_subagent` child (no-terminal profile) text-completes; the parent's next request carries `session_settled` whose `summary` is the child's text, unquoted |
| U15 | `notification/tests/trigger-runner.test.ts` | the trigger payload carries terminal_tool null for a text-mode run | stubbed `runCommand` captures the payload; `terminal_tool === null`; survives JSON serialization as `null` |

### E2e test checklist

New shard `packages/agent-runtime/e2e/text-termination.e2e.ts` under
`agent-runtime`, following the 04.6 harness (codex credential probe via
`loadConfiguredCodexRuntime`, clean-skip without credentials, repo baseline
`notification_rules.json` and `hooks.json`, temp profiles via
`runtimeFixture`).

| # | Case | Asserts |
| --- | --- | --- |
| T1 | a no-terminal subagent completes by text, untouched by reminder rules | profile (kind `subagent`, allowed `[lookup_codeword]`, no `terminal_tool`) told to look up the codeword and reply with only it: `completed`; `submission` contains `CODEWORD`; `llm` has no `submit_*` `tool_use`, no `"reminder"` notification; `unansweredToolUses` empty; transcript `result.jsonl` records `completed` |
| T2 | the parent receives the child's text through session settlement | main profile (terminal `submit_main_outcome`, allowed `[run_subagent]`) spawns the T1 child and submits a summary echoing the settlement: child `completed` with text submission; parent `llm` has a `session_settled` containing the codeword; parent `completed` |
| T3 | a text turn while a child runs parks, then finishes after settlement | no-terminal middle agent (allowed `[run_subagent]`) spawns the HOLDER child (terminal mode, over `gateTool`), then is instructed to reply `WAITING` with no tool call: park observed (`parkedOnBareText` probe; run not finished); `release()` -> holder settles; the settlement precedes the final text in the middle agent's `llm`; middle `completed` by text |
| T4 | budget reminders speak text-mode wording at the ladder | no-terminal profile with `max_turns: 4`; the 50% rule fires at turn 2 with the `null`-branch wording (no literal "null" interpolation) and the reminder is drained into `llm`; the run still completes by text |

### Commands

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS/eos-agent-core
pnpm run check                       # typecheck + lint + unit (U1-U15, no network)
pnpm exec vitest run --config vitest.e2e.config.ts \
  packages/agent-runtime/e2e/text-termination.e2e.ts   # T1-T4, live
```

- Rust boundary hygiene: `git diff --stat -- agent-core` stays empty.
- Docs hygiene: `git diff --check -- docs/plans/agent-core-rust-to-typescript-migration eos-agent-core .eos-agents`.

## 14. Acceptance Criteria

Phase 04.10 is accepted when:

- a profile without `terminal_tool` (kinds `main`/`advisor`/`subagent`)
  starts a run that completes on the §5 step-6 gate with
  `submission = assistantText(final_message)`; `planner`/`worker` omission
  fails at startup,
- a profile **with** `terminal_tool` behaves byte-for-byte as before this
  phase: the pre-existing engine, runtime, notification, and e2e suites pass
  without modification (test-support helpers excepted),
- the text exit is blocked by exactly the session set the
  `no-open-background-sessions` hook denies on, by pending steers, and by
  nothing else; running sessions still park; undelivered settlements are
  delivered before any exit,
- the finishing text turn fires no `TurnCompleted` scripts and publishes no
  reminder; trigger payloads for text-mode runs carry `terminal_tool: null`
  and both baseline scripts handle it,
- a `run_subagent` parent reads a text child's final text verbatim from the
  settlement summary,
- the §13 unit checklist is green under `pnpm run check` (no network) and
  the §13 e2e checklist is green live (clean-skip without credentials),
- `agent-core/` is byte-for-byte unchanged, and the migration `index.md`
  lists this phase with status and verification.

## 15. Progress Tracker

| Step | Status | Required proof |
| --- | --- | --- |
| Notification contract widening | Done | U15 green; `pnpm run typecheck` green |
| Engine mode + text exit + observer skip | Done | U1-U9 green; pre-existing loop suite green unmodified (54 engine tests) |
| Profile contract + runtime wiring | Done | U10-U13 green |
| `run_subagent` summary passthrough | Done | U14 green |
| Baseline rule scripts | Done | piped-payload smoke (`null` → remind silent, budget speaks text-mode wording); T1/T4 live green |
| E2e shard | Done | T1-T4 green live on this machine (4/4 in 34.6s); clean-skip without credentials (4 skipped) |
| Index updated | Done | Phase 04.10 row in `index.md` marked Completed with verification |
