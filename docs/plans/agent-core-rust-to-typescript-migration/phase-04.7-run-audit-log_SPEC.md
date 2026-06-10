# EOS Agent Core Rust to TypeScript Migration - Phase 04.7 Run Audit Log

Status: Proposed
Date: 2026-06-11
Owner: eos-agent-core
Depends on: Phase 04.5 (agent runtime), Phase 02.5 (provider composition)

## 1. Intent

Give every agent run an auditable on-disk record with token accounting at
both scopes:

| Requirement | Artifact |
| --- | --- |
| (a) token usage and cache hit per assistant turn | `turn_completed` lines in `events.jsonl`, one per completed turn, carrying that turn's `UsageSnapshot` and `cache_hit_rate` |
| (b) accumulated token usage and cache-hit percentage per agent run | one `result.jsonl` line per run, rolled up from `outcome.usage` |
| (c) per-run disk persistence as `events.jsonl`, `transcript.jsonl`, `result.jsonl` | `<dataDir>/runs/<run_id>/` owned by the runtime's recorder |

Everything needed already flows through existing seams:

- `assistant_message_complete` carries a per-turn `UsageSnapshot`
  (`input_tokens`, `output_tokens`, optional `cache_read_input_tokens`,
  `cache_creation_input_tokens`) and is forwarded unchanged into the
  engine's `AgentEvent` stream.
- The loop already sums per-turn usage (`addUsage`) onto
  `AgentRunOutcome.usage` ("summed across completed turns"), delivered in
  the terminal `run_finished` event.
- The runtime is already the event stream's single consumer (Phase 04.5
  decision 5) and already owns a per-run JSONL writer with an ordered
  append queue, latched failure, and the `outcome.finally` flush trigger.

What is missing: the transcript writer drops `usage` and `stop_reason`
on the floor, the two wire codecs disagree about what `input_tokens`
means relative to the cache fields, and `transcript.jsonl` is the run
directory's only file — no audit timeline, no result rollup. This phase
closes exactly those three gaps. No engine changes.

## 2. Design Decisions

1. **The recorder stays the runtime's single subscriber.** The audit
   fan-out happens inside one `RunLog` object fed by the existing
   `for await (const event of handle.events)` loop in `startRun`. No
   second stream consumer, no engine events added, no broadcaster pulled
   forward.

2. **One run directory, three files, one shared `seq`.**
   `<dataDir>/runs/<run_id>/{events,transcript,result}.jsonl`. A single
   run-global sequence counter stamps every line across all three files,
   so per-file `seq` is sparse but a merge of the files sorts into the
   exact write order. One append queue serializes all three files and
   keeps the single latched-failure/`flush()` contract of Phase 04.5 §6.

3. **`transcript.jsonl` keeps its name, path, and schema.** The line
   union (`TranscriptLine`), `runTranscriptPath`, the `transcript_path`
   value flowing into `AgentRunState` and every
   `HookPayload`/`ToolCallMeta`, and `read_agent_run_transcript` are
   all untouched. "The transcript" remains the conversation-shaping
   artifact; `events.jsonl` and `result.jsonl` are audit artifacts
   beside it, never consumed by hooks or tools.

4. **Per-turn usage is derived in the recorder, not a new engine
   event.** `RunLog` tracks the current turn from `turn_started` and, on
   `assistant_message_complete`, writes a `turn_completed` events line
   with the turn number, `stop_reason`, the turn's `UsageSnapshot`, and
   the computed `cache_hit_rate`. A `turn_started` with no matching
   `turn_completed` is the audit signature of a turn that died
   (abort mid-stream or provider error) — exactly the engine's existing
   semantics, recorded for free.

5. **`events.jsonl` is the lifecycle timeline, not the byte stream:
   deltas are excluded.** Recording `assistant_text_delta` /
   `reasoning_delta` / `tool_use_delta` would write one enveloped line
   per SSE chunk — hundreds of lines per turn that are mostly `seq`/`ts`
   wrapper — to persist text `transcript.jsonl` already stores
   assembled, and token accounting never derives from delta text (usage
   is the provider-reported snapshot on `assistant_message_complete`).
   Live tailing of an in-flight turn is the broadcaster/server phase's
   concern, not the audit file's. Tool lines record compact facts
   (`name`, ids, flags, `duration_ms`) while the full output stays in
   the `transcript.jsonl` `tool_result` line. No content is stored
   twice. The one cost of exclusion: partial output of a turn that dies
   mid-stream stays memory-only (`outcome.displayed`) — recorded in
   decision 9 as a deferred seam, not a reason to log every chunk.

