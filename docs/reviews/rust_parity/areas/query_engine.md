# Rust parity review — Query engine main loop (terminal-forced exit)

Area key: `query_engine` · Domain: `agent-core`

**Headline:** The four core invariants (terminal-forced exit, text/non-terminal
does NOT end the loop, exit conditioned on a successful terminal stamp, budget +
max-iteration integration) are **correctly ported**. The hard-ceiling arithmetic
`ceil(1.5 × tool_call_limit)`, the terminal stamp, and the batch-exclusivity
rejection all match bilaterally. The real gaps are **(1) background-supervisor
lifecycle integration is entirely absent from the loop** (creation, per-turn
completion drain, parent-exit termination on TOOL_STOP, cancel-all on
TERMINAL_NOT_SUBMITTED — the Rust team's own plan §8.7 specifies these and the
loop does none of them) and **(2) provider-message sanitization
(`build_provider_messages` / `sanitize_tool_sequence`) is missing** — Rust sends
the raw durable transcript to the provider, risking Anthropic-API 400s on
malformed tool-use/result pairs. Two candidate bugs (double-count, top-vs-bottom
ceiling check) were **considered and rejected** — both are behaviorally
equivalent.

---

## Ground truth

Authoritative Python sources:
- `backend/src/engine/query/loop.py` — the loop itself (`_run_query_loop`,
  `terminal_submission_failed`, `_prepare_query_loop_runtime`).
- `backend/src/engine/query/context.py` — `QueryContext`, `QueryExitReason`
  (`TOOL_STOP`, `TERMINAL_NOT_SUBMITTED`).
- `backend/src/engine/query/request.py` — `build_query_run_request` →
  `build_provider_messages`.
- `backend/src/engine/query/provider_history.py` — `build_provider_messages`,
  `sanitize_tool_sequence`, `_drop_unmatched_tool_blocks_in_place`.
- `backend/src/engine/tool_call/dispatch.py` — `dispatch_assistant_tools`,
  `_validate_tool_batch`, `_first_terminal_tool_result`, foreground dispatch.
- `backend/src/engine/tool_call/streaming.py` — `StreamingToolExecutor`
  (mid-stream execution), `defer_background_dispatch`.
- `backend/src/tools/_framework/execution/tool_call.py:197-198` — the terminal
  stamp (`if tool.is_terminal_tool and not final.is_error: replace(is_terminal=True)`).
- `backend/src/tools/_framework/execution/tool_call.py:34-41,80-81` —
  `_count_tool_dispatch` / `consume_budget`.
- `backend/src/notification/rules/must_submit_terminal_tool.py`,
  `terminal_tool_call_count_reminder.py` — reminder bodies + 75/100/125% tiers.

Curated corroboration:
- `docs/architecture/agent_loops/main-loop.html`
  - §"Termination" (lines 164-207): "keep looping until a terminal tool is
    submitted"; the only failure is reaching `ceil(1.5 × tool_call_limit)` tool
    calls; on `TERMINAL_NOT_SUBMITTED` "Background tasks are cancelled and a
    synthetic `ToolExecutionCompletedEvent(is_error=True)` is emitted."
  - §"Streaming and Deferral" (101-110): when `terminal_tools` is non-empty
    "every streamed tool is deferred until the final assistant message is
    available."
  - §"Tool Dispatch and Terminal Stop" (112-155): terminal tool must be called
    alone; "first `is_terminal` block becomes the run terminal result and stops
    the query loop."

Key constants (ground truth):
- Hard ceiling = `math.ceil(1.5 * tool_call_limit)` (loop.py:47,56).
- Failure gate uses **`tool_calls_used + text_only_no_terminal_turns >= ceiling`**
  (loop.py:44-47) — `>=`, not `>`.
- Budget tiers: `("75%",3,4) ("100%",1,1) ("125%",5,4)`; trigger
  `tool_calls_used >= ceil(limit*num/den)` (terminal_tool_call_count_reminder.py:14-35).

---

## Rust mapping

- `agent-core/crates/eos-engine/src/query/loop_.rs` — `run_query` (the loop),
  `terminal_submission_failed`, `terminal_not_submitted_event`,
  `tool_uses_from_message`.
- `agent-core/crates/eos-engine/src/query/context.rs` — `QueryContext`,
  `QueryExitReason::{ToolStop, TerminalNotSubmitted}`, `EventSource` trait.
