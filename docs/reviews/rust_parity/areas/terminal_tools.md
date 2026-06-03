# Rust parity audit — Terminal tools enforcement

Area: Terminal tools enforcement (called-alone, stamped terminating, dispatch/loop exit).
Domain: agent-core.

Verdict in one line: the **core terminal contract is faithfully ported** — exclusivity rejection, success-only stamping, first-terminal projection, and `TOOL_STOP` exit all match Python with byte-exact rejection messages and an identical hard-ceiling formula. The gaps are (1) a **divergent `terminal_not_submitted` failure message**, (2) **missing background-task drain/cancel on terminal exit**, (3) a **1-iteration timing difference** in when the hard ceiling fires relative to a non-terminal tool batch, (4) **dead streaming-deferral scaffolding** (`StreamingToolExecutor`/`should_defer_tool`) that the loop never calls, and (5) the engine loop **does not stamp `terminal_call_reminder`/budget notifications into the transcript after tool dispatch** the way Python flushes them.

## Ground truth

Python (authoritative):
- `backend/src/tools/_framework/execution/tool_call.py:197-200` — terminal stamping: `if tool.is_terminal_tool and not final.is_error: final = replace(final, is_terminal=True)`.
- `backend/src/engine/tool_call/dispatch.py:180-206` — `_validate_tool_batch`: `len(tool_calls) <= 1 → None`; if any call name in `context.terminal_tools`, build the "must be called alone" message and reject **every** call.
- `backend/src/engine/tool_call/dispatch.py:165-177` — `_first_terminal_tool_result`: first batch-order result with `is_terminal` true is projected.
- `backend/src/engine/tool_call/dispatch.py:346-358` — terminal batch rejection runs first, cancels the streaming executor, returns early.
- `backend/src/engine/query/loop.py:41-47` — `terminal_submission_failed`: `tool_calls_used + text_only_no_terminal_turns >= math.ceil(1.5 * tool_call_limit)`.
- `backend/src/engine/query/loop.py:298-327` — set `context.terminal_result`; if set → `QueryExitReason.TOOL_STOP` + break (after draining background tasks); else maybe `text_only_no_terminal_turns += 1`; then `terminal_submission_failed` → `TERMINAL_NOT_SUBMITTED`.
- `backend/src/engine/query/loop.py:50-57` — failure message includes `tool_calls_used=`, `text_only_no_terminal_turns=`, `tool_call_limit=`, `hard_ceiling=`.
- `backend/src/engine/query/loop.py:60-78,123-128` — `_make_stream_dispatch_deferrer`: when `context.terminal_tools` non-empty, defer **every** streamed tool until the full assistant message; `context.terminal_tools` derived from registry `tool.is_terminal_tool`.
- `backend/src/engine/query/context.py:31-35,54,56` — `QueryExitReason.{TOOL_STOP,TERMINAL_NOT_SUBMITTED}`, `terminal_tools`, `terminal_result`.
- `backend/src/notification/rules/must_submit_terminal_tool.py:14-42` — soft `terminal_call_reminder` (fire_once=False).
- Terminal tool set: `backend/src/tools/submission/{root,planner,generator,reducer,advisor,explorer}/.../*.py` all decorated `is_terminal_tool=True` (6 tools).

Docs (corroboration):
- `docs/architecture/workflow/terminal-tools.html:55-101,167-180` — descriptor → submission → framework stamp → dispatcher exclusivity → loop `TOOL_STOP`; "rejection returns one error tool result per tool-use id and executes none of the batch"; "error results are deliberately not stamped as terminal".
- `docs/architecture/tools/submission.html:66-81,109-117` — six `submit_*` terminals; "Terminal exclusivity and `TOOL_STOP` are enforced in `engine.tool_call.dispatch` and `engine.query.loop`".

## Rust mapping