6. **One `UsageSnapshot` semantic, therefore one cache-hit formula.**
   Anthropic reports `input_tokens` net of cache reads/creations; the
   OpenAI Responses wire currently passes through `input_tokens` as the
   total *including* `cached_tokens`. This phase normalizes at the
   provider edge (where SDK shapes belong): the openai-responses codec
   subtracts `cached_tokens` from `input_tokens`. The contract becomes
   "`input_tokens` counts uncached prompt tokens; total prompt =
   `input + cache_read + cache_creation`", documented on `UsageSnapshot`,
   and one helper computes
   `cache_hit_rate = cache_read / (input + cache_read + cache_creation)`
   (0 when the denominator is 0). Without this, every cache-hit number
   for codex runs would be wrong.

7. **`result.jsonl` is written exactly once, from `run_finished`.**
   Accumulated usage comes straight off `outcome.usage` — the engine's
   accumulation is authoritative; the recorder adds only the run-scope
   `cache_hit_rate` and identity/timing facts. A run directory without
   `result.jsonl` is itself an audit signal: the process died or the run
   is still live.

8. **Flush and ordering semantics are unchanged.** The §4
   `outcome.finally` chain (drain tail → flush → `registry.finish`)
   stays the one authoritative flush trigger; `run_finished` is always
   the stream's last event, so the `result.jsonl` line is the last write
   in the queue. The per-run read barrier in `startRun` keeps awaiting
   the same `flush()`.

9. **Steer, notification, and died-turn salvage lines stay deferred.**
   Steer and notification entries still have no live event source
   (Phase 04.5 §6); the `transcript.jsonl` union keeps carrying them for
   recorders, and the broadcaster phase wires them. A salvage line for a
   died turn's partial text would need a new engine event (salvage
   currently lands only in `outcome.displayed`), so it is a named seam
   for the phase that first needs it.

## 3. Scope

In scope:

- `@eos/llm-client`: openai-responses usage normalization, `UsageSnapshot`
  doc contract, exported `cacheHitRate` helper, wire/contract test
  updates.
- `@eos/agent-runtime`: `transcript.ts` grows from `TranscriptWriter`
  into `RunLog` (three files, shared seq, run metadata line), `runtime.ts`
  wiring delta, two new exported line types.
- Tests across both packages; tracker index row.

Out of scope (named seams, unchanged):

- Process/structured logging (`pino`) and OpenTelemetry spans — separate
  observability phase; `packages/observability` stays a stub.
- SQLite persistence of runs/usage — Phase 05 introduces `@eos/db` for
  workflow state; run audit stays file-based this phase.
- Event broadcaster / server transport; steer + notification recording.
- Surfacing usage on `RunSummary`/`listRuns()` (callers already have
  `handle.outcome.usage`; transport surfaces arrive with the server
  phase).
- Run-directory eviction/retention.

## 4. Usage Semantics (`@eos/llm-client` owned change)

`UsageSnapshot` gains a written contract: `input_tokens` counts
**uncached** prompt tokens; the total prompt is
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`.

The anthropic-messages codec already conforms (provider-native
semantics). The openai-responses codec normalizes in `#complete`:

```ts
const cached = reported?.input_tokens_details?.cached_tokens;
const usage: UsageSnapshot = {
  input_tokens: Math.max(0, (reported?.input_tokens ?? 0) - (cached ?? 0)),
  output_tokens: reported?.output_tokens ?? 0,
};
if (typeof cached === "number") usage.cache_read_input_tokens = cached;
```

One exported helper next to the type, used by the recorder and available
to callers:

```ts
/** cache_read / total prompt tokens; 0 when no prompt tokens were reported. */
export function cacheHitRate(usage: UsageSnapshot): number {
  const read = usage.cache_read_input_tokens ?? 0;
  const denominator =
    usage.input_tokens + read + (usage.cache_creation_input_tokens ?? 0);
  return denominator > 0 ? read / denominator : 0;
}
```

`addUsage` (engine) needs no change: it sums fields, and uniform
semantics make the sums meaningful. Golden/contract expectations that
pinned the old OpenAI pass-through value are updated as part of this
phase, not suppressed.

## 5. Run Log (`agent-runtime/src/transcript.ts`)

The module keeps its path (ten test/e2e files import from it);
`TranscriptWriter` becomes `RunLog` because it now owns the whole run
directory:

```
engine loop ──emit──▶ handle.events ──(runtime's single consumer)──▶ RunLog
                                                                      │
                                       ┌──────────────────────────────┼──────────────────┐
                                       ▼                              ▼                  ▼
                              events.jsonl                  transcript.jsonl      result.jsonl
                              (audit timeline:                (conversation:      (one rollup line
                               run/turn/tool lifecycle,        user/assistant/     at run_finished:
                               per-turn usage + cache;         tool_result/...;    totals + hit rate)
                               no streaming deltas)            unchanged file)
```