- `agent-core/crates/eos-engine/src/query/request.rs` — `build_query_run_request`.
- `agent-core/crates/eos-engine/src/query/provider_source.rs` —
  `ProviderEventSource` (production event source).
- `agent-core/crates/eos-engine/src/tool_call/dispatch.rs` —
  `dispatch_assistant_tools`, `first_terminal_result`, foreground dispatch.
- `agent-core/crates/eos-engine/src/tool_call/streaming.rs` —
  `StreamingToolExecutor` (deferral tracker only — **not** an executor),
  `should_defer_tool`.
- `agent-core/crates/eos-tools/src/execution.rs:104-115` — `stamp_terminal`
  (terminal stamp).
- `agent-core/crates/eos-tools/src/dispatch.rs:63-110` — `reject_terminal_batch`,
  `lifecycle_batch_decision`.
- `agent-core/crates/eos-engine/src/notifications.rs` — `NotificationRule`,
  `dispatch_rules`, budget tiers, reminder bodies.

No `// PORT` comments exist in `eos-engine/src/query/*.rs` or `tool_call/*.rs`
(grep returned nothing); mapping was done structurally + via
`docs/plans/.../impl-eos-engine.md`.

---

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | Loop drives provider turns + tool exec; ends ONLY on a terminal tool, not on text/non-tool output | **match** | none | loop.py:280-310 (`if final_message.tool_uses: dispatch...; if context.terminal_result is not None: TOOL_STOP; break`) | loop_.rs:177-206 (`if tool_uses.is_empty() {...continue}` … `if outcome.terminal_result ... is_terminal { ToolStop; break }`) | Core semantics ported faithfully. |
| 2 | Text-only / non-terminal tool call does NOT end loop; loop continues / re-prompts | **match** | none | loop.py:311-312,328 (`if not tool_uses: text_only += 1` then loop) | loop_.rs:177-185 (`continue`); 199-206 (no terminal ⇒ fall through to top) | Non-terminal tool results appended, loop re-iterates. |
| 3 | Exit conditioned on a **successful terminal-tool stamp** | **match** | none | tool_call.py:197-198 stamp; dispatch.py:165-177 `_first_terminal_tool_result` (only `is_terminal` blocks); loop.py:298-304 | execution.rs:106-115 `stamp_terminal` (`is_terminal && !is_error`); dispatch.rs:82-95 `first_terminal_result` (filters `tool.is_terminal` AND `result.is_terminal`); loop_.rs:199-206 | Errored terminal does NOT stamp/stop — proven by dispatch.rs test `terminal_tool_error_does_not_project_terminal_result`. |
| 4 | Tool-call budget + max-iteration (hard ceiling) integrated into loop | **match** | none | loop.py:41-47,313-327; `_count_tool_dispatch` tool_call.py:34-41 | loop_.rs:24-29 (`(limit*3+1)/2`), 103-106 + 180-183 (ceiling check), 140-142 + 169-174 (counting) | Ceiling `(3*limit+1)/2 == ceil(1.5*limit)` for all L. |
| 4a | Ceiling literal = `ceil(1.5 × tool_call_limit)`, comparison `>=` | **match** | none | loop.py:44-47 (`>= math.ceil(1.5 * tool_call_limit)`) | loop_.rs:25-28 (`>= ceiling`, `ceiling = limit.saturating_mul(3).saturating_add(1) / 2`) | Integer-equivalent to `ceil(1.5L)`. |
| 4b | Failure gate operand = `tool_calls_used + text_only_no_terminal_turns` | **match** | none | loop.py:45-46 | loop_.rs:26-28 (`tool_calls_used.saturating_add(text_only_no_terminal_turns)`) | Same operand sum. |
| 4c | Tool calls counted once per unique tool-use id (`consume_budget` semantics) | **match** | none | loop.py:199 (`_count_tool_dispatch` on each ToolUseDelta) + tool_call.py:80-81 (`consume_budget = id not in streamed`) | loop_.rs:140-142 (insert-gated +1 on delta) + 169-174 (+1 for non-streamed final tool_uses); dispatch.rs has **no** `tool_calls_used` mutation | No double count — verified dispatch.rs/streaming.rs only touch the field in test ctors. |
| 5 | Budget-tier reminders 75/100/125%, fire-once, with budget body | **match** | low | terminal_tool_call_count_reminder.py:14-54 | notifications.rs:129-153 tiers; 57-59 fire_once; 80-85 trigger | Threshold `used*den >= limit*num` == `used >= ceil(limit*num/den)`. |
| 6 | Repeating `terminal_call_reminder` fires every turn after first assistant msg while no terminal | **match** | low | must_submit_terminal_tool.py:17-21 (`any(role=='assistant')`), fire_once=False | notifications.rs:71-74 (`!terminal_tools.is_empty() && any(Assistant)`); not fire_once (57-59) | Body parity verified in notifications.rs test. |
| 7 | On TOOL_STOP: terminate background for parent exit | **missing** | high | loop.py:305-307 (`await background_tasks.terminate_for_parent_exit()`) | ABSENT — loop_.rs:204-206 only sets `exit_reason=ToolStop; break`; no supervisor call | Plan §8.7 (impl-eos-engine.md:459-460) REQUIRES it; loop omits it. |
| 8 | On TERMINAL_NOT_SUBMITTED: cancel all background tasks | **missing** | high | loop.py:313-315 (`await background_tasks.cancel_all()`) | ABSENT — loop_.rs:84-96, 103-106, 180-183 emit the failure event only; no cancel | Doc main-loop.html:186-188 + plan:461 require "Background tasks are cancelled." |
| 9 | Background supervisor created + per-turn completion-notification drain + `finally` cancel | **missing** | high | loop.py:113-116, 162-172, 238-241, 329-331 | ABSENT — loop_.rs never constructs `BackgroundTaskSupervisor` nor drains/cancels | `enable_background_tasks` is read into ctx but never acted on in the loop. |
| 10 | Provider request uses sanitized history copy (`build_provider_messages`) | **missing** | high | request.py:29 → provider_history.py:20-39 (`sanitize_tool_sequence` + drop unmatched tool pairs) | ABSENT — request.rs:26 `messages.to_vec()` (raw); no `provider_history.rs` | Risks Anthropic 400 on malformed tool-use/result pairs; not in plan as ported/dropped. |
| 11 | Mid-stream foreground tool execution + progress events (when no terminal tools) | **partial** | medium | streaming.py:95-130, 195-238 (`add_tool`/`_execute_tool` start tools mid-stream) | streaming.rs:1-36 is a name-only deferral tracker; loop_.rs never executes mid-stream — all tools go through `dispatch_assistant_tools` post-message | Plan §8.1 (impl:432-439): intentional in production (terminal tools force defer-all). Lost: mid-stream start + progress events for terminal-less agents. |
| 12 | Provider stream without final assistant message ⇒ RuntimeError, cancel in-flight streamed tools | **partial** | medium | loop.py:214-222 (raise + `executor.cancel_all()`) | loop_.rs:157-162 (returns `EngineError::Internal`) — no in-flight cancel because no mid-stream execution exists | Error path present; cancel is moot given gap #11. |
| 13 | `TERMINAL_NOT_SUBMITTED` synthetic event body content (used/limit/ceiling) | **divergent** | low | loop.py:50-57 (`tool_calls_used=…, text_only_no_terminal_turns=…, tool_call_limit=…, hard_ceiling=…`) | loop_.rs:31-37 (only "used N tool calls/text-only turns without submitting…") | Both emit `ToolExecutionCompleted(tool_name="", is_error=True)`; Rust message drops the structured counts. |
| 14 | Notifications drained into transcript at TOP of turn before building request | **match** | none | loop.py:238-251 (drain → dispatch_rules → append) | loop_.rs:108-117 (`dispatch_rules` → append before `build_query_run_request`) | Order preserved; Rust lacks the background-completion drain (part of #9). |
| 15 | Identity stamping of agent_name/agent_run_id on emitted events | **match** | low | loop.py:343-364 `_stamp` (only fills empty fields) | events.rs `stamp_identity` (loop_.rs:137,189) | Behaviorally equivalent fill-if-empty. |

---

## Disparities (detailed)

### D1 — Background-supervisor lifecycle entirely absent from the loop (HIGH)
**Evidence (Python):** `loop.py:113-116` creates `BackgroundTaskSupervisor` when
`enable_background_tasks`; `:238-241` drains background-completion notifications
each turn; `:305-307` calls `terminate_for_parent_exit()` on TOOL_STOP and emits
its notifications; `:313-315` calls `cancel_all()` on the hard-ceiling exit;
`:329-331` is a `finally` that cancels any pending background work.
**Evidence (Rust):** `loop_.rs` (whole file) never constructs a supervisor,
never drains, never cancels. `enable_background_tasks` is a `QueryContext` field
(context.rs:69) read by the factory (factory.rs:138) but the loop ignores it.
`dispatch_assistant_tools` (dispatch.rs:205-303) has no background branch at all —
every call becomes a foreground call.
**Why it matters:** This is a *correctness/leak* gap, not just missing telemetry.
On a successful terminal submission, in-flight subagents/command-sessions are
neither awaited nor cancelled; on the hard-ceiling failure the doc
(main-loop.html:186-188) and the Rust team's own plan
(impl-eos-engine.md:461 "On ceiling crossed → `cancel_all`") explicitly require
cancellation. The Rust loop diverges from its **own** specified behavior.
**Suggested fix:** Wire `BackgroundTaskSupervisor` (already exported from
lib.rs:20) into `run_query`: create it under `enable_background_tasks`, drain
completion notifications at the top of each turn, route engine-background tools
through it in dispatch, call `terminate_for_parent_exit` before the `ToolStop`
break, `cancel_all` before the `TerminalNotSubmitted` break, and a final cancel
when the stream is dropped. If this is a deliberate Phase-6 deferral, the loop
should at minimum carry a tracked TODO; today there is no marker.

### D2 — Provider-message sanitization missing (HIGH)
**Evidence (Python):** `request.py:29` `provider_messages =
build_provider_messages(messages)`; `provider_history.py:20-39` runs
`sanitize_tool_sequence` (deep-copies, drops unmatched tool-use/result blocks via
`_drop_unmatched_tool_blocks_in_place`, removes empty messages) over a
`reduce_background_task_history` pass — never mutating the durable transcript.
**Evidence (Rust):** `request.rs:25-30` builds `LlmRequest` from
`messages.to_vec()` — the raw durable transcript. `loop_.rs:120-128` records
`record_llm_request` with raw `messages`. Grep confirms no `sanitize`,
`build_provider_messages`, or `drop_unmatched` anywhere in `eos-engine` or
`eos-llm-client`.
**Why it matters:** The Anthropic Messages API rejects a request where a
`tool_use` block has no paired `tool_result` (or vice-versa). Python sanitizes
exactly this case before every request. Rust sending the raw transcript can
produce 400s after, e.g., a batch-rejected terminal turn leaves orphaned blocks,
or any future history compaction. The migration plan only documents the
*background-history* half (`reduce_background_task_history`,
impl-eos-engine.md:80 "Dropped: identity passthrough"); the tool-pair
sanitization half is neither ported nor explicitly dropped — an undocumented gap.
**Suggested fix:** Port `sanitize_tool_sequence` into a `provider_history.rs`
under `eos-engine/src/query/` and route `build_query_run_request` + the
`record_llm_request` call through it. The background-history reduction can stay
dropped per plan, but the unmatched-tool-block drop is load-bearing for API
correctness.

### D3 — Mid-stream foreground execution + progress events not implemented (MEDIUM)
**Evidence (Python):** `streaming.py:95-130` `add_tool` starts non-deferred tools
the moment a `ToolUseDeltaEvent` arrives; `:138-152` `get_progress` streams
progress lines; `:200-238` runs the tool body concurrently with the stream.
**Evidence (Rust):** `streaming.rs:8-30` `StreamingToolExecutor` only holds a
`Vec<ToolName> deferred` and a `defer`/`deferred` accessor — no execution, no
progress. `loop_.rs:136-155` consumes the stream but only *counts* tool deltas;
all execution happens later in `dispatch_assistant_tools`.
**Why it matters / classification:** Per plan §8.1 (impl-eos-engine.md:432-439)
and the architecture doc (main-loop.html:104-105), production agents always
declare ≥1 terminal tool, so Python ALSO defers all mid-stream execution in
production. Therefore this gap does **not** affect termination semantics. It is
an **intentional migration simplification**, but it loses (a) mid-stream tool
*start* and *progress* streaming for any terminal-less agent and (b) the MiniMax
"tool_use + complete in one frame" race handling (`get_remaining` await,
streaming.py:154-168). Frame as medium/low: lost streaming UX + a race-handling
nuance, not a correctness bug for production termination.
**Suggested fix:** Acceptable to defer; document it as a known divergence. If
terminal-less agents are ever supported, port the mid-stream executor.

### D4 — `TERMINAL_NOT_SUBMITTED` event body drops structured counts (LOW)
**Evidence:** Python (loop.py:50-57) emits
`"...tool_calls_used=N, text_only_no_terminal_turns=M, tool_call_limit=L,
hard_ceiling=C."`; Rust (loop_.rs:31-37) emits only
`"The agent used N tool calls/text-only turns without submitting a terminal
tool..."`. Both correctly emit `ToolExecutionCompleted{tool_name:"",
is_error:true}` (loop.py:316-323 vs loop_.rs:84-96) and set the same exit reason.
**Why it matters:** Cosmetic / observability only; the machine-readable shape
(empty tool name + is_error) matches, so downstream detection is unaffected. The
Rust test only asserts `contains("without submitting a terminal tool")`.
**Suggested fix:** Align the message text if transcript/debug parity is desired;
low priority.

---

## Considered and REJECTED (not bugs)

### R1 — Double-counting of tool calls — REJECTED
Concern: Rust counts `+1` on each `ToolUseDelta` (loop_.rs:140-142) AND `+1` for
non-streamed final `tool_uses` (loop_.rs:169-174); could a tool be counted
twice? No. The delta path is gated by `streamed_tool_use_ids.insert(...)`
(returns true only on first insert), and the final-message path increments only
for ids **not** in `streamed_tool_use_ids`. This is exactly Python's
`consume_budget = tool_use_id not in streamed_tool_use_ids` (tool_call.py:80-81)
restructured. Verified: `dispatch.rs` and `execution.rs` never mutate
`ctx.tool_calls_used` (grep shows only test-ctor `tool_calls_used: 0`). Net count
per unique id = 1 on both sides. **MATCH (invariant 4c).**

### R2 — Top-of-loop vs bottom-of-loop ceiling check — REJECTED
Concern: Rust checks `terminal_submission_failed` at the **top** of the loop
(loop_.rs:103-106) plus a redundant in-turn check after a text-only turn
(loop_.rs:180-183); Python checks at the **bottom** (loop.py:313). Walked through
with limit=2 (ceiling=3): for tool-call turns Rust breaks at the top of the
*next* iteration but performs no provider work there (the check precedes
`build_query_run_request`), so the stream count, dispatched tools, and break
point are identical. For text-only turns the redundant loop_.rs:180 check fires
in-turn, matching Python's bottom check. Terminal wins in-turn on both sides. The
emitted reminder sequence is identical (terminal reminder on turn 2; 75%+100%+
terminal on turn 3; then fail). The only divergence is unreachable in production:
`limit=0` (ceiling=0) makes Rust fail before turn 1, and a pre-seeded count ≥
ceiling — both invalid (every agent declares a positive `tool_call_limit`).
**MATCH (structural divergence, low/none).**

---

## Extra findings

- **`task_id` typing divergence (informational):** Python `context.task_id: str`
  (context.py:49, default `""`); Rust `task_id: Option<TaskId>` (context.rs:61).
  Behaviorally equivalent (`""`/`None` both = "unknown"); the Rust
  `build_query_run_request` does not thread `task_id` into metadata the way
  Python's `_build_stream_executor` does (loop.py:144-145), but metadata threading
  happens in dispatch.rs:97-101 instead. No functional gap for termination.
- **`prompt_report_seq` default 0 when no recorder (request.rs:21-24):** Python
  always has a recorder via `recorder_for_context`; Rust returns seq 0 when
  `prompt_report` is `None`. Only affects prompt-report ordering, not the loop.
- **Plan self-divergence is the strongest signal for D1:** impl-eos-engine.md
  lines 459-466 spell out `terminate_for_parent_exit` on ToolStop and `cancel_all`
  on ceiling — the loop implements neither, so this is a gap against the Rust
  team's own acceptance criteria (AC-engine-07 claims status/parent-exit
  semantics are "proven," but the loop never calls them).
- **`reduce_background_task_history` correctly dropped** (plan:80, "identity
  passthrough") — not a finding; only the `sanitize_tool_sequence` half (D2) is a
  real gap.

---

## Open questions

1. Is the background-supervisor loop integration (D1) a tracked Phase-6 deferral?
   The plan §GC-engine-05 (impl:517) calls full runner ownership a "Phase-6
   runtime wiring residual," but it does **not** carve out the loop-level
   `terminate_for_parent_exit` / `cancel_all` / per-turn drain, which §8.7 lists
   as in-scope. Confirm intended phase.
2. Is provider-message sanitization (D2) intended to live in `eos-llm-client`
   (provider adapter) rather than `eos-engine`? It is absent from both today; if
   the Anthropic adapter is meant to reject/repair malformed pairs, that path was
   not found.
3. Will terminal-less agents ever be supported? If not, D3 (mid-stream execution)
   can stay permanently deferred; if yes, the streaming executor must be ported.