- `agent-core/crates/eos-tools/src/terminal.rs:17-70` — `TerminalTool` closed enum of the **six** terminals + `from_tool_name`/`tool_name` round-trip.
- `agent-core/crates/eos-tools/src/meta.rs:49-51` — `is_terminal(name) = TerminalTool::from_tool_name(name).is_some()`; `model_tools/mod.rs:47` sets `RegisteredTool.is_terminal` from this.
- `agent-core/crates/eos-tools/src/execution.rs:106-115` — `stamp_terminal`: `if tool.is_terminal && !result.is_error { is_terminal = true }`.
- `agent-core/crates/eos-tools/src/dispatch.rs:63-103` — `reject_terminal_batch` (pure): `calls.len() <= 1 → None`; any terminal → reject all with byte-exact message.
- `agent-core/crates/eos-tools/src/dispatch.rs:111-181` — `lifecycle_batch_decision` (pure): the `Intent.LIFECYCLE` policy.
- `agent-core/crates/eos-engine/src/tool_call/dispatch.rs:205-303` — async loop consuming both decisions; `first_terminal_result` (293) + per-completion `ctx.terminal_result` set (279-281, 294-296).
- `agent-core/crates/eos-engine/src/tool_call/dispatch.rs:82-95` — `first_terminal_result`: first batch-order call whose registered tool `is_terminal` and whose result `is_terminal`.
- `agent-core/crates/eos-engine/src/query/loop_.rs:24-29` — `terminal_submission_failed`: `(tool_call_limit*3 + 1)/2` (== `ceil(1.5*limit)`, verified for n=0..19).
- `agent-core/crates/eos-engine/src/query/loop_.rs:199-206` — terminal exit → `QueryExitReason::ToolStop` + break.
- `agent-core/crates/eos-engine/src/query/loop_.rs:31-37,84-96` — `terminal_not_submitted_message` + `TerminalNotSubmitted` exit.
- `agent-core/crates/eos-engine/src/query/context.rs:34-38` — `QueryExitReason::{ToolStop,TerminalNotSubmitted}`.
- `agent-core/crates/eos-engine/src/notifications.rs:43-124` — `TerminalCallReminder` rule + body.

## Invariant table

| # | invariant | status | severity | python file:line | rust file:line | note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | Terminal batched with siblings is rejected; none execute | match | none | `dispatch.py:180-206,346-358` | `dispatch.rs(tools):63-103` + `dispatch.rs(engine):217-235` | byte-exact message; all calls rejected; runnable never built so 0 executions (test `terminal_batched_with_sibling_rejects_all`) |
| 2 | Successful terminal stamped `is_terminal=True` by exec layer; error not stamped | match | none | `tool_call.py:197-200` | `execution.rs:106-115` | identical `is_terminal && !is_error` predicate (test `stamps_terminal_on_success`) |
| 3 | Dispatch + loop exit run off the stamp | match | none | `dispatch.py:165-177` + `loop.py:298-310` | `dispatch.rs(engine):279-296` + `loop_.rs:199-206` | first-in-batch terminal projected; loop sets `ToolStop` + break |
| 4 | Terminal results are persisted task/workflow state inputs | partial | low | `tool_call.py:121-127` (`is_terminal` block) | `dispatch.rs(engine):40-48` (`result_block` carries `is_terminal`) | result block + metadata flow through; actual persistence is the submission executor's job and is verified separately (submission.rs port) |
| 5 | Terminal set enumerated = the six submit_* + matches Python | match | none | six `submit_*/.../*.py` `is_terminal_tool=True` | `terminal.rs:17-70` + `meta.rs:49-51` | exactly 6; `ALL`/`from_tool_name` round-trip (test `descriptors_total`) |
| C | Hard-ceiling constant `ceil(1.5*limit)` | match | none | `loop.py:46` | `loop_.rs:25` | `(limit*3+1)/2` proven equal to `ceil(1.5n)` for all n |
| C | Comparison operator `>=` | match | none | `loop.py:45` `>=` | `loop_.rs:27` `>=` | identical |
| C | Ceiling input = `tool_calls_used + text_only_no_terminal_turns` | match | none | `loop.py:45` | `loop_.rs:26-28` | same sum |

## Disparities

### D1 — `terminal_not_submitted` failure message diverges (status: divergent, severity: low)
- Python `loop.py:50-57`: `"Agent stopped: terminal tool not submitted. tool_calls_used={..}, text_only_no_terminal_turns={..}, tool_call_limit={..}, hard_ceiling={ceil(1.5*limit)}."`
- Rust `loop_.rs:31-37`: `"The agent used {used} tool calls/text-only turns without submitting a terminal tool. Submit one of the terminal tools to finish the run."`
- Why it matters: this string is a **persisted/user-facing run-failure result** (it is the body of a `ToolExecutionCompleted` error event that goes into the transcript and feeds the run tracker). It is not a transient debug log. The Rust message drops the structured `tool_call_limit` / `hard_ceiling` / split call-vs-text counts, so downstream consumers or operators parsing the failure lose those fields, and any test/snapshot pinning the Python text will not match.
- Suggested fix: render the Python format verbatim from `ctx.tool_calls_used`, `ctx.text_only_no_terminal_turns`, `ctx.tool_call_limit`, and the ceiling, or document the intentional re-wording in the migration plan.