Event-to-file mapping (one queue, shared `seq`):

| Source | `events.jsonl` | `transcript.jsonl` | `result.jsonl` |
| --- | --- | --- | --- |
| `RunLog` construction | `run_started` (identity metadata) | — | — |
| `appendUser` (runtime call) | — | `user` | — |
| `turn_started` | `turn_started` | — | — |
| `assistant_text_delta`, `reasoning_delta`, `tool_use_delta` | — | — | — |
| `assistant_message_complete` | `turn_completed` (turn, stop_reason, usage, cache_hit_rate) | `assistant` (full message) | — |
| `tool_execution_started` | `tool_started` | — | — |
| `tool_execution_completed` | `tool_completed` (flags, duration_ms) | `tool_result` (full result) | — |
| `run_finished` | `run_finished` (status only) | `run_finished` (status, reason, submission) | the rollup line |

### Line schemas

`transcript.jsonl`: `TranscriptLine`, unchanged from Phase 04.5 §6.

`events.jsonl` (every line `{ seq, ts } & ...`; snake_case, serialized):

```ts
export type EventLine = { seq: number; ts: string } & (
  | { type: "run_started"; run_id: AgentRunId; agent_name: string;
      agent_kind: AgentKind; parent?: AgentRunId; llm_client_id: string;
      model_id: string; reasoning_effort?: string; max_turns: number }
  | { type: "turn_started"; turn: number }
  | { type: "turn_completed"; turn: number; stop_reason?: string;
      usage: UsageSnapshot; cache_hit_rate: number }
  | { type: "tool_started"; turn: number; tool_use_id: ToolUseId; name: string }
  | { type: "tool_completed"; turn: number; tool_use_id: ToolUseId;
      name: string; is_error: boolean; is_terminal: boolean;
      duration_ms: number }
  | { type: "run_finished"; status: "completed" | "cancelled" | "failed" }
);
```

`result.jsonl` (exactly one line; identity repeated so the file stands
alone):

```ts
export type ResultLine = {
  seq: number; ts: string;
  run_id: AgentRunId; agent_name: string; agent_kind: AgentKind;
  parent?: AgentRunId; llm_client_id: string; model_id: string;
  status: "completed" | "cancelled" | "failed";
  interrupt_reason?: string;            // cancelled
  failure?: { kind: string; message: string }; // failed
  submission?: JsonValue;               // completed
  turns: number;
  usage: UsageSnapshot;                 // outcome.usage: completed turns
  cache_hit_rate: number;               // run scope, same formula
  started_at: string;                   // RunLog construction
  finished_at: string;                  // run_finished append
  duration_ms: number;
};
```

### Recorder shape

```ts
export interface RunLogMeta {
  run_id: AgentRunId; agent_name: string; agent_kind: AgentKind;
  parent?: AgentRunId; llm_client_id: string; model_id: string;
  reasoning_effort?: string; max_turns: number;
}

export class RunLog {
  constructor(dataDir: string, meta: RunLogMeta); // enqueues run_started (seq 0)
  append(event: AgentEvent): void;     // the table above
  appendUser(origin: "initial" | "steer", message: Message): void;
  flush(): Promise<void>;              // all three files; rethrows latched failure
  readonly transcriptPath: string;     // <dataDir>/runs/<run_id>/transcript.jsonl
}
```

Internals stay the Phase 04.5 machinery, widened by one dimension: one
ordered promise queue whose entries carry a target path, one `#seq`, a
`#turn` updated by `turn_started`, `#startedAt` captured at
construction, lazy `mkdir` of the run directory, and the existing
latched-failure semantics (`flush()` rethrows the first write error).
`runTranscriptPath(dataDir, runId)` and the byte-offset reader
(`readTranscriptFile`) are untouched.

### `runtime.ts` wiring delta

The §4 wiring order is preserved; only the recorder construction
changes:

```ts
const runLog = new RunLog(ctx.dataDir, {
  run_id: runId, agent_name: profile.name, agent_kind: profile.agent_kind,
  parent: context.parent, llm_client_id: profile.llm_client_id,
  model_id: llm.model_id, reasoning_effort: llm.reasoning_effort,
  max_turns: profile.max_turns,
});
const transcriptPath = runLog.transcriptPath; // flows to runState/hooks as today
```

The subscriber loop, transcript barrier, and `outcome.finally` flush
chain are textually identical with `transcriptWriter` replaced by
`runLog`.