### D2 — No background-task drain / cancel on terminal exit or loop teardown (status: missing, severity: medium)
- Python `loop.py:303-309` on `TOOL_STOP` calls `background_tasks.terminate_for_parent_exit()` and flushes resulting system notifications; `loop.py:329-331` `finally` cancels any still-pending background tasks; `loop.py:313-315` cancels all on `TERMINAL_NOT_SUBMITTED`.
- Rust `loop_.rs:98-208`: the loop has an `enable_background_tasks` field (`context.rs:69`) but **no** background supervisor, no `dispatch_background_tool_call` path in `dispatch.rs`, and no cancel/drain at either exit. `is_engine_background_tool` and `BackgroundTaskSupervisor` are absent from the engine crate.
- Why it matters: in Python, a terminal submission (or hard-ceiling failure) is the point where in-flight background work is force-terminated and its completion notices flushed. Without it, once background dispatch lands, a terminal stop could leave orphaned background tasks and lose end-of-run notifications. As of the engine-only phase there is no background dispatch at all, so this is currently inert — but it is a real dropped dynamic, not equivalent behavior.
- Classify: **intentional migration deferral** (background subsystem not yet ported to the engine), but it should be tracked as a known gap so the terminal-exit cleanup is restored when background dispatch lands. Suggested fix: when background dispatch is added to `dispatch.rs`, port the `terminate_for_parent_exit` drain into the `ToolStop` branch and a cancel-all into loop teardown.

### D3 — Hard-ceiling check fires one iteration later than Python after a non-terminal tool batch (status: divergent, severity: low)
- Python `loop.py:298-327`: in the **same** iteration after dispatching a tool batch, it sets `terminal_result`, checks `TOOL_STOP`, then (only for text-only turns) increments `text_only_no_terminal_turns`, then checks `terminal_submission_failed` → `TERMINAL_NOT_SUBMITTED`. So a tool batch that crosses the ceiling without terminating fails immediately at the bottom of that iteration.
- Rust `loop_.rs:102-207`: `terminal_submission_failed` is checked at the **top** of the loop (103) and after a **text-only** turn (180), but **not** after a non-terminal tool batch in the same iteration. After a non-terminal tool batch the loop falls through to the next iteration's top-of-loop check.
- Why it matters: the agent gets one extra provider round-trip (an extra model turn / extra tool budget) before the hard ceiling is enforced, compared to Python. Same terminal state is reached; the timing and the exact `tool_calls_used` at failure can differ by one turn. Could break a test that asserts the precise failure iteration.
- Suggested fix: add a `terminal_submission_failed` check immediately after the non-terminal dispatch branch (after line 206 when no terminal result), mirroring Python's bottom-of-loop placement; or accept and document the equivalence.

### D4 — `StreamingToolExecutor` / `should_defer_tool` is dead scaffolding; deferral is implicit (status: divergent, severity: low)
- Python `loop.py:60-78,150-159,196-205` wires a real `StreamingToolExecutor` with a `should_defer` predicate; when `terminal_tools` is non-empty, every streamed tool is deferred so terminal exclusivity is validated **before any sibling tool body runs**, and `executor.cancel_all()` is called on terminal-batch rejection.
- Rust `streaming.rs:9-36` defines `StreamingToolExecutor` + `should_defer_tool(ctx,_) = !terminal_tools.is_empty()` but **the loop never constructs or calls them** (`loop_.rs` has no reference). Instead the Rust loop never executes tools mid-stream at all: `ToolUseDelta` only feeds `streamed_tool_use_ids` for budget counting (`loop_.rs:139-143`), and **all** tools are dispatched post-message at `loop_.rs:187`.
- Why it matters: the *observable* guarantee (no sibling body runs before exclusivity is validated) is actually **stronger/equivalent** in Rust because there is zero mid-stream execution — so the invariant holds. But the `StreamingToolExecutor`/`should_defer_tool` types are unused, misleadingly suggesting a deferral mechanism that does not exist, and there is no `cancel_all` to port because nothing runs early.
- Suggested fix: delete the dead `streaming.rs` deferral scaffolding (or add a `#[allow(dead_code)]` + doc comment stating that universal post-message dispatch supersedes it), so reviewers do not assume mid-stream execution exists.