## 6. Workspace Changes

| File | Change |
| --- | --- |
| `packages/llm-client/src/wires/openai-responses.ts` | normalize `input_tokens` to net-of-cache in `#complete` |
| `packages/llm-client/src/types.ts` | `UsageSnapshot` contract doc + `cacheHitRate` |
| `packages/llm-client/src/index.ts` | export `cacheHitRate` |
| `packages/agent-runtime/src/transcript.ts` | `TranscriptWriter` → `RunLog`; `EventLine`/`ResultLine` added; `runTranscriptPath` unchanged |
| `packages/agent-runtime/src/runtime.ts` | construct `RunLog` with `RunLogMeta`; rename local uses |
| `packages/agent-runtime/src/index.ts` | export `EventLine`, `ResultLine` types alongside `TranscriptLine`, `TranscriptRead` |
| `packages/agent-runtime/tests/transcript.test.ts` | becomes the `RunLog` suite (§7 cases 3–11) |
| `packages/llm-client/tests/...` | wire/contract usage expectations updated (§7 cases 1–2) |

Engine, tool, contracts, testkit, db, observability packages: no
changes. Rust `agent-core/` untouched.

## 7. Verification

Ladder: `pnpm run typecheck`, `pnpm run lint`, `pnpm run test` in
`eos-agent-core/`; `pnpm run check` before landing. The live e2e suite
(`vitest --config vitest.e2e.config.ts`) must stay green/clean-skip; a
live codex run is the one place real `cached_tokens` are observable, so
one expanded assertion (per-turn `cache_hit_rate` present and within
[0, 1]) may ride an existing shard rather than a new one.

| # | Case | Asserts |
| --- | --- | --- |
| 1 | OpenAI usage normalization | `#complete` maps `input_tokens = reported − cached`, `cache_read_input_tokens = cached`; golden fixture expectations updated; anthropic wire byte-identical behavior |
| 2 | `cacheHitRate` table | `it.each`: no cache fields → 0; read-only; read+creation+input mix; zero denominator → 0 |
| 3 | run_started first | line `seq` 0 in `events.jsonl` carries the full `RunLogMeta` identity |
| 4 | per-turn pairing | a scripted two-turn run yields two `turn_completed` lines whose `turn`, `usage`, and `cache_hit_rate` match the per-turn snapshots fed in |
| 5 | died turn | abort mid-stream: `turn_started` unmatched, no `turn_completed`, `run_finished` + result line `status: "cancelled"`, usage = completed turns only |
| 6 | deltas excluded | delta events produce no lines in any file |
| 7 | tool lines compact | `tool_started`/`tool_completed` carry flags + `duration_ms = tool_end_time − tool_start_time`; full output appears only in the `transcript.jsonl` `tool_result` line |
| 8 | transcript behavior preserved | Phase 04.5 §6 cases survive: kinds, ordering, `run_finished` payload, latched write failure resurfacing at `flush()`; `seq` values become sparse (shared counter) but stay strictly increasing |
| 9 | shared seq | merging the three files and sorting by `seq` is strictly increasing with no duplicates |
| 10 | result rollup | exactly one `result.jsonl` line; `usage` equals `outcome.usage`; `cache_hit_rate` matches the formula; `turns`, identity fields, and `duration_ms = finished_at − started_at` sanity |
| 11 | integration unchanged | runtime suite: hooks still read `transcript_path`; `read_agent_run_transcript` offset reads keep working; `listRuns` flush ordering intact |

## 8. Coexistence and Rollback

Additive within the TypeScript workspace; the behavioral deltas visible
to existing callers are sparse `seq` values on transcript lines (the
shared run-global counter) and normalized OpenAI `input_tokens` (smaller
by `cached_tokens`; the live e2e asserts only `> 0`). Rollback is
reverting the phase commit.
`git diff --stat -- agent-core` stays empty.

## 9. Acceptance Criteria

- Every run directory contains `events.jsonl`, `transcript.jsonl`, and
  (after a clean finish) `result.jsonl` with the §5 schemas (req. c).
- Each completed assistant turn produces one `turn_completed` line with
  that turn's `UsageSnapshot` and `cache_hit_rate` (req. a).
- Each finished run produces one `result.jsonl` line whose `usage` is
  the run-accumulated total and whose `cache_hit_rate` is the run-scope
  percentage (req. b).
- `UsageSnapshot` has one documented semantic across both wires;
  `cacheHitRate` is the one formula, exported from `@eos/llm-client`.
- §7 ladder green (`pnpm run check`); live e2e green or clean-skip;
  Rust tree untouched; tracker index row added.