### D5 — Post-dispatch notification flush not mirrored (status: partial, severity: low)
- Python `loop.py:296-297,308-309,324-325` flushes `flush_system_notification_events(notification_service)` after tool dispatch, after a terminal `TOOL_STOP`, and after `TERMINAL_NOT_SUBMITTED`, so tool-emitted system notifications reach the stream on the same turn.
- Rust `loop_.rs`: `dispatch_rules` runs only at the **top** of the loop (line 108); there is no post-dispatch flush of tool-emitted notifications, and the `NotificationService` queue (`notifications.rs:178-205`) is drained nowhere in the loop.
- Why it matters: tool-generated system notifications (e.g. background completions, soft reminders raised during a tool body) are delivered a turn late or not at all relative to Python. The `terminal_call_reminder` declarative rule itself **is** ported and fires top-of-loop, so the soft-nudge invariant holds; only the tool-emitted queue flush is missing.
- Suggested fix: drain `NotificationService` after `dispatch_assistant_tools` and yield those as `SystemNotification` events, matching the Python flush points.

## Extra findings

- **EF1 — Root terminal is advisor-gated in Rust (intentional EOS divergence).** `meta.rs` comment + hook chain gate `submit_root_outcome` with `AdvisorApproval`; Python gates only planner/generator/reducer. This is explicitly documented as an EOS decision, not a porting bug. It is adjacent to terminal enforcement (a pre-hook denial keeps the terminal from stamping) so worth noting, but out of this area's checklist.
- **EF2 — Six descriptors vs Python's four.** `terminal.rs:8-9,108-119` notes Python's `_terminals/registry.py` only authors 4 descriptors (advisor + explorer fall through `render_terminal_catalog`'s generic fallback); Rust authors all 6 for compile-time totality. Behavior-preserving (descriptors are prose for prompts), documented (GC-tools-03).
- **EF3 — `generator_role` token normalized.** `submission.rs:222-224` writes `{"generator_role": "generator"}` where Python writes `{"generator_role": "executor"}` (vestigial profile name). Intentional per anchor §4 (the `executor` token is forbidden in persisted Rust state). This is a **divergent persisted-metadata value** on the terminal result — flagged in case any consumer keys on `"executor"`.
- **EF4 — `submission_kind` metadata preserved** (`submission.rs`: `root_success`/`root_failure`, `generator_success`/…, `planner_completes`/`planner_defers`, etc.) matching Python kinds — good for state-input parity (invariant 4).
- **EF5 — Lifecycle-batch policy ported faithfully** including the deliberate divergence-from-terminal-precedent (`dispatch.rs(tools):105-181`): `>1` lifecycle rejects all lifecycle + keeps siblings; `=1` lifecycle + siblings rejects siblings + runs the lifecycle solo. Byte-exact messages. The Python telemetry counters (`_LIFECYCLE_BATCH_REJECTION_COUNTERS`) and audit emit (`emit_lifecycle_batch_rejected`) are **not** ported — pure-decision function only; audit/telemetry deferred.
- **EF6 — Terminal-error result still appears in `tool_results`** (Rust `dispatch.rs(engine):282-290` appends every completion's block) and `terminal_result` stays `None` (test `terminal_tool_error_does_not_project_terminal_result`), matching Python (an errored terminal is a normal retryable tool result). Good.
- **EF7 — Budget counting moved entirely to the loop.** Python counts via `_count_tool_dispatch` inside `execute_tool_call_streaming` gated on `consume_budget = tool_use_id not in streamed_tool_use_ids`. Rust counts in the loop: streamed deltas at `loop_.rs:140-142`, non-streamed post-message at `loop_.rs:171-173`. Net effect (each call counted exactly once) is equivalent; Rust `execute_tool_once` never touches the counter. Behavior-preserving relocation.

## Open questions

1. Is the divergent `terminal_not_submitted` message (D1) a deliberate re-wording, or should it be restored to the structured Python format? Any snapshot test pinning the Python string will fail.
2. When background dispatch lands in the engine (D2), will the `terminate_for_parent_exit` drain + cancel-on-exit be reintroduced, or is the cleanup intentionally relocated elsewhere (e.g. eos-runtime)?
3. Should the timing of the hard-ceiling check (D3) be aligned to Python's bottom-of-loop placement, or is the one-turn slack acceptable and documented?
4. Is `EF3`'s `generator_role: "generator"` value safe for all consumers of the terminal result metadata, or does any reader still expect `"executor"`?
5. Should `streaming.rs` (D4) be deleted as dead code, given the loop achieves deferral by never executing mid-stream?
